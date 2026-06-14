/**
 * script.js — Shared JavaScript for all Panacea pages
 * FIXED VERSION — resolves assessment flash/restart bugs:
 *   1. checkUserSession no longer clears localStorage mid-assessment
 *   2. Session ready promise prevents assessment init race condition
 *   3. logout() no longer redirects from assessment page without confirmation
 *   4. Duplicate DOMContentLoaded listeners removed
 */

/* ═══════════════════════════════════════════
   SHARED TOP NAV INJECTION
   Injects the standard .top-nav into every page.
   Doctors get a stripped-down nav: only Dashboard
   and Profile. Patient links are hidden for them.
   ═══════════════════════════════════════════ */
function injectTopNav() {
  // Don't inject if a .top-nav already exists in the HTML
  if (document.querySelector('.top-nav')) return;

  const currentPage = window.location.pathname.split('/').pop() || 'index.html';
  const isDoctor = localStorage.getItem('panacea_user_type') === 'doctor';

  // Doctor nav: only Dashboard + (no patient features)
  const doctorLinks = [
    { href: 'doctor-dashboard.html', label: '🩺 Dashboard', guarded: true },
  ];

  // Patient nav: full feature set
  const patientLinks = [
    { href: 'index.html',             label: 'Home' },
    { href: 'assessment.html',        label: 'Symptom Checker',  guarded: true },
    { href: 'pharmacy.html',          label: 'Pharmacy',         guarded: true },
    { href: 'consultation.html',      label: 'Consult Doctor',   guarded: true },
    { href: 'patient-dashboard.html', label: 'Records',          guarded: true },
  ];

  const links = isDoctor ? doctorLinks : patientLinks;

  const navLinksHTML = links.map(l => {
    const isActive = l.href === currentPage ? ' class="active"' : '';
    if (l.guarded) {
      return `<a href="#"${isActive} onclick="requireAuthNav('${l.href}'); return false;">${l.label}</a>`;
    }
    return `<a href="${l.href}"${isActive}>${l.label}</a>`;
  }).join('\n      ');

  // Page-specific extra dropdown items
  const pageExtras = {
    'assessment.html': `
        <div class="dropdown-divider"></div>
        <a href="#" id="newSessionMenuItem">⟳ New Session</a>`,
  };
  const extraDropdownHTML = (!isDoctor && pageExtras[currentPage]) || '';

  const nav = document.createElement('nav');
  nav.className = 'top-nav';
  nav.innerHTML = `
  <div class="nav-logo-zone">
    <img src="assets/logo.png" alt="Panacea"
         class="nav-logo-image"
         onerror="this.style.display='none';">
    <div class="nav-logo-text-wrap">
      <span class="nav-logo-text">panacea</span>
      <span class="nav-logo-sub">intelligent health companion</span>
    </div>
  </div>

  <div class="nav-area">
    <div class="nav-links">
      ${navLinksHTML}
    </div>

    <div class="profile-section" id="profileToggle">
      <div class="profile-avatar" id="profileAvatar">G</div>
      <div class="profile-info">
        <div class="profile-name" id="profileName">Guest User</div>
        <div class="profile-role" id="profileRole">Not logged in</div>
      </div>
      <div class="dropdown-icon">▼</div>
      <div class="profile-dropdown" id="profileDropdownMenu">
        <a href="#" id="loginMenuItem"   class="dd-guest">🔑 Login</a>
        <a href="#" id="signupMenuItem"  class="dd-guest">✨ Sign Up</a>
        <a href="#" id="profileMenuItem" class="dd-user"  style="display:none;">👤 View Profile</a>
        <div class="dropdown-divider dd-user" style="display:none;"></div>
        <a href="#" id="signOutMenuItem" class="dd-user"  style="display:none;">🚪 Sign Out</a>${extraDropdownHTML}
      </div>
    </div>
  </div>`;

  document.body.insertBefore(nav, document.body.firstChild);
}

// Run synchronously so the nav is present before DOMContentLoaded fires
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', injectTopNav, { once: true });
} else {
  injectTopNav();
}

/* ═══════════════════════════════════════════
   SHARED SIDEBAR LOGO INJECTION
   Replaces any .strip-logo element's inner HTML
   with the canonical logo markup so every page
   with a dark sidebar shows an identical logo
   from one single source.
   ═══════════════════════════════════════════ */
function injectSidebarLogo() {
  const logoBlock = document.querySelector('.strip-logo');
  if (!logoBlock) return; // page has no sidebar — nothing to do

  logoBlock.innerHTML = `
    <img src="assets/logo.png" alt="Panacea"
         class="logo-image"
         onerror="this.style.display='none';">
    <div>
      <div class="logo">panacea</div>
      <div class="logo-sub">intelligent health companion</div>
    </div>`;
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', injectSidebarLogo, { once: true });
} else {
  injectSidebarLogo();
}



/* ═══════════════════════════════════════════
   GLOBAL CONFIG
   ═══════════════════════════════════════════ */
const API_BASE = 'http://localhost:8000';

/* ═══════════════════════════════════════════
   CACHE VALIDATION  (runs immediately)
   ═══════════════════════════════════════════ */
(function validateAndFixCache() {
  const userId       = localStorage.getItem('panacea_user_id');
  const sessionToken = localStorage.getItem('panacea_session_token');

  // Old-format user IDs (e.g. "user_abc") or non-numeric values are stale
  if (userId && (userId.startsWith('user_') || isNaN(parseInt(userId)))) {
    console.warn('⚠️ Detected invalid cached user ID:', userId, '— clearing cache…');
    _clearLocalStorage();
  }

  // Session token without matching user ID is an inconsistent state
  if (sessionToken && !localStorage.getItem('panacea_user_id')) {
    console.warn('⚠️ Session token found without user ID — clearing…');
    localStorage.removeItem('panacea_session_token');
  }
})();

