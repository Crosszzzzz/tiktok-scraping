"""Structured collection contracts, isolated from real accounts and network."""
import json
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app import store, media, tiktok_feed as feed
from app.analysis import safe_frame
from app.capture import BrowserSession, NeedsAttention
from app.incremental import CapturePlan
from app.planning import date_eligibility


def item(identifier='123', **changes):
    return {'id': identifier, 'author': {'uniqueId': 'creator'}, 'desc': 'Una receta',
            'createTime': 1700000000, 'video': {'cover': 'https://p16.tiktokcdn.com/cover.jpg'},
            'stats': {'diggCount': 12, 'playCount': 200}, **changes}


def batch(items=None, **changes):
    return {'statusCode': 0, 'itemList': [item()] if items is None else items,
            'hasMore': False, 'cursor': '30', **changes}


def test_normalize_preserves_original_author_and_does_not_invent_action_date():
    row = feed.normalize(item(repostTime=1700000010), 'reposts', 'example')
    assert row['url'] == 'https://www.tiktok.com/@creator/video/123'
    assert row['author'] == 'creator'
    assert row['published_date_source'] == 'tiktok_web_response'
    assert row['metrics'] == {'likes': 12, 'views': 200}
    assert 'interaction_at' not in row
    assert feed.normalize(item(), 'uploads', 'example') is None
    assert '/photo/' in feed.normalize(item(imagePost={'images': []}), 'likes', 'example')['url']


@pytest.mark.parametrize('value', [None, [], {}, {'id': 'bad'}, item(author=[]), item(author={'uniqueId': '../secret'})])
def test_bad_items_are_rejected(value):
    assert feed.normalize(value, 'reposts', 'example') is None


@pytest.mark.parametrize('url', ['http://p16.tiktokcdn.com/a', 'https://tiktokcdn.com.evil.org/a',
                                'https://localhost/a', 'https://127.0.0.1/a',
                                'https://user:password@p16.tiktokcdn.com/a', 'https://p16.tiktokcdn.com:8080/a'])
def test_cover_urls_are_restricted(url):
    assert feed.media_url(url) is None


def test_feed_binds_source_frame_identity_and_cursor_chain():
    page = SimpleNamespace(main_frame=object())
    collector = feed.Feed(page, 'reposts')
    def send(source='reposts', identity='correct', cursor='0', data=None, frame=None):
        collector.receive(SimpleNamespace(
            url=f'https://www.tiktok.com{feed.ENDPOINTS[source]}/?secUid={identity}&cursor={cursor}',
            request=SimpleNamespace(frame=page.main_frame if frame is None else frame),
            status=200, headers={}, body=lambda: json.dumps(data or batch()).encode()))
    send(source='uploads')
    send(frame=object())
    send(identity='other')
    send(cursor='99')
    send(data=batch(hasMore=True, cursor='30'))
    send(cursor='30', data=batch([item('456')]))
    assert len(collector.pages('correct')) == 2
    assert not collector.pages('absent')
    assert not collector.pages(None)


def test_invalid_feed_is_not_empty_success():
    page = SimpleNamespace(main_frame=object())
    collector = feed.Feed(page, 'likes')
    collector.receive(SimpleNamespace(url='https://www.tiktok.com/api/favorite/item_list/?secUid=x',
        request=SimpleNamespace(frame=page.main_frame), status=200, headers={},
        body=lambda: b'{"statusCode": 10221}'))
    assert collector.error == 'feed_invalid_response'
    assert collector.pages('x') == []


