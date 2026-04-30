/* ═══════════════════════════════════════════════════════
   ShamrockLeads — Intelligence Dashboard Engine
   ═══════════════════════════════════════════════════════ */
const SL = (() => {
const API = location.origin;
const PRESETS = {
  swfl: ['Lee','Collier','Charlotte','DeSoto','Hendry','Sarasota','Manatee'],
  all: [],
  none: []
};
let state = {
  counties: [], selectedCounties: [], days: 3, custody: '', status: '',
  minBond: 0, search: '', sort: 'arrest_date', order: 'desc',
  page: 1, limit: 50, leads: [], total: 0, pages: 1,
  scraperData: {}, mongoData: {}, prevHotCount: -1
};
let searchTimer = null;

// ── Theme ──
function toggleTheme() {
  const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = t;
  localStorage.setItem('sl-theme', t);
}
(function initTheme(){ document.documentElement.dataset.theme = localStorage.getItem('sl-theme') || 'dark'; })();

// ── Tabs ──
function switchTab(btn) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(btn.dataset.tab).classList.add('active');
  if (btn.dataset.tab === 'tabLeads' && state.leads.length === 0) applyFilters();
  if (btn.dataset.tab === 'tabDefendants') loadDefendants();
  if (btn.dataset.tab === 'tabHealth') renderHealth();
}

// ── Multi-County Select ──
function toggleCountyDropdown() {
  const dd = document.getElementById('countyDropdown');
  const tr = document.querySelector('.multi-select-trigger');
  dd.classList.toggle('show'); tr.classList.toggle('open');
}
function buildCountyOptions(counties) {
  state.counties = counties;
  const el = document.getElementById('countyOptions');
  el.innerHTML = counties.map(c => {
    const chk = state.selectedCounties.includes(c) ? 'checked' : '';
    return `<label class="multi-select-option ${chk ? 'checked' : ''}"><input type="checkbox" value="${c}" ${chk} onchange="SL.toggleCounty('${c}', this.checked)">${c}</label>`;
  }).join('');
  updateCountyLabel();
}
function toggleCounty(county, checked) {
  if (checked && !state.selectedCounties.includes(county)) state.selectedCounties.push(county);
  else state.selectedCounties = state.selectedCounties.filter(c => c !== county);
  updateCountyLabel(); buildCountyOptions(state.counties); applyFilters();
}
function updateCountyLabel() {
  const el = document.getElementById('countyLabel');
  const n = state.selectedCounties.length;
  if (n === 0) el.innerHTML = 'All Counties';
  else if (n <= 3) el.innerHTML = state.selectedCounties.join(', ');
  else el.innerHTML = `${n} counties <span class="multi-select-count">${n}</span>`;
}
function filterCountyOptions(q) {
  const opts = document.querySelectorAll('.multi-select-option');
  opts.forEach(o => { o.style.display = o.textContent.toLowerCase().includes(q.toLowerCase()) ? '' : 'none'; });
}
function applyPreset(name) {
  document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
  if (name === 'all' || name === 'none') state.selectedCounties = [];
  else state.selectedCounties = [...PRESETS[name]];
  event.target.closest('.preset-btn').classList.add('active');
  buildCountyOptions(state.counties); applyFilters();
}

// ── Date Range ──
function setDays(d) {
  state.days = d; state.page = 1;
  document.querySelectorAll('#dateRange button').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active'); applyFilters();
}

// ── Bond Range ──
function setBond(v) {
  state.minBond = v; state.page = 1;
  document.querySelectorAll('#bondRange button').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active'); applyFilters();
}

// ── Sort ──
function sortBy(field) {
  if (state.sort === field) state.order = state.order === 'desc' ? 'asc' : 'desc';
  else { state.sort = field; state.order = 'desc'; }
  state.page = 1;
  document.querySelectorAll('th[data-sort]').forEach(th => { th.classList.remove('sort-asc','sort-desc'); });
  const th = document.querySelector(`th[data-sort="${field}"]`);
  if (th) th.classList.add(state.order === 'desc' ? 'sort-desc' : 'sort-asc');
  applyFilters();
}

// ── Search ──
function debounceSearch() { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.search = document.getElementById('searchInput').value; state.page = 1; applyFilters(); }, 350); }
function debounceDefSearch() { clearTimeout(searchTimer); searchTimer = setTimeout(loadDefendants, 350); }

