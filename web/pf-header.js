/* ============================================================
   Profource — gedeelde app-balk (Scrappingtool · Leadbeheer · Maatman · Beheer)
   ------------------------------------------------------------
   Eén bestand dat in elke app wordt geladen, zodat de balk overal gelijk is
   en alleen hier hoeft te worden aangepast. De apps staan op verschillende
   GitHub-pagina's, dus laad hem vanaf deze vaste plek:

     <script src="auth.js"></script>
     <script src="https://kloos1123.github.io/Scrappingtool-v2/pf-header.js"
             data-system="leadbeheer"></script>

   data-system = de `system` van de huidige app in public.app_pages
   ('scrappingtool' | 'leadbeheer' | 'maatman'); die krijgt de actieve markering.
   In Scrappingtool zelf kan de src relatief zijn ("pf-header.js").

   Wat erin staat komt uit het paginaregister (public.app_pages, zie
   supabase/page_access.sql): de gebruiker ziet alleen de apps waar hij toegang
   toe heeft, en een app zonder `url` wordt niet getoond. Beheerders zien
   daarnaast "Beheer". Vereist auth.js (window.getProfile / getPageAccess).
   De balk staat in een shadow root, zodat de stijl van de app er niet
   doorheen lekt en andersom.
   ============================================================ */
(function () {
  if (window.__pfHeaderLoaded) return;
  window.__pfHeaderLoaded = true;

  var script = document.currentScript;
  var current = (script && script.dataset.system) || '';
  // admin.html staat naast dit bestand, waar het ook gehost wordt
  var adminUrl = script && script.src ? new URL('admin.html', script.src).href : null;

  var CSS = [
    ':host{all:initial;display:block}',
    '.bar{box-sizing:border-box;display:flex;align-items:center;gap:4px;height:36px;padding:0 16px;',
    ' background:#14212f;border-bottom:1px solid rgba(255,255,255,.08);',
    " font:500 12.5px/1 'IBM Plex Sans',system-ui,-apple-system,'Segoe UI',sans-serif;",
    ' overflow-x:auto;white-space:nowrap;-webkit-font-smoothing:antialiased}',
    '.merk{color:#8ea2b8;font-weight:600;letter-spacing:.06em;text-transform:uppercase;font-size:11px;margin-right:10px}',
    'a{color:#cfe0f0;text-decoration:none;padding:6px 10px;border-radius:6px;display:inline-block}',
    'a:hover{background:rgba(255,255,255,.08);color:#fff}',
    'a:focus-visible{outline:2px solid #4ab8e6;outline-offset:1px}',
    'a[aria-current=page]{background:rgba(25,157,217,.18);color:#fff;box-shadow:inset 0 -2px 0 #199DD9}',
    '.ruimte{flex:1}',
    '.beheer{color:#8ea2b8}',
    '@media (max-width:520px){.bar{padding:0 8px}.merk{display:none}}'
  ].join('');

  function link(label, href, active, extraClass) {
    var a = document.createElement('a');
    a.textContent = label;
    a.href = href;
    if (extraClass) a.className = extraClass;
    if (active) a.setAttribute('aria-current', 'page');
    return a;
  }

  async function mount() {
    if (!window.getProfile || !window.getPageAccess) return; // auth.js ontbreekt
    var profile;
    try { profile = await window.getProfile(); } catch (e) { return; }
    if (!profile || profile.status === 'blocked') return;

    var access;
    try { access = await window.getPageAccess(profile); } catch (e) { return; }

    // Eén knop per systeem, naar de eerste toegankelijke pagina met een url
    var systems = [], seen = {};
    access.pages.forEach(function (p) {
      if (!p.url || seen[p.system]) return;
      seen[p.system] = true;
      systems.push({ system: p.system, label: p.system_label, url: p.url });
    });
    var isAdmin = profile.role === 'admin' && adminUrl;
    // Niets om tussen te wisselen: dan geen balk
    if (systems.length + (isAdmin ? 1 : 0) < 2) return;

    var host = document.createElement('div');
    host.id = 'pf-app-balk';
    var root = host.attachShadow({ mode: 'open' });
    var style = document.createElement('style');
    style.textContent = CSS;
    var nav = document.createElement('nav');
    nav.className = 'bar';
    nav.setAttribute('aria-label', 'Apps');
    var merk = document.createElement('span');
    merk.className = 'merk';
    merk.textContent = 'Profource';
    nav.appendChild(merk);
    systems.forEach(function (s) {
      nav.appendChild(link(s.label, s.url, s.system === current));
    });
    if (isAdmin) {
      var ruimte = document.createElement('span');
      ruimte.className = 'ruimte';
      nav.appendChild(ruimte);
      nav.appendChild(link('Beheer', adminUrl, current === 'admin', 'beheer'));
    }
    root.appendChild(style);
    root.appendChild(nav);
    document.body.insertBefore(host, document.body.firstChild);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
