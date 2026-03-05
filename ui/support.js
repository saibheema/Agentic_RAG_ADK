/**
 * Support Agent Chat
 * Talks to the 'support_agent' ADK agent at /apps/support_agent/…
 * Users can report issues, ask questions, or request help with the RAG tool.
 */
(function initSupport() {
  const S = {
    sessionId: '',
    userId: localStorage.getItem('userId') || 'web-user',
    appName: 'support_agent',
  };

  function apiBase() {
    return (localStorage.getItem('apiBase') || '').replace(/\/$/, '');
  }

  async function getAuthHeaders() {
    if (typeof window.Auth === 'undefined') return {};
    const token = await window.Auth.getIdToken();
    if (!token) return {};
    return { 'Authorization': `Bearer ${token}` };
  }

  const chatEl    = document.getElementById('support-chat');
  const form      = document.getElementById('supportForm');
  const promptEl  = document.getElementById('support-prompt');
  const sendBtn   = document.getElementById('supportSendBtn');

  if (!chatEl || !form) return;  // Support view not in DOM

  // ── Message rendering ──────────────────────────────────────────────────────
  function appendMsg(kind, label, text) {
    const tmpl = document.getElementById('msgTemplate');
    if (!tmpl) return;
    const node = tmpl.content.firstElementChild.cloneNode(true);
    node.classList.add(kind);
    node.dataset.label = label.toLowerCase().replace(/[^a-z0-9]+/g, '-');

    const avatar = node.querySelector('.msg-avatar');
    if (avatar) {
      if (kind === 'user')       avatar.textContent = (window.Auth?.currentUser()?.email || 'U')[0].toUpperCase();
      else if (label === 'Error')  avatar.textContent = '⚠';
      else if (label === 'System') avatar.textContent = 'ℹ';
      else                         avatar.textContent = '🛠';
    }
    node.querySelector('.msg-meta').textContent = label;

    const body = node.querySelector('.msg-body');
    if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
      const raw     = marked.parse(String(text));
      const wrapped = raw.replace(/<table/g, '<div class="table-scroll"><table')
                         .replace(/<\/table>/g, '</table></div>');
      const clean   = DOMPurify.sanitize(wrapped, { USE_PROFILES: { html: true } });
      const wrapper = document.createElement('div');
      wrapper.className = 'md-body';
      wrapper.innerHTML = clean;
      body.appendChild(wrapper);
    } else {
      body.textContent = text;
    }

    chatEl.appendChild(node);
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  // ── Session management ────────────────────────────────────────────────────
  async function createSession() {
    S.userId = localStorage.getItem('userId') || 'web-user';
    const url = `${apiBase()}/apps/${encodeURIComponent(S.appName)}/users/${encodeURIComponent(S.userId)}/sessions`;
    const authHeaders = await getAuthHeaders();
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders },
      body: JSON.stringify({}),
    });
    if (!res.ok) throw new Error(`Session create failed: ${res.status} ${await res.text()}`);
    const data = await res.json();
    S.sessionId = data.id;
  }

  // ── Send message ──────────────────────────────────────────────────────────
  async function sendMessage(text) {
    if (!S.sessionId) await createSession();
    const authHeaders = await getAuthHeaders();
    const res = await fetch(`${apiBase()}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders },
      body: JSON.stringify({
        appName: S.appName,
        userId: S.userId,
        sessionId: S.sessionId,
        newMessage: { role: 'user', parts: [{ text }] },
        streaming: false,
      }),
    });
    if (!res.ok) throw new Error(`Agent error: ${res.status} ${await res.text()}`);
    const events = await res.json();
    if (!Array.isArray(events)) throw new Error('Unexpected response from agent');

    // Find the last text event from the support agent
    let lastText = '';
    for (const evt of events) {
      const part = evt?.content?.parts?.[0];
      if (part?.text) lastText = part.text;
    }
    appendMsg('agent', 'Support Agent', lastText || '(No response received)');
  }

  function setBusy(busy) {
    if (sendBtn)  sendBtn.disabled  = busy;
    if (promptEl) promptEl.disabled = busy;
  }

  // ── Form submit ───────────────────────────────────────────────────────────
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = promptEl.value.trim();
    if (!text) return;
    appendMsg('user', 'You', text);
    promptEl.value = '';
    promptEl.style.height = 'auto';
    try {
      setBusy(true);
      await sendMessage(text);
    } catch (err) {
      appendMsg('agent', 'Error', err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  });

  // ── Textarea auto-resize + Enter key ─────────────────────────────────────
  if (promptEl) {
    promptEl.addEventListener('input', function () {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 160) + 'px';
    });
    promptEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
      }
    });
  }

  // ── Create session when tab becomes active ────────────────────────────────
  window.addEventListener('tab-changed', ({ detail: { tab } }) => {
    if (tab === 'support' && !S.sessionId) {
      createSession().catch((err) => console.warn('[support] session init failed:', err));
    }
  });

  // ── Re-create session when user ID changes (auth) ────────────────────────
  window.addEventListener('auth-changed', () => {
    S.sessionId = '';  // Force fresh session on next message
  });
})();