// ── Core Fetch ──
async function applyFilters() {
  state.custody = document.getElementById('custodyFilter').value;
  state.status = document.getElementById('statusFilter').value;
  state.limit = parseInt(document.getElementById('limitSelect').value);
  const params = new URLSearchParams({ page: state.page, limit: state.limit, sort: state.sort, order: state.order });
  if (state.selectedCounties.length) params.set('county', state.selectedCounties.join(','));
  if (state.days) params.set('days', state.days);
  if (state.custody) params.set('custody', state.custody);
  if (state.status) params.set('status', state.status);
  if (state.minBond) params.set('min_bond', state.minBond);
  if (state.search) params.set('search', state.search);
  try {
    const r = await fetch(`${API}/api/leads?${params}`);
    if (!r.ok) { console.warn('[Leads] HTTP', r.status); return; }
    const ct = r.headers.get('content-type') || '';
    if (!ct.includes('application/json')) { console.warn('[Leads] non-JSON response'); return; }
    const d = await r.json();
    state.leads = d.leads || []; state.total = d.total || 0; state.pages = d.pages || 1;
    if (d.counties && state.counties.length === 0) buildCountyOptions(d.counties);
    document.getElementById('leadsBadge').textContent = state.total.toLocaleString();
    document.getElementById('resultsMeta').textContent = `${state.total.toLocaleString()} results · Page ${state.page}/${state.pages}`;
    renderLeads(); renderPills(); renderPagination();
  } catch(e) { console.error(e); }
}

// ── Render Leads Table ──
function renderLeads() {
  const tb = document.getElementById('leadsBody');
  if (!state.leads.length) { tb.innerHTML = '<tr><td colspan="9" class="loading">No leads match current filters</td></tr>'; return; }
  tb.innerHTML = state.leads.map(l => {
    const bond = l.bond_amount || 0;
    const bc = bond >= 10000 ? 'bond-high' : bond >= 2500 ? 'bond-mid' : 'bond-low';
    const sc = (l.lead_status||'').toLowerCase();
    const scoreCls = sc === 'hot' ? 'score-hot' : sc === 'warm' ? 'score-warm' : sc === 'disqualified' ? 'score-disq' : 'score-cold';
    const charges = (l.charges||'').length > 50 ? (l.charges||'').slice(0,47)+'…' : (l.charges||'—');
    const custBadge = (l.status||'').toLowerCase().includes('custody') ? '<span class="def-status-badge custody">In Custody</span>' : (l.status||'').toLowerCase().includes('release') ? '<span class="def-status-badge released">Released</span>' : `<span class="def-status-badge other">${l.status||'—'}</span>`;
    const courtCls = isCourtSoon(l.court_date) ? 'court-soon' : '';
    return `<tr>
      <td><strong>${l.full_name||'Unknown'}</strong><br><span style="color:var(--muted);font-size:11px">${[l.sex,l.race,l.dob].filter(Boolean).join(' · ')}</span></td>
      <td><span class="county-count">${l.county||'—'}</span></td>
      <td title="${(l.charges||'').replace(/"/g,'&quot;')}">${charges}</td>
      <td class="${bc}">$${bond.toLocaleString()}</td>
      <td><span class="score-pill ${scoreCls}">${l.lead_score||0} ${l.lead_status||''}</span></td>
      <td>${custBadge}</td>
      <td>${fmtDate(l.arrest_date || l.booking_date)}</td>
      <td class="${courtCls}">${l.court_date || '—'}</td>
      <td>${l.detail_url ? `<a href="${l.detail_url}" target="_blank" style="color:var(--accent)">🔗</a>` : '—'}</td>
    </tr>`;
  }).join('');
}

