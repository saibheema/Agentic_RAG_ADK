const state = {
  apiBase: localStorage.getItem('apiBase') || '',  // empty = same-origin (works when served by run_local.py)
  appName: localStorage.getItem('appName') || 'agentic_rag',
  userId: localStorage.getItem('userId') || 'web-user',
  sessionId: '',
  dbAlias: localStorage.getItem('dbAlias') || '',
  // RBAC context — forwarded to the agent via session state
  replevel: localStorage.getItem('replevel') || '1',          // 1=Internal, 3=Manager, 5=Salesperson
  salespersonId: localStorage.getItem('salespersonId') || '', // e.g. F1010
  roleName: localStorage.getItem('roleName') || '',           // display role name
};

const el = {
  apiBase: document.getElementById('apiBase'),
  appName: document.getElementById('appName'),
  userId: document.getElementById('userId'),
  sessionId: document.getElementById('sessionId'),
  dbAlias: document.getElementById('dbAlias'),
  dbBadge: document.getElementById('dbBadge'),
  // RBAC / settings page
  replevel: document.getElementById('replevel'),
  salespersonId: document.getElementById('salespersonId'),
  roleName: document.getElementById('roleName'),
  rbacApplyBtn: document.getElementById('rbacApplyBtn'),
  settingsSalespersonRow: document.getElementById('settingsSalespersonRow'),
  settingsSpLabel: document.getElementById('settingsSpLabel'),
  roleStatusDot: document.getElementById('roleStatusDot'),
  roleStatusLabel: document.getElementById('roleStatusLabel'),
  chat: document.getElementById('chat'),
  prompt: document.getElementById('prompt'),
  chatForm: document.getElementById('chatForm'),
  newSessionBtn: document.getElementById('newSessionBtn'),
  clearChatBtn: document.getElementById('clearChatBtn'),
  sendBtn: document.getElementById('sendBtn'),
  msgTemplate: document.getElementById('msgTemplate'),
};

function updateRbacBar() {
  // Read the LIVE dropdown value so the row shows immediately when the user
  // changes Role Level — before they click Apply.
  const lvl = (el.replevel ? el.replevel.value : null) || state.replevel || '1';
  const ROLE_COLORS = { '1': '#22c55e', '3': '#6366f1', '5': '#f59e0b' };
  const ROLE_LABELS = { '1': 'Internal', '3': 'Manager', '5': 'Salesperson' };

  // Settings page: show/hide salesperson row
  if (el.settingsSalespersonRow) {
    el.settingsSalespersonRow.style.display = (lvl === '3' || lvl === '5') ? '' : 'none';
  }

  // Update label (Manager ID vs Salesperson ID) and input placeholder
  if (el.settingsSpLabel) {
    el.settingsSpLabel.textContent = lvl === '3' ? 'Manager ID' : 'Salesperson ID';
  }
  if (el.salespersonId && el.salespersonId.tagName === 'INPUT') {
    el.salespersonId.placeholder = lvl === '3' ? 'e.g. F1010 (manager prefix)' : 'e.g. F1010';
  }

  // Update sidebar role status indicator
  if (el.roleStatusDot) el.roleStatusDot.style.background = ROLE_COLORS[lvl] || '#22c55e';
  if (el.roleStatusLabel) {
    const who = (lvl !== '1' && state.salespersonId) ? ` · ${state.salespersonId}` : '';
    el.roleStatusLabel.textContent = (ROLE_LABELS[lvl] || 'Internal') + who;
  }
}

function syncInputs() {
  el.apiBase.value = state.apiBase;
  el.appName.value = state.appName;
  el.userId.value = state.userId;
  el.sessionId.value = state.sessionId;
  if (el.dbAlias && state.dbAlias) el.dbAlias.value = state.dbAlias;
  // Settings panel RBAC fields
  if (el.replevel) el.replevel.value = state.replevel;
  if (el.salespersonId) el.salespersonId.value = state.salespersonId;
  if (el.roleName) el.roleName.value = state.roleName;
  updateDbBadge();
  updateRbacBar();
}

function updateDbBadge() {
  if (!el.dbBadge) return;
  const selected = el.dbAlias ? el.dbAlias.options[el.dbAlias.selectedIndex] : null;
  const dbtype = selected && selected.value ? (selected.dataset.dbtype || '') : '';

  // Hide the old text badge — the topbar select is the primary indicator
  el.dbBadge.style.display = 'none';

  // Color-code the topbar-db container via data attribute
  const dbContainer = el.dbAlias ? el.dbAlias.closest('.topbar-db') : null;
  if (dbContainer) {
    dbContainer.dataset.dbtype = dbtype;
  }
}

function saveSettings() {
  state.apiBase = el.apiBase.value.trim().replace(/\/$/, '');
  state.appName = el.appName.value.trim();
  state.userId = el.userId.value.trim();
  state.dbAlias = el.dbAlias ? el.dbAlias.value : state.dbAlias;
  state.replevel = el.replevel ? el.replevel.value : state.replevel;
  state.salespersonId = el.salespersonId ? el.salespersonId.value.trim() : state.salespersonId;
  state.roleName = el.roleName ? el.roleName.value.trim() : state.roleName;
  localStorage.setItem('apiBase', state.apiBase);
  localStorage.setItem('appName', state.appName);
  localStorage.setItem('userId', state.userId);
  localStorage.setItem('dbAlias', state.dbAlias);
  localStorage.setItem('replevel', state.replevel);
  localStorage.setItem('salespersonId', state.salespersonId);
  localStorage.setItem('roleName', state.roleName);
}

function appendMessage(kind, label, bodyRenderer) {
  _clearWelcome();
  hideTyping();
  const node = el.msgTemplate.content.firstElementChild.cloneNode(true);
  node.classList.add(kind);
  // data-label lets CSS style system/error/agent variants
  node.dataset.label = label.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  // set avatar glyph
  const avatar = node.querySelector('.msg-avatar');
  if (avatar) {
    if (kind === 'user') avatar.textContent = 'U';
    else if (label === 'Error') avatar.textContent = '⚠';
    else if (label === 'System') avatar.textContent = 'ℹ';
    else avatar.textContent = '✦';
  }
  node.querySelector('.msg-meta').textContent = label;
  const body = node.querySelector('.msg-body');
  bodyRenderer(body);

  // Copy button on substantive assistant answers
  if (kind === 'agent' && label === 'Assistant') {
    const actions = document.createElement('div');
    actions.className = 'msg-actions';
    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'msg-action-btn';
    copyBtn.innerHTML = '\u29c9 Copy';
    copyBtn.addEventListener('click', () => {
      const text = body.innerText.trim();
      navigator.clipboard.writeText(text).then(() => {
        copyBtn.innerHTML = '\u2713 Copied';
        setTimeout(() => { copyBtn.innerHTML = '\u29c9 Copy'; }, 1500);
      }).catch(() => {});
    });
    actions.appendChild(copyBtn);
    node.querySelector('.msg-content').appendChild(actions);
  }

  el.chat.appendChild(node);
  el.chat.scrollTop = el.chat.scrollHeight;
}