/** Removes all Panacea keys from localStorage (internal helper). */
function _clearLocalStorage() {
  ['panacea_session_token', 'panacea_user_id', 'panacea_user_name',
   'panacea_user_email',   'panacea_user_gender', 'panacea_user_age',
   'panacea_user_type']
    .forEach(k => localStorage.removeItem(k));
}

/* ═══════════════════════════════════════════
   SESSION STATE
   ═══════════════════════════════════════════ */
let currentSessionToken = localStorage.getItem('panacea_session_token') || null;
let currentUserId       = localStorage.getItem('panacea_user_id')        || null;
let currentUser         = null;
let pendingCallback     = null;

/*
 * FIX #1 — Session ready promise
 * assessment.html's init() awaits this before reading currentUser,
 * eliminating the race condition where checkUserSession() hasn't
 * finished its async API call yet when the page tries to read the user.
 */
let _sessionReadyResolve;
const sessionReady = new Promise(resolve => { _sessionReadyResolve = resolve; });

/* ═══════════════════════════════════════════
   API HELPERS
   ═══════════════════════════════════════════ */
async function apiPost(path, body) {
  const res = await fetch(API_BASE + path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(currentSessionToken ? { 'X-Session-Token': currentSessionToken } : {}),
    },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

async function apiGet(path) {
  const res = await fetch(API_BASE + path, {
    headers: currentSessionToken ? { 'X-Session-Token': currentSessionToken } : {},
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

async function apiPut(path, body) {
  const res = await fetch(API_BASE + path, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...(currentSessionToken ? { 'X-Session-Token': currentSessionToken } : {}),
    },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

async function apiDelete(path) {
  const res = await fetch(API_BASE + path, {
    method: 'DELETE',
    headers: currentSessionToken ? { 'X-Session-Token': currentSessionToken } : {},
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

/* ═══════════════════════════════════════════
   SESSION PERSISTENCE
   ═══════════════════════════════════════════ */
function persistSession(token, userId, user) {
  localStorage.setItem('panacea_session_token', token);
  localStorage.setItem('panacea_user_id',       String(userId));
  localStorage.setItem('panacea_user_name',     user.name);
  localStorage.setItem('panacea_user_email',    user.email || '');
  localStorage.setItem('panacea_user_type',     user.user_type || 'patient');
  if (user.gender) localStorage.setItem('panacea_user_gender', user.gender);
  if (user.age)    localStorage.setItem('panacea_user_age',    String(user.age));
  currentSessionToken = token;
  currentUserId       = String(userId);
  currentUser         = user;
  console.log('✅ Session persisted for user:', user.name, '| type:', user.user_type || 'patient');
}

function clearSession() {
  _clearLocalStorage();
  currentSessionToken = null;
  currentUserId       = null;
  currentUser         = null;
  console.log('🗑️ Session cleared from localStorage');
}

/* ═══════════════════════════════════════════
   AGE GROUP
   ═══════════════════════════════════════════ */
function getAgeGroup(age) {
  if (!age) return 'adult';
  age = parseInt(age);
  if (age < 2)  return 'infant';
  if (age < 12) return 'child';
  if (age < 18) return 'adolescent';
  if (age < 65) return 'adult';
  return 'elderly';
}

/* ═══════════════════════════════════════════
   PROFILE UI  (nav pill)
   ═══════════════════════════════════════════ */
function updateNavPill(userOrLoggedIn) {
  let isLoggedIn, name, gender, age, userType;

  if (typeof userOrLoggedIn === 'boolean') {
    isLoggedIn = userOrLoggedIn;
    const P = (typeof PROFILE !== 'undefined') ? PROFILE : {};
    name     = P.name   || 'Guest User';
    gender   = P.gender || null;
    age      = P.age    || null;
    userType = P.user_type || localStorage.getItem('panacea_user_type') || 'patient';
  } else {
    const user = userOrLoggedIn;
    isLoggedIn = !!(user && user.name);
    name     = user?.name      || 'Guest User';
    gender   = user?.gender    || null;
    age      = user?.age       || null;
    userType = user?.user_type || user?.role
                               || localStorage.getItem('panacea_user_type')
                               || 'patient';
  }

  const nameEl   = document.getElementById('profileName');
  const roleEl   = document.getElementById('profileRole');
  const avatarEl = document.getElementById('profileAvatar');

  if (!nameEl || !roleEl || !avatarEl) return;

  document.querySelectorAll('.dd-guest').forEach(el => el.style.display = isLoggedIn ? 'none'  : 'block');
  document.querySelectorAll('.dd-user').forEach(el  => el.style.display = isLoggedIn ? 'block' : 'none');

  if (isLoggedIn) {
    nameEl.textContent = name;
    if (userType === 'doctor') {
      roleEl.textContent = '🩺 Doctor';
      avatarEl.style.background = 'linear-gradient(135deg, #1a6b8a, #0e4d6b)';
    } else {
      const gLabel = gender === 'male' ? 'Male' : gender === 'female' ? 'Female' : null;
      const aLabel = age ? `${age} yrs` : null;
      roleEl.textContent   = [gLabel, aLabel].filter(Boolean).join(' · ') || 'Member';
      avatarEl.style.background = 'linear-gradient(135deg, #2c7a6e, #3b82b6)';
    }
    avatarEl.textContent = name.charAt(0).toUpperCase();
  } else {
    nameEl.textContent   = 'Guest User';
    roleEl.textContent   = 'Not logged in';
    avatarEl.textContent = 'G';
    avatarEl.style.background = 'linear-gradient(135deg, #8b9a9e, #6b7a7e)';
  }
}

/** Alias used by index.html */
function updateProfileUI(user) { updateNavPill(user); }

/* ═══════════════════════════════════════════
   PAGE ACCESS CONTROL
   Doctors are only allowed on doctor-dashboard.html.
   Patients are blocked from doctor-dashboard.html.
   Called after session is confirmed.
   ═══════════════════════════════════════════ */
const _DOCTOR_PAGES  = new Set(['doctor-dashboard.html']);
const _PATIENT_PAGES = new Set([
  'assessment.html', 'pharmacy.html', 'consultation.html',
  'patient-dashboard.html', 'cart.html',
]);

function enforcePageAccess(userType) {
  const page = window.location.pathname.split('/').pop() || 'index.html';
  if (userType === 'doctor' && _PATIENT_PAGES.has(page)) {
    showToast('This page is not available for doctor accounts.');
    setTimeout(() => { window.location.replace('doctor-dashboard.html'); }, 1200);
    return false;
  }
  if (userType === 'patient' && _DOCTOR_PAGES.has(page)) {
    showToast('Access restricted to doctor accounts.');
    setTimeout(() => { window.location.replace('index.html'); }, 1200);
    return false;
  }
  return true;
}

/* ═══════════════════════════════════════════
   SESSION VERIFICATION
   FIX #2 — On assessment page, a failed verify no longer calls
   clearSession() which would wipe localStorage and cause the
   assessment to re-initialise as guest mid-session.
   ═══════════════════════════════════════════ */
async function checkUserSession() {
  const isAssessmentPage = window.location.pathname.includes('assessment');
  const token  = localStorage.getItem('panacea_session_token');
  const cached = {
    name:      localStorage.getItem('panacea_user_name'),
    email:     localStorage.getItem('panacea_user_email'),
    gender:    localStorage.getItem('panacea_user_gender'),
    age:       localStorage.getItem('panacea_user_age'),
    id:        localStorage.getItem('panacea_user_id'),
    user_type: localStorage.getItem('panacea_user_type') || 'patient',
  };

  if (!token) {
    updateNavPill(null);
    _sessionReadyResolve(); // resolve even for guests
    return;
  }

  if (cached.name) updateNavPill(cached); // optimistic paint

  try {
    const data = await apiPost('/api/auth/verify', { session_token: token });
    if (data.valid && data.user) {
      currentSessionToken = token;
      currentUserId       = String(data.user.id);
      currentUser         = data.user;
      localStorage.setItem('panacea_user_name',  data.user.name);
      localStorage.setItem('panacea_user_email', data.user.email || '');
      localStorage.setItem('panacea_user_type',  data.user.user_type || 'patient');
      if (data.user.gender) localStorage.setItem('panacea_user_gender', data.user.gender);
      if (data.user.age)    localStorage.setItem('panacea_user_age',    String(data.user.age));
      updateNavPill(data.user);
      enforcePageAccess(data.user.user_type || 'patient');
      console.log('✅ Session verified with backend');
    } else {
      if (!isAssessmentPage) {
        clearSession();
        updateNavPill(null);
      } else {
        if (cached.name) {
          currentSessionToken = token;
          currentUserId       = cached.id;
          currentUser         = cached;
          updateNavPill(cached);
          console.warn('⚠️ Token invalid but on assessment page — keeping cached session');
        } else {
          updateNavPill(null);
        }
      }
    }
  } catch (err) {
    console.warn('Session verify failed (API may be offline):', err.message);
    if (cached.name) {
      currentSessionToken = token;
      currentUserId       = cached.id;
      currentUser         = cached;
      updateNavPill(cached);
      // Still enforce page access from cached type when offline
      enforcePageAccess(cached.user_type);
    } else {
      if (!isAssessmentPage) {
        clearSession();
      }
      updateNavPill(null);
    }
  } finally {
    _sessionReadyResolve();
  }
}

/* ═══════════════════════════════════════════
   LOGIN
   ═══════════════════════════════════════════ */
async function doLogin(email, name, age, gender) {
  const data   = await apiPost('/api/profile/login', { email, name });
  const userId = data.user_id;
  const token  = data.session_token;
  const user   = { id: userId, email, name, age, gender, user_type: 'patient', profile_completed: data.profile_completed };

  persistSession(token, userId, user);
  updateNavPill(user);

  if (age || gender) {
    try {
      await apiPut(`/api/profile/${userId}`,
        { ...(age ? { age } : {}), ...(gender ? { gender } : {}) });
      user.age    = age    || user.age;
      user.gender = gender || user.gender;
      updateNavPill(user);
    } catch (e) { /* non-critical */ }
  }

  showToast(`Welcome to Panacea, ${name}! 👋`);

  if (pendingCallback && typeof pendingCallback === 'function') {
    const cb = pendingCallback; pendingCallback = null; cb();
  }

  if (!data.profile_completed) {
    setTimeout(() => showHealthMetricsModal(), 500);
  }

  return data;
}

/* ═══════════════════════════════════════════
   DOCTOR LOGIN
   ═══════════════════════════════════════════ */
async function doDoctorLogin(email, licenseNumber) {
  const data = await apiPost('/api/auth/doctor-login', { email, license_number: licenseNumber });
  const user = { id: data.user_id, email, name: data.name, user_type: 'doctor' };

  persistSession(data.session_token, data.user_id, user);
  updateNavPill(user);
  showToast(`Welcome back, Dr. ${data.name}! 🩺`);

  if (pendingCallback && typeof pendingCallback === 'function') {
    const cb = pendingCallback; pendingCallback = null; cb();
  } else {
    // Redirect to doctor dashboard after a brief pause
    setTimeout(() => { window.location.href = 'doctor-dashboard.html'; }, 800);
  }

  return data;
}

   /*FIX #3 — On assessment page, show a confirmation toast instead of
   silently redirecting, which caused the page to appear to "restart".
   ═══════════════════════════════════════════ */
async function logout() {
  console.log('🔴 LOGOUT FUNCTION CALLED');
  const tokenToRevoke = currentSessionToken;
  const isAssessmentPage = window.location.pathname.includes('assessment');

  clearSession();
  updateNavPill(null);

  const profileToggle = document.getElementById('profileToggle');
  if (profileToggle) profileToggle.classList.remove('active');

  if (typeof closeProfileModal === 'function') closeProfileModal();

  if (tokenToRevoke) {
    try {
      const response = await fetch(`${API_BASE}/api/auth/logout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_token: tokenToRevoke }),
      });
      showToast(response.ok ? 'You have been signed out successfully.' : 'You have been signed out.');
    } catch (e) {
      console.error('❌ Logout API network error:', e.message);
      showToast('You have been signed out (local only).');
    }
  } else {
    showToast('You have been signed out.');
  }

  /*
   * FIX #3 — Only redirect if NOT on the assessment page.
   * Previously this redirect caused the assessment to flash and restart
   * because the page reloaded just as results were appearing.
   * On assessment page, we let the user stay and see a signed-out state.
   */
  if (!isAssessmentPage) {
    setTimeout(() => window.location.href = 'index.html', 800);
  }
  console.log('🟢 LOGOUT FUNCTION COMPLETED');
}

/* ═══════════════════════════════════════════
   HEALTH METRICS
   ═══════════════════════════════════════════ */
async function saveHealthMetrics(metrics) {
  if (!currentUserId) return;
  try {
    await apiPut(`/api/profile/${currentUserId}/metrics`, metrics);
    showToast('Health information saved successfully! ✅');
  } catch (e) {
    showToast('Could not save health info: ' + e.message);
  }
}

/* ═══════════════════════════════════════════
   PROFILE DETAILS MODAL
   ═══════════════════════════════════════════ */
async function showUserProfileDetails() {
  showProfileModal();
  const content = document.getElementById('profileContent');
  if (!content) return;
  content.innerHTML = '<p style="text-align:center;padding:20px;color:#8b9a9e;">Loading…</p>';

  try {
    const data = await apiGet(`/api/profile/${currentUserId}`);
    const p = data.profile;
    const m = data.health_metrics;

    const genderDisplay = p.gender === 'male'   ? 'Male'
                        : p.gender === 'female' ? 'Female'
                        : p.gender === 'other'  ? 'Other' : 'Not specified';
    const ageDisplay    = p.age ? `${p.age} years` : 'Not specified';
    const initial       = p.name.charAt(0).toUpperCase();
    const joined        = p.created_at
      ? new Date(p.created_at).toLocaleDateString('en-IN', { month: 'short', year: 'numeric' })
      : '2025';

    const metricsHTML = m ? `
      <div style="margin-top:12px;padding-top:12px;border-top:1px solid #e2e4e0;">
        <p style="font-size:11px;color:#8b9a9e;margin-bottom:8px;text-transform:uppercase;letter-spacing:1px;">Health Info</p>
        <div style="display:flex;flex-wrap:wrap;gap:8px;">
          ${m.blood_group ? `<span style="background:#e0f0ec;color:#2c7a6e;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:500;">🩸 ${m.blood_group}</span>` : ''}
          ${m.height_cm   ? `<span style="background:#eef2fc;color:#3b82b6;padding:4px 12px;border-radius:20px;font-size:12px;">📏 ${m.height_cm} cm</span>` : ''}
          ${m.weight_kg   ? `<span style="background:#eef2fc;color:#3b82b6;padding:4px 12px;border-radius:20px;font-size:12px;">⚖️ ${m.weight_kg} kg</span>` : ''}
        </div>
      </div>` : '';

    content.innerHTML = `
      <div style="text-align:center;margin-bottom:24px;">
        <div style="width:80px;height:80px;background:linear-gradient(135deg,#2c7a6e,#3b82b6);border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;box-shadow:0 4px 15px rgba(44,122,110,0.3);">
          <span style="font-size:36px;color:white;font-weight:600;">${initial}</span>
        </div>
        <h3 style="font-size:22px;font-weight:600;color:#1e2a2e;margin-bottom:4px;">${p.name}</h3>
        <p style="color:#8b9a9e;font-size:13px;">Member since ${joined}</p>
      </div>
      <div style="background:#f5f3f0;border-radius:20px;padding:20px;margin:16px 0;">
        <div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid #e2e4e0;">
          <span style="font-size:20px;">📧</span>
          <div style="flex:1;"><p style="font-size:11px;color:#8b9a9e;margin-bottom:2px;">Email</p><p style="font-size:14px;color:#1e2a2e;font-weight:500;">${p.email}</p></div>
        </div>
        <div style="display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid #e2e4e0;">
          <span style="font-size:20px;">🎂</span>
          <div style="flex:1;"><p style="font-size:11px;color:#8b9a9e;margin-bottom:2px;">Age</p><p style="font-size:14px;color:#1e2a2e;font-weight:500;">${ageDisplay}</p></div>
        </div>
        <div style="display:flex;align-items:center;gap:12px;padding:12px 0;">
          <span style="font-size:20px;">👤</span>
          <div style="flex:1;"><p style="font-size:11px;color:#8b9a9e;margin-bottom:2px;">Gender</p><p style="font-size:14px;color:#1e2a2e;font-weight:500;">${genderDisplay}</p></div>
        </div>
        ${metricsHTML}
      </div>
      <div style="background:linear-gradient(135deg,#e0f0ec33,#eef2fc33);border-radius:20px;padding:16px;text-align:center;">
        <p style="font-size:12px;color:#2c7a6e;">✓ Profile secured with AES-256 encryption</p>
        <p style="font-size:11px;color:#8b9a9e;margin-top:6px;">Your data is private and never shared</p>
      </div>`;

  } catch (e) {
    console.error('Profile fetch error:', e);
    const name   = localStorage.getItem('panacea_user_name')   || 'User';
    const email  = localStorage.getItem('panacea_user_email')  || '—';
    const age    = localStorage.getItem('panacea_user_age');
    const gender = localStorage.getItem('panacea_user_gender');
    const gDisp  = gender === 'male' ? 'Male' : gender === 'female' ? 'Female' : 'Not specified';
    content.innerHTML = `
      <div style="text-align:center;padding:10px 0 20px;">
        <div style="width:64px;height:64px;background:linear-gradient(135deg,#2c7a6e,#3b82b6);border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 12px;">
          <span style="font-size:28px;color:white;font-weight:600;">${name.charAt(0).toUpperCase()}</span>
        </div>
        <h3 style="font-size:20px;font-weight:600;">${name}</h3>
        <p style="color:#8b9a9e;font-size:13px;margin-top:4px;">${email}</p>
        ${age ? `<p style="color:#8b9a9e;font-size:13px;">${age} yrs · ${gDisp}</p>` : ''}
        <p style="font-size:12px;color:#e74c3c;margin-top:12px;">⚠ Could not reach server — showing cached data.</p>
      </div>`;
  }
}

/* ═══════════════════════════════════════════
   MODAL INJECTION
   Dynamically injects all shared modals into
   every page that includes script.js, so login
   / signup / health-metrics / profile modals
   work on assessment.html, pharmacy.html, etc.
   Skips injection if the element already exists
   (i.e. index.html which has them hard-coded).
   ═══════════════════════════════════════════ */
function injectModals() {
  if (!document.getElementById('loginModal')) {
    const html = `
<!-- ══ LOGIN MODAL (injected by script.js) ══ -->
<div id="loginModal" class="modal-overlay">
  <div class="modal-container">
    <div class="modal-header">
      <h2>Welcome back</h2>
      <button class="modal-close" onclick="closeLoginModal()">&times;</button>
    </div>
    <!-- Tab switcher for Patient / Doctor -->
    <div style="display:flex;gap:0;margin-bottom:20px;border-radius:12px;overflow:hidden;border:1px solid var(--border-light);">
      <button id="loginTabPatient" onclick="switchLoginTab('patient')"
        style="flex:1;padding:10px;border:none;cursor:pointer;font-size:13px;font-weight:600;background:var(--accent-teal);color:#fff;transition:all .2s;">
        👤 Patient
      </button>
      <button id="loginTabDoctor" onclick="switchLoginTab('doctor')"
        style="flex:1;padding:10px;border:none;cursor:pointer;font-size:13px;font-weight:600;background:var(--bg-card);color:var(--text-secondary);transition:all .2s;">
        🩺 Doctor
      </button>
    </div>
    <!-- Patient login form -->
    <form id="loginForm" autocomplete="on">
      <div class="form-group">
        <label>Email <span class="required">*</span></label>
        <input type="email" id="loginEmail" required placeholder="your@email.com">
      </div>
      <div class="form-group">
        <label>Name <span class="required">*</span></label>
        <input type="text" id="loginName" required placeholder="Your full name">
      </div>
      <div class="form-group">
        <label>Age (optional)</label>
        <input type="number" id="loginAge" placeholder="Your age">
      </div>
      <div class="form-group">
        <label>Gender (optional)</label>
        <select id="loginGender">
          <option value="">Prefer not to say</option>
          <option value="female">Female</option>
          <option value="male">Male</option>
          <option value="other">Other</option>
        </select>
      </div>
      <button type="submit" class="btn-submit">Login →</button>
      <p class="modal-tab-switch">Don't have an account? <a onclick="switchToSignup()" style="cursor:pointer;">Sign Up</a></p>
      <p class="form-note">By continuing, you agree to our Terms of Service and Privacy Policy.</p>
    </form>
    <!-- Doctor login form (hidden by default) -->
    <form id="doctorLoginForm" style="display:none;" autocomplete="on">
      <div class="form-group">
        <label>Doctor Email <span class="required">*</span></label>
        <input type="email" id="doctorLoginEmail" required placeholder="doctor@hospital.com">
      </div>
      <div class="form-group">
        <label>License Number <span class="required">*</span></label>
        <input type="password" id="doctorLoginLicense" required placeholder="Your medical license number">
      </div>
      <button type="submit" class="btn-submit">Doctor Login →</button>
      <p class="modal-tab-switch">New doctor? <a onclick="switchToDoctorRegister()" style="cursor:pointer;">Register here</a></p>
      <p class="form-note">Access is restricted to verified healthcare professionals.</p>
    </form>
  </div>
</div>

<!-- ══ SIGN UP MODAL (injected by script.js) ══ -->
<div id="signupModal" class="modal-overlay">
  <div class="modal-container">
    <div class="modal-header">
      <h2>Create your account</h2>
      <button class="modal-close" onclick="closeSignupModal()">&times;</button>
    </div>
    <form id="signupForm" autocomplete="on">
      <div class="form-group">
        <label>Full Name <span class="required">*</span></label>
        <input type="text" id="signupName" required placeholder="Your full name">
      </div>
      <div class="form-group">
        <label>Email <span class="required">*</span></label>
        <input type="email" id="signupEmail" required placeholder="your@email.com">
      </div>
      <div class="form-group">
        <label>Age (optional)</label>
        <input type="number" id="signupAge" placeholder="Your age">
      </div>
      <div class="form-group">
        <label>Gender (optional)</label>
        <select id="signupGender">
          <option value="">Prefer not to say</option>
          <option value="female">Female</option>
          <option value="male">Male</option>
          <option value="other">Other</option>
        </select>
      </div>
      <button type="submit" class="btn-submit">Create Account →</button>
      <p class="modal-tab-switch">Already have an account? <a onclick="switchToLogin()" style="cursor:pointer;">Login</a></p>
      <p class="form-note">By signing up, you agree to our Terms of Service and Privacy Policy.</p>
    </form>
  </div>
</div>

<!-- ══ DOCTOR REGISTER MODAL (injected by script.js) ══ -->
<div id="doctorRegisterModal" class="modal-overlay">
  <div class="modal-container" style="max-width:520px;">
    <div class="modal-header">
      <h2>🩺 Doctor Registration</h2>
      <button class="modal-close" onclick="closeDoctorRegisterModal()">&times;</button>
    </div>
    <p style="color:var(--text-secondary);font-size:13px;margin-bottom:16px;">
      Register as a verified healthcare professional. Your license number will be used to log in.
    </p>
    <form id="doctorRegisterForm" autocomplete="on">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
        <div class="form-group" style="grid-column:1/-1;">
          <label>Full Name <span class="required">*</span></label>
          <input type="text" id="drName" required placeholder="Dr. Full Name">
        </div>
        <div class="form-group" style="grid-column:1/-1;">
          <label>Email <span class="required">*</span></label>
          <input type="email" id="drEmail" required placeholder="doctor@hospital.com">
        </div>
        <div class="form-group" style="grid-column:1/-1;">
          <label>License Number <span class="required">*</span></label>
          <input type="text" id="drLicense" required placeholder="Medical license number (used to log in)">
        </div>
        <div class="form-group">
          <label>Specialization</label>
          <input type="text" id="drSpec" placeholder="e.g. Cardiology">
        </div>
        <div class="form-group">
          <label>Qualification</label>
          <input type="text" id="drQual" placeholder="e.g. MBBS, MD">
        </div>
        <div class="form-group">
          <label>Experience (years)</label>
          <input type="number" id="drExp" min="0" placeholder="e.g. 10">
        </div>
        <div class="form-group">
          <label>Consultation Fee (₹)</label>
          <input type="number" id="drFee" min="0" placeholder="e.g. 500">
        </div>
        <div class="form-group" style="grid-column:1/-1;">
          <label>Phone</label>
          <input type="tel" id="drPhone" placeholder="Contact number">
        </div>
        <div class="form-group" style="grid-column:1/-1;">
          <label>Bio / About</label>
          <textarea id="drBio" rows="3" placeholder="Brief professional bio…"
            style="width:100%;padding:10px 14px;border:1px solid var(--border-light);border-radius:12px;resize:vertical;font-family:inherit;font-size:14px;"></textarea>
        </div>
      </div>
      <button type="submit" class="btn-submit" style="margin-top:4px;">Register as Doctor →</button>
      <p class="modal-tab-switch">Already registered? <a onclick="switchToDoctorLogin()" style="cursor:pointer;">Login here</a></p>
    </form>
  </div>
</div>

<!-- ══ HEALTH METRICS MODAL (injected by script.js) ══ -->
<div id="healthMetricsModal" class="modal-overlay">
  <div class="modal-container">
    <div class="modal-header">
      <h2>Optional Health Details</h2>
      <button class="modal-close" onclick="closeHealthMetricsModal()">&times;</button>
    </div>
    <p style="color:var(--text-secondary);margin-bottom:20px;font-size:14px;">You can skip this and add later from your profile settings.</p>
    <form id="healthMetricsForm">
      <div class="form-group"><label>Height (cm)</label><input type="number" id="healthHeight" step="0.1" placeholder="e.g., 165"></div>
      <div class="form-group"><label>Weight (kg)</label><input type="number" id="healthWeight" step="0.1" placeholder="e.g., 60"></div>
      <div class="form-group">
        <label>Blood Group</label>
        <select id="healthBloodGroup">
          <option value="">Select</option>
          <option>A+</option><option>A-</option><option>B+</option><option>B-</option>
          <option>O+</option><option>O-</option><option>AB+</option><option>AB-</option>
        </select>
      </div>
      <div class="form-group"><label>Allergies (comma separated)</label><input type="text" id="healthAllergies" placeholder="e.g., pollen, peanuts"></div>
      <div class="form-group"><label>Emergency Contact Name</label><input type="text" id="emergencyName" placeholder="Emergency contact person"></div>
      <div class="form-group"><label>Emergency Contact Phone</label><input type="tel" id="emergencyPhone" placeholder="Phone number"></div>
      <div style="display:flex;gap:12px;">
        <button type="button" class="btn-secondary" onclick="skipHealthMetrics()">Skip for now</button>
        <button type="submit" class="btn-submit">Save health info</button>
      </div>
    </form>
  </div>
</div>

<!-- ══ PROFILE MODAL (injected by script.js) ══ -->
<div id="profileModal" class="modal-overlay">
  <div class="modal-container" style="max-width:450px;">
    <div class="modal-header">
      <h2>👤 My Profile</h2>
      <button class="modal-close" onclick="closeProfileModal()">&times;</button>
    </div>
    <div id="profileContent" style="padding:10px 0;"></div>
    <button class="btn-secondary" onclick="closeProfileModal()" style="margin-top:20px;">Close</button>
  </div>
</div>`;

    const wrapper = document.createElement('div');
    wrapper.innerHTML = html;
    while (wrapper.firstChild) {
      document.body.appendChild(wrapper.firstChild);
    }

    // Wire up all modal form handlers now that the elements exist
    _bindModalForms();
  }
}

/** Attach form submit listeners and menu-item click listeners. */
function _bindModalForms() {
  // ── Patient login form ──
  const loginForm = document.getElementById('loginForm');
  if (loginForm && !loginForm.dataset.bound) {
    loginForm.dataset.bound = '1';
    loginForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn    = e.target.querySelector('button[type="submit"]');
      const email  = document.getElementById('loginEmail').value.trim();
      const name   = document.getElementById('loginName').value.trim();
      const age    = document.getElementById('loginAge').value    ? parseInt(document.getElementById('loginAge').value)    : null;
      const gender = document.getElementById('loginGender').value || null;
      setButtonLoading(btn, true);
      try {
        await doLogin(email, name, age, gender);
        closeLoginModal();
        loginForm.reset();
      } catch (err) {
        showToast('Login failed: ' + err.message);
      } finally {
        setButtonLoading(btn, false, 'Login →');
      }
    });
  }

  // ── Doctor login form ──
  const doctorLoginForm = document.getElementById('doctorLoginForm');
  if (doctorLoginForm && !doctorLoginForm.dataset.bound) {
    doctorLoginForm.dataset.bound = '1';
    doctorLoginForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn     = e.target.querySelector('button[type="submit"]');
      const email   = document.getElementById('doctorLoginEmail').value.trim();
      const license = document.getElementById('doctorLoginLicense').value.trim();
      setButtonLoading(btn, true);
      try {
        await doDoctorLogin(email, license);
        closeLoginModal();
        doctorLoginForm.reset();
      } catch (err) {
        showToast('Doctor login failed: ' + err.message);
      } finally {
        setButtonLoading(btn, false, 'Doctor Login →');
      }
    });
  }

  // ── Doctor register form ──
  const doctorRegisterForm = document.getElementById('doctorRegisterForm');
  if (doctorRegisterForm && !doctorRegisterForm.dataset.bound) {
    doctorRegisterForm.dataset.bound = '1';
    doctorRegisterForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = e.target.querySelector('button[type="submit"]');
      const payload = {
        name:              document.getElementById('drName').value.trim(),
        email:             document.getElementById('drEmail').value.trim(),
        license_number:    document.getElementById('drLicense').value.trim(),
        specialization:    document.getElementById('drSpec').value.trim()  || null,
        qualification:     document.getElementById('drQual').value.trim()  || null,
        experience_years:  document.getElementById('drExp').value          ? parseInt(document.getElementById('drExp').value)    : null,
        consultation_fee:  document.getElementById('drFee').value          ? parseFloat(document.getElementById('drFee').value)  : null,
        phone:             document.getElementById('drPhone').value.trim() || null,
        bio:               document.getElementById('drBio').value.trim()   || null,
      };
      setButtonLoading(btn, true);
      try {
        const data = await apiPost('/api/auth/doctor-register', payload);
        persistSession(data.session_token, data.user_id, { name: data.name, email: payload.email, user_type: 'doctor' });
        updateNavPill({ name: data.name, role: 'Doctor' });
        closeDoctorRegisterModal();
        doctorRegisterForm.reset();
        showToast(`Welcome, Dr. ${data.name}! Your account has been created. 🩺`);
        setTimeout(() => { window.location.href = 'doctor-dashboard.html'; }, 900);
      } catch (err) {
        showToast('Registration failed: ' + err.message);
      } finally {
        setButtonLoading(btn, false, 'Register as Doctor →');
      }
    });
  }

  // ── Sign-up form ──
  const signupForm = document.getElementById('signupForm');
  if (signupForm && !signupForm.dataset.bound) {
    signupForm.dataset.bound = '1';
    signupForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn    = e.target.querySelector('button[type="submit"]');
      const name   = document.getElementById('signupName').value.trim();
      const email  = document.getElementById('signupEmail').value.trim();
      const age    = document.getElementById('signupAge').value    ? parseInt(document.getElementById('signupAge').value)    : null;
      const gender = document.getElementById('signupGender').value || null;
      setButtonLoading(btn, true);
      try {
        await doLogin(email, name, age, gender);
        closeSignupModal();
        signupForm.reset();
      } catch (err) {
        showToast('Sign up failed: ' + err.message);
      } finally {
        setButtonLoading(btn, false, 'Create Account →');
      }
    });
  }

  // ── Health metrics form ──
  const hForm = document.getElementById('healthMetricsForm');
  if (hForm && !hForm.dataset.bound) {
    hForm.dataset.bound = '1';
    hForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = e.target.querySelector('button[type="submit"]');
      const metrics = {
        height_cm:               document.getElementById('healthHeight').value    ? parseFloat(document.getElementById('healthHeight').value) : null,
        weight_kg:               document.getElementById('healthWeight').value    ? parseFloat(document.getElementById('healthWeight').value) : null,
        blood_group:             document.getElementById('healthBloodGroup').value || null,
        allergies:               document.getElementById('healthAllergies').value  ? document.getElementById('healthAllergies').value.split(',').map(s => s.trim()) : null,
        emergency_contact_name:  document.getElementById('emergencyName').value   || null,
        emergency_contact_phone: document.getElementById('emergencyPhone').value  || null,
      };
      Object.keys(metrics).forEach(k => metrics[k] === null && delete metrics[k]);
      setButtonLoading(btn, true);
      await saveHealthMetrics(metrics);
      closeHealthMetricsModal();
      hForm.reset();
      setButtonLoading(btn, false, 'Save health info');
    });
  }

  // ── Nav dropdown menu items ──
  const loginItem = document.getElementById('loginMenuItem');
  if (loginItem && !loginItem.dataset.bound) {
    loginItem.dataset.bound = '1';
    loginItem.addEventListener('click', (e) => {
      e.preventDefault(); e.stopPropagation();
      document.getElementById('profileToggle')?.classList.remove('active');
      showLoginModal();
    });
  }
  const signupItem = document.getElementById('signupMenuItem');
  if (signupItem && !signupItem.dataset.bound) {
    signupItem.dataset.bound = '1';
    signupItem.addEventListener('click', (e) => {
      e.preventDefault(); e.stopPropagation();
      document.getElementById('profileToggle')?.classList.remove('active');
      showSignupModal();
    });
  }
  const profileItem = document.getElementById('profileMenuItem');
  if (profileItem && !profileItem.dataset.bound) {
    profileItem.dataset.bound = '1';
    profileItem.addEventListener('click', (e) => {
      e.preventDefault(); e.stopPropagation();
      document.getElementById('profileToggle')?.classList.remove('active');
      if (currentUserId && currentSessionToken) showUserProfileDetails();
      else showLoginModal();
    });
  }
}

/* Run injector once DOM is ready */
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', injectModals, { once: true });
} else {
  injectModals();
}

/* ═══════════════════════════════════════════
   MODAL HELPERS
   ═══════════════════════════════════════════ */
function showLoginModal()  {
  // Ensure patient tab is visible by default
  switchLoginTab('patient');
  document.getElementById('signupModal')?.classList.remove('active');
  document.getElementById('doctorRegisterModal')?.classList.remove('active');
  document.getElementById('loginModal')?.classList.add('active');
}
function closeLoginModal() {
  document.getElementById('loginModal')?.classList.remove('active');
  pendingCallback = null;
}
function showSignupModal()  {
  document.getElementById('loginModal')?.classList.remove('active');
  document.getElementById('doctorRegisterModal')?.classList.remove('active');
  document.getElementById('signupModal')?.classList.add('active');
}
function closeSignupModal() {
  document.getElementById('signupModal')?.classList.remove('active');
  pendingCallback = null;
}
function switchToSignup() { showSignupModal(); }
function switchToLogin()  { showLoginModal();  }

function showDoctorRegisterModal() {
  document.getElementById('loginModal')?.classList.remove('active');
  document.getElementById('signupModal')?.classList.remove('active');
  document.getElementById('doctorRegisterModal')?.classList.add('active');
}
function closeDoctorRegisterModal() {
  document.getElementById('doctorRegisterModal')?.classList.remove('active');
}
function switchToDoctorRegister() { showDoctorRegisterModal(); }
function switchToDoctorLogin() {
  closeDoctorRegisterModal();
  showLoginModal();
  switchLoginTab('doctor');
}

/** Toggle between Patient and Doctor tabs inside the login modal */
function switchLoginTab(tab) {
  const patientForm = document.getElementById('loginForm');
  const doctorForm  = document.getElementById('doctorLoginForm');
  const patientTab  = document.getElementById('loginTabPatient');
  const doctorTab   = document.getElementById('loginTabDoctor');
  if (!patientForm || !doctorForm) return;

  const activeBg   = 'var(--accent-teal)';
  const inactiveBg = 'var(--bg-card)';

  if (tab === 'patient') {
    patientForm.style.display = '';
    doctorForm.style.display  = 'none';
    if (patientTab) { patientTab.style.background = activeBg;   patientTab.style.color = '#fff'; }
    if (doctorTab)  { doctorTab.style.background  = inactiveBg; doctorTab.style.color  = 'var(--text-secondary)'; }
  } else {
    patientForm.style.display = 'none';
    doctorForm.style.display  = '';
    if (doctorTab)  { doctorTab.style.background  = activeBg;   doctorTab.style.color  = '#fff'; }
    if (patientTab) { patientTab.style.background = inactiveBg; patientTab.style.color = 'var(--text-secondary)'; }
  }
}

function showHealthMetricsModal()  { document.getElementById('healthMetricsModal')?.classList.add('active'); }
function closeHealthMetricsModal() { document.getElementById('healthMetricsModal')?.classList.remove('active'); }
function skipHealthMetrics()       { closeHealthMetricsModal(); showToast('You can add health metrics later from your profile.'); }

function showProfileModal()  { document.getElementById('profileModal')?.classList.add('active'); }
function closeProfileModal() { document.getElementById('profileModal')?.classList.remove('active'); }

/* ═══════════════════════════════════════════
   NAVIGATION GUARD
   ═══════════════════════════════════════════ */
function requireAuthNav(destination) {
  if (currentUserId && currentSessionToken) {
    const userType = localStorage.getItem('panacea_user_type') || 'patient';
    // Doctors should only ever go to the doctor dashboard
    if (userType === 'doctor' && _PATIENT_PAGES.has(destination)) {
      showToast('That page is for patients. Redirecting to your dashboard.');
      navigateTo('doctor-dashboard.html');
      return;
    }
    navigateTo(destination);
  } else {
    showToast('Please login to continue.');
    showLoginModal();
  }
}

/**
 * Smooth page navigation — fades out the current page,
 * then navigates. Works on all pages that wrap content
 * in .page-transition-wrapper (or just fades body).
 */
function navigateTo(destination) {
  // Mark body so CSS applies the exit animation
  document.body.classList.add('page-leaving');

  // After the exit animation completes, navigate
  setTimeout(() => {
    window.location.href = destination;
  }, 290); // matches pageExit duration (0.28s)
}

/* ═══════════════════════════════════════════
   UTILITIES
   ═══════════════════════════════════════════ */
function showToast(message, duration = 3500) {
  const toast = document.createElement('div');
  toast.className   = 'toast-notification';
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), duration);
}

function setButtonLoading(btn, loading, originalText) {
  if (loading) {
    btn.disabled         = true;
    btn.dataset.original = btn.textContent;
    btn.textContent      = 'Please wait…';
  } else {
    btn.disabled    = false;
    btn.textContent = originalText || btn.dataset.original || btn.textContent;
  }
}

/* ═══════════════════════════════════════════
   AUTO-INIT  (runs once DOM is ready)
   ═══════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {

  /* ── Profile pill toggle ── */
  const profileSection = document.getElementById('profileToggle');
  if (profileSection) {
    profileSection.addEventListener('click', e => {
      e.stopPropagation();
      profileSection.classList.toggle('active');
    });
    document.addEventListener('click', e => {
      if (!profileSection.contains(e.target)) profileSection.classList.remove('active');
    });
  }

  /* ── Sign-out button (works on all pages) ── */
  const signOutBtn = document.getElementById('signOutMenuItem');
  if (signOutBtn) {
    const fresh = signOutBtn.cloneNode(true);
    signOutBtn.parentNode.replaceChild(fresh, signOutBtn);
    fresh.addEventListener('click', async e => {
      e.preventDefault();
      e.stopPropagation();
      await logout();
    });
    console.log('✅ Sign-out listener attached');
  }

  /* ── Scroll-reveal for .fade-up elements ── */
  const fadeEls = document.querySelectorAll('.fade-up');
  if (fadeEls.length) {
    const observer = new IntersectionObserver(
      entries => entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          observer.unobserve(entry.target);
        }
      }),
      { threshold: 0.12 }
    );
    fadeEls.forEach(el => observer.observe(el));
  }

  /* ── Active nav link ── */
  const currentPage = window.location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('.nav-links a[href]').forEach(link => {
    if (link.getAttribute('href') === currentPage) link.classList.add('active');
  });

  console.log('🚀 Panacea shared JS loaded — DOM ready');
});

/* ═══════════════════════════════════════════
   START SESSION CHECK IMMEDIATELY
   Must run outside DOMContentLoaded so that
   sessionReady resolves before any page's own
   DOMContentLoaded tries to await it.
   (assessment.html's init() awaits sessionReady)
   ═══════════════════════════════════════════ */
console.log('🚀 Panacea shared JS loaded — initialising session…');
checkUserSession();