// ── Filter Pills ──
function renderPills() {
  const el = document.getElementById('filterPills'); const pills = [];
  if (state.selectedCounties.length) pills.push(pill(`Counties: ${state.selectedCounties.length}`, () => { state.selectedCounties = []; buildCountyOptions(state.counties); applyFilters(); }));
  if (state.days) pills.push(pill(`${state.days}d range`, () => { setDaysQuiet(0); applyFilters(); }));
  if (state.minBond) pills.push(pill(`$${state.minBond.toLocaleString()}+ bond`, () => { setBondQuiet(0); applyFilters(); }));
  if (state.custody) pills.push(pill(`Custody: ${state.custody}`, () => { document.getElementById('custodyFilter').value=''; applyFilters(); }));
  if (state.status) pills.push(pill(`Score: ${state.status}`, () => { document.getElementById('statusFilter').value=''; applyFilters(); }));
  if (state.search) pills.push(pill(`"${state.search}"`, () => { document.getElementById('searchInput').value=''; state.search=''; applyFilters(); }));
  if (pills.length) pills.push('<span class="filter-clear-all" onclick="SL.clearAll()">Clear all</span>');
  el.innerHTML = pills.join('');
}
function pill(label, onclick) { const id = 'p'+Math.random().toString(36).slice(2,6); window['_pill_'+id] = onclick; return `<span class="filter-pill">${label}<span class="pill-close" onclick="window._pill_${id}()">✕</span></span>`; }
function setDaysQuiet(d) { state.days = d; document.querySelectorAll('#dateRange button').forEach((b,i) => b.classList.toggle('active', [1,2,3,5,0][i] === d)); }
function setBondQuiet(v) { state.minBond = v; document.querySelectorAll('#bondRange button').forEach((b,i) => b.classList.toggle('active', [0,1000,2500,5000,10000][i] === v)); }
function clearAll() { state.selectedCounties=[]; setDaysQuiet(3); setBondQuiet(0); state.search=''; state.custody=''; state.status='';
  document.getElementById('searchInput').value=''; document.getElementById('custodyFilter').value=''; document.getElementById('statusFilter').value='';
  buildCountyOptions(state.counties); applyFilters(); }

// ── Pagination ──
function renderPagination() {
  const el = document.getElementById('pagination');
  el.innerHTML = `<button ${state.page<=1?'disabled':''} onclick="SL.goPage(${state.page-1})">← Prev</button><span>Page ${state.page} of ${state.pages}</span><button ${state.page>=state.pages?'disabled':''} onclick="SL.goPage(${state.page+1})">Next →</button>`;
}
function goPage(p) { state.page = p; applyFilters(); document.getElementById('tabLeads').scrollIntoView({behavior:'smooth'}); }

