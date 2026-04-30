/* ShamrockLeads Core — State, Theme, Tabs, County Select, SSE, Notifications
   Enhanced: Full SSE event routing, badge counters, desktop notifications,
   sound alerts, activity feed auto-update, keyboard shortcuts
*/
window.SL_STATE = {
  counties: [], selectedCounties: [], days: 0, custody: '', status: '',
  minBond: 0, search: '', sort: 'arrest_date', order: 'desc',
  page: 1, limit: 50, leads: [], total: 0, pages: 1,
  defSort: 'bond_amount', defOrder: 'desc', defCustody: '', defCounty: '', defBond: 0, defPage: 1, defLimit: 48,
  scraperData: {}, mongoData: {}, prevHotCount: -1,
  // Badge counters (incremented by SSE, reset on tab visit)
  badges: {
    leads: 0, prospective: 0, activeBonds: 0, tracking: 0,
    intake: 0, indemnitor: 0, calendar: 0
  },
  // Activity feed (live-updated via SSE)
  activityFeed: [],
  // Notification permission state
  notifGranted: false,
};

const API = location.origin;
const PRESETS = {
  swfl: ['Lee','Collier','Charlotte','DeSoto','Hendry','Sarasota','Manatee'],
  all: [], none: []
};
let searchTimer = null;

// ── Theme ────────────────────────────────────────────────────────────────
function toggleTheme() {
  const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = t;
  localStorage.setItem('sl-theme', t);
}
(function(){ document.documentElement.dataset.theme = localStorage.getItem('sl-theme') || 'dark'; })();

// ── Tab Switching ─────────────────────────────────────────────────────────
function switchTab(btn) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  btn.classList.add('active');
  const tabId = btn.dataset.tab;
  document.getElementById(tabId).classList.add('active');

  // Reset badge for this tab on visit
  const badgeMap = {
    tabLeads: 'leads', tabProspective: 'prospective', tabActiveBonds: 'activeBonds',
    tabTracking: 'tracking', tabIntake: 'intake', tabIndemnitor: 'indemnitor',
    tabCalendar: 'calendar'
  };
  if (badgeMap[tabId]) {
    SL_STATE.badges[badgeMap[tabId]] = 0;
    _updateBadgeEl(badgeMap[tabId], 0);
  }

  if (tabId === 'tabLeads' && SL_STATE.leads.length === 0) applyFilters();
  if (tabId === 'tabDefendants') loadDefendants();
  if (tabId === 'tabHealth') renderHealth();
  if (tabId === 'tabAnalytics') { if (typeof SLAnalytics !== 'undefined') SLAnalytics.load(); }
  if (tabId === 'tabCalendar') { if (typeof SLCalendar !== 'undefined') SLCalendar.load(); }
}

// ── Badge Helpers ─────────────────────────────────────────────────────────
const _badgeIds = {
  leads: 'leadsBadge', prospective: 'prospectiveBadge', activeBonds: 'activeBondsBadge',
  tracking: 'trackingBadge', intake: 'intakeBadge', indemnitor: 'indemnitorBadge',
  calendar: 'calendarBadge'
};

function _updateBadgeEl(key, count) {
  const el = document.getElementById(_badgeIds[key]);
  if (!el) return;
  if (count > 0) {
    el.textContent = count > 99 ? '99+' : count;
    el.classList.add('badge-live');
  } else {
    el.textContent = '—';
    el.classList.remove('badge-live');
  }
}

function _incrementBadge(key) {
  // Only increment if the tab is NOT currently active
  const tabMap = {
    leads: 'tabLeads', prospective: 'tabProspective', activeBonds: 'tabActiveBonds',
    tracking: 'tabTracking', intake: 'tabIntake', indemnitor: 'tabIndemnitor',
    calendar: 'tabCalendar'
  };
  const tabEl = document.getElementById(tabMap[key]);
  if (tabEl && tabEl.classList.contains('active')) return;
  SL_STATE.badges[key] = (SL_STATE.badges[key] || 0) + 1;
  _updateBadgeEl(key, SL_STATE.badges[key]);
}

// ── Activity Feed ─────────────────────────────────────────────────────────
function _addActivity(icon, text, type = 'info') {
  const item = { icon, text, type, ts: new Date().toISOString() };
  SL_STATE.activityFeed.unshift(item);
  if (SL_STATE.activityFeed.length > 50) SL_STATE.activityFeed.pop();
  _renderActivityFeed();
}

