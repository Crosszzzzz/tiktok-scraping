"""Read profile feeds delivered to our browser; normalize data before analysis.

Architecture reference: https://github.com/xtofuub/Repostify (MIT).
Independent Python implementation; no third-party service or patched browser.
"""
from datetime import datetime, timezone
from io import BytesIO
import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx
from PIL import Image

ENDPOINTS = {'uploads': '/api/post/item_list', 'reposts': '/api/repost/item_list',
             'likes': '/api/favorite/item_list'}
MAX_JSON = 4 * 1024 * 1024
MAX_IMAGE = 8 * 1024 * 1024
CDN_HOSTS = ('tiktokcdn.com', 'tiktokcdn-us.com', 'tiktokcdn-eu.com', 'ibytedtos.com', 'byteoversea.com')


def media_url(value):
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        host = url.hostname or ''
        if (url.scheme == 'https' and not url.username and not url.password
                and url.port in (None, 443)
                and any(host == suffix or host.endswith('.' + suffix) for suffix in CDN_HOSTS)):
            return value
    except ValueError:
        pass
    return None


def normalize(item, source, profile):
    """Only documented-in-response fields; publication time is never action time."""
    if not isinstance(item, dict):
        return None
    identifier = str(item.get('id', ''))
    author = item.get('author') or {}
    if not isinstance(author, dict):
        return None
    handle = author.get('uniqueId', '')
    if not isinstance(handle, str) or not re.fullmatch(r'[A-Za-z0-9_.]{1,64}', handle):
        return None
    if not re.fullmatch(r'[0-9]{1,30}', identifier):
        return None
    if source == 'uploads' and handle.lower() != profile.lower():
        return None
    photo = bool(item.get('imagePost'))
    result = {'url': f'https://www.tiktok.com/@{handle}/{"photo" if photo else "video"}/{identifier}',
              'author': handle, 'caption': item.get('desc', '') if isinstance(item.get('desc', ''), str) else '',
              'format': 'photo' if photo else 'video', 'metadata_source': 'tiktok_web_response'}
    timestamp = item.get('createTime')
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        try:
            instant = datetime.fromtimestamp(timestamp, timezone.utc)
            if 2016 <= instant.year and instant <= datetime.now(timezone.utc):
                result.update(published_at=instant.isoformat(), published_date_source='tiktok_web_response')
        except (ValueError, OverflowError, OSError):
            pass
    video = item.get('video') or {}
    if isinstance(video, dict):
        result['thumbnail_url'] = next((url for key in ('cover', 'originCover', 'dynamicCover')
                                        if (url := media_url(video.get(key)))), None)
    stats = item.get('statsV2') or item.get('stats') or {}
    if isinstance(stats, dict):
        result['metrics'] = {}
        for key, name in {'playCount': 'views', 'diggCount': 'likes', 'commentCount': 'comments',
                          'shareCount': 'shares', 'collectCount': 'saves'}.items():
            value = stats.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).isdigit():
                result['metrics'][name] = int(value)
    music = item.get('music')
    if isinstance(music, dict):
        result['music'] = {key: music.get(field, '') for key, field in [('title', 'title'), ('author', 'authorName')]
                           if isinstance(music.get(field, ''), str)}
        result['music']['source'] = 'tiktok_web_response'
    challenges = item.get('challenges')
    if isinstance(challenges, list):
        result['hashtags'] = [c['title'] for c in challenges[:100]
                              if isinstance(c, dict) and isinstance(c.get('title'), str)]
    return result


def profile_secuid(page, profile):
    """Bind every captured feed to the requested profile, not recommendations."""
    value = page.evaluate('''() => {
      const text = document.querySelector('#__UNIVERSAL_DATA_FOR_REHYDRATION__')?.textContent;
      if (text) {
        const data = JSON.parse(text).__DEFAULT_SCOPE__?.['webapp.user-detail'];
        if (data?.userInfo?.user) return data.userInfo.user;
      }
      const legacy = document.querySelector('#SIGI_STATE')?.textContent;
      return legacy ? Object.values(JSON.parse(legacy).UserModule?.users || {})[0] : null;
    }''')
    if isinstance(value, dict) and str(value.get('uniqueId', '')).lower() == profile.lower():
        return value.get('secUid') or None
    return None


