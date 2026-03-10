/**
 * Admin panel — user activity & last login.
 * Only reachable by sbheema@swardesi.com (enforced by auth.js nav visibility
 * AND by the /admin/users backend endpoint).
 */
(function initAdminPanel() {
  let _refreshInterval = null;

  // ── Helpers ──────────────────────────────────────────────────────────────

  function _apiBase() {
    return (document.getElementById('apiBase')?.value?.trim() || '').replace(/\/$/, '');
  }

  function _esc(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function _fmtDateTime(iso) {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleString([], {
        month: 'short', day: 'numeric', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
      });
    } catch { return iso; }
  }

  function _fmtRelative(iso) {
    if (!iso) return '—';
    try {
      const diffMs = Date.now() - new Date(iso).getTime();
      const sec = Math.floor(diffMs / 1000);
      if (sec < 5)   return 'just now';
      if (sec < 60)  return `${sec}s ago`;
      const min = Math.floor(sec / 60);
      if (min < 60)  return `${min}m ago`;
      const hr = Math.floor(min / 60);
      if (hr < 24)   return `${hr}h ago`;
      return _fmtDateTime(iso);
    } catch { return iso; }
  }

  // ── Fetch & render ────────────────────────────────────────────────────────

  async function loadAdminUsers() {
    const container = document.getElementById('adminUsersTable');
    if (!container) return;

    const token = await window.Auth?.getIdToken();
    if (!token) {
      container.innerHTML = '<p class="admin-error">Not signed in.</p>';
      return;
    }

    const base = _apiBase();
    const url  = base ? `${base}/admin/users` : '/admin/users';

    container.innerHTML = '<div class="admin-loading">Loading\u2026</div>';

    try {
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (res.status === 403) {
        container.innerHTML = '<p class="admin-error">Access denied.</p>';
        return;
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        container.innerHTML = `<p class="admin-error">Error ${res.status}: ${_esc(err.error || res.statusText)}</p>`;
        return;
      }

      const data = await res.json();
      _renderUsers(data.users || [], container);
    } catch (e) {
      container.innerHTML = `<p class="admin-error">Network error: ${_esc(e.message)}</p>`;
    }
  }

  function _renderUsers(users, container) {
    if (!users.length) {
      container.innerHTML = '<p class="admin-empty">No users found.</p>';
      return;
    }

    const onlineCount = users.filter((u) => u.isOnline).length;
    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    let html = `
      <div class="admin-stats">
        <span class="admin-stat-total">${users.length} user${users.length !== 1 ? 's' : ''} registered</span>
        <span class="admin-stat-online">${onlineCount} online now</span>
        <span class="admin-stat-refresh">Updated ${now}</span>
      </div>
      <div class="table-scroll">
        <table class="admin-table">
          <thead>
            <tr>
              <th>Status</th>
              <th>User</th>
              <th>Registered</th>
              <th>Last Login</th>
              <th>Last Active</th>
            </tr>
          </thead>
          <tbody>`;

    for (const u of users) {
      const isOnline  = u.isOnline;
      const dotClass  = isOnline ? 'online' : 'offline';
      const dotTitle  = isOnline ? 'Online now' : 'Offline';
      const initial   = (u.email || '?')[0].toUpperCase();
      const name      = u.displayName ? `<span class="admin-display-name">${_esc(u.displayName)}</span>` : '';
      const disabled  = u.disabled ? ' <span class="admin-disabled-badge">disabled</span>' : '';

      html += `
            <tr class="${isOnline ? 'row-online' : ''}">
              <td class="td-status">
                <span class="presence-dot ${dotClass}" title="${dotTitle}"></span>
              </td>
              <td>
                <div class="admin-user-cell">
                  <span class="admin-avatar ${isOnline ? 'avatar-online' : ''}">${_esc(initial)}</span>
                  <div>
                    <span class="admin-email">${_esc(u.email)}${disabled}</span>
                    ${name}
                  </div>
                </div>
              </td>
              <td class="ts-cell">${_fmtDateTime(u.createdAt)}</td>
              <td class="ts-cell">${u.lastLogin ? _fmtDateTime(u.lastLogin) : '<span class="admin-never">never</span>'}</td>
              <td class="ts-cell">${_fmtRelative(u.lastActive)}</td>
            </tr>`;
    }

    html += `
          </tbody>
        </table>
      </div>`;

    container.innerHTML = html;
  }

  // ── Auto-refresh when admin tab is active ─────────────────────────────────

  window.addEventListener('tab-changed', (e) => {
    if (e.detail.tab === 'admin') {
      loadAdminUsers();
      if (!_refreshInterval) {
        _refreshInterval = setInterval(loadAdminUsers, 30_000);
      }
    } else {
      clearInterval(_refreshInterval);
      _refreshInterval = null;
    }
  });

  // Manual refresh button
  document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('adminRefreshBtn');
    if (btn) btn.addEventListener('click', loadAdminUsers);
  });

  // Load immediately if admin tab happens to be active on auth change
  window.addEventListener('auth-changed', ({ detail: { user } }) => {
    const adminView = document.getElementById('view-admin');
    if (user && adminView?.classList.contains('active')) {
      loadAdminUsers();
    }
  });
})();
