"""Capture quality decisions with small browser fakes; no live TikTok access."""
from pathlib import Path
from types import SimpleNamespace
import re

import pytest

from app import capture, media, profiles, store


class Locator:
    def __init__(self, nodes=(), texts=(), snapshot=None):
        self.nodes=list(nodes)
        self.texts=list(texts)
        self.snapshot=snapshot
    def count(self): return len(self.nodes)
    def nth(self,index): return self.nodes[index]
    @property
    def first(self): return self.nodes[0] if self.nodes else self
    def is_visible(self): return False
    def filter(self,**kwargs): return self
    def evaluate_all(self,*args): return self.snapshot() if self.snapshot else []
    def all_text_contents(self): return self.texts


class Element:
    def __init__(self,text='',visible=True,quality=None,on_click=None,attrs=None):
        self.text=text
        self.visible=visible
        self.qualities=list(quality or [{'clear':True,'sampled_points':9,'blocked_points':0}])
        self.on_click=on_click
        self.attrs=attrs or {}
        self.screenshots=[]
        self.clicks=0
    def is_visible(self): return self.visible
    def inner_text(self,**kwargs): return self.text
    def click(self,**kwargs):
        self.clicks+=1
        if self.on_click: self.on_click()
    def get_attribute(self,key): return self.attrs.get(key)
    def locator(self,*args): return Locator()
    def scroll_into_view_if_needed(self,**kwargs): pass
    def evaluate(self,script,*args,**kwargs):
        if script == capture.VERIFY_TEXT_CHECK:
            return re.search(args[0],self.text,re.I) is not None
        if script == capture.OCCLUSION_CHECK:
            return self.qualities.pop(0) if len(self.qualities)>1 else self.qualities[0]
        if 'e.duration' in script: return 0
        if 'e.currentSrc' in script: return 'photo-one'
        return True
    def screenshot(self,path,**kwargs):
        self.screenshots.append(path)
        Path(path).write_bytes(b'fake screenshot bytes')


class Page:
    def __init__(self):
        self.body=Element('')
        self.buttons=[]
        self.captchas=[]
        self.url='https://www.tiktok.com/@creator/video/123'
        self.ticks=0
        self.reloads=0
        self.diagnostics=[]
        self.goto_calls=[]
        self.mouse=SimpleNamespace(move=lambda *args:None)
    def locator(self,selector):
        if selector=='body': return self.body
        if selector==capture.CAPTCHA_SELECTOR: return Locator(self.captchas)
        return Locator()
    def get_by_role(self,role,**kwargs):
        if role=='button':
            pattern=kwargs.get('name')
            return Locator([button for button in self.buttons if pattern.search(button.text)])
        return Locator()
    def wait_for_timeout(self,duration): self.ticks+=1
    def reload(self,**kwargs): self.reloads+=1
    def goto(self,url,**kwargs):
        self.goto_calls.append(url)
        self.url=url
    def is_closed(self): return False
    def screenshot(self,path,**kwargs):
        self.diagnostics.append(path)
        Path(path).write_bytes(b'diagnostic screenshot')


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path.resolve())


def test_onboarding_is_dismissed_before_media_capture(tmp_path):
    page=Page()
    overlay=Element('Entendido',on_click=lambda:setattr(overlay,'visible',False))
    page.buttons=[overlay]
    session=capture.BrowserSession()
    target=tmp_path/'frame.jpg'
    quality=session.clean_screenshot(page,Element(),target)
    assert overlay.clicks==1
    assert quality['status']=='verified' and quality['dismissed_overlays']==['Entendido']
    assert quality['occlusion_checked'] and target.exists()


def test_captcha_iframe_stops_before_clicking_any_notice_or_screenshot(tmp_path):
    page=Page()
    page.captchas=[Element(visible=False),Element(visible=True)]
    notice=Element('Got it')
    page.buttons=[notice]
    media_element=Element()
    with pytest.raises(capture.NeedsAttention) as exc:
        capture.BrowserSession().clean_screenshot(page,media_element,tmp_path/'bad.jpg')
    assert exc.value.code=='verification_required'
    assert notice.clicks==0 and media_element.screenshots==[]


