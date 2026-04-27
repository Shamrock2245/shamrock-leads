/* ShamrockLeads Core — State, Theme, Tabs, County Select */
window.SL_STATE = {
  counties: [], selectedCounties: [], days: 0, custody: '', status: '',
  minBond: 0, search: '', sort: 'arrest_date', order: 'desc',
  page: 1, limit: 50, leads: [], total: 0, pages: 1,
  defSort: 'bond_amount', defOrder: 'desc', defCustody: '', defCounty: '', defBond: 0, defPage: 1, defLimit: 48,
  scraperData: {}, mongoData: {}, prevHotCount: -1
};
const API = location.origin;
const PRESETS = {
  swfl: ['Lee','Collier','Charlotte','DeSoto','Hendry','Sarasota','Manatee'],
  all: [], none: []
};
let searchTimer = null;

function toggleTheme() {
  const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = t;
  localStorage.setItem('sl-theme', t);
}
(function(){ document.documentElement.dataset.theme = localStorage.getItem('sl-theme') || 'dark'; })();

function switchTab(btn) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(btn.dataset.tab).classList.add('active');
  if (btn.dataset.tab === 'tabLeads' && SL_STATE.leads.length === 0) applyFilters();
  if (btn.dataset.tab === 'tabDefendants') loadDefendants();
  if (btn.dataset.tab === 'tabHealth') renderHealth();
}

function toggleCountyDropdown() {
  document.getElementById('countyDropdown').classList.toggle('show');
  document.querySelector('.multi-select-trigger').classList.toggle('open');
}
function buildCountyOptions(counties) {
  SL_STATE.counties = counties;
  const el = document.getElementById('countyOptions');
  el.innerHTML = counties.map(c => {
    const chk = SL_STATE.selectedCounties.includes(c) ? 'checked' : '';
    return `<label class="multi-select-option ${chk?'checked':''}"><input type="checkbox" value="${c}" ${chk} onchange="toggleCounty('${c}',this.checked)">${c}</label>`;
  }).join('');
  updateCountyLabel();
  // Populate defendant county filter
  const df = document.getElementById('defCountyFilter');
  if (df && df.options.length <= 1) {
    counties.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; df.appendChild(o); });
  }
}
function toggleCounty(county, checked) {
  if (checked && !SL_STATE.selectedCounties.includes(county)) SL_STATE.selectedCounties.push(county);
  else SL_STATE.selectedCounties = SL_STATE.selectedCounties.filter(c => c !== county);
  updateCountyLabel(); buildCountyOptions(SL_STATE.counties); applyFilters();
}
function updateCountyLabel() {
  const el = document.getElementById('countyLabel');
  const n = SL_STATE.selectedCounties.length;
  if (n === 0) el.innerHTML = 'All Counties';
  else if (n <= 3) el.innerHTML = SL_STATE.selectedCounties.join(', ');
  else el.innerHTML = `${n} counties <span class="multi-select-count">${n}</span>`;
}
function filterCountyOptions(q) {
  document.querySelectorAll('.multi-select-option').forEach(o => {
    o.style.display = o.textContent.toLowerCase().includes(q.toLowerCase()) ? '' : 'none';
  });
}
function applyPreset(name) {
  document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
  SL_STATE.selectedCounties = name === 'all' || name === 'none' ? [] : [...PRESETS[name]];
  event.target.closest('.preset-btn').classList.add('active');
  buildCountyOptions(SL_STATE.counties); applyFilters();
}

function setDays(d) {
  SL_STATE.days = d; SL_STATE.page = 1;
  document.querySelectorAll('#dateRange button').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active'); applyFilters();
}
function setBond(v) {
  SL_STATE.minBond = v; SL_STATE.page = 1;
  document.querySelectorAll('#bondRange button').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active'); applyFilters();
}
function setDefBond(v) {
  SL_STATE.defBond = v; SL_STATE.defPage = 1;
  document.querySelectorAll('#defBondRange button').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active'); loadDefendants();
}
function sortBy(field) {
  if (SL_STATE.sort === field) SL_STATE.order = SL_STATE.order === 'desc' ? 'asc' : 'desc';
  else { SL_STATE.sort = field; SL_STATE.order = 'desc'; }
  SL_STATE.page = 1;
  document.querySelectorAll('th[data-sort]').forEach(th => th.classList.remove('sort-asc','sort-desc'));
  const th = document.querySelector(`th[data-sort="${field}"]`);
  if (th) th.classList.add(SL_STATE.order === 'desc' ? 'sort-desc' : 'sort-asc');
  applyFilters();
}
function debounceSearch() { clearTimeout(searchTimer); searchTimer = setTimeout(() => { SL_STATE.search = document.getElementById('searchInput').value; SL_STATE.page = 1; applyFilters(); }, 350); }
function debounceDefSearch() { clearTimeout(searchTimer); searchTimer = setTimeout(loadDefendants, 350); }

