"""Cumulative persistence tests; all data stays in a temporary directory."""
import copy
import importlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import profiles, store


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path.resolve())


def row(post="123", source="reposts", profile="example", **changes):
    record = store.empty_record(profile, source, f"https://www.tiktok.com/@creator/video/{post}")
    record.update(observed_at="2026-01-10T12:00:00Z", first_seen_at="2026-01-10T12:00:00Z", caption="Original")
    record.update(changes)
    return record


def run(run_id="one", records=None, profile="example", created_at="2026-01-10T12:00:00Z", **changes):
    result = dict(id=run_id, profile=profile, created_at=created_at,
                  status="completed", records=records if records is not None else [row(profile=profile)],
                  collection={"sources":{"reposts":{"status":"sampled", "count":1}}}, analysis={})
    result.update(changes)
    return result


def test_saved_settings_and_profile_scoped_records():
    saved = profiles.save_profile("@Example", {"label":"Lectura", "notes":"Notas", "tags":[" uno ", "uno"],
                                             "sources":["reposts"], "mode":"refresh", "max_items":30})
    assert saved["profile"] == "example"
    assert saved["tags"] == ["uno"]
    assert saved["record_count"] == 0
    profiles.sync_run(run())
    profiles.sync_run(run("other", profile="different"))
    assert profiles.known_records("example").keys() == profiles.known_records("different").keys()
    first = next(iter(profiles.known_records("example").values()))
    other = next(iter(profiles.known_records("different").values()))
    assert first["id"] != other["id"]
    assert len(profiles.list_profiles()) == 2
    saved = next(item for item in profiles.list_profiles() if item["profile"] == "example")
    assert saved["label"] == "Lectura" and saved["mode"] == "refresh"
    assert saved["last_run_id"] == "one" and saved["record_count"] == 1
    assert "records" not in saved


def test_numeric_post_key_ignores_author_rename_format_and_query():
    original = row()
    renamed = row(url="https://tiktok.com/@another/photo/123/?lang=es")
    assert profiles.record_key(original) == profiles.record_key(renamed)
    assert profiles.record_key(original) != profiles.record_key(row(source="likes"))
    assert profiles.record_key(dict(id="manual_id", source="manual")) == "manual:id:manual_id"
    profiles.sync_run(run(records=[original]))
    profiles.sync_run(run("two", records=[renamed]))
    assert len(profiles.known_records("example")) == 1


def test_repeated_sync_and_reanalysis_do_not_inflate_observations():
    source = run(records=[row(ocr_text="OCR", metrics={"views":10})])
    before = copy.deepcopy(source)
    profiles.sync_run(source)
    profiles.sync_run(source)
    report = profiles.profile_report("example")
    source["analysis"] = {"recomputed":True}
    profiles.sync_run(source)
    again = profiles.profile_report("example")
    assert again == report
    assert before["records"] == source["records"]
    record = again["records"][0]
    assert record["observation_count"] == 1
    assert record["observation_run_ids"] == ["one"]
    assert len(record["metrics_history"]) == 1


def test_library_retains_old_content_and_enrichment_after_partial_sample():
    profiles.sync_run(run(records=[row(ocr_text="Texto reconocido", transcript="Canción", visual_summary="Escena", frames=["artifacts/one/a.png"]), row("456")]))
    later = row(caption="Descripción nueva", observed_at="2026-02-10T12:00:00Z", ocr_text="", frames=[])
    profiles.sync_run(run("two", records=[later], created_at="2026-02-10T12:00:00Z"))
    library = profiles.known_records("example")
    assert len(library) == 2
    enriched = library[profiles.record_key(later)]
    assert enriched["ocr_text"] == "Texto reconocido"
    assert enriched["transcript"] == "Canción"
    assert enriched["visual_summary"] == "Escena"
    assert enriched["frames"] == ["artifacts/one/a.png"]
    assert enriched["caption"] == "Descripción nueva"
    assert enriched["observation_count"] == 2
    assert enriched["first_seen_at"] == "2026-01-10T12:00:00Z"
    assert enriched["last_seen_at"] == "2026-02-10T12:00:00Z"
    assert enriched["interaction_at"] is None
    assert enriched["origin_run_id"] == "two"
    assert enriched["origin_record_id"] == later["id"]
    assert all("deleted" not in record for record in library.values())


