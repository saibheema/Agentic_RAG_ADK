/**
 * Firebase Authentication — Google Sign-In
 * Uses Firebase compat SDK loaded via CDN in index.html.
 * Exposes window.Auth for use by app.js and support.js.
 */
(function initAuth() {
  const FIREBASE_CONFIG = {
    apiKey: "AIzaSyCSv_MrpciLJDGFmLZySNWx1faD0GkaLds",
    authDomain: "unicon-494419.firebaseapp.com",
    projectId: "unicon-494419",
    storageBucket: "unicon-494419.firebasestorage.app",
    messagingSenderId: "664984131730",
    appId: "1:664984131730:web:e3066a3def43d9b87eae4b",
  };

  const ADMIN_EMAIL = "sbheema@swardesi.com";

  if (!firebase.apps.length) {
    firebase.initializeApp(FIREBASE_CONFIG);
  }
  const auth = firebase.auth();
  let _currentUser = null;
  let _pingInterval = null;

  // ── Presence ping ─────────────────────────────────────────────────────────
  async function _doPing() {
    if (!_currentUser) return;
    try {
      const token = await _currentUser.getIdToken(false);
      if (!token) return;
      const base = (localStorage.getItem('apiBase') || '').replace(/\/$/, '');
      const url = base ? `${base}/ping` : '/ping';
      await fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
    } catch { /* ignore ping errors */ }
  }

  function _startPing() {
    _stopPing();
    _doPing();
    _pingInterval = setInterval(_doPing, 30_000);
  }

  function _stopPing() {
    if (_pingInterval) { clearInterval(_pingInterval); _pingInterval = null; }
  }

  // ── Public API ────────────────────────────────────────────────────────────
  window.Auth = {
    /** Returns a fresh ID token string, or null if not signed in. */
    async getIdToken() {
      if (!_currentUser) return null;
      try {
        return await _currentUser.getIdToken(/* forceRefresh= */ false);
      } catch {
        return null;
      }
    },

    /** Open Google Sign-In popup. */
    async signIn() {
      const provider = new firebase.auth.GoogleAuthProvider();
      return auth.signInWithPopup(provider);
    },

    /** Sign out the current user. */
    async signOut() {
      return auth.signOut();
    },

    /** True if the current user has admin privileges. */
    isAdmin() {
      return _currentUser?.email === ADMIN_EMAIL;
    },

    /** The current Firebase user object, or null. */
    currentUser() {
      return _currentUser;
    },
  };

  // ── Auth state observer ───────────────────────────────────────────────────
  auth.onAuthStateChanged((user) => {
    _currentUser = user;
    if (user) {
      _startPing();
    } else {
      _stopPing();
    }
    _applyAuthState(user);
    window.dispatchEvent(new CustomEvent("auth-changed", { detail: { user } }));
  });

  function _applyAuthState(user) {
    const overlay   = document.getElementById("authOverlay");
    const appShell  = document.getElementById("appShell");
    const userPill  = document.getElementById("userPill");
    const userEmail = document.getElementById("userEmail");
    const userAvatar= document.getElementById("userAvatar");
    const adminBadge= document.getElementById("adminBadge");
    const signInBtn = document.getElementById("signInBtn");

    const adminNav   = document.querySelector('.sidebar-nav-item[data-tab="admin"]');

    if (user) {
      if (overlay)    { overlay.classList.add("hidden"); }
      if (appShell)   { appShell.style.display = ""; }
      if (userEmail)  { userEmail.textContent = user.email; }
      if (userAvatar) { userAvatar.textContent = (user.email || "U")[0].toUpperCase(); }
      if (userPill)   { userPill.style.display = ""; }
      if (adminBadge) { adminBadge.style.display = window.Auth.isAdmin() ? "" : "none"; }
      if (signInBtn)  { signInBtn.disabled = false; }
      if (adminNav)   { adminNav.style.display = window.Auth.isAdmin() ? "" : "none"; }
    } else {
      if (overlay)   { overlay.classList.remove("hidden"); }
      if (appShell)  { appShell.style.display = "none"; }
      if (userPill)  { userPill.style.display = "none"; }
      if (adminNav)  { adminNav.style.display = "none"; }
    }
  }

  // ── Button wiring ─────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    const signInBtn  = document.getElementById("signInBtn");
    const signOutBtn = document.getElementById("signOutBtn");

    if (signInBtn) {
      signInBtn.addEventListener("click", async () => {
        signInBtn.disabled = true;
        signInBtn.textContent = "Signing in…";
        try {
          await window.Auth.signIn();
        } catch (err) {
          console.error("Sign-in error:", err);
          signInBtn.disabled = false;
          signInBtn.innerHTML = '<span class="google-g">G</span>Sign in with Google';
        }
      });
    }

    if (signOutBtn) {
      signOutBtn.addEventListener("click", () => window.Auth.signOut());
    }
  });
})();
