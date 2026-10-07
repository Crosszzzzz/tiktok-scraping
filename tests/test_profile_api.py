"""Saved-profile API integration; no browser, OCR engine, or network is used."""
import time

import pytest
from fastapi.testclient import TestClient

from app import capture, main, media, store


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path.resolve())
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "TASKS", set())

    def forbidden(*args, **kwargs):
        raise AssertionError("Profile API tests cannot open a browser or run extraction")
    monkeypatch.setattr(main.session, "open", forbidden)
    monkeypatch.setattr(main.session, "capture", forbidden)
    monkeypatch.setattr(main.session, "close", lambda: {"open":False})
    monkeypatch.setattr(main.session, "state", lambda: {"open":False, "profile_available":False, "status":"Test"})
    monkeypatch.setattr(media, "process_media", forbidden)
    monkeypatch.setattr(media, "describe_frames", forbidden)
    monkeypatch.setattr(media, "capabilities", lambda: {})


@pytest.fixture
def client():
    with TestClient(main.app, raise_server_exceptions=False) as api:
        yield api


def evidence(post="123", **changes):
    record = dict(source="reposts", url=f"https://www.tiktok.com/@creator/video/{post}", caption="Una receta de cocina",
                  observed_at="2026-09-01T10:00:00Z", first_seen_at="2026-09-01T10:00:00Z")
    record.update(changes)
    return record


def import_records(client, *records, profile="example"):
    response = client.post("/api/import", json={"profile":profile, "records":list(records)})
    assert response.status_code == 200, response.text
    return response.json()


def report(client, profile="example"):
    response = client.get(f"/api/profiles/{profile}/report")
    assert response.status_code == 200, response.text
    return response.json()


def wait_job(client, identifier):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{identifier}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] not in {"pending", "running"}:
            return job
        time.sleep(0.01)
    pytest.fail("The stubbed capture job did not finish")


def test_saved_settings_survive_partial_updates_and_appear_in_state(client):
    response = client.post("/api/profiles", json={"profile":"@Example", "label":"Recetas", "notes":"Revisar después", "sources":["reposts"],
                                                "tags":["cocina"], "max_items":30, "headless":False})
    assert response.status_code == 200, response.text
    updated = client.post("/api/profiles", json={"profile":"example", "mode":"refresh"})
    assert updated.status_code == 200
    profile = updated.json()
    assert profile["label"] == "Recetas" and profile["notes"] == "Revisar después"
    assert profile["sources"] == ["reposts"] and profile["max_items"] == 30
    assert profile["headless"] is False and profile["mode"] == "refresh"
    assert client.get("/api/profiles").json() == [profile]
    assert client.get("/api/state").json()["profiles"] == [profile]
    assert client.get("/api/profiles/unknown").status_code == 404


def test_successive_imports_accumulate_deduplicate_and_reanalyze_without_ocr(client):
    first = import_records(client, evidence(ocr_text="OCR conservado", transcript="Audio conservado"), evidence("456"))
    second = import_records(client,
                            evidence(url="https://tiktok.com/@renamed/photo/123", caption="Descripción nueva", ocr_text="", observed_at="2026-10-01T10:00:00Z"),
                            evidence("789", source="uploads"))
    cumulative = report(client)
    assert cumulative["aggregate"] is True and cumulative["analysis"]["metrics"]["total_records"] == 3
    assert len(cumulative["records"]) == 3
    matching = next(item for item in cumulative["records"] if item["url"].endswith("/123"))
    assert matching["observation_count"] == 2
    assert matching["ocr_text"] == "OCR conservado" and matching["transcript"] == "Audio conservado"
    assert matching["caption"] == "Descripción nueva"
    assert matching["origin_run_id"] == second["id"]
    assert matching["origin_record_id"] == second["records"][0]["id"]
    reanalyzed = client.post("/api/profiles/example/analyze")
    assert reanalyzed.status_code == 200
    assert reanalyzed.json()["records"] == cumulative["records"]
    assert client.get(f"/api/runs/{first['id']}").json()["records"][0]["caption"] == "Una receta de cocina"
    assert len(client.get("/api/runs").json()) == 2  # Aggregate views are not new capture runs.
    exported = client.get("/api/profiles/example/export?format=json")
    assert exported.status_code == 200 and exported.json()["records"] == cumulative["records"]


