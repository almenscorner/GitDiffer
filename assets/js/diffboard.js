/* ----------------- Helpers & Icons ----------------- */
const badgeForType = (t) => {
  switch (t) {
    case 'insert': return 'bg-emerald-900 text-emerald-100 border-emerald-700';
    case 'replace': return 'bg-amber-900 text-amber-100 border-amber-700';
    case 'delete': return 'bg-rose-900 text-rose-100 border-rose-700';
  case 'file-deleted': return 'bg-gray-800 text-gray-200 border-gray-700';
  default: return 'bg-gray-800 text-gray-200 border-gray-700';
  }
};

const icon = (name, cls='w-6 h-6') => {
  const icons = {
    total: `<svg xmlns="http://www.w3.org/2000/svg" class="${cls}" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 5h18M3 12h18M3 19h18"/></svg>`,
    replace: `<svg xmlns="http://www.w3.org/2000/svg" class="${cls}" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 7h9m7 0h-7m0 0l3-3m-3 3l3 3M20 17H11m-7 0h7m0 0l-3-3m3 3l-3 3"/></svg>`,
    insert: `<svg xmlns="http://www.w3.org/2000/svg" class="${cls}" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 4v16m8-8H4"/></svg>`,
    delete: `<svg xmlns="http://www.w3.org/2000/svg" class="${cls}" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M6 7h12m-9 0v12m6-12v12M9 7l1-2h4l1 2"/></svg>`,
    kv: `<svg xmlns="http://www.w3.org/2000/svg" class="${cls}" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 7h10M7 12h10M7 17h6"/></svg>`,
    block: `<svg xmlns="http://www.w3.org/2000/svg" class="${cls}" fill="none" viewBox="0 0 24 24" stroke="currentColor"><rect x="4" y="5" width="16" height="14" rx="2"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 9h8M8 12h8M8 15h5"/></svg>`
  };
  return icons[name] || icons.total;
};

const escapeHtml = (s) => (s ?? '').toString().replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const groupBy = (arr, key) => arr.reduce((m, x) => ((m[x[key]] ||= []).push(x), m), {});

/* ----------------- Rendering ----------------- */
function renderSummary(changes){
  const total = changes.length;
  const types = ['insert','replace','delete','file-deleted'];
  const counts = Object.fromEntries(types.map(t => [t, changes.filter(c=>c.change_type===t).length]));
  const kvCount = changes.filter(c=>c.context==='kv' || c.context==='plist-kv').length;
  const blockCount = changes.filter(c=>c.context==='block').length;

  const cards = [
  {label:'Total changes', value: total, key:'total', accent:'from-gray-500/50 via-gray-700/40 to-gray-900', pill:'bg-gray-800 text-gray-100', icon:'total'},
    {label:'Replacements', value: counts['replace'], key:'replace', accent:'from-amber-600/40 via-amber-700/30 to-slate-900', pill:'bg-amber-800 text-amber-100', icon:'replace'},
    {label:'Insertions', value: counts['insert'], key:'insert', accent:'from-emerald-600/40 via-emerald-700/30 to-slate-900', pill:'bg-emerald-800 text-emerald-100', icon:'insert'},
    {label:'Deletions', value: counts['delete'], key:'delete', accent:'from-rose-600/40 via-rose-700/30 to-slate-900', pill:'bg-rose-800 text-rose-100', icon:'delete'},
    {label:'KV / Plist pairs', value: kvCount, key:'kv', accent:'from-indigo-600/40 via-indigo-700/30 to-slate-900', pill:'bg-indigo-800 text-indigo-100', icon:'kv'},
    {label:'Raw blocks', value: blockCount, key:'block', accent:'from-sky-600/40 via-sky-700/30 to-slate-900', pill:'bg-sky-800 text-sky-100', icon:'block'},
  ];
  const el = document.getElementById('summary');
  el.innerHTML = cards.map(c=>`
    <div class="relative rounded-2xl">
      <div class="hidden dark:block absolute -inset-px rounded-2xl bg-gradient-to-br ${c.accent} blur-[2px] opacity-25"></div>
      <div class="rounded-2xl p-[1px] dark:bg-gradient-to-br ${c.accent}">
  <div class="rounded-2xl bg-white border border-slate-200 p-4 flex items-center gap-4 shadow-sm dark:bg-gray-900 dark:border-gray-800">
          <div class="p-2 rounded-xl ${c.pill}">${icon(c.icon,'w-5 h-5')}</div>
          <div class="grow">
            <div class="text-xs text-slate-500 dark:text-gray-400">${c.label}</div>
            <div class="text-2xl font-semibold text-slate-900 dark:text-gray-100">${c.value}</div>
          </div>
        </div>
      </div>
    </div>
  `).join('');
}