def test_visible_captcha_pauses_and_resumes_after_human_solves_without_clicking():
    page=Page()
    challenge=Element(visible=True)
    page.captchas=[challenge]
    session=capture.BrowserSession()
    session.headless=False
    session.captcha_wait_seconds=5
    job={}
    original=page.wait_for_timeout
    def tick(duration):
        original(duration)
        if page.ticks>=2:
            challenge.visible=False
    page.wait_for_timeout=tick
    assert session.wait_for_manual_verification(page,job) is True
    assert 'Reanudando' in job['message'] and 'waiting_for_verification' not in job
    assert page.buttons==[] and challenge.clicks==0


def test_captcha_wait_times_out_without_attempting_to_solve():
    page=Page()
    page.captchas=[Element(visible=True)]
    session=capture.BrowserSession()
    session.headless=False
    session.captcha_wait_seconds=2
    job={}
    assert session.wait_for_manual_verification(page,job) is False
    assert 'waiting_for_verification' not in job


def test_headless_capture_never_waits_for_captcha():
    page=Page()
    page.captchas=[Element(visible=True)]
    session=capture.BrowserSession()
    session.headless=True
    session.captcha_wait_seconds=5
    assert session.wait_for_manual_verification(page,{}) is False


def test_only_named_notice_buttons_are_clicked_and_cookie_rejection_is_safe():
    page=Page()
    unrelated=Element('Continuar')
    reject=Element('Rechazar todo',on_click=lambda:setattr(reject,'visible',False))
    page.buttons=[unrelated,reject]
    capture.BrowserSession().prepare_page(page)
    assert reject.clicks==1 and unrelated.clicks==0


@pytest.mark.parametrize('reason',['media_obscured','media_clipped','video_not_loaded'])
def test_obstructed_or_unloaded_media_never_becomes_an_image(tmp_path,reason):
    media_element=Element(quality=[{'clear':False,'reason':reason}])
    with pytest.raises(capture.EvidenceQualityError) as exc:
        capture.BrowserSession().clean_screenshot(Page(),media_element,tmp_path/'bad.jpg')
    assert exc.value.code==reason
    assert not (tmp_path/'bad.jpg').exists() and not media_element.screenshots


def test_overlay_appearing_during_screenshot_discards_the_written_image(tmp_path):
    media_element=Element(quality=[{'clear':True},{'clear':False,'reason':'media_obscured'}])
    with pytest.raises(capture.EvidenceQualityError):
        capture.BrowserSession().clean_screenshot(Page(),media_element,tmp_path/'bad.jpg')
    assert len(media_element.screenshots)==1 and not (tmp_path/'bad.jpg').exists()


def test_detail_with_obscured_player_never_calls_ocr(tmp_path,monkeypatch):
    page=Page()
    player=Element(quality=[{'clear':False,'reason':'media_obscured'}])
    session=capture.BrowserSession()
    monkeypatch.setattr(session,'primary_media',lambda page,row:player)
    calls=[]
    monkeypatch.setattr(media,'process_media',lambda *args:calls.append(args))
    record=store.empty_record('example','reposts',page.url)
    with pytest.raises(capture.EvidenceQualityError):
        session.capture_detail(page,record,tmp_path)
    assert calls==[] and record['frames']==[]
    assert record['screenshots']==[]
    assert len(record['diagnostic_screenshots'])==1


def test_clean_detail_passes_only_element_crops_to_ocr(tmp_path,monkeypatch):
    directory=store.DATA/'artifacts'/'run'
    directory.mkdir(parents=True)
    page=Page()
    player=Element()
    session=capture.BrowserSession()
    monkeypatch.setattr(session,'primary_media',lambda page,row:player)
    calls=[]
    def ocr(path,*args):
        calls.append(path.name)
        return {'ocr_text':'Texto del video','notes':[]}
    monkeypatch.setattr(media,'process_media',ocr)
    record=store.empty_record('example','reposts',page.url)
    assert session.capture_detail(page,record,directory) is True
    assert calls==['frame-1.jpg','frame-2.jpg','frame-3.jpg']
    assert all(record['frame_quality'][path]['status']=='verified' for path in record['frames'])
    assert record['ocr_text']=='Texto del video'
    assert not any(path.endswith('page.jpg') for path in record['frames'])


