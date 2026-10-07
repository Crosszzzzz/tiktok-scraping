"""Validated, bounded capture planning; no network or model operations."""
from datetime import date, timedelta

DEPTHS = {'quick': (5, 3), 'deep': (60, 25), 'all': (200, 50)}


def capture_options(body):
    depth = body.depth
    items, rounds = DEPTHS.get(depth, (body.max_items, body.scroll_rounds))
    sources = list(dict.fromkeys(body.sources))
    if body.date_filter == 'last30days' and sources != ['uploads']:
        raise ValueError('Últimos 30 días solo admite publicaciones propias; las fechas de Me gusta y compartidos no están disponibles.')
    return {'depth': depth, 'max_items': items, 'scroll_rounds': rounds,
            'isolate_history': bool(getattr(body, 'isolate_history', False)),
            'sources': sources, 'date_filter': body.date_filter,
            'session_mode': body.session_mode,
            'cutoff': (date.today() - timedelta(days=30)).isoformat() if body.date_filter == 'last30days' else None,
            'end_date': date.today().isoformat(),
            'duration_minutes': [max(1, round(len(sources) * items * 8 / 60)),
                                 max(2, round(len(sources) * items * 45 / 60 + rounds))],
            'duration_basis': 'Heurística de 8–45 segundos por detalle más navegación; excluye esperas manuales y no garantiza duración.',
            'coverage': 'Muestra acotada; total histórico desconocido. All accesible tiene límite de 200 trabajos y 50 desplazamientos por fuente; continuar con otra captura incremental.'}


def date_eligibility(row, options):
    if options.get('date_filter') != 'last30days':
        return 'included'
    value = row.get('published_at')
    if row.get('source') != 'uploads' or row.get('published_date_source') not in {'visible', 'user_verified', 'tiktok_web_response'} or not value:
        return 'unknown_date'
    try:
        published = date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return 'unknown_date'
    return 'included' if date.fromisoformat(options['cutoff']) <= published <= date.fromisoformat(options['end_date']) else 'outside_window'