// ── Command Center ──
async function loadDashboard() {
  try {
    const _sf = async (url) => { const r = await fetch(url); if (!r.ok) return null; const ct = r.headers.get('content-type')||''; return ct.includes('application/json') ? r.json() : null; };
    const [s, m] = await Promise.all([_sf(`${API}/api/status`), _sf(`${API}/api/mongo-stats`)]);
    if (!s || !m) { console.warn('[Dashboard] core endpoints unavailable'); return; }
    state.scraperData = s; state.mongoData = m;
    const sc = s.scrapers||{}, by = m.by_county||{}, scores = m.scores||{};
    const ok = Object.values(sc).filter(x=>x.status==='ok').length;
    const err = Object.values(sc).filter(x=>x.status!=='ok').length;
    document.getElementById('kpiRecords').textContent = fmt(m.total_records||0);
    document.getElementById('kpiRecordsSub').textContent = `${Object.keys(by).length} counties`;
    document.getElementById('kpiActive').textContent = ok;
    document.getElementById('kpiActiveSub').textContent = `${err} errors`;
    document.getElementById('kpiHot').textContent = scores.hot||0;
    document.getElementById('kpiWarm').textContent = scores.warm||0;
    document.getElementById('kpiErrors').textContent = err;
    // Hot lead audio alert
    if (state.prevHotCount >= 0 && (scores.hot||0) > state.prevHotCount) playHotAlert();
    state.prevHotCount = scores.hot||0;
    // County list
    const sorted = Object.entries(by).sort((a,b)=>b[1]-a[1]);
    document.getElementById('countyList').innerHTML = sorted.map(([c,n]) => `<div class="county-row"><span class="county-name">${c}</span><span class="county-bond">${(sc[c]?.hot_leads||0)} 🔥</span><span class="county-count">${fmt(n)}</span></div>`).join('');
    // Recent hot leads
    const hot = m.recent_hot_leads || [];
    document.getElementById('recentHot').innerHTML = hot.length ? hot.slice(0,8).map(l => `<div class="county-row"><span class="county-name" style="font-size:12px">${l.full_name||'?'}</span><span style="color:var(--accent);font-size:12px;font-weight:700">$${(l.bond_amount||0).toLocaleString()}</span><span class="county-count">${l.county||'—'}</span></div>`).join('') : '<div class="loading">No hot leads in 24h</div>';
    if (state.counties.length === 0 && m.by_county) buildCountyOptions(Object.keys(m.by_county).sort());
  } catch(e) { console.error('Dashboard load error:', e); }
}

// ── Scraper Health ──
function renderHealth() {
  const sc = state.scraperData?.scrapers || {};
  const entries = Object.entries(sc).sort((a,b)=>a[0].localeCompare(b[0]));
  const ok = entries.filter(([,d])=>d.status==='ok').length;
  document.getElementById('healthKpis').innerHTML = `
    <div class="stat-card"><div class="stat-label">Healthy</div><div class="stat-value">${ok}</div></div>
    <div class="stat-card"><div class="stat-label">Total Fleet</div><div class="stat-value">${entries.length}</div></div>
    <div class="stat-card"><div class="stat-label">Errors</div><div class="stat-value">${entries.length - ok}</div></div>`;
  document.getElementById('healthBody').innerHTML = entries.map(([c,d]) => {
    const cls = d.status==='ok'?'status-healthy':'status-offline';
    const lbl = d.status==='ok'?'Healthy':'Error';
    return `<tr class="health-row"><td><strong>${c}</strong></td><td><span class="status-badge ${cls}">${lbl}</span></td><td>${d.records||0}</td><td>${d.hot_leads||0}</td><td>${d.last_run?timeAgo(d.last_run):'—'}</td><td>${d.avg_time?d.avg_time+'s':'—'}</td></tr>`;
  }).join('');
}

