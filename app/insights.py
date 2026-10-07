"""Observable mentions and thematic periods, with explicit source attribution.

An @mention is a reference in captured text. It does not establish a real-world
relationship, and content topics do not establish the account owner's biography.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import re
import unicodedata
from urllib.parse import urlsplit


VERIFIED_SOURCES = {"visible", "user_verified"}
_HANDLE = re.compile(r"[a-z0-9_][a-z0-9_.]{0,30}[a-z0-9_]", re.ASCII)
_MENTION = re.compile(r"(?<![\w@./\\])@([A-Za-z0-9_.]+)(?!\w)")
_URL = re.compile(r"(?:https?://|www\.|(?:[a-z0-9-]+\.)+[a-z]{2,}/)[^\s<>()]+", re.I)


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _handle(value):
    """Normalize only explicit handles/URLs; display names are not identities."""
    value = unicodedata.normalize("NFKC", _text(value)).lower().rstrip("/")
    if value.startswith(("https://", "http://")):
        try:
            parts = urlsplit(value)
            if parts.hostname not in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
                return ""
            value = parts.path.split("/")[1]
        except (ValueError, IndexError):
            return ""
    elif value.startswith(("tiktok.com/@", "www.tiktok.com/@")):
        value = value.split("/")[1]
    value = value.removeprefix("@")
    return "@" + value if _HANDLE.fullmatch(value) else ""


def observed_repost_note(record):
    """Return public note text only when its recorded origin is verifiable."""
    if record.get("source") != "reposts":
        return ""
    if _text(record.get("repost_note_source")) not in VERIFIED_SOURCES:
        return ""
    return _text(record.get("repost_note"))


def extract_mentions(record: dict) -> list[dict]:
    """Extract literal mentions in captions/notes, preserving their authorship.