class Feed:
    def __init__(self, page, source):
        self.page, self.source = page, source
        self.responses = []
        self.error = None

    def receive(self, response):
        try:
            url = urlsplit(response.url)
            if (url.hostname != 'www.tiktok.com' or url.path.rstrip('/') != ENDPOINTS[self.source]
                    or response.request.frame != self.page.main_frame):
                return
            query = parse_qs(url.query)
            if response.status != 200:
                self.error = 'feed_http_error'
                return
            if int(response.headers.get('content-length', '0')) > MAX_JSON:
                self.error = 'feed_too_large'
                return
            raw = response.body()
            if len(raw) > MAX_JSON:
                self.error = 'feed_too_large'
                return
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get('statusCode', -1) != 0 or not isinstance(data.get('itemList'), list):
                self.error = 'feed_invalid_response'
                return
            if len(self.responses) < 60:
                self.responses.append((query.get('secUid', [''])[0], query.get('cursor', ['0'])[0], data))
        except Exception:
            # Never leak signed request URLs, cookies or server error bodies.
            self.error = 'feed_unreadable'

    def pages(self, secuid):
        batches, expected = [], '0'
        for identity, cursor, data in self.responses:
            if not secuid or identity != secuid or cursor != expected:
                continue
            batches.append(data)
            if data.get('hasMore') in (False, 0):
                break
            next_cursor = str(data.get('cursor', ''))
            if not next_cursor or next_cursor == expected:
                break
            expected = next_cursor
        return batches


