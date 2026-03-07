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
  const lvl = state.replevel || '1';
  const ROLE_COLORS = { '1': '#22c55e', '3': '#6366f1', '5': '#f59e0b' };
  const ROLE_LABELS = { '1': 'Internal', '3': 'Manager', '5': 'Salesperson' };

  // Settings page: show/hide salesperson row
  if (el.settingsSalespersonRow) {
    el.settingsSalespersonRow.style.display = (lvl === '3' || lvl === '5') ? '' : 'none';
  }

  // Update label for the salesperson select
  if (el.settingsSpLabel) {
    el.settingsSpLabel.textContent = lvl === '3' ? 'Manager ID' : 'Salesperson ID';
  }

  // Update first (empty) option label
  if (el.salespersonId && el.salespersonId.tagName === 'SELECT') {
    const first = el.salespersonId.options[0];
    if (first && first.value === '') {
      first.textContent = lvl === '3' ? '— select manager ID —' : '— select salesperson —';
    }
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
  bodyRenderer(node.querySelector('.msg-body'));
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

  // Results table (typed if columns_meta present, plain otherwise)
  if (json.results && json.results.length > 0) {
    if (json.columns_meta && json.columns_meta.length > 0) {
      renderTypedTable(target, json.results, json.columns_meta);
    } else {
      renderTable(target, json.results);
    }
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
    fetchSalespersons();            // pre-load salesperson dropdown for this DB
  } catch {
    // Server not up yet or no /databases endpoint — leave selector as-is
  }
}

async function fetchSalespersons() {
  const sel = el.salespersonId;
  if (!sel || sel.tagName !== 'SELECT') return;

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

    const prev = sel.value || state.salespersonId;
    const lvl  = state.replevel || '1';
    const emptyLabel = lvl === '3' ? '— select manager ID —' : '— select salesperson —';

    sel.innerHTML = `<option value="">${emptyLabel}</option>`;
    list.forEach(({ id, name }) => {
      const opt = document.createElement('option');
      opt.value = id;
      opt.textContent = name && name !== id ? `${id}  —  ${name}` : id;
      sel.appendChild(opt);
    });

    // Restore previously selected value if it still exists
    if (prev) sel.value = prev;
  } catch {
    // Server not available — leave dropdown as-is
  }
}

async function createSession() {
  saveSettings();
  const url = `${state.apiBase}/apps/${encodeURIComponent(state.appName)}/users/${encodeURIComponent(state.userId)}/sessions`;
  const authHeaders = await getAuthHeaders();

  // Build session state with DB alias + RBAC context forwarded to the agent
  const sessionPayload = {};
  if (state.dbAlias) sessionPayload.db_alias = state.dbAlias;
  if (state.replevel) sessionPayload.replevel = parseInt(state.replevel, 10);
  if (state.salespersonId) sessionPayload.salesperson_id = state.salespersonId;
  if (state.roleName) sessionPayload.role_name = state.roleName;
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
        appendMessage('agent', `SQL Result (${resp.row_count} rows)`, (target) => {
          const sqlBox = document.createElement('code');
          sqlBox.className = 'sql-display';
          sqlBox.textContent = resp.sql_executed;
          target.appendChild(sqlBox);
          if (resp.columns_meta && resp.columns_meta.length > 0) {
            renderTypedTable(target, resp.rows, resp.columns_meta);
          } else {
            renderTable(target, resp.rows);
          }
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

async function runPrompt(promptText) {
  if (!state.sessionId) {
    await createSession();
  }

  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${state.apiBase}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders },
    body: JSON.stringify({
      appName: state.appName,
      userId: state.userId,
      sessionId: state.sessionId,
      newMessage: {
        role: 'user',
        parts: [{ text: promptText }],
      },
      streaming: false,
    }),
  });

  if (!res.ok) {
    throw new Error(`Run failed: ${res.status} ${await res.text()}`);
  }

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

  try {
    setBusy(true);
    saveSettings();
    await runPrompt(prompt);
  } catch (err) {
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
});

// Re-create session when user switches DB so the new alias is in session state
if (el.dbAlias) {
  el.dbAlias.addEventListener('change', async () => {
    state.dbAlias = el.dbAlias.value;
    localStorage.setItem('dbAlias', state.dbAlias);
    state.sessionId = '';
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

/* ── Settings page: role & DB wiring ───────────────────── */
(function initSettingsPage() {
  const ROLE_LABELS = { '1': 'Internal', '3': 'Manager', '5': 'Salesperson' };

  /** Apply role change: persist → update UI → recreate session */
  async function applyRoleChange() {
    const lvl  = el.replevel ? el.replevel.value : '1';
    const spId = (el.salespersonId ? el.salespersonId.value.trim() : '') || '';

    // Require an ID when switching to level 3 or 5
    if ((lvl === '5' || lvl === '3') && !spId) {
      if (el.salespersonId) el.salespersonId.focus();
      return;
    }

    state.replevel      = lvl;
    state.salespersonId = spId;
    localStorage.setItem('replevel',      lvl);
    localStorage.setItem('salespersonId', spId);
    updateRbacBar();

    // Invalidate the current session — RBAC context bakes in at session creation
    state.sessionId = '';

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

  // Role select: update row visibility on change (apply waits for the button)
  if (el.replevel) {
    el.replevel.addEventListener('change', updateRbacBar);
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