def test_manual_clears_survive_auto_refresh_and_older_manual_edits():
    manual = row(ocr_text="", interaction_at=None, interaction_date_source=None, notes=[],
                 manual_fields={field:"2026-03-02T10:00:00Z" for field in ("ocr_text", "interaction_at", "interaction_date_source", "notes")})
    profiles.sync_run(run(records=[manual]))
    auto = row(ocr_text="Texto automático", notes=["Automática"], interaction_at="2026-01-01", interaction_date_source="visible",
               observed_at="2026-04-01T10:00:00Z")
    profiles.sync_run(run("two", records=[auto], created_at="2026-04-01T10:00:00Z"))
    older = row(ocr_text="Edición anterior", manual_fields={"ocr_text":"2026-03-01T10:00:00Z"})
    profiles.sync_run(run("three", records=[older]))
    record = profiles.profile_report("example")["records"][0]
    assert record["ocr_text"] == ""
    assert record["interaction_at"] is None
    assert record["interaction_date_source"] is None
    assert record["notes"] == []
    newest = row(ocr_text="Corrección reciente", manual_fields={"ocr_text":"2026-05-01T10:00:00Z"})
    profiles.sync_run(run("four", records=[newest]))
    record = profiles.profile_report("example")["records"][0]
    assert record["ocr_text"] == "Corrección reciente"
    assert record["origin_run_id"] == "two"  # Edits keep media/vision routed to the latest captured evidence.
    assert record["last_seen_at"] == "2026-04-01T10:00:00Z"
    assert record["observation_count"] == 4


def test_dates_compare_instants_not_iso_strings_and_ignore_bad_dates():
    early = row(first_seen_at="2026-01-01T01:00:00+02:00", observed_at="2026-01-10T03:00:00+03:00")
    profiles.sync_run(run(records=[early]))
    later = row(first_seen_at="2025-12-31T23:30:00Z", observed_at="2026-01-10T01:00:00Z")
    profiles.sync_run(run("two", records=[later]))
    profiles.sync_run(run("three", records=[row(first_seen_at="bad", observed_at="bad")], created_at="2026-01-09T10:00:00Z"))
    record = profiles.profile_report("example")["records"][0]
    assert record["first_seen_at"] == "2026-01-01T01:00:00+02:00"
    assert record["last_seen_at"] == "2026-01-10T01:00:00Z"
    assert record["observed_at"] == record["last_seen_at"]


def test_first_seen_future_does_not_create_future_last_seen():
    profiles.sync_run(run(records=[row(first_seen_at="2099-01-01")]))
    record = profiles.profile_report("example")["records"][0]
    assert record["first_seen_at"] == record["last_seen_at"] == "2026-01-10T12:00:00Z"
    assert record["published_at"] is None and record["interaction_at"] is None


def test_late_import_of_an_old_observation_does_not_overwrite_newer_caption():
    profiles.sync_run(run(records=[row(caption="Actual", observed_at="2026-02-01")], created_at="2026-02-01"))
    profiles.sync_run(run("late_import", records=[row(caption="Antigua", observed_at="2026-01-01", ocr_text="Detalle recuperado")], created_at="2026-03-01"))
    record = profiles.profile_report("example")["records"][0]
    assert record["caption"] == "Actual"
    assert record["ocr_text"] == "Detalle recuperado"
    assert record["origin_run_id"] == "one"


def test_migration_is_persistent_and_picks_up_changed_and_new_runs():
    store.save_run(run())
    store.save_run(run("two", records=[row("456")], created_at="2026-02-10T12:00:00Z"))
    assert profiles.list_profiles()[0]["record_count"] == 2
    module = importlib.reload(profiles)
    assert module.list_profiles()[0]["record_count"] == 2
    changed = store.load_run("one")
    changed["records"][0].update(ocr_text="Manual", manual_fields={"ocr_text":"2026-03-01"})
    store.save_run(changed)
    assert module.known_records("example")[profiles.record_key(row())]["ocr_text"] == "Manual"
    store.save_run(run("three", records=[row("789")]))
    assert module.list_profiles()[0]["record_count"] == 3


def test_deleted_profile_cache_rebuilds_all_migrated_runs():
    store.save_run(run())
    store.save_run(run("two", records=[row("456")]))
    assert profiles.list_profiles()[0]["record_count"] == 2
    (store.DATA / "profiles" / "example.json").unlink()
    assert profiles.list_profiles()[0]["record_count"] == 2


