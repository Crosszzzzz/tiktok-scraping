"""API boundaries for optional cloud vision; tests never call a provider."""
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app import main,store


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setattr(main,'JOBS',{})
    monkeypatch.setattr(main,'TASKS',set())
    monkeypatch.setattr(main.session,'close',lambda: {})
    return tmp_path


def test_validation_never_echoes_api_key(isolated):
    with TestClient(main.app) as client:
        key='very-secret-api-key-'*100
        response=client.put('/api/ai/settings',json={'api_key':key})
        assert response.status_code==422
        assert 'very-secret-api-key' not in response.text


def test_settings_blank_key_preserves_secret_and_passes_only_public_data(isolated,monkeypatch):
    from app import vision
    calls=[]
    monkeypatch.setattr(vision,'update_settings',lambda **kw: calls.append(kw) or {'provider':'gemini','configured':True})
    with TestClient(main.app) as client:
        response=client.put('/api/ai/settings',json={'provider':'gemini','api_key':'','max_per_run':3})
        assert response.status_code==200
        assert calls==[{'provider':'gemini','max_per_run':3}]
        assert 'api_key' not in response.json()


def test_rejected_frames_never_reach_vision(isolated):
    folder=isolated/'artifacts'
    folder.mkdir()
    for name in ['clean.jpg','bad.jpg']:
        (folder/name).write_bytes(b'local-test')
    row={'frames':['artifacts/bad.jpg','artifacts/clean.jpg'],
         'frame_quality':{'artifacts/bad.jpg':{'status':'obstructed'},'artifacts/clean.jpg':{'status':'verified'}}}
    assert main.vision_files(row)==[folder/'clean.jpg']


def test_auto_visual_budget_preserves_cache_and_manual_corrections(isolated,monkeypatch):
    from app import vision
    monkeypatch.setattr(vision,'get_settings',lambda: {'auto_visual':True,'configured':True,'max_per_run':1})
    monkeypatch.setattr(main,'vision_files',lambda row:[Path('frame.jpg')])
    calls=[]
    def inference(row,files):
        calls.append(row['id'])
        return {'visual_summary':'Escena visible','visual_analysis':{'cached':row['id']=='cached'},'notes':[]}
    monkeypatch.setattr(vision,'analyze_record',inference)
    run=store.new_run('example')
    run['records']=[{'id':'manual','manual_fields':{'visual_summary':'2026-10-01'},'visual_summary':'Texto corregido'},
                    {'id':'cached'},{'id':'fresh'},{'id':'over_budget'}]
    asyncio.run(main.auto_vision({},run))
    assert calls==['cached','fresh']
    assert run['records'][0]['visual_summary']=='Texto corregido'
    assert run['collection']['visual_processing']=={'attempted':1,'completed':2,'failed':0,'cached':1,'limit':1,'remaining':1}


def test_visual_error_keeps_existing_summary(isolated):
    row={'visual_summary':'Descripción revisada','notes':[]}
    main.apply_visual_result(row,{'visual_error':{'code':'rate_limited','message':'Cuota agotada'},'notes':[]})
    assert row['visual_summary']=='Descripción revisada'
    assert row['visual_error']['code']=='rate_limited'


def test_settings_cannot_change_during_active_job(isolated):
    main.JOBS['work']={'status':'running'}
    with TestClient(main.app) as client:
        assert client.put('/api/ai/settings',json={'provider':'gemini'}).status_code==409
        assert client.delete('/api/ai/key').status_code==409