function renderChangeItem(ch, idx){
  const line = (ch.line_new ?? ch.line_old ?? '-')
  const typeBadge = `<span class="text-[11px] px-2 py-0.5 rounded-full border ${badgeForType(ch.change_type)}">${ch.change_type}</span>`;

  if (ch.context === 'kv' || ch.context === 'plist-kv'){
    return `
  <div class="rounded-xl border p-3 bg-white border-slate-200 text-slate-900 dark:bg-gray-900 dark:border-gray-800 dark:text-gray-100">
        <div class="flex items-center justify-between gap-3">
          <div class="flex items-center gap-2 min-w-0">
            <span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border bg-indigo-50 text-indigo-700 border-indigo-300 dark:bg-indigo-900/20 dark:text-indigo-300 dark:border-indigo-800">${icon('kv','w-4 h-4')}<span>Property</span></span>
            <code class="font-mono text-sm truncate px-2 py-1 rounded-lg border bg-slate-50 text-slate-900 border-slate-200 dark:bg-gray-900 dark:text-gray-100 dark:border-gray-800" title="${escapeHtml(ch.property ?? '(unknown)')}">${escapeHtml(ch.property ?? '(unknown)')}</code>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <span class="text-xs text-slate-500 dark:text-gray-400">line ${line}</span>
            ${typeBadge}
          </div>
        </div>
        <div class="mt-2 grid grid-cols-1 md:grid-cols-2 gap-3 text-sm font-mono">
          <div class="rounded-lg border p-2 overflow-auto border-rose-300 bg-rose-50/50 text-rose-700 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-300"><div class="text-[11px] uppercase tracking-wide text-rose-700 dark:text-rose-300 mb-1">Baseline</div>${escapeHtml(ch.old)}</div>
          <div class="rounded-lg border p-2 overflow-auto border-emerald-300 bg-emerald-50/50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-300"><div class="text-[11px] uppercase tracking-wide text-emerald-700 dark:text-emerald-300 mb-1">New</div>${escapeHtml(ch.new)}</div>
        </div>
  <div class="mt-3 flex justify-end"><button data-copy-change="${idx}" class="px-2 py-1 text-[11px] rounded border border-slate-300 bg-slate-100 hover:bg-slate-200 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-700">Copy</button></div>
      </div>`;
  }

  if (ch.change_type === 'file-deleted'){
    return `
  <div class="rounded-xl border p-3 bg-white border-slate-200 text-slate-900 dark:bg-gray-900 dark:border-gray-800 dark:text-gray-100">
        <div class="flex items-center justify-between">
          <div class="text-sm">File removed</div>
          ${typeBadge}
        </div>
  <div class="mt-3 flex justify-end"><button data-copy-change="${idx}" class="px-2 py-1 text-[11px] rounded border border-slate-300 bg-slate-100 hover:bg-slate-200 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-700">Copy</button></div>
      </div>`;
  }

  const oldBlock = (ch.old_lines||[]).map(l=>`- ${escapeHtml(l)}`).join('\n');
  const newBlock = (ch.new_lines||[]).map(l=>`+ ${escapeHtml(l)}`).join('\n');
  return `
  <div class="rounded-xl border p-3 bg-white border-slate-200 text-slate-900 dark:bg-gray-900 dark:border-gray-800 dark:text-gray-100">
      <div class="flex items-center justify-between gap-3">
  <div class="text-sm text-slate-500 dark:text-gray-400">line ${line}</div>
        ${typeBadge}
      </div>
      <div class="mt-2 grid grid-cols-1 md:grid-cols-2 gap-3">
        <div class="rounded-lg border p-2 overflow-auto text-xs border-rose-200 bg-rose-50/50 text-rose-800 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-200"><div class="text-[11px] uppercase tracking-wide text-rose-700 dark:text-rose-300 mb-1">Baseline</div><pre class="diff"><code>${oldBlock || '—'}</code></pre></div>
        <div class="rounded-lg border p-2 overflow-auto text-xs border-emerald-200 bg-emerald-50/50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-200"><div class="text-[11px] uppercase tracking-wide text-emerald-700 dark:text-emerald-300 mb-1">New</div><pre class="diff"><code>${newBlock || '—'}</code></pre></div>
      </div>
  <div class="mt-3 flex justify-end"><button data-copy-change="${idx}" class="px-2 py-1 text-[11px] rounded border border-slate-300 bg-slate-100 hover:bg-slate-200 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-700">Copy</button></div>
    </div>`;
}