def test_cover_download_validates_decodes_and_reencodes(monkeypatch, tmp_path):
    image = BytesIO()
    Image.new('RGB', (20, 30), 'red').save(image, format='PNG')
    @contextmanager
    def stream(*args, **kwargs):
        assert kwargs['follow_redirects'] is False
        yield SimpleNamespace(status_code=200, headers={'content-type': 'image/png'},
                              raise_for_status=lambda: None, iter_bytes=lambda: iter([image.getvalue()]))
    monkeypatch.setattr(feed.httpx, 'stream', stream)
    target = tmp_path / 'cover.jpg'
    quality = feed.download_cover('https://p16.tiktokcdn.com/a', target)
    assert Image.open(target).format == 'JPEG'
    assert safe_frame(quality)
    assert not safe_frame({'source': 'tiktok_cdn', 'status': 'verified'})


class Page:
    def __init__(self, batches):
        self.batches = list(batches)
        self.main_frame = object()
        self.listener = None
        self.mouse = SimpleNamespace(wheel=lambda *args: self.deliver())
        self.selected = True
    def on(self, event, callback): self.listener = callback
    def remove_listener(self, event, callback): self.listener = None
    def evaluate(self, script): return {'uniqueId': 'example', 'secUid': 'target'}
    def wait_for_timeout(self, timeout): pass
    def get_attribute(self, name): return 'true' if self.selected else 'false'
    def click(self, **kwargs): self.selected = True
    def deliver(self):
        if self.batches:
            cursor, body = self.batches.pop(0)
            self.listener(SimpleNamespace(url=f'https://www.tiktok.com/api/repost/item_list/?secUid=target&cursor={cursor}',
                request=SimpleNamespace(frame=self.main_frame), status=200, headers={},
                body=lambda: json.dumps(body).encode()))


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(media, 'process_media', lambda *args: {'ocr_text': 'receta', 'notes': []})
    def download(url, target):
        Image.new('RGB', (10, 10)).save(target)
        return {'status': 'verified', 'source': 'tiktok_cdn', 'validation_version': 1, 'kind': 'thumbnail'}
    monkeypatch.setattr(feed, 'download_cover', download)
    def make(batches, known=None, limit=10):
        page = Page(batches)
        session = BrowserSession()
        session.options = {}
        session._goto = lambda url: page.deliver()
        session.prepare_page = lambda page: None
        session.wait_tab = lambda page, source: page
        run = store.new_run('example')
        directory = tmp_path / 'artifacts' / run['id']
        directory.mkdir(parents=True)
        planner = CapturePlan('example', known or {}, 'incremental', limit)
        return session, page, {}, run, 'reposts', directory, planner, 5
    return make


def test_collection_paginates_deduplicates_and_saves_local_cover(setup):
    args = setup([('0', batch(hasMore=True)), ('30', batch([item(), item('456')]))])
    assert feed.collect(*args)
    session, page, _, run, _, _, planner, _ = args
    assert page.listener is None
    assert len(run['records']) == 2
    assert planner.stats['processed_count'] == 2
    assert run['collection']['sources']['reposts']['stop_reason'] == 'feed_end'
    assert run['records'][0]['ocr_text'] == 'receta'
    assert store.media_path(run['records'][0]['frames'][0]).exists()
    assert run['records'][0]['interaction_at'] is None


def test_limit_and_incremental_reuse_preserve_manual_fields(setup):
    from app.profiles import record_key
    known = store.empty_record('example', 'reposts', 'https://www.tiktok.com/@creator/video/123')
    known.update(caption='Corrección', manual_fields={'caption': store.now()}, processing_status='complete')
    args = setup([('0', batch([item(), item('456'), item('789')], hasMore=True))],
                 known={record_key(known): known}, limit=1)
    assert feed.collect(*args)
    rows = args[3]['records']
    assert len(rows) == 2 and rows[0]['caption'] == 'Corrección'
    assert args[6].stats['reused_count'] == 1
    assert args[6].stats['processed_count'] == 1


def test_missing_feed_falls_back_and_removes_listener(setup):
    args = setup([])
    assert feed.collect(*args) is False
    assert args[1].listener is None
    assert not args[3]['records']