def download_cover(url, target):
    """Bounded CDN-only download, decoded and re-encoded; never forward cookies."""
    if not media_url(url):
        raise ValueError('Portada fuera de los dominios multimedia admitidos.')
    with httpx.stream('GET', url, timeout=15, follow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200 or not response.headers.get('content-type', '').lower().startswith('image/'):
            raise ValueError('Respuesta de portada inválida.')
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_IMAGE:
                raise ValueError('Portada demasiado grande.')
    with Image.open(BytesIO(data)) as image:
        if not 0 < image.width * image.height <= 20_000_000:
            raise ValueError('Dimensiones de portada inválidas.')
        image.load()
        image = image.convert('RGB')
        image.thumbnail((1600, 1600))
        image.save(target, 'JPEG', quality=90)
    return {'status': 'verified', 'source': 'tiktok_cdn', 'kind': 'thumbnail',
            'validation_version': 1}


def collect(session, page, job, run, source, directory, planner, rounds):
    """Observe native cursor pagination, retaining the DOM collector as fallback."""
    from copy import deepcopy
    from app import store, media
    from app.planning import date_eligibility

    feed = Feed(page, source)
    details = run['collection']['sources'].setdefault(source, {})
    page.on('response', feed.receive)
    rows, seen, pages_read = [], set(), 0
    stop = 'scan_limit'
    try:
        # A fresh navigation guarantees a cursor-zero response, including uploads.
        session.stage(run, source, 'reading_structured_feed')
        session._goto('https://www.tiktok.com/@' + run['profile'])
        session.prepare_page(page)
        secuid = profile_secuid(page, run['profile'])
        tab = session.wait_tab(page, source)
        if tab is None or not secuid:
            details['structured_fallback'] = 'profile_identity_or_tab_missing'
            return False
        if tab.get_attribute('aria-selected') != 'true':
            tab.click(timeout=5000)
        for _ in range(12):
            session.prepare_page(page)
            if job.get('cancel_requested'):
                return True
            if feed.pages(secuid) and tab.get_attribute('aria-selected') == 'true':
                break
            page.wait_for_timeout(500)
        if tab.get_attribute('aria-selected') != 'true' or not feed.pages(secuid):
            details['structured_fallback'] = feed.error or 'feed_not_observed'
            return False
        stalled = 0
        for turn in range(rounds):
            session.prepare_page(page)
            if job.get('cancel_requested'):
                stop = 'cancelled'
                break
            batches = feed.pages(secuid)
            fresh = batches[pages_read:]
            pages_read = len(batches)
            for batch in fresh:
                for item in batch['itemList']:
                    metadata = normalize(item, source, run['profile'])
                    if not metadata or metadata['url'] in seen:
                        continue
                    seen.add(metadata['url'])
                    url = metadata.pop('url')
                    planned = planner.consider(source, url, metadata, len(rows))
                    if planned is None:
                        continue
                    row, process = planned
                    rows.append(row)
                    run['records'].append(row)
                    row['date_filter_status'] = date_eligibility(row, session.options)
                    if process:
                        protected = {key: deepcopy(row.get(key)) for key in row.get('manual_fields', {})}
                        session.prepare_record_frames(row)
                        row['notes'].append('Portada de TikTok; no representa todas las escenas del video. Fecha de interacción desconocida.')
                        row['availability'].update(status='partial', audio_status='not_captured')
                        complete = False
                        try:
                            folder = directory / row['id']
                            folder.mkdir(exist_ok=True)
                            target = folder / 'cover.jpg'
                            quality = download_cover(metadata.get('thumbnail_url'), target)
                            path = store.relative(target)
                            row['frames'].append(path)
                            row['frame_quality'][path] = quality | {'observed_at': store.now()}
                            row['availability']['status'] = 'available'
                            if row['date_filter_status'] == 'included':
                                result = media.process_media(target, folder / 'ocr-cover')
                                row['ocr_text'] = result.get('ocr_text', '')
                                row['notes'].extend(result.get('notes', []))
                            complete = True
                        except Exception:
                            row['notes'].append('No se completó la portada/OCR; se conservan enlace y metadatos para reintentar.')
                        finally:
                            row['notes'] = list(dict.fromkeys(row['notes']))
                            row.update(protected)
                            planner.finish(row, complete)
                    job.update(message=f'{source}: {len(rows)} registros desde datos de TikTok…')
                    store.save_run(run)
                    if planner.at_limit(source):
                        break
                if planner.at_limit(source):
                    break
            details.update(method='tiktok_web_response', count=len(rows), observed_minimum=len(rows),
                           pages_observed=pages_read, status='sampled' if rows else 'unavailable')
            if planner.at_limit(source):
                stop = 'processing_limit'
                break
            if batches[-1].get('hasMore') in (False, 0):
                stop = 'feed_end'
                break
            stalled = 0 if fresh else stalled + 1
            if stalled >= 2 or feed.error:
                stop = feed.error or 'pagination_stalled'
                break
            if turn + 1 < rounds:
                # Let TikTok sign and perform its own next-cursor request.
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(1500)
        malformed = bool(seen) is False and any(batch['itemList'] for batch in feed.pages(secuid))
        partial = stop in {'pagination_stalled', 'feed_http_error', 'feed_invalid_response',
                           'feed_unreadable', 'feed_too_large'} or any(r.get('processing_status') == 'partial' for r in rows)
        details.update(status=('partial' if partial else 'sampled') if rows else
                       ('observed_empty' if stop == 'feed_end' and not malformed else 'unavailable'),
                       stop_reason=stop, method='tiktok_web_response', count=len(rows), observed_minimum=len(rows),
                       date_excluded_count=sum(r['date_filter_status'] == 'outside_window' for r in rows),
                       unknown_date_count=sum(r['date_filter_status'] == 'unknown_date' for r in rows),
                       reason='Muestra de respuestas de TikTok y portadas disponibles; no garantiza todo el historial ni el contenido completo de cada video.')
        planner.stats['stop_reasons'][source] = stop
        run['collection']['method'] = 'browser_with_structured_feeds'
        return True
    finally:
        page.remove_listener('response', feed.receive)