let CURRENT_RENDERED = [];
function renderFiles(changes){
  const groups = groupBy(changes, 'file');
  const filesEl = document.getElementById('files');
  const fileNames = Object.keys(groups).sort();
  let globalIndex = 0; // sequential index across all displayed changes
  CURRENT_RENDERED = [];
  const fileMarkup = fileNames.map(fname => {
    const items = groups[fname];
    const badges = Object.entries(items.reduce((m,c)=>{m[c.change_type]=(m[c.change_type]||0)+1; return m;},{}))
      .map(([k,v])=>`<span class="text-[11px] px-2 py-0.5 rounded-full border ${badgeForType(k)}">${k}: ${v}</span>`)
      .join(' ');
    const renderedItems = items.map(item => {
      const markup = renderChangeItem(item, globalIndex);
      CURRENT_RENDERED.push(item);
      globalIndex++;
      return markup;
    }).join('');
    return `
  <details class="group rounded-2xl border border-slate-200 bg-white shadow-sm transition-all dark:border-gray-800 dark:bg-gray-900">
        <summary class="cursor-pointer list-none p-4 flex items-center justify-between">
          <div class="min-w-0 pr-4">
            <div class="font-medium text-slate-900 dark:text-gray-100 truncate">${escapeHtml(fname)}</div>
            <div class="text-xs text-slate-500 mt-1 flex flex-wrap gap-2 dark:text-gray-400">${badges}</div>
          </div>
          <div class="text-slate-400 group-open:rotate-180 transition-transform">▾</div>
        </summary>
        <div class="p-4 pt-0 space-y-3">
          ${renderedItems}
        </div>
      </details>`;
  }).join('');
  filesEl.innerHTML = fileMarkup;
}

/* ----------------- Data + Filters ----------------- */
let DATA = [];
let LAST_FILTERED = [];
let PAGE = 1;
let PAGE_SIZE = 50; // or 'all'
const groupFilterInputs = () => {
  document.getElementById('q').addEventListener('input', applyFilters);
  document.getElementById('typeFilter').addEventListener('change', applyFilters);
  document.getElementById('ctxFilter').addEventListener('change', applyFilters);
  document.getElementById('clearFilters').addEventListener('click', () => {
    document.getElementById('q').value = '';
    document.getElementById('typeFilter').value = '';
    document.getElementById('ctxFilter').value = '';
    applyFilters();
  });
};

function applyFilters(){
  const q = document.getElementById('q').value.toLowerCase().trim();
  const tf = document.getElementById('typeFilter').value;
  const cf = document.getElementById('ctxFilter').value;
  LAST_FILTERED = DATA.filter(c => {
    if (tf && c.change_type !== tf) return false;
    if (cf && c.context !== cf) return false;
    if (!q) return true;
    const hay = [c.file, c.property, c.old, c.new, (c.old_lines||[]).join('\n'), (c.new_lines||[]).join('\n')]
      .filter(Boolean).join(' ').toLowerCase();
    return hay.includes(q);
  });
  PAGE = 1; // reset to first page on filter change
  renderSummary(LAST_FILTERED);
  updatePage();
}

