/* ============================================================
   Profource Marktmonitor — shared auth helpers
   Include this AFTER the supabase-js <script> tag on every page.
   ============================================================ */

// Guard: if this script is accidentally included twice on the same page
// (duplicate <script src="auth.js"> tag, caching quirk, etc.), skip the
// second run instead of crashing with "Identifier already declared".
if (!window.__pfAuthLoaded) {
  window.__pfAuthLoaded = true;

  // --- 1. Fill these in with your Supabase project's values ---
  // Project Settings > API > Project URL / anon public key
const SUPABASE_URL = 'https://lzbzwfxdcszirbnpexij.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imx6Ynp3ZnhkY3N6aXJibnBleGlqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODUyMjcwNzAsImV4cCI6MjEwMDgwMzA3MH0.DX31BPrjI1vjOIgwWJfCbxnbmrZBJBO7ilftbzWD6Yw';

  window.supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

  // --- 2. Get the current session, or null if not logged in ---
  window.getSession = async function getSession() {
    const { data: { session } } = await window.supabase.auth.getSession();
    return session;
  };

  // --- 3. Get the current user's profile row (role + status), or null ---
  window.getProfile = async function getProfile() {
    const session = await window.getSession();
    if (!session) return null;
    const { data, error } = await window.supabase
      .from('profiles')
      .select('id, email, role, status')
      .eq('id', session.user.id)
      .single();
    if (error) return null;
    return data;
  };

  // --- 4. Call at the top of any protected page (e.g. index.html) ---
  // Redirects to login if not signed in, or if the account is blocked.
  window.requireUser = async function requireUser() {
    const profile = await window.getProfile();
    if (!profile) {
      window.location.href = 'login.html';
      return null;
    }
    if (profile.status === 'blocked') {
      await window.supabase.auth.signOut();
      alert('Dit account is geblokkeerd. Neem contact op met een beheerder.');
      window.location.href = 'login.html';
      return null;
    }
    return profile;
  };

  // --- 5. Call at the top of admin.html ---
  // Redirects to the dashboard if the user isn't an admin.
  window.requireAdmin = async function requireAdmin() {
    const profile = await window.requireUser();
    if (!profile) return null;
    if (profile.role !== 'admin') {
      window.location.href = 'index.html';
      return null;
    }
    return profile;
  };

  // --- 5b. Page access (managed in admin.html) ---
  // Returns { pages: [{key, system, system_label, label, url}], keys: Set<string> }
  // with the pages this profile may open. Admins always get every page.
  window.getPageAccess = async function getPageAccess(profile) {
    const { data: pages } = await window.supabase
      .from('app_pages')
      .select('key, system, system_label, label, url, sort_order')
      .order('sort_order');
    const all = pages || [];
    if (profile.role === 'admin') {
      return { pages: all, keys: new Set(all.map(p => p.key)) };
    }
    const { data: rows } = await window.supabase
      .from('user_page_access')
      .select('page_key')
      .eq('user_id', profile.id);
    const keys = new Set((rows || []).map(r => r.page_key));
    return { pages: all.filter(p => keys.has(p.key)), keys };
  };

  // Full-screen "no access" notice, with links to the pages the user can open.
  function showNoAccess(pageKey, access) {
    const esc = s => String(s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const links = access.pages
      .filter(p => p.url && p.key !== pageKey)
      .map(p => `<a href="${esc(p.url)}" style="color:#4ab8e6;display:block;margin:6px 0">`
        + `${esc(p.system_label)} · ${esc(p.label)}</a>`)
      .join('');
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;inset:0;z-index:99999;display:flex;align-items:center;'
      + 'justify-content:center;background:#101b28;color:#eef3f8;'
      + 'font-family:"IBM Plex Sans",system-ui,sans-serif;padding:24px;text-align:center';
    box.innerHTML = `<div style="max-width:380px">
      <h1 style="font-size:20px;margin:0 0 10px">Geen toegang</h1>
      <p style="color:#8ea2b8;font-size:14px;line-height:1.5;margin:0 0 18px">
        Je account heeft geen toegang tot deze pagina. Neem contact op met een beheerder als dit niet klopt.</p>
      ${links ? `<div style="font-size:14px;margin-bottom:18px">Wel toegang tot:${links}</div>` : ''}
      <button id="pfNoAccessLogout" style="font:inherit;font-size:13px;padding:8px 16px;border-radius:8px;
        cursor:pointer;border:1px solid rgba(25,157,217,.4);background:rgba(25,157,217,.1);color:#199DD9">Uitloggen</button>
    </div>`;
    document.body.appendChild(box);
    document.getElementById('pfNoAccessLogout').onclick = window.logout;
  }

  // --- 5c. Call at the top of any page that needs a specific page permission ---
  // Same checks as requireUser, then verifies access to `pageKey`
  // (e.g. 'leadbeheer.dashboard'). Returns the profile with `.pages` (Set of
  // accessible keys) and `.pageList`, or null after showing the no-access notice.
  window.requirePage = async function requirePage(pageKey) {
    const profile = await window.requireUser();
    if (!profile) return null;
    const access = await window.getPageAccess(profile);
    if (!access.keys.has(pageKey)) {
      showNoAccess(pageKey, access);
      return null;
    }
    profile.pages = access.keys;
    profile.pageList = access.pages;
    return profile;
  };

  // --- 6. Logout helper — wire this to any "Uitloggen" button ---
  window.logout = async function logout() {
    await window.supabase.auth.signOut();
    window.location.href = 'login.html';
  };
}