/* lightweight markdown → safe HTML -------------------------------- */
(function configureMarked() {
  if (typeof marked === 'undefined') return;
  marked.setOptions({
    breaks: true,       // single newline → <br>
    gfm: true,          // GitHub-Flavored Markdown
  });
})();

function renderText(target, text) {
  if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
    const raw = marked.parse(String(text));
    // Wrap bare <table> in scroll container before sanitising
    const wrapped = raw.replace(/<table/g, '<div class="table-scroll"><table').replace(/<\/table>/g, '</table></div>');
    const clean = DOMPurify.sanitize(wrapped, { USE_PROFILES: { html: true } });
    const wrapper = document.createElement('div');
    wrapper.className = 'md-body';
    wrapper.innerHTML = clean;
    target.appendChild(wrapper);
  } else {
    // fallback: plain text
    const p = document.createElement('p');
    p.textContent = text;
    target.appendChild(p);
  }
}

function renderTable(target, rows) {
  if (!rows.length || typeof rows[0] !== 'object') {
    const pre = document.createElement('code');
    pre.textContent = JSON.stringify(rows, null, 2);
    target.appendChild(pre);
    return;
  }

  // Wrap in scroll container so wide tables don't crush column widths
  const scroll = document.createElement('div');
  scroll.className = 'table-scroll';

  const table = document.createElement('table');
  const cols = Object.keys(rows[0]);
  const thead = document.createElement('thead');
  const trHead = document.createElement('tr');
  cols.forEach((col) => {
    const th = document.createElement('th');
    th.textContent = col;
    trHead.appendChild(th);
  });
  thead.appendChild(trHead);

  const tbody = document.createElement('tbody');
  rows.forEach((row) => {
    const tr = document.createElement('tr');
    cols.forEach((col) => {
      const td = document.createElement('td');
      td.textContent = String(row[col] ?? '');
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });

  table.appendChild(thead);
  table.appendChild(tbody);
  scroll.appendChild(table);
  target.appendChild(scroll);
}

/* ── Structured agent response (JSON output format) ──────── */

/**
 * Return true if text is the agent's structured JSON response envelope.
 * Requires at least the 'sql_query' key signature.
 */
function _isStructuredResponse(text) {
  if (!text || typeof text !== 'string') return false;
  const trimmed = text.trim();
  if (!trimmed.startsWith('{')) return false;
  try {
    const json = JSON.parse(trimmed);
    return (
      json !== null &&
      typeof json === 'object' &&
      !Array.isArray(json) &&
      ('sql_query' in json || 'greetings' in json || ('error' in json && !('ok' in json)))
    );
  } catch {
    return false;
  }
}

/**
 * Render a table with type-aware cell formatting using columns_meta.
 * @param {HTMLElement} target
 * @param {Array<object>} rows
 * @param {Array<{key:string, header:string, type:string}>} columnsMeta
 */
function renderTypedTable(target, rows, columnsMeta) {
  if (!rows || !rows.length) return;

  const scroll = document.createElement('div');
  scroll.className = 'table-scroll';

  const table = document.createElement('table');
  table.className = 'structured-table';

  // Header
  const thead = document.createElement('thead');
  const trHead = document.createElement('tr');
  columnsMeta.forEach((col) => {
    const th = document.createElement('th');
    th.textContent = col.header;
    th.dataset.type = col.type;
    trHead.appendChild(th);
  });
  thead.appendChild(trHead);

  // Body
  const tbody = document.createElement('tbody');
  rows.forEach((row) => {
    const tr = document.createElement('tr');
    columnsMeta.forEach((col) => {
      const td = document.createElement('td');
      td.dataset.type = col.type;
      const val = row[col.key];
      if (col.type === 'currency' && val !== null && val !== undefined) {
        const num = parseFloat(val);
        td.textContent = isNaN(num) ? String(val) : '$' + num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        td.className = 'cell-currency';
      } else if (col.type === 'numeric') {
        const num = parseFloat(val);
        td.textContent = (val !== null && val !== undefined && !isNaN(num)) ? num.toLocaleString() : (val ?? '');
        td.className = 'cell-numeric';
      } else if (col.type === 'date') {
        td.textContent = (val !== null && val !== undefined) ? String(val).split('T')[0] : '';
      } else {
        td.textContent = String(val ?? '');
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });

  table.appendChild(thead);
  table.appendChild(tbody);
  scroll.appendChild(table);
  target.appendChild(scroll);
}

/* ── Zero-latency inline charts (pure SVG, no external library) ───────────── */

const _SVG_NS = 'http://www.w3.org/2000/svg';
const _CHART_PALETTE = ['#6366f1', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];

function _svg(tag, attrs, text) {
  const e = document.createElementNS(_SVG_NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
}

/** Measure rendered text width (px) using a shared canvas — pixel-accurate, no layout. */
let _measureCtx = null;
function _textWidth(str, font = '600 12px system-ui, -apple-system, sans-serif') {
  if (!_measureCtx) _measureCtx = document.createElement('canvas').getContext('2d');
  _measureCtx.font = font;
  return _measureCtx.measureText(String(str ?? '')).width;
}

/** Truncate a label to fit a max pixel width, adding an ellipsis. */
function _truncToWidth(str, maxW, font) {
  str = String(str ?? '');
  if (_textWidth(str, font) <= maxW) return str;
  let s = str;
  while (s.length > 1 && _textWidth(s + '\u2026', font) > maxW) s = s.slice(0, -1);
  return s + '\u2026';
}

/** "Nice" axis max for clean gridline ticks. */
function _niceMax(v) {
  if (v <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / pow;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * pow;
}

/** Compact human-readable value for chart labels/axes. */
function _fmtChartValue(v, type) {
  const n = Number(v);
  if (!isFinite(n)) return String(v ?? '');
  const abs = Math.abs(n);
  const unit = (x) => (abs >= 1e9 ? (x / 1e9).toFixed(1) + 'B'
    : abs >= 1e6 ? (x / 1e6).toFixed(1) + 'M'
    : abs >= 1e3 ? (x / 1e3).toFixed(1) + 'K'
    : x.toLocaleString('en-US', { maximumFractionDigits: 0 }));
  return type === 'currency' ? '$' + unit(n) : unit(n);
}

/**
 * Decide whether typed rows can be charted.
 * @returns {null | {kind:'bar'|'line', labelKey:string, valueCols:Array, valueType:string}}
 */
function detectChartable(rows, columnsMeta) {
  if (!Array.isArray(rows) || rows.length < 2 || rows.length > 60) return null;
  if (!Array.isArray(columnsMeta) || columnsMeta.length < 2) return null;

  const numericCols = columnsMeta.filter((c) => c.type === 'numeric' || c.type === 'currency');
  const labelCols   = columnsMeta.filter((c) => c.type !== 'numeric' && c.type !== 'currency');
  if (!numericCols.length || !labelCols.length) return null;

  const dateCol  = columnsMeta.find((c) => c.type === 'date');
  const labelCol = dateCol || labelCols[0];
  const valueCols = numericCols.slice(0, 2);

  // Primary value column must be fully numeric
  const primary = valueCols[0].key;
  if (!rows.every((r) => isFinite(Number(r[primary])))) return null;

  // Labels should be mostly distinct (avoid charting raw record dumps)
  const labels = rows.map((r) => String(r[labelCol.key] ?? ''));
  if (new Set(labels).size < Math.max(2, Math.floor(rows.length * 0.6))) return null;

  return { kind: dateCol ? 'line' : 'bar', labelKey: labelCol.key, valueCols, valueType: valueCols[0].type };
}

/** Horizontal bar chart — ideal for ranked categories (top N).
 * Rendered at exact pixel `width` so fonts stay crisp and consistent. */
function _buildBarChart(rows, spec, width) {
  const col    = spec.valueCols[0];
  const labels = rows.map((r) => String(r[spec.labelKey] ?? ''));
  const values = rows.map((r) => Number(r[col.key]) || 0);
  const maxAbs = Math.max(...values.map(Math.abs), 1);
  const axisMax = _niceMax(maxAbs);

  const W = Math.max(280, Math.round(width));
  const labelFont = '500 12px system-ui, -apple-system, sans-serif';
  const valueFont = '600 12px system-ui, -apple-system, sans-serif';

  // Label column: fit longest label but cap at 38% of width.
  const longestLabel = Math.min(
    Math.max(...labels.map((l) => _textWidth(l, labelFont)), 40) + 12,
    Math.floor(W * 0.38),
  );
  const valueW = Math.max(...values.map((v) => _textWidth(_fmtChartValue(v, col.type), valueFont)), 36) + 10;
  const padX = 6, padTop = 6, axisH = 22;

  // Adaptive row height: comfortable for few rows, compact for many — never cramped.
  const rowH = Math.max(20, Math.min(38, Math.round(360 / rows.length)));
  const gap  = rowH >= 30 ? 12 : rowH >= 24 ? 9 : 6;

  const labelW = longestLabel;
  const plotX  = padX + labelW;
  const plotW  = W - labelW - valueW - padX * 2;
  const height = padTop + rows.length * (rowH + gap) - gap + 8 + axisH;
  const baseY  = padTop + rows.length * (rowH + gap) - gap + 4;

  const svg = _svg('svg', { viewBox: `0 0 ${W} ${height}`, width: W, height, class: 'chart-svg' });

  // Vertical gridlines + axis ticks (4 steps to the nice max).
  for (let g = 0; g <= 4; g++) {
    const gv = (axisMax * g) / 4;
    const gx = plotX + (gv / axisMax) * plotW;
    svg.appendChild(_svg('line', { x1: gx, y1: padTop, x2: gx, y2: baseY, class: 'chart-grid' }));
    svg.appendChild(_svg('text', { x: gx, y: baseY + 15, class: 'chart-axis', 'text-anchor': 'middle' }, _fmtChartValue(gv, col.type)));
  }

  rows.forEach((r, i) => {
    const y = padTop + i * (rowH + gap);
    const v = values[i];
    const barLen = Math.max(2, (Math.abs(v) / axisMax) * plotW);
    const lblTxt = _truncToWidth(labels[i], labelW - 10, labelFont);
    const valTxt = _fmtChartValue(v, col.type);
    const lbl = _svg('text', { x: plotX - 8, y: y + rowH / 2 + 4, class: 'chart-label', 'text-anchor': 'end' }, lblTxt);
    lbl.appendChild(_svg('title', {}, `${labels[i]}: ${valTxt}`));
    svg.appendChild(lbl);
    svg.appendChild(_svg('rect', { x: plotX, y, width: plotW, height: rowH, rx: 5, class: 'chart-track' }));
    const bar = _svg('rect', { x: plotX, y, width: barLen, height: rowH, rx: 5, fill: _CHART_PALETTE[0], class: 'chart-bar' });
    bar.appendChild(_svg('title', {}, `${labels[i]}: ${valTxt}`));
    svg.appendChild(bar);
    // Place value inside the bar if it fits, else just after it.
    const valW = _textWidth(valTxt, valueFont);
    const inside = barLen > valW + 16;
    svg.appendChild(_svg('text', {
      x: inside ? plotX + barLen - 7 : plotX + barLen + 7,
      y: y + rowH / 2 + 4,
      class: inside ? 'chart-value chart-value-in' : 'chart-value',
      'text-anchor': inside ? 'end' : 'start',
    }, valTxt));
  });
  return svg;
}

/** Multi-series line chart — ideal for time series.
 * Rendered at exact pixel `width`; height adapts to the chat window. */
function _buildLineChart(rows, spec, width) {
  const labels = rows.map((r) => String(r[spec.labelKey] ?? ''));
  const W = Math.max(280, Math.round(width));
  const height = Math.round(Math.max(200, Math.min(320, W * 0.45)));
  const padL = 56, padR = 16, padT = 16, padB = 40;
  const plotW = W - padL - padR;
  const plotH = height - padT - padB;

  const series = spec.valueCols.map((c, ci) => ({
    header: c.header, type: c.type, color: _CHART_PALETTE[ci % _CHART_PALETTE.length],
    values: rows.map((r) => Number(r[c.key]) || 0),
  }));
  const all = series.flatMap((s) => s.values);
  const rawMax = Math.max(...all, 0);
  const minV = Math.min(...all, 0);
  const maxV = minV < 0 ? rawMax : _niceMax(rawMax);
  const range = (maxV - minV) || 1;

  const svg = _svg('svg', { viewBox: `0 0 ${W} ${height}`, width: W, height, class: 'chart-svg' });
  const x = (i) => padL + (rows.length === 1 ? plotW / 2 : (i / (rows.length - 1)) * plotW);
  const y = (v) => padT + plotH - ((v - minV) / range) * plotH;

  for (let g = 0; g <= 4; g++) {
    const gv = minV + (range * g) / 4;
    const gy = y(gv);
    svg.appendChild(_svg('line', { x1: padL, y1: gy, x2: W - padR, y2: gy, class: 'chart-grid' }));
    svg.appendChild(_svg('text', { x: padL - 8, y: gy + 4, class: 'chart-axis', 'text-anchor': 'end' }, _fmtChartValue(gv, spec.valueType)));
  }

  // X labels: thin out so they never overlap, based on available width.
  const labelFont = '400 11px system-ui, -apple-system, sans-serif';
  const maxLabels = Math.max(2, Math.floor(plotW / 70));
  const step = Math.max(1, Math.ceil(rows.length / maxLabels));
  labels.forEach((l, i) => {
    if (i % step !== 0 && i !== rows.length - 1) return;
    const t = _truncToWidth(l, 64, labelFont);
    svg.appendChild(_svg('text', { x: x(i), y: height - padB + 20, class: 'chart-axis', 'text-anchor': 'middle' }, t));
  });

  series.forEach((s) => {
    const d = s.values.map((v, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
    svg.appendChild(_svg('path', { d, fill: 'none', stroke: s.color, 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    s.values.forEach((v, i) => {
      const c = _svg('circle', { cx: x(i), cy: y(v), r: 3, fill: s.color });
      c.appendChild(_svg('title', {}, `${labels[i]}: ${_fmtChartValue(v, s.type)}`));
      svg.appendChild(c);
    });
  });
  return svg;
}

/**
 * Render results as a Chart⇄Table card when chartable, else a plain table.
 * Charts are built client-side from data already in hand → zero added latency.
 */
function renderDataWithChart(target, rows, columnsMeta) {
  const meta = (columnsMeta && columnsMeta.length) ? columnsMeta : null;
  const spec = meta ? detectChartable(rows, meta) : null;

  if (!spec) {
    if (meta) renderTypedTable(target, rows, meta);
    else renderTable(target, rows);
    return;
  }

  const wrap = document.createElement('div');
  wrap.className = 'data-viz';

  const toggle = document.createElement('div');
  toggle.className = 'viz-toggle';
  const btnChart = document.createElement('button');
  btnChart.type = 'button'; btnChart.className = 'viz-tab active';
  btnChart.innerHTML = '<span aria-hidden="true">\ud83d\udcca</span> Chart';
  const btnTable = document.createElement('button');
  btnTable.type = 'button'; btnTable.className = 'viz-tab';
  btnTable.innerHTML = '<span aria-hidden="true">\u25a6</span> Table';
  toggle.append(btnChart, btnTable);
  wrap.appendChild(toggle);

  const chartPane = document.createElement('div');
  chartPane.className = 'viz-pane';
  const chartScroll = document.createElement('div');
  chartScroll.className = 'chart-scroll';
  chartPane.appendChild(chartScroll);

  // Render the chart at the pane's real pixel width so fonts stay crisp and the
  // chart fits the chat window exactly — re-render (debounced) when it resizes.
  const buildChart = () => {
    const w = chartScroll.clientWidth || chartPane.clientWidth || 640;
    chartScroll.replaceChildren(
      spec.kind === 'line' ? _buildLineChart(rows, spec, w) : _buildBarChart(rows, spec, w),
    );
  };
  buildChart();
  if (typeof ResizeObserver !== 'undefined') {
    let raf = 0, lastW = 0;
    const ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0].contentRect.width);
      if (!w || Math.abs(w - lastW) < 8) return;
      lastW = w;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(buildChart);
    });
    ro.observe(chartScroll);
  }

  if (spec.valueCols.length > 1) {
    const legend = document.createElement('div');
    legend.className = 'chart-legend';
    spec.valueCols.forEach((c, i) => {
      const item = document.createElement('span');
      item.className = 'legend-item';
      item.innerHTML = `<i style="background:${_CHART_PALETTE[i % _CHART_PALETTE.length]}"></i>${c.header}`;
      legend.appendChild(item);
    });
    chartPane.appendChild(legend);
  }

  const tablePane = document.createElement('div');
  tablePane.className = 'viz-pane';
  tablePane.style.display = 'none';
  renderTypedTable(tablePane, rows, meta);

  wrap.append(chartPane, tablePane);
  btnChart.addEventListener('click', () => {
    btnChart.classList.add('active'); btnTable.classList.remove('active');
    chartPane.style.display = ''; tablePane.style.display = 'none';
  });
  btnTable.addEventListener('click', () => {
    btnTable.classList.add('active'); btnChart.classList.remove('active');
    tablePane.style.display = ''; chartPane.style.display = 'none';
  });

  target.appendChild(wrap);
}

/* ── Welcome state + live example prompts ─────────────────────────────────── */

const _SUGGESTIONS = [
  { icon: '\ud83c\udfc6', text: 'Top 5 salespeople by total sales' },
  { icon: '\ud83d\udcc8', text: 'Monthly sales trend this year' },
  { icon: '\ud83d\udc65', text: 'Top 10 customers by revenue' },
  { icon: '\ud83d\udce6', text: 'Sales by product category' },
];

function _submitPromptText(text) {
  if (!el.prompt) return;
  el.prompt.value = text;
  el.chatForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
}

function renderWelcome() {
  if (!el.chat || el.chat.children.length > 0) return;
  const wrap = document.createElement('div');
  wrap.className = 'welcome';

  const logo = document.createElement('div');
  logo.className = 'welcome-logo';
  logo.textContent = '\u2726';
  const title = document.createElement('h1');
  title.className = 'welcome-title';
  title.textContent = 'How can I help you today?';
  const sub = document.createElement('p');
  sub.className = 'welcome-sub';
  sub.textContent = "Ask about your sales data in plain English — I'll query the database and visualize the answer.";
  wrap.append(logo, title, sub);

  const grid = document.createElement('div');
  grid.className = 'welcome-chips';
  _SUGGESTIONS.forEach((s) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'welcome-chip';
    b.innerHTML = `<span class="chip-icon" aria-hidden="true">${s.icon}</span><span class="chip-text"></span>`;
    b.querySelector('.chip-text').textContent = s.text;
    b.addEventListener('click', () => _submitPromptText(s.text));
    grid.appendChild(b);
  });
  wrap.appendChild(grid);
  el.chat.appendChild(wrap);
}

function _clearWelcome() {
  const w = el.chat && el.chat.querySelector('.welcome');
  if (w) w.remove();
}

/* ── Typing / thinking indicator ──────────────────────────────────────────── */

function showTyping() {
  hideTyping();
  if (!el.msgTemplate) return;
  const node = el.msgTemplate.content.firstElementChild.cloneNode(true);
  node.classList.add('agent');
  node.id = '_typingIndicator';
  node.querySelector('.msg-avatar').textContent = '\u2726';
  node.querySelector('.msg-meta').textContent = 'Assistant';
  node.querySelector('.msg-body').innerHTML = '<div class="typing" aria-label="Assistant is thinking"><span></span><span></span><span></span></div>';
  el.chat.appendChild(node);
  el.chat.scrollTop = el.chat.scrollHeight;
}

function hideTyping() {
  const t = document.getElementById('_typingIndicator');
  if (t) t.remove();
}

/**
 * Render the structured JSON response envelope returned by the database agent.
 */
function renderStructuredResponse(target, json) {
  // Greeting
  if (json.greetings) {
    renderText(target, json.greetings);
    return;
  }

  // Error from agent or database
  if (json.error) {
    const errorDiv = document.createElement('div');
    errorDiv.className = 'response-error';
    errorDiv.textContent = '\u26a0 ' + json.error;
    target.appendChild(errorDiv);
    return;
  }

  // Summary (shown first, above the data)
  if (json.summary) {
    renderText(target, json.summary);
  }

  // SQL query badge
  if (json.sql_query) {
    const sqlBox = document.createElement('code');
    sqlBox.className = 'sql-display';
    sqlBox.textContent = json.sql_query;
    target.appendChild(sqlBox);
  }

  // Results — auto-chart when the shape suits it, with a Chart⇄Table toggle
  if (json.results && json.results.length > 0) {
    renderDataWithChart(target, json.results, json.columns_meta || []);
  } else if (json.sql_query) {
    const noData = document.createElement('p');
    noData.className = 'no-results';
    noData.textContent = 'No results found for this query.';
    target.appendChild(noData);
  }

  // Insights panel
  if (json.insights && json.insights.length > 0) {
    const panel = document.createElement('div');
    panel.className = 'insights-panel';
    const title = document.createElement('div');
    title.className = 'insights-title';
    title.textContent = '\ud83d\udca1 Insights';
    panel.appendChild(title);
    const ul = document.createElement('ul');
    ul.className = 'insights-list';
    json.insights.forEach((insight) => {
      const li = document.createElement('li');
      li.textContent = insight;
      ul.appendChild(li);
    });
    panel.appendChild(ul);
    target.appendChild(panel);
  }
}

function renderJson(target, value) {
  if (Array.isArray(value)) {
    if (value.every((item) => item && typeof item === 'object' && !Array.isArray(item))) {
      renderTable(target, value);
      return;
    }
    const pre = document.createElement('code');
    pre.textContent = JSON.stringify(value, null, 2);
    target.appendChild(pre);
    return;
  }

  if (value && typeof value === 'object') {
    const wrapper = document.createElement('div');
    wrapper.className = 'json-block';

    Object.entries(value).forEach(([k, v]) => {
      const card = document.createElement('div');
      card.className = 'json-kv';
      const key = document.createElement('div');
      key.className = 'json-key';
      key.textContent = k;
      card.appendChild(key);

      if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean' || v === null) {
        const p = document.createElement('p');
        p.textContent = String(v);
        card.appendChild(p);
      } else if (Array.isArray(v)) {
        renderJson(card, v);
      } else {
        const pre = document.createElement('code');
        pre.textContent = JSON.stringify(v, null, 2);
        card.appendChild(pre);
      }
      wrapper.appendChild(card);
    });

    target.appendChild(wrapper);
    return;
  }

  const p = document.createElement('p');
  p.textContent = String(value);
  target.appendChild(p);
}

function isLocalOrigin() {
  const base = (el.apiBase.value.trim() || state.apiBase || window.location.origin);
  return base.includes('localhost') || base.includes('127.0.0.1');
}

/** Returns Authorization header object for the current signed-in user, or {}. */
async function getAuthHeaders() {
  if (typeof window.Auth === 'undefined') return {};
  const token = await window.Auth.getIdToken();
  if (!token) return {};
  return { 'Authorization': `Bearer ${token}` };
}

async function fetchDatabases() {
  try {
    const base = (el.apiBase.value.trim() || state.apiBase || '').replace(/\/$/, '');
    const url = base ? `${base}/databases` : '/databases';
    const authHeaders = await getAuthHeaders();
    const res = await fetch(url, { headers: authHeaders });
    if (!res.ok) return;
    const data = await res.json();
    const allConnections = data.connections || [];
    const defaultAlias = data.default || '';

    if (!el.dbAlias || allConnections.length === 0) return;

    // Filter out local-only connections when accessing from a remote URL
    const local = isLocalOrigin();
    const connections = allConnections.filter((c) => local || !c.local_only);

    el.dbAlias.innerHTML = '';
    connections.forEach((c) => {
      const opt = document.createElement('option');
      opt.value = c.alias;
      opt.textContent = c.label + (c.local_only ? ' (local only)' : '');
      opt.dataset.dbtype = c.db_type;
      opt.disabled = !local && !!c.local_only;
      if (c.alias === (state.dbAlias || defaultAlias)) opt.selected = true;
      el.dbAlias.appendChild(opt);
    });

    // Persist the resolved alias
    state.dbAlias = el.dbAlias.value;
    localStorage.setItem('dbAlias', state.dbAlias);
    updateDbBadge();
    // Only fetch salespersons if already authenticated — avoids a 401 on the
    // cold page-load call that happens before the Firebase token is ready.
    // The auth-changed listener below re-calls fetchDatabases() after sign-in
    // which will then reach this branch with a valid token.
    const _authedUser = (typeof window.Auth !== 'undefined') ? window.Auth.currentUser() : null;
    if (_authedUser) fetchSalespersons();
  } catch {
    // Server not up yet or no /databases endpoint — leave selector as-is
  }
}

async function fetchSalespersons() {
  const input = el.salespersonId;
  if (!input) return;

  const base = (el.apiBase.value.trim() || state.apiBase || '').replace(/\/$/, '');
  const alias = state.dbAlias || '';
  const url = `${base || ''}/salespersons${alias ? `?db_alias=${encodeURIComponent(alias)}` : ''}`;

  try {
    const authHeaders = await getAuthHeaders();
    const res = await fetch(url, { headers: authHeaders });
    if (!res.ok) return;
    const data = await res.json();
    const list = data.salespersons || [];
    if (!list.length) return;

    // Populate the <datalist> for autocomplete suggestions.
    // The <input> always works as a plain text field — this just adds hints.
    const datalist = document.getElementById('salespersonsDatalist');
    if (datalist) {
      datalist.innerHTML = '';
      list.forEach(({ id, name }) => {
        const opt = document.createElement('option');
        opt.value = id;
        if (name && name !== id) opt.label = `${id}  —  ${name}`;
        datalist.appendChild(opt);
      });
    }

    // If the input is empty, restore a previously saved value
    if (!input.value && state.salespersonId) {
      input.value = state.salespersonId;
    }
  } catch {
    // Server not available — text input still works without suggestions
  }
}

async function createSession(sessionContext = '') {
  saveSettings();
  const url = `${state.apiBase}/apps/${encodeURIComponent(state.appName)}/users/${encodeURIComponent(state.userId)}/sessions`;
  const authHeaders = await getAuthHeaders();

  // Build session state with DB alias + RBAC context forwarded to the agent
  const sessionPayload = {};
  if (state.dbAlias) sessionPayload.db_alias = state.dbAlias;
  sessionPayload.replevel = parseInt(state.replevel || '1', 10);
  sessionPayload.salesperson_id = state.salespersonId || '';
  sessionPayload.role_name = state.roleName || '';
  // Carry previous-session topic summary so the agent retains broad context across
  // the hard session boundary imposed by SESSION_RESET_TURNS.
  // The agent reads this via tool_context.state["session_context"] in get_schema_metadata.
  if (sessionContext) sessionPayload.session_context = sessionContext;
  // Derive user_name from Firebase auth if available, else fall back to userId
  const currentUser = (typeof window.Auth !== 'undefined') ? window.Auth.currentUser() : null;
  sessionPayload.user_name = (currentUser && (currentUser.displayName || currentUser.email)) || state.userId;

  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders },
    body: JSON.stringify({ state: sessionPayload }),
  });

  if (!res.ok) {
    throw new Error(`Session create failed: ${res.status} ${await res.text()}`);
  }

  const data = await res.json();
  state.sessionId = data.id;
  syncInputs();
}

/* ── Trace helpers ─────────────────────────────────────── */

function fmtTs(unix) {
  const d = new Date(unix * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 });
}

function fmtDuration(sec) {
  return sec < 1 ? `${Math.round(sec * 1000)}ms` : `${sec.toFixed(2)}s`;
}

function buildTraceSteps(events) {
  const steps = [];
  for (const evt of events) {
    const part = evt?.content?.parts?.[0];
    if (!part) continue;

    const base = {
      author: evt.author ?? '',
      timestamp: evt.timestamp,
      tokens: evt.usageMetadata ?? null,
    };

    if (part.functionCall) {
      const name = part.functionCall.name;
      // ADK multi-agent: transfer_to_<agent_name> calls
      if (name.startsWith('transfer_to_')) {
        const target = name.replace('transfer_to_', '').replace(/_/g, ' ');
        steps.push({ ...base, type: 'transfer', target });
      } else {
        steps.push({ ...base, type: 'call', name, args: part.functionCall.args || {} });
      }
    } else if (part.functionResponse) {
      // skip transfer responses (they are just acks)
      const name = part.functionResponse.name;
      if (name.startsWith('transfer_to_')) continue;
      steps.push({ ...base, type: 'response', name, data: part.functionResponse.response });
    } else if (part.text) {
      steps.push({ ...base, type: 'text', text: part.text });
    }
  }
  return steps;
}

function tokenSummary(meta) {
  if (!meta) return '';
  const parts = [];
  if (meta.promptTokenCount) parts.push(`prompt: ${meta.promptTokenCount}`);
  if (meta.candidatesTokenCount) parts.push(`output: ${meta.candidatesTokenCount}`);
  if (meta.thoughtsTokenCount) parts.push(`thinking: ${meta.thoughtsTokenCount}`);
  return parts.join(' · ');
}

function renderTracePanel(steps) {
  const wrapper = document.createElement('div');
  wrapper.className = 'trace-panel';

  const toggle = document.createElement('button');
  toggle.className = 'trace-toggle';
  toggle.type = 'button';
  toggle.innerHTML = '<span class="trace-icon">▸</span> Trace <span class="trace-badge">' + steps.length + ' steps</span>';
  wrapper.appendChild(toggle);

  const body = document.createElement('div');
  body.className = 'trace-body collapsed';

  // overall timing
  const first = steps[0]?.timestamp;
  const last = steps[steps.length - 1]?.timestamp;
  if (first && last) {
    const dur = document.createElement('div');
    dur.className = 'trace-duration';
    dur.textContent = `Total: ${fmtDuration(last - first)}`;
    body.appendChild(dur);
  }

  // total tokens
  const totalTokens = steps.reduce((t, s) => t + (s.tokens?.totalTokenCount ?? 0), 0);
  if (totalTokens) {
    const tok = document.createElement('div');
    tok.className = 'trace-duration';
    tok.textContent = `Tokens: ${totalTokens}`;
    body.appendChild(tok);
  }

  const timeline = document.createElement('ol');
  timeline.className = 'trace-timeline';

  for (const step of steps) {
    const li = document.createElement('li');
    li.className = `trace-step trace-${step.type}`;

    const header = document.createElement('div');
    header.className = 'trace-step-header';

    const icon = document.createElement('span');
    icon.className = 'trace-step-icon';
    icon.textContent = step.type === 'transfer' ? '🔀' : step.type === 'call' ? '⚡' : step.type === 'response' ? '📦' : '💬';

    const authorTag = step.author ? `[${step.author}] ` : '';

    const label = document.createElement('span');
    label.className = 'trace-step-label';
    if (step.type === 'transfer') {
      label.textContent = `${authorTag}→ transfer to ${step.target}`;
    } else if (step.type === 'call') {
      label.textContent = `${authorTag}→ ${step.name}()`;
    } else if (step.type === 'response') {
      label.textContent = `${authorTag}← ${step.name}`;
    } else {
      label.textContent = `${authorTag}Final answer`;
    }

    const meta = document.createElement('span');
    meta.className = 'trace-step-meta';
    const metaParts = [];
    if (step.timestamp) metaParts.push(fmtTs(step.timestamp));
    if (step.tokens) {
      const ts = tokenSummary(step.tokens);
      if (ts) metaParts.push(ts);
    }
    meta.textContent = metaParts.join(' | ');

    header.appendChild(icon);
    header.appendChild(label);
    header.appendChild(meta);
    li.appendChild(header);

    // expandable detail
    const detail = document.createElement('div');
    detail.className = 'trace-step-detail collapsed';

    if (step.type === 'transfer') {
      const p = document.createElement('p');
      p.textContent = `Routing to: ${step.target}`;
      detail.appendChild(p);
    } else if (step.type === 'call') {
      const pre = document.createElement('code');
      pre.textContent = JSON.stringify(step.args, null, 2);
      detail.appendChild(pre);
    } else if (step.type === 'response') {
      const pre = document.createElement('code');
      pre.textContent = JSON.stringify(step.data, null, 2);
      detail.appendChild(pre);
    } else if (step.type === 'text') {
      const p = document.createElement('p');
      p.textContent = step.text;
      detail.appendChild(p);
    }

    header.style.cursor = 'pointer';
    header.addEventListener('click', () => {
      detail.classList.toggle('collapsed');
      header.classList.toggle('expanded');
    });

    li.appendChild(detail);
    timeline.appendChild(li);
  }

  body.appendChild(timeline);
  wrapper.appendChild(body);

  toggle.addEventListener('click', () => {
    body.classList.toggle('collapsed');
    const icon = toggle.querySelector('.trace-icon');
    icon.textContent = body.classList.contains('collapsed') ? '▸' : '▾';
  });

  return wrapper;
}

function renderRunEvents(events) {
  hideTyping();
  const steps = buildTraceSteps(events);
  console.log('[renderRunEvents] steps:', steps.map(s => ({ type: s.type, author: s.author, text: s.text?.slice(0, 80) })));

  // find the final text answer
  const lastText = [...steps].reverse().find((s) => s.type === 'text');
  console.log('[renderRunEvents] lastText:', lastText);

  // ── Render main answer ──────────────────────────────────────────────────
  let isStructuredJSON = false;
  if (lastText) {
    if (_isStructuredResponse(lastText.text)) {
      // Agent returned the structured JSON envelope — render richly
      isStructuredJSON = true;
      const json = JSON.parse(lastText.text.trim());
      appendMessage('agent', 'Assistant', (target) => renderStructuredResponse(target, json));
    } else {
      appendMessage('agent', 'Assistant', (target) => renderText(target, lastText.text));
    }
  } else if (steps.length === 0) {
    appendMessage('agent', 'Assistant', (target) => renderText(target, '(No response)'));
  } else {
    // No text step — show last response data directly as the answer
    const lastResp = [...steps].reverse().find((s) => s.type === 'response');
    if (lastResp?.data) {
      const d = lastResp.data;
      const summary = d.ok === false
        ? `Error: ${d.error || JSON.stringify(d)}`
        : d.rows
          ? `Query returned ${d.row_count ?? d.rows.length} row(s)`
          : JSON.stringify(d).slice(0, 200);
      appendMessage('agent', 'Assistant', (target) => renderText(target, summary));
    } else {
      appendMessage('agent', 'Assistant', (target) => renderText(target, '(Agent did not return a text response)'));
    }
  }

  // ── Render raw tool result tables (only when NOT a structured response) ──
  // Structured JSON already embeds SQL + results inside renderStructuredResponse.
  if (!isStructuredJSON) {
    const lastResponse = [...steps].reverse().find((s) => s.type === 'response');
    if (lastResponse?.data) {
      const resp = lastResponse.data;
      if (resp.ok && resp.rows && resp.rows.length > 0) {
        appendMessage('agent', `${resp.source || 'SQL'} Result (${resp.row_count} rows)`, (target) => {
          if (resp.sql_executed) {
            const sqlBox = document.createElement('code');
            sqlBox.className = 'sql-display';
            sqlBox.textContent = resp.sql_executed;
            target.appendChild(sqlBox);
          }
          renderDataWithChart(target, resp.rows, resp.columns_meta || []);
        });
      }
    }
  }

  // render trace panel
  if (steps.length > 0) {
    const traceEl = renderTracePanel(steps);
    el.chat.appendChild(traceEl);
    el.chat.scrollTop = el.chat.scrollHeight;
  }
}

// ── Session auto-renewal ─────────────────────────────────────────────────────
// After SESSION_RESET_TURNS turns we silently start a fresh session to prevent
// the conversation history from growing large enough to overflow the model's
// context window (observed failure mode: 500 after ~8-9 questions).
// On any 5xx / 404 (server restart, Cloud Run cold-start, context overflow)
// we discard the broken session and retry once with a brand-new one.

// Persist turn count in state so it survives navigating between panes.
state._turnCount = 0;
// Rolling list of user questions in the current session (max SESSION_RESET_TURNS entries).
// Used by _buildSessionSummary() to carry topics into the next session on proactive reset.
state._sessionHistory = [];
// Configurable in localStorage so admins can tune without a redeploy.
const SESSION_RESET_TURNS = parseInt(localStorage.getItem('sessionResetTurns') || '8', 10);

/**
 * Build a concise topic list from the current session to prime the next one.
 * Returns an empty string when fewer than 2 turns exist (single-question sessions
 * have no context worth carrying over).
 * @returns {string} multi-line bullet string, or '' when history is small.
 */
function _buildSessionSummary() {
  if (!state._sessionHistory || state._sessionHistory.length < 2) return '';
  const bullets = state._sessionHistory
    .slice(-SESSION_RESET_TURNS)
    .map((t) => `• ${t.q}`)
    .join('\n');
  return `[Context from previous session — ${state._sessionHistory.length} questions]:\n${bullets}`;
}

/**
 * Fire-and-forget POST to /session-summary.
 * The backend records the summary for analytics and future satisfaction dashboards.
 * Non-critical — failures are only console.warn'd so they never block the user.
 * @param {string} ctx - output of _buildSessionSummary()
 */
function _saveSessionSummary(ctx) {
  if (!ctx) return;
  getAuthHeaders().then((authHeaders) => {
    fetch(`${state.apiBase}/session-summary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders },
      body: JSON.stringify({
        userId:     state.userId,
        sessionId:  state.sessionId,       // the session that is ending
        db_alias:   state.dbAlias || '',
        summary:    ctx,
        turn_count: state._turnCount,
        // satisfaction: null — reserved for future thumbs-up / thumbs-down feature
        satisfaction: null,
        // tags: [] — reserved for future automatic topic classification
        tags: [],
      }),
    }).catch((e) => console.warn('[_saveSessionSummary] non-critical failure:', e));
  });
}

/** Build the /run request body for a given session ID. */
function _buildRunBody(promptText, sessionId) {
  return JSON.stringify({
    appName: state.appName,
    userId: state.userId,
    sessionId,
    newMessage: {
      role: 'user',
      parts: [{ text: promptText }],
    },
    streaming: false,
  });
}

async function runPrompt(promptText) {
  // Proactively reset after SESSION_RESET_TURNS turns to avoid context overflow.
  if (!state.sessionId || state._turnCount >= SESSION_RESET_TURNS) {
    const _prevCtx = _buildSessionSummary();  // capture topic list BEFORE wiping
    _saveSessionSummary(_prevCtx);            // fire-and-forget → /session-summary
    state._sessionHistory = [];              // fresh history slate for the new session
    state._turnCount = 0;
    await createSession(_prevCtx);           // seed new session with topic context
  }

  const authHeaders = await getAuthHeaders();
  let res = await fetch(`${state.apiBase}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders },
    body: _buildRunBody(promptText, state.sessionId),
  });

  // Auto-recover: 5xx (context overflow, unhandled server error, cold-start wipe)
  //               404 (session evicted from server memory after idle scale-down)
  //               422 (context overflow with specific error code from server)
  if (res.status >= 500 || res.status === 404 || res.status === 422) {
    const errBody = await res.text();
    console.warn(`[runPrompt] ${res.status} — auto-renewing session. Server said: ${errBody}`);
    state.sessionId = '';
    state._sessionHistory = [];              // context is lost on error-path recovery
    state._turnCount = 0;
    await createSession();
    appendMessage('agent', 'System', (target) =>
      renderText(target, '⟳ Session auto-renewed — resending your question…'));
    const authHeaders2 = await getAuthHeaders();
    res = await fetch(`${state.apiBase}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders2 },
      body: _buildRunBody(promptText, state.sessionId),
    });
  }

  if (!res.ok) {
    throw new Error(`Run failed: ${res.status} ${await res.text()}`);
  }

  state._turnCount += 1;
  // Record question so _buildSessionSummary() can carry it to the next session.
  state._sessionHistory.push({ q: promptText });
  if (state._sessionHistory.length > SESSION_RESET_TURNS) state._sessionHistory.shift();
  const events = await res.json();
  console.log('[runPrompt] raw events:', JSON.stringify(events, null, 2));
  if (!Array.isArray(events)) {
    throw new Error(`Unexpected /run response (not an array): ${JSON.stringify(events).slice(0, 200)}`);
  }
  renderRunEvents(events);
}

function setBusy(busy) {
  el.sendBtn.disabled = busy;
  el.newSessionBtn.disabled = busy;
}

el.chatForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const prompt = el.prompt.value.trim();
  if (!prompt) return;

  appendMessage('user', 'You', (target) => renderText(target, prompt));
  el.prompt.value = '';
  el.prompt.style.height = 'auto';
  showTyping();

  try {
    setBusy(true);
    saveSettings();
    await runPrompt(prompt);
  } catch (err) {
    hideTyping();
    appendMessage('agent', 'Error', (target) => {
      renderText(target, err instanceof Error ? err.message : String(err));
    });
  } finally {
    setBusy(false);
  }
});

el.newSessionBtn.addEventListener('click', async () => {
  try {
    setBusy(true);
    state._turnCount = 0;
    state._sessionHistory = [];
    await createSession();
    appendMessage('agent', 'System', (target) => renderText(target, `Session created: ${state.sessionId}`));
  } catch (err) {
    appendMessage('agent', 'Error', (target) => {
      renderText(target, err instanceof Error ? err.message : String(err));
    });
  } finally {
    setBusy(false);
  }
});

el.clearChatBtn.addEventListener('click', () => {
  el.chat.innerHTML = '';
  renderWelcome();
});

// Re-create session when user switches DB so the new alias is in session state
if (el.dbAlias) {
  el.dbAlias.addEventListener('change', async () => {
    state.dbAlias = el.dbAlias.value;
    localStorage.setItem('dbAlias', state.dbAlias);
    state.sessionId = '';
    state._turnCount = 0;
    state._sessionHistory = [];
    fetchSalespersons();           // refresh salesperson dropdown for the new DB
    const selected = el.dbAlias.options[el.dbAlias.selectedIndex];
    const dbLabel = selected ? selected.textContent : state.dbAlias;

    // Visual feedback — mark the select as switching
    el.dbAlias.classList.add('db-switching');
    updateDbBadge();

    appendMessage('agent', 'System', (target) => {
      renderText(target, `⏳ Switching to "${dbLabel}" — creating new session…`);
    });

    try {
      setBusy(true);
      await createSession();
      appendMessage('agent', 'System', (target) => {
        renderText(target, `✅ Now querying "${dbLabel}" (session: ${state.sessionId})`);
      });
    } catch (err) {
      appendMessage('agent', 'Error', (target) => {
        renderText(target, err instanceof Error ? err.message : String(err));
      });
    } finally {
      el.dbAlias.classList.remove('db-switching');
      setBusy(false);
    }
  });
}

syncInputs();
fetchDatabases();
renderWelcome();
// fetchDatabases() runs on page load BEFORE auth resolves, so the first
// call to /salespersons has no Bearer token → 401.  Once auth-changed fires
// the token is available and we can populate the dropdown properly.
window.addEventListener('auth-changed', ({ detail: { user } }) => {
  if (user) fetchDatabases();
});

/* ── Settings page: role & DB wiring ───────────────────── */
(function initSettingsPage() {
  const ROLE_LABELS = { '1': 'Internal', '3': 'Manager', '5': 'Salesperson' };

  /** Apply role change: persist → update UI → recreate session */
  async function applyRoleChange() {
    const lvl  = el.replevel ? el.replevel.value : '1';
    const spId = (el.salespersonId ? el.salespersonId.value.trim() : '') || '';

    // Require an ID when switching to level 3 or 5
    if ((lvl === '5' || lvl === '3') && !spId) {
      if (el.salespersonId) {
        el.salespersonId.focus();
        // Visual cue: highlight the dropdown
        el.salespersonId.style.borderColor = '#f59e0b';
        setTimeout(() => { el.salespersonId.style.borderColor = ''; }, 2000);
      }
      const idLabel = lvl === '3' ? 'Manager ID' : 'Salesperson ID';
      appendMessage('agent', 'System', (target) =>
        renderText(target, `⚠ Please select a ${idLabel} before applying.`));
      // Switch to chat tab so the user sees the warning
      const chatNav = document.querySelector('.sidebar-nav-item[data-tab="chat"]');
      if (chatNav) chatNav.click();
      return;
    }

    state.replevel      = lvl;
    state.salespersonId = spId;
    localStorage.setItem('replevel',      lvl);
    localStorage.setItem('salespersonId', spId);
    updateRbacBar();

    // Invalidate the current session — RBAC context bakes in at session creation
    state.sessionId = '';
    state._turnCount = 0;
    state._sessionHistory = [];

    const roleLabel = ROLE_LABELS[lvl] || lvl;
    const who = spId ? ` (${spId})` : '';
    appendMessage('agent', 'System', (target) =>
      renderText(target, `⏳ Switching to ${roleLabel}${who} — creating new session…`));

    try {
      setBusy(true);
      await createSession();
      appendMessage('agent', 'System', (target) =>
        renderText(target, `✅ Now running as ${roleLabel}${who} (session: ${state.sessionId})`));
    } catch (err) {
      appendMessage('agent', 'Error', (target) =>
        renderText(target, err instanceof Error ? err.message : String(err)));
    } finally {
      setBusy(false);
    }
  }

  // Role select: update row visibility on change and re-fetch the appropriate
  // salesperson list (label + options differ between Manager and Salesperson).
  if (el.replevel) {
    el.replevel.addEventListener('change', () => {
      updateRbacBar();
      const lvl = el.replevel.value;
      if (lvl === '3' || lvl === '5') fetchSalespersons();
    });
  }

  // Apply Role button
  if (el.rbacApplyBtn) {
    el.rbacApplyBtn.addEventListener('click', applyRoleChange);
  }
})();

// Re-fetch DB list when API base URL is changed
el.apiBase.addEventListener('change', () => {
  state.apiBase = el.apiBase.value.trim() || state.apiBase;
  localStorage.setItem('apiBase', state.apiBase);
  fetchDatabases();
});

/* ── Theme Toggle ──────────────────────────────────────── */
(function initTheme() {
  const btn = document.getElementById('themeToggle');
  const saved = localStorage.getItem('theme') || 'light';
  document.documentElement.dataset.theme = saved;
  if (btn) btn.textContent = saved === 'dark' ? '☀️' : '🌙';
  if (btn) btn.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('theme', next);
    btn.textContent = next === 'dark' ? '☀️' : '🌙';
  });
})();

/* ── Sidebar Navigation ─────────────────────────────────── */
(function initTabs() {
  const navItems  = document.querySelectorAll('.sidebar-nav-item');
  const tabViews  = document.querySelectorAll('.tab-view');

  navItems.forEach((btn) => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      navItems.forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
      tabViews.forEach((v) => v.classList.toggle('active', v.id === `view-${tab}`));
      window.dispatchEvent(new CustomEvent('tab-changed', { detail: { tab } }));
      // Close mobile sidebar when a nav item is tapped
      _closeMobileSidebar();
    });
  });

  // Sidebar collapse toggle (desktop) / close (mobile)
  const sidebar   = document.getElementById('sidebar');
  const collapseBtn = document.getElementById('sidebarCollapseBtn');
  if (sidebar && collapseBtn) {
    const saved = localStorage.getItem('sidebarCollapsed') === 'true';
    if (saved) sidebar.classList.add('collapsed');
    collapseBtn.addEventListener('click', () => {
      if (window.innerWidth <= 640) {
        // On mobile, collapse-arrow means "close"
        _closeMobileSidebar();
      } else {
        sidebar.classList.toggle('collapsed');
        localStorage.setItem('sidebarCollapsed', sidebar.classList.contains('collapsed'));
      }
    });
  }
})();

/* ── Mobile sidebar open / close ────────────────────────── */
function _isMobile() { return window.innerWidth <= 640; }

function _openMobileSidebar() {
  const sidebar  = document.getElementById('sidebar');
  const overlay  = document.getElementById('sidebarOverlay');
  if (!sidebar || !overlay) return;
  sidebar.classList.add('mobile-open');
  overlay.classList.add('visible');
  document.body.style.overflow = 'hidden'; // prevent background scroll
}

function _closeMobileSidebar() {
  const sidebar  = document.getElementById('sidebar');
  const overlay  = document.getElementById('sidebarOverlay');
  if (!sidebar || !overlay) return;
  sidebar.classList.remove('mobile-open');
  overlay.classList.remove('visible');
  document.body.style.overflow = '';
}

(function initMobileSidebar() {
  const hamburger = document.getElementById('mobileMenuBtn');
  const overlay   = document.getElementById('sidebarOverlay');

  if (hamburger) {
    hamburger.addEventListener('click', () => {
      const sidebar = document.getElementById('sidebar');
      if (sidebar && sidebar.classList.contains('mobile-open')) {
        _closeMobileSidebar();
      } else {
        _openMobileSidebar();
      }
    });
  }

  // Tap overlay to close
  if (overlay) {
    overlay.addEventListener('click', _closeMobileSidebar);
  }

  // Close on resize back to desktop
  window.addEventListener('resize', () => {
    if (!_isMobile()) _closeMobileSidebar();
  });
})();

/* ── Textarea Auto-resize ──────────────────────────────── */
el.prompt.addEventListener('input', function () {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 160) + 'px';
});

/* ── Enter = Send, Shift+Enter = new line ──────────────── */
el.prompt.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    el.chatForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  }
});