function computePage(){
  const total = LAST_FILTERED.length;
  const size = (PAGE_SIZE === 'all') ? total : PAGE_SIZE;
  const totalPages = size ? Math.max(1, Math.ceil(total / (size || 1))) : 1;
  const clampedPage = Math.min(Math.max(1, PAGE), totalPages);
  const start = (size === 0) ? 0 : (clampedPage - 1) * size;
  const end = (size === 0) ? total : Math.min(start + size, total);
  const pageItems = LAST_FILTERED.slice(start, end);
  return { total, size, totalPages, page: clampedPage, start, end, pageItems };
}

function renderPager(info){
  const sets = [
    {
      prev: document.getElementById('pagePrev'),
      next: document.getElementById('pageNext'),
      label: document.getElementById('pageInfo'),
    },
    {
      prev: document.getElementById('pagePrevB'),
      next: document.getElementById('pageNextB'),
      label: document.getElementById('pageInfoB'),
    }
  ];
  const text = info.total === 0
    ? 'No results'
    : `${info.start + 1}–${info.end} of ${info.total} • Page ${info.page}/${info.totalPages}`;
  for (const s of sets){
    if (!s.label) continue;
    s.label.textContent = text;
    if (s.prev) s.prev.disabled = (info.page <= 1);
    if (s.next) s.next.disabled = (info.page >= info.totalPages);
  }
}

function updatePage(){
  const info = computePage();
  PAGE = info.page; // in case it was clamped
  renderFiles(info.pageItems);
  renderPager(info);
}

/* ----------------- Import / Paste / Sample ----------------- */
onEach(['fileInput','fileInputMobile'], 'change', (e)=>{
  const file = e.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const parsed = JSON.parse(reader.result);
      if (!Array.isArray(parsed)) throw new Error('Root must be an array');
      DATA = parsed;
      applyFilters();
    } catch (err){
      alert('Invalid JSON: ' + err.message);
    }
  };
  reader.readAsText(file);
});
const dlg = document.getElementById('pasteDialog');
onEach(['pasteJsonBtn','pasteJsonBtnMobile'], 'click', ()=> dlg.showModal());
document.getElementById('pasteApply').addEventListener('click', (ev)=>{
  ev.preventDefault();
  try{
    const txt = document.getElementById('pasteArea').value;
    const parsed = JSON.parse(txt);
    if (!Array.isArray(parsed)) throw new Error('Root must be an array');
    DATA = parsed;
    dlg.close();
    applyFilters();
  }catch(err){
    alert('Invalid JSON: ' + err.message);
  }
});

function onEach(ids, type, handler){
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener(type, handler);
  });
}

onEach(['loadSample','loadSampleMobile'], 'click', ()=>{
  DATA = [
    {"file":"Scripts/Powershell/Script Data/intunecd_appreg.ps1","property":null,"line_old":14,"line_new":14,"context":"block","old_lines":["$appName = \"IntuneCD-Monitor\""],"new_lines":["$appName = \"IntuneCD\""],"change_type":"replace"},
    {"file":"Settings Catalog/Baseline - W365_mdm.json","property":"value","old":"\"device_vendor_msft_policy_config_devicelock_preventenablinglockscreencamera_1\"","new":"\"device_vendor_msft_policy_config_devicelock_preventenablinglockscreencamera_0\"","line_old":28,"line_new":28,"context":"kv","change_type":"replace"},
    {"file":"Assignment Report/report.json","property":null,"line_old":1,"line_new":null,"context":"block","old_lines":[],"new_lines":[],"change_type":"file-deleted"}
  ];
  applyFilters();
});

