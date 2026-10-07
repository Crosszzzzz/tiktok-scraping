"""Visible-page collector with a dedicated persistent Chrome profile.

No hidden endpoints, copied cookies, stealth patches, or CAPTCHA solving.
All Playwright work executes on one worker thread (Windows compatible).
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from copy import deepcopy
from urllib.parse import urlsplit, urlunsplit
from app import store
from app.incremental import CapturePlan

POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='huella-browser')
TABS = {'uploads':'videos-tab', 'reposts':'repost-tab', 'likes':'liked-tab'}
LABELS = {'uploads':r'^(V[ií]deos|Publicaciones|Posts)$', 'reposts':r'^(Publicaciones compartidas|Reposts?|Republicaciones|Compartidos|Compartidas)$', 'likes':r'^(Me gustó|Me gusta|Liked|Liked videos)$'}
CAPTCHA_SELECTOR = 'iframe[src*="captcha" i], iframe[src*="verify" i], iframe[title*="captcha" i], [data-e2e="captcha"], #captcha_container, .captcha_verify_container'
VERIFY_PATTERN = r'drag\s+the\s+slider|fit\s+the\s+puzzle|rotate\s+(?:the\s+)?(?:picture|image)|drag\s+the\s+puzzle|verify\s+to\s+continue|arrastra\s+(?:el\s+)?(?:rompecabezas|deslizador)|verifica\s+para\s+continuar|demuestra\s+que\s+eres\s+humano|gira\s+(?:la\s+)?imagen'
VERIFY_TEXT_CHECK = '''(body,pattern) => {
  const matches=new RegExp(pattern,'i');
  const excluded='article,[data-e2e="browse-video-desc"],[data-e2e="video-desc"],[data-e2e*="comment"],[data-e2e="user-bio"],[data-e2e="browse-user-bio"]';
  const walker=document.createTreeWalker(body,NodeFilter.SHOW_TEXT);
  let node,inspected=0;
  while((node=walker.nextNode())&&inspected++<5000){
    const element=node.parentElement;
    if(!element||element.closest(excluded)||element.closest('script,style,template'))continue;
    const own=(node.nodeValue||'').replace(/\\s+/g,' ').trim();
    const parent=(element.textContent||'').replace(/\\s+/g,' ').trim();
    if(!matches.test(own)&&!(parent.length<300&&matches.test(parent)))continue;
    const r=element.getBoundingClientRect(),style=getComputedStyle(element);
    if(r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth&&style.visibility!=='hidden'&&style.display!=='none'&&style.opacity!=='0')return true;
  }
  return false;
}'''
DISMISS_LABELS = (r'^(Entendido|Got it)$', r'^(Rechazar todo|Rechazar todas|Rechazar cookies opcionales|Reject all|Decline optional cookies|Permitir solo cookies necesarias|Only allow essential cookies)$')
OCCLUSION_CHECK = '''(e,minSide) => {
  const r=e.getBoundingClientRect(), style=getComputedStyle(e);
  const left=Math.max(r.left,0),top=Math.max(r.top,0),right=Math.min(r.right,innerWidth),bottom=Math.min(r.bottom,innerHeight);
  if(r.width<minSide||r.height<minSide||style.visibility==='hidden'||style.display==='none')return {clear:false,reason:'media_not_visible'};
  const fraction=Math.max(0,right-left)*Math.max(0,bottom-top)/(r.width*r.height);
  if(fraction<0.98)return {clear:false,reason:'media_clipped',visible_fraction:fraction};
  if(e.tagName==='VIDEO'&&(e.readyState<2||!e.videoWidth))return {clear:false,reason:'video_not_loaded'};
  const image=e.tagName==='IMG'?e:e.querySelector('img');
  if(image&&(!image.complete||!image.naturalWidth))return {clear:false,reason:'image_not_loaded'};
  let blocked=0;
  for(const x of [0.06,0.5,0.94])for(const y of [0.06,0.5,0.94]){
    const hit=document.elementFromPoint(r.left+r.width*x,r.top+r.height*y);
    if(!hit||(hit!==e&&!e.contains(hit)))blocked++;
  }
  return {clear:blocked===0,reason:blocked?'media_obscured':null,blocked_points:blocked,sampled_points:9,
          width:Math.round(r.width),height:Math.round(r.height),visible_fraction:fraction};
}'''

def parse_visible_date(text: str) -> str | None:
    """Only absolute, complete visible dates; never infer year from a short label."""
    match = re.search(r'\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b', text or '')
    if not match:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None

def parse_alt(text: str) -> dict:
    caption, author, music = text, '', {}
    match = re.match(r'(.*?)\s+(?:creado por|created by)\s+(.*?)\s+(?:con la música|with the music)\s+(.*)', text, re.S)
    if match:
        caption, author, song = (part.strip() for part in match.groups())
        music = {'title':song, 'author':'', 'url':'', 'source':'thumbnail_alt'}
    return {'caption':caption.strip(), 'author':author, 'music':music}

class NeedsAttention(RuntimeError):
    def __init__(self,message,code='needs_attention'):
        super().__init__(message)
        self.code=code

class EvidenceQualityError(RuntimeError):
    def __init__(self,message,code='media_obscured'):
        super().__init__(message)
        self.code=code

# Network/TLS hiccups that are worth a bounded retry. These are transient transport
# failures, not anti-bot signals; the collector never bypasses a challenge.
TRANSIENT_NAV_ERRORS = ('ERR_SSL_PROTOCOL_ERROR','ERR_CONNECTION_RESET','ERR_CONNECTION_CLOSED',
                        'ERR_CONNECTION_ABORTED','ERR_CONNECTION_REFUSED','ERR_TIMED_OUT',
                        'ERR_NETWORK_CHANGED','ERR_EMPTY_RESPONSE','ERR_HTTP2_PROTOCOL_ERROR',
                        'ERR_INTERNET_DISCONNECTED')

CHROME_CANDIDATES = (r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                     r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe')

def chrome_executable():
    """Locate the installed Chrome; the collector only ever drives the browser."""
    for path in CHROME_CANDIDATES:
        if os.path.isfile(path):
            return path
    local=os.path.join(os.environ.get('LOCALAPPDATA',''),'Google','Chrome','Application','chrome.exe')
    if local and os.path.isfile(local):
        return local
    found=shutil.which('chrome') or shutil.which('chrome.exe')
    if found:
        return found
    raise NeedsAttention('No se encontró Chrome instalado en este equipo.','chrome_not_found')

def open_login_browser(profile=''):
    """Open a normal (non-automated) Chrome window sharing the app profile.

    Login happens without any debugger attached, so TikTok's anti-automation
    page is not triggered; the session is then reused by the automated capture.
    """
    url='https://www.tiktok.com/login' if not profile else 'https://www.tiktok.com/@' + profile
    subprocess.Popen([chrome_executable(),'--user-data-dir=' + str(store.ROOT / '.browser-profile'),
                      '--new-window',url], close_fds=True)

class BrowserSession:
    def __init__(self):
        self.context = None
        self.playwright = None
        self.page = None
        self.headless = False
        self.status = 'Cerrado. Abre Chrome e inicia sesión manualmente.'
        self.job = None
        self.captcha_wait_seconds = 0

    def state(self):
        return {'open':self.context is not None, 'status':self.status, 'headless':self.headless,
                'profile_available':(store.ROOT/'.browser-profile'/'Default').is_dir()}

    def capture_diagnostic(self,run):
        """Bounded UI diagnostics; no body text, inputs, or cookies are returned."""
        diagnostic={'observed_at':store.now(),'path':None,'url':None,'tabs':[],
                    'dialogs':[],'session_signal':'unknown','captcha_visible':False,
                    'stage':run.get('collection',{}).get('stage'),'errors':[]}
        page=self.page
        try:
            if page is None or page.is_closed():
                diagnostic['errors'].append({'stage':'page','type':'PageUnavailable'})
                return diagnostic
        except Exception as exc:
            diagnostic['errors'].append({'stage':'page','type':type(exc).__name__})
            return diagnostic
        try:
            parsed=urlsplit(page.url)
            host=parsed.hostname or ''
            diagnostic['url']=urlunsplit((parsed.scheme,host,parsed.path,'',''))[:500]
        except Exception as exc:
            diagnostic['errors'].append({'stage':'url','type':type(exc).__name__})
        readers={
            'tabs':lambda:self.tabs_snapshot(page),
            'dialogs':lambda:page.locator('[role="dialog"], [aria-modal="true"]').evaluate_all('''els=>els.filter(e=>{
                const r=e.getBoundingClientRect(),s=getComputedStyle(e);
                return r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&s.visibility!=='hidden';
              }).slice(0,6).map(e=>({
                label:(e.getAttribute('aria-label')||e.querySelector('h1,h2,[role="heading"]')?.innerText||'').trim().slice(0,160),
                buttons:[...e.querySelectorAll('button,[role="button"]')].filter(b=>{const r=b.getBoundingClientRect();return r.width>0&&r.height>0;})
                  .slice(0,8).map(b=>(b.innerText||b.getAttribute('aria-label')||'').trim().slice(0,80))
              }))'''),
            'session_signal':lambda:'login_prompt_visible' if self.visible(page.get_by_role('button',name=re.compile(r'^(Iniciar sesi[oó]n|Inicia sesi[oó]n|Log in|Login)$',re.I))) is not None else 'unknown',
            'captcha_visible':lambda:self.verification_visible(page),
        }
        for key,reader in readers.items():
            try:
                diagnostic[key]=reader()
            except Exception as exc:
                diagnostic['errors'].append({'stage':key,'type':type(exc).__name__})
        try:
            directory=store.DATA/'artifacts'/store.safe_id(run['id'])
            directory.mkdir(parents=True,exist_ok=True)
            path=directory/'capture-diagnostic.jpg'
            page.screenshot(path=str(path),full_page=False,timeout=5000)
            diagnostic['path']=store.relative(path)
        except Exception as exc:
            diagnostic['errors'].append({'stage':'screenshot','type':type(exc).__name__})
        return diagnostic

    def open(self, profile='', headless=False):
        if self.context is not None and self.headless != headless:
            self.close()
        if self.context is None:
            from playwright.sync_api import sync_playwright
            if self.playwright:
                self.playwright.stop()
            self.playwright = sync_playwright().start()
            try:
                self.context = self.playwright.chromium.launch_persistent_context(
                    str(store.ROOT / '.browser-profile'), channel='chrome', headless=headless,
                    viewport={'width':1440, 'height':1000}, locale='es-ES',
                    accept_downloads=False)
            except Exception as exc:
                self.playwright.stop()
                self.playwright = None
                raise NeedsAttention('No se pudo abrir Chrome porque el perfil está en uso. Cerrá con la X la ventana de Chrome que usaste para iniciar sesión (no la dejes abierta) y volvé a intentar. Si no hay ninguna abierta, revisá que Chrome esté instalado.','chrome_profile_locked') from exc
            self.context.on('close', self._closed)
            self.headless=headless
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(8000)
        if self.page is None or self.page.is_closed():
            self.page = self.context.new_page()
        destination = 'https://www.tiktok.com/@' + profile if profile else 'https://www.tiktok.com/login'
        self._goto(destination)
        self.status = 'Sesión activa sin ventana.' if headless else 'Chrome abierto. Completa el inicio de sesión manual y luego inicia la captura.'
        return self.state()

    def _goto(self, url, attempts=5):
        """Navigate, retrying transient network/TLS failures. Never bypasses anti-bot."""
        last = None
        for attempt in range(attempts):
            try:
                self.page.goto(url, wait_until='domcontentloaded', timeout=45000)
                return
            except Exception as exc:
                last = exc
                transient = any(code in str(exc) for code in TRANSIENT_NAV_ERRORS)
                if attempt + 1 >= attempts or not transient:
                    break
                time.sleep(2.0 * (attempt + 1))
        raise NeedsAttention('No se pudo abrir TikTok en el navegador tras varios intentos. Revisa la conexión y vuelve a intentarlo.','navigation_failed') from last

    def _closed(self, *_):
        self.context = None
        self.page = None
        self.status = 'Chrome cerrado; la sesión permanece en el perfil local.'

    def close(self):
        failure=None
        try:
            if self.context:
                self.context.close()
        except Exception as exc:
            if type(exc).__name__!='TargetClosedError':
                failure=exc
        finally:
            try:
                if self.playwright:
                    self.playwright.stop()
            except Exception as exc:
                if type(exc).__name__!='TargetClosedError' and failure is None:
                    failure=exc
            finally:
                self.playwright=None
                self._closed()
        if failure is not None:
            raise failure
        return self.state()

    def text(self, page):
        return page.locator('body').inner_text(timeout=8000)

    @staticmethod
    def visible(locator):
        for index in range(min(locator.count(), 30)):
            item=locator.nth(index)
            if item.is_visible():
                return item
        return None

    @staticmethod
    def _challenge_text(scope):
        try:
            # This returns one boolean, never copies body text into evidence.
            return bool(scope.locator('body').evaluate(VERIFY_TEXT_CHECK,VERIFY_PATTERN,timeout=1500))
        except Exception:
            return False

    def verification_visible(self,page):
        if self.visible(page.locator(CAPTCHA_SELECTOR)) is not None:
            return True
        if self._challenge_text(page):
            return True
        # A rendered challenge may use an iframe URL with no captcha/verify
        # marker. Inspect only visible child frames; never interact with them.
        for frame in getattr(page,'frames',[]):
            if frame is getattr(page,'main_frame',None):
                continue
            try:
                element=frame.frame_element()
                if element.is_visible() and self._challenge_text(frame):
                    return True
            except Exception:
                continue
        return False

    def wait_for_manual_verification(self, page, job=None):
        """Pause while a human solves a visible verification, then resume.

        The collector never interacts with or solves the challenge: it only
        waits (bounded) and resumes once the challenge is no longer present.
        Returns True if verification was resolved, False on timeout or when a
        visible window is not available.
        """
        if not self.captcha_wait_seconds or self.headless:
            return False
        active = job if job is not None else self.job
        waited = 0
        while waited < self.captcha_wait_seconds:
            if active is not None:
                active['message'] = 'TikTok pide una verificación. Resuélvela en la ventana de Chrome; la captura seguirá sola.'
                active['waiting_for_verification'] = True
            page.wait_for_timeout(1000)
            waited += 1
            if not self.verification_visible(page):
                if active is not None:
                    active['message'] = 'Verificación resuelta. Reanudando la captura…'
                    active.pop('waiting_for_verification', None)
                return True
        if active is not None:
            active.pop('waiting_for_verification', None)
        return False

    def check_blockers(self, page):
        if self.verification_visible(page) and not self.wait_for_manual_verification(page):
            raise NeedsAttention('TikTok muestra una verificación. Ábrela en una ventana (desactiva «Capturar sin ventana») y resuélvela; la captura continuará sola.','verification_required')
        text = self.text(page).lower()
        if any(x in text for x in ['no se encontró esta cuenta','couldn’t find this account',"couldn't find this account"]):
            raise NeedsAttention('TikTok no encontró este perfil. Comprueba el nombre.','profile_not_found')
        if any(x in text for x in ['esta cuenta es privada','this account is private']):
            raise NeedsAttention('El perfil es privado. Solo se recopila contenido público accesible.','private_profile')
        if any(x in text for x in ['too many requests','demasiadas solicitudes','access denied']):
            raise NeedsAttention('TikTok ha limitado el acceso. Captura detenida; no se intentará evadir el bloqueo.','rate_limited')
        login=page.get_by_role('dialog').filter(has_text=re.compile(r'Inicia sesi[oó]n|Log in to TikTok',re.I))
        if login.count() and login.first.is_visible():
            raise NeedsAttention('TikTok solicita iniciar sesión. Usa Abrir sesión para completar el acceso manual.','login_required')

    def prepare_page(self,page):
        """Dismiss only named, harmless notices; never click a verification UI."""
        dismissed=[]
        for _ in range(3):
            self.check_blockers(page)
            button=None
            for pattern in DISMISS_LABELS:
                button=self.visible(page.get_by_role('button',name=re.compile(pattern,re.I)))
                if button is not None:
                    break
            if button is None:
                break
            label=(button.inner_text() or '').strip()[:100]
            button.click(timeout=2000)
            dismissed.append(label)
            page.wait_for_timeout(250)
        self.check_blockers(page)
        return dismissed

    def clean_screenshot(self,page,element,path,min_side=80):
        dismissed=self.prepare_page(page)
        element.scroll_into_view_if_needed(timeout=4000)
        page.mouse.move(0,0)
        page.wait_for_timeout(150)
        quality=element.evaluate(OCCLUSION_CHECK,min_side)
        if not isinstance(quality,dict) or not quality.get('clear'):
            code=quality.get('reason','media_obscured') if isinstance(quality,dict) else 'quality_unknown'
            raise EvidenceQualityError('El medio está tapado, recortado o todavía no cargó; no se procesa esta imagen.',code)
        element.screenshot(path=str(path),timeout=5000)
        try:
            self.check_blockers(page)
        except NeedsAttention:
            path.unlink(missing_ok=True)
            raise
        after=element.evaluate(OCCLUSION_CHECK,min_side)
        if not isinstance(after,dict) or not after.get('clear'):
            path.unlink(missing_ok=True)
            raise EvidenceQualityError('Apareció un elemento sobre el medio durante la captura; imagen descartada.')
        return dict(status='verified',source='profile_capture',observed_at=store.now(),
                    crop='element',occlusion_checked=True,dismissed_overlays=dismissed,**quality)

    @staticmethod
    def issue(run,source,code,message,status='unavailable'):
        issue={'source':source,'code':code,'message':message,'reason':message,'observed_at':store.now()}
        collection=run.setdefault('collection',{})
        collection.setdefault('issues',[]).append(issue)
        collection.setdefault('blockers',[]).append(issue)
        details=collection.setdefault('sources',{}).setdefault(source,{})
        details.update(status=status,reason=message)
        details.setdefault('issues',[]).append(issue)
        return issue

    @staticmethod
    def stage(run,source,value):
        run['collection'].setdefault('sources',{}).setdefault(source,{})['stage']=value

    def diagnostic_source(self,page,run,source,directory):
        try:
            directory.mkdir(parents=True,exist_ok=True)
            path=directory/f'{source}-diagnostic.jpg'
            page.screenshot(path=str(path),full_page=False,timeout=4000)
            run['collection'].setdefault('sources',{}).setdefault(source,{})['diagnostic_screenshot']=store.relative(path)
        except Exception:
            pass

    def tabs_snapshot(self,page):
        return page.locator('[role="tab"], [data-e2e="videos-tab"], [data-e2e="repost-tab"], [data-e2e="liked-tab"]').evaluate_all('''els => els.filter(e=>{
          const r=e.getBoundingClientRect();return r.width>0&&r.height>0;
        }).map(e=>({label:(e.innerText||e.getAttribute('aria-label')||'').trim().slice(0,100),
                   selector:e.getAttribute('data-e2e'),selected:e.getAttribute('aria-selected')}))''')

    def find_tab(self,page,source):
        tab=self.visible(page.locator(f'[data-e2e="{TABS[source]}"]'))
        return tab if tab is not None else self.visible(page.get_by_role('tab',name=re.compile(LABELS[source],re.I)))

    def wait_tab(self,page,source):
        for _ in range(20):
            self.prepare_page(page)
            tab=self.find_tab(page,source)
            if tab is not None:
                return tab
            page.wait_for_timeout(400)
        return None

    @staticmethod
    def cards(page):
        cards=page.locator('[id="main-content-others_homepage"] a[href*="/video/"], [id="main-content-others_homepage"] a[href*="/photo/"]').filter(visible=True)
        if not cards.count():
            cards=page.locator('[data-e2e="user-post-item-list"] a[href*="/video/"], [data-e2e="user-post-item-list"] a[href*="/photo/"]').filter(visible=True)
        return cards

    def wait_source_content(self,page,source):
        for _ in range(20):
            self.prepare_page(page)
            body=self.text(page).lower()
            if source=='likes' and any(text in body for text in ['videos que le gustan a este usuario son privados','liked videos are private','están ocultos actualmente']):
                return 'private'
            if self.cards(page).count() and self.visible(page.locator('[aria-busy="true"], [role="progressbar"]')) is None:
                return 'loaded'
            if any(text in body for text in ['no videos yet','aún no hay videos','todavía no hay publicaciones']):
                return 'empty_visible'
            page.wait_for_timeout(400)
        return 'loading_timeout'

    def select_source(self,page,run,source):
        collection=run['collection']
        self.stage(run,source,'waiting_for_tab')
        tab=self.wait_tab(page,source)
        if tab is None and not collection.get('profile_reload_attempted'):
            collection['profile_reload_attempted']=True
            page.reload(wait_until='domcontentloaded',timeout=30000)
            tab=self.wait_tab(page,source)
        collection['detected_tabs']=self.tabs_snapshot(page)
        if tab is None:
            self.issue(run,source,'tab_not_visible','La pestaña no apareció tras esperar. Puede depender de la sesión o de la versión de TikTok; no prueba ausencia de contenido.')
            collection['sources'][source]['tab_state']='not_visible'
            return False
        self.stage(run,source,'selecting_tab')
        if tab.get_attribute('aria-selected')!='true':
            tab.click(timeout=5000)
        for _ in range(15):
            self.prepare_page(page)
            if tab.get_attribute('aria-selected')=='true':
                break
            page.wait_for_timeout(300)
        if tab.get_attribute('aria-selected')!='true':
            self.issue(run,source,'tab_not_selected','No se pudo confirmar la selección de esta pestaña.')
            return False
        page.wait_for_timeout(350)
        self.stage(run,source,'waiting_for_content')
        content=self.wait_source_content(page,source)
        if content=='loading_timeout' and not collection.get('profile_reload_attempted'):
            collection['profile_reload_attempted']=True
            page.reload(wait_until='domcontentloaded',timeout=30000)
            return self.select_source(page,run,source)
        collection['detected_tabs']=self.tabs_snapshot(page)
        if content=='private':
            self.issue(run,source,'likes_private','TikTok indica que los me gusta están ocultos.','private')
            return False
        if content!='loaded':
            message='TikTok muestra una sección vacía.' if content=='empty_visible' else 'La pestaña se seleccionó pero su contenido no terminó de cargar. No se interpreta como una sección vacía.'
            self.issue(run,source,content,message)
            collection['sources'][source]['content_state']=content
            return False
        return True

    @staticmethod
    def prepare_record_frames(row):
        previous=row.get('availability')
        if isinstance(previous,dict) and previous:
            history=row.setdefault('availability_history',[])
            history.append({'observed_at':previous.get('observed_at') or row.get('processed_at'), 'availability':deepcopy(previous)})
            row['availability_history']=history[-20:]
        row['availability']={'status':'pending','audio_status':'not_captured','issues':[]}
        quality=row.get('frame_quality') or {}
        kept=[]
        archived=list(row.get('archived_frames') or [])
        legacy=bool(row.get('screenshots')) and row.get('processor_version') not in {'3'}
        for path in row.get('frames') or []:
            info=quality.get(path,{})
            if info.get('source')=='user_upload' or info.get('status') in {'user_provided','user_uploaded'}:
                kept.append(path)
            else:
                archived.append(path)
                legacy=legacy or info.get('status')!='verified'
        row['archived_frames']=list(dict.fromkeys(archived))
        row['frames']=kept
        row['frame_quality']={path:quality[path] for path in kept}
        row['diagnostic_screenshots']=list(dict.fromkeys((row.get('diagnostic_screenshots') or [])+(row.get('screenshots') or [])))
        row['screenshots']=[]
        if legacy:
            invalidated=list(row.get('quality_invalidated_fields') or [])
            for field in ('ocr_text','visual_summary','visual_analysis'):
                if row.get(field) and field not in (row.get('manual_fields') or {}):
                    row['archived_'+field]=deepcopy(row[field])
                    row[field]={} if field=='visual_analysis' else ''
                    invalidated.append(field)
            row['quality_invalidated_fields']=list(dict.fromkeys(invalidated))

    @staticmethod
    def note_login_requirement(run, sources, logged_out):
        """Warn clearly when login-gated sources are requested without a session."""
        if not logged_out:
            return
        labels={'reposts':'«Publicaciones compartidas»','likes':'«Me gusta»'}
        gated=[source for source in ('reposts','likes') if source in sources]
        if not gated:
            return
        run.setdefault('collection',{}).setdefault('issues',[]).append({
            'code':'login_required_for_sources',
            'message':'No hay sesión iniciada en el navegador de la app. TikTok oculta '+ ' y '.join(labels[source] for source in gated) +' a quienes no iniciaron sesión; usá «Abrir sesión» e iniciá sesión una vez para verlas.',
            'observed_at':store.now()})

    def capture(self, job:dict, run:dict, sources:list[str], max_items:int, scroll_rounds:int, mode:str='incremental',headless:bool=True,wait_captcha:bool=True):
        self.job = job
        self.captcha_wait_seconds = 240 if wait_captcha and not headless else 0
        run.setdefault('collection',{}).setdefault('sources',{})
        profile_url = 'https://www.tiktok.com/@' + run['profile']
        run['collection'].update(headless=headless,profile_url=profile_url,requested_sources=list(sources),stage='opening_browser')
        try:
            opened=False
            if not self.context or not self.page or self.page.is_closed() or self.headless != headless:
                self.open(run['profile'],headless=headless)
                opened=True
            page = self.page
            already_navigated=False
            if opened:
                try:
                    already_navigated=store.profile_name(page.url)==run['profile']
                except (ValueError,AttributeError):
                    pass
            if not already_navigated:
                run['collection']['stage']='navigating_profile'
                self._goto(profile_url)
            run['collection']['stage']='preparing_profile'
            page.wait_for_timeout(500)
            dismissed=self.prepare_page(page)
        except NeedsAttention as exc:
            for source in sources:
                self.issue(run,source,exc.code,str(exc),'needs_attention')
            raise
        artifact_dir = store.DATA / 'artifacts' / run['id']
        artifact_dir.mkdir(parents=True, exist_ok=True)
        from app.profiles import known_records
        planner = CapturePlan(run['profile'],known_records(run['profile']),mode,max_items)
        run['collection'].update({'method':'visible_browser', 'sample_limit_per_source':max_items,
                                   'incremental':planner.stats,
                                   'headless':headless,'issues':[], 'dismissed_overlays':dismissed,
                                   'scan_limit_rounds':scroll_rounds,
                                   'ordering':'Orden visible de la plataforma; fijados y ranking pueden alterarlo.',
                                   'profile_url':profile_url})
        run['collection']['stage']='reading_profile_header'
        login_prompt=self.visible(page.get_by_role('button',name=re.compile(r'^(Iniciar sesi[oó]n|Inicia sesi[oó]n|Log in|Login)$',re.I)))
        run['collection']['session_signal']='login_prompt_visible' if login_prompt is not None else 'unknown'
        self.note_login_requirement(run, sources, login_prompt is not None)
        title = page.locator('h1').first
        run['collection']['display_name'] = title.inner_text() if title.count() else ''
        avatar=page.locator('#main-content-others_homepage [data-e2e="user-avatar"] img, #main-content-others_homepage [data-e2e="user-avatar"], #main-content-others_homepage [data-e2e="user-avatar-container"] img').filter(visible=True)
        if not avatar.count():
            images=page.locator('#main-content-others_homepage img')
            index=images.evaluate_all('''els=>{
              const title=document.querySelector('#main-content-others_homepage h1'), t=title?.getBoundingClientRect();
              if(!t)return -1;
              return els.findIndex(e=>{const r=e.getBoundingClientRect();return r.width>=120&&r.width<=260&&Math.abs(r.width-r.height)<20&&Math.abs(r.top-t.top)<200&&r.bottom>0;});
            }''')
            if index>=0:
                avatar=images.nth(index)
        if avatar.count():
            try:
                avatar_path=artifact_dir/'profile-avatar.png'
                self.clean_screenshot(page,avatar.first,avatar_path,min_side=24)
                run['collection']['profile_avatar']={'path':store.relative(avatar_path),'source':'profile_capture','observed_at':store.now()}
            except NeedsAttention:
                raise
            except Exception:
                run['collection']['issues'].append({'code':'avatar_not_captured','message':'La foto del perfil no llegó a cargar.'})
        else:
            run['collection']['issues'].append({'code':'avatar_not_found','message':'No se pudo aislar la foto pública del perfil; puedes cargarla manualmente.'})
        for source in sources:
            if job.get('cancel_requested'):
                break
            job.update(message=f'Leyendo {source}…', status='running')
            run['collection']['stage']='reading_source_'+source
            try:
                if self.select_source(page,run,source):
                    self._capture_source(page,job,run,source,sources,artifact_dir,planner,scroll_rounds)
                else:
                    self.diagnostic_source(page,run,source,artifact_dir)
            except NeedsAttention as exc:
                self.issue(run,source,exc.code,str(exc),'needs_attention')
                self.diagnostic_source(page,run,source,artifact_dir)
                store.save_run(run)
                raise
            except Exception as exc:
                stage=run['collection']['sources'].get(source,{}).get('stage','unknown')
                self.issue(run,source,'source_capture_failed',f'La sección no pudo terminar de leerse en la etapa {stage} ({type(exc).__name__}); se conserva lo ya observado.')
                self.diagnostic_source(page,run,source,artifact_dir)
            store.save_run(run)
        run['status'] = 'cancelled' if job.get('cancel_requested') else 'completed'
        if not job.get('cancel_requested') and not run['records'] and run['collection'].get('blockers'):
            run['status']='needs_attention'
            run['collection']['last_capture_reason']='No se logró leer ninguna sección solicitada. Revisa los diagnósticos y la sesión manual; no se interpreta como un perfil vacío.'
        return run

    def _capture_source(self,page,job,run,source,sources,artifact_dir,planner,scroll_rounds):
        self.stage(run,source,'reading_grid')
        found = {}
        stagnant = 0
        stop_reason = 'scroll_limit'
        page.keyboard.press('Control+Home')
        for turn in range(scroll_rounds):
            if job.get('cancel_requested'):
                stop_reason = 'cancelled'
                break
            # Scope to the profile area; sidebar recommendations are not evidence.
            self.prepare_page(page)
            cards = self.cards(page)
            rows = cards.evaluate_all("els => els.map(e => ({url:e.href, alt:e.querySelector('img')?.alt || e.getAttribute('aria-label') || '', grid_metric:e.innerText, pinned:!!e.closest('[data-e2e=\"user-post-item\"]')?.innerText.match(/Fijado|Pinned/)}))")
            before = len(found)
            for card_index, item in enumerate(rows):
                try:
                    self.stage(run,source,'reading_grid')
                    url = store.canonical_post_url(item['url'])
                except ValueError:
                    continue
                if source == 'uploads' and url.split('/')[3].lower() != '@' + run['profile']:
                    continue
                if url in found:
                    continue
                metadata = parse_alt(item['alt']) | {'format':'photo' if '/photo/' in url else 'video',
                                                   'pinned':item['pinned'],'grid_metric_raw':item['grid_metric']}
                plan = planner.consider(source,url,metadata,len(found))
                if plan is None:
                    continue
                row,needs_processing = plan
                found[url] = (row,needs_processing)
                run['records'].append(row)
                if not needs_processing:
                    continue
                self.prepare_record_frames(row)
                if 'Orden de rejilla observado; no es fecha de interacción.' not in row['notes']:
                    row['notes'].append('Orden de rejilla observado; no es fecha de interacción.')
                cover_dir = artifact_dir / row['id']
                cover_dir.mkdir(exist_ok=True)
                try:
                    self.stage(run,source,'cropping_grid_card')
                    card = cards.nth(card_index)
                    href = card.get_attribute('href') or ''
                    if href.startswith('/'):
                        href = 'https://www.tiktok.com' + href
                    if store.canonical_post_url(href) == url:
                        cover = cover_dir / 'grid-card.jpg'
                        quality=self.clean_screenshot(page,card,cover)
                        path=store.relative(cover)
                        row['frames'].append(path)
                        row['frame_quality'][path]=quality | {'kind':'grid_card'}
                        row['notes'].append('Incluye miniatura recortada de la tarjeta; no representa todas las escenas o diapositivas.')
                except NeedsAttention:
                    raise
                except EvidenceQualityError as exc:
                    self.record_detail_issue(run,row,source,exc.code,str(exc))
                except Exception:
                    row['notes'].append('Miniatura no capturada; la tarjeta cambió o no llegó a cargar.')
            store.save_run(run)
            if planner.at_limit(source):
                stop_reason = 'processing_limit'
                break
            stagnant = stagnant + 1 if len(found) == before else 0
            if stagnant >= 2:
                stop_reason = 'no_more_loaded'
                break
            page.mouse.wheel(0, 850)
            page.wait_for_timeout(1200)
            self.prepare_page(page)
        planner.stats['stop_reasons'][source] = stop_reason
        overview = artifact_dir / f'{source}-grid.jpg'
        self.prepare_page(page)
        page.screenshot(path=str(overview), full_page=False)
        prior=run['collection']['sources'].get(source,{})
        run['collection']['sources'][source] = prior | {'status':('partial' if prior.get('issues') else 'sampled') if found else 'unavailable',
            'count':len(found), 'diagnostic_screenshot':store.relative(overview), 'stop_reason':stop_reason,
            'reason':'Rejilla revisada con límite; no garantiza todo el historial. Las ausencias no se interpretan como eliminación.' if found else 'Sin elementos cargados. Puede requerir sesión, otra versión de TikTok o más tiempo.'}
        work = [row for row,needs_processing in found.values() if needs_processing]
        store.save_run(run)
        if not work:
            return
        # A separate tab keeps the selected source and scroll position intact.
        detail = self.context.new_page()
        detail.set_default_timeout(6000)
        try:
            for index, row in enumerate(work):
                if job.get('cancel_requested'):
                    break
                job.update(message=f'{source}: procesando {index+1}/{len(work)}; {planner.stats["reused_count"]} reutilizadas',
                           progress=round(90 * (sources.index(source) + (index+1)/max(1,len(work)))/len(sources)))
                protected = {key:deepcopy(row.get(key)) for key in row.get('manual_fields',{})}
                try:
                    self.stage(run,source,'extracting_detail')
                    complete=self.capture_detail(detail, row, artifact_dir)
                    planner.finish(row,complete is not False)
                    if complete is False:
                        self.record_detail_issue(run,row,source,'primary_media_not_found','No se pudo aislar el medio principal; solo se conserva evidencia recortada verificable.')
                except NeedsAttention as exc:
                    planner.finish(row,False)
                    self.record_detail_issue(run,row,source,exc.code,str(exc))
                    row.update(protected)
                    store.save_run(run)
                    raise
                except Exception as exc:
                    planner.finish(row,False)
                    self.record_detail_issue(run,row,source,getattr(exc,'code','detail_capture_failed'),str(exc)[:400] or type(exc).__name__)
                    row['notes'].append('Detalle incompleto: ' + type(exc).__name__ + '. Conservada evidencia de la rejilla.')
                row.update(protected)
                store.save_run(run)
        finally:
            detail.close()

    def record_detail_issue(self,run,row,source,code,message):
        availability=row.setdefault('availability',{'status':'partial','issues':[]})
        if availability.get('status')!='unavailable':
            availability['status']='partial'
        availability.setdefault('issues',[]).append({'code':code,'message':message,'basis':'capture_quality'})
        self.issue(run,source,code,message,'partial')

    @staticmethod
    def same_post(current,row):
        from app.profiles import record_key
        try:
            return record_key({'url':current,'source':row['source']})==record_key(row)
        except ValueError:
            return False

    def primary_media(self,page,row):
        candidates=page.locator('video') if row.get('format')!='photo' and '/photo/' not in row['url'] else page.locator('article img, main img, [id="main-content-video_detail"] img')
        post_id=row['url'].rstrip('/').split('/')[-1]
        index=candidates.evaluate_all('''(els,postId) => {
          const candidates=els.map((e,index)=>{
            const r=e.getBoundingClientRect(),article=e.closest('article');
            const links=article?[...article.querySelectorAll('a[href*="/video/"],a[href*="/photo/"]')].map(a=>a.href):[];
            const wrong=links.length&&!links.some(h=>new RegExp('/(?:video|photo)/'+postId+'(?:[/?#]|$)').test(h));
            const hidden=e.closest('[aria-hidden="true"]');
            const area=wrong||hidden?0:Math.max(0,Math.min(r.right,innerWidth)-Math.max(r.left,0))*Math.max(0,Math.min(r.bottom,innerHeight)-Math.max(r.top,0));
            return {index,area};
          }).filter(x=>x.area>10000).sort((a,b)=>b.area-a.area);
          return candidates.length?candidates[0].index:-1;
        }''',post_id)
        return candidates.nth(index) if index>=0 else None

    def capture_detail(self, page, row, directory: Path):
        page.goto(row['url'], wait_until='domcontentloaded', timeout=35000)
        page.wait_for_timeout(500)
        row.setdefault('dismissed_overlays',[]).extend(self.prepare_page(page))
        if not self.same_post(page.url,row):
            raise ValueError('La reproducción cambió de publicación; detalle descartado para evitar atribución incorrecta.')
        page.locator('video').evaluate_all('els => els.forEach(e => { e.pause(); e.muted=true; })')
        primary=None
        for _ in range(8):
            self.prepare_page(page)
            primary=self.primary_media(page,row)
            if primary is not None and primary.evaluate("e=>e.tagName==='VIDEO'?(e.readyState>=2&&e.videoWidth>0):(e.complete&&e.naturalWidth>0)"):
                break
            page.wait_for_timeout(400)
        is_photo=row.get('format')=='photo' or '/photo/' in row['url']
        video=primary if not is_photo else None
        # Modern TikTok renders several recommendation articles simultaneously.
        # Use the on-screen player's article, never another loaded recommendation.
        article = primary.locator('xpath=ancestor::article[1]') if primary is not None else None
        scope = article if article is not None and article.count() else page
        from app.availability import assess_notices
        notices=scope.locator('[role="alert"], [role="status"], [data-e2e="video-error"], [data-e2e="video-mute-notice"]').all_text_contents()
        earlier_issues=row.get('availability',{}).get('issues',[])
        row['availability']=assess_notices('\n'.join(notices),video_present=primary is not None)
        row['availability']['observed_at']=store.now()
        row['availability']['issues'].extend(earlier_issues)
        # Specific rendered fields avoid incorporating comments and recommended videos.
        def field(selector):
            if primary is None:
                return ''
            values = scope.locator(selector).evaluate_all('''els => els.filter(e=>{
              const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth;
            }).map(e=>e.innerText)''')
            return next((value[:12000] for value in values if value), '')
        caption = field('[data-e2e="browse-video-desc"], [data-e2e="video-desc"]')
        if caption:
            row['caption'] = caption
        author = field('[data-e2e="browse-username"], [data-e2e="video-author-uniqueid"]')
        if author:
            row['author'] = author
        music = scope.locator('a[href*="/music/"]').filter(visible=True)
        if music.count():
            row['music'] = {'title':music.first.inner_text()[:500], 'author':'', 'url':music.first.get_attribute('href'), 'source':'visible_link'}
        visible_date = field('[data-e2e="browser-nickname"], [data-e2e="video-create-time"], time')
        row['published_at'] = parse_visible_date(visible_date)
        row['published_date_raw'] = visible_date
        row['published_date_source'] = 'visible' if row['published_at'] else None
        row['hashtags'] = re.findall(r'#[\w]+', row['caption'], re.UNICODE)
        for name, selector in {'likes':'like-count','comments':'comment-count','shares':'share-count','saves':'collect-count'}.items():
            raw = field(f'[data-e2e="{selector}"]')
            if raw:
                row['metrics'][name] = {'raw':raw, 'observed_at':row['observed_at']}
        output = directory / row['id']
        output.mkdir(exist_ok=True)
        full = output / 'page.jpg'
        self.prepare_page(page)
        page.screenshot(path=str(full), full_page=False)
        row.setdefault('diagnostic_screenshots',[]).append(store.relative(full))
        if row['availability']['status']=='unavailable':
            return True
        # Multiple rendered frames retain scene context. Audio needs a provided media file.
        frame_paths = []
        row.setdefault('frame_quality',{})
        if video is not None:
            duration = video.evaluate('e => Number.isFinite(e.duration) ? e.duration : 0')
            for i in range(3):
                if duration > 0:
                    video.evaluate('(e,t) => {e.pause(); e.currentTime=t;}', min(duration - 0.1, duration * (i + 1) / 4))
                    page.wait_for_timeout(400)
                if not self.same_post(page.url,row):
                    raise ValueError('El reproductor cambió de publicación.')
                target = output / f'frame-{i+1}.jpg'
                quality=self.clean_screenshot(page,video,target)
                relative=store.relative(target)
                row['frame_quality'][relative]=quality | {'kind':'video_frame'}
                if relative not in row['frames']:
                    row['frames'].append(relative)
                frame_paths.append(target)
        elif primary is not None:
            seen_images=set()
            for index in range(6):
                if not self.same_post(page.url,row):
                    raise ValueError('El carrusel cambió de publicación.')
                current=self.primary_media(page,row)
                if current is None:
                    break
                image_key=current.evaluate('e=>e.currentSrc||e.src')
                if image_key in seen_images:
                    break
                target=output/f'photo-{index+1}.jpg'
                quality=self.clean_screenshot(page,current,target)
                relative=store.relative(target)
                row['frame_quality'][relative]=quality | {'kind':'photo_slide','slide_index':index}
                if relative not in row['frames']:
                    row['frames'].append(relative)
                frame_paths.append(target)
                seen_images.add(image_key)
                next_button=self.visible(scope.locator('[data-e2e="photo-next"], button[aria-label="Next slide"], button[aria-label="Siguiente diapositiva"], [class*="swiper-button-next"]'))
                if next_button is None or next_button.get_attribute('aria-disabled')=='true' or next_button.get_attribute('disabled') is not None:
                    break
                next_button.click(timeout=2500)
                page.wait_for_timeout(400)
                self.prepare_page(page)
            row['notes'].append(f'Carrusel: {len(frame_paths)} diapositivas visibles capturadas (límite 6). No garantiza el carrusel completo.')
        else:
            frame_paths = [store.media_path(p) for p in row.get('frames',[]) if p.endswith('/grid-card.jpg') and row['frame_quality'].get(p,{}).get('status')=='verified']
            row['notes'].append('Medio principal no aislado: OCR limitado a la tarjeta recortada y verificada, si está disponible. La página es solo diagnóstico.')
        from app.media import process_media
        texts = []
        for index, target in enumerate(frame_paths):
            result = process_media(target, output / f'ocr-{index}')
            if store.relative(target) not in row['frames']:
                row['frames'].append(store.relative(target))
            if result.get('ocr_text') and result['ocr_text'] not in texts:
                texts.append(result['ocr_text'])
            row['notes'].extend(result.get('notes', []))
        if texts:
            row['ocr_text'] = '\n\n'.join(texts)
        row['notes'] = list(dict.fromkeys(row['notes']))
        row['notes'].append('La canción es una etiqueta de TikTok; no se ha identificado el audio ni transcrito la letra automáticamente.')
        if not row['published_at']:
            row['notes'].append('Fecha absoluta de publicación no visible o no interpretable; no se adivinó el año.')
        return primary is not None

session = BrowserSession()