OCR, lyrics, visual summaries, analyst notes, and video URL paths are not used
as identity references. Repetition within the same text field counts once.
"""
    if not isinstance(record, dict):
        return []
    profile = _handle(record.get("profile"))
    source = _text(record.get("source"))
    author = _handle(record.get("author"))
    fields = [("caption", _text(record.get("caption")),
               profile if source == "uploads" else author,
               "profile" if source == "uploads" and profile else
               "original_author" if source in {"reposts", "likes"} else "unverified")]
    if source == "reposts" and _text(record.get("repost_note")):
        note_author = _handle(record.get("repost_note_author"))
        verified = bool(observed_repost_note(record)) and not record.get("_repost_note_conflict")
        fields.append(("repost_note", _text(record.get("repost_note")),
                       note_author if verified else "",
                       "profile" if verified and profile and note_author == profile else "unverified"))
    contexts = []
    for origin, raw_text, actor, attribution in fields:
        text = unicodedata.normalize("NFKC", raw_text)
        # Preserve character offsets for the evidence excerpt while masking URLs.
        searchable = _URL.sub(lambda match: " " * len(match.group(0)), text)
        seen = set()
        for match in _MENTION.finditer(searchable):
            handle = _handle(match.group(1).rstrip("."))
            if not handle or handle in seen:
                continue
            seen.add(handle)
            start = max(0, match.start() - 95)
            end = min(len(text), match.end() + 140)
            excerpt = ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
            contexts.append({
                "handle": handle, "record_id": str(record.get("id", "")),
                "profile": profile, "source": source, "origin": origin,
                "actor": actor, "excerpt": excerpt, "attribution": attribution,
                "is_self": bool(profile and handle == profile),
            })
    return contexts


def _key(record):
    profile = _handle(record.get("profile")) or _text(record.get("profile")).lower()
    return (profile, _text(record.get("source")), str(record.get("id", "")))


def _month_number(month):
    year, number = month.split("-")
    return int(year) * 12 + int(number)


def _periods(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["profile"], row["source"], row["date_basis"])].append(row)
    periods = []
    for (profile, source, basis), group in grouped.items():
        topic_months = defaultdict(set)
        for row in group:
            for topic in row["topics"]:
                topic_months[topic].add(row["month"])
        for topic, months in topic_months.items():
            episodes = []
            for month in sorted(months):
                if episodes and _month_number(month) == _month_number(episodes[-1][-1]) + 1:
                    episodes[-1].append(month)
                else:
                    episodes.append([month])
            for episode in episodes:
                in_period = [row for row in group if episode[0] <= row["month"] <= episode[-1]]
                matching = [row for row in in_period if topic in row["topics"]]
                if len(matching) < 2:
                    continue
                count, total = len(matching), len(in_period)
                label = {"uploads": "publicaciones propias", "reposts": "republicaciones", "likes": "Me gusta"}[source]
                date_label = "publicación" if basis == "own_publication" else "interacción verificada"
                description = (
                    f"«{topic}» aparece en {count} de {total} {label} ({count / total:.0%}) "
                    f"de la muestra entre {episode[0]} y {episode[-1]}, según la fecha de {date_label}. "
                    "Describe el contenido observado; no establece un acontecimiento personal."
                )
                periods.append({
                    "topic": topic, "profile": profile, "source": source,
                    "start": episode[0], "end": episode[-1], "date_basis": basis,
                    "count": count, "total_in_period": total, "share": round(count / total, 4),
                    "evidence_ids": list(dict.fromkeys(row["id"] for row in matching)),
                    "description": description,
                    "limitations": [
                        "El intervalo reúne meses consecutivos con evidencia del tema; sus extremos son los meses observados, no el inicio o fin de una vivencia.",
                        "La muestra puede ser incompleta. Un mes sin registros del tema no demuestra ausencia de actividad ni de ese contenido.",
                        "Las reglas léxicas requieren revisar contexto, citas, ficción y negaciones; no establecen rupturas, infidelidad ni relaciones personales.",
                    ],
                })
    return sorted(periods, key=lambda row: (row["start"], row["end"], row["profile"], row["source"], row["topic"]))


def _data_quality(records, dated_keys):
    """Summarize gaps separately from explicit player/capture notices."""
    availability_counts = Counter({name: 0 for name in ("available", "unavailable", "blocked", "partial", "unknown")})
    audio_counts = Counter({name: 0 for name in ("available", "muted_notice", "not_captured", "unknown")})
    missing_context = Counter({field: 0 for field in ("ocr_text", "transcript", "visual_summary")})
    issues = {}
    issue_records = defaultdict(set)
    record_issues = []

    def add_issue(record, code, message, basis):
        key = (code, message, basis)
        issue = issues.setdefault(key, {"code": code, "message": message, "count": 0,
                                       "basis": basis, "evidence_ids": []})
        issue_records[key].add(_key(record))
        identifier = str(record.get("id", ""))
        if identifier not in issue["evidence_ids"]:
            issue["evidence_ids"].append(identifier)

    for record in records:
        availability = record.get("availability") if isinstance(record.get("availability"), dict) else {}
        status = _text(availability.get("status"))
        if status not in availability_counts:
            status = "unknown"
        audio = _text(availability.get("audio_status")) or "not_captured"
        if audio not in audio_counts:
            audio = "unknown"
        availability_counts[status] += 1
        audio_counts[audio] += 1
        explicit_issues = availability.get("issues") if isinstance(availability.get("issues"), list) else []
        valid_issues = []
        for issue in explicit_issues:
            if not isinstance(issue, dict) or not _text(issue.get("message")):
                continue
            code = _text(issue.get("code")) or "capture_notice"
            message = _text(issue["message"])
            basis = _text(issue.get("basis")) or "recorded_notice"
            add_issue(record, code, message, basis)
            valid_issues.append({"code": code, "message": message, "basis": basis})
        audio_reason = _text(availability.get("audio_reason"))
        if audio == "muted_notice" and not any(issue["code"] == "audio_muted_notice" for issue in valid_issues):
            reason = audio_reason or "Aviso registrado de audio silenciado o no disponible; no se ha establecido la causa."
            add_issue(record, "audio_muted_notice", reason, "recorded_notice")
            valid_issues.append({"code": "audio_muted_notice", "message": reason, "basis": "recorded_notice"})
        if status in {"unavailable", "blocked"} and not valid_issues:
            add_issue(record, "post_" + status,
                      "La captura registra contenido " + ("no disponible." if status == "unavailable" else "bloqueado."),
                      "recorded_status")
        record_issues.append({"record_id": str(record.get("id", "")), "source": _text(record.get("source")),
                              "status": status, "audio_status": audio, "audio_reason": audio_reason,
                              "issues": valid_issues})
        for field, message in (
            ("ocr_text", "Sin texto OCR registrado; puede faltar texto incrustado en imágenes o fotogramas."),
            ("transcript", "Sin transcripción registrada; no permite conocer las palabras del audio ni demuestra que haya sido eliminado."),
            ("visual_summary", "Sin descripción visual registrada; el significado de la imagen puede faltar en el análisis textual."),
        ):
            if not _text(record.get(field)):
                missing_context[field] += 1
                add_issue(record, "missing_" + field, message, "record_fields")
        if _key(record) not in dated_keys:
            if record.get("source") == "uploads":
                add_issue(record, "missing_publication_date", "Publicación propia sin fecha de publicación válida; no se incluye en los periodos.", "record_fields")
            elif record.get("source") in {"reposts", "likes"}:
                add_issue(record, "missing_interaction_date", "Sin fecha verificable de interacción; la publicación original y la captura no permiten fechar el compartido o Me gusta.", "record_fields")
    for key, issue in issues.items():
        issue["count"] = len(issue_records[key])
    missing_date_count = sum(_key(record) not in dated_keys for record in records)
    summary = (
        f"Avisos registrados: {availability_counts['unavailable']} registros no disponibles, {availability_counts['blocked']} bloqueados "
        f"y {audio_counts['muted_notice']} con aviso de audio silenciado o no disponible. "
        f"Faltan fechas utilizables en {missing_date_count} registros y transcripción en {missing_context['transcript']}. "
        "La falta de audio capturado no demuestra restricciones de derechos de autor."
    )
    return {"summary": summary, "availability_counts": dict(availability_counts), "audio_counts": dict(audio_counts),
            "missing_context": dict(missing_context), "missing_date_count": missing_date_count,
            "issues": sorted(issues.values(), key=lambda row: (row["basis"] == "record_fields", -row["count"], row["code"])),
            "record_issues": record_issues}


def _profile_summary(analysis, mentions, periods, quality):
    """Summarize public-content patterns; never label the account owner's traits."""
    observations = []
    labels = {"uploads": "publicaciones propias", "reposts": "republicaciones", "likes": "Me gusta", "manual": "registros manuales"}
    for source in analysis.get("source_comparison", []):
        top = source.get("topics", [])[:2]
        if not top:
            continue
        details = "; ".join(f"«{row['name']}» en {row['count']} de {source['count']}" for row in top)
        observations.append({"text": f"En {labels.get(source['source'], source['source'])}, los temas más frecuentes detectados son {details}.",
                             "evidence_ids": list(dict.fromkeys(identifier for row in top for identifier in row["evidence_ids"]))})
    for period in sorted(periods, key=lambda row: (row["end"], row["count"]), reverse=True)[:2]:
        observations.append({"text": period["description"], "evidence_ids": period["evidence_ids"]})
    attributable = [row for row in mentions if row["owner_authored_count"] and not row["is_self"]]
    if attributable:
        details = ", ".join(f"{row['handle']} ({row['owner_authored_count']} registros)" for row in attributable[:3])
        observations.append({"text": f"Las menciones atribuibles al perfil incluyen {details}. Indican referencias públicas, sin establecer el vínculo entre las cuentas.",
                             "evidence_ids": list(dict.fromkeys(context["record_id"] for row in attributable[:3]
                                                                 for context in row["contexts"] if context["attribution"] == "profile"))})
    unknowns = [
        "El contenido no permite establecer personalidad, estado sentimental, una ruptura o infidelidad del titular.",
        "No se conoce la cobertura total del historial ni el contexto de contenidos ausentes, borrados o privados.",
    ]
    if quality["missing_date_count"]:
        unknowns.append(f"{quality['missing_date_count']} registros no pueden situarse en los periodos con sus fechas actuales.")
    if quality["missing_context"]["transcript"]:
        unknowns.append(f"No hay transcripción de audio en {quality['missing_context']['transcript']} registros; el título musical no sustituye la letra ni las palabras del video.")
    top_topics = analysis.get("topics", [])[:3]
    total = analysis.get("metrics", {}).get("total_records", 0)
    if top_topics:
        topics = ", ".join(f"{row['name']} ({row['count']}/{total})" for row in top_topics)
        text = f"En los {total} registros analizados destacan estos temas del contenido: {topics}. "
        if periods:
            text += f"Se identificaron {len(periods)} periodos temáticos con fechas utilizables. "
        else:
            text += "La evidencia fechada todavía no delimita periodos temáticos. "
        text += "Este resumen describe lo publicado e interactuado, sin convertirlo en hechos de la vida privada."
    else:
        text = "El contenido y contexto disponibles todavía no permiten construir un resumen temático respaldado por evidencia."
    return {"text": text, "observations": observations, "unknowns": unknowns}


