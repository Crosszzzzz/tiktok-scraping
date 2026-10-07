from __future__ import annotations
import asyncio
import json
import re
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from app import store
from app.capture import POOL, NeedsAttention, session

JOBS: dict[str, dict] = {}
TASKS: set = set()
EDIT_LOCK = threading.RLock()

@asynccontextmanager
async def lifespan(app):
    store.DATA.mkdir(parents=True, exist_ok=True)
    yield
    for task in TASKS:
        task.cancel()
    for job in JOBS.values():
        job['cancel_requested'] = True
    try:
        await asyncio.wait_for(asyncio.wrap_future(POOL.submit(session.close)), timeout=5)
    except Exception:
        pass

app = FastAPI(title='Huella — Observatorio local', lifespan=lifespan)

@app.middleware('http')
async def local_only(request: Request, call_next):
    host = request.url.hostname
    if host not in {'127.0.0.1','localhost','::1','testserver'}:
        return JSONResponse({'detail':'Servicio disponible solo en localhost.'}, status_code=403)
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        return JSONResponse({'detail':'Origen no permitido.'}, status_code=403)
    if request.method not in {'GET','HEAD','OPTIONS'} and request.headers.get('sec-fetch-site') == 'cross-site':
        return JSONResponse({'detail':'Solicitud externa bloqueada.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    return response

@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({'detail':str(exc)}, status_code=422)

@app.exception_handler(RequestValidationError)
async def invalid_request(request,exc):
    # Pydantic includes rejected input values by default, including API keys.
    return JSONResponse({'detail':[{'loc':list(e['loc']),'msg':e['msg'],'type':e['type']} for e in exc.errors()]},status_code=422)

@app.exception_handler(FileNotFoundError)
async def missing(request, exc):
    return JSONResponse({'detail':'Archivo o informe no encontrado.'}, status_code=404)

class ProfileInput(BaseModel):
    profile: str = ''
    @field_validator('profile')
    @classmethod
    def validate_profile(cls, value):
        return store.profile_name(value) if value else ''

class CaptureInput(ProfileInput):
    sources: list[Literal['uploads','reposts','likes']] = Field(default_factory=lambda:['uploads','reposts','likes'], min_length=1,max_length=3)
    max_items: int = Field(default=20, ge=1, le=200)
    scroll_rounds: int = Field(default=8, ge=1, le=50)
    mode: Literal['incremental','refresh'] = 'incremental'
    headless: bool = True
    wait_captcha: bool = True

class AutoInput(ProfileInput):
    sources: list[Literal['uploads','reposts','likes']] = Field(default_factory=lambda:['uploads','reposts','likes'], min_length=1,max_length=3)
    max_items: int = Field(default=30, ge=1, le=200)
    scroll_rounds: int = Field(default=12, ge=1, le=50)
    publish: bool = True

class SavedProfileInput(ProfileInput):
    label: str = Field(default='',max_length=100)
    notes: str = Field(default='',max_length=5000)
    tags: list[str] = Field(default_factory=list,max_length=20)
    sources: list[Literal['uploads','reposts','likes']] = Field(default_factory=lambda:['uploads','reposts','likes'],min_length=1,max_length=3)
    max_items: int = Field(default=20,ge=1,le=200)
    scroll_rounds: int = Field(default=8,ge=1,le=50)
    mode: Literal['incremental','refresh'] = 'incremental'
    headless: bool = True

class ImportInput(ProfileInput):
    records: list[dict] = Field(max_length=2000)
    collection: dict = Field(default_factory=dict)

class EditInput(BaseModel):
    caption: str | None = Field(default=None,max_length=20000)
    interaction_at: str | None = None
    interaction_date_source: Literal['visible','user_verified'] | None = None
    published_at: str | None = None
    ocr_text: str | None = Field(default=None,max_length=50000)
    transcript: str | None = Field(default=None,max_length=100000)
    visual_summary: str | None = Field(default=None,max_length=20000)
    notes: str | list[str] | None = None
    repost_note: str | None = Field(default=None,max_length=10000)
    repost_note_author: str | None = Field(default=None,max_length=100)
    repost_note_source: Literal['visible','user_verified'] | None = None

class AISettingsInput(BaseModel):
    provider: Literal['local','gemini'] | None = None
    model: str | None = Field(default=None,max_length=100)
    api_key: str | None = Field(default=None,max_length=512,repr=False)
    auto_visual: bool | None = None
    max_per_run: int | None = Field(default=None,ge=1,le=50)

def analyze(run):
    from app.analysis import analyze_records
    from app.profiles import sync_run
    run['analysis'] = analyze_records(run['records'])
    store.save_run(run)
    sync_run(run)
    return run

def cumulative_report(profile):
    from app.profiles import profile_report
    from app.analysis import analyze_records
    run=profile_report(store.profile_name(profile))
    run['analysis']=analyze_records(run['records'])
    return run

def job_new(message: str, run_id: str | None = None):
    job = dict(id=uuid.uuid4().hex, status='pending', message=message, progress=0,run_id=run_id,cancel_requested=False)
    JOBS[job['id']] = job
    return job

def spawn(coro):
    task = asyncio.create_task(coro)
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)

def busy():
    return any(j['status'] in {'pending','running'} for j in JOBS.values())

@app.get('/api/state')
async def state():
    from app.media import capabilities
    from app.profiles import list_profiles
    caps = await asyncio.to_thread(capabilities)
    profiles = await asyncio.to_thread(list_profiles)
    from app.vision import get_settings
    ai=await asyncio.to_thread(get_settings)
    caps['vision']=ai.get('configured',False)
    return {'browser':session.state(), 'capabilities':caps,'ai':ai,'profiles':profiles,'runs':store.list_runs(),'jobs':list(JOBS.values())[-20:]}

@app.get('/api/ai/settings')
async def ai_settings():
    from app.vision import get_settings
    return await asyncio.to_thread(get_settings)

@app.put('/api/ai/settings')
async def save_ai_settings(body:AISettingsInput):
    if busy():
        raise HTTPException(409,'Espera a que termine el trabajo antes de cambiar la IA.')
    from app.vision import update_settings
    changes={key:value for key,value in body.model_dump(exclude_unset=True).items() if value is not None}
    if not changes.get('api_key'):
        changes.pop('api_key',None)
    return await asyncio.to_thread(update_settings,**changes)

@app.delete('/api/ai/key')
async def remove_ai_key():
    if busy():
        raise HTTPException(409,'Espera a que termine el trabajo antes de borrar la clave.')
    from app.vision import clear_key
    return await asyncio.to_thread(clear_key)

@app.get('/api/profiles')
async def saved_profiles():
    from app.profiles import list_profiles
    return await asyncio.to_thread(list_profiles)

@app.post('/api/profiles')
async def save_profile(body:SavedProfileInput):
    from app.profiles import save_profile
    if not body.profile:
        raise HTTPException(422,'Introduce un perfil.')
    return await asyncio.to_thread(save_profile,body.profile,body.model_dump(exclude_unset=True,exclude={'profile'}))

@app.get('/api/profiles/{profile}')
async def saved_profile(profile:str):
    from app.profiles import list_profiles
    name=store.profile_name(profile)
    for entry in await asyncio.to_thread(list_profiles):
        if entry['profile']==name:
            return entry
    raise HTTPException(404,'Perfil no guardado.')

@app.get('/api/profiles/{profile}/report')
async def get_profile_report(profile:str):
    await saved_profile(profile)
    return await asyncio.to_thread(cumulative_report,profile)

@app.post('/api/profiles/{profile}/analyze')
async def analyze_profile(profile:str):
    return await get_profile_report(profile)

@app.get('/api/profiles/{profile}/export')
async def export_profile(profile:str,format:Literal['json','md']='json'):
    run=await get_profile_report(profile)
    return export_response(run,format)

@app.post('/api/profiles/{profile}/publish')
async def publish_profile(profile:str,deploy:bool=False):
    """Build a sanitized static report and, optionally, publish it to Vercel."""
    await saved_profile(profile)
    run=await asyncio.to_thread(cumulative_report,profile)
    from app.publish import build_bundle,deploy_bundle
    target=await asyncio.to_thread(build_bundle,profile,run)
    result={'profile':store.profile_name(profile),'path':store.relative(target),'url':None}
    if deploy:
        result['url']=await asyncio.to_thread(deploy_bundle,target)
    return result

@app.post('/api/profiles/{profile}/avatar')
async def upload_avatar(profile:str,file:UploadFile=File(...)):
    from app.profiles import save_profile
    from PIL import Image,ImageOps,UnidentifiedImageError
    from io import BytesIO
    name=store.profile_name(profile)
    payload=await file.read(10*1024*1024+1)
    await file.close()
    if not payload or len(payload)>10*1024*1024:
        raise HTTPException(413,'La foto debe pesar entre 1 byte y 10 MB.')
    try:
        with Image.open(BytesIO(payload)) as im:
            if im.format not in {'PNG','JPEG','WEBP'} or im.width*im.height>20000000:
                raise ValueError('Usa JPG, PNG o WebP de hasta 20 megapíxeles.')
            im.load()
            normalized=ImageOps.exif_transpose(im).convert('RGB')
            normalized.thumbnail((512,512))
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as exc:
        raise HTTPException(422,'No se pudo leer la imagen.') from exc
    directory=store.DATA/'artifacts'/'profiles'/name
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/(uuid.uuid4().hex+'.jpg')
    normalized.save(path,'JPEG',quality=88)
    return await asyncio.to_thread(save_profile,name,{'avatar_path':store.relative(path),'avatar_source':'user_upload','avatar_updated_at':store.now()})

@app.post('/api/session/open')
async def open_session(body: ProfileInput):
    if busy():
        raise HTTPException(409,'Espera a que termine el trabajo actual o cancélalo.')
    try:
        return await asyncio.wrap_future(POOL.submit(session.open,body.profile))
    except NeedsAttention as exc:
        raise HTTPException(409,str(exc))

@app.post('/api/session/close')
async def close_session():
    if busy():
        raise HTTPException(409,'Cancela primero la captura en curso.')
    return await asyncio.wrap_future(POOL.submit(session.close))

@app.post('/api/session/login')
async def open_login_session(body:ProfileInput):
    """Open a normal Chrome window (no automation) so the user can log in manually."""
    if busy():
        raise HTTPException(409,'Espera a que termine el trabajo actual o cancélalo.')
    from app.capture import open_login_browser
    try:
        await asyncio.wrap_future(POOL.submit(session.close))
    except Exception:
        pass
    try:
        await asyncio.to_thread(open_login_browser,body.profile)
    except NeedsAttention as exc:
        raise HTTPException(409,str(exc))
    return {'status':'login_window_opened',
            'message':'Se abrió una ventana NORMAL de Chrome con el perfil de la app. Iniciá sesión en TikTok, CERRÁ esa ventana y después generá el informe.'}

async def collect(job,run,body):
    job['status']='running'
    run['status']='running'
    store.save_run(run)
    try:
        await asyncio.wrap_future(POOL.submit(session.capture,job,run,list(dict.fromkeys(body.sources)),body.max_items,body.scroll_rounds,body.mode,body.headless,body.wait_captcha))
        message=f"{len(run['records'])} evidencias observadas. {('Captura cancelada.' if run['status']=='cancelled' else 'Archivo acumulado actualizado.') }"
        if run['status']=='needs_attention':
            message=run.get('collection',{}).get('last_capture_reason') or 'La captura necesita atención. Revisa los bloqueos; el archivo anterior se conserva.'
    except NeedsAttention as exc:
        run['status']='needs_attention'
        message=str(exc)
        run['collection']['last_capture_reason']=message
        run['collection']['blockers']=[{'code':exc.code,'message':message,'observed_at':store.now()}]
        await save_capture_diagnostic(run)
    except Exception as exc:
        run['status']='failed'
        message=f'Captura incompleta ({type(exc).__name__}). Revisa Chrome; la evidencia obtenida se conserva.'
        run['collection']['last_capture_reason']=message
        run['collection']['blockers']=[{'code':'capture_error','message':message,'observed_at':store.now()}]
        await save_capture_diagnostic(run)
    finally:
        blockers=run.get('collection',{}).get('blockers',[])
        private_profile=any(isinstance(blocker,dict) and blocker.get('code')=='private_profile' for blocker in blockers)
        if not job.get('cancel_requested') and not private_profile:
            await auto_vision(job,run)
        if private_profile:
            message='El perfil es privado. Solo se recopila contenido público accesible; se detiene el proceso sin análisis visual ni publicación.'
            job['message']=message
            try:
                from app.profiles import sync_run
                await asyncio.to_thread(store.save_run,run)
                await asyncio.to_thread(sync_run,run)
            except Exception as exc:
                job.update(status='failed',message=f'No se pudo guardar el estado de la captura ({type(exc).__name__}).')
                return
        else:
            job['message']='Guardando y analizando el archivo acumulado…'
            try:
                await asyncio.to_thread(analyze,run)
            except Exception as exc:
                job.update(status='failed',message=f'No se pudo actualizar el análisis ({type(exc).__name__}); se conservan las capturas guardadas.')
                return
    job.update(status=run['status'],progress=100,message=message)

async def save_capture_diagnostic(run):
    try:
        diagnostic=await asyncio.wrap_future(POOL.submit(session.capture_diagnostic,run))
        run['collection']['failure_diagnostic']=diagnostic
    except Exception:
        run['collection']['failure_diagnostic']={'status':'unavailable','reason':'No se pudo obtener una captura del estado final del navegador.'}

def vision_files(row):
    quality=row.get('frame_quality') or {}
    files=[]
    for path in row.get('frames',[]):
        info=quality.get(path,{})
        if info.get('status') != 'verified':
            continue
        target=store.media_path(path)
        if target.is_file():
            files.append(target)
    return files[:8]

def apply_visual_result(row,result):
    for name in ['visual_summary','visual_analysis']:
        if result.get(name) and name not in (row.get('manual_fields') or {}):
            row[name]=result[name]
    if result.get('visual_error'):
        row['visual_error']=result['visual_error']
    else:
        row.pop('visual_error',None)
    row['notes']=list(dict.fromkeys(row.get('notes',[])+result.get('notes',[])))

async def auto_vision(job,run):
    from app.vision import get_settings,analyze_record
    settings=await asyncio.to_thread(get_settings)
    if not settings.get('auto_visual') or not settings.get('configured'):
        return
    stats={'attempted':0,'completed':0,'failed':0,'cached':0,'limit':settings['max_per_run'],'remaining':0}
    run['collection']['visual_processing']=stats
    for row in run['records']:
        if job.get('cancel_requested'):
            break
        if 'visual_summary' in (row.get('manual_fields') or {}):
            continue
        files=vision_files(row)
        if not files:
            continue
        if stats['attempted']>=stats['limit']:
            stats['remaining']+=1
            continue
        job['message']=f"Interpretando imágenes: {stats['attempted']+1}/{stats['limit']}…"
        try:
            result=await asyncio.to_thread(analyze_record,row,files)
            apply_visual_result(row,result)
            if result.get('visual_analysis',{}).get('cached'):
                stats['cached']+=1
            else:
                stats['attempted']+=1
            stats['failed' if result.get('visual_error') else 'completed']+=1
            store.save_run(run)
            if result.get('visual_error',{}).get('code') in {'quota_exceeded','rate_limited','authentication_error','invalid_api_key','http_429','http_401','http_403','http_404','missing_key'}:
                break
        except Exception:
            stats['attempted']+=1
            stats['failed']+=1
            row['visual_error']={'code':'processing_error','message':'No se pudo completar el contexto visual; se conserva la evidencia.'}
            store.save_run(run)

@app.post('/api/capture')
async def capture(body: CaptureInput):
    if not body.profile:
        raise HTTPException(422,'Introduce un perfil.')
    if busy():
        raise HTTPException(409,'Ya hay un trabajo en curso.')
    browser=session.state()
    if not browser.get('open') and not browser.get('profile_available'):
        raise HTTPException(409,'Abre Chrome e inicia sesión manualmente primero.')
    from app.profiles import save_profile
    await asyncio.to_thread(save_profile,body.profile,body.model_dump(exclude={'profile','wait_captcha'}))
    run = store.new_run(body.profile)
    job = job_new('Preparando captura…',run['id'])
    job['profile']=body.profile
    spawn(collect(job,run,body))
    return job

async def auto_pipeline(job,run,body):
    """One-shot flow: open a visible session, capture, analyze and publish."""
    browser=session.state()
    if not browser.get('open'):
        job['message']='Abriendo Chrome. Si TikTok pide verificación, resuélvela en la ventana.'
        try:
            await asyncio.wrap_future(POOL.submit(session.open,body.profile,False))
        except NeedsAttention as exc:
            message=str(exc)
            run['status']='needs_attention'
            run['collection']['last_capture_reason']=message
            run['collection']['blockers']=[{'code':exc.code,'message':message,'observed_at':store.now()}]
            store.save_run(run)
            job.update(status='needs_attention',message=message)
            return
        except Exception as exc:
            message=f'No se pudo abrir Chrome ({type(exc).__name__}). Revisa la sesión y vuelve a intentarlo.'
            run['status']='failed'
            run['collection']['last_capture_reason']=message
            run['collection']['blockers']=[{'code':'session_open_failed','message':message,'observed_at':store.now()}]
            store.save_run(run)
            job.update(status='failed',message=message)
            return
    capture_body=SimpleNamespace(sources=list(dict.fromkeys(body.sources)),max_items=body.max_items,
                                 scroll_rounds=body.scroll_rounds,mode='incremental',headless=False,wait_captcha=True)
    await collect(job,run,capture_body)
    if not body.publish or run.get('status')!='completed':
        return
    job['message']='Captura lista. Publicando informe público en Vercel…'
    try:
        from app.publish import build_bundle,deploy_bundle
        report=await asyncio.to_thread(cumulative_report,body.profile)
        target=await asyncio.to_thread(build_bundle,body.profile,report)
        url=await asyncio.to_thread(deploy_bundle,target)
        job['url']=url
        job['message']='Informe publicado: '+url
    except Exception as exc:
        job['publish_error']=str(exc)[:300]
        job['message']='La captura terminó, pero la publicación falló ('+type(exc).__name__+'). Puedes reintentar desde el informe.'
        if run.get('status')=='completed':
            job['status']='completed'

@app.post('/api/auto')
async def auto_run(body:AutoInput):
    if not body.profile:
        raise HTTPException(422,'Introduce un perfil.')
    if busy():
        raise HTTPException(409,'Ya hay un trabajo en curso.')
    from app.profiles import save_profile
    await asyncio.to_thread(save_profile,body.profile,{'sources':list(dict.fromkeys(body.sources)),
        'max_items':body.max_items,'scroll_rounds':body.scroll_rounds,'mode':'incremental','headless':False})
    run=store.new_run(body.profile)
    job=job_new('Preparando informe automático…',run['id'])
    job['profile']=body.profile
    spawn(auto_pipeline(job,run,body))
    return job

@app.get('/api/jobs/{job_id}')
async def get_job(job_id:str):
    if job_id not in JOBS:
        raise HTTPException(404,'Trabajo no encontrado; los trabajos activos se reinician con el servidor.')
    return JOBS[job_id]

@app.post('/api/jobs/{job_id}/cancel')
async def cancel(job_id:str):
    job = await get_job(job_id)
    job['cancel_requested']=True
    job['message']='Cancelando después de la evidencia actual…'
    return job

@app.get('/api/runs')
async def runs():
    return store.list_runs()

@app.get('/api/runs/{run_id}')
async def get_run(run_id:str):
    return store.load_run(run_id)

@app.post('/api/runs/{run_id}/analyze')
async def reanalyze(run_id:str):
    ensure_editable(run_id)
    return await asyncio.to_thread(analyze,store.load_run(run_id))

def ensure_editable(run_id):
    if any(j['run_id']==run_id and j['status'] in {'pending','running'} for j in JOBS.values()):
        raise HTTPException(409,'Espera a que termine el trabajo de este informe.')

def import_record(raw,profile):
    source = raw.get('source','manual')
    if not isinstance(source,str) or source not in {'manual','uploads','reposts','likes'}:
        raise ValueError('Fuente de evidencia inválida.')
    url = store.canonical_post_url(raw.get('url',''))
    row = store.empty_record(profile,source,url)
    allowed = {'caption','author','published_at','published_date_raw','published_date_source','interaction_at','interaction_date_source','observed_at','first_seen_at','order_index','music','ocr_text','transcript','visual_summary','notes','hashtags','metrics','format','pinned','grid_metric_raw','repost_note','repost_note_author','repost_note_source'}
    for key in allowed:
        if key in raw:
            row[key] = raw[key]
    # Do not accept imported filesystem references. Reattach media via the upload endpoint.
    for key in ['caption','author','ocr_text','transcript','visual_summary']:
        if not isinstance(row[key],str) or len(row[key])>100000:
            raise ValueError(f'Campo inválido o demasiado largo: {key}.')
    if not isinstance(row['music'],dict):
        raise ValueError('music debe ser un objeto.')
    if isinstance(row['notes'],str):
        row['notes']=[row['notes']]
    if not isinstance(row['notes'],list) or any(not isinstance(n,str) for n in row['notes']):
        raise ValueError('notes debe contener textos.')
    for key in ['repost_note','repost_note_author']:
        if not isinstance(row.get(key,''),str) or len(row.get(key,''))>10000:
            raise ValueError(f'{key} debe ser texto.')
    provenance=row.get('repost_note_source')
    if not isinstance(provenance,(str,type(None))) or provenance not in {None,'visible','user_verified'}:
        row['repost_note_source']=None
    if not isinstance(row['interaction_date_source'],(str,type(None))) or row['interaction_date_source'] not in {None,'visible','user_verified'}:
        row['interaction_at']=None
        row['interaction_date_source']=None
    from datetime import datetime
    for key in ['published_at','interaction_at','observed_at','first_seen_at']:
        value=row.get(key)
        if value is None and key in {'published_at','interaction_at'}:
            continue
        if not isinstance(value,str):
            raise ValueError(f'{key} debe ser una fecha ISO o null cuando corresponda.')
        try:
            datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError as exc:
            raise ValueError(f'Fecha ISO inválida en {key}.') from exc
    return row

@app.post('/api/import')
async def import_data(body:ImportInput):
    if not body.profile:
        raise HTTPException(422,'El perfil es obligatorio.')
    run = store.new_run(body.profile)
    run.update(records=[import_record(r,body.profile) for r in body.records],status='completed')
    # Collection provenance is data, not instructions; cap serialized size.
    if len(json.dumps(body.collection))>50000:
        raise HTTPException(422,'Metadatos de colección demasiado grandes.')
    run['collection'] = body.collection or {'method':'manual_import','sources':{}}
    return await asyncio.to_thread(analyze,run)

@app.patch('/api/runs/{run_id}/records/{record_id}')
async def edit_record(run_id:str,record_id:str,body:EditInput):
    ensure_editable(run_id)
    with EDIT_LOCK:
        run = store.load_run(run_id)
        row = find_record(run,record_id)
        changes = body.model_dump(exclude_unset=True)
        for name in ['interaction_at','published_at']:
            if changes.get(name):
                from datetime import datetime
                datetime.fromisoformat(changes[name].replace('Z','+00:00'))
        if isinstance(changes.get('notes'),str):
            changes['notes']=[changes['notes']]
        if changes.get('interaction_at'):
            changes['interaction_date_source']='user_verified'
        if 'interaction_at' in changes and not changes['interaction_at']:
            changes['interaction_at']=None
            changes['interaction_date_source']=None
        if any(key in changes and changes[key] != row.get(key) for key in ['repost_note','repost_note_author']):
            changes['repost_note_source']='user_verified' if changes.get('repost_note',row.get('repost_note')) else None
        edited=store.now()
        protections=dict(row.get('manual_fields') or {})
        for key,value in changes.items():
            if value != row.get(key):
                protections[key]=edited
        row.update(changes)
        row['manual_fields']=protections
        row['edited_at']=edited
        return analyze(run)

def find_record(run,record_id):
    for row in run['records']:
        if row['id']==record_id:
            return row
    raise HTTPException(404,'Evidencia no encontrada.')

async def media_work(job,run_id,record_id,path=None,vision=False):
    from app.media import process_media
    job['status']='running'
    try:
        run=store.load_run(run_id)
        row=find_record(run,record_id)
        if vision:
            from app.vision import analyze_record
            files=vision_files(row)
            if not files:
                raise ValueError('Vuelve a capturar esta evidencia o adjunta una imagen o video. La visión requiere recortes verificados; las capturas antiguas y de página completa quedan como referencia.')
            result=await asyncio.to_thread(analyze_record,row,files)
        else:
            result=await asyncio.to_thread(process_media,path,path.parent/'processed')
            result['frames']=[store.relative(p) for p in result.get('frames',[])]
        with EDIT_LOCK:
            run=store.load_run(run_id)
            row=find_record(run,record_id)
            if vision:
                apply_visual_result(row,result)
            for name in ['ocr_text','transcript','visual_summary']:
                if result.get(name) and name not in (row.get('manual_fields') or {}):
                    row[name]=result[name]
            row['frames']=list(dict.fromkeys(row.get('frames',[])+result.get('frames',[])))
            if not vision:
                quality=row.setdefault('frame_quality',{})
                for frame in result.get('frames',[]):
                    quality[frame]={'status':'verified','source':'user_upload','checked_at':store.now()}
            row['notes']=list(dict.fromkeys(row.get('notes',[])+result.get('notes',[])))
            analyze(run)
        if result.get('visual_error'):
            job.update(status='failed',message=result['visual_error']['message'])
            return
        job.update(status='cancelled' if job.get('cancel_requested') else 'completed',progress=100,message='Evidencia procesada; cancelación aplicada al terminar la operación actual.' if job.get('cancel_requested') else 'Evidencia procesada. Revisa los campos y las notas de cobertura.')
    except Exception as exc:
        job.update(status='failed',message=str(exc)[:500])

@app.post('/api/runs/{run_id}/records/{record_id}/media')
async def upload(run_id:str,record_id:str,file:UploadFile=File(...)):
    ensure_editable(run_id)
    find_record(store.load_run(run_id),record_id)
    suffix=Path(file.filename or '').suffix.lower()
    if suffix not in {'.png','.jpg','.jpeg','.webp','.mp4','.mov','.webm','.wav','.mp3','.m4a'}:
        raise HTTPException(422,'Formato no admitido.')
    directory=store.DATA/'artifacts'/store.safe_id(run_id)/store.safe_id(record_id)/uuid.uuid4().hex[:8]
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/('original'+suffix)
    total=0
    try:
        with path.open('wb') as stream:
            while chunk:=await file.read(1024*1024):
                total+=len(chunk)
                if total>100*1024*1024:
                    raise HTTPException(413,'El máximo es 100 MB por archivo.')
                stream.write(chunk)
        if not total:
            raise HTTPException(422,'Archivo vacío.')
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    job=job_new('Procesando medio local…',run_id)
    spawn(media_work(job,run_id,record_id,path))
    return job

@app.post('/api/runs/{run_id}/records/{record_id}/vision')
async def vision(run_id:str,record_id:str):
    ensure_editable(run_id)
    find_record(store.load_run(run_id),record_id)
    from app.vision import get_settings
    settings=await asyncio.to_thread(get_settings)
    if not settings.get('configured'):
        raise HTTPException(409,'Configura Gemini u Ollama en Ajustes IA antes de analizar imágenes.')
    job=job_new('Interpretando imágenes con '+settings['provider']+'…',run_id)
    spawn(media_work(job,run_id,record_id,vision=True))
    return job

@app.get('/api/runs/{run_id}/export')
async def export(run_id:str,format:Literal['json','md']='json'):
    run=store.load_run(run_id)
    return export_response(run,format)

def export_response(run,format):
    if format=='md':
        from app.analysis import build_markdown_report
        content=build_markdown_report(run)
        content_type='text/markdown'
    else:
        content=json.dumps(run,ensure_ascii=False,indent=2)
        content_type='application/json'
    return Response(content,media_type=content_type,headers={'Content-Disposition':f'attachment; filename="huella-{run["profile"]}.{format}"'})

@app.post('/api/demo')
async def demo():
    run=store.new_run('demo_ficticia',is_demo=True)
    captions=['Receta de pasta casera #cocina','Un viaje por la montaña #viajes','Aprender a decir adiós: una escena de película #cine','Canción sobre despedidas #musica','Una comedia para el fin de semana #humor','Cena entre amigos #amistad','Decoración y canciones de Navidad #navidad','Rutina para estudiar mejor #estudio']
    for i,text in enumerate(captions):
        row=store.empty_record('demo_ficticia',['uploads','reposts','likes'][i%3])
        row.update(caption=text,published_at=f'2025-{9+i//3:02d}-{2+i:02d}',interaction_at=f'2025-{9+i//3:02d}-{5+i:02d}' if i%3 else None,interaction_date_source='user_verified' if i%3 else None)
        row['notes']=['EJEMPLO FICTICIO. No corresponde a ningún perfil real.']
        run['records'].append(row)
    run.update(status='completed',collection={'method':'synthetic_demo','sources':{},'note':'Datos inventados exclusivamente para demostrar la interfaz.'})
    return analyze(run)

@app.get('/data/{path:path}')
async def artifact(path:str):
    target=store.media_path(path)
    if not target.is_file():
        raise HTTPException(404,'Evidencia no encontrada.')
    if target.suffix.lower() not in {'.png','.jpg','.jpeg','.webp','.mp4','.mov','.webm','.wav','.mp3','.m4a'}:
        raise HTTPException(403,'Tipo de archivo no disponible.')
    return FileResponse(target)

@app.get('/')
async def home():
    return FileResponse(store.ROOT/'app'/'static'/'index.html')

app.mount('/static',StaticFiles(directory=store.ROOT/'app'/'static',check_dir=False),name='static')