class SourcePage(Page):
    def __init__(self,tab_after=0,content_after=0,missing=False,load_on_reload=False):
        super().__init__()
        self.tab_after=tab_after
        self.content_after=content_after
        self.missing=missing
        self.load_on_reload=load_on_reload
        self.tab=Element('Publicaciones compartidas',attrs={'aria-selected':'false'})
        self.tab.on_click=lambda:self.tab.attrs.update({'aria-selected':'true'})
    def tab_visible(self): return not self.missing and self.ticks>=self.tab_after
    def locator(self,selector):
        if selector=='[data-e2e="repost-tab"]': return Locator([self.tab] if self.tab_visible() else [])
        if selector.startswith('[role="tab"],'):
            return Locator(snapshot=lambda:[{'label':self.tab.text,'selector':'repost-tab','selected':self.tab.attrs['aria-selected']}] if self.tab_visible() else [])
        if 'a[href*="/video/"]' in selector:
            loaded=self.tab.attrs['aria-selected']=='true' and (self.ticks>=self.content_after or self.load_on_reload and self.reloads>0)
            return Locator([Element()] if loaded else [])
        return super().locator(selector)
    def get_by_role(self,role,**kwargs):
        if role=='tab': return Locator([self.tab] if self.tab_visible() and kwargs['name'].search(self.tab.text) else [])
        return super().get_by_role(role,**kwargs)


def test_repost_tab_and_content_are_waited_for_instead_of_assumed_missing():
    page=SourcePage(tab_after=5,content_after=12)
    run={'collection':{'sources':{}}}
    assert capture.BrowserSession().select_source(page,run,'reposts') is True
    assert page.ticks>=12 and page.reloads==0
    assert run['collection']['detected_tabs'][0]['selector']=='repost-tab'
    assert not run['collection'].get('blockers')


def test_unloaded_content_gets_one_bounded_reload_then_can_recover():
    page=SourcePage(content_after=999,load_on_reload=True)
    run={'collection':{'sources':{}}}
    assert capture.BrowserSession().select_source(page,run,'reposts') is True
    assert page.reloads==1 and page.ticks<50


def test_missing_tab_is_unknown_visibility_with_scoped_and_global_issue():
    page=SourcePage(missing=True)
    run={'collection':{'sources':{}}}
    assert capture.BrowserSession().select_source(page,run,'reposts') is False
    source=run['collection']['sources']['reposts']
    assert source['tab_state']=='not_visible' and source['status']=='unavailable'
    assert page.reloads==1
    assert source['issues'][0]['code']=='tab_not_visible'
    assert run['collection']['blockers'][0]['source']=='reposts'
    assert 'no prueba' in source['reason']


def test_unknown_content_after_timeout_does_not_become_an_empty_section():
    page=SourcePage(content_after=999)
    run={'collection':{'sources':{}}}
    assert capture.BrowserSession().select_source(page,run,'reposts') is False
    source=run['collection']['sources']['reposts']
    assert source['content_state']=='loading_timeout' and source['status']=='unavailable'
    assert page.reloads==1 and 'no terminó de cargar' in source['reason']


def test_legacy_frames_and_ocr_are_archived_but_manual_upload_and_correction_survive():
    record={'frames':['old.jpg','uploaded.jpg'],'screenshots':['page.jpg'],'ocr_text':'Desplázate Entendido',
            'frame_quality':{'uploaded.jpg':{'source':'user_upload','status':'user_provided'}}}
    capture.BrowserSession.prepare_record_frames(record)
    assert record['frames']==['uploaded.jpg'] and record['archived_frames']==['old.jpg']
    assert record['diagnostic_screenshots']==['page.jpg'] and record['screenshots']==[]
    assert record['ocr_text']=='' and record['archived_ocr_text']=='Desplázate Entendido'
    assert record['quality_invalidated_fields']==['ocr_text']
    corrected={'frames':['old.jpg'],'ocr_text':'Corrección manual','manual_fields':{'ocr_text':'2026-10-03'}}
    capture.BrowserSession.prepare_record_frames(corrected)
    assert corrected['ocr_text']=='Corrección manual' and not corrected.get('quality_invalidated_fields')