def test_demo_and_aggregate_and_cross_profile_rows_do_not_contaminate():
    store.save_run(run("demo", is_demo=True, profile="example"))
    store.save_run(run("aggregate", aggregate=True))
    profiles.sync_run(run("demo", is_demo=True))
    assert profiles.list_profiles() == []
    mixed = run(records=[row(profile="different"), row("456", source="uploads"), row("456", source="likes")])
    profiles.sync_run(mixed)
    records = profiles.profile_report("example")["records"]
    assert len(records) == 2
    assert {record["source"] for record in records} == {"uploads", "likes"}
    assert profiles.known_records("different") == {}


def test_report_keeps_last_source_status_without_erasing_cumulative_evidence():
    profiles.sync_run(run(records=[row(source="likes")]))
    profiles.sync_run(run("two", records=[], created_at="2026-04-10T12:00:00Z",
                          collection={"sources":{"likes":{"status":"private", "reason":"Ocultos"}}}))
    report = profiles.profile_report("example")
    assert report["aggregate"] and report["id"].startswith("profile_")
    assert report["analysis"] == {} and len(report["records"]) == 1
    assert report["collection"]["sources"]["likes"]["status"] == "private"
    assert report["collection"]["sources"]["likes"]["cumulative_count"] == 1
    assert report["collection"]["run_count"] == 2


def test_metrics_history_is_bounded_but_observation_count_remains_complete(monkeypatch):
    monkeypatch.setattr(profiles, "METRICS_HISTORY_LIMIT", 3)
    for number in range(7):
        stamp = f"2026-01-{number+10:02d}T12:00:00Z"
        profiles.sync_run(run(f"run{number}", records=[row(observed_at=stamp, metrics={"views":number})], created_at=stamp))
    record = profiles.profile_report("example")["records"][0]
    assert record["observation_count"] == 7
    assert [item["value"]["views"] for item in record["metrics_history"]] == [4, 5, 6]


def test_concurrent_sync_preserves_all_records_and_observations():
    items = [run(f"run{number}", records=[row(), row(str(1000 + number))]) for number in range(10)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(profiles.sync_run, items))
    records = profiles.known_records("example")
    assert len(records) == 11
    assert records[profiles.record_key(row())]["observation_count"] == 10


@pytest.mark.parametrize("changes", [{"max_items":0}, {"max_items":True}, {"scroll_rounds":51},
                                      {"mode":"wrong"}, {"sources":[]}, {"sources":["manual"]},
                                      {"tags":"tag"}, {"record_count":3}, {"notes":None}])
def test_reject_invalid_settings(changes):
    with pytest.raises(ValueError):
        profiles.save_profile("example", changes)


def test_profile_path_is_validated():
    for operation in (profiles.known_records, profiles.profile_report):
        with pytest.raises(ValueError):
            operation("../../outside")
    with pytest.raises(ValueError):
        profiles.save_profile("../../outside", {})


def avatar_file(name="face.png"):
    from PIL import Image
    path = store.DATA / "artifacts" / "profiles" / "example" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color="blue").save(path)
    return store.relative(path)


def test_headless_defaults_and_settings_are_boolean():
    assert profiles.save_profile("example", {})["headless"] is True
    assert profiles.save_profile("example", {"headless":False})["headless"] is False
    assert profiles.profile_report("example")["headless"] is False
    for invalid in (1, "false", [], None):
        with pytest.raises(ValueError):
            profiles.save_profile("example", {"headless":invalid})


def test_capture_avatar_updates_profile_and_report_only_from_valid_local_images():
    path = avatar_file()
    captured = run(collection={"sources":{}, "profile_avatar":{
        "path":path, "source":"profile_capture", "observed_at":"2026-01-11T00:00:00Z"}})
    summary = profiles.sync_run(captured)
    assert summary["avatar_path"] == path
    assert summary["avatar_source"] == "profile_capture"
    assert summary["avatar_updated_at"] == "2026-01-11T00:00:00Z"
    assert profiles.profile_report("example")["avatar_path"] == path
    old = avatar_file("old.png")
    profiles.sync_run(run("older", collection={"sources":{}, "profile_avatar":{
        "path":old, "source":"profile_capture", "observed_at":"2026-01-01T00:00:00Z"}}))
    assert profiles.list_profiles()[0]["avatar_path"] == path