def test_origin_edits_and_explicit_clears_persist_in_aggregate_through_future_import(client):
    import_records(client, evidence(ocr_text="OCR original", interaction_at="2026-08-01", interaction_date_source="visible"))
    record = report(client)["records"][0]
    endpoint = f"/api/runs/{record['origin_run_id']}/records/{record['origin_record_id']}"
    response = client.patch(endpoint, json={"caption":"Corrección manual", "ocr_text":"", "interaction_at":None})
    assert response.status_code == 200, response.text
    import_records(client, evidence(caption="Automático posterior", ocr_text="OCR automático", observed_at="2026-10-03T10:00:00Z",
                                    interaction_at="2026-09-01", interaction_date_source="visible"))
    canonical = report(client)["records"][0]
    assert canonical["caption"] == "Corrección manual"
    assert canonical["ocr_text"] == ""
    assert canonical["interaction_at"] is None and canonical["interaction_date_source"] is None
    assert {"caption", "ocr_text", "interaction_at"} <= canonical["manual_fields"].keys()
    assert report(client)["analysis"]["timeline"] == []


def test_same_post_in_different_profiles_and_sources_stays_separate(client):
    import_records(client, evidence(ocr_text="Primero"), evidence(source="likes", ocr_text="Me gusta"))
    import_records(client, evidence(ocr_text="Segundo"), profile="another")
    assert {item["ocr_text"] for item in report(client)["records"]} == {"Primero", "Me gusta"}
    assert report(client, "another")["records"][0]["ocr_text"] == "Segundo"
    assert len(client.get("/api/profiles").json()) == 2


def test_demo_never_populates_real_saved_profiles_or_cache(client):
    response = client.post("/api/demo")
    assert response.status_code == 200 and response.json()["is_demo"] is True
    assert client.get("/api/profiles").json() == []
    assert client.get("/api/profiles/demo_ficticia/report").status_code == 404
    import_records(client, evidence())
    assert [item["profile"] for item in client.get("/api/profiles").json()] == ["example"]


@pytest.mark.parametrize("headless", [True, False])
def test_capture_dispatches_saved_mode_limits_and_background_preference(client, monkeypatch, headless):
    calls = []
    monkeypatch.setattr(main.session, "state", lambda: {"open":True, "profile_available":True, "status":"Ready"})

    def capture_stub(job, run, sources, max_items, scroll_rounds, mode, background=True, wait_captcha=True):
        calls.append((sources, max_items, scroll_rounds, mode, background, wait_captcha))
        run["status"] = "completed"
    monkeypatch.setattr(main.session, "capture", capture_stub)
    response = client.post("/api/capture", json={"profile":"example", "sources":["reposts"], "max_items":7,
                                                "scroll_rounds":9, "mode":"refresh", "headless":headless})
    assert response.status_code == 200, response.text
    assert wait_job(client, response.json()["id"])["status"] == "completed"
    assert calls == [(["reposts"], 7, 9, "refresh", headless, True)]
    saved = client.get("/api/profiles/example").json()
    assert saved["mode"] == "refresh" and saved["headless"] is headless


def test_auto_pipeline_runs_capture_and_skips_publish_when_disabled(client, monkeypatch):
    monkeypatch.setattr(main.session, "state", lambda: {"open":True, "profile_available":True, "status":"Ready"})
    calls = []

    def capture_stub(job, run, *options):
        calls.append(options)
        run["status"] = "completed"
    monkeypatch.setattr(main.session, "capture", capture_stub)
    response = client.post("/api/auto", json={"profile":"example", "publish":False})
    assert response.status_code == 200, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "completed" and calls


def test_background_capture_can_resume_saved_browser_profile_without_open_window(client, monkeypatch):
    monkeypatch.setattr(main.session, "state", lambda: {"open":False, "profile_available":True, "status":"Closed"})

    def capture_stub(job, run, *options):
        run["status"] = "completed"
    monkeypatch.setattr(main.session, "capture", capture_stub)
    response = client.post("/api/capture", json={"profile":"example", "headless":True})
    assert response.status_code == 200, response.text
    assert wait_job(client, response.json()["id"])["status"] == "completed"


@pytest.mark.parametrize("endpoint", ["/api/profiles", "/api/capture"])
def test_malformed_headless_value_is_rejected_before_any_capture(client, endpoint):
    response = client.post(endpoint, json={"profile":"example", "headless":{"invalid":True}})
    assert response.status_code == 422
    assert client.get("/api/profiles").json() == []