function _renderActivityFeed() {
  const el = document.getElementById('recentActivity');
  if (!el) return;
  if (SL_STATE.activityFeed.length === 0) {
    el.innerHTML = '<div class="activity-empty">No recent activity</div>';
    return;
  }
  el.innerHTML = SL_STATE.activityFeed.slice(0, 20).map(item => `
    <div class="activity-item activity-${item.type}">
      <span class="activity-icon">${item.icon}</span>
      <span class="activity-text">${item.text}</span>
      <span class="activity-time">${timeAgo(item.ts)}</span>
    </div>
  `).join('');
}

// ── Desktop Notifications ─────────────────────────────────────────────────
function _requestNotifPermission() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted') {
    SL_STATE.notifGranted = true;
  } else if (Notification.permission !== 'denied') {
    Notification.requestPermission().then(p => {
      SL_STATE.notifGranted = p === 'granted';
    });
  }
}

function _desktopNotif(title, body, icon = '🍀') {
  if (!SL_STATE.notifGranted || document.hasFocus()) return;
  try {
    new Notification(title, { body, icon: '/favicon.ico', tag: 'shamrock-lead' });
  } catch(e) {}
}

// ── Sound Alerts ──────────────────────────────────────────────────────────
function playHotAlert() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    // Two-tone ascending alert
    [[880, 0], [1100, 0.12], [1320, 0.24]].forEach(([freq, when]) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      osc.frequency.value = freq;
      osc.type = 'sine';
      gain.gain.setValueAtTime(0.12, ctx.currentTime + when);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + when + 0.25);
      osc.start(ctx.currentTime + when);
      osc.stop(ctx.currentTime + when + 0.25);
    });
  } catch(e) {}
}

function playPaymentAlert() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.setValueAtTime(523, ctx.currentTime);
    osc.frequency.setValueAtTime(659, ctx.currentTime + 0.1);
    osc.frequency.setValueAtTime(784, ctx.currentTime + 0.2);
    osc.type = 'sine';
    gain.gain.setValueAtTime(0.1, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.5);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.5);
  } catch(e) {}
}

// ── SSE Real-time Events ──────────────────────────────────────────────────
let _sseRetries = 0;
let _sseInstance = null;

