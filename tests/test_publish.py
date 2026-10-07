"""Static public report bundle: sanitized, self-contained and path-free."""
import json

import pytest

from app import publish, store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path.resolve())


def _record():
    media_dir = store.DATA / "artifacts" / "run1" / "rec1"
    media_dir.mkdir(parents=True)
    frame = media_dir / "frame-1.jpg"
    frame.write_bytes(b"fake-image")
    record = store.empty_record("example", "uploads", "https://www.tiktok.com/@example/video/1")
    relative = store.relative(frame)
    record.update(
        caption="Hola #test",
        published_at="2025-09-01",
        ocr_text="Texto",
        frames=[relative],
        frame_quality={relative: {"status": "verified"}},
        manual_fields={"caption": "2026-01-01"},
        url="https://www.tiktok.com/@example/video/1?is_from_webapp=1&secret=abc",
    )
    return record


def test_build_bundle_is_self_contained_and_sanitized(tmp_path):
    record = _record()
    target = publish.build_bundle("example", {"profile": "example", "records": [record]})
    assert (target / "index.html").is_file() and (target / "report.json").is_file()
    assert len(list((target / "media").iterdir())) == 1
    html = (target / "index.html").read_text(encoding="utf-8")
    report_text = (target / "report.json").read_text(encoding="utf-8")
    entry = json.loads(report_text)["records"][0]
    assert entry["url"] == "https://www.tiktok.com/@example/video/1"
    assert entry["frames"][0].startswith("media/")
    assert "manual_fields" not in entry
    assert str(tmp_path) not in html and str(tmp_path) not in report_text


def test_build_bundle_skips_unverified_frames_when_quality_is_known(tmp_path):
    record = _record()
    relative = record["frames"][0]
    record["frame_quality"] = {relative: {"status": "pending"}}
    target = publish.build_bundle("example", {"profile": "example", "records": [record]})
    assert list((target / "media").iterdir()) == []