// ── Defendants ──
async function loadDefendants() {
  const search = document.getElementById('defSearch')?.value || '';
  const params = new URLSearchParams({limit:50,sort:'lead_score',order:'desc'});
  if (search) params.set('search', search);
  try {
    const r = await fetch(`${API}/api/leads?${params}`);
    if (!r.ok) { console.warn('[Defendants] HTTP', r.status); return; }
    const ct = r.headers.get('content-type') || '';
    if (!ct.includes('application/json')) { console.warn('[Defendants] non-JSON response'); return; }
    const d = await r.json();
    const grid = document.getElementById('defendantGrid');
    grid.innerHTML = (d.leads||[]).map(l => {
      const bond = l.bond_amount||0;
      const bc = bond>=10000?'high':bond>=2500?'mid':'low';
      const stBadge = (l.status||'').toLowerCase().includes('custody')?'custody':(l.status||'').toLowerCase().includes('release')?'released':'other';
      return `<div class="def-card">
        <div class="def-card-header"><div><div class="def-name">${l.full_name||'Unknown'}</div><div class="def-booking">${l.booking_number||'—'}</div></div><div class="def-bond-pill ${bc}">$${bond.toLocaleString()}</div></div>
        <div class="def-body">
          <div class="def-section"><div class="def-section-title">📋 Details</div><div class="def-row"><div class="def-field"><span class="def-label">County</span><span class="def-value">${l.county||'—'}</span></div><div class="def-field"><span class="def-label">DOB</span><span class="def-value">${l.dob||'—'}</span></div><div class="def-field"><span class="def-label">Status</span><span class="def-status-badge ${stBadge}">${l.status||'—'}</span></div><div class="def-field"><span class="def-label">Score</span><span class="def-value">${l.lead_score||0} (${l.lead_status||'—'})</span></div></div></div>
          <div class="def-section"><div class="def-section-title">⚖️ Charges</div><div class="def-row wide"><div class="def-value" style="font-size:12px;white-space:normal">${l.charges||'—'}</div></div></div>
        </div>
        <div class="def-card-footer"><button class="btn-detail" onclick="window.open('${l.detail_url||'#'}')">🔗 Source</button><button class="btn-contact-indem" onclick="SLContact.openModal('${(l.booking_number||'')}','${(l.full_name||'').replace(/'/g,"\\'")}',' ${l.county||''}',${bond},'${(l.booking_number||'')}')">📞 Contact Indem</button><button class="btn-write-bond" onclick="SL.openBondModal('${(l.full_name||'').replace(/'/g,"\\'")}',${bond},'${l.county||''}','${l.booking_number||''}')">✍️ Write Bond</button></div>
      </div>`;
    }).join('') || '<div class="loading">No defendants found</div>';
  } catch(e) { console.error(e); }
}

// ── Write Bond Modal ──
function openBondModal(name,bond,county,booking) {
  document.getElementById('bondModal').classList.add('show');
  const premium = Math.max(100, bond * 0.1);
  document.getElementById('bondModalBody').innerHTML = `
    <div class="wb-section"><div class="wb-section-label">Defendant</div><div class="wb-defendant-summary"><div class="wb-name">${name}</div><div class="wb-meta-grid"><div><span class="wb-meta-label">County</span>${county}</div><div><span class="wb-meta-label">Booking</span>${booking}</div><div><span class="wb-meta-label">Bond</span>$${bond.toLocaleString()}</div><div><span class="wb-meta-label">Est. Premium</span>$${premium.toLocaleString()}</div></div></div></div>
    <div class="wb-section"><div class="wb-section-label">Surety Company</div><div class="insurer-selector"><button class="insurer-pill active" onclick="this.parentElement.querySelectorAll('.insurer-pill').forEach(p=>p.classList.remove('active'));this.classList.add('active')"><span class="insurer-pill-icon">🛡️</span><span class="insurer-pill-name">OSI</span><span class="insurer-pill-full">O'Shaughnahill S&I</span></button><button class="insurer-pill" onclick="this.parentElement.querySelectorAll('.insurer-pill').forEach(p=>p.classList.remove('active'));this.classList.add('active')"><span class="insurer-pill-icon">🌴</span><span class="insurer-pill-name">Palmetto</span><span class="insurer-pill-full">Palmetto Surety</span></button></div></div>
    <div class="wb-poa-notice"><span class="wb-poa-icon">📋</span><div><div class="wb-poa-title">Power of Attorney Required</div><div class="wb-poa-text">A POA will be assigned from your available inventory for this surety company.</div></div></div>`;
}
function closeModal() { document.getElementById('bondModal').classList.remove('show'); }
function submitBond() { toast('Bond packet generation coming soon','info'); closeModal(); }

// ── Export ──
function exportCSV() {
  const params = new URLSearchParams({sort:state.sort,order:state.order});
  if (state.selectedCounties.length) params.set('county', state.selectedCounties.join(','));
  if (state.days) params.set('days', state.days);
  if (state.custody) params.set('custody', state.custody);
  if (state.status) params.set('status', state.status);
  if (state.minBond) params.set('min_bond', state.minBond);
  if (state.search) params.set('search', state.search);
  window.open(`${API}/api/leads/export?${params}`);
  toast('CSV download started','success');
}
function copyToSlack() {
  if (!state.leads.length) { toast('No leads to copy','error'); return; }
  const lines = state.leads.slice(0,20).map(l => `• *${l.full_name}* — ${l.county} — $${(l.bond_amount||0).toLocaleString()} — Score: ${l.lead_score||0} (${l.lead_status||''})`);
  const text = `*☘️ ShamrockLeads Export* (${state.total} total)\n${lines.join('\n')}${state.total>20?'\n_...and '+(state.total-20)+' more_':''}`;
  navigator.clipboard.writeText(text).then(()=>toast('Copied to clipboard — paste in Slack!','success')).catch(()=>toast('Copy failed','error'));
}