function initSSE() {
  if (_sseInstance) { try { _sseInstance.close(); } catch(e) {} }
  const es = new EventSource('/api/events/stream');
  _sseInstance = es;

  // ── New arrest ──────────────────────────────────────────────────────────
  es.addEventListener('new_arrest', (e) => {
    try {
      const data = JSON.parse(e.data);
      toast(`🚨 New arrest: ${data.full_name} (${data.county})`, 'info');
      _addActivity('🚨', `New arrest: ${data.full_name} — ${data.county}`, 'info');
      _incrementBadge('leads');
    } catch(_) {}
  });

  // ── Hot lead ────────────────────────────────────────────────────────────
  es.addEventListener('hot_lead', (e) => {
    try {
      const data = JSON.parse(e.data);
      toast(`🔥 Hot lead: ${data.full_name} — Score ${data.lead_score}`, 'warning');
      _addActivity('🔥', `Hot lead: ${data.full_name} (${data.county}) · Score ${data.lead_score}`, 'warning');
      _incrementBadge('leads');
      playHotAlert();
      _desktopNotif('🔥 Hot Lead!', `${data.full_name} — Score ${data.lead_score} in ${data.county}`);
    } catch(_) {}
  });

  // ── Rearrest ────────────────────────────────────────────────────────────
  es.addEventListener('rearrest_detected', (e) => {
    try {
      const data = JSON.parse(e.data);
      toast(`🚨 Repeat offender: ${data.full_name}`, 'error');
      _addActivity('🚨', `Repeat offender detected: ${data.full_name}`, 'error');
      if (typeof SLRearrest !== 'undefined') SLRearrest.onSSE(data);
      _desktopNotif('🚨 Repeat Offender!', `${data.full_name} has been rearrested`);
    } catch(_) {}
  });

  // ── Bond written ────────────────────────────────────────────────────────
  es.addEventListener('bond_written', (e) => {
    try {
      const data = JSON.parse(e.data);
      toast(`✅ Bond written: ${data.defendant_name}`, 'success');
      _addActivity('✅', `Bond written: ${data.defendant_name} — ${data.county}`, 'success');
      _incrementBadge('activeBonds');
      if (typeof SLTracking !== 'undefined') SLTracking.onBondWritten(data);
    } catch(_) {}
  });

  // ── Payment confirmed ───────────────────────────────────────────────────
  es.addEventListener('payment_confirmed', (e) => {
    try {
      const data = JSON.parse(e.data);
      const amt = data.amount ? `$${Number(data.amount).toLocaleString()}` : '';
      toast(`💰 Payment received: ${amt} — ${data.defendant_name || data.customer_name || ''}`, 'success');
      _addActivity('💰', `Payment received: ${amt} for ${data.defendant_name || data.booking_number || ''}`, 'success');
      playPaymentAlert();
      _desktopNotif('💰 Payment Received!', `${amt} — ${data.defendant_name || ''}`);
    } catch(_) {}
  });

  // ── Payment received (legacy event name) ────────────────────────────────
  es.addEventListener('payment_received', (e) => {
    try {
      const data = JSON.parse(e.data);
      const amt = data.amount ? `$${Number(data.amount).toLocaleString()}` : '';
      toast(`💰 Payment: ${amt}`, 'success');
      _addActivity('💰', `Payment: ${amt}`, 'success');
      playPaymentAlert();
    } catch(_) {}
  });

  // ── Document signed ─────────────────────────────────────────────────────
  es.addEventListener('document_signed', (e) => {
    try {
      const data = JSON.parse(e.data);
      toast(`📝 Document signed: ${data.document_name || data.booking_number || ''}`, 'success');
      _addActivity('📝', `Document signed: ${data.document_name || ''} for ${data.defendant_name || data.booking_number || ''}`, 'success');
      _desktopNotif('📝 Document Signed', `${data.document_name || 'Bond document'} completed`);
    } catch(_) {}
  });

  // ── New intake ──────────────────────────────────────────────────────────
  es.addEventListener('new_intake', (e) => {
    try {
      const data = JSON.parse(e.data);
      toast(`📥 New intake: ${data.defendant_name || data.source || 'Unknown'}`, 'info');
      _addActivity('📥', `New intake: ${data.defendant_name || ''} via ${data.source || ''}`, 'info');
      _incrementBadge('intake');
      if (typeof SLIntake !== 'undefined') SLIntake.load();
    } catch(_) {}
  });

  // ── Incoming message ────────────────────────────────────────────────────
  es.addEventListener('message_received', (e) => {
    try {
      const data = JSON.parse(e.data);
      toast(`📱 Message from ${data.from || 'unknown'}`, 'info');
      _addActivity('📱', `iMessage from ${data.from || ''}: ${(data.text || '').slice(0, 60)}`, 'info');
      _incrementBadge('prospective');
    } catch(_) {}
  });

  es.addEventListener('sms_received', (e) => {
    try {
      const data = JSON.parse(e.data);
      toast(`💬 SMS from ${data.from || 'unknown'}`, 'info');
      _addActivity('💬', `SMS from ${data.from || ''}: ${(data.body || '').slice(0, 60)}`, 'info');
      _incrementBadge('prospective');
    } catch(_) {}
  });

  // ── Scraper error ───────────────────────────────────────────────────────
  es.addEventListener('scraper_error', (e) => {
    try {
      const data = JSON.parse(e.data);
      toast(`⚠️ Scraper error: ${data.county || data.scraper || ''}`, 'error');
      _addActivity('⚠️', `Scraper error: ${data.county || data.scraper || ''} — ${data.error || ''}`, 'error');
    } catch(_) {}
  });

  // ── Court reminder sent ─────────────────────────────────────────────────
  es.addEventListener('court_reminder_sent', (e) => {
    try {
      const data = JSON.parse(e.data);
      _addActivity('📅', `Court reminder sent to ${data.defendant_name || ''} (${data.county || ''})`, 'info');
      _incrementBadge('calendar');
    } catch(_) {}
  });

  // ── Heartbeat (keep connection alive) ───────────────────────────────────
  es.addEventListener('heartbeat', () => {
    _sseRetries = 0; // Reset retry counter on heartbeat
  });

  es.onerror = () => {
    es.close();
    _sseInstance = null;
    _sseRetries++;
    if (_sseRetries <= 5) {
      const delays = [5000, 15000, 30000, 60000, 120000];
      const delay = delays[Math.min(_sseRetries - 1, delays.length - 1)];
      setTimeout(initSSE, delay);
    }
    // Never reload the page — SSE is optional enhancement
  };
}

