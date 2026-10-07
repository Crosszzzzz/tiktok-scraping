"""Build a sanitized, self-contained static report for public hosting.

The capture itself runs locally (Chrome + persistent profile + Gemini).
Only the resulting summary is exported so it can be served as a static site
(for example through Vercel). Nothing here exports cookies, the browser
profile, absolute filesystem paths, API keys or internal run identifiers.
"""
from __future__ import annotations

import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import store

PUBLISH_SUBDIR = "publish"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_FRAMES_PER_RECORD = 4

SOURCE_LABELS = {
    "uploads": "Publicaciones propias",
    "reposts": "Compartidos",
    "likes": "Me gusta",
    "manual": "Importado",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value, limit=20000) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _clean_url(value) -> str:
    """Keep only the public HTTPS TikTok post URL, without query string."""
    try:
        parsed = urlparse(str(value))
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith("tiktok.com"):
        return ""
    return f"https://{parsed.hostname}{parsed.path}"


def _copy_frames(row: dict, media_dir: Path) -> list[str]:
    quality = row.get("frame_quality") or {}
    copied: list[str] = []
    for relative in list(row.get("frames") or [])[:MAX_FRAMES_PER_RECORD]:
        if quality and (quality.get(relative) or {}).get("status") != "verified":
            continue
        source = store.media_path(relative)
        if not source.is_file() or source.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        destination = media_dir / f"{store.safe_id(row.get('id', 'rec'))}-{len(copied)}{source.suffix.lower()}"
        try:
            shutil.copyfile(source, destination)
        except OSError:
            continue
        copied.append("media/" + destination.name)
    return copied


def _public_record(row: dict, media_dir: Path) -> dict:
    music = row.get("music") if isinstance(row.get("music"), dict) else {}
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    public_metrics = {}
    for name, value in metrics.items():
        if isinstance(value, dict) and _text(value.get("raw")):
            public_metrics[name] = _text(value.get("raw"), 60)
    return {
        "id": str(row.get("id", "")),
        "source": row.get("source", "manual"),
        "url": _clean_url(row.get("url")),
        "caption": _text(row.get("caption"), 4000),
        "author": _text(row.get("author"), 300),
        "published_at": _text(row.get("published_at"), 40) or None,
        "interaction_at": _text(row.get("interaction_at"), 40) or None,
        "hashtags": [_text(tag, 120) for tag in list(row.get("hashtags") or [])[:40]],
        "music": {"title": _text(music.get("title"), 500), "author": _text(music.get("author"), 300)},
        "metrics": public_metrics,
        "ocr_text": _text(row.get("ocr_text"), 20000),
        "visual_summary": _text(row.get("visual_summary"), 6000),
        "frames": _copy_frames(row, media_dir),
    }


def build_bundle(profile: str, run: dict) -> Path:
    """Create data/publish/<profile>/ with index.html, report.json and media/."""
    from .analysis import analyze_records

    name = store.profile_name(profile)
    analysis = analyze_records(run.get("records") or [])
    target = store.DATA / PUBLISH_SUBDIR / name
    if target.exists():
        shutil.rmtree(target)
    media_dir = target / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    records = [_public_record(row, media_dir) for row in run.get("records") or []]
    report = {
        "profile": name,
        "generated_at": _now(),
        "source_counts": (analysis.get("metrics") or {}).get("source_counts", {}),
        "record_count": len(records),
        "summary": (analysis.get("profile_summary") or {}).get("text", "") or _text(analysis.get("summary")),
        "observations": [
            {"text": _text(item.get("text"), 2000), "evidence_ids": list(item.get("evidence_ids") or [])}
            for item in (analysis.get("profile_summary") or {}).get("observations", [])
        ],
        "metrics": analysis.get("metrics", {}),
        "topics": [
            {"name": _text(topic.get("name"), 200), "count": topic.get("count", 0), "percent": topic.get("percent", 0)}
            for topic in (analysis.get("topics") or [])
        ],
        "source_comparison": analysis.get("source_comparison", []),
        "thematic_periods": analysis.get("thematic_periods", []),
        "music": analysis.get("music", []),
        "mentions": analysis.get("mentions", []),
        "data_quality": analysis.get("data_quality", {}),
        "limitations": analysis.get("limitations", []),
        "records": records,
    }
    (target / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "index.html").write_text(_page(report), encoding="utf-8")
    return target