// ── SSE Real-time Events (graceful — no page reload on failure) ───────────
let _sseRetries = 0;
function initSSE() {
  const es = new EventSource('/api/events/stream');
  es.addEventListener('new_arrest', (e) => {
      try { const data = JSON.parse(e.data); toast(`🚨 New arrest: ${data.full_name} (${data.county})`, 'info'); } catch(_){}
  });
  es.addEventListener('hot_lead', (e) => {
      try { const data = JSON.parse(e.data); toast(`🔥 Hot lead: ${data.full_name} — Score ${data.lead_score}`, 'info'); playHotAlert(); } catch(_){}
  });
  es.addEventListener('bond_written', (e) => {
      try { const data = JSON.parse(e.data); toast(`✅ Bond written: ${data.defendant_name}`, 'success'); if (typeof SLTracking !== 'undefined') SLTracking.onBondWritten(data); } catch(_){}
  });
  es.onerror = () => {
    es.close();
    _sseRetries++;
    // Back off: 10s, 30s, 60s, then stop trying
    if (_sseRetries <= 3) {
      const delay = [10000, 30000, 60000][_sseRetries - 1];
      setTimeout(initSSE, delay);
    }
    // Never reload the page — SSE is optional
  };
}
// Only attempt SSE if not on localhost dev
try { initSSE(); } catch(_) {}

// ── SL namespace (used by index.html onclick attributes) ──────────────────
const SL = {
  switchTab: (btn) => switchTab(btn),
  toggleTheme: () => toggleTheme(),
  refresh: () => { if (typeof applyFilters === 'function') applyFilters(); },
  applyPreset: (name) => applyPreset(name),
  applyFilters: () => applyFilters(),
  setDays: (d) => setDays(d),
  setBond: (v) => setBond(v),
  sortBy: (f) => sortBy(f),
  debounceSearch: () => debounceSearch(),
  toggleCountyDropdown: () => toggleCountyDropdown(),
  filterCountyOptions: (v) => filterCountyOptions(v),
  closeModal: () => { const m = document.getElementById('bondModal'); if (m) m.style.display = 'none'; },
  submitBond: () => { if (typeof submitBond === 'function') submitBond(); },
  toast: (msg, type) => toast(msg, type),
};

// Utilities
function fmt(n) { return n >= 1000000 ? (n/1000000).toFixed(1)+'M' : n >= 1000 ? (n/1000).toFixed(1)+'K' : n.toString(); }
function timeAgo(iso) { if(!iso) return '—'; const d=(Date.now()-new Date(iso).getTime())/1000; if(d<60) return Math.round(d)+'s ago'; if(d<3600) return Math.round(d/60)+'m ago'; if(d<86400) return Math.round(d/3600)+'h ago'; return Math.round(d/86400)+'d ago'; }
function fmtDate(d) { if(!d) return '—'; try { const dt = new Date(d); return isNaN(dt)?d:dt.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'2-digit'}); } catch(e) { return d; } }
function isCourtSoon(d) { if(!d) return false; try { const dt=new Date(d); const diff=(dt-Date.now())/(1000*60*60*24); return diff>=0&&diff<=3; } catch(e) { return false; } }
function toast(msg, type='info') {
  const t = document.getElementById('toast');
  t.querySelector('.toast-icon').textContent = {success:'✅',error:'❌',info:'ℹ️'}[type]||'ℹ️';
  t.querySelector('.toast-message').textContent = msg;
  t.className = `toast-notification toast-${type} show`;
  setTimeout(()=>t.classList.remove('show'), 3500);
}
function playHotAlert() {
  toast('🔥 New hot lead detected!','info');
  try {
    const ctx = new (window.AudioContext||window.webkitAudioContext)();
    const osc = ctx.createOscillator(); const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.setValueAtTime(1100, ctx.currentTime + 0.1);
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.4);
    osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.4);
  } catch(e) {}
}
