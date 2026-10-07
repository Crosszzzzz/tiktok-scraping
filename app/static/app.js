(() => {
  'use strict';
  // The historical dashboard is inert in the guided UI; never autoload its data.
  if (!document.querySelector('.sidebar')) return;
  const $ = (id) => document.getElementById(id);
  const sourceNames = { uploads: 'Publicación', reposts: 'Compartido', likes: 'Me gusta', manual: 'Importado' };
  const state = { run: null, app: null, job: null, timeline: 'interaction', page: 1, filterIds: null, filterLabel: '', polling: false, loading: false, jobProfile: null, formProfile: null, selectionVersion: 0, ai: null };
  const perPage = 12;
  let toastTimer;

  function el(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  }
  function append(parent, ...children) { children.filter(Boolean).forEach((child) => parent.append(child)); return parent; }
  function list(value) { return Array.isArray(value) ? value : []; }
  function number(value, fallback = 0) { const result = Number(value); return Number.isFinite(result) ? result : fallback; }
  function readable(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return value.map(readable).filter(Boolean).join(' · ');
    if (typeof value === 'object') return value.description || value.summary || value.text || value.name || JSON.stringify(value);
    return String(value);
  }
  function short(value, length = 150) { const text = readable(value); return text.length > length ? text.slice(0, length - 1) + '…' : text; }
  function date(value, withTime = false) {
    if (!value) return 'No disponible';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat('es-BO', { day: 'numeric', month: 'short', year: 'numeric', ...(/^\d{4}-\d{2}-\d{2}$/.test(String(value)) ? { timeZone: 'UTC' } : {}), ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}) }).format(parsed);
  }
  function period(value) {
    if (/^\d{4}-\d{2}$/.test(value || '')) {
      return new Intl.DateTimeFormat('es-BO', { month: 'short', year: '2-digit', timeZone: 'UTC' }).format(new Date(value + '-01T12:00:00Z'));
    }
    return String(value || 'Sin período');
  }
  function handle(profile) { return String(profile || '').trim().replace(/^(?:https?:\/\/)?(?:www\.)?tiktok\.com\//i, '').replace(/[?#].*$/, '').replace(/\/$/, '').replace(/^@/, ''); }
  function safeUrl(value) {
    try { const url = new URL(String(value)); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; }
  }
  function mediaUrl(value) {
    if (typeof value !== 'string' || !value) return null;
    if (/^https?:\/\//i.test(value)) return safeUrl(value);
    const path = value.replace(/\\/g, '/').replace(/^\/+/, '').replace(/^data\//, '');
    if (!path || path.split('/').includes('..') || /^[a-z]:/i.test(path) || path.includes('\0')) return null;
    return '/data/' + path.split('/').map(encodeURIComponent).join('/');
  }
  function verifiedFrames(record) { return list(record.frames).filter((path) => { const info = record.frame_quality?.[path]; return info?.status === 'verified' && (info.source !== 'profile_capture' || info.overlay_guard_version === 1); }); }
  function diagnosticImages(record) { return list(record.diagnostic_screenshots).filter((path) => record.diagnostic_quality?.[path]?.overlay_guard_version === 1); }
  function captureDiagnostic(info, label = 'captura') {
    if (!info || typeof info !== 'object') return null;
    const path = info.overlay_guard_version === 1 ? mediaUrl(info.diagnostic_screenshot || info.screenshot || info.path) : null;
    if (!path && !info.stage && !info.session_signal) return null;
    const details = el('details', 'capture-diagnostic');
    append(details, el('summary', '', 'Diagnóstico de ' + label), el('p', 'muted', 'Referencia técnica de este intento de captura; no se incluye en las evidencias del perfil.'));
    const stageNames = { profile_navigation: 'Abrir el perfil', source_activation: 'Abrir la sección', source_selection: 'Seleccionar la sección', source_capture: 'Capturar la sección', grid_scan: 'Recorrer la cuadrícula', navigation: 'Navegación', profile: 'Abrir el perfil', login: 'Acceso a la sesión' };
    if (info.stage) details.append(el('p', 'muted', 'Etapa: ' + (stageNames[info.stage] || readable(info.stage))));
    if (info.captcha_visible === true) details.append(el('p', 'muted', 'Se observó una verificación CAPTCHA en la página.'));
    if (info.session_signal) details.append(el('p', 'muted', info.session_signal === 'login_prompt_visible' ? 'Se observó una solicitud de inicio de sesión.' : 'No se pudo determinar el estado de la sesión.'));
    if (path) {
      const link = el('a', 'text-button', 'Abrir captura de diagnóstico ↗'); link.href = path; link.target = '_blank'; link.rel = 'noopener noreferrer'; details.append(link);
      const image = el('img', 'capture-diagnostic-image'); image.src = path; image.alt = 'Estado del navegador durante el intento de ' + label; image.loading = 'lazy'; image.referrerPolicy = 'no-referrer'; details.append(image);
    }
    return details;
  }
  function externalLink(url, text, className) {
    const href = safeUrl(url);
    if (!href) return null;
    const link = el('a', className, text);
    link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer';
    return link;
  }
  async function api(path, options = {}) {
    const request = { ...options };
    if (request.body && !(request.body instanceof FormData)) {
      request.headers = { 'Content-Type': 'application/json', ...(request.headers || {}) };
      request.body = JSON.stringify(request.body);
    }
    const response = await fetch(path, request);
    const contentType = response.headers.get('content-type') || '';
    const payload = contentType.includes('application/json') ? await response.json() : await response.text();
    if (!response.ok) throw new Error(readable(payload?.detail || payload?.error || payload?.message || payload) || `Error ${response.status}`);
    return payload;
  }
  function notice(message, error = false) {
    $('notice').hidden = !message;
    $('notice').classList.toggle('error', error);
    $('notice').textContent = message;
  }
  function toast(message) { clearTimeout(toastTimer); $('toast').textContent = message; $('toast').hidden = false; toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4200); }
  function showPublishResult(url, error) {
    const target = $('publish-result'); target.replaceChildren(); target.hidden = false; target.classList.toggle('error', Boolean(error));
    if (url) { target.append(document.createTextNode('Informe público listo: ')); const link = el('a', '', url); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; target.append(link); }
    else target.textContent = error || '';
  }
  async function busy(button, work) { const wasDisabled = button.disabled; button.disabled = true; try { return await work(); } catch (error) { notice(error.message, true); } finally { button.disabled = wasDisabled; } }
  function profileEndpoint(profile, suffix = '') { return '/api/profiles/' + encodeURIComponent(handle(profile)) + suffix; }
  function runEndpoint(suffix = '') { return state.run.aggregate ? profileEndpoint(state.run.profile, suffix || '/report') : '/api/runs/' + encodeURIComponent(state.run.id) + suffix; }
  function recordEndpoint(record, suffix = '') { return '/api/runs/' + encodeURIComponent(record.origin_run_id || state.run.id) + '/records/' + encodeURIComponent(record.origin_record_id || record.id) + suffix; }
  function isActive(job) { return job && ['pending', 'running', 'queued', 'processing'].includes(job.status); }

  async function refreshState({ quiet = false } = {}) {
    try {
      const data = await api('/api/state'); state.app = data;
      if (data.ai) state.ai = data.ai;
      $('connection-label').textContent = 'Servidor local conectado'; $('connection-dot').classList.remove('offline');
      const opened = Boolean(data.browser?.open), headless = Boolean(data.browser?.headless);
      $('browser-badge').textContent = opened ? (headless ? 'Sin ventana' : 'Sesión abierta') : 'Navegador cerrado'; $('browser-badge').classList.toggle('good', opened);
      $('session-button').textContent = opened && !headless ? '↗ Ver sesión' : '↗ Abrir sesión'; $('close-session').hidden = !opened;
      $('session-hint').textContent = `Modo ${data.browser?.session_mode === 'logged_in' ? 'sesión dedicada' : 'anónimo'}. No necesitas iniciar sesión para intentar contenido público. Si TikTok exige acceso o verificación, resuélvelo manualmente. Las superposiciones nunca son evidencia.`;
      renderProfiles(data.profiles); renderRuns(data.runs);
      const capabilities = $('capabilities'); capabilities.replaceChildren();
      [['ocr', 'OCR'], ['opencv', 'OpenCV'], ['whisper', 'Audio opcional']].forEach(([key, name]) => {
        const item = el('span', 'capability' + (data.capabilities?.[key] ? ' available' : ''), name);
        item.title = data.capabilities?.[key] ? `${name} disponible` : `${name} no disponible; consulta la configuración local`;
        capabilities.append(item);
      });
      const currentJob = list(data.jobs).find(isActive);
      $('session-button').disabled = Boolean(currentJob); $('close-session').disabled = Boolean(currentJob); $('login-button').disabled = Boolean(currentJob); $('preflight-button').disabled = Boolean(currentJob);
      if (currentJob && !state.polling) startPolling(currentJob);
      return data;
    } catch (error) {
      $('connection-label').textContent = 'Sin conexión al servidor'; $('connection-dot').classList.add('offline');
      if (!quiet) notice('No se pudo conectar con el servidor local. Comprueba que la aplicación esté ejecutándose. ' + error.message, true);
      return null;
    }
  }
  function renderProfiles(profiles) {
    const target = $('profiles-list'); target.replaceChildren();
    if (!list(profiles).length) return target.append(el('p', 'sidebar-empty', 'Guarda un perfil para continuar después.'));
    list(profiles).forEach((profile) => {
      const selected = state.run?.aggregate && handle(state.run.profile).toLowerCase() === handle(profile.profile).toLowerCase();
      const button = el('button', 'run-item profile-item' + (selected ? ' selected' : '')); button.type = 'button';
      button.title = '@' + handle(profile.profile);
      if (mediaUrl(profile.avatar_path || profile.avatar_url)) { const avatar = el('img', 'profile-avatar'); avatar.src = mediaUrl(profile.avatar_path || profile.avatar_url); avatar.alt = ''; avatar.loading = 'lazy'; avatar.referrerPolicy = 'no-referrer'; button.append(avatar); }
      append(button, el('strong', '', profile.label || '@' + handle(profile.profile)));
      if (profile.label && profile.label !== '@' + handle(profile.profile)) button.append(el('small', 'profile-handle', '@' + handle(profile.profile)));
      button.append(el('small', '', `${number(profile.record_count ?? profile.count)} evidencias · ${profile.last_run_at ? date(profile.last_run_at) : 'Sin capturas'}`));
      if (list(profile.tags).length) button.append(el('small', 'profile-tag-line', list(profile.tags).join(' · ')));
      button.addEventListener('click', () => busy(button, () => loadProfile(profile.profile)));
      target.append(button);
    });
  }
  function updateCaptureMode() {
    const refresh = $('capture-mode').value === 'refresh';
    $('capture-button').textContent = refresh ? 'Actualizar y revisar conocidos' : 'Actualizar: solo nuevos';
    $('max-items-label').textContent = refresh ? 'A procesar por sección' : 'Nuevos por sección';
    $('capture-mode-hint').textContent = refresh ? 'Reprocesa también el detalle de elementos conocidos dentro de los límites elegidos. Conserva el archivo acumulado.' : 'Recorre la cuadrícula para encontrar novedades y reintenta capturas fallidas. Reutiliza el contexto de elementos conocidos; una captura parcial no elimina el historial.';
    const depth = $('capture-depth').value;
    const limit = ({quick: 5, deep: 60, all: 200})[depth] || $('max-items').value;
    $('capture-options-summary').textContent = `· ${$('capture-headless').checked ? 'sin ventana' : 'con ventana · espera verificación'} · ${limit} por sección`;
  }
  function renderExecutive(report) {
    const target = $('executive-report'); target.replaceChildren();
    target.hidden = !report;
    if (!report) return;
    append(target, el('h2', '', 'Resumen ejecutivo'), el('p', 'summary-text', report.summary));
    const coverage = el('ul', 'thematic-profile');
    list(report.coverage).forEach((item) => coverage.append(el('li', '', `${sourceNames[item.source] || item.source}: ${item.observed} observados · ${item.analyzed} con texto analizable · ${item.visual_analyzed} con visión · ${item.unknown_publication_dates} sin fecha original · ${item.excluded_by_date} fuera de ventana · ${item.unknown_date_filter} sin fecha para el filtro · ${item.status}. Total histórico desconocido.`)));
    target.append(coverage);
    list(report.themes).forEach((theme) => {
      const line = el('p', 'summary-text', `${theme.name} (${theme.count}): `);
      list(theme.evidence).forEach((item) => { const link = externalLink(item.url, item.id + ' ↗', 'text-button'); if (link) append(line, link, document.createTextNode(' ')); else line.append(document.createTextNode(item.id + ' ')); });
      target.append(line);
    });
    target.append(el('p', 'muted', report.date_provenance));
    const limits = el('details'); limits.append(el('summary', '', 'Límites y próximos pasos'));
    const items = el('ul', 'thematic-profile');
    [...list(report.limitations), ...list(report.next_steps)].forEach((item) => items.append(el('li', '', item)));
    limits.append(items); target.append(limits);
  }
  function fillProfile(config) {
    const settings = { ...(config.settings || {}), ...config };
    state.formProfile = handle(settings.profile);
    $('profile-input').value = state.formProfile;
    $('profile-label').value = settings.label || '';
    $('profile-notes').value = readable(settings.notes);
    $('profile-tags').value = list(settings.tags).join(', ');
    document.querySelectorAll('input[name="source"]').forEach((input) => { input.checked = list(settings.sources || ['uploads', 'reposts', 'likes']).includes(input.value); });
    const maxItems = String(number(settings.max_items, 30));
    if (![...$('max-items').options].some((option) => option.value === maxItems)) $('max-items').append(new Option(maxItems + ' elementos', maxItems));
    $('max-items').value = maxItems;
    $('scroll-rounds').value = number(settings.scroll_rounds, 12);
    $('capture-mode').value = settings.mode === 'refresh' ? 'refresh' : 'incremental';
    $('capture-headless').checked = settings.headless === true;
    $('capture-depth').value = settings.depth || 'quick';
    $('session-mode').value = settings.session_mode || 'anonymous';
    $('date-filter').value = settings.date_filter || 'any';
    const avatarPath = mediaUrl(settings.avatar_path || settings.avatar_url);
    $('profile-avatar-preview').hidden = !avatarPath;
    if (avatarPath) $('profile-avatar-preview').src = avatarPath;
    else $('profile-avatar-preview').removeAttribute('src');
    $('saved-profile-status').textContent = '· @' + state.formProfile;
    updateCaptureMode();
  }
  function profilePayload() {
    const profile = $('profile-input').value.trim(), sources = [...document.querySelectorAll('input[name="source"]:checked')].map((input) => input.value);
    if (!profile) throw new Error('Escribe un usuario o una URL de perfil de TikTok.');
    if (!sources.length) throw new Error('Selecciona al menos una sección para capturar.');
    if (!$('scroll-rounds').checkValidity()) throw new Error('El límite de desplazamientos debe estar entre 1 y 50.');
    if ($('date-filter').value === 'last30days' && (sources.length !== 1 || sources[0] !== 'uploads')) throw new Error('Últimos 30 días requiere solo Publicaciones; compartidos y Me gusta no tienen fecha de acción verificable.');
    return { profile, label: $('profile-label').value.trim(), notes: $('profile-notes').value.trim(), tags: $('profile-tags').value.split(',').map((tag) => tag.trim()).filter(Boolean), sources, max_items: Number($('max-items').value), scroll_rounds: Number($('scroll-rounds').value), mode: $('capture-mode').value, headless: $('capture-headless').checked, depth: $('capture-depth').value, date_filter: $('date-filter').value, session_mode: $('session-mode').value };
  }
  async function saveProfile() {
    const payload = profilePayload();
    const response = await api('/api/profiles', { method: 'POST', body: payload });
    fillProfile({ ...payload, ...(response.profile && typeof response.profile === 'object' ? response.profile : response) });
    await refreshState({ quiet: true });
    return response;
  }
  async function loadProfile(profile, { fill = true } = {}) {
    const version = ++state.selectionVersion;
    const reportPromise = api(profileEndpoint(profile, '/report'));
    const configPromise = fill ? api(profileEndpoint(profile)) : Promise.resolve(null);
    const [report, config] = await Promise.all([reportPromise, configPromise]);
    if (version !== state.selectionVersion) return report;
    if (config) fillProfile(config);
    showRun(report);
    return report;
  }
  async function reloadRecordResult(result, aggregateProfile) {
    if (aggregateProfile) await loadProfile(aggregateProfile, { fill: false });
    else showRun(result);
    await refreshState({ quiet: true });
  }
  function updateAiProviderFields() {
    const cloud = $('ai-provider').value === 'gemini';
    $('ai-key-field').hidden = !cloud;
    $('ai-disclosure').hidden = !cloud;
    $('ai-model').disabled = !cloud;
  }
  function renderAiSettings(settings) {
    state.ai = settings;
    $('ai-provider').value = settings.provider === 'gemini' ? 'gemini' : 'disabled';
    $('ai-model').value = settings.model || 'gemini-3.5-flash-lite';
    $('ai-key').value = '';
    $('ai-auto-visual').checked = Boolean(settings.auto_visual);
    $('ai-max-per-run').value = number(settings.max_per_run, 10);
    const cloud = settings.provider === 'gemini';
    $('ai-settings-summary').textContent = '· ' + (cloud ? 'Gemini' : 'Solo OCR') + (settings.configured ? ' configurado' : '');
    const usage = settings.usage || {};
    const keyStatus = settings.has_api_key ? ({ encrypted: 'Clave guardada cifrada en este equipo', session: 'Clave disponible durante esta sesión', environment: 'Clave configurada en el entorno' }[settings.key_persistence] || 'Clave disponible') : 'Sin clave de Gemini';
    $('ai-settings-status').textContent = `${keyStatus}. ${cloud ? (settings.configured ? 'Gemini configurado; disponibilidad y coste dependen de tu cuenta.' : 'Añade una clave para usar Gemini.') : 'Interpretación visual desactivada; OCR permanece local.'} Uso registrado: ${number(usage.requests)} solicitudes · ${number(usage.total_tokens)} tokens · ${number(usage.cached_hits)} resultados reutilizados.`;
    $('delete-ai-key').disabled = !settings.has_api_key;
    updateAiProviderFields();
  }
  async function refreshAiSettings() {
    try { renderAiSettings(await api('/api/ai/settings')); }
    catch (error) { $('ai-settings-status').textContent = 'No se pudieron cargar los ajustes de IA. ' + error.message; }
  }
  function renderRuns(runs) {
    const target = $('runs-list'); target.replaceChildren();
    if (!list(runs).length) return target.append(el('p', 'sidebar-empty', 'Tus exploraciones aparecerán aquí.'));
    list(runs).forEach((run) => {
      const button = el('button', 'run-item' + (state.run?.id === run.id ? ' selected' : ''));
      button.type = 'button'; button.title = '@' + handle(run.profile);
      const statusNames = { needs_attention: 'Requiere atención', failed: 'Falló', error: 'Falló', running: 'En curso', pending: 'Pendiente', queued: 'Pendiente', completed: 'Completado', complete: 'Completado', cancelled: 'Cancelado', canceled: 'Cancelado' };
      append(button, el('strong', '', '@' + handle(run.profile)), el('small', '', date(run.created_at, true)), el('small', '', `${number(run.count ?? run.record_count)} evidencias${run.status ? ' · ' + (statusNames[run.status] || translateStatus(run.status)) : ''}`));
      button.addEventListener('click', () => busy(button, () => loadRun(run.id)));
      target.append(button);
    });
  }
  async function loadRun(id) { const version = ++state.selectionVersion; const run = await api('/api/runs/' + encodeURIComponent(id)); if (version === state.selectionVersion) showRun(run); return run; }
  function showRun(run) {
    state.run = run; state.page = 1; state.filterIds = null; state.filterLabel = '';
    $('report').hidden = false; $('report-empty').hidden = true;
    renderExecutive(run.analysis?.executive);
    $('report-profile').textContent = '@' + handle(run.profile);
    $('synthesis-heading').textContent = 'Resumen del contenido de @' + handle(run.profile);
    $('report-meta').textContent = run.aggregate ? `${list(run.records).length} evidencias acumuladas · actualizado ${date(run.last_updated_at || run.updated_at || run.created_at, true)}` : `Captura del ${date(run.created_at, true)} · ${list(run.records).length} registros en esta ejecución`;
    const fictional = Boolean(run.demo || run.is_demo || run.collection?.demo || /^(demo|ejemplo|ficticio)/i.test(handle(run.profile)));
    $('demo-notice').hidden = !fictional;
    $('report-kicker').textContent = fictional ? 'EXPLORACIÓN DE DEMOSTRACIÓN' : run.aggregate ? 'ARCHIVO ACUMULADO DEL PERFIL' : 'HISTORIAL · UNA CAPTURA';
    $('view-profile').hidden = Boolean(run.aggregate || fictional);
    $('export-json').href = runEndpoint('/export?format=json'); $('export-md').href = runEndpoint('/export?format=md');
    $('nav-count').textContent = list(run.records).length;
    renderAnalysis(); renderEvidence(); renderCollection();
    if (state.app) { renderRuns(state.app.runs); renderProfiles(state.app.profiles); }
  }
  function renderCollection() {
    const target = $('collection-status'); target.replaceChildren();
    const summary = $('incremental-summary'); summary.replaceChildren();
    const incremental = state.run.collection?.incremental;
    summary.hidden = !incremental;
    if (incremental) {
      const stats = el('div', 'incremental-stats');
      [['new_count', 'nuevos'], ['reused_count', 'reutilizados'], ['retried_count', 'reintentados'], ['updated_count', 'actualizados'], ['scanned_count', 'vistos en cuadrícula']].forEach(([key, label]) => stats.append(append(el('span'), el('strong', '', number(incremental[key])), document.createTextNode(' ' + label))));
      append(summary, el('strong', 'incremental-title', incremental.mode === 'refresh' ? 'Última revisión de elementos nuevos y conocidos' : 'Última actualización incremental'), stats, el('p', '', 'El archivo conserva las evidencias anteriores aunque no aparezcan en este recorrido. La cobertura puede ser parcial.'));
      const stopReasons = incremental.stop_reasons;
      if (stopReasons && Object.keys(stopReasons).length) {
        const reasons = Array.isArray(stopReasons) ? stopReasons.map(readable) : Object.entries(stopReasons).map(([source, reason]) => `${sourceNames[source] || source}: ${readable(reason)}`);
        const details = el('details', 'scan-details'); append(details, el('summary', '', 'Límites del recorrido'), el('p', '', reasons.join(' · '))); summary.append(details);
      }
    }
    const issues = [...list(state.run.collection?.issues), ...list(state.run.blockers)];
    if (state.run.last_capture_reason) issues.push({ message: state.run.last_capture_reason });
    const shownIssues = new Set();
    issues.forEach((issue) => { const text = readable(issue.message || issue.reason || issue); if (text && !shownIssues.has(text)) { shownIssues.add(text); summary.hidden = false; summary.append(el('p', 'collection-issue', text)); } });
    const failureDiagnostic = captureDiagnostic(state.run.collection?.failure_diagnostic);
    if (state.run.collection?.failure_diagnostic?.captcha_visible === true) { summary.hidden = false; summary.append(el('p', 'collection-issue', 'Esta captura encontró una verificación CAPTCHA. Abre la sesión para resolverla antes de volver a capturar.')); }
    if (failureDiagnostic) { summary.hidden = false; summary.append(failureDiagnostic); }
    const sources = state.run.collection?.sources;
    if (!sources) return;
    const entries = Array.isArray(sources) ? sources.map((item) => [typeof item === 'string' ? item : item.source || item.name, item]) : Object.entries(sources);
    entries.forEach(([source, info]) => {
      const status = typeof info === 'object' ? info.status || info.message || '' : typeof info === 'string' && info !== source ? info : '';
      const label = sourceNames[source] || source;
      const badge = el('span', 'badge', label + (status ? ': ' + translateStatus(status) : ''));
      if (/private|unavailable|blocked|error|not_found/i.test(status)) badge.classList.add('warn');
      badge.title = typeof info === 'object' ? readable(info.message || info.reason || info.detail) : '';
      target.append(badge);
    });
  }
  function translateStatus(status) {
    const names = { public: 'público', available: 'disponible', private: 'privado', unavailable: 'no disponible', empty: 'sin contenido', sampled: 'muestra parcial', collected: 'recopilado', complete: 'completado', completed: 'completado', success: 'recopilado', skipped: 'omitido', not_found: 'no encontrado', blocked: 'acceso bloqueado', error: 'error' };
    return names[status] || String(status);
  }
  function renderAnalysis() {
    const run = state.run, records = list(run.records), analysis = run.analysis || {}, metrics = analysis.metrics || {}, topics = list(analysis.topics);
    const total = number(metrics.total_records, records.length);
    const dated = number(metrics.dated_count, records.filter((record) => record.interaction_at).length);
    $('metric-total').textContent = total;
    $('metric-total-detail').textContent = number(metrics.duplicate_count) ? `${number(metrics.duplicate_count)} duplicados descartados` : run.aggregate ? 'En el archivo acumulado' : 'En esta captura';
    $('metric-topics').textContent = topics.length;
    $('metric-dated').textContent = dated;
    const undated = number(analysis.undated_count ?? metrics.undated_count, Math.max(0, total - dated));
    $('metric-dated-detail').textContent = `${undated} sin fecha de interacción`;
    const context = records.filter((record) => record.caption || record.ocr_text || record.transcript || record.visual_summary).length;
    $('metric-context').replaceChildren(document.createTextNode(total ? String(Math.round(context / records.length * 100)) : '0'), el('span', '', '%'));
    $('analysis-summary').textContent = readable(analysis.profile_summary?.text || analysis.summary) || 'No hay análisis disponible todavía. Añade evidencias o usa Reanalizar para construir una lectura temática de la muestra.';
    const observations = list(analysis.profile_summary?.observations);
    $('thematic-profile').replaceChildren(...(observations.length ? observations : list(analysis.thematic_profile)).map((item) => {
      const row = el('li', '', readable(item));
      if (list(item?.evidence_ids).length) { const button = el('button', 'text-button', 'Ver evidencia'); button.type = 'button'; button.addEventListener('click', () => filterEvidence(item.evidence_ids, short(item.text, 65))); row.append(document.createTextNode(' '), button); }
      return row;
    }));
    const target = $('topics-list'); target.replaceChildren();
    if (!topics.length) target.append(el('p', 'empty-inline', 'No hay suficiente texto o contexto para identificar temas. Puedes completar cada evidencia con OCR, transcripción y descripción visual.'));
    topics.slice(0, 8).forEach((topic) => {
      const percent = Math.max(0, Math.min(100, number(topic.percent, total ? number(topic.count) / total * 100 : 0)));
      const button = el('button', 'topic-row'); button.type = 'button'; button.title = 'Ver evidencias de ' + topic.name;
      const top = append(el('div', 'topic-top'), el('span', '', topic.name), el('span', '', `${number(topic.count)} · ${Math.round(percent)}%`));
      const bar = el('span', 'bar-fill'); bar.style.width = percent + '%';
      append(button, top, append(el('div', 'bar-track'), bar));
      button.addEventListener('click', () => filterEvidence(topic.evidence_ids, topic.name)); target.append(button);
    });
    renderSources(); renderTimeline(); renderCoverage(); renderMusic(); renderMentions(); renderThematicPeriods(); renderDataQuality();
    const limitations = list(analysis.limitations);
    $('limitations-list').replaceChildren(...(limitations.length ? limitations : ['La muestra puede ser parcial y depende de las secciones visibles y de la sesión.', 'La fecha original del video no equivale a la fecha de compartido o me gusta.', 'El contenido compartido no demuestra experiencias personales ni características psicológicas.']).map((value) => el('li', '', readable(value))));
  }
  function renderDataQuality() {
    const target = $('data-quality'); target.replaceChildren();
    const quality = state.run.analysis?.data_quality;
    if (!quality) return target.append(el('p', 'empty-inline', 'El informe de cobertura se calcula al reanalizar. Los límites de cada captura aparecen también en el historial.'));
    target.append(el('p', 'muted', readable(quality.summary)));
    const counts = el('div', 'quality-counts');
    const labels = { available: 'Disponibles', unavailable: 'No disponibles', blocked: 'Bloqueados', partial: 'Parciales', unknown: 'Sin verificar' };
    Object.entries(quality.availability_counts || {}).forEach(([key, count]) => counts.append(el('span', 'badge', `${labels[key] || key}: ${number(count)}`)));
    target.append(counts);
    const audioLabels = { available: 'disponible', muted_notice: 'con aviso de silencio', not_captured: 'sin capturar', unknown: 'sin verificar' };
    target.append(el('p', 'muted', 'Audio: ' + Object.entries(quality.audio_counts || {}).map(([key, count]) => `${number(count)} ${audioLabels[key] || key}`).join(' · ')));
    list(quality.issues).forEach((issue) => { const row = el('div', 'quality-issue'); append(row, el('p', '', readable(issue.message)), el('span', 'muted', `${number(issue.count)} evidencias`)); if (list(issue.evidence_ids).length) { const button = el('button', 'text-button', 'Ver'); button.type = 'button'; button.addEventListener('click', () => filterEvidence(issue.evidence_ids, readable(issue.message))); row.append(button); } target.append(row); });
  }
  function renderSources() {
    const target = $('source-comparison'); target.replaceChildren();
    const comparisons = list(state.run.analysis?.source_comparison), records = list(state.run.records);
    [['uploads', 'Publicaciones propias'], ['reposts', 'Compartidos'], ['likes', 'Me gusta']].forEach(([source, label]) => {
      const group = comparisons.find((item) => item.source === source), selected = records.filter((record) => record.source === source), collection = state.run.collection?.sources?.[source];
      const column = el('div', 'source-column');
      const top = append(el('div', 'source-column-top'), el('h3', '', label), el('strong', '', number(group?.count, selected.length)));
      const details = collection?.reason || collection?.message || (selected.length ? 'Muestra recopilada; la cobertura del historial completo es desconocida.' : 'No hay registros de esta sección en la muestra.');
      append(column, top, el('p', '', details));
      const diagnostic = captureDiagnostic(collection, label.toLocaleLowerCase('es'));
      if (diagnostic) column.append(diagnostic);
      const topics = list(group?.topics).slice(0, 3);
      const tags = el('div', 'source-topic-tags');
      topics.forEach((topic) => { const tag = el('button', '', `${topic.name} · ${Math.round(number(topic.percent))}%`); tag.type = 'button'; tag.addEventListener('click', () => filterEvidence(topic.evidence_ids, `${label} · ${topic.name}`)); tags.append(tag); });
      if (!topics.length && selected.length) tags.append(el('span', '', 'Temas pendientes de contexto suficiente'));
      column.append(tags);
      if (selected.length) {
        const button = el('button', 'text-button', 'Ver esta sección →'); button.type = 'button';
        button.addEventListener('click', () => { state.filterIds = null; state.filterLabel = ''; $('evidence-source').value = source; $('evidence-search').value = ''; state.page = 1; renderEvidence(); $('evidence').scrollIntoView({ behavior: 'smooth' }); }); column.append(button);
      }
      target.append(column);
    });
  }
  function renderMentions() {
    const target = $('mentions-list'); target.replaceChildren();
    const mentions = list(state.run.analysis?.mentions);
    if (!mentions.length) return target.append(el('p', 'empty-inline', 'Todavía no hay menciones documentadas. En una evidencia compartida puedes añadir la nota visible, su autor y la captura o enlace que permite verificarla.'));
    const originNames = { caption: 'Descripción del video', repost_note: 'Nota del compartido', ocr_text: 'Texto de imagen', transcript: 'Audio transcrito', visual_summary: 'Descripción visual', original_caption: 'Descripción del creador original' };
    mentions.forEach((mention) => {
      const card = el('article', 'mention-card');
      const button = el('button', 'mention-handle', '@' + handle(mention.handle)); button.type = 'button'; button.addEventListener('click', () => filterEvidence(mention.evidence_ids, 'Mención a @' + handle(mention.handle)));
      const own = number(mention.owner_authored_count);
      append(card, append(el('div', 'mention-heading'), button, el('span', 'badge', number(mention.count) + ' evidencias')), el('p', 'mention-attribution', own ? `${own} con autoría atribuida al perfil en el texto fuente` : 'Sin menciones con autoría confirmada del perfil'));
      const contexts = el('div', 'mention-contexts');
      list(mention.contexts).slice(0, 4).forEach((context) => {
        const item = el('button', 'mention-context'); item.type = 'button';
        const attributionNames = { profile: 'Autoría del perfil', original_author: 'Autoría del creador original', unverified: 'Autoría sin verificar', owner: 'Autoría del perfil', profile_owner: 'Autoría del perfil', original_creator: 'Autoría del creador original', unknown: 'Autoría sin verificar', third_party: 'Autoría de otra cuenta', uncertain: 'Autoría sin verificar' };
        const source = originNames[context.origin] || readable(context.origin) || 'Origen sin identificar';
        append(item, el('span', 'context-source', source + (context.actor ? ' · @' + handle(context.actor) : '')), el('p', '', short(context.excerpt, 220)), el('small', '', attributionNames[context.attribution] || readable(context.attribution) || 'Autoría sin verificar'));
        item.addEventListener('click', () => openRecord(context.record_id)); contexts.append(item);
      });
      card.append(contexts);
      if (list(mention.contexts).length > 4) card.append(el('p', 'section-footnote', 'Abre la mención para consultar todas sus evidencias.'));
      target.append(card);
    });
  }
  function renderThematicPeriods() {
    const target = $('thematic-periods'); target.replaceChildren();
    const periods = list(state.run.analysis?.thematic_periods);
    if (!periods.length) return target.append(el('p', 'empty-inline', 'No hay suficiente contenido temático con fechas documentadas para construir períodos. La primera observación de un compartido no permite asignarle una fecha de interacción.'));
    const basisNames = { interaction: 'Interacción documentada', interaction_at: 'Interacción documentada', own_publication: 'Publicación propia', published_at: 'Publicación documentada', uploads: 'Publicación propia', original: 'Publicación original ajena' };
    periods.forEach((entry) => {
      const card = el('article', 'period-card');
      const range = entry.start === entry.end ? period(entry.start) : `${period(entry.start)} — ${period(entry.end)}`;
      const percent = Math.max(0, Math.min(100, number(entry.share) * (number(entry.share) <= 1 ? 100 : 1)));
      append(card, el('span', 'period-basis', (basisNames[entry.date_basis] || readable(entry.date_basis)) + (entry.source ? ' · ' + (sourceNames[entry.source] || entry.source) : '')), el('h3', '', readable(entry.topic)), el('p', 'period-range', range), el('p', 'period-share', `${number(entry.count)} de ${number(entry.total_in_period)} evidencias · ${Math.round(percent)}%`), el('p', 'period-description', readable(entry.description)));
      if (entry.limitations) card.append(el('p', 'period-limitations', readable(entry.limitations)));
      const button = el('button', 'text-button', 'Ver evidencias del período →'); button.type = 'button'; button.addEventListener('click', () => filterEvidence(entry.evidence_ids, `${readable(entry.topic)} · ${range}`)); card.append(button); target.append(card);
    });
  }
  function renderTimeline() {
    const analysis = state.run?.analysis || {}, original = state.timeline === 'original', uploads = state.timeline === 'uploads';
    const entries = list(uploads ? analysis.own_timeline : original ? analysis.original_timeline : analysis.timeline);
    const basisLabel = uploads ? 'Fecha de publicación propia' : original ? 'Fecha de publicación original' : 'Fecha documentada de interacción';
    $('timeline-explanation').textContent = uploads ? 'Publicaciones propias del perfil, agrupadas por su fecha de publicación documentada. Esta vista permite comparar sus temas en el tiempo; excluye contenido ajeno compartido o marcado con me gusta.' : original ? 'Fecha original de los elementos de la muestra, incluidas publicaciones ajenas. No indica cuándo el perfil los compartió o marcó como me gusta.' : 'Fechas de interacción documentadas. La fecha de publicación de un elemento no indica cuándo se compartió o recibió un me gusta.';
    document.querySelectorAll('[data-timeline]').forEach((button) => { const selected = button.dataset.timeline === state.timeline; button.classList.toggle('selected', selected); button.setAttribute('aria-pressed', String(selected)); });
    const chart = $('timeline-chart'); chart.replaceChildren();
    if (!entries.length) {
      append(chart, append(el('div', 'chart-empty'), el('span', '', '◷'), el('strong', '', uploads ? 'Todavía no hay publicaciones propias con fecha' : original ? 'Todavía no hay fechas de publicación' : 'Las interacciones aún no tienen fecha verificable'), el('p', '', uploads ? 'Captura la sección Publicaciones o completa la fecha visible de publicación en el detalle de cada evidencia propia.' : original ? 'Cuando una evidencia incluya la fecha original de publicación, aparecerá en esta vista.' : 'TikTok no siempre muestra cuándo se compartió o marcó un elemento. Puedes añadir una fecha documentada en el detalle de la evidencia. La captura de hoy no fecha una interacción pasada.')));
    } else {
      const bars = el('div', 'chart-bars' + (uploads ? ' uploads' : original ? ' original' : ''));
      const max = Math.max(...entries.map((item) => number(item.count)), 1);
      entries.forEach((entry) => {
        const column = el('button', 'chart-column'); column.type = 'button';
        column.title = `${period(entry.period)}: ${number(entry.count)} evidencias. ${list(entry.topics).map((topic) => topic.name + ': ' + topic.count).join(', ')}`;
        column.setAttribute('aria-label', `Ver ${number(entry.count)} evidencias de ${period(entry.period)}`);
        const bar = el('span', 'chart-bar'); bar.style.height = Math.max(3, number(entry.count) / max * 145) + 'px';
        append(column, el('span', 'chart-value', number(entry.count)), bar, el('span', 'chart-period', period(entry.period)));
        column.addEventListener('click', () => filterEvidence(entry.evidence_ids, `${uploads ? 'Publicaciones propias' : original ? 'Publicación original' : 'Interacción'} · ${period(entry.period)}`)); bars.append(column);
      }); chart.append(bars);
    }
    $('timeline-key').replaceChildren(el('span', '', basisLabel), el('span', '', `${entries.reduce((sum, entry) => sum + number(entry.count), 0)} evidencias fechadas`));
    const changes = $('timeline-changes'); changes.replaceChildren();
    if (!original) list(uploads ? analysis.own_changes : analysis.changes).slice(0, 6).forEach((change) => {
      const item = append(el('button', 'timeline-change'), el('strong', '', period(change.period) + (change.topic ? ' · ' + change.topic : '')), el('p', '', change.description));
      item.type = 'button'; item.addEventListener('click', () => filterEvidence(change.evidence_ids, change.topic || period(change.period))); changes.append(item);
    });
  }
  function renderCoverage() {
    const records = list(state.run.records), total = records.length, target = $('coverage-list'); target.replaceChildren();
    [['caption', 'Descripción'], ['ocr_text', 'Texto en imagen'], ['transcript', 'Transcripción'], ['visual_summary', 'Contexto visual'], ['music', 'Referencia musical']].forEach(([key, name]) => {
      const count = records.filter((record) => key === 'music' ? record.music?.title || record.music?.author : record[key]).length;
      const bar = el('span', 'bar-fill'); bar.style.width = (total ? count / total * 100 : 0) + '%';
      target.append(append(el('div', 'coverage-row'), el('span', '', name), append(el('div', 'bar-track'), bar), el('span', '', `${count}/${total}`)));
    });
  }
  function renderMusic() {
    const target = $('music-list'); target.replaceChildren();
    let music = list(state.run.analysis?.music);
    if (!music.length) {
      const grouped = new Map();
      list(state.run.records).forEach((record) => { if (record.music?.title) { const key = record.music.title; const prior = grouped.get(key); grouped.set(key, { ...record.music, count: prior ? prior.count + 1 : 1 }); } });
      music = [...grouped.values()].sort((a, b) => b.count - a.count);
    }
    if (!music.length) return target.append(el('p', 'empty-inline', 'No se identificaron referencias musicales. Puedes añadir audio local a una evidencia para transcribirlo si está disponible el motor de audio.'));
    music.slice(0, 5).forEach((item) => {
      const title = item.title || item.name || item.track || 'Audio sin título';
      const heading = externalLink(item.url, title) || el('span', '', title);
      const description = append(el('div'), append(el('strong'), heading), el('p', '', item.author || item.artist || 'Autor no identificado'));
      target.append(append(el('div', 'music-item'), el('span', 'music-icon', '♫'), description, el('span', '', number(item.count, 1) + '×')));
    });
  }
  function filterEvidence(ids, label) {
    if (!list(ids).length) return toast('Este grupo no tiene referencias de evidencia disponibles.');
    state.filterIds = new Set(ids.map(String)); state.filterLabel = label || 'Selección'; state.page = 1;
    $('evidence-source').value = 'all'; $('evidence-search').value = ''; renderEvidence(); $('evidence').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function renderEvidence() {
    if (!state.run) return;
    const query = $('evidence-search').value.trim().toLocaleLowerCase('es'), source = $('evidence-source').value;
    const records = list(state.run.records).filter((record) => {
      if (state.filterIds && !state.filterIds.has(String(record.id))) return false;
      if (source !== 'all' && record.source !== source) return false;
      return !query || [record.caption, record.repost_note, record.repost_note_author, record.ocr_text, record.transcript, record.visual_summary, record.notes, record.music?.title, record.music?.author, record.author].map(readable).join(' ').toLocaleLowerCase('es').includes(query);
    });
    $('evidence-count').textContent = records.length;
    $('active-filter').hidden = !state.filterIds; $('active-filter').querySelector('span').textContent = state.filterLabel;
    const pageCount = Math.max(1, Math.ceil(records.length / perPage)); state.page = Math.max(1, Math.min(state.page, pageCount));
    const grid = $('evidence-grid'); grid.replaceChildren();
    if (!records.length) grid.append(el('p', 'grid-empty', list(state.run.records).length ? 'No hay evidencias que coincidan con estos filtros.' : 'Esta exploración todavía no contiene evidencias. Revisa la sesión y la disponibilidad de las secciones del perfil.'));
    records.slice((state.page - 1) * perPage, state.page * perPage).forEach((record, index) => grid.append(evidenceCard(record, index + 1 + (state.page - 1) * perPage)));
    const pagination = $('evidence-pagination'); pagination.replaceChildren();
    if (pageCount > 1) {
      const previous = el('button', 'button button-small', '← Anterior'); previous.disabled = state.page === 1;
      const next = el('button', 'button button-small', 'Siguiente →'); next.disabled = state.page === pageCount;
      previous.addEventListener('click', () => { state.page--; renderEvidence(); $('evidence').scrollIntoView(); });
      next.addEventListener('click', () => { state.page++; renderEvidence(); $('evidence').scrollIntoView(); });
      append(pagination, previous, el('span', '', `${state.page} / ${pageCount}`), next);
    }
  }
  function evidenceCard(record, index) {
    const card = el('article', 'evidence-card');
    const preview = el('button', 'evidence-preview'); preview.type = 'button'; preview.setAttribute('aria-label', `Abrir evidencia ${index}: ${short(record.caption || record.ocr_text || 'Sin descripción', 90)}`);
    const imagePath = verifiedFrames(record).map(mediaUrl).find(Boolean);
    if (imagePath) {
      const image = el('img'); image.src = imagePath; image.alt = 'Captura de la evidencia ' + index; image.loading = 'lazy'; image.referrerPolicy = 'no-referrer';
      image.addEventListener('error', () => { image.replaceWith(placeholder(record)); }, { once: true }); preview.append(image);
    } else preview.append(placeholder(record));
    append(preview, el('span', 'evidence-source-badge', sourceNames[record.source] || 'Evidencia'), el('span', 'evidence-index', String(index).padStart(2, '0')));
    const badges = el('div', 'evidence-context-badges');
    if (record.ocr_text) badges.append(el('span', '', 'OCR'));
    if (record.transcript) badges.append(el('span', '', 'Audio'));
    if (record.visual_summary) badges.append(el('span', '', 'Visual'));
    if (record.evidence_quality === 'obstructed' || record.evidence_quality?.status === 'obstructed') badges.append(el('span', '', 'Captura obstruida'));
    if (record.repost_note) badges.append(el('span', '', 'Nota del compartido'));
    if (record.availability?.status && record.availability.status !== 'available') badges.append(el('span', '', { unavailable: 'No disponible', blocked: 'Acceso bloqueado', partial: 'Contexto parcial' }[record.availability.status] || 'Disponibilidad sin verificar'));
    if (record.availability?.audio_status === 'muted_notice') badges.append(el('span', '', 'Aviso de audio silenciado'));
    if (!imagePath) badges.append(el('span', '', diagnosticImages(record).length ? 'Recorte pendiente de verificar' : 'Sin captura'));
    preview.append(badges); preview.addEventListener('click', () => openRecord(record.id));
    const body = el('div', 'evidence-body'); body.append(el('h3', '', record.caption || record.ocr_text || record.visual_summary || 'Evidencia sin descripción'));
    const dates = el('div', 'evidence-date');
    dates.append(el('span', '', `Video publicado: ${record.published_at ? date(record.published_at) : 'sin fecha'}`));
    if (record.source !== 'uploads') dates.append(el('span', record.interaction_at ? '' : 'unknown-date', `Interacción: ${record.interaction_at ? date(record.interaction_at) : 'fecha desconocida'}`));
    else dates.append(el('span', '', 'Publicación propia del perfil'));
    body.append(dates);
    if (record.music?.title) body.append(el('p', 'evidence-music', '♫ ' + record.music.title));
    const detail = el('button', '', 'Ver contexto →'); detail.type = 'button'; detail.addEventListener('click', () => openRecord(record.id));
    body.append(append(el('div', 'evidence-card-footer'), detail, externalLink(record.url, 'TikTok ↗')));
    return append(card, preview, body);
  }
  function placeholder(record) { return append(el('div', 'evidence-placeholder'), el('p', '', short(record.ocr_text || record.caption || record.visual_summary || 'El contexto empieza con una evidencia.', 125))); }
  function openRecord(id) {
    const record = list(state.run.records).find((entry) => String(entry.id) === String(id)); if (!record) return;
    const content = $('dialog-content'); content.replaceChildren();
    const layout = el('div', 'dialog-layout');
    append(layout, append(el('div', 'dialog-header'), el('h2', '', sourceNames[record.source] || 'Evidencia'), externalLink(record.url, 'Abrir original ↗', 'button button-small')));
    const paths = [...new Set(verifiedFrames(record))].map(mediaUrl).filter(Boolean);
    if (paths.length) {
      const gallery = el('div', 'dialog-gallery');
      paths.forEach((path, index) => { const image = el('img'); image.src = path; image.alt = `Captura o fotograma ${index + 1}`; image.loading = 'lazy'; image.referrerPolicy = 'no-referrer'; gallery.append(image); }); layout.append(gallery);
    }
    const diagnostics = diagnosticImages(record).map(mediaUrl).filter(Boolean);
    if (diagnostics.length) {
      const details = el('details', 'diagnostic-gallery');
      append(details, el('summary', '', `Capturas de referencia y archivo anterior (${diagnostics.length})`), el('p', 'muted', 'Estas imágenes pueden incluir menús, ventanas superpuestas o recortes antiguos. No se usan para la interpretación visual hasta obtener un recorte verificado.'));
      const gallery = el('div', 'dialog-gallery'); diagnostics.forEach((path, index) => { const image = el('img'); image.src = path; image.alt = `Referencia de diagnóstico ${index + 1}`; image.loading = 'lazy'; image.referrerPolicy = 'no-referrer'; gallery.append(image); }); details.append(gallery); layout.append(details);
    }
    if (record.evidence_quality === 'obstructed' || record.evidence_quality?.status === 'obstructed') layout.append(el('p', 'notice', 'La captura está obstruida. Revisa el navegador y vuelve a capturar para obtener un fotograma útil.'));
    if (record.visual_error?.message) layout.append(el('p', 'notice error', record.visual_error.message));
    const visual = record.visual_analysis;
    if (visual) {
      const panel = el('section', 'visual-analysis');
      append(panel, el('h3', 'record-data-heading', 'Interpretación visual'), el('p', 'muted', `${visual.provider || 'IA'} · ${visual.model || ''} · ${number(visual.frame_count)} imágenes${visual.cached ? ' · reutilizado' : ''}`));
      if (list(visual.observations).length) panel.append(append(el('ul'), ...visual.observations.map((observation) => el('li', '', readable(observation)))));
      list(visual.cultural_context).forEach((context) => { append(panel, el('h3', 'record-data-heading', 'Contexto cultural sugerido'), el('p', '', readable(context.interpretation)), el('p', 'muted', `Apoyo observable: ${list(context.evidence).map(readable).join(' · ')}. Confianza: ${{ low: 'baja', medium: 'media', high: 'alta' }[context.confidence] || 'sin indicar'}.`)); });
      if (list(visual.uncertainties).length) panel.append(el('p', 'muted', 'Límites: ' + visual.uncertainties.map(readable).join(' · ')));
      panel.append(el('p', 'muted', 'Las interpretaciones describen el contenido y sus referencias; no identifican etnia, vida privada ni relaciones de una persona.'));
      layout.append(panel);
    }
    const metadata = el('dl', 'record-meta');
    const metricLabels = { likes: 'Me gusta del contenido', comments: 'Comentarios del contenido', shares: 'Veces compartido', saves: 'Guardados del contenido', views: 'Visualizaciones' };
    const rows = [
      ['Autor', record.author],
      ['Formato', record.format ? ({ photo: 'Foto o carrusel', video: 'Video' }[record.format] || record.format) : ''],
      ['Publicación original', record.published_at ? date(record.published_at) : ''],
      ['Texto original de la fecha', record.published_date_raw],
      ['Fecha de interacción', record.interaction_at ? date(record.interaction_at) : ''],
      ['Origen de la fecha de interacción', record.interaction_date_source === 'user_verified' ? 'Verificada por el usuario' : record.interaction_date_source === 'visible' ? 'Visible en TikTok' : record.interaction_date_source],
      ['Primera observación de la aplicación', record.first_seen_at ? date(record.first_seen_at, true) : ''],
      ['Última observación de la aplicación', record.last_seen_at ? date(record.last_seen_at, true) : ''],
      ['Veces observado', record.observation_count || list(record.observations).length || ''],
      ['Momento de captura', record.observed_at ? date(record.observed_at, true) : ''],
      ['Fijado en el perfil', typeof record.pinned === 'boolean' ? (record.pinned ? 'Sí' : 'No') : ''],
      ['Texto de la métrica en la cuadrícula', record.grid_metric_raw],
      ['Hashtags', readable(record.hashtags)],
      ['Música', record.music?.title],
      ['Autor de la música', record.music?.author],
      ['Fuente de la nota del compartido', record.repost_note_source === 'user_verified' ? 'Verificada por el usuario' : record.repost_note_source === 'visible' ? 'Visible en TikTok' : record.repost_note_source],
    ];
    Object.entries(record.metrics || {}).forEach(([key, value]) => {
      const raw = value && typeof value === 'object' ? value.raw ?? value.value ?? value.count : value;
      if (raw === null || raw === undefined || raw === '') return;
      rows.push([metricLabels[key] || key, readable(raw) + (value?.observed_at ? ' · observado ' + date(value.observed_at, true) : '')]);
    });
    rows.filter(([, value]) => value !== undefined && value !== null && value !== '').forEach(([label, value]) => metadata.append(append(el('div'), el('dt', '', label), el('dd', '', readable(value)))));
    if (metadata.children.length) append(layout, el('h3', 'record-data-heading', 'Datos de la evidencia'), metadata);
    const availability = record.availability;
    if (availability) {
      const panel = el('div', 'availability-panel');
      const statusNames = { available: 'Disponible', unavailable: 'No disponible', blocked: 'Acceso bloqueado', partial: 'Captura parcial' }, audioNames = { unknown: 'Sin verificar', muted_notice: 'Aviso de audio silenciado', not_captured: 'Audio sin capturar', available: 'Disponible' };
      append(panel, el('h3', 'record-data-heading', 'Disponibilidad observada'), el('p', '', `Contenido: ${statusNames[availability.status] || 'Sin verificar'} · Audio: ${audioNames[availability.audio_status] || 'Sin verificar'}`));
      if (availability.audio_reason) panel.append(el('p', '', availability.audio_reason));
      list(availability.issues).forEach((issue) => panel.append(el('p', '', readable(issue.message || issue))));
      layout.append(panel);
    }
    const form = el('form', 'record-form');
    const fields = {};
    function field(key, label, value, type = 'textarea', full = false, hint = '') {
      const wrapper = el('label', 'form-field' + (full ? ' full' : '')); wrapper.append(document.createTextNode(label));
      const input = el(type === 'textarea' ? 'textarea' : 'input'); input.name = key; input.value = value || '';
      if (type !== 'textarea') input.type = type;
      if (key === 'interaction_at') input.placeholder = 'AAAA-MM-DD';
      fields[key] = input; wrapper.append(input); if (hint) wrapper.append(el('span', '', hint)); form.append(wrapper);
    }
    field('caption', record.source === 'uploads' ? 'Descripción de la publicación propia' : 'Descripción del video original', record.caption, 'textarea', true, record.source === 'uploads' ? '' : 'Este texto pertenece al video original. Sus etiquetas no se atribuyen automáticamente al perfil que lo compartió.');
    if (record.source === 'reposts') {
      field('repost_note', 'Nota visible del compartido', record.repost_note, 'textarea', true, 'Transcribe solo la nota visible que acompaña al compartido, incluidas sus @menciones.');
      field('repost_note_author', 'Autor visible de la nota', record.repost_note_author, 'text', true, 'Indica el @usuario que aparece como autor. Déjalo vacío si no se puede verificar; no se asumirá que es el dueño del perfil.');
    }
    field('published_at', 'Fecha de publicación original', record.published_at ? String(record.published_at).slice(0, 10) : '', 'date', false, 'Fecha del video original, visible en TikTok.');
    field('interaction_at', 'Fecha documentada de interacción', record.interaction_at ? String(record.interaction_at).slice(0, 10) : '', 'date', false, 'Solo si una fuente confirma el día de compartido o me gusta.');
    field('ocr_text', 'Texto extraído de imagen (OCR)', record.ocr_text);
    field('transcript', 'Transcripción de audio', record.transcript);
    field('visual_summary', 'Descripción del contexto visual', record.visual_summary, 'textarea', true, 'Describe lo observable: escena, gestos, objetos y texto. Evita inferir experiencias privadas.');
    field('notes', 'Notas y fuente de verificación', Array.isArray(record.notes) ? record.notes.join('\n') : record.notes, 'textarea', true, 'Incluye el enlace o referencia de la captura que documenta la fecha, la nota del compartido y su autor. Puedes adjuntar la captura más abajo.');
    const save = el('button', 'button button-primary', 'Guardar y reanalizar'); save.type = 'submit';
    form.append(append(el('div', 'record-form-actions'), el('p', '', 'Tus anotaciones se guardan en esta exploración local y se incluyen en el informe.'), save));
    const feedback = el('p', 'notice dialog-notice'); feedback.hidden = true;
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); save.disabled = true; feedback.hidden = true;
      try {
        const body = Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, input.value.trim()]));
        for (const key of ['interaction_at', 'published_at']) {
          if ((record[key] ? String(record[key]).slice(0, 10) : '') === body[key]) delete body[key];
          else body[key] = body[key] || null;
        }
        if ('interaction_at' in body) body.interaction_date_source = body.interaction_at ? 'user_verified' : null;
        const noteChanged = ['repost_note', 'repost_note_author'].some((key) => key in body && body[key] !== (record[key] || ''));
        if (noteChanged) body.repost_note_source = body.repost_note ? 'user_verified' : null;
        else { delete body.repost_note; delete body.repost_note_author; }
        body.notes = body.notes.split('\n').map((line) => line.trim()).filter(Boolean);
        const aggregateProfile = state.run.aggregate ? state.run.profile : null;
        const run = await api(recordEndpoint(record), { method: 'PATCH', body }); await reloadRecordResult(run, aggregateProfile); $('evidence-dialog').close(); toast('Evidencia guardada y análisis actualizado.');
      } catch (error) { feedback.textContent = error.message; feedback.classList.add('error'); feedback.hidden = false; } finally { save.disabled = false; }
    });
    append(layout, form, feedback);
    const media = el('section', 'media-panel');
    append(media, el('h3', '', 'Completar el contexto con un archivo'), el('p', '', 'Añade una imagen, video o audio obtenido legítimamente. El procesamiento local extrae fotogramas, texto y transcripción según las herramientas instaladas.'));
    const input = el('input', 'media-input'); input.type = 'file'; input.accept = '.png,.jpg,.jpeg,.webp,.mp4,.mov,.webm,.wav,.mp3,.m4a'; input.setAttribute('aria-label', 'Archivo de imagen, video o audio, máximo 100 MB');
    const upload = el('button', 'button button-small', 'Procesar archivo'); upload.type = 'button'; upload.disabled = true;
    input.addEventListener('change', () => { upload.disabled = !input.files.length; });
    upload.addEventListener('click', async () => {
      if (!input.files[0]) return;
      if (input.files[0].size > 100 * 1024 * 1024) { feedback.textContent = 'El archivo supera el máximo de 100 MB.'; feedback.classList.add('error'); feedback.hidden = false; return; }
      upload.disabled = true;
      try { const data = new FormData(); data.append('file', input.files[0]); const job = await api(recordEndpoint(record, '/media'), { method: 'POST', body: data }); state.jobProfile = state.run.aggregate ? state.run.profile : null; $('evidence-dialog').close(); startPolling(job); }
      catch (error) { feedback.textContent = error.message; feedback.classList.add('error'); feedback.hidden = false; } finally { upload.disabled = !input.files.length; }
    });
    const gemini = state.ai?.provider === 'gemini', visionReady = gemini && Boolean(state.ai?.configured);
    const vision = el('button', 'button button-small', gemini ? 'Interpretar con Gemini' : 'Solo OCR · visión desactivada'); vision.type = 'button'; vision.disabled = !visionReady || !paths.length;
    vision.title = !paths.length ? 'Se necesita un recorte verificado: vuelve a capturar o adjunta una imagen del contenido' : !visionReady ? 'Selecciona Gemini en Ajustes IA si aceptas envío remoto y posible coste' : 'Enviar recortes verificados y texto de esta evidencia a Google';
    vision.addEventListener('click', async () => { vision.disabled = true; try { const job = await api(recordEndpoint(record, '/vision'), { method: 'POST' }); state.jobProfile = state.run.aggregate ? state.run.profile : null; $('evidence-dialog').close(); startPolling(job); } catch (error) { feedback.textContent = error.message; feedback.classList.add('error'); feedback.hidden = false; } finally { vision.disabled = false; } });
    append(media, append(el('div', 'media-actions'), input, upload, vision));
    if (!paths.length) media.append(el('p', '', 'La interpretación necesita un recorte verificado del contenido. Vuelve a capturar o adjunta una imagen; las referencias de diagnóstico se conservan aparte.'));
    else if (gemini) media.append(el('p', '', 'Al interpretar, los recortes y el texto de esta evidencia se envían a Google.'));
    layout.append(media); content.append(layout);
    if (!$('evidence-dialog').open) $('evidence-dialog').showModal();
  }
  function updateJob(job) {
    state.job = job;
    $('active-job').hidden = false;
    $('job-title').textContent = /media|vision|audio/i.test(job.kind || job.type || '') ? 'Procesando el contexto' : 'Recopilando evidencias';
    $('job-message').textContent = job.message || 'Preparando el procesamiento…';
    const progress = number(job.progress, -1);
    if (progress < 0) $('job-progress').removeAttribute('value'); else $('job-progress').value = Math.max(0, Math.min(100, progress > 0 && progress < 1 ? progress * 100 : progress));
    $('cancel-job').disabled = !isActive(job);
    $('capture-button').disabled = isActive(job);
    $('auto-button').disabled = isActive(job);
    $('session-button').disabled = isActive(job); $('close-session').disabled = isActive(job); $('login-button').disabled = isActive(job); $('preflight-button').disabled = isActive(job);
  }
  async function startPolling(initial) {
    const job = initial.job || initial;
    if (!job.id) { if (job.records) showRun(job); else notice('El servidor no devolvió un identificador de trabajo.', true); return; }
    if (state.polling && state.job?.id === job.id) return;
    state.polling = true; updateJob(job);
    const jobId = job.id;
    let failures = 0;
    while (state.job?.id === jobId) {
      try {
        const current = await api('/api/jobs/' + encodeURIComponent(jobId)); updateJob(current); failures = 0;
        if (!isActive(current)) {
          state.polling = false; $('capture-button').disabled = false; $('auto-button').disabled = false; $('active-job').hidden = true;
          if (current.run_id) {
            const run = await api('/api/runs/' + encodeURIComponent(current.run_id));
            const profile = state.jobProfile || (run.collection?.incremental || /capture/i.test(current.kind || current.type || '') ? run.profile : null);
            if (profile) await loadProfile(profile, { fill: false }); else showRun(run);
          }
          state.jobProfile = null;
          await refreshState({ quiet: true });
          if (current.url) { showPublishResult(current.url); toast('Informe publicado.'); }
          else if (current.publish_error) showPublishResult(null, 'La captura terminó, pero no se pudo publicar: ' + current.publish_error);
          if (['failed', 'error'].includes(current.status)) notice(current.error || current.message || 'El trabajo terminó con un error.', true);
          else if (current.status === 'needs_attention') notice(current.message || 'El navegador necesita tu atención. Revisa la sesión y vuelve a iniciar la captura.');
          else if (['cancelled', 'canceled'].includes(current.status)) notice('Procesamiento cancelado. Las evidencias ya guardadas siguen disponibles.');
          else { notice(''); toast('Exploración actualizada.'); }
          return;
        }
      } catch (error) {
        failures++;
        $('job-message').textContent = 'Se perdió la conexión. Intentando recuperar el estado…';
        if (failures >= 5) { state.polling = false; $('capture-button').disabled = false; $('auto-button').disabled = false; $('active-job').hidden = true; notice('No se pudo recuperar el estado del trabajo. Actualiza las exploraciones para volver a conectarte. ' + error.message, true); return; }
      }
      await new Promise((resolve) => setTimeout(resolve, failures ? 4000 : 1800));
    }
    state.polling = false;
  }

  $('capture-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    notice(''); $('capture-button').disabled = true;
    try { const payload = profilePayload(); await saveProfile(); const job = await api('/api/capture', { method: 'POST', body: payload }); state.jobProfile = payload.profile; startPolling(job); }
    catch (error) { notice(error.message, true); $('capture-button').disabled = false; }
  });
  $('auto-button').addEventListener('click', () => busy($('auto-button'), async () => {
    notice(''); $('publish-result').hidden = true;
    const profile = $('profile-input').value.trim();
    if (!profile) throw new Error('Escribe un usuario o una URL de perfil de TikTok.');
    const sources = [...document.querySelectorAll('input[name="source"]:checked')].map((input) => input.value);
    if (!sources.length) throw new Error('Selecciona al menos una sección para capturar.');
    await saveProfile();
    const payload = { ...profilePayload(), publish: false };
    const job = await api('/api/auto', { method: 'POST', body: payload });
    state.jobProfile = handle(profile);
    startPolling(job);
  }));
  $('capture-mode').addEventListener('change', updateCaptureMode);
  $('capture-depth').addEventListener('change', updateCaptureMode);
  $('preflight-button').addEventListener('click', () => busy($('preflight-button'), async () => {
    $('preflight-result').textContent = 'Inspeccionando contenido visible; sin guardar imágenes ni analizar…';
    try {
      const result = await api('/api/preflight', { method: 'POST', body: profilePayload() });
      const sources = Object.entries(result.sources).map(([source, item]) => `${sourceNames[source]}: ${item.observed_minimum === undefined ? item.reason || item.status : 'al menos ' + item.observed_minimum + ' enlaces visibles'}`);
      const range = result.options.duration_minutes;
      $('preflight-result').textContent = sources.join(' · ') + `. Estimación ${range[0]}–${range[1]} minutos. ${result.options.duration_basis} ${result.options.coverage}`;
    } finally { await refreshState({ quiet: true }); }
  }));
  $('capture-headless').addEventListener('change', updateCaptureMode);
  $('max-items').addEventListener('change', updateCaptureMode);
  $('ai-provider').addEventListener('change', updateAiProviderFields);
  $('ai-settings-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    await busy($('save-ai-settings'), async () => {
      const body = { provider: $('ai-provider').value, model: $('ai-model').value.trim() || 'gemini-3.5-flash-lite', auto_visual: $('ai-auto-visual').checked, max_per_run: Number($('ai-max-per-run').value) };
      if ($('ai-key').value.trim()) body.api_key = $('ai-key').value.trim();
      try { const response = await api('/api/ai/settings', { method: 'PUT', body }); renderAiSettings(response); toast('Ajustes de interpretación guardados.'); }
      finally { $('ai-key').value = ''; }
    });
  });
  $('delete-ai-key').addEventListener('click', async () => { await busy($('delete-ai-key'), async () => { await api('/api/ai/key', { method: 'DELETE' }); $('ai-key').value = ''; await refreshAiSettings(); toast(state.ai?.has_api_key ? 'Clave guardada borrada. Sigue disponible la clave del entorno.' : 'Clave guardada borrada.'); }); $('delete-ai-key').disabled = !state.ai?.has_api_key; });
  $('save-profile').addEventListener('click', () => busy($('save-profile'), async () => { await saveProfile(); notice(''); toast('Perfil y preferencias guardados.'); }));
  $('view-profile').addEventListener('click', () => busy($('view-profile'), () => loadProfile(state.run.profile)));
  $('profile-input').addEventListener('change', () => {
    const profile = handle($('profile-input').value).toLowerCase();
    const config = list(state.app?.profiles).find((item) => handle(item.profile).toLowerCase() === profile);
    if (config) fillProfile(config);
    else if (profile !== String(state.formProfile || '').toLowerCase()) { state.formProfile = null; $('profile-label').value = ''; $('profile-notes').value = ''; $('profile-tags').value = ''; $('saved-profile-status').textContent = ''; $('profile-avatar-preview').hidden = true; }
  });
  $('profile-input').addEventListener('input', () => {
    if (state.formProfile && handle($('profile-input').value).toLowerCase() !== state.formProfile.toLowerCase()) { state.formProfile = null; $('profile-label').value = ''; $('profile-notes').value = ''; $('profile-tags').value = ''; $('saved-profile-status').textContent = ''; $('profile-avatar-preview').hidden = true; }
  });
  $('profile-avatar-file').addEventListener('change', async (event) => {
    const file = event.target.files[0]; if (!file) return;
    try { await saveProfile(); const data = new FormData(); data.append('file', file); await api(profileEndpoint($('profile-input').value, '/avatar'), { method: 'POST', body: data }); const config = await api(profileEndpoint($('profile-input').value)); fillProfile(config); await refreshState({ quiet: true }); toast('Foto del perfil guardada.'); }
    catch (error) { notice(error.message, true); } finally { event.target.value = ''; }
  });
  $('session-button').addEventListener('click', () => busy($('session-button'), async () => { const result = await api('/api/session/open', { method: 'POST', body: { profile: $('profile-input').value.trim(), session_mode: $('session-mode').value } }); notice(result.status || 'Navegador abierto.'); await refreshState({ quiet: true }); }));
  $('login-button').addEventListener('click', () => busy($('login-button'), async () => { $('session-mode').value = 'logged_in'; const result = await api('/api/session/login', { method: 'POST', body: { profile: $('profile-input').value.trim(), session_mode: 'logged_in' } }); notice(result.message || 'Inicia sesión manualmente, cierra esa ventana y vuelve a capturar.'); await refreshState({ quiet: true }); }));
  $('close-session').addEventListener('click', () => busy($('close-session'), async () => { await api('/api/session/close', { method: 'POST' }); await refreshState(); toast('Navegador cerrado. La sesión guardada permanece en tu equipo.'); }));
  $('refresh-state').addEventListener('click', () => busy($('refresh-state'), async () => { await refreshState(); }));
  $('cancel-job').addEventListener('click', () => busy($('cancel-job'), async () => { if (state.job?.id) { await api('/api/jobs/' + encodeURIComponent(state.job.id) + '/cancel', { method: 'POST' }); $('job-message').textContent = 'Cancelación solicitada. Esperando al proceso actual…'; } }));
  $('demo-button').addEventListener('click', () => busy($('demo-button'), async () => { notice(''); const run = await api('/api/demo', { method: 'POST' }); showRun(run); await refreshState({ quiet: true }); $('report').scrollIntoView({ behavior: 'smooth', block: 'start' }); }));
  $('import-file').addEventListener('change', async (event) => {
    const file = event.target.files[0]; if (!file) return;
    try {
      notice('Importando evidencias…');
      const parsed = JSON.parse(await file.text()); const records = Array.isArray(parsed) ? parsed : parsed.records;
      if (!Array.isArray(records)) throw new Error('El archivo debe incluir una lista de registros o un objeto con la propiedad records.');
      const profile = parsed.profile || $('profile-input').value.trim() || 'importado';
      const run = await api('/api/import', { method: 'POST', body: { profile, records, ...(parsed.collection ? { collection: parsed.collection } : {}) } }); showRun(run); notice(''); await refreshState({ quiet: true }); toast('Evidencias importadas.');
    } catch (error) { notice('No se pudo importar el archivo. ' + error.message, true); } finally { event.target.value = ''; }
  });
  $('analyze-button').addEventListener('click', () => busy($('analyze-button'), async () => { const run = await api(runEndpoint('/analyze'), { method: 'POST' }); showRun(run); toast('Análisis actualizado.'); }));
  $('evidence-search').addEventListener('input', () => { state.page = 1; renderEvidence(); });
  $('evidence-source').addEventListener('change', () => { state.page = 1; renderEvidence(); });
  $('clear-filter').addEventListener('click', () => { state.filterIds = null; state.filterLabel = ''; state.page = 1; renderEvidence(); });
  document.querySelectorAll('[data-timeline]').forEach((button) => button.addEventListener('click', () => { state.timeline = button.dataset.timeline; renderTimeline(); }));
  document.querySelectorAll('.primary-nav a').forEach((link) => link.addEventListener('click', () => { document.querySelectorAll('.primary-nav a').forEach((item) => item.classList.toggle('active', item === link)); if (link.hash !== '#overview' && !state.run) toast('Inicia una exploración o carga el ejemplo ficticio para ver esta sección.'); }));
  $('evidence-dialog').addEventListener('click', (event) => { if (event.target === $('evidence-dialog')) { const bounds = event.target.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) event.target.close(); } });
  async function initialize() {
    refreshAiSettings();
    const data = await refreshState();
    if (data && !state.run) {
      try { if (list(data.profiles).length) await loadProfile(data.profiles[0].profile); else if (list(data.runs).length) await loadRun(data.runs[0].id); } catch (error) { notice('No se pudo abrir el último perfil. ' + error.message, true); }
    }
  }
  initialize();
})();