def test_manual_verification_failure_keeps_evidence_and_persistent_reason(client, monkeypatch):
    monkeypatch.setattr(main.session, "state", lambda: {"open":True, "profile_available":True, "status":"Ready"})

    def capture_stub(job, run, *options):
        record = store.empty_record("example", "reposts", "https://www.tiktok.com/@creator/video/123")
        record["caption"] = "Evidencia obtenida antes de la verificación"
        run["records"].append(record)
        raise main.NeedsAttention("TikTok solicita verificación manual", "captcha")
    monkeypatch.setattr(main.session, "capture", capture_stub)
    response = client.post("/api/capture", json={"profile":"example", "headless":True})
    assert response.status_code == 200, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "needs_attention"
    summary = client.get("/api/profiles/example").json()
    assert summary["last_run_status"] == "needs_attention"
    assert summary["last_capture_reason"] == "TikTok solicita verificación manual"
    assert len(summary["blockers"]) == 1 and summary["blockers"][0]["code"] == "captcha"
    assert report(client)["records"][0]["caption"] == "Evidencia obtenida antes de la verificación"


def test_private_profile_stops_before_vision_analysis_and_publish(client, monkeypatch):
    monkeypatch.setattr(main.session, "state", lambda: {"open":True, "profile_available":True, "status":"Ready"})
    calls = {"vision":0, "analyze":0}

    def capture_stub(job, run, *options):
        raise main.NeedsAttention("El perfil es privado. Solo se recopila contenido público accesible.", "private_profile")
    monkeypatch.setattr(main.session, "capture", capture_stub)

    async def vision_spy(*args, **kwargs):
        calls["vision"] += 1
    monkeypatch.setattr(main, "auto_vision", vision_spy)

    def analyze_spy(*args, **kwargs):
        calls["analyze"] += 1
    monkeypatch.setattr(main, "analyze", analyze_spy)

    response = client.post("/api/auto", json={"profile":"example", "publish":True})
    assert response.status_code == 200, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "needs_attention"
    assert "privado" in job["message"].lower()
    assert "url" not in job and "publish_error" not in job
    assert calls == {"vision":0, "analyze":0}
    summary = client.get("/api/profiles/example").json()
    assert summary["last_run_status"] == "needs_attention"
    assert summary["last_capture_reason"].lower().startswith("el perfil es privado")


def test_auto_pipeline_reports_failure_when_session_open_raises(client, monkeypatch):
    monkeypatch.setattr(main.session, "state", lambda: {"open":False, "profile_available":True, "status":"Closed"})

    def open_stub(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(main.session, "open", open_stub)

    response = client.post("/api/auto", json={"profile":"example", "publish":False})
    assert response.status_code == 200, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "failed"
    assert "abrir Chrome" in job["message"]
    summary = client.get("/api/profiles/example").json()
    assert summary["last_run_status"] == "failed"


def test_login_session_opens_normal_chrome_with_app_profile(client, monkeypatch):
    launched = []
    monkeypatch.setattr(capture, "open_login_browser", lambda profile='': launched.append(profile))
    response = client.post("/api/session/login", json={"profile":"@enkerzz"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "login_window_opened"
    assert "iniciá sesión" in body["message"].lower()
    assert launched == ["enkerzz"]


def test_avatar_upload_normalizes_local_image_ignores_filename_and_preserves_settings(client):
    from io import BytesIO
    from PIL import Image
    image = BytesIO()
    Image.new("RGB", (1200, 800), color="green").save(image, format="PNG")
    client.post("/api/profiles", json={"profile":"example", "label":"Guardado", "headless":False})
    response = client.post("/api/profiles/example/avatar", files={"file":("../../unsafe.png", image.getvalue(), "image/png")})
    assert response.status_code == 200, response.text
    profile = response.json()
    assert profile["avatar_source"] == "user_upload" and profile["label"] == "Guardado"
    assert profile["headless"] is False
    assert profile["avatar_path"].startswith("artifacts/profiles/example/")
    assert "unsafe" not in profile["avatar_path"]
    with Image.open(store.media_path(profile["avatar_path"])) as saved:
        assert saved.format == "JPEG" and max(saved.size) <= 512
    assert client.get("/data/" + profile["avatar_path"]).status_code == 200
    assert report(client)["avatar_path"] == profile["avatar_path"]


def test_invalid_avatar_upload_does_not_create_profile_or_files(client):
    response = client.post("/api/profiles/example/avatar", files={"file":("fake.png", b"not a photo", "image/png")})
    assert response.status_code == 422
    assert client.get("/api/profiles").json() == []
    assert not list((store.DATA / "artifacts").rglob("*"))