def enrich_analysis(records: list[dict], analysis: dict) -> dict:
    """Build additional observations from already deduplicated, dated evidence."""
    records = [record for record in records if isinstance(record, dict)]
    evidence = {_key(row): row for row in analysis.get("evidence_index", []) if isinstance(row, dict)}
    mentions = {}
    mention_records = defaultdict(set)
    owner_records = defaultdict(set)
    dated = []
    dated_keys = set()
    unverified_notes = 0
    for record in records:
        key = _key(record)
        for context in extract_mentions(record):
            handle = context["handle"]
            entry = mentions.setdefault(handle, {"handle": handle, "count": 0, "evidence_ids": [],
                                                "contexts": [], "owner_authored_count": 0, "is_self": False})
            entry["contexts"].append(context)
            entry["is_self"] = entry["is_self"] or context["is_self"]
            mention_records[handle].add(key)
            if context["attribution"] == "profile":
                owner_records[handle].add(key)
            if context["record_id"] not in entry["evidence_ids"]:
                entry["evidence_ids"].append(context["record_id"])
        if _text(record.get("repost_note")) and (not observed_repost_note(record) or
                record.get("_repost_note_conflict") or not _handle(record.get("repost_note_author"))):
            unverified_notes += 1
        item = evidence.get(key, {})
        source = _text(record.get("source"))
        date = item.get("published_at") if source == "uploads" else item.get("interaction_at")
        if not date or source not in {"uploads", "reposts", "likes"}:
            continue
        dated_keys.add(key)
        dated.append({"id": str(record.get("id", "")), "profile": key[0],
                      "source": source, "date_basis": "own_publication" if source == "uploads" else "interaction",
                      "month": date[:7], "topics": item.get("topics", [])})
    for handle, entry in mentions.items():
        entry["count"] = len(mention_records[handle])
        entry["owner_authored_count"] = len(owner_records[handle])
    sorted_mentions = sorted(mentions.values(), key=lambda row: (-row["owner_authored_count"], -row["count"], row["handle"]))
    periods = _periods(dated)
    quality = _data_quality(records, dated_keys)
    return {
        "mentions": sorted_mentions,
        "thematic_periods": periods,
        "data_quality": quality,
        "profile_summary": _profile_summary(analysis, sorted_mentions, periods, quality),
        "observation_summary": {
            "dated_period_records": len(dated), "undated_period_records": len(records) - len(dated),
            "total_mentions": len(mentions), "owner_authored_mentions": sum(bool(rows) for rows in owner_records.values()),
            "unverified_note_count": unverified_notes,
            "caveat": "Las menciones identifican referencias textuales, no vínculos personales. Una mención en una descripción republicada pertenece al autor original; solo una nota con autor y procedencia verificados se atribuye al perfil consultado.",
        },
    }