def _api_token() -> str:
    import os

    candidates = [
        Path(os.environ.get("APPDATA", "")) / "com.vercel.cli" / "Data" / "auth.json",
        Path(os.environ.get("LOCALAPPDATA", "")) / "com.vercel.cli" / "Data" / "auth.json",
        Path.home() / ".vercel" / "auth.json",
    ]
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        token = data.get("token") if isinstance(data, dict) else None
        if isinstance(token, str) and token:
            return token
    return ""


def _make_deployment_public(project_name: str) -> bool:
    """Disable Vercel Authentication so the deployed report is publicly readable."""
    from urllib import error, request

    token = _api_token()
    if not token or not project_name:
        return False
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}

    def call(url, method="GET", body=None):
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        req = request.Request(url, data=payload, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=30) as response:
                raw = response.read()
            return json.loads(raw) if raw else {}
        except (error.HTTPError, error.URLError, TimeoutError, ValueError):
            return None

    teams = call("https://api.vercel.com/v2/teams") or {}
    team_ids = [None] + [team.get("id") for team in teams.get("teams", []) if team.get("id")]
    for team_id in team_ids:
        query = f"?teamId={team_id}" if team_id else ""
        project = call(f"https://api.vercel.com/v9/projects/{project_name}{query}")
        if isinstance(project, dict) and project.get("id"):
            patched = call(
                f"https://api.vercel.com/v9/projects/{project['id']}{query}",
                "PATCH",
                {"ssoProtection": None, "passwordProtection": None},
            )
            return bool(patched is not None)
    return False


def deploy_bundle(directory: Path, timeout: int = 600) -> str:
    """Deploy the built bundle to Vercel and return the public HTTPS URL.

    Uses the locally authenticated Vercel CLI. The static report is the only
    thing uploaded; no session, cookies or source code leave this machine.
    """
    import re
    import subprocess

    command = f'npx --yes vercel@latest deploy "{directory}" --prod --yes'
    try:
        completed = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("No se pudo ejecutar Vercel CLI. Instala Node y verifica la sesión.") from exc
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    urls = re.findall(r"https://[A-Za-z0-9.-]+\.vercel\.app\b", output)
    if completed.returncode != 0 or not urls:
        raise RuntimeError("La publicación en Vercel falló; revisa la sesión con «npx vercel whoami».")
    try:
        _make_deployment_public(directory.name)
    except Exception:
        pass
    return urls[-1]


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _pct(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, number))


def _metric_cards(report: dict) -> str:
    metrics = report.get("metrics") or {}
    total = metrics.get("total_records", report.get("record_count", 0))
    topics = len(report.get("topics") or [])
    dated = metrics.get("dated_count", 0)
    cards = [
        ("Evidencias", total),
        ("Temas detectados", topics),
        ("Interacciones con fecha", dated),
    ]
    return "".join(
        f'<article class="metric"><span>{_esc(label)}</span><strong>{_esc(value)}</strong></article>'
        for label, value in cards
    )


def _topics(report: dict) -> str:
    topics = report.get("topics") or []
    if not topics:
        return '<p class="muted">No hay suficiente texto o contexto para identificar temas.</p>'
    rows = []
    for topic in topics[:12]:
        percent = _pct(topic.get("percent"))
        rows.append(
            '<div class="topic"><div class="topic-top"><span>'
            + _esc(topic.get("name"))
            + f'</span><span>{_esc(topic.get("count"))} · {round(percent)}%</span></div>'
            + f'<div class="bar"><i style="width:{percent:.1f}%"></i></div></div>'
        )
    return "".join(rows)


