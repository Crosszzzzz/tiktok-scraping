"""Atomic JSON persistence; all artifacts stay under a local data directory."""
from __future__ import annotations
import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('HUELLA_DATA_DIR', str(ROOT / 'data'))).resolve()
LOCK = threading.RLock()

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def profile_name(value: str) -> str:
    value = value.strip()
    if value.startswith(('https://', 'http://')):
        parsed = urlparse(value)
        if parsed.hostname not in {'tiktok.com', 'www.tiktok.com'} or parsed.username or parsed.port:
            raise ValueError('Usa un enlace de perfil de tiktok.com.')
        value = parsed.path.strip('/')
    elif value.startswith(('tiktok.com/', 'www.tiktok.com/')):
        value = value.split('/', 1)[1].strip('/')
    value = value.removeprefix('@')
    if not re.fullmatch(r'[A-Za-z0-9_.]{2,32}', value):
        raise ValueError('Perfil inválido. Ejemplo: @adry_zg26.')
    return value.lower()

def safe_id(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('Identificador inválido.')
    return value

def media_path(relative: str) -> Path:
    path = (DATA / relative).resolve()
    if not path.is_relative_to(DATA / 'artifacts'):
        raise ValueError('Ruta de evidencia inválida.')
    return path

def relative(path: Path | str) -> str:
    return Path(path).resolve().relative_to(DATA).as_posix()

def canonical_post_url(value: str) -> str:
    if not isinstance(value,str):
        raise ValueError('El enlace debe ser un texto.')
    if not value:
        return ''
    p = urlparse(value)
    if p.scheme != 'https' or p.hostname not in {'www.tiktok.com', 'tiktok.com'} or p.username or p.port:
        raise ValueError('La evidencia debe enlazar a un post HTTPS de TikTok.')
    if not re.fullmatch(r'/@[A-Za-z0-9_.]+/(video|photo)/[0-9]+/?', p.path):
        raise ValueError('Enlace de publicación inválido.')
    return 'https://www.tiktok.com' + p.path.rstrip('/')

def record_id(url: str, source: str) -> str:
    return hashlib.sha256(f'{source}|{url}'.encode()).hexdigest()[:20]

def empty_record(profile: str, source: str, url: str = '') -> dict:
    return dict(id=record_id(url, source) if url else uuid.uuid4().hex[:20], profile=profile,
                source=source, url=url, caption='', author='', published_at=None,
                interaction_at=None, interaction_date_source=None, observed_at=now(),
                first_seen_at=now(), order_index=0, music={}, screenshots=[], frames=[],
                ocr_text='', transcript='', visual_summary='', notes=[], hashtags=[], metrics={})

def save_run(run: dict) -> dict:
    with LOCK:
        directory = DATA / 'runs'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (safe_id(run['id']) + '.json')
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
    return run

def load_run(run_id: str) -> dict:
    with LOCK:
        return json.loads((DATA / 'runs' / (safe_id(run_id) + '.json')).read_text(encoding='utf-8'))

def list_runs() -> list[dict]:
    items = []
    with LOCK:
        for path in (DATA / 'runs').glob('*.json'):
            try:
                run = json.loads(path.read_text(encoding='utf-8'))
                items.append({k:run.get(k) for k in ['id','profile','created_at','status','is_demo']} | {'count':len(run.get('records',[]))})
            except (ValueError, OSError):
                continue
    return sorted(items, key=lambda x:x['created_at'], reverse=True)

def new_run(profile: str, **extra) -> dict:
    return dict(id=uuid.uuid4().hex, profile=profile, created_at=now(), status='pending',
                records=[], analysis={}, collection={'sources':{}}, **extra)

def first_seen(profile: str, url: str, source: str, fallback: str) -> str:
    dates = [fallback]
    for summary in list_runs():
        if summary['profile'] == profile and not summary.get('is_demo'):
            for row in load_run(summary['id']).get('records', []):
                if row.get('url') == url and row.get('source') == source:
                    dates.append(row.get('first_seen_at') or row.get('observed_at') or fallback)
    def instant(value):
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    return min(dates,key=instant)