/* ----------------- Boot ----------------- */
groupFilterInputs();
// Theme toggle
const themeBtnIds = ['themeToggle','themeToggleMobile'];
function setThemeButtonIcon(){
  const isDark = document.documentElement.classList.contains('dark');
  themeBtnIds.forEach(id => {
    const b = document.getElementById(id);
    if (b) b.textContent = isDark ? '☀️' : '🌙';
  });
}
onEach(themeBtnIds, 'click', () => {
  const root = document.documentElement;
  const isDark = root.classList.toggle('dark');
  try { localStorage.setItem('theme', isDark ? 'dark' : 'light'); } catch {}
  setThemeButtonIcon();
});
setThemeButtonIcon();

// default to empty; use "Load sample" or import JSON
DATA = [];
applyFilters();

// Expand / Collapse all
onEach(['expandAll','expandAllMobile'], 'click', () => {
  document.querySelectorAll('#files details').forEach(d => d.open = true);
});
onEach(['collapseAll','collapseAllMobile'], 'click', () => {
  document.querySelectorAll('#files details').forEach(d => d.open = false);
});

// Pagination controls
document.getElementById('rowsPerPage').addEventListener('change', (e) => {
  const v = e.target.value;
  PAGE_SIZE = (v === 'all') ? 'all' : Math.max(1, Number(v) || 50);
  PAGE = 1;
  updatePage();
});
document.getElementById('pagePrev').addEventListener('click', () => {
  if (PAGE > 1) { PAGE--; updatePage(); }
});
document.getElementById('pageNext').addEventListener('click', () => {
  const info = computePage();
  if (PAGE < info.totalPages) { PAGE++; updatePage(); }
});

// Bottom pager controls (if present)
const pagePrevB = document.getElementById('pagePrevB');
if (pagePrevB){
  pagePrevB.addEventListener('click', () => {
    if (PAGE > 1) { PAGE--; updatePage(); }
  });
}
const pageNextB = document.getElementById('pageNextB');
if (pageNextB){
  pageNextB.addEventListener('click', () => {
    const info = computePage();
    if (PAGE < info.totalPages) { PAGE++; updatePage(); }
  });
}

// Keyboard shortcuts
window.addEventListener('keydown', (e) => {
  if (e.key === 't' && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault();
    themeBtn.click();
  }
  if (e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault();
    document.getElementById('q').focus();
  }
  if (e.key === 'Escape') {
    const q = document.getElementById('q');
    if (document.activeElement === q) {
      q.blur();
    }
  }
});

/* ----------------- Drag & Drop JSON ----------------- */
const dropZone = document.getElementById('dropZone');
const showDZ = () => dropZone.classList.add('active');
const hideDZ = () => dropZone.classList.remove('active');
['dragenter','dragover'].forEach(evt => window.addEventListener(evt, e => {
  if (e.dataTransfer?.types?.includes('application/json') || e.dataTransfer?.types?.includes('text/plain')) {
    e.preventDefault(); showDZ();
  }
}));
['dragleave','drop'].forEach(evt => window.addEventListener(evt, e => {
  if (evt === 'dragleave' && e.relatedTarget) return; // inside element
  e.preventDefault(); hideDZ();
}));
window.addEventListener('drop', e => {
  const file = e.dataTransfer?.files?.[0];
  if (!file) return;
  if (!file.type.match(/json|text/)) { alert('Please drop a JSON file.'); return; }
  const reader = new FileReader();
  showLoading();
  reader.onload = () => {
    try {
      const parsed = JSON.parse(reader.result);
      if (!Array.isArray(parsed)) throw new Error('Root must be an array');
      DATA = parsed; applyFilters();
    } catch(err){ alert('Invalid JSON: ' + err.message); }
    hideLoading();
  };
  reader.readAsText(file);
});

/* ----------------- Copy JSON for each change ----------------- */
document.getElementById('files').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-copy-change]');
  if (!btn) return;
  const idx = Number(btn.getAttribute('data-copy-change'));
  const obj = CURRENT_RENDERED[idx];
  if (!obj) return;
  const txt = JSON.stringify(obj, null, 2);
  navigator.clipboard.writeText(txt).then(()=>{
    btn.textContent = 'Copied';
    setTimeout(()=>{ btn.textContent='Copy'; }, 1500);
  }).catch(()=> alert('Clipboard failed'));
});

