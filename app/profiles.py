"""Saved search settings and an idempotent, cumulative evidence library.

One JSON file per validated profile keeps source runs untouched. Observation dates
describe collection only; they never become publication or interaction dates.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app import store

SOURCES = {"uploads", "reposts", "likes", "manual"}
SETTINGS = {"label", "notes", "tags", "sources", "max_items", "scroll_rounds", "mode", "headless",
            "depth", "date_filter", "session_mode", "wait_captcha",
            "avatar_path", "avatar_source", "avatar_updated_at"}
METRICS_HISTORY_LIMIT = 100
_INTERNAL = {"records", "run_ids", "collection", "collection_updated_at", "capture_blockers"}
_DERIVED = {"id", "record_key", "origin_run_id", "origin_record_id", "observation_count",
            "observation_run_ids", "metrics_history", "first_seen_at", "last_seen_at", "manual_fields"}
_ARRAY_EVIDENCE = {"screenshots", "frames", "artifacts", "archived_frames", "diagnostic_screenshots"}


def _instant(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _date_choice(values, latest=False):
    valid = [(value, _instant(value)) for value in values if _instant(value) is not None]
    if not valid:
        return None
    return (max if latest else min)(valid, key=lambda item: item[1])[0]


def _newer(candidate, existing):
    value, previous = _instant(candidate), _instant(existing)
    return value is not None and (previous is None or value >= previous)


def record_key(record: dict) -> str:
    """Identify one item in one source; the profile is the enclosing namespace."""
    source = record.get("source", "manual")
    if not isinstance(source, str) or source not in SOURCES:
        raise ValueError("Fuente de evidencia inválida.")
    url = record.get("url")
    if url:
        canonical = store.canonical_post_url(url)
        post_id = re.search(r"/(?:video|photo)/([0-9]+)$", urlparse(canonical).path).group(1)
        return f"{source}:post:{post_id}"
    identifier = record.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("La evidencia sin enlace necesita un identificador estable.")
    return f"{source}:id:{identifier}"


def _path(profile):
    return store.DATA / "profiles" / (store.profile_name(profile) + ".json")


def _read(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return copy.deepcopy(fallback)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _empty(profile, created_at=None):
    timestamp = created_at if _instant(created_at) else store.now()
    return dict(profile=profile, label=f"@{profile}", notes="", tags=[],
                sources=["uploads", "reposts", "likes"], max_items=20, scroll_rounds=8,
                mode="incremental", headless=False, depth='quick', date_filter='any', session_mode='logged_in', avatar_path=None, avatar_source="initials", avatar_updated_at=None,
                created_at=timestamp, updated_at=timestamp,
                last_run_at=None, last_run_id=None, last_run_status=None, last_capture_reason=None,
                record_count=0, capture_blockers=[],
                records={}, run_ids=[], collection={"sources":{}}, collection_updated_at={})


def _read_profile(profile, created_at=None):
    return _empty(profile, created_at) | _read(_path(profile), {})


def _coverage(data):
    coverage = {}
    sources = data.get("collection", {}).get("sources", {})
    for source in ("uploads", "reposts", "likes", "manual"):
        count = sum(row.get("source") == source for row in data.get("records", {}).values())
        if source == "manual" and not count and source not in sources:
            continue
        details = sources.get(source)
        details = details if isinstance(details, dict) else {}
        status = details.get("status", "not_captured")
        reason = details.get("reason")
        coverage[source] = dict(status=status if isinstance(status, str) else "unknown", reason=reason if isinstance(reason, str) else None,
                                cumulative_count=count, last_checked_at=data.get("collection_updated_at", {}).get(source))
    return coverage


def _public(data):
    summary = copy.deepcopy({key: value for key, value in data.items() if key not in _INTERNAL})
    summary["coverage"] = _coverage(data)
    blockers = copy.deepcopy(data.get("capture_blockers", []))
    for source, details in summary["coverage"].items():
        if details["status"] in {"private", "unavailable", "blocked", "needs_attention", "failed"}:
            blockers.append(dict(source=source, status=details["status"], reason=details.get("reason"),
                                 observed_at=details["last_checked_at"]))
    summary["blockers"] = _unique(blockers)
    if not summary.get("last_capture_reason"):
        summary["last_capture_reason"] = next((item["reason"] for item in summary["blockers"]
                                                if isinstance(item, dict) and isinstance(item.get("reason"), str)), None)
    return summary


def _avatar_path(value):
    if not isinstance(value, str) or not value or Path(value).is_absolute() or urlparse(value).scheme:
        raise ValueError("La foto debe ser una imagen local dentro de artifacts.")
    path = store.media_path(value)
    if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("No se encontró una foto local compatible.")
    from PIL import Image
    try:
        with Image.open(path) as image:
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("El archivo de foto no contiene una imagen válida.") from exc
    return store.relative(path)


def _capture_avatar(data, collection, observed_at):
    avatar = collection.get("profile_avatar")
    if data["avatar_source"] == "user_upload" or not isinstance(avatar, dict) or avatar.get("source") != "profile_capture":
        return
    timestamp = avatar.get("observed_at") if _instant(avatar.get("observed_at")) else observed_at
    if not _newer(timestamp, data.get("avatar_updated_at")):
        return
    try:
        path = _avatar_path(avatar.get("path"))
    except ValueError:
        return
    data.update(avatar_path=path, avatar_source="profile_capture", avatar_updated_at=timestamp)


def _manual_fields(row):
    values = row.get("manual_fields")
    if not isinstance(values, dict):
        return {}
    return {key: value for key, value in values.items()
            if key in row and key not in _DERIVED and key not in {"profile", "source", "url", "observed_at"}
            and _instant(value) is not None}


def _version(row, fallback=None):
    # A note edited today does not make yesterday's captured metrics or images
    # newer. Manual timestamps are compared per field in _merge only.
    return row.get("observed_at") if _instant(row.get("observed_at")) else fallback


def _has_value(value):
    return value is not None and value != "" and value != [] and value != {}


def _unique(values):
    output, seen = [], set()
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            output.append(copy.deepcopy(value))
    return output


def _merge(previous, incoming, run, profile, key):
    old = copy.deepcopy(previous or {})
    merged = copy.deepcopy(old)
    old_manual, new_manual = _manual_fields(old), _manual_fields(incoming)
    manual = dict(old_manual)
    current = _version(incoming, run.get("created_at"))
    latest = not old or _newer(current, _version(old))
    for field, value in incoming.items():
        if field in _DERIVED or field in {"profile", "source"}:
            continue
        if field in new_manual and (field not in old_manual or _newer(new_manual[field], old_manual[field])):
            merged[field] = copy.deepcopy(value)  # Explicit null/empty clears are authoritative.
            manual[field] = new_manual[field]
        elif field in old_manual:
            continue
        elif latest and field in {'ocr_text', 'visual_summary', 'visual_analysis'} and field in (incoming.get('quality_invalidated_fields') or []):
            merged[field] = copy.deepcopy(value)
        elif latest and field == 'frames' and isinstance(incoming.get('frame_quality'), dict):
            # Repaired captures explicitly separate verified media from legacy
            # screenshots; unioning the old frames here would restore bad input.
            quality = incoming['frame_quality']
            merged[field] = [path for path in value if quality.get(path, {}).get('status') in {'verified', 'user_provided', 'user_uploaded'}
                             or quality.get(path, {}).get('source') == 'user_upload']
        elif latest and field == 'screenshots' and isinstance(incoming.get('frame_quality'), dict):
            merged[field] = copy.deepcopy(value)
        elif not latest and field == 'frames' and _has_value(old.get(field)):
            # A historical note edit must not restore historical automatic
            # images. Newly attached user media remains valid enrichment.
            quality = incoming.get('frame_quality') or {}
            user_frames = [path for path in value if quality.get(path, {}).get('source') == 'user_upload'
                           or quality.get(path, {}).get('status') in {'user_provided', 'user_uploaded'}]
            merged[field] = _unique(old[field] + user_frames)
            if user_frames:
                merged.setdefault('frame_quality', {}).update({path:copy.deepcopy(quality[path]) for path in user_frames})
        elif not latest and field == 'screenshots' and isinstance(old.get('frame_quality'), dict):
            merged['diagnostic_screenshots'] = _unique((merged.get('diagnostic_screenshots') or []) + (value or []))
        elif field in _ARRAY_EVIDENCE and isinstance(value, list):
            merged[field] = _unique((old.get(field) or []) + value)
        elif _has_value(value) and (latest or not _has_value(old.get(field))):
            merged[field] = copy.deepcopy(value)
        elif field not in merged:
            merged[field] = copy.deepcopy(value)
    observed = incoming.get("observed_at") if _instant(incoming.get("observed_at")) else run.get("created_at")
    merged.update(id=hashlib.sha256(f"{profile}|{key}".encode()).hexdigest()[:20],
                  record_key=key, profile=profile, source=incoming.get("source", "manual"), manual_fields=manual,
                  first_seen_at=_date_choice([old.get("first_seen_at"), incoming.get("first_seen_at"), observed]),
                  last_seen_at=_date_choice([old.get("last_seen_at"), observed], latest=True))
    merged["observed_at"] = merged["last_seen_at"]
    # Imported first_seen_at may be wrong; it cannot move last_seen_at forward.
    run_ids = sorted(set(old.get("observation_run_ids", [])) | {run["id"]})
    merged.update(observation_run_ids=run_ids, observation_count=len(run_ids))
    if latest or not old.get("origin_run_id"):
        merged.update(origin_run_id=run["id"], origin_record_id=incoming["id"])
    history = old.get("metrics_history", [])
    if isinstance(incoming.get("metrics"), dict) and incoming["metrics"] and _instant(observed):
        # Updating the same observation replaces its metric snapshot, without inflating history.
        history = [item for item in history if item.get("run_id") != run["id"]]
        history.append({"timestamp":observed, "value":copy.deepcopy(incoming["metrics"]), "run_id":run["id"]})
    merged["metrics_history"] = sorted(history, key=lambda item: _instant(item.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))[-METRICS_HISTORY_LIMIT:]
    return merged


def _sync(run):
    profile = store.profile_name(run["profile"])
    path = _path(profile)
    data = _read_profile(profile, run.get("created_at"))
    if run.get("is_demo") or run.get("aggregate"):
        return _public(data)
    store.safe_id(run["id"])
    before = copy.deepcopy(data)
    for incoming in run.get("records", []):
        if not isinstance(incoming, dict):
            continue
        # A mixed-profile imported run must never leak evidence into a saved profile.
        try:
            if incoming.get("profile") and store.profile_name(incoming["profile"]) != profile:
                continue
            key = record_key(incoming)
        except (ValueError, TypeError, AttributeError):
            continue
        if not incoming.get("id"):
            continue
        data["records"][key] = _merge(data["records"].get(key), incoming, run, profile, key)
    data["record_count"] = len(data["records"])
    data["run_ids"] = sorted(set(data["run_ids"]) | {run["id"]})
    observed_at = run.get("created_at")
    collection = run.get("collection")
    collection = collection if isinstance(collection, dict) else {}
    if _newer(observed_at, data.get("last_run_at")):
        reason = next((value for value in [collection.get("last_capture_reason"), collection.get("reason"),
                                           collection.get("error"), run.get("error"), run.get("message")]
                       if isinstance(value, str) and value.strip()), None)
        capture_blockers = collection.get("blockers", [])
        capture_blockers = capture_blockers if isinstance(capture_blockers, list) else []
        if not reason and run.get("status") in {"failed", "needs_attention", "cancelled"}:
            reason = {"failed":"La última captura quedó incompleta.",
                      "needs_attention":"La última captura necesita atención en el navegador.",
                      "cancelled":"La última captura se canceló; se conserva la muestra obtenida."}[run["status"]]
        already_reported = any(item == reason if isinstance(item, str) else
                               isinstance(item, dict) and (item.get("reason") == reason or item.get("message") == reason)
                               for item in capture_blockers)
        if reason and run.get("status") in {"failed", "needs_attention"} and not already_reported:
            capture_blockers = capture_blockers + [dict(source="capture", status=run["status"], reason=reason, observed_at=observed_at)]
        data.update(last_run_at=observed_at, last_run_id=run["id"], last_run_status=run.get("status"),
                    last_capture_reason=reason, capture_blockers=copy.deepcopy(capture_blockers))
    data["created_at"] = _date_choice([data["created_at"], observed_at]) or data["created_at"]
    _capture_avatar(data, collection, observed_at)
    sources = collection.get("sources", {})
    if isinstance(sources, dict):
        for source, details in sources.items():
            if source in SOURCES and _newer(observed_at, data["collection_updated_at"].get(source)):
                data["collection"]["sources"][source] = copy.deepcopy(details)
                data["collection_updated_at"][source] = observed_at
    if data != before or not path.exists():
        data["updated_at"] = store.now()
        _write(path, data)
    return _public(data)


def _fingerprint(run):
    return hashlib.sha256(json.dumps(run, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _run_stamp(path):
    stat = path.stat()
    return {"mtime_ns":stat.st_mtime_ns, "size":stat.st_size}


def _ledger_entry(run, digest, stamp=None):
    return dict(digest=digest, profile=store.profile_name(run["profile"]),
                ignored=bool(run.get("is_demo") or run.get("aggregate")), **(stamp or {}))


def _ensure_migrated(skip_run=None):
    """A persistent content ledger handles restarts, old edits, and added runs.

    Files are committed before the ledger; an interrupted migration can replay
    safely because record/run identities and observations are idempotent.
    """
    ledger_path = store.DATA / "profiles" / "_migration.json"
    ledger = _read(ledger_path, {})
    original = dict(ledger)
    missing_profiles = set()
    for entry in ledger.values():
        try:
            if isinstance(entry, dict) and not entry.get("ignored") and not _path(entry["profile"]).exists():
                missing_profiles.add(store.profile_name(entry["profile"]))
        except (KeyError, ValueError, TypeError):
            continue
    changed = []
    # Do not call list_runs here: it parses every potentially large JSON file.
    # Unchanged files need only stat + profile existence checks. A changed file
    # is read once and compared by content before replaying its observations.
    for path in (store.DATA / "runs").glob("*.json"):
        if path.stem == skip_run:
            continue
        try:
            stamp = _run_stamp(path)
            entry = ledger.get(path.stem)
            if isinstance(entry, dict) and all(entry.get(key) == value for key, value in stamp.items()) and (
                    entry.get("ignored") or (entry.get("profile") and entry["profile"] not in missing_profiles and _path(entry["profile"]).exists())):
                continue
            run = store.load_run(path.stem)
            digest = _fingerprint(run)
            profile = store.profile_name(run["profile"])
            if not _path(profile).exists():
                missing_profiles.add(profile)
            changed.append((path.stem, run, digest, stamp))
        except (OSError, ValueError, KeyError, TypeError):
            # Preserve legacy source files even if an individual file is malformed.
            continue
    changed.sort(key=lambda item: _instant(item[1].get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
    for identifier, run, digest, stamp in changed:
        try:
            previous = ledger.get(identifier)
            previous_digest = previous.get("digest") if isinstance(previous, dict) else previous
            if not run.get("is_demo") and not run.get("aggregate") and (
                    previous_digest != digest or store.profile_name(run["profile"]) in missing_profiles):
                _sync(run)
            ledger[identifier] = _ledger_entry(run, digest, stamp)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if ledger != original:
        _write(ledger_path, ledger)


def list_profiles() -> list[dict]:
    with store.LOCK:
        _ensure_migrated()
        profiles = []
        for path in (store.DATA / "profiles").glob("*.json"):
            if path.name == "_migration.json":
                continue
            data = _read(path, {})
            if data.get("profile") and path == _path(data["profile"]):
                profiles.append(_public(_empty(data["profile"]) | data))
        return sorted(profiles, key=lambda item: item.get("updated_at", ""), reverse=True)


def save_profile(profile: str, updates: dict) -> dict:
    name = store.profile_name(profile)
    if not isinstance(updates, dict) or set(updates) - SETTINGS:
        raise ValueError("Ajustes de perfil inválidos.")
    changes = copy.deepcopy(updates)
    for field, maximum in (("label", 200), ("notes", 20000)):
        if field in changes and (not isinstance(changes[field], str) or len(changes[field]) > maximum):
            raise ValueError(f"Campo de perfil inválido: {field}.")
    if "tags" in changes:
        tags = changes["tags"]
        if not isinstance(tags, list) or len(tags) > 50 or any(not isinstance(tag, str) or len(tag) > 100 for tag in tags):
            raise ValueError("Las etiquetas deben ser una lista de textos cortos.")
        changes["tags"] = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
    if "sources" in changes:
        sources = changes["sources"]
        if not isinstance(sources, list) or not sources or any(not isinstance(source, str) or source not in SOURCES - {"manual"} for source in sources):
            raise ValueError("Selecciona publicaciones, compartidos o me gusta.")
        changes["sources"] = list(dict.fromkeys(sources))
    for field, maximum in (("max_items", 200), ("scroll_rounds", 50)):
        if field in changes and (type(changes[field]) is not int or not 1 <= changes[field] <= maximum):
            raise ValueError(f"Límite de perfil inválido: {field}.")
    if "mode" in changes and changes["mode"] not in {"incremental", "refresh"}:
        raise ValueError("Modo de captura inválido.")
    for field, choices in (('depth',{'quick','custom','deep','all'}), ('date_filter',{'any','last30days'}), ('session_mode',{'anonymous','logged_in'})):
        if field in changes and changes[field] not in choices:
            raise ValueError('Preferencia de captura inválida: '+field)
    if "headless" in changes and type(changes["headless"]) is not bool:
        raise ValueError("La captura en segundo plano debe ser true o false.")
    if "avatar_source" in changes and (not isinstance(changes["avatar_source"], str) or changes["avatar_source"] not in {"profile_capture", "user_upload", "initials"}):
        raise ValueError("Origen de foto inválido.")
    if "avatar_path" in changes and changes["avatar_path"] is not None and not isinstance(changes["avatar_path"], str):
        raise ValueError("La ruta de foto debe ser texto o null.")
    if "avatar_updated_at" in changes and changes["avatar_updated_at"] is not None and not _instant(changes["avatar_updated_at"]):
        raise ValueError("La fecha de actualización de foto debe ser ISO.")
    with store.LOCK:
        _ensure_migrated()
        path = _path(name)
        data = _read_profile(name)
        if {"avatar_path", "avatar_source", "avatar_updated_at"} & changes.keys():
            avatar_source = changes.get("avatar_source", "user_upload" if changes.get("avatar_path") else data["avatar_source"])
            avatar_path = changes.get("avatar_path", data["avatar_path"])
            if avatar_source == "initials" or ("avatar_path" in changes and not avatar_path):
                changes.update(avatar_path=None, avatar_source="initials", avatar_updated_at=changes.get("avatar_updated_at") or store.now())
            else:
                changes.update(avatar_path=_avatar_path(avatar_path), avatar_source=avatar_source,
                               avatar_updated_at=changes.get("avatar_updated_at") or store.now())
        data.update(changes, updated_at=store.now())
        _write(path, data)
        return _public(data)


def known_records(profile: str, *, migrate=True) -> dict[str, dict]:
    name = store.profile_name(profile)
    with store.LOCK:
        if migrate:
            _ensure_migrated()
        return copy.deepcopy(_read_profile(name)["records"])


def sync_run(run: dict, *, migrate=True) -> dict:
    with store.LOCK:
        if migrate:
            _ensure_migrated(skip_run=run.get("id"))
        result = _sync(run)
        if migrate and not run.get("is_demo") and not run.get("aggregate"):
            path = store.DATA / "profiles" / "_migration.json"
            ledger = _read(path, {})
            digest = _fingerprint(run)
            stamp = None
            try:
                source = store.DATA / "runs" / (store.safe_id(run["id"]) + ".json")
                # Direct callers may sync unsaved changes. Only certify disk
                # metadata when it really describes the same observation.
                if _fingerprint(store.load_run(run["id"])) == digest:
                    stamp = _run_stamp(source)
            except (FileNotFoundError, ValueError):
                pass
            ledger[run["id"]] = _ledger_entry(run, digest, stamp)
            _write(path, ledger)
        return result


def profile_report(profile: str) -> dict:
    name = store.profile_name(profile)
    with store.LOCK:
        _ensure_migrated()
        data = _read_profile(name)
        records = copy.deepcopy(list(data["records"].values()))
        records.sort(key=lambda row: (_instant(row.get("last_seen_at")) or datetime.min.replace(tzinfo=timezone.utc), row["id"]), reverse=True)
        collection = copy.deepcopy(data["collection"])
        collection.update(method="cumulative_library", run_count=len(data["run_ids"]),
                          note="Biblioteca acumulada de muestras. Una ausencia posterior no prueba eliminación; la fecha de captura no fecha una interacción.")
        for source in SOURCES:
            count = sum(row["source"] == source for row in records)
            if count or source in collection["sources"]:
                details = collection["sources"].get(source)
                details = details if isinstance(details, dict) else {}
                details.update(cumulative_count=count, last_checked_at=data["collection_updated_at"].get(source))
                collection["sources"][source] = details
        return dict(id="profile_" + hashlib.sha256(name.encode()).hexdigest()[:16],
                    profile=name, label=data["label"], created_at=data["created_at"],
                    last_updated_at=data["updated_at"], aggregate=True, status="completed",
                    records=records, run_ids=copy.deepcopy(data["run_ids"]), collection=collection, analysis={},
                    **{key:value for key, value in _public(data).items() if key in {
                        "avatar_path", "avatar_source", "avatar_updated_at", "headless", "record_count", "coverage", "blockers",
                        "last_run_id", "last_run_at", "last_run_status", "last_capture_reason"}})