// Request notification permission on first user interaction
document.addEventListener('click', function _firstClick() {
  _requestNotifPermission();
  document.removeEventListener('click', _firstClick);
}, { once: true });

// Only attempt SSE if browser supports it
try { initSSE(); } catch(_) {}

// ── Keyboard Shortcuts ────────────────────────────────────────────────────
document.addEventListener('keydown', function(e) {
  // Ignore if typing in an input
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;

  // Cmd/Ctrl+K — Quick search
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
      // Switch to leads tab first
      const leadsBtn = document.querySelector('[data-tab="tabLeads"]');
      if (leadsBtn) { leadsBtn.click(); }
      setTimeout(() => { searchInput.focus(); searchInput.select(); }, 100);
    }
    return;
  }

  // Number keys 1-9 for tab switching (no modifier)
  if (!e.metaKey && !e.ctrlKey && !e.altKey && e.key >= '1' && e.key <= '9') {
    const tabBtns = document.querySelectorAll('.tab-btn:not(.inv-tab-trigger)');
    const idx = parseInt(e.key) - 1;
    if (tabBtns[idx]) tabBtns[idx].click();
    return;
  }

  // R — Refresh
  if (!e.metaKey && !e.ctrlKey && e.key === 'r') {
    if (typeof SL !== 'undefined' && SL.refresh) SL.refresh();
    return;
  }
});

// ── County Multi-Select ───────────────────────────────────────────────────
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
  const df = document.getElementById('defCountyFilter');
  if (df && df.options.length <= 1) {
    counties.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; df.appendChild(o); });
  }
  // Populate calendar county filter
  const cf = document.getElementById('calCountyFilter');
  if (cf && cf.options.length <= 1) {
    counties.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; cf.appendChild(o); });
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

// ── Filter Helpers ────────────────────────────────────────────────────────
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

// ── SL namespace is defined in sl-features.js ─────────────────────────────

// ── Utilities ─────────────────────────────────────────────────────────────
function fmt(n) { return n >= 1000000 ? (n/1000000).toFixed(1)+'M' : n >= 1000 ? (n/1000).toFixed(1)+'K' : n.toString(); }
function timeAgo(iso) {
  if(!iso) return '—';
  const d=(Date.now()-new Date(iso).getTime())/1000;
  if(d<60) return Math.round(d)+'s ago';
  if(d<3600) return Math.round(d/60)+'m ago';
  if(d<86400) return Math.round(d/3600)+'h ago';
  return Math.round(d/86400)+'d ago';
}
function fmtDate(d) {
  if(!d) return '—';
  try { const dt = new Date(d); return isNaN(dt)?d:dt.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'2-digit'}); } catch(e) { return d; }
}
function isCourtSoon(d) {
  if(!d) return false;
  try { const dt=new Date(d); const diff=(dt-Date.now())/(1000*60*60*24); return diff>=0&&diff<=3; } catch(e) { return false; }
}

// ── Toast Notifications ───────────────────────────────────────────────────
const _toastQueue = [];
let _toastActive = false;

function toast(msg, type='info') {
  _toastQueue.push({ msg, type });
  if (!_toastActive) _processToastQueue();
}

function _processToastQueue() {
  if (_toastQueue.length === 0) { _toastActive = false; return; }
  _toastActive = true;
  const { msg, type } = _toastQueue.shift();
  const t = document.getElementById('toast');
  if (!t) { _toastActive = false; return; }
  const icons = { success: '✅', error: '❌', info: 'ℹ️', warning: '⚠️' };
  t.querySelector('.toast-icon').textContent = icons[type] || 'ℹ️';
  t.querySelector('.toast-message').textContent = msg;
  t.className = `toast-notification toast-${type} show`;
  setTimeout(() => {
    t.classList.remove('show');
    setTimeout(_processToastQueue, 300);
  }, 3500);
}