/* ----------------- Loading indicator ----------------- */
let loadingEl;
function showLoading(){
  if (loadingEl) return;
  loadingEl = document.createElement('div');
  loadingEl.className = 'fixed top-4 right-4 z-50 flex items-center gap-2 px-3 py-2 rounded-xl bg-gray-900 text-white text-sm shadow-lg dark:bg-gray-800';
  loadingEl.innerHTML = '<span class="animate-spin inline-block w-4 h-4 border-2 border-white/40 border-t-white rounded-full"></span> Loading…';
  document.body.appendChild(loadingEl);
}
function hideLoading(){
  if (!loadingEl) return;
  loadingEl.remove(); loadingEl = null;
}

/* ----------------- Hero floating particles ----------------- */
(function heroParticles(){
  const hero = document.querySelector('.hero-bg');
  if (!hero) return;
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const canvas = document.createElement('canvas');
  canvas.className = 'hero-particles';
  hero.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  let dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
  let W = 0, H = 0, running = !prefersReduced.matches;

  const state = { particles: [], palette: [] };
  const pick = (arr) => arr[Math.floor(Math.random()*arr.length)];

  function setPalette(){
    const dark = document.documentElement.classList.contains('dark');
    state.palette = dark
      ? ['rgba(125,211,252,0.18)','rgba(110,231,183,0.16)','rgba(165,180,252,0.16)','rgba(203,213,225,0.12)']
      : ['rgba(56,189,248,0.22)','rgba(16,185,129,0.20)','rgba(99,102,241,0.18)','rgba(2,6,23,0.06)'];
    // refresh particle colors
    state.particles.forEach(p => p.color = pick(state.palette));
  }

  function resize(){
    const rect = hero.getBoundingClientRect();
    W = Math.max(200, Math.floor(rect.width));
    H = Math.max(120, Math.floor(rect.height));
    canvas.width = Math.floor(W * dpr);
    canvas.height = Math.floor(H * dpr);
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    ctx.setTransform(dpr,0,0,dpr,0,0);
  }

  function makeParticles(){
    const count = Math.min(120, Math.max(28, Math.floor((W*H)/18000)));
    state.particles = Array.from({length: count}, () => ({
      x: Math.random()*W,
      y: Math.random()*H,
      r: 0.8 + Math.random()*2.2,
      dx: (Math.random()*0.3 - 0.15),
      dy: (Math.random()*0.3 - 0.15),
      color: pick(state.palette),
    }));
  }

  function step(){
    if (!running) return; // paused for reduced motion
    ctx.clearRect(0,0,W,H);
    for (const p of state.particles){
      p.x += p.dx; p.y += p.dy;
      // gentle wrap
      if (p.x < -10) p.x = W + 10; else if (p.x > W + 10) p.x = -10;
      if (p.y < -10) p.y = H + 10; else if (p.y > H + 10) p.y = -10;

      ctx.beginPath();
      ctx.fillStyle = p.color;
      ctx.shadowBlur = 8; ctx.shadowColor = p.color;
      ctx.arc(p.x, p.y, p.r, 0, Math.PI*2);
      ctx.fill();
    }
    requestAnimationFrame(step);
  }

  // Observe reduced motion changes
  prefersReduced.addEventListener?.('change', () => {
    running = !prefersReduced.matches;
    if (running) requestAnimationFrame(step);
    else ctx.clearRect(0,0,W,H);
  });

  // React to theme changes
  const mo = new MutationObserver(setPalette);
  mo.observe(document.documentElement, { attributes:true, attributeFilter:['class'] });

  // React to visibility
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { running = false; }
    else if (!prefersReduced.matches) { running = true; requestAnimationFrame(step); }
  });

  // Init
  setPalette();
  resize();
  makeParticles();
  window.addEventListener('resize', () => { dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1)); resize(); makeParticles(); });
  if (running) requestAnimationFrame(step);
})();

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('navToggle');
  const panel = document.getElementById('mobileNav');
  if (!btn || !panel) return;
  btn.addEventListener('click', () => {
    const open = panel.classList.toggle('hidden') === false;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
});