def test_user_uploaded_photo_wins_over_later_capture_and_can_be_cleared():
    uploaded = avatar_file("uploaded.png")
    captured = avatar_file("captured.png")
    profiles.save_profile("example", {"avatar_path":uploaded, "avatar_source":"user_upload"})
    profiles.sync_run(run(collection={"sources":{}, "profile_avatar":{
        "path":captured, "source":"profile_capture", "observed_at":"2099-01-01T00:00:00Z"}}))
    assert profiles.list_profiles()[0]["avatar_path"] == uploaded
    cleared = profiles.save_profile("example", {"avatar_source":"initials"})
    assert cleared["avatar_path"] is None and cleared["avatar_source"] == "initials"
    assert profiles.save_profile("example", {"avatar_path":uploaded})["avatar_source"] == "user_upload"
    assert profiles.save_profile("example", {"avatar_path":None})["avatar_source"] == "initials"


@pytest.mark.parametrize("path", ["https://example.com/avatar.png", "../outside.png", "runs/one.json", "artifacts/missing.png", [], 0])
def test_avatar_rejects_remote_outside_missing_and_nontext_paths(path):
    with pytest.raises(ValueError):
        profiles.save_profile("example", {"avatar_path":path, "avatar_source":"user_upload"})


def test_avatar_rejects_fake_image_and_absolute_path():
    good = avatar_file()
    with pytest.raises(ValueError):
        profiles.save_profile("example", {"avatar_path":str(store.DATA / good), "avatar_source":"user_upload"})
    fake = store.DATA / "artifacts" / "fake.png"
    fake.write_text("not image bytes", encoding="utf-8")
    with pytest.raises(ValueError):
        profiles.save_profile("example", {"avatar_path":store.relative(fake), "avatar_source":"user_upload"})
    assert profiles.list_profiles() == []


def test_invalid_captured_avatar_does_not_fail_or_discard_evidence():
    summary = profiles.sync_run(run(collection={"sources":{}, "profile_avatar":{
        "path":"https://example.com/face.png", "source":"profile_capture", "observed_at":"2026-01-11"}}))
    assert summary["avatar_source"] == "initials" and summary["record_count"] == 1


def test_avatar_metadata_and_date_validation():
    for changes in ({"avatar_source":"remote"}, {"avatar_source":[]}, {"avatar_updated_at":"invalid"}):
        with pytest.raises(ValueError):
            profiles.save_profile("example", changes)


def test_profile_summary_reports_persistent_blockers_and_source_coverage():
    failed = run(status="needs_attention", collection={"sources":{
        "likes":{"status":"private", "reason":"Me gusta ocultos"},
        "reposts":{"status":"sampled", "count":1}}, "last_capture_reason":"Sesión expirada"})
    summary = profiles.sync_run(failed)
    assert summary["last_run_status"] == "needs_attention"
    assert summary["last_capture_reason"] == "Sesión expirada"
    assert summary["coverage"]["reposts"]["cumulative_count"] == 1
    assert summary["coverage"]["likes"]["status"] == "private"
    assert summary["coverage"]["uploads"]["status"] == "not_captured"
    assert {item["source"] for item in summary["blockers"]} == {"capture", "likes"}
    assert profiles.profile_report("example")["blockers"] == summary["blockers"]
    assert importlib.reload(profiles).list_profiles()[0]["blockers"] == summary["blockers"]
    recovered = run("two", created_at="2026-04-01", collection={"sources":{
        "likes":{"status":"sampled", "count":1}, "reposts":{"status":"sampled", "count":1}}})
    assert profiles.sync_run(recovered)["blockers"] == []


def test_old_profile_files_get_new_defaults_without_losing_settings():
    import json
    profiles.save_profile("example", {"label":"Antes"})
    path = store.DATA / "profiles" / "example.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("headless", "avatar_path", "avatar_source", "avatar_updated_at", "last_run_status", "last_capture_reason", "capture_blockers"):
        data.pop(key)
    path.write_text(json.dumps(data), encoding="utf-8")
    summary = profiles.list_profiles()[0]
    assert summary["label"] == "Antes" and summary["headless"] is True
    assert summary["avatar_source"] == "initials"
    assert profiles.profile_report("example")["avatar_path"] is None
    assert profiles.save_profile("example", {"headless":False})["avatar_source"] == "initials"


