"""Evidence-linked, conservative analysis of a captured TikTok content sample.

This module deliberately analyzes content rather than the account owner's private
life. It uses only local text and explicit metadata; it does not call a model.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import html
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from .insights import enrich_analysis, observed_repost_note


TOPICS = {
    "Despedidas y cambios en vínculos": (
        "despedida", "adios", "soltar", "terminamos", "ruptura", "rompimiento",
        "corazon roto", "superar a", "me dejaste", "te fuiste", "dejar ir",
        "volver contigo", "te extrano", "ex pareja", "expareja", "breakup",
        "heartbreak", "separacion", "ya no estamos", "terminar la relacion",
        "despecho", "desamor", "desamorosa", "desamoroso", "me enganaste",
        "infidelidad", "infiel", "traicion", "me cambiaste", "ya te supere",
    ),
    "Amistad y compañía": (
        "amistad", "amigos", "amigas", "amigo", "amiga", "companeros",
        "juntos", "amistades", "friend", "bestie", "te acompano",
    ),
    "Motivación y metas": (
        "motivacion", "metas", "objetivos", "disciplina", "perseverar",
        "no te rindas", "sigue adelante", "logros", "esfuerzo", "cumplir mis suenos",
    ),
    "Humor y entretenimiento": (
        "humor", "meme", "memes", "comedia", "broma", "jajaja", "jajaja",
        "risa", "chiste", "parodia", "sarcasmo", "ironia", "funny",
    ),
    "Navidad y celebraciones estacionales": (
        "navidad", "navideno", "navidena", "navidenos", "navidenas",
        "christmas", "nochebuena", "ano nuevo", "nochevieja", "halloween",
        "carnaval", "decoracion navidena", "luces navidenas", "arbol de navidad",
    ),
    "Cine y ficción": (
        "cine", "pelicula", "peliculas", "film", "movie", "movies", "serie",
        "series", "escena", "ficcion", "personaje", "personajes", "dorama",
        "kdrama", "novela", "protagonista", "protagonistas", "cinema",
    ),
    "Romance y afecto en el contenido": (
        "romance", "romantico", "romantica", "romanticos", "romanticas",
        "enamorados", "novios", "beso", "besos", "te amo", "te quiero",
        "carino", "abrazo", "abrazos", "juntos por siempre",
        "mi novio", "mi novia", "mi pareja", "pareja", "parejas", "enamorada", "enamorado",
    ),
    "Música y baile": (
        "musica", "cancion", "cantar", "cantando", "baile", "bailar",
        "bailando", "concierto", "coreografia", "guitarra", "piano", "dance",
    ),
    "Estética y estilo": (
        "maquillaje", "outfit", "moda", "peinado", "vestido", "ropa",
        "skincare", "manicura", "fotografia", "dibujo", "pintura",
    ),
    "Viajes y lugares": (
        "viaje", "viajar", "viajes", "turismo", "playa", "aeropuerto",
        "vacaciones", "paisaje", "lugares", "travel", "senderismo",
    ),
    "Comida y cocina": (
        "receta", "cocina", "cocinar", "comida", "restaurante", "postre",
        "desayuno", "almuerzo", "cena", "pizza", "cafe", "panaderia",
    ),
    "Deporte y movimiento": (
        "deporte", "entrenamiento", "gimnasio", "gym", "futbol", "basquet",
        "correr", "natacion", "voleibol", "ejercicio", "fitness", "ciclismo",
    ),
    "Animales y naturaleza": (
        "perro", "perrito", "gato", "gatito", "mascota", "animales",
        "naturaleza", "bosque", "flores", "plantas", "montana",
    ),
    "Estudios y aprendizaje": (
        "estudio", "estudios", "estudiar", "universidad", "colegio",
        "examen", "aprendizaje", "tutorial", "clase", "tarea", "curso", "cursos",
    ),
    "Trabajo y proyectos": (
        "trabajo", "trabajar", "oficina", "emprendimiento", "negocio",
        "proyecto", "cliente", "programacion", "programar", "diseno",
    ),
    "Juegos y tecnología": (
        "videojuego", "videojuegos", "gaming", "gamer", "minecraft",
        "fortnite", "tecnologia", "computadora", "celular", "software",
    ),
    "Rutinas y vida cotidiana": (
        "rutina", "dia a dia", "vlog", "limpieza", "ordenar", "compras",
        "mi dia", "vida cotidiana", "organizacion", "decoracion",
    ),
    "Recuerdos y nostalgia": (
        "recuerdo", "recuerdos", "recordar", "nostalgia", "nostalgico",
        "aquellos tiempos", "infancia", "cuando eramos", "throwback",
    ),
}

TONES = {
    "Alegría expresada en el contenido": ("feliz", "felicidad", "alegria", "que emocion", "celebrar"),
    "Tristeza expresada en el contenido": ("triste", "tristeza", "llorar", "llorando", "me duele"),
    "Afecto expresado en el contenido": ("te quiero", "te amo", "carino", "abrazo", "amor"),
    "Frustración expresada en el contenido": ("rabia", "frustracion", "enojo", "enojado", "me molesta"),
}

SOURCE_LABELS = {
    "reposts": "Republicaciones",
    "likes": "Me gusta",
    "uploads": "Publicaciones propias",
    "manual": "Registro manual",
}
TEXT_FIELDS = ("caption", "ocr_text", "transcript", "visual_summary")
VERIFIED_DATE_SOURCES = {"visible", "user_verified"}


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _normalize(value):
    value = unicodedata.normalize("NFKD", _text(value).lower())
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).split())


def _matches(text, terms):
    return any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text) for term in terms)


def _iso_date(value):
    value = _text(value)
    if not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|T| )", value):
        return None
    try:
        # Keep the explicitly recorded local calendar date, without inventing a timezone.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _safe_url(value):
    value = _text(value)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"https", "http"} or not parts.netloc:
            return ""
        return value.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
    except ValueError:
        return ""


def _canonical_url(value):
    safe = _safe_url(value)
    if not safe:
        return ""
    parts = urlsplit(safe)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def _prepare(records):
    """Deduplicate within each profile/source and retain a safe evidence identity."""
    unique = {}
    duplicate_count = 0
    invalid_count = 0
    conflict_count = 0
    for original in records:
        if not isinstance(original, dict):
            invalid_count += 1
            continue
        record = dict(original)
        source = _text(record.get("source")) or "manual"
        record["source"] = source if source in SOURCE_LABELS else "manual"
        canonical = _canonical_url(record.get("url"))
        identifier = _text(str(record.get("id"))) if record.get("id") is not None else ""
        # A TikTok ID in the URL also joins captures whose supplied ID is absent.
        url_id = re.search(r"/(?:video|photo)/(\d+)", canonical)
        identity = ("video:" + url_id.group(1)) if url_id else (identifier or canonical)
        if not identity:
            # Text-identical records without IDs/URLs could be different videos.
            identity = "unidentified-" + str(len(unique))
        profile = _text(record.get("profile")).lower().rstrip("/")
        key = (profile, record["source"], identity)
        if not identifier:
            identifier = url_id.group(1) if url_id else "capture-" + hashlib.sha256(
                repr(key).encode("utf-8")
            ).hexdigest()[:12]
        record["id"] = identifier
        if key not in unique:
            unique[key] = record
            continue
        duplicate_count += 1
        previous = unique[key]
        old_date = _interaction_date(previous)
        new_date = _interaction_date(record)
        if old_date and new_date and old_date != new_date:
            previous["_date_conflict"] = True
            conflict_count += 1
        for field in TEXT_FIELDS + ("notes",):
            old_text, new_text = _text(previous.get(field)), _text(record.get(field))
            if len(new_text) > len(old_text):
                previous[field] = new_text
        # A note's text, author and provenance form one observation. Never combine
        # a longer note from one capture with a different capture's attribution.
        old_note, new_note = _text(previous.get("repost_note")), _text(record.get("repost_note"))
        if old_note and new_note:
            old_author, new_author = _text(previous.get("repost_note_author")), _text(record.get("repost_note_author"))
            if old_note != new_note or (old_author and new_author and old_author.lower() != new_author.lower()):
                previous["_repost_note_conflict"] = True
            elif observed_repost_note(record) and (not observed_repost_note(previous) or not old_author):
                for field in ("repost_note", "repost_note_author", "repost_note_source"):
                    previous[field] = record.get(field)
        elif new_note:
            for field in ("repost_note", "repost_note_author", "repost_note_source"):
                previous[field] = record.get(field)
        for field in ("screenshots", "frames"):
            old_items = previous.get(field) if isinstance(previous.get(field), list) else []
            new_items = record.get(field) if isinstance(record.get(field), list) else []
            previous[field] = list(dict.fromkeys(str(v) for v in old_items + new_items))
        if not old_date and new_date and not previous.get("_date_conflict"):
            previous["interaction_at"] = record.get("interaction_at")
            previous["interaction_date_source"] = record.get("interaction_date_source")
        for field, value in record.items():
            if field in {"repost_note", "repost_note_author", "repost_note_source"}:
                continue
            if not previous.get(field) and value:
                previous[field] = value
    return list(unique.values()), duplicate_count, invalid_count, conflict_count


def _interaction_date(record):
    if record.get("_date_conflict") or _text(record.get("interaction_date_source")) not in VERIFIED_DATE_SOURCES:
        return None
    return _iso_date(record.get("interaction_at"))


def _topic_rows(items, topic_map):
    counts = Counter(topic for item in items for topic in topic_map[item["_index"]])
    return [
        {
            "name": name,
            "count": count,
            "percent": round(count * 100 / len(items), 1) if items else 0,
            "evidence_ids": list(dict.fromkeys(item["id"] for item in items if name in topic_map[item["_index"]])),
        }
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _make_timeline(groups, topic_map, date_basis):
    return [{
        "period": period, "date_basis": date_basis, "count": len(items),
        "topics": [{"name": row["name"], "count": row["count"], "share": round(row["count"] / len(items), 4)}
                   for row in _topic_rows(items, topic_map)],
        "evidence_ids": list(dict.fromkeys(item["id"] for item in items)),
        "source_counts": dict(sorted(Counter(item["source"] for item in items).items())),
    } for period, items in sorted(groups.items())]


def _changes_from_timeline(timeline, groups, topic_map, sample_description):
    changes = []
    for previous, current in zip(timeline, timeline[1:]):
        if min(previous["count"], current["count"]) < 2:
            continue
        before = {row["name"]: row for row in previous["topics"]}
        after = {row["name"]: row for row in current["topics"]}
        for topic in sorted(set(before) | set(after)):
            first = before.get(topic, {"count": 0, "share": 0})
            last = after.get(topic, {"count": 0, "share": 0})
            delta = last["share"] - first["share"]
            if abs(delta) < 0.25 or max(first["count"], last["count"]) < 2:
                continue
            ids = list(dict.fromkeys(
                item["id"] for month in (previous["period"], current["period"])
                for item in groups[month] if topic in topic_map[item["_index"]]
            ))
            description = (
                f"De {previous['period']} a {current['period']}, «{topic}» pasó de "
                f"{first['count']}/{previous['count']} ({first['share']:.0%}) a "
                f"{last['count']}/{current['count']} ({last['share']:.0%}) "
                f"en la muestra de {sample_description}. Es una diferencia descriptiva; "
                "no prueba un cambio personal ni una causa."
            )
            previous_sources = {source: count / previous["count"] for source, count in previous["source_counts"].items()}
            current_sources = {source: count / current["count"] for source, count in current["source_counts"].items()}
            if previous_sources != current_sources:
                description += " La composición por fuente también difiere entre estos meses."
            changes.append({"period": current["period"], "topic": topic, "description": description, "evidence_ids": ids})
    return changes


def analyze_records(records: list[dict]) -> dict:
    """Summarize observable themes with a strictly verified interaction chronology."""
    records = records if isinstance(records, list) else []
    prepared, duplicates, invalid, conflicts = _prepare(records)
    total = len(prepared)
    topic_map = {}
    tone_map = {}
    interaction_groups = defaultdict(list)
    original_groups = defaultdict(list)
    own_groups = defaultdict(list)
    source_groups = defaultdict(list)
    coverage = Counter()
    music_groups = {}
    evidence = []
    unverified_dates = 0
    invalid_dates = 0
    for index, item in enumerate(prepared):
        item["_index"] = index
        verified_note = observed_repost_note(item)
        content = _normalize("\n".join([*(_text(item.get(field)) for field in TEXT_FIELDS), verified_note]))
        topic_map[index] = [name for name, terms in TOPICS.items() if _matches(content, terms)]
        tone_map[index] = [name for name, terms in TONES.items() if _matches(content, terms)]
        for field in TEXT_FIELDS:
            if _text(item.get(field)):
                coverage[field] += 1
        if content:
            coverage["any_text"] += 1
        if verified_note:
            coverage["repost_note"] += 1
        for field in ("screenshots", "frames"):
            if isinstance(item.get(field), list) and item[field]:
                coverage[field] += 1
        verified_date = _interaction_date(item)
        published_date = _iso_date(item.get("published_at"))
        if verified_date:
            interaction_groups[verified_date[:7]].append(item)
        elif item.get("interaction_at"):
            if _text(item.get("interaction_date_source")) not in VERIFIED_DATE_SOURCES:
                unverified_dates += 1
            elif not _iso_date(item.get("interaction_at")):
                invalid_dates += 1
        if published_date:
            original_groups[published_date[:7]].append(item)
            if item["source"] == "uploads":
                own_groups[published_date[:7]].append(item)
        source_groups[item["source"]].append(item)
        music = item.get("music") if isinstance(item.get("music"), dict) else {}
        title, author = _text(music.get("title")), _text(music.get("author"))
        if title:
            coverage["music"] += 1
            key = (_normalize(title), _normalize(author))
            entry = music_groups.setdefault(key, {
                "title": title, "author": author, "url": _safe_url(music.get("url")),
                "count": 0, "evidence_ids": [], "sources": [],
            })
            entry["count"] += 1
            if item["id"] not in entry["evidence_ids"]:
                entry["evidence_ids"].append(item["id"])
            if item["source"] not in entry["sources"]:
                entry["sources"].append(item["source"])
        evidence.append({
            "id": item["id"], "profile": _text(item.get("profile")), "source": item["source"],
            "url": _safe_url(item.get("url")), "caption": _text(item.get("caption")),
            "published_at": published_date, "interaction_at": verified_date,
            "interaction_date_source": item.get("interaction_date_source") if verified_date else None,
            "repost_note": _text(item.get("repost_note")),
            "repost_note_author": _text(item.get("repost_note_author")),
            "repost_note_source": _text(item.get("repost_note_source")) or None,
            "topics": topic_map[index],
            "text_excerpt": " ".join([*(_text(item.get(field)) for field in TEXT_FIELDS), verified_note]).strip()[:400],
        })

    topics = _topic_rows(prepared, topic_map)
    dated_count = sum(len(items) for items in interaction_groups.values())
    timeline = _make_timeline(interaction_groups, topic_map, "interaction")
    original_timeline = _make_timeline(original_groups, topic_map, "original_publication")
    own_timeline = _make_timeline(own_groups, topic_map, "own_publication")
    changes = _changes_from_timeline(timeline, interaction_groups, topic_map, "interacciones verificadas")
    own_changes = _changes_from_timeline(own_timeline, own_groups, topic_map, "publicaciones propias con fecha de publicación")
    own_dated_count = sum(len(items) for items in own_groups.values())
    own_undated_count = len(source_groups.get("uploads", [])) - own_dated_count

    source_comparison = [{
        "source": source, "label": SOURCE_LABELS[source], "count": len(items),
        "dated_count": sum(bool(_interaction_date(item)) for item in items),
        "undated_count": sum(not bool(_interaction_date(item)) for item in items),
        "topics": _topic_rows(items, topic_map),
    } for source, items in sorted(source_groups.items())]
    coverage_dict = {field: coverage[field] for field in (*TEXT_FIELDS, "repost_note", "any_text", "screenshots", "frames", "music")}
    metrics = {
        "input_count": len(records), "total_records": total, "duplicate_count": duplicates,
        "invalid_record_count": invalid, "dated_count": dated_count,
        "undated_count": total - dated_count,
        "original_dated_count": sum(len(items) for items in original_groups.values()),
        "own_dated_count": own_dated_count, "own_undated_count": own_undated_count,
        "source_counts": {source: len(items) for source, items in sorted(source_groups.items())},
        "coverage": coverage_dict, "without_text_count": total - coverage["any_text"],
        "unclassified_count": sum(not topic_map[item["_index"]] for item in prepared),
        "content_tone": _topic_rows(prepared, tone_map),
    }
    limitations = [
        "La muestra depende del contenido accesible y capturado; su cobertura del historial total es desconocida. La ausencia de registros no demuestra ausencia de actividad.",
        "Republicar, marcar Me gusta o publicar un video no demuestra acuerdo, autoría de sus afirmaciones ni vivencias personales. No se infieren personalidad, rupturas, diagnósticos ni atributos sensibles.",
        "Los temas y el tono se detectan con reglas léxicas en descripción, OCR, transcripción, resumen visual y notas de republicación con procedencia verificable. Negaciones, ironía, citas y contexto pueden producir errores; cada interpretación requiere revisar la evidencia.",
        "Las imágenes, capturas y el audio no se interpretan automáticamente aquí: solo se analizan sus textos o resúmenes suministrados. El título de una canción no acredita su letra ni su significado para la persona.",
        "La cronología de interacciones usa únicamente interaction_at con procedencia visible o user_verified. La publicación original, la captura, first_seen_at y los identificadores nunca sustituyen esa fecha.",
        "La cronología de publicaciones propias incluye únicamente registros uploads con fecha de publicación válida; describe el contenido publicado por la cuenta, sin inferir que sus narraciones sean autobiográficas.",
        "Las proporciones usan como denominador los registros únicos de cada grupo. Un contenido puede pertenecer a varios temas; sus porcentajes no tienen por qué sumar 100 %. Un mismo video en varias fuentes cuenta una vez en cada fuente.",
        "Las diferencias mensuales son descriptivas, sin prueba estadística ni inferencia causal; meses sin muestras y cambios en la composición por fuente limitan la comparación.",
    ]
    if total - dated_count:
        limitations.append(f"{total - dated_count} de {total} registros carecen de fecha verificable de interacción y quedan fuera de esa cronología.")
    if not dated_count:
        limitations.append("No hay evidencia suficiente para situar temporalmente las interacciones de este perfil.")
    if own_undated_count:
        limitations.append(f"{own_undated_count} publicaciones propias carecen de fecha de publicación válida y quedan fuera de su cronología.")
    if unverified_dates:
        limitations.append(f"Se descartaron {unverified_dates} fechas de interacción sin procedencia visible o verificada por el usuario.")
    if invalid_dates:
        limitations.append(f"Se descartaron {invalid_dates} fechas de interacción con formato o fecha de calendario inválidos.")
    if conflicts:
        limitations.append(f"Se detectaron {conflicts} conflictos de fecha entre capturas duplicadas; esos registros quedan sin fecha de interacción.")
    if duplicates:
        limitations.append(f"Se consolidaron {duplicates} capturas duplicadas dentro de la misma fuente y perfil.")
    if invalid:
        limitations.append(f"Se omitieron {invalid} entradas que no eran registros válidos.")
    missing_sources = [SOURCE_LABELS[source] for source in ("uploads", "reposts", "likes") if source not in source_groups]
    if missing_sources:
        limitations.append("Sin registros de: " + ", ".join(missing_sources) + ". Esto no permite saber si están vacíos, privados o pendientes de captura.")
    if len({_text(item.get("profile")) for item in prepared if _text(item.get("profile"))}) > 1:
        limitations.append("La entrada contiene varios perfiles; el resultado resume la muestra conjunta y no debe atribuirse a una sola cuenta.")
    if not total:
        summary = "No hay contenido capturado para analizar. No se puede elaborar un resumen temático ni temporal."
    elif topics:
        leading = ", ".join(f"{row['name']} ({row['count']}/{total})" for row in topics[:3])
        summary = (
            f"La muestra contiene {total} registros únicos de contenido. Los temas detectados con mayor frecuencia son: "
            f"{leading}. Hay {dated_count} registros con fecha verificable de interacción y {total - dated_count} sin ella. "
            "Estas observaciones describen el contenido de la muestra, no la personalidad ni la vida privada de su titular."
        )
    else:
        summary = (
            f"La muestra contiene {total} registros únicos, pero los textos disponibles no permiten detectar temas con las reglas actuales. "
            f"Hay {dated_count} registros con fecha verificable de interacción. Se necesita revisar o completar el contexto del contenido."
        )
    thematic_profile = [
        f"«{row['name']}» aparece en {row['count']} de {total} registros ({row['percent']:.1f} %). "
        "Es una asociación temática del contenido, pendiente de revisión contextual."
        for row in topics[:8]
    ]
    result = {
        "summary": summary, "metrics": metrics, "topics": topics, "timeline": timeline,
        "original_timeline": original_timeline, "changes": changes, "undated_count": total - dated_count,
        "own_timeline": own_timeline, "own_changes": own_changes,
        "limitations": limitations, "thematic_profile": thematic_profile,
        "music": sorted(music_groups.values(), key=lambda item: (-item["count"], item["title"])),
        "source_comparison": source_comparison, "evidence_index": evidence,
        "methodology": "Reglas léxicas locales v2; menciones con procedencia y autoría separadas; periodos temáticos por fuente y base temporal; fechas explícitas verificables; deduplicación por video, fuente y perfil; sin inferencias de atributos privados.",
    }
    result.update(enrich_analysis(prepared, result))
    return result


def executive_report(run):
    """Coverage and actionable evidence, not a personal profile."""
    records=run.get('records',[])
    analysis=run.get('analysis',{})
    collection=run.get('collection',{})
    sources=collection.get('sources',{})
    requested=collection.get('requested_sources') or collection.get('options',{}).get('sources',[])
    coverage=[]
    for source in ('uploads','reposts','likes','manual'):
        rows=[row for row in records if row.get('source')==source]
        detail=sources.get(source,{})
        if not rows and not detail and source not in requested:
            continue
        eligible=list(eligible_records(rows,run.get('collection',{}).get('options',{})))
        coverage.append({'source':source,'observed':len(rows),'observed_minimum':detail.get('observed_minimum',len(rows)),
            'analyzed':sum(bool(row.get('caption') or row.get('ocr_text') or row.get('visual_summary') or row.get('transcript')) for row in eligible),
            'visual_analyzed':sum(bool(row.get('visual_summary')) for row in eligible),
            'unknown_publication_dates':sum(not row.get('published_at') for row in rows),
            'excluded_by_date':sum(row.get('date_filter_status')=='outside_window' for row in rows),
            'unknown_date_filter':sum(row.get('date_filter_status')=='unknown_date' for row in rows),
            'status':detail.get('status','observed' if rows else 'unassessed'),
            'visibility_status':detail.get('visibility_status','unknown'),
            'reason':detail.get('reason') or ('Disponibilidad no evaluada; cero registros recopilados no demuestra ausencia de contenido.' if not rows else 'Muestra observada, sin total histórico conocido.'),
            'stop_reason':detail.get('stop_reason'), 'total':None})
    by_id={row.get('id'):row for row in records}
    themes=[{'name':topic['name'],'count':topic['count'],
             'evidence':[{'id':identifier,'url':by_id.get(identifier,{}).get('url')} for identifier in topic.get('evidence_ids',[])[:8]]}
            for topic in analysis.get('topics',[])[:5]]
    return {'summary':analysis.get('summary',''),'coverage':coverage,'themes':themes,
            'date_provenance':'Fechas originales visibles o verificadas; fechas desconocidas conservadas. Compartidos y Me gusta no tienen fecha de acción inferida.',
            'limitations':analysis.get('limitations',[]) + ['Recuento observado mínimo, no total histórico. Fotogramas acotados; no se escuchó audio del navegador.'],
            'next_steps':['Revisar los enlaces y textos de los temas principales.',
                          'Resolver manualmente las fuentes bloqueadas y repetir captura incremental.',
                          'Verificar fechas desconocidas; adjuntar audio local solo si hace falta transcripción opcional.']}


def safe_record(row):
    """Rebuild derived context only from individually attributable safe evidence."""
    result=dict(row)
    quality=row.get('frame_quality') or {}
    safe_paths={path for path in row.get('frames',[]) if safe_frame(quality.get(path,{}))}
    manual=row.get('manual_fields') or {}
    if 'ocr_text' not in manual:
        texts=[item.get('text','') for item in row.get('ocr_sequence',[])
               if item.get('frame') in safe_paths]
        upload=row.get('upload_ocr') or {}
        if upload.get('source')=='user_upload':
            texts.append(upload.get('text',''))
        result['ocr_text']='\n\n'.join(dict.fromkeys(text for text in texts if text))
    if 'visual_summary' not in manual:
        provenance=(row.get('visual_analysis') or {}).get('safe_input_paths',[])
        if not provenance or not set(provenance).issubset(safe_paths):
            result['visual_summary']=''
            result['visual_analysis']={}
    return result


def safe_frame(info):
    return info.get('status')=='verified' and (info.get('source')=='user_upload' or
        (info.get('source')=='profile_capture' and info.get('overlay_guard_version')==1) or
        (info.get('source')=='tiktok_cdn' and info.get('validation_version')==1))


def current_record(row, options):
    from app.planning import date_eligibility
    row['date_filter_status']=date_eligibility(row,options)
    return safe_record(row)


def eligible_records(records, options=None):
    """Share sanitized evidence between thematic analysis and coverage counts."""
    for row in records:
        row=current_record(row,options) if options is not None else safe_record(row)
        if row.get('date_filter_status','included')!='included':
            continue
        yield row


def _md(value):
    value = html.escape(str(value or ""), quote=False).replace("\n", " ")
    return re.sub(r"([\\`*_{\[\]}|])", r"\\\1", value)


def build_markdown_report(run: dict) -> str:
    """Export a self-contained Spanish report with linked supporting records."""
    run = run if isinstance(run, dict) else {}
    result = run.get("analysis")
    if not isinstance(result, dict):
        result = analyze_records(run.get("records", []))
    metrics = result.get("metrics", {})
    evidence = result.get("evidence_index", [])
    by_id = {str(item["id"]): item for item in evidence}

    def references(ids):
        parts = []
        for identifier in ids:
            item = by_id.get(str(identifier), {})
            label = _md(identifier)
            url = _safe_url(item.get("url"))
            parts.append(f"[{label}]({url})" if url else label)
        return ", ".join(parts) or "Sin evidencia enlazada"

    lines = ["# Análisis del contenido observado", ""]
    executive=result.get('executive') or executive_report({**run,'analysis':result})
    lines.extend(['## Resumen ejecutivo','',_md(executive['summary']),'','### Cobertura por fuente',''])
    for item in executive['coverage']:
        lines.append(f"- {_md(SOURCE_LABELS.get(item['source'],item['source']))}: {item['observed']} observados, {item['analyzed']} con texto analizable, {item['visual_analyzed']} interpretados visualmente; estado {_md(item['status'])}; total histórico desconocido.")
    lines.extend(['',_md(executive['date_provenance']),'','### Próximos pasos',''])
    lines.extend('- '+_md(step) for step in executive['next_steps'])
    lines.append('')
    collection = run.get("collection") if isinstance(run.get("collection"), dict) else {}
    if run.get("is_demo") or collection.get("method") == "synthetic_demo":
        lines.extend(["**EJEMPLO FICTICIO. Datos inventados para demostrar la aplicación; no corresponden a un perfil real.**", ""])
    profile = run.get("profile") or run.get("profile_url")
    if profile:
        lines.extend([f"Perfil consultado: {_md(profile)}", ""])
    if collection:
        lines.extend(["## Procedencia de la captura", ""])
        if collection.get("method"):
            lines.append(f"Método registrado: {_md(collection['method'])}.")
        if run.get("created_at"):
            lines.append(f"Informe iniciado: {_md(run['created_at'])}. Esta fecha no indica cuándo ocurrieron las interacciones.")
        if run.get("status"):
            lines.append(f"Estado de la ejecución: {_md(run['status'])}.")
        source_states = collection.get("sources") if isinstance(collection.get("sources"), dict) else {}
        for source, state in source_states.items():
            if isinstance(state, dict):
                lines.append(f"- {_md(SOURCE_LABELS.get(source, source))}: {_md(state.get('status', 'sin estado'))}. {_md(state.get('reason', ''))}")
        lines.append("")
    lines.extend([_md(result.get("summary", "")), "", "## Cobertura de la muestra", "",
        f"- Registros únicos: {metrics.get('total_records', 0)}.",
        f"- Capturas duplicadas consolidadas: {metrics.get('duplicate_count', 0)}.",
        f"- Interacciones con fecha verificable: {metrics.get('dated_count', 0)}.",
        f"- Registros sin fecha verificable de interacción: {result.get('undated_count', 0)}.",
        f"- Registros sin texto analizable: {metrics.get('without_text_count', 0)}.",
    ])
    for field, count in metrics.get("coverage", {}).items():
        lines.append(f"- Campo `{field}` disponible en {count} registros.")
    profile_summary = result.get("profile_summary", {})
    if profile_summary:
        lines.extend(["", "## Resumen del contenido del perfil", "", _md(profile_summary.get("text", "")), ""])
        for observation in profile_summary.get("observations", []):
            lines.append(f"- {_md(observation['text'])} Evidencia: {references(observation['evidence_ids'])}.")
        for unknown in profile_summary.get("unknowns", []):
            lines.append(f"- Límite: {_md(unknown)}")
    quality = result.get("data_quality", {})
    if quality:
        lines.extend(["", "## Disponibilidad y contexto pendiente", "", _md(quality.get("summary", "")), ""])
        for issue in quality.get("issues", []):
            lines.append(f"- {_md(issue['message'])} ({issue['count']} registros; procedencia: {_md(issue['basis'])}). Evidencia: {references(issue['evidence_ids'])}.")
    lines.extend(["", "## Temas del contenido", ""])
    if result.get("topics"):
        lines.extend(["| Tema | Registros | Proporción | Evidencia |", "| --- | ---: | ---: | --- |"])
        for topic in result["topics"]:
            lines.append(f"| {_md(topic['name'])} | {topic['count']} | {topic['percent']} % | {references(topic['evidence_ids'])} |")
    else:
        lines.append("Sin temas identificados en los textos disponibles.")
    lines.extend(["", "## Periodos temáticos observados", "",
                  "Se agrupan meses consecutivos con contenido del mismo tema y al menos dos registros. Publicaciones propias, republicaciones y Me gusta se analizan por separado; los intervalos no acreditan vivencias personales.", ""])
    for period in result.get("thematic_periods", []):
        lines.append(f"- {_md(period['description'])} Evidencia: {references(period['evidence_ids'])}.")
    if not result.get("thematic_periods"):
        lines.append("No hay suficientes registros fechados de un mismo tema para delimitar un periodo. Las fechas de publicación original de contenidos republicados no sustituyen las de interacción.")
    lines.append("Los meses sin muestra no demuestran ausencia de actividad; cada periodo describe únicamente los registros capturados.")
    lines.extend(["", "## Menciones y procedencia", "", _md(result.get("observation_summary", {}).get("caveat", "")), ""])
    for mention in result.get("mentions", []):
        own = mention.get("owner_authored_count", 0)
        self_label = " · incluye autorreferencias" if mention.get("is_self") else ""
        lines.append(f"- **{_md(mention['handle'])}**: {mention['count']} registros; {own} con mención atribuible al perfil{self_label}. Evidencia: {references(mention['evidence_ids'])}.")
        for context in mention.get("contexts", []):
            origin = "nota de republicación" if context["origin"] == "repost_note" else "descripción"
            attribution = {"profile": "perfil consultado", "original_author": "autor original", "unverified": "autoría sin verificar"}.get(context["attribution"], "autoría sin verificar")
            actor = f" ({_md(context['actor'])})" if context.get("actor") else ""
            lines.append(f"  - {origin} · {attribution}{actor}: «{_md(context['excerpt'])}». Evidencia: {references([context['record_id']])}.")
    if not result.get("mentions"):
        lines.append("No se encontraron menciones explícitas en las descripciones ni en notas de republicación registradas.")
    lines.extend(["", "## Comparación por fuente", ""])
    for source in result.get("source_comparison", []):
        lines.extend([f"### {_md(source['label'])}", "",
                      f"{source['count']} registros; {source['dated_count']} con fecha verificable de interacción.", ""])
        for topic in source["topics"][:6]:
            lines.append(f"- {_md(topic['name'])}: {topic['count']} ({topic['percent']} %). Evidencia: {references(topic['evidence_ids'])}.")
        if not source["topics"]:
            lines.append("Sin temas detectados para esta fuente.")
        lines.append("")
    lines.extend(["## Cronología de interacciones verificadas", "",
                  "Solo incluye fechas de interacción visibles o verificadas manualmente; no usa fechas de publicación ni de captura.", ""])
    if not result.get("timeline"):
        lines.append("No hay interacciones con fecha verificable. No se puede reconstruir cuándo se compartió o marcó Me gusta al contenido.")
    for month in result.get("timeline", []):
        topics = "; ".join(f"{_md(topic['name'])}: {topic['count']} ({topic['share']:.0%})" for topic in month["topics"])
        lines.append(f"- **{month['period']}**: {month['count']} registros. {topics or 'Sin temas detectados'}. Evidencia: {references(month['evidence_ids'])}.")
    lines.extend(["", "## Diferencias entre meses observados", ""])
    for change in result.get("changes", []):
        lines.append(f"- {_md(change['description'])} Evidencia: {references(change['evidence_ids'])}.")
    if not result.get("changes"):
        lines.append("No se detectaron diferencias que cumplan los umbrales descriptivos, o faltan suficientes registros fechados. Esto no demuestra estabilidad personal.")
    if result.get("own_timeline"):
        lines.extend(["", "## Cronología de publicaciones propias", "",
                      "Incluye únicamente publicaciones propias con fecha de publicación válida. Los temas describen el contenido publicado, sin suponer vivencias autobiográficas ni fechas de interacción.", ""])
        for month in result["own_timeline"]:
            topics = "; ".join(f"{_md(topic['name'])}: {topic['count']} ({topic['share']:.0%})" for topic in month["topics"])
            lines.append(f"- **{month['period']}**: {month['count']} publicaciones propias. {topics or 'Sin temas detectados'}. Evidencia: {references(month['evidence_ids'])}.")
        lines.extend(["", "### Diferencias en publicaciones propias", ""])
        for change in result.get("own_changes", []):
            lines.append(f"- {_md(change['description'])} Evidencia: {references(change['evidence_ids'])}.")
        if not result.get("own_changes"):
            lines.append("No se detectaron diferencias que cumplan los umbrales descriptivos, o faltan suficientes publicaciones fechadas. Esto no demuestra estabilidad personal.")
    lines.extend(["", "## Fechas de publicación original", "",
                  "Esta cronología sitúa la publicación de los videos originales. Para republicaciones y Me gusta no indica cuándo interactuó el perfil consultado.", ""])
    for month in result.get("original_timeline", []):
        lines.append(f"- **{month['period']}**: {month['count']} registros. Evidencia: {references(month['evidence_ids'])}.")
    if not result.get("original_timeline"):
        lines.append("No se capturaron fechas de publicación original válidas.")
    lines.extend(["", "## Música registrada", ""])
    for music in result.get("music", []):
        author = f" — {_md(music['author'])}" if music.get("author") else ""
        lines.append(f"- {_md(music['title'])}{author}: {music['count']} registros. Evidencia: {references(music['evidence_ids'])}.")
    if not result.get("music"):
        lines.append("No hay metadatos de música disponibles.")
    lines.extend(["", "## Tono explícito del contenido", "",
                  "Estas etiquetas describen expresiones textuales y requieren revisar negaciones, citas e ironía. No representan el estado emocional del titular.", ""])
    for tone in metrics.get("content_tone", []):
        lines.append(f"- {_md(tone['name'])}: {tone['count']} registros. Evidencia: {references(tone['evidence_ids'])}.")
    if not metrics.get("content_tone"):
        lines.append("Sin expresiones reconocidas por las reglas actuales.")
    lines.extend(["", "## Registro de evidencia", ""])
    for item in evidence:
        lines.append(f"- {references([item['id']])} · {_md(SOURCE_LABELS.get(item['source'], item['source']))} · "
                     f"interacción: {_md(item.get('interaction_at') or 'sin fecha verificable')} · "
                     f"publicación original: {_md(item.get('published_at') or 'sin fecha')}.")
        if item.get("text_excerpt"):
            lines.append(f"  Texto/contexto capturado: {_md(item['text_excerpt'])}")
    if not evidence:
        lines.append("No hay registros de evidencia.")
    lines.extend(["", "## Método y límites", "", _md(result.get("methodology", "")), ""])
    lines.extend(f"- {_md(item)}" for item in result.get("limitations", []))
    lines.append("")
    return "\n".join(lines)
