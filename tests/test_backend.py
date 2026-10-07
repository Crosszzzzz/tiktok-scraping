"""Local API integration checks. No test starts or drives a real browser."""

import pytest
from fastapi.testclient import TestClient

from app import main, store
from app.capture import parse_alt, parse_visible_date


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path.resolve())
    monkeypatch.setattr(main, "JOBS", {})
    monkeypatch.setattr(main, "TASKS", set())
    return tmp_path


@pytest.fixture
def client():
    # Browser methods are replaced by no_browser, including the lifespan shutdown.
    with TestClient(main.app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Integration tests must never open a real browser")

    monkeypatch.setattr(main.session, "open", unexpected)
    monkeypatch.setattr(main.session, "capture", unexpected)
    monkeypatch.setattr(main.session, "close", lambda: {"open": False, "status": "Test double"})
    monkeypatch.setattr(main.session, "state", lambda: {"open": False, "status": "Test double"})


def payload(**record_changes):
    row = {
        "url": "https://www.tiktok.com/@creator/video/123456789?lang=es",
        "source": "reposts",
        "caption": "Una receta de cocina #receta",
    }
    row.update(record_changes)
    return {"profile": "https://www.tiktok.com/@Example", "records": [row]}


def imported(client, **changes):
    response = client.post("/api/import", json=payload(**changes))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("profile,expected", [
    ("@Adry_zg26", "adry_zg26"),
    ("https://www.tiktok.com/@Example/", "example"),
    ("tiktok.com/@Example", "example"),
    ("example.name_2", "example.name_2"),
])
def test_profile_normalization(profile, expected):
    assert store.profile_name(profile) == expected


@pytest.mark.parametrize("profile", [
    "https://evil.example/@name", "https://tiktok.com.evil.example/@name",
    "https://evil@tiktok.com/@name", "https://tiktok.com:444/@name",
    "https://tiktok.com/@name/video/12", "../../secret", "one two",
])
def test_profile_rejects_unsupported_destinations(profile):
    with pytest.raises(ValueError):
        store.profile_name(profile)


@pytest.mark.parametrize("relative", ["../secret.png", "runs/run.json", "artifacts/../../secret.jpg", "C:/Windows/system.ini"])
def test_artifact_paths_stay_inside_artifacts(relative):
    with pytest.raises(ValueError):
        store.media_path(relative)


def test_artifact_valid_path_and_missing_file(client):
    artifact = store.DATA / "artifacts" / "run" / "photo.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"local-image")
    response = client.get("/data/artifacts/run/photo.png")
    assert response.status_code == 200
    assert response.content == b"local-image"
    assert client.get("/data/artifacts/missing.png").status_code == 404


def test_artifact_refuses_non_media_and_traversal(client):
    directory = store.DATA / "artifacts"
    directory.mkdir(parents=True)
    (directory / "cookies.json").write_text("{}", encoding="utf-8")
    assert client.get("/data/artifacts/cookies.json").status_code == 403
    assert client.get("/data/artifacts/%2e%2e/%2e%2e/secrets.png").status_code in {404, 422}
    assert client.get("/api/runs/%2e%2e").status_code in {404, 422}


def test_host_origin_and_cross_site_requests_are_blocked(client):
    assert client.get("/api/runs", headers={"host": "attacker.example"}).status_code == 403
    assert client.get("/api/runs", headers={"origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/demo", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    same_origin = client.get("/api/runs", headers={"origin": "http://testserver"})
    assert same_origin.status_code == 200
    assert same_origin.headers["x-content-type-options"] == "nosniff"
    assert same_origin.headers["x-frame-options"] == "DENY"


def test_import_normalizes_identity_and_discards_filesystem_references(client):
    run = imported(client, id="attacker-id", profile="someone_else", screenshots=["C:/secret.png"], frames=["../../private.jpg"])
    row = run["records"][0]
    assert run["profile"] == "example"
    assert row["profile"] == "example"
    assert row["id"] != "attacker-id"
    assert row["url"] == "https://www.tiktok.com/@creator/video/123456789"
    assert row["screenshots"] == row["frames"] == []
    assert run["analysis"]["metrics"]["total_records"] == 1
    assert run["analysis"]["topics"][0]["name"] == "Comida y cocina"
    assert store.load_run(run["id"])["records"] == run["records"]


@pytest.mark.parametrize("changes", [
    {"source": "unknown"}, {"source": []},
    {"url": {"bad": "value"}}, {"url": "https://evil.example/video/1"},
    {"caption": ["not a string"]}, {"music": "not an object"}, {"notes": [3]},
    {"first_seen_at": ["2025-09-01"]}, {"published_at": "2025-02-31"},
])
def test_import_rejects_malformed_records_with_client_error(client, changes):
    response = client.post("/api/import", json=payload(**changes))
    assert response.status_code == 422, response.text
    assert store.list_runs() == []


def test_import_unverified_dates_do_not_enter_interaction_timeline(client):
    run = imported(client, published_at="2025-09-01", interaction_at="2026-10-01", interaction_date_source="estimated")
    row = run["records"][0]
    assert row["interaction_at"] is None
    assert row["interaction_date_source"] is None
    assert run["analysis"]["timeline"] == []
    assert run["analysis"]["original_timeline"][0]["period"] == "2025-09"


def test_import_malformed_date_provenance_is_discarded(client):
    run = imported(client, interaction_at="2026-10-01", interaction_date_source=["visible"])
    assert run["records"][0]["interaction_at"] is None
    assert run["records"][0]["interaction_date_source"] is None
    assert run["analysis"]["timeline"] == []


def test_import_duplicate_capture_has_one_analysis_record(client):
    body = payload()
    body["records"].append(dict(body["records"][0]))
    response = client.post("/api/import", json=body)
    assert response.status_code == 200
    assert response.json()["analysis"]["metrics"]["total_records"] == 1
    assert response.json()["analysis"]["metrics"]["duplicate_count"] == 1


def test_dates_can_be_added_cleared_and_rejected_without_losing_evidence(client):
    run = imported(client)
    row = run["records"][0]
    endpoint = f"/api/runs/{run['id']}/records/{row['id']}"
    added = client.patch(endpoint, json={"interaction_at": "2026-09-01", "interaction_date_source": "visible"})
    assert added.status_code == 200
    assert added.json()["records"][0]["interaction_date_source"] == "user_verified"
    assert added.json()["analysis"]["timeline"][0]["period"] == "2026-09"
    invalid = client.patch(endpoint, json={"interaction_at": "2026-02-31"})
    assert invalid.status_code == 422
    assert store.load_run(run["id"])["records"][0]["interaction_at"] == "2026-09-01"
    cleared = client.patch(endpoint, json={"interaction_at": None})
    assert cleared.status_code == 200
    assert cleared.json()["analysis"]["timeline"] == []
    assert cleared.json()["records"][0]["interaction_date_source"] is None
    assert cleared.json()["records"][0]["caption"] == row["caption"]


def test_original_date_edit_does_not_invent_an_interaction(client):
    run = imported(client, source="uploads")
    row = run["records"][0]
    response = client.patch(f"/api/runs/{run['id']}/records/{row['id']}", json={"published_at": "2025-09-02", "notes": "Revisado manualmente"})
    assert response.status_code == 200
    result = response.json()
    assert result["analysis"]["timeline"] == []
    assert result["analysis"]["original_timeline"][0]["period"] == "2025-09"
    assert result["records"][0]["notes"] == ["Revisado manualmente"]


def test_active_jobs_prevent_edits_and_capture_needs_manual_session(client):
    run = imported(client)
    row = run["records"][0]
    main.JOBS["active"] = {"id": "active", "run_id": run["id"], "status": "running"}
    assert client.patch(f"/api/runs/{run['id']}/records/{row['id']}", json={"caption": "update"}).status_code == 409
    assert client.post(f"/api/runs/{run['id']}/analyze").status_code == 409
    main.JOBS.clear()
    response = client.post("/api/capture", json={"profile": "example"})
    assert response.status_code == 409
    assert "manualmente" in response.json()["detail"]


def test_demo_is_marked_fictional_and_exports_both_formats(client):
    response = client.post("/api/demo")
    assert response.status_code == 200
    run = response.json()
    assert run["is_demo"] is True
    assert run["collection"]["method"] == "synthetic_demo"
    assert len(run["records"]) == 8
    assert all("FICTICIO" in row["notes"][0] for row in run["records"])
    assert set(run["analysis"]["metrics"]["source_counts"]) == {"uploads", "reposts", "likes"}
    exported = client.get(f"/api/runs/{run['id']}/export?format=json")
    assert exported.status_code == 200
    assert exported.json()["records"] == run["records"]
    assert "attachment" in exported.headers["content-disposition"]
    markdown = client.get(f"/api/runs/{run['id']}/export?format=md")
    assert markdown.status_code == 200
    assert "Cronología de interacciones verificadas" in markdown.text
    assert "Fechas de publicación original" in markdown.text
    assert "EJEMPLO FICTICIO" in markdown.text
    assert client.get(f"/api/runs/{run['id']}/export?format=html").status_code == 422


def test_persistence_list_reload_and_earliest_observation(client):
    run = imported(client, observed_at="2026-09-02T10:00:00Z", first_seen_at="2026-09-01T10:00:00Z")
    assert client.get(f"/api/runs/{run['id']}").json()["id"] == run["id"]
    summaries = client.get("/api/runs").json()
    assert len(summaries) == 1
    assert summaries[0]["count"] == 1
    assert store.first_seen("example", run["records"][0]["url"], "reposts", "2026-10-03T10:00:00Z") == "2026-09-01T10:00:00Z"
    assert not list((store.DATA / "runs").glob("*.tmp"))
    assert client.get("/api/runs/missing").status_code == 404
    assert client.patch(f"/api/runs/{run['id']}/records/missing", json={"caption": "x"}).status_code == 404


def test_corrupt_json_does_not_hide_valid_persisted_runs(client):
    run = imported(client)
    (store.DATA / "runs" / "broken.json").write_text("{not-json", encoding="utf-8")
    summaries = client.get("/api/runs")
    assert summaries.status_code == 200
    assert [row["id"] for row in summaries.json()] == [run["id"]]


@pytest.mark.parametrize("label,expected", [
    ("2025-9-3", "2025-09-03"), ("Nombre · 2024/02/29", "2024-02-29"),
    ("Publicado 2025-11-23", "2025-11-23"), ("2025-02-29", None),
    ("9-3", None), ("hace 2 días", None), ("September 3", None), ("", None),
])
def test_visible_date_only_accepts_complete_absolute_dates(label, expected):
    assert parse_visible_date(label) == expected


@pytest.mark.parametrize("label,caption,author,title", [
    ("Una receta creado por Cocina con la música Canción Uno", "Una receta", "Cocina", "Canción Uno"),
    ("Travel scene created by Alice with the music Song Two", "Travel scene", "Alice", "Song Two"),
    ("Descripción sin metadatos", "Descripción sin metadatos", "", None),
])
def test_thumbnail_alt_metadata_is_kept_separate(label, caption, author, title):
    parsed = parse_alt(label)
    assert parsed["caption"] == caption
    assert parsed["author"] == author
    assert parsed["music"].get("title") == title
    assert "interaction_at" not in parsed