def test_unchanged_reports_skip_source_json_reads_but_detect_later_edits(monkeypatch):
    source = run()
    store.save_run(source)
    store.save_run(run("two", records=[row("456")]))
    assert profiles.list_profiles()[0]["record_count"] == 2
    reads = []
    original_loader = store.load_run

    def tracking_loader(identifier):
        reads.append(identifier)
        return original_loader(identifier)
    monkeypatch.setattr(store, "load_run", tracking_loader)
    for _ in range(3):
        profiles.list_profiles()
        profiles.profile_report("example")
        profiles.known_records("example")
    assert reads == []
    source["records"][0].update(ocr_text="Corrección detectada", manual_fields={"ocr_text":"2026-06-01"})
    store.save_run(source)
    assert profiles.known_records("example")[profiles.record_key(row())]["ocr_text"] == "Corrección detectada"
    assert reads == ["one"]
    profiles.profile_report("example")
    assert reads == ["one"]


def test_legacy_digest_ledger_upgrades_and_rebuilds_a_missing_cache():
    import json
    sources = [run(), run("two", records=[row("456")])]
    for source in sources:
        store.save_run(source)
    profiles.list_profiles()
    path = store.DATA / "profiles" / "_migration.json"
    path.write_text(json.dumps({source["id"]:profiles._fingerprint(source) for source in sources}), encoding="utf-8")
    (store.DATA / "profiles" / "example.json").unlink()
    assert profiles.list_profiles()[0]["record_count"] == 2
    upgraded = json.loads(path.read_text(encoding="utf-8"))
    assert all(isinstance(entry, dict) and entry["mtime_ns"] > 0 and entry["size"] > 0 for entry in upgraded.values())


def test_late_note_edit_does_not_rewind_automatic_capture_or_origin():
    old=row(caption='Descripción antigua', observed_at='2026-01-01', frames=['artifacts/old.jpg'],
            metrics={'views':10}, frame_quality={'artifacts/old.jpg':{'status':'verified'}})
    newer=row(caption='Descripción reciente', observed_at='2026-02-01', frames=['artifacts/new.jpg'],
              metrics={'views':200}, frame_quality={'artifacts/new.jpg':{'status':'verified'}})
    profiles.sync_run(run('old',records=[old],created_at='2026-01-01'))
    profiles.sync_run(run('new',records=[newer],created_at='2026-02-01'))
    old.update(notes=['Corrección sobre el informe anterior'],edited_at='2026-03-01',manual_fields={'notes':'2026-03-01'})
    profiles.sync_run(run('old',records=[old],created_at='2026-01-01'))
    canonical=profiles.profile_report('example')['records'][0]
    assert canonical['notes']==old['notes']
    assert canonical['caption']=='Descripción reciente' and canonical['metrics']=={'views':200}
    assert canonical['frames']==['artifacts/new.jpg']
    assert canonical['origin_run_id']=='new' and canonical['origin_record_id']==newer['id']
    assert canonical['observation_count']==2 and canonical['observed_at']=='2026-02-01'


def test_user_media_attached_to_history_is_added_with_quality_without_restoring_old_auto_frames():
    old=row(observed_at='2026-01-01',frames=['artifacts/old.jpg'])
    newer=row(observed_at='2026-02-01',frames=['artifacts/new.jpg'],frame_quality={'artifacts/new.jpg':{'status':'verified'}})
    profiles.sync_run(run('old',records=[old],created_at='2026-01-01'))
    profiles.sync_run(run('new',records=[newer],created_at='2026-02-01'))
    old.update(frames=['artifacts/old.jpg','artifacts/uploaded.jpg'],frame_quality={'artifacts/uploaded.jpg':{'status':'verified','source':'user_upload'}})
    profiles.sync_run(run('old',records=[old],created_at='2026-01-01'))
    canonical=profiles.profile_report('example')['records'][0]
    assert canonical['frames']==['artifacts/new.jpg','artifacts/uploaded.jpg']
    assert canonical['frame_quality']['artifacts/uploaded.jpg']['source']=='user_upload'
    assert canonical['origin_run_id']=='new'