def test_repaired_frame_set_replaces_legacy_frames_in_cumulative_library():
    old=store.empty_record('example','reposts','https://www.tiktok.com/@creator/video/123')
    old.update(observed_at='2026-01-01',frames=['artifacts/old.jpg'],screenshots=['artifacts/page.jpg'],ocr_text='Entendido')
    first={'id':'old','profile':'example','created_at':'2026-01-01','records':[old]}
    profiles.sync_run(first)
    fixed=dict(old,observed_at='2026-02-01',frames=['artifacts/clean.jpg'],screenshots=[],ocr_text='',
               frame_quality={'artifacts/clean.jpg':{'status':'verified'}},archived_frames=['artifacts/old.jpg'],
               diagnostic_screenshots=['artifacts/page.jpg'],quality_invalidated_fields=['ocr_text'])
    profiles.sync_run({'id':'fixed','profile':'example','created_at':'2026-02-01','records':[fixed]})
    result=profiles.profile_report('example')['records'][0]
    assert result['frames']==['artifacts/clean.jpg'] and result['ocr_text']==''
    assert result['screenshots']==[] and result['diagnostic_screenshots']==['artifacts/page.jpg']
    assert result['archived_frames']==['artifacts/old.jpg']


def test_close_cleans_up_after_browser_was_already_closed():
    class TargetClosedError(Exception): pass
    def closed(): raise TargetClosedError('Target page, context or browser has been closed')
    stopped=[]
    session=capture.BrowserSession()
    session.context=SimpleNamespace(close=closed)
    session.playwright=SimpleNamespace(stop=lambda:stopped.append(True))
    session.page=Page()
    assert session.close()['open'] is False
    assert stopped==[True] and session.playwright is None and session.page is None


def test_unexpected_close_error_still_cleans_up_and_is_not_silenced():
    def broken(): raise RuntimeError('Unexpected error')
    stopped=[]
    session=capture.BrowserSession()
    session.context=SimpleNamespace(close=broken)
    session.playwright=SimpleNamespace(stop=lambda:stopped.append(True))
    with pytest.raises(RuntimeError): session.close()
    assert stopped==[True] and session.context is None and session.playwright is None


def test_old_availability_notices_are_archived_before_new_capture(tmp_path,monkeypatch):
    directory=store.DATA/'artifacts'/'run'
    directory.mkdir(parents=True)
    page=Page()
    session=capture.BrowserSession()
    monkeypatch.setattr(session,'primary_media',lambda page,row:Element())
    monkeypatch.setattr(media,'process_media',lambda *args:{'ocr_text':'Texto','notes':[]})
    record=store.empty_record('example','reposts',page.url)
    record.update(availability={'status':'available','audio_status':'muted_notice','issues':[{'code':'audio_muted_notice','message':'Aviso anterior'}]},
                  processed_at='2026-01-01')
    session.prepare_record_frames(record)
    session.capture_detail(page,record,directory)
    assert record['availability']['audio_status']=='not_captured'
    assert not record['availability']['issues']
    assert record['availability_history'][0]['availability']['audio_status']=='muted_notice'
    assert record['availability_history'][0]['observed_at']=='2026-01-01'


def test_legacy_visual_derivatives_are_invalidated_except_manual_fields():
    record={'frames':['legacy.jpg'],'visual_summary':'El aviso tiene botones','visual_analysis':{'summary':'Interfaz'},'ocr_text':'Entendido'}
    capture.BrowserSession.prepare_record_frames(record)
    assert record['visual_summary']=='' and record['visual_analysis']=={}
    assert record['archived_visual_summary']=='El aviso tiene botones'
    assert set(record['quality_invalidated_fields'])=={'ocr_text','visual_summary','visual_analysis'}
    corrected={'frames':['legacy.jpg'],'visual_summary':'Comentario humano','manual_fields':{'visual_summary':'2026-10-03'}}
    capture.BrowserSession.prepare_record_frames(corrected)
    assert corrected['visual_summary']=='Comentario humano'


def test_source_failure_keeps_stage_diagnostic_and_zero_results_need_attention(monkeypatch):
    class CapturePage(Page):
        def locator(self,selector):
            if selector=='#main-content-others_homepage img': return Locator(snapshot=lambda:-1)
            return super().locator(selector)
    page=CapturePage()
    session=capture.BrowserSession()
    session.page=page
    session.context=object()
    session.headless=True
    def blocked(page,run,source):
        session.stage(run,source,'selecting_tab')
        raise TimeoutError('Untrusted page or secret text should not appear in API errors')
    monkeypatch.setattr(session,'select_source',blocked)
    run=store.new_run('example')
    session.capture({},run,['uploads'],2,2,headless=True)
    source=run['collection']['sources']['uploads']
    assert run['status']=='needs_attention' and source['stage']=='selecting_tab'
    assert store.media_path(source['diagnostic_screenshot']).exists()
    assert 'selecting_tab' in source['reason'] and 'secret' not in source['reason']
    assert run['collection']['session_signal']=='unknown'


