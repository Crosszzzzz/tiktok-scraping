"""Planner contracts that protect saved evidence and avoid repeated extraction."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app import incremental, profiles, store


FIXED_NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED_NOW.astimezone(tz) if tz else FIXED_NOW.replace(tzinfo=None)
    monkeypatch.setattr(incremental, "datetime", Clock)
    monkeypatch.setattr(store, "now", lambda: FIXED_NOW.isoformat())


def url(post="123", author="creator", format="video"):
    return f"https://www.tiktok.com/@{author}/{format}/{post}"


def saved(post="123", source="reposts", **changes):
    record = store.empty_record("example", source, url(post))
    record.update(processing_status="complete", processor_version=incremental.PROCESSOR_VERSION, caption="Descripción guardada", observed_at="2026-09-01T10:00:00Z",
                  first_seen_at="2026-09-01T10:00:00Z", frames=["artifacts/source/frame.png"],
                  ocr_text="OCR existente", transcript="Texto de audio", visual_summary="Paisaje", screenshots=["artifacts/source/page.png"])
    record.update(changes)
    return record


def planner(*records, mode="incremental", max_items=2):
    return incremental.CapturePlan("example", {profiles.record_key(record):record for record in records}, mode, max_items)


def test_same_numeric_item_is_deduplicated_across_author_alias_format_and_query():
    plan = planner()
    first, needs_processing = plan.consider("reposts", url(), {}, 0)
    assert needs_processing and first["capture_action"] == "new"
    assert plan.consider("reposts", url(author="renamed", format="photo") + "?lang=es", {}, 1) is None
    other_source, needs_processing = plan.consider("likes", url(author="renamed"), {}, 2)
    assert needs_processing and other_source["source"] == "likes"
    assert plan.stats["new_count"] == 2


def test_known_complete_record_reuses_extraction_and_preserves_manual_clears_and_files():
    record = saved(caption="Corrección manual", ocr_text="", manual_fields={"caption":"2026-09-02", "ocr_text":"2026-09-02"},
                   origin_run_id="old", origin_record_id="old_record", observation_run_ids=["old"], observation_count=1,
                   metrics_history=[{"timestamp":"2026-09-01", "value":{"views":10}}])
    before = deepcopy(record)
    plan = planner(record)
    reused, needs_processing = plan.consider("reposts", url(author="renamed", format="photo"),
                                           {"caption":"Texto automático", "ocr_text":"OCR automático", "music":{}}, 4)
    assert not needs_processing
    assert reused["capture_action"] == "reused" and not plan.at_limit("reposts")
    assert reused["caption"] == "Corrección manual" and reused["ocr_text"] == ""
    assert reused["frames"] == before["frames"] and reused["screenshots"] == before["screenshots"]
    assert reused["transcript"] == before["transcript"] and reused["visual_summary"] == before["visual_summary"]
    assert reused["manual_fields"] == before["manual_fields"]
    assert reused["first_seen_at"] == before["first_seen_at"]
    assert reused["observed_at"] == FIXED_NOW.isoformat()
    assert reused["order_index"] == 4
    assert "origin_run_id" not in reused and "metrics_history" not in reused
    assert record == before  # Planning cannot mutate the cached canonical record.


def test_empty_grid_metadata_keeps_richer_caption_and_direct_music_link():
    record = saved(music={"title":"Título completo", "source":"visible_link", "url":"https://www.tiktok.com/music/123"})
    plan = planner(record)
    reused, expensive = plan.consider("reposts", url(), {"caption":"", "music":{"title":"recortado", "source":"thumbnail_alt"}}, 0)
    assert not expensive
    assert reused["caption"] == record["caption"]
    assert reused["music"] == record["music"]


def test_pending_records_retry_immediately_and_retain_evidence_until_success():
    record = saved(processing_status="pending", processed_at=FIXED_NOW.isoformat())
    plan = planner(record)
    retry, expensive = plan.consider("reposts", url(), {}, 0)
    assert expensive and retry["capture_action"] == "retry"
    assert retry["ocr_text"] == record["ocr_text"] and retry["frames"] == record["frames"]
    plan.finish(retry, success=True)
    assert retry["processing_status"] == "complete"
    assert retry["processor_version"] == incremental.PROCESSOR_VERSION
    assert plan.stats["processed_count"] == 1


@pytest.mark.parametrize("status", ["partial", "failed"])
@pytest.mark.parametrize("elapsed,retry", [(timedelta(hours=5, minutes=59), False), (timedelta(hours=6), True), (timedelta(days=1), True)])
def test_incomplete_extraction_obeys_six_hour_cooldown_with_timezone_offsets(status, elapsed, retry):
    timestamp = (FIXED_NOW - elapsed).astimezone(timezone(timedelta(hours=-4))).isoformat()
    record = saved(processing_status=status, processed_at=timestamp)
    plan = planner(record)
    result, expensive = plan.consider("reposts", url(), {}, 0)
    assert expensive is retry
    assert result["capture_action"] == ("retry" if retry else "reused")


@pytest.mark.parametrize("processed_at", [None, "", "not-a-date"])
def test_legacy_partial_record_without_valid_processing_timestamp_can_retry(processed_at):
    plan = planner(saved(processing_status="partial", processed_at=processed_at))
    result, expensive = plan.consider("reposts", url(), {}, 0)
    assert expensive and result["capture_action"] == "retry"


def test_refresh_bypasses_completed_cache_and_cooldown_but_preserves_manual_evidence():
    first = saved(manual_fields={"caption":"2026-09-02"})
    second = saved("456", processing_status="partial", processed_at=FIXED_NOW.isoformat())
    plan = planner(first, second, mode="refresh")
    for order, record in enumerate((first, second)):
        result, expensive = plan.consider("reposts", record["url"], {"caption":"Otra descripción"}, order)
        assert expensive and result["capture_action"] == "refresh"
        assert result["frames"] == record["frames"]
        if record is first:
            assert result["caption"] == first["caption"]
    assert plan.stats["refreshed_count"] == 2 and plan.at_limit("reposts")


def test_known_items_do_not_consume_work_quota_or_hide_interleaved_new_items():
    known = [saved(str(post)) for post in range(100, 106)]
    plan = planner(*known, max_items=2)
    actions = []
    for order, post in enumerate(("100", "101", "900", "102", "103", "901", "104", "902", "105")):
        result = plan.consider("reposts", url(post), {}, order)
        if result:
            actions.append((post, result[0]["capture_action"]))
    assert [post for post, action in actions if action == "new"] == ["900", "901"]
    assert [post for post, action in actions if action == "reused"] == [str(post) for post in range(100, 106)]
    assert plan.stats["new_count"] == 2 and plan.stats["reused_count"] == 6
    assert plan.consider("uploads", url("902"), {}, 0)[1] is True  # Independent source work budget.


def test_failed_extraction_retains_existing_content_and_sets_retry_cooldown():
    plan = planner(saved(), mode="refresh")
    record, expensive = plan.consider("reposts", url(), {}, 0)
    plan.finish(record, success=False)
    assert expensive and record["processing_status"] == "partial"
    assert record["ocr_text"] == "OCR existente" and record["transcript"] == "Texto de audio"
    assert incremental.retry_due(record) is False
    assert plan.stats["failed_count"] == 1


def test_legacy_browser_frames_are_repaired_once_even_if_previously_complete():
    record=saved(processor_version='2')
    plan=planner(record)
    repaired,expensive=plan.consider('reposts',record['url'],{},0)
    assert expensive and repaired['capture_action']=='retry'
    plan.finish(repaired,True)
    assert not incremental.retry_due(repaired)