def test_challenge_stops_and_removes_listener(setup):
    args = setup([('0', batch())])
    def blocked(page): raise NeedsAttention('Verifica el acceso', 'captcha')
    args[0].prepare_page = blocked
    with pytest.raises(NeedsAttention): feed.collect(*args)
    assert args[1].listener is None


def test_missing_cover_keeps_metadata_partial(setup, monkeypatch):
    args = setup([('0', batch())])
    monkeypatch.setattr(feed, 'download_cover', lambda *args: (_ for _ in ()).throw(ValueError()))
    assert feed.collect(*args)
    assert args[3]['records'][0]['processing_status'] == 'partial'
    assert args[3]['collection']['sources']['reposts']['status'] == 'partial'


def test_empty_and_stalled_feeds_have_distinct_results(setup):
    args = setup([('0', batch([]))])
    assert feed.collect(*args)
    assert args[3]['collection']['sources']['reposts']['status'] == 'observed_empty'
    args = setup([('0', batch(hasMore=True))])
    assert feed.collect(*args)
    assert args[3]['collection']['sources']['reposts']['stop_reason'] == 'pagination_stalled'
    assert args[3]['collection']['sources']['reposts']['status'] == 'partial'


def test_publication_date_is_usable_but_not_for_reposts():
    options = {'date_filter': 'last30days', 'cutoff': '2023-11-01', 'end_date': '2023-11-30'}
    row = feed.normalize(item(), 'uploads', 'creator') | {'source': 'uploads'}
    assert date_eligibility(row, options) == 'included'
    row['source'] = 'reposts'
    assert date_eligibility(row, options) == 'unknown_date'


def test_date_exclusion_skips_ocr(setup, monkeypatch):
    args = setup([('0', batch())])
    args[0].options = {'date_filter': 'last30days', 'cutoff': '2026-01-01', 'end_date': '2026-01-31'}
    monkeypatch.setattr(media, 'process_media', lambda *args: pytest.fail('Excluded evidence reached OCR'))
    assert feed.collect(*args)
    assert args[3]['records'][0]['date_filter_status'] == 'unknown_date'
    assert args[3]['records'][0]['ocr_text'] == ''


def test_cancelled_scan_never_downloads(setup, monkeypatch):
    args = setup([('0', batch())])
    args[2]['cancel_requested'] = True
    monkeypatch.setattr(feed, 'download_cover', lambda *args: pytest.fail('Cancelled scan downloaded'))
    assert feed.collect(*args)
    assert not args[3]['records']
    assert args[1].listener is None


def test_parser_error_falls_back_without_discarding_prior_sources(setup, monkeypatch):
    args = setup([])
    monkeypatch.setattr(feed, 'profile_secuid', lambda *args: (_ for _ in ()).throw(ValueError()))
    session, page, job, run, source, directory, planner, rounds = args
    assert session.capture_structured_source(page, job, run, source, directory, planner, rounds) is False
    assert page.listener is None
    assert run['collection']['sources'][source]['structured_fallback'] == 'structured_capture_unavailable'


def test_main_collect_calls_new_pipeline_without_mandatory_dom_preview(monkeypatch, tmp_path):
    import asyncio
    from app import main
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(main.session, 'preview', lambda *args: pytest.fail('Mandatory DOM preview blocks structured feeds'))
    def capture(job, run, *args):
        run['records'].append(store.empty_record('example', 'reposts', 'https://www.tiktok.com/@creator/video/123'))
        run['collection']['sources']['reposts'].update(status='sampled', count=1, observed_minimum=1)
        run['status'] = 'completed'
    async def no_vision(*args): pass
    monkeypatch.setattr(main.session, 'capture', capture)
    monkeypatch.setattr(main, 'auto_vision', no_vision)
    monkeypatch.setattr(main, 'analyze', lambda run: run)
    job, run = {}, store.new_run('example')
    asyncio.run(main.collect(job, run, main.CaptureInput(profile='example', sources=['reposts'])))
    assert job['status'] == 'completed'
    assert len(run['records']) == 1