def test_diagnostic_returns_bounded_ui_metadata_without_reading_body_or_input_values():
    class DiagnosticPage(Page):
        def locator(self,selector):
            if selector=='body':
                element=Element('')
                def no_body_read(**kwargs): raise AssertionError('Diagnostics must not extract full body text')
                element.inner_text=no_body_read
                return element
            if selector=='[role="dialog"], [aria-modal="true"]':
                return Locator(snapshot=lambda:[{'label':'Cookies','buttons':['Rechazar todas']}])
            return super().locator(selector)
    page=DiagnosticPage()
    page.url='https://www.tiktok.com/@example?token=secret#hidden'
    page.buttons=[Element('Iniciar sesión')]
    session=capture.BrowserSession()
    session.page=page
    run=store.new_run('example')
    run['collection']['stage']='navigating_profile'
    diagnostic=session.capture_diagnostic(run)
    assert diagnostic['url']=='https://www.tiktok.com/@example'
    assert diagnostic['dialogs']==[{'label':'Cookies','buttons':['Rechazar todas']}]
    assert diagnostic['session_signal']=='login_prompt_visible'
    assert diagnostic['stage']=='navigating_profile' and diagnostic['errors']==[]
    assert store.media_path(diagnostic['path']).is_file()
    assert run['records']==[]


def test_diagnostic_preserves_metadata_if_screenshot_fails_and_never_exposes_exception_text():
    class BrokenPage(Page):
        def screenshot(self,**kwargs): raise TimeoutError('Bearer do-not-expose-this-token')
    session=capture.BrowserSession()
    session.page=BrokenPage()
    diagnostic=session.capture_diagnostic(store.new_run('example'))
    assert diagnostic['path'] is None
    assert diagnostic['url']=='https://www.tiktok.com/@creator/video/123'
    assert diagnostic['errors']==[{'stage':'screenshot','type':'TimeoutError'}]
    assert 'Bearer' not in str(diagnostic)


def test_diagnostic_handles_missing_page_after_early_browser_failure():
    diagnostic=capture.BrowserSession().capture_diagnostic(store.new_run('example'))
    assert diagnostic['path'] is None
    assert diagnostic['errors']==[{'stage':'page','type':'PageUnavailable'}]


def test_already_selected_source_is_not_clicked_again():
    page=SourcePage()
    page.tab.attrs['aria-selected']='true'
    assert capture.BrowserSession().select_source(page,{'collection':{'sources':{}}},'reposts') is True
    assert page.tab.clicks==0


def test_open_then_capture_does_not_navigate_to_same_profile_twice(monkeypatch):
    class ProfilePage(Page):
        def locator(self,selector):
            if selector=='#main-content-others_homepage img': return Locator(snapshot=lambda:-1)
            return super().locator(selector)
    page=ProfilePage()
    session=capture.BrowserSession()
    def open_stub(profile,headless=False):
        session.context=object()
        session.page=page
        session.headless=headless
        page.goto('https://www.tiktok.com/@'+profile)
    monkeypatch.setattr(session,'open',open_stub)
    session.capture({},store.new_run('example'),[],2,2,headless=True)
    assert page.goto_calls==['https://www.tiktok.com/@example']
    # A subsequent capture still refreshes the profile once to discover new posts.
    session.capture({},store.new_run('example'),[],2,2,headless=True)
    assert page.goto_calls==['https://www.tiktok.com/@example']*2


def test_navigation_timeout_remains_a_failure_and_keeps_early_diagnostic_context(monkeypatch):
    session=capture.BrowserSession()
    page=Page()
    def failed_open(profile,headless=False):
        session.page=page
        page.url='https://www.tiktok.com/@'+profile
        raise TimeoutError('navigation failed')
    monkeypatch.setattr(session,'open',failed_open)
    run=store.new_run('example')
    with pytest.raises(TimeoutError):
        session.capture({},run,['uploads','reposts'],2,2,headless=True)
    assert run['collection']['stage']=='opening_browser'
    assert run['collection']['requested_sources']==['uploads','reposts']
    assert session.capture_diagnostic(run)['path']