// ── Hot Lead Sound ──
function playHotAlert() {
  toast('🔥 New hot lead detected!','info');
  try {
    const ctx = new (window.AudioContext||window.webkitAudioContext)();
    const osc = ctx.createOscillator(); const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.setValueAtTime(1100, ctx.currentTime + 0.1);
    osc.frequency.setValueAtTime(880, ctx.currentTime + 0.2);
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.4);
    osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.4);
  } catch(e) {}
}

// ── Toast ──
function toast(msg, type='info') {
  const t = document.getElementById('toast');
  const icons = {success:'✅',error:'❌',info:'ℹ️'};
  t.querySelector('.toast-icon').textContent = icons[type]||'ℹ️';
  t.querySelector('.toast-message').textContent = msg;
  t.className = `toast-notification toast-${type} show`;
  setTimeout(()=>t.classList.remove('show'), 3500);
}

// ── Utilities ──
function fmt(n) { return n >= 1000000 ? (n/1000000).toFixed(1)+'M' : n >= 1000 ? (n/1000).toFixed(1)+'K' : n; }
function timeAgo(iso) { const d=(Date.now()-new Date(iso).getTime())/1000; if(d<60) return Math.round(d)+'s'; if(d<3600) return Math.round(d/60)+'m'; if(d<86400) return Math.round(d/3600)+'h'; return Math.round(d/86400)+'d'; }
function fmtDate(d) { if(!d) return '—'; try { const dt = new Date(d); return isNaN(dt)?d:dt.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'2-digit'}); } catch(e) { return d; } }
function isCourtSoon(d) { if(!d) return false; try { const dt=new Date(d); const diff=(dt-Date.now())/(1000*60*60*24); return diff>=0&&diff<=3; } catch(e) { return false; } }

// ── Auto-Refresh ──
let cd = 30;
async function refresh() { cd = 30; await loadDashboard(); if (document.getElementById('tabLeads').classList.contains('active')) applyFilters(); }
setInterval(() => { cd--; document.getElementById('refreshMeta').textContent = `Auto-refresh in ${cd}s`; if (cd <= 0) { cd = 30; refresh(); } }, 1000);

// ── Close dropdown on outside click ──
document.addEventListener('click', e => { if (!e.target.closest('.multi-select')) { document.getElementById('countyDropdown')?.classList.remove('show'); document.querySelector('.multi-select-trigger')?.classList.remove('open'); } });

// ── Keyboard shortcuts ──
document.addEventListener('keydown', e => {
  if (e.key === '/' && !e.target.matches('input,textarea,select')) { e.preventDefault(); document.getElementById('searchInput')?.focus(); }
  if (e.key === 'Escape') { document.getElementById('searchInput').value=''; state.search=''; closeModal(); applyFilters(); }
});

// ── Mobile redirect ──
if (/Mobi|Android/i.test(navigator.userAgent) && !location.pathname.includes('mobile')) {
  const mobilePath = location.pathname.replace('index.html','') + 'mobile.html';
  if (confirm('Switch to mobile view?')) location.href = mobilePath;
}

// ── Init ──
loadDashboard();

return { toggleTheme, switchTab, toggleCountyDropdown, filterCountyOptions, toggleCounty,
  applyPreset, setDays, setBond, sortBy, debounceSearch, debounceDefSearch, applyFilters,
  goPage, openBondModal, closeModal, submitBond, exportCSV, copyToSlack, clearAll, refresh, toast };
})();