def _list(items, empty: str, render) -> str:
    if not items:
        return f'<p class="muted">{_esc(empty)}</p>'
    return "".join(render(item) for item in items)


def _periods(report: dict) -> str:
    def render(entry):
        start, end = _esc(entry.get("start")), _esc(entry.get("end"))
        span = start if start == end else f"{start} — {end}"
        return (
            '<article class="period"><h4>'
            + _esc(entry.get("topic"))
            + f'</h4><p class="muted">{span} · {_esc(entry.get("count"))} de {_esc(entry.get("total_in_period"))}</p><p>'
            + _esc(entry.get("description"))
            + "</p></article>"
        )

    return _list(report.get("thematic_periods"), "Sin períodos temáticos con fechas documentadas.", render)


def _music(report: dict) -> str:
    def render(item):
        return (
            '<li><span class="music">♫</span> '
            + _esc(item.get("title") or item.get("name"))
            + f' <span class="muted">· {_esc(item.get("author") or "autor no identificado")}{" · " + _esc(item.get("count")) + "×" if item.get("count") else ""}</span></li>'
        )

    return _list(report.get("music"), "Sin referencias musicales.", render)


def _mentions(report: dict) -> str:
    def render(item):
        return (
            '<li>@'
            + _esc(item.get("handle"))
            + f' <span class="muted">· {_esc(item.get("count"))} evidencias</span></li>'
        )

    return _list(report.get("mentions"), "Sin menciones documentadas.", render)


def _evidence(report: dict) -> str:
    cards = []
    for record in report.get("records") or []:
        frames = record.get("frames") or []
        images = "".join(
            f'<img src="{_esc(path)}" alt="Evidencia" loading="lazy">' for path in frames
        ) or '<div class="noimg">Sin captura verificada</div>'
        badges = []
        if record.get("ocr_text"):
            badges.append("OCR")
        if record.get("visual_summary"):
            badges.append("Visual IA")
        if record.get("music", {}).get("title"):
            badges.append("Música")
        link = (
            f'<a href="{_esc(record["url"])}" target="_blank" rel="noopener noreferrer nofollow">Ver en TikTok ↗</a>'
            if record.get("url")
            else ""
        )
        title = record.get("caption") or record.get("ocr_text") or record.get("visual_summary") or "Evidencia sin descripción"
        visual = (
            f'<p class="visual"><strong>Interpretación visual:</strong> {_esc(record["visual_summary"])}</p>'
            if record.get("visual_summary")
            else ""
        )
        cards.append(
            '<article class="ev">'
            + f'<div class="ev-media">{images}</div>'
            + '<div class="ev-body"><div class="ev-badges">'
            + "".join(f"<span>{_esc(badge)}</span>" for badge in badges)
            + f'</div><h3>{_esc(title[:220])}</h3>'
            + f'<p class="muted">{_esc(SOURCE_LABELS.get(record.get("source"), "Evidencia"))}'
            + (f' · publicado {_esc(record["published_at"])}' if record.get("published_at") else "")
            + "</p>"
            + visual
            + link
            + "</div></article>"
        )
    if not cards:
        return '<p class="muted">Esta exploración no contiene evidencias.</p>'
    return "".join(cards)