@pytest.mark.parametrize('challenge',['Drag the slider to fit the puzzle','Fit the puzzle','Rotate the picture','Arrastra el deslizador'])
def test_observed_challenge_phrase_stops_capture_without_clicking(challenge):
    page=Page()
    page.body=Element(challenge)
    button=Element('Entendido')
    page.buttons=[button]
    with pytest.raises(capture.NeedsAttention) as exc:
        capture.BrowserSession().prepare_page(page)
    assert exc.value.code=='verification_required' and button.clicks==0


def test_visible_unmarked_iframe_challenge_is_detected_in_blocker_and_diagnostic():
    class Frame:
        def __init__(self,visible): self.visible=visible
        def frame_element(self): return Element(visible=self.visible)
        def locator(self,selector):
            assert selector=='body'
            return Element('Drag the slider to fit the puzzle')
    page=Page()
    page.frames=[Frame(False)]
    session=capture.BrowserSession()
    session.page=page
    assert session.verification_visible(page) is False
    page.frames.append(Frame(True))
    assert session.verification_visible(page) is True
    with pytest.raises(capture.NeedsAttention): session.check_blockers(page)
    assert session.capture_diagnostic(store.new_run('example'))['captcha_visible'] is True


def test_caption_containing_challenge_words_does_not_use_full_body_fallback():
    class CaptionBody(Element):
        def evaluate(self,script,*args,**kwargs):
            if script==capture.VERIFY_TEXT_CHECK:
                return False  # The DOM detector excluded the article containing this caption.
            return super().evaluate(script,*args,**kwargs)
    page=Page()
    page.body=CaptionBody('A meme says: Drag the slider to fit the puzzle, verify to continue')
    session=capture.BrowserSession()
    assert session.verification_visible(page) is False
    session.check_blockers(page)


class NavPage:
    def __init__(self,results):
        self.results=list(results)
        self.calls=[]
    def goto(self,url,**kwargs):
        self.calls.append(url)
        result=self.results.pop(0)
        if isinstance(result,Exception):
            raise result
        return result


def test_goto_retries_transient_network_errors(monkeypatch):
    session=capture.BrowserSession()
    session.page=NavPage([Exception('Page.goto: net::ERR_SSL_PROTOCOL_ERROR at https://www.tiktok.com/'),None])
    monkeypatch.setattr(capture.time,'sleep',lambda seconds: None)
    session._goto('https://www.tiktok.com/@example')
    assert len(session.page.calls)==2


def test_goto_gives_up_after_bounded_transient_retries(monkeypatch):
    session=capture.BrowserSession()
    session.page=NavPage([Exception('net::ERR_SSL_PROTOCOL_ERROR') for _ in range(5)])
    monkeypatch.setattr(capture.time,'sleep',lambda seconds: None)
    with pytest.raises(capture.NeedsAttention) as excinfo:
        session._goto('https://www.tiktok.com/@example')
    assert excinfo.value.code=='navigation_failed'
    assert len(session.page.calls)==5


def test_goto_does_not_retry_non_transient_errors(monkeypatch):
    session=capture.BrowserSession()
    session.page=NavPage([Exception('Page.goto: Timeout 45000ms exceeded')])
    monkeypatch.setattr(capture.time,'sleep',lambda seconds: None)
    with pytest.raises(capture.NeedsAttention):
        session._goto('https://www.tiktok.com/@example')
    assert len(session.page.calls)==1


def test_login_requirement_warning_only_for_gated_sources_when_logged_out():
    run={'collection':{'issues':[]}}
    capture.BrowserSession.note_login_requirement(run,['uploads','reposts'],True)
    issues=run['collection']['issues']
    assert len(issues)==1 and issues[0]['code']=='login_required_for_sources'
    assert 'Publicaciones compartidas' in issues[0]['message']
    assert 'Me gusta' not in issues[0]['message']

    logged_in={'collection':{'issues':[]}}
    capture.BrowserSession.note_login_requirement(logged_in,['reposts'],False)
    assert logged_in['collection']['issues']==[]

    uploads_only={'collection':{'issues':[]}}
    capture.BrowserSession.note_login_requirement(uploads_only,['uploads'],True)
    assert uploads_only['collection']['issues']==[]

    both={'collection':{'issues':[]}}
    capture.BrowserSession.note_login_requirement(both,['reposts','likes'],True)
    assert 'Publicaciones compartidas' in both['collection']['issues'][0]['message']
    assert 'Me gusta' in both['collection']['issues'][0]['message']