// Supported URL -> scope/depth -> analyze -> executive report workflow.
(() => {
  'use strict';
  const $ = (id) => document.getElementById('guided-' + id);
  const names = {uploads: 'Publicaciones', reposts: 'Compartidos', likes: 'Me gusta'};
  const depthLimits = {quick: [5, 3], deep: [60, 25], all: [200, 50]};
  const active = (job) => job && ['pending', 'running'].includes(job.status);
  let job = null;
  let polling = false;
  let currentRun = null;
  const rows = (value) => Array.isArray(value) ? value : [];
  const node = (tag, text, className = '') => {
    const result = document.createElement(tag);
    result.textContent = String(text ?? '');
    result.className = className;
    return result;
  };
  function message(text, error = false) {
    $('notice').hidden = !text;
    $('notice').textContent = text;
    $('notice').classList.toggle('error', error);
  }
  async function api(path, method = 'GET', body) {
    const response = await fetch(path, {method, ...(body ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})});
    let result;
    try { result = await response.json(); }
    catch { throw new Error(`El servidor no devolvió una respuesta válida (HTTP ${response.status}).`); }
    if (!response.ok) {
      const detail = result.detail;
      throw new Error(typeof detail === 'string' ? detail : detail?.message || rows(detail).map((entry) => entry.msg).join('; ') || 'Solicitud no disponible.');
    }
    return result;
  }
  function options() {
    const sources = [...document.querySelectorAll('input[name="guided-source"]:checked')].map((input) => input.value);
    if (!sources.length) throw new Error('Selecciona al menos una fuente.');
    if ($('date').value === 'last30days' && (sources.length !== 1 || sources[0] !== 'uploads')) throw new Error('Últimos 30 días solo admite publicaciones propias.');
    const depth = $('depth').value;
    const [items, rounds] = depthLimits[depth] || [Number($('items').value), Number($('scrolls').value)];
    if (!Number.isInteger(items) || items < 1 || items > 200 || !Number.isInteger(rounds) || rounds < 1 || rounds > 50) {
      throw new Error('Personalizada requiere 1–200 elementos y 1–50 rondas, ambos enteros.');
    }
    return {profile: $('url').value.trim(), sources, depth,
      max_items: items, scroll_rounds: rounds,
      date_filter: $('date').value, mode: $('mode').value, session_mode: 'logged_in',
      headless: false, wait_captcha: false, visual_limit: Number($('ai-limit').value)};
  }
  function estimate() {
    const preset = depthLimits[$('depth').value];
    const [items, rounds] = preset || [Number($('items').value), Number($('scrolls').value)];
    const sources = document.querySelectorAll('input[name="guided-source"]:checked').length;
    $('custom').hidden = Boolean(preset);
    ['items', 'scrolls'].forEach((id) => {
      $(id).disabled = Boolean(preset);
      $(id).required = !preset;
    });
    if (!preset && (!Number.isInteger(items) || items < 1 || items > 200 || !Number.isInteger(rounds) || rounds < 1 || rounds > 50)) {
      $('estimate').textContent = 'Introduce límites personalizados válidos: 1–200 elementos y 1–50 rondas. No se ajustan automáticamente.';
      return;
    }
    const low = Math.max(1, Math.round(sources * items * 8 / 60));
    const high = Math.max(2, Math.round(sources * items * 45 / 60 + rounds));
    $('estimate').textContent = `Hasta ${items} trabajos y ${rounds} rondas por fuente. Duración orientativa: ${low}–${high} min, sin esperas manuales. Inspección visible automática; mínimos observados, nunca totales históricos.`;
  }
  function access(reason) {
    $('access').hidden = !reason;
    $('access-reason').textContent = reason || '';
  }
  function evidenceLink(url, label) {
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== 'https:' || !['www.tiktok.com', 'tiktok.com'].includes(parsed.hostname)) return node('span', label);
      const link = node('a', label, 'text-button');
      link.href = parsed.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
      return link;
    } catch { return node('span', label); }
  }
  function render(run) {
    if (run.is_demo || run.collection?.method === 'synthetic_demo' || run.profile === 'demo_ficticia') throw new Error('Los datos ficticios no se abren como informes reales.');
    currentRun = run;
    const report = run.analysis?.executive;
    $('report').hidden = false;
    $('report-heading').textContent = 'Informe ejecutivo · @' + run.profile;
    const outcome = effectiveStatus(run);
    $('report-meta').textContent = `${run.records?.length || 0} evidencias observadas · estado: ${outcome} · ${run.created_at || ''}`;
    const base = '/api/runs/' + encodeURIComponent(run.id) + '/export?format=';
    $('export-md').href = base + 'md'; $('export-json').href = base + 'json';
    const target = $('executive'); target.replaceChildren();
    if (outcome !== 'completed') target.append(node('p', outcome === 'observed_empty'
      ? 'Sin evidencia en el recorrido observado. No se afirma ausencia de actividad ni historial vacío.'
      : 'Informe parcial o no disponible: este intento no produjo una muestra completa. Las fuentes bloqueadas o no evaluadas no se interpretan como vacías.', 'notice'));
    const blockers = [...rows(run.collection?.blockers), ...rows(run.collection?.issues)];
    const reasons = [...new Set([run.collection?.last_capture_reason, ...blockers.map((issue) => issue.message || issue.reason || issue.code)].filter(Boolean))];
    reasons.forEach((reason) => target.append(node('p', reason, 'notice')));
    target.append(node('p', report?.summary || 'No hay evidencia suficiente para un resumen de contenido.', 'summary-text'));
    const coverage = node('ul', '', 'thematic-profile');
    const coverageRows = rows(report?.coverage).slice();
    const requested = rows(run.collection?.requested_sources).length ? run.collection.requested_sources : rows(run.collection?.options?.sources);
    requested.forEach((source) => {
      if (coverageRows.some((item) => item.source === source)) return;
      const detail = run.collection?.sources?.[source] || {};
      coverageRows.push({source, observed: rows(run.records).filter((record) => record.source === source).length,
        analyzed: 0, visual_analyzed: 0, unknown_publication_dates: 0, excluded_by_date: 0, unknown_date_filter: 0,
        status: detail.status || 'unassessed', reason: detail.reason || 'Disponibilidad no evaluada; no se afirma ausencia de contenido.'});
    });
    coverageRows.forEach((item) => coverage.append(node('li', `${names[item.source] || item.source}: ${item.observed} recopilados · ${item.analyzed} con texto analizable · ${item.visual_analyzed} con visión · ${item.status}. ${item.reason || ''} ${item.unknown_publication_dates} sin fecha original; ${item.excluded_by_date} excluidos y ${item.unknown_date_filter} sin fecha para el filtro. Total histórico desconocido.`)));
    target.append(coverage);
    rows(report?.themes).forEach((theme) => {
      const line = node('p', `${theme.name} (${theme.count}): `, 'summary-text');
      rows(theme.evidence).forEach((item) => { line.append(evidenceLink(item.url, item.id + ' ↗'), document.createTextNode(' ')); });
      target.append(line);
    });
    target.append(node('p', report?.date_provenance || 'Las fechas de publicación y las acciones se conservan por separado.', 'muted'));
    const limits = node('details', '', 'guided-details');
    limits.append(node('summary', 'Limitaciones y próximos pasos'));
    const list = node('ul', '', 'thematic-profile');
    [...rows(report?.limitations), ...rows(report?.next_steps)].forEach((text) => list.append(node('li', text)));
    limits.append(list); target.append(limits);
    const details = $('evidence'); details.replaceChildren();
    reasons.forEach((reason) => details.append(node('p', reason, 'muted')));
    Object.entries(run.collection?.sources || {}).forEach(([source, info]) => {
      if (info.method === 'tiktok_web_response') {
        details.append(node('p', `${names[source] || source}: datos de TikTok y portadas · ${info.pages_observed || 0} páginas observadas.`, 'muted'));
      } else if (info.structured_fallback) {
        details.append(node('p', `${names[source] || source}: se utilizó la captura visual porque no se obtuvo una respuesta de datos válida.`, 'muted'));
      }
    });
    const tabs = rows(run.collection?.detected_tabs || run.collection?.preview?.detected_tabs);
    if (tabs.length) details.append(node('p', 'Pestañas observadas: ' + tabs.map((tab) => tab.label || tab.selector).join(' · '), 'muted'));
    rows(run.records).forEach((record) => {
      const entry = node('details', '', 'guided-record');
      entry.append(node('summary', `${names[record.source] || record.source}: ${record.caption || record.id}`));
      entry.append(evidenceLink(record.url, 'Evidencia original ↗'));
      entry.append(node('p', `Fecha original: ${record.published_at || 'desconocida'} · fecha de acción: ${record.interaction_at || 'desconocida'} · filtro: ${record.date_filter_status || 'included'}`, 'muted'));
      entry.append(node('p', record.visual_summary || '', 'summary-text'));
      // Only guarded frames may be displayed, never legacy or diagnostic images.
      rows(record.frames).filter((path) => {
        const info = record.frame_quality?.[path];
        return info?.status === 'verified' && (info.source === 'user_upload' || info.overlay_guard_version === 1 || (info.source === 'tiktok_cdn' && info.validation_version === 1));
      }).slice(0, 7).forEach((path) => {
        if (typeof path !== 'string' || !path.startsWith('artifacts/') || path.split('/').includes('..')) return;
        const image = document.createElement('img');
        image.src = '/data/' + path.split('/').map(encodeURIComponent).join('/');
        image.alt = 'Recorte verificado de la evidencia'; image.loading = 'lazy';
        entry.append(image);
      });
      details.append(entry);
    });
  }
  function effectiveStatus(run) {
    if (run.status !== 'completed' || rows(run.records).length) return run.status;
    const requested = rows(run.collection?.requested_sources).length ? run.collection.requested_sources : rows(run.collection?.options?.sources);
    return requested.length && requested.every((source) => run.collection?.sources?.[source]?.status === 'observed_empty') ? 'observed_empty' : 'unavailable';
  }
  function outcomeTitle(status) {
    return {completed: 'Análisis terminado', observed_empty: 'Sin evidencia en este recorrido',
      unavailable: 'Evidencia no disponible', partial: 'Análisis parcial', cancelled: 'Análisis detenido',
      needs_attention: 'Análisis bloqueado · requiere atención', failed: 'Análisis fallido'}[status] || 'Análisis detenido / parcial';
  }
  async function refresh() {
    const state = await api('/api/state');
    $('connection').textContent = 'Servidor local conectado';
    $('browser').textContent = state.browser?.open ? state.browser.status : 'Chrome de Windows · HuellaBrowser · se conectará automáticamente al analizar.';
    const history = $('history'); history.replaceChildren();
    rows(state.runs).filter((run) => !run.is_demo && run.profile !== 'demo_ficticia').slice(0, 30).forEach((run) => {
      const status = run.status === 'completed' && !run.count ? 'unavailable' : run.status;
      const button = node('button', `@${run.profile} · ${run.count} evidencias · ${status} · ${run.created_at}`, 'run-item');
      button.type = 'button';
      button.addEventListener('click', async () => {
        try { render(await api('/api/runs/' + encodeURIComponent(run.id))); } catch (error) { message(error.message, true); }
      }); history.append(button);
    });
    // Never select historical or synthetic reports automatically.
    if (!job && !polling) {
      const running = rows(state.jobs).find(active);
      if (running) poll(running);
    }
    return state;
  }
  async function poll(initial) {
    if (polling) return;
    polling = true; job = initial;
    $('analyze').disabled = true; $('progress').hidden = false; $('open').disabled = true;
    try {
      while (true) {
        job = await api('/api/jobs/' + encodeURIComponent(initial.id));
        $('job-title').textContent = active(job) ? 'Analizando…' : 'Comprobando el resultado…';
        $('job-message').textContent = job.message || '';
        $('job-progress').value = job.progress || 0;
        if (job.preview) {
          const counts = Object.entries(job.preview.sources || {}).map(([source, info]) => `${names[source]}: ${info.observed_minimum ?? info.status ?? 'no disponible'}`).join(' · ');
          $('job-message').textContent += ' Mínimos visibles: ' + counts;
        }
        if (!active(job)) break;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      if (job.run_id) render(await api('/api/runs/' + encodeURIComponent(job.run_id)));
      const outcome = job.run_id ? effectiveStatus(currentRun) : job.status;
      $('job-title').textContent = outcomeTitle(outcome);
      if (job.status === 'needs_attention' || job.status === 'failed') access(job.message || 'Revisa el navegador dedicado y vuelve a analizar.');
      message(outcome !== 'completed' ? currentRun?.collection?.last_capture_reason || 'Este intento no es un análisis exitoso; revisa los estados de cobertura y los bloqueos.' : job.message, outcome !== 'completed');
    } catch (error) { message(error.message, true); }
    finally {
      polling = false; job = null; $('analyze').disabled = false; $('open').disabled = false;
      $('cancel').hidden = true;
      try { await refresh(); } catch { $('connection').textContent = 'Servidor local no disponible'; }
    }
  }
  $('form').addEventListener('submit', async (event) => {
    event.preventDefault(); if (polling) return;
    $('analyze').disabled = true;
    try {
      const body = options();
      message(''); access(''); $('report').hidden = true; currentRun = null; $('cancel').hidden = false;
      await poll(await api('/api/auto', 'POST', body));
    } catch (error) { message(error.message, true); }
    finally { $('analyze').disabled = false; }
  });
  $('open').addEventListener('click', async () => {
    if (polling || active(job)) return;
    $('open').disabled = true;
    message('Conectando con el navegador dedicado de Windows…');
    try {
      const result = await api('/api/session/login', 'POST', {profile: currentRun?.profile || $('url').value.trim(), session_mode: 'logged_in'});
      message(result.message); await refresh();
    } catch (error) { message(error.message, true); access(error.message); }
    finally { $('open').disabled = false; }
  });
  $('cancel').addEventListener('click', async () => {
    if (!job) return;
    try { await api('/api/jobs/' + encodeURIComponent(job.id) + '/cancel', 'POST'); message('Detención solicitada; se conserva la evidencia obtenida.'); }
    catch (error) { message(error.message, true); }
  });
  $('ai-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const body = {provider: $('provider').value, model: $('model').value, auto_visual: $('ai-auto').checked, max_per_run: Number($('ai-limit').value)};
      if ($('key').value) body.api_key = $('key').value;
      const result = await api('/api/ai/settings', 'PUT', body);
      $('key').value = '';
      $('ai-status').textContent = `Ajustes guardados · modelo efectivo: ${result.model} · ${result.has_api_key ? 'clave disponible, no mostrada' : 'sin clave'}.`;
    } catch (error) { message(error.message, true); }
  });
  ['depth', 'items', 'scrolls'].forEach((id) => $(id).addEventListener('change', estimate));
  document.querySelectorAll('input[name="guided-source"]').forEach((input) => input.addEventListener('change', estimate));
  estimate();
  refresh().then((state) => {
    const ai = state.ai || {};
    $('provider').value = ai.provider || 'disabled'; $('model').value = ai.model || 'gemini-3.5-flash-lite';
    $('ai-auto').checked = ai.auto_visual === true; $('ai-limit').value = ai.max_per_run || 1;
    $('ai-status').textContent = ai.has_api_key ? 'Clave disponible desde configuración; no se muestra.' : 'Sin clave: OCR local disponible.';
  }).catch((error) => { $('connection').textContent = 'Servidor local no disponible'; message(error.message, true); });
})();