def _page(report: dict) -> str:
    profile = _esc(report.get("profile"))
    limitations = _list(report.get("limitations"), "Sin limitaciones registradas.", lambda item: f"<li>{_esc(item)}</li>")
    observations = _list(
        report.get("observations"),
        "Sin observaciones.",
        lambda item: f"<li>{_esc(item.get('text'))}</li>",
    )
    quality = report.get("data_quality") or {}
    quality_summary = _text(quality.get("summary"), 2000) if isinstance(quality, dict) else ""
    generated = _esc(report.get("generated_at"))
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Informe de @{profile} · Huella</title>
<style>
:root{{--bg:#0f1313;--card:#171d1d;--line:#25302f;--ink:#e9f1ef;--muted:#8fa3a0;--accent:#38d39f}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:32px 20px 80px}}
header.top{{border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:26px}}
.brand{{color:var(--accent);font-weight:700;letter-spacing:.08em;text-transform:uppercase;font-size:13px}}
h1{{margin:.2em 0 .1em;font-size:30px}}
h2{{margin:34px 0 12px;font-size:20px}}
h3{{margin:.2em 0;font-size:17px}}
h4{{margin:.2em 0;font-size:15px}}
.muted{{color:var(--muted)}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}
.metric{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}}
.metric span{{display:block;color:var(--muted);font-size:13px}}
.metric strong{{font-size:26px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-top:14px}}
.summary{{font-size:17px}}
.topic{{margin:10px 0}}
.topic-top{{display:flex;justify-content:space-between;font-size:14px;margin-bottom:5px}}
.bar{{height:8px;background:#0c1110;border-radius:6px;overflow:hidden}}
.bar i{{display:block;height:100%;background:var(--accent)}}
.period{{border-left:2px solid var(--accent);padding:4px 0 4px 12px;margin:10px 0}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
ul{{margin:.4em 0;padding-left:20px}}
li{{margin:.25em 0}}
.ev{{display:grid;grid-template-columns:220px 1fr;gap:16px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:12px 0}}
.ev-media{{display:flex;gap:6px;flex-wrap:wrap}}
.ev-media img{{width:104px;height:104px;object-fit:cover;border-radius:8px;background:#0c1110}}
.noimg{{width:100%;min-height:104px;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:13px;border:1px dashed var(--line);border-radius:8px}}
.ev-badges{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px}}
.ev-badges span{{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:2px 8px;color:var(--muted)}}
.visual{{font-size:14px;background:#0c1110;border-radius:8px;padding:8px 10px}}
a{{color:var(--accent)}}
.foot{{margin-top:40px;border-top:1px solid var(--line);padding-top:14px;color:var(--muted);font-size:13px}}
@media(max-width:640px){{.ev{{grid-template-columns:1fr}}.grid2{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="wrap">
<header class="top">
  <div class="brand">Huella · Observatorio de contenido</div>
  <h1>Informe de @{profile}</h1>
  <p class="muted">Generado {generated} · {_esc(report.get("record_count"))} evidencias observadas</p>
</header>
<section>
  <div class="metrics">{_metric_cards(report)}</div>
</section>
<section>
  <h2>Resumen</h2>
  <div class="card summary">{_esc(report.get("summary") or "Sin resumen disponible.")}</div>
  <div class="card"><h3>Observaciones</h3><ul>{observations}</ul></div>
</section>
<section>
  <h2>Mapa de temas</h2>
  <div class="card">{_topics(report)}</div>
</section>
<div class="grid2">
  <section><h2>Períodos temáticos</h2><div class="card">{_periods(report)}</div></section>
  <section><h2>Música</h2><div class="card"><ul>{_music(report)}</ul>
    <h3>Menciones</h3><ul>{_mentions(report)}</ul></div></section>
</div>
{f'<section><h2>Cobertura</h2><div class="card"><p>{_esc(quality_summary)}</p></div></section>' if quality_summary else ''}
<section>
  <h2>Evidencias</h2>
  {_evidence(report)}
</section>
<section>
  <h2>Alcance y limitaciones</h2>
  <div class="card"><ul>{limitations}</ul></div>
</section>
<p class="foot">Informe descriptivo de contenido público observado. No constituye un diagnóstico, perfil psicológico ni prueba de hechos de la vida privada. Las ausencias no demuestran inexistencia.</p>
</div>
</body>
</html>
"""
