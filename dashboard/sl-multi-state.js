/**
 * SLMultiState — Multi-State Operations Dashboard Module
 * Shows live scraper status, arrest data, and system health across FL, GA, SC, NC, TN, TX, LA, CT, AL, MS.
 * Uses ApexCharts for charts. API: /api/ops/*
 */
const SLMultiState = (() => {
  let _registryData = [];
  let _stateFilter = 'ALL';
  let _platformFilter = '';
  let _statusFilter = '';
  let _searchQuery = '';
  let _arrestsData = [];
  let _feedTimer = null;
  let _registryChart = null;
  let _platformChart = null;
  let _initialized = false;

  // STATE_ORDER controls card layout order. Live states first, scaffolded states at end.
  const STATE_ORDER = ['FL', 'GA', 'SC', 'NC', 'TN', 'TX', 'LA', 'CT', 'AL', 'MS'];
  const STATE_NAMES = {
    FL: 'Florida',
    GA: 'Georgia',
    SC: 'South Carolina',
    NC: 'North Carolina',
    TN: 'Tennessee',
    TX: 'Texas',
    LA: 'Louisiana',
    CT: 'Connecticut',
    AL: 'Alabama',
    MS: 'Mississippi',
  };
  const STATE_EMOJI = {
    FL: '🌴', GA: '🍑', SC: '🌙', NC: '🦅',
    TN: '🎸', TX: '⭐',  LA: '🎷', CT: '⚓',
    AL: '🌻', MS: '🎶',
  };
  const STATE_COLORS = {
    FL: '#00d4aa',
    GA: '#f59e0b',
    SC: '#8b5cf6',
    NC: '#3b82f6',
    TN: '#ef4444',
    TX: '#eab308',
    LA: '#ec4899',
    CT: '#06b6d4',
    AL: '#f97316',
    MS: '#84cc16',
  };
  // States that are scaffolded (no live data yet) — shown with a dimmed card style.
  const SCAFFOLDED_STATES = new Set(['CT', 'AL', 'MS']);

  const PLATFORM_COLORS = {
    'JailTracker':   '#ef4444',
    'P2C':           '#3b82f6',
    'EAS':           '#10b981',
    'InteropWeb':    '#f59e0b',
    'Zuercher':      '#8b5cf6',
    'Southern SW':   '#ec4899',
    'Socrata':       '#06b6d4',
    'XML Feed':      '#84cc16',
    'New World':     '#f97316',
    'Tyler Odyssey': '#6366f1',
    'Kologik':       '#eab308',
    'SmartCOP':      '#14b8a6',
    'SmartWeb':      '#a78bfa',
    'Custom HTML':   '#94a3b8',
  };

  const STATUS_CONFIG = {
    ok:        { label: 'Active',    cls: 'ms-badge-ok',      icon: '●' },
    healthy:   { label: 'Healthy',   cls: 'ms-badge-ok',      icon: '●' },
    stale:     { label: 'Stale',     cls: 'ms-badge-warn',    icon: '◐' },
    warning:   { label: 'Warning',   cls: 'ms-badge-warn',    icon: '◐' },
    error:     { label: 'Error',     cls: 'ms-badge-error',   icon: '✕' },
    offline:   { label: 'Offline',   cls: 'ms-badge-error',   icon: '✕' },
    never_run: { label: 'Pending',   cls: 'ms-badge-pending', icon: '○' },
    disabled:  { label: 'Disabled',  cls: 'ms-badge-disabled',icon: '—' },
  };

  function _statusCfg(s) {
    return STATUS_CONFIG[s] || STATUS_CONFIG.never_run;
  }

  function _fmtRelative(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d)) return '—';
    const mins = Math.round((Date.now() - d) / 60000);
    if (mins < 2) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  }

  function _fmtNum(n) {
    if (n == null || n === 0) return '—';
    return Number(n).toLocaleString();
  }

  // ─── INIT ─────────────────────────────────────────────────────────────────
  async function init() {
    if (_initialized) { await _refresh(); return; }
    _initialized = true;
    _renderShell();
    await _refresh();
    _startAutoRefresh();
  }

  async function _refresh() {
    await Promise.all([_loadStateSummary(), _loadRegistry(), _loadLiveFeed()]);
  }

  function _startAutoRefresh() {
    if (_feedTimer) clearInterval(_feedTimer);
    _feedTimer = setInterval(() => {
      const tab = document.getElementById('tabMultiState');
      if (tab && tab.classList.contains('active')) _refresh();
    }, 30000);
  }

  // ─── SHELL ────────────────────────────────────────────────────────────────
  function _renderShell() {
    const container = document.getElementById('tabMultiState');
    if (!container) return;
    container.innerHTML = `
      <div class="ms-header">
        <div class="ms-title">
          <span class="ms-title-icon">🌎</span>
          <div>
            <h2 class="ms-title-text">Multi-State Operations</h2>
            <p class="ms-title-sub">Live scraper network across Florida, Georgia, South Carolina &amp; North Carolina &mdash; expanding to Tennessee, Texas, Louisiana, Connecticut, Alabama &amp; Mississippi</p>
          </div>
        </div>
        <div class="ms-header-actions">
          <span id="msLastRefresh" class="ms-last-refresh">—</span>
          <button class="ms-btn ms-btn-primary" onclick="SLMultiState.refresh()">↻ Refresh</button>
          <button class="ms-btn ms-btn-danger" onclick="SLMultiState.runAll()">▶ Run All</button>
        </div>
      </div>

      <!-- STATE KPI CARDS -->
      <div id="msStateCards" class="ms-state-cards">
        <div class="ms-kpi-skeleton"></div>
        <div class="ms-kpi-skeleton"></div>
        <div class="ms-kpi-skeleton"></div>
        <div class="ms-kpi-skeleton"></div>
      </div>

      <!-- CHARTS ROW -->
      <div class="ms-charts-row">
        <div class="ms-chart-card">
          <div class="ms-chart-title">Scrapers by State</div>
          <div id="msStateChart" style="height:220px"></div>
        </div>
        <div class="ms-chart-card">
          <div class="ms-chart-title">Platform Distribution</div>
          <div id="msPlatformChart" style="height:220px"></div>
        </div>
        <div class="ms-chart-card ms-chart-card-wide">
          <div class="ms-chart-title">Arrests — Last 7 Days by State</div>
          <div id="msArrestsChart" style="height:220px"></div>
        </div>
      </div>

      <!-- LIVE FEED + REGISTRY SPLIT -->
      <div class="ms-split-row">
        <!-- LIVE ARREST FEED -->
        <div class="ms-feed-panel">
          <div class="ms-panel-header">
            <span class="ms-panel-title">⚡ Live Arrest Feed</span>
            <span id="msFeedCount" class="ms-badge-count">—</span>
          </div>
          <div id="msFeedList" class="ms-feed-list">
            <div class="ms-loading">Loading feed…</div>
          </div>
        </div>

        <!-- SCRAPER REGISTRY TABLE -->
        <div class="ms-registry-panel">
          <div class="ms-panel-header">
            <span class="ms-panel-title">🗂 Scraper Registry</span>
            <span id="msRegistryCount" class="ms-badge-count">—</span>
          </div>
          <div class="ms-registry-filters">
            <input id="msSearch" type="text" class="ms-search" placeholder="Search county…" oninput="SLMultiState.setSearch(this.value)">
            <select id="msStateFilter" class="ms-select" onchange="SLMultiState.setStateFilter(this.value)">
              <option value="ALL">All States</option>
              <option value="FL">🌴 Florida</option>
              <option value="GA">🍑 Georgia</option>
              <option value="SC">🌙 South Carolina</option>
              <option value="NC">🦅 North Carolina</option>
              <option value="TN">🎸 Tennessee</option>
              <option value="TX">⭐ Texas</option>
              <option value="LA">🎷 Louisiana</option>
              <option value="CT">⚓ Connecticut</option>
              <option value="AL">🌻 Alabama</option>
              <option value="MS">🎶 Mississippi</option>
            </select>
            <select class="ms-select" onchange="SLMultiState.setStatusFilter(this.value)">
              <option value="">All Status</option>
              <option value="ok">Active</option>
              <option value="error">Error</option>
              <option value="never_run">Pending</option>
              <option value="disabled">Disabled</option>
            </select>
          </div>
          <div class="ms-registry-table-wrap">
            <table class="ms-registry-table">
              <thead>
                <tr>
                  <th>County</th>
                  <th>State</th>
                  <th>Platform</th>
                  <th>Status</th>
                  <th>Last Run</th>
                  <th>Records</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody id="msRegistryBody">
                <tr><td colspan="7" class="ms-loading">Loading registry…</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;
  }

  // ─── STATE SUMMARY CARDS ──────────────────────────────────────────────────
  async function _loadStateSummary() {
    try {
      const res = await fetch('/api/ops/state-summary');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      _renderStateCards(data.states);
      _renderStateChart(data.states);
      _renderArrestsChart(data.states);
    } catch (e) {
      console.error('[SLMultiState] state-summary error:', e);
    }
  }

  function _renderStateCards(states) {
    const container = document.getElementById('msStateCards');
    if (!container) return;

    container.innerHTML = STATE_ORDER.map(s => {
      const d = states[s] || {};
      const color = STATE_COLORS[s] || '#64748b';
      const isScaffolded = SCAFFOLDED_STATES.has(s);
      const healthPct = d.total_counties > 0
        ? Math.round((d.active_scrapers / d.total_counties) * 100)
        : (isScaffolded ? 0 : 0);
      const scaffoldBadge = isScaffolded
        ? `<div class="ms-scaffolded-badge">🚧 In Development</div>`
        : '';
      return `
        <div class="ms-state-card${isScaffolded ? ' ms-state-card--scaffolded' : ''}" style="--state-color:${color}" onclick="SLMultiState.setStateFilter('${s}')" title="${isScaffolded ? STATE_NAMES[s] + ' — Scrapers in development' : 'Filter registry to ' + STATE_NAMES[s]}">
          ${scaffoldBadge}
          <div class="ms-state-card-header">
            <span class="ms-state-emoji">${STATE_EMOJI[s] || '📍'}</span>
            <div>
              <div class="ms-state-name">${STATE_NAMES[s] || s}</div>
              <div class="ms-state-abbr">${s}</div>
            </div>
            <div class="ms-state-health-ring">
              <svg viewBox="0 0 36 36" class="ms-ring-svg">
                <circle cx="18" cy="18" r="15.9" fill="none" stroke="rgba(255,255,255,0.1)" stroke-width="3"/>
                <circle cx="18" cy="18" r="15.9" fill="none" stroke="${color}" stroke-width="3"
                  stroke-dasharray="${healthPct} ${100 - healthPct}"
                  stroke-dashoffset="25" stroke-linecap="round"/>
              </svg>
              <span class="ms-ring-pct">${healthPct}%</span>
            </div>
          </div>
          <div class="ms-state-metrics">
            <div class="ms-metric">
              <div class="ms-metric-val">${_fmtNum(d.total_counties) || '—'}</div>
              <div class="ms-metric-lbl">Counties</div>
            </div>
            <div class="ms-metric">
              <div class="ms-metric-val" style="color:${color}">${_fmtNum(d.active_scrapers) || '0'}</div>
              <div class="ms-metric-lbl">Active</div>
            </div>
            <div class="ms-metric">
              <div class="ms-metric-val" style="color:#ef4444">${_fmtNum(d.error_scrapers) || '0'}</div>
              <div class="ms-metric-lbl">Errors</div>
            </div>
            <div class="ms-metric">
              <div class="ms-metric-val">${_fmtNum(d.arrests_24h) || '0'}</div>
              <div class="ms-metric-lbl">24h Arrests</div>
            </div>
            <div class="ms-metric">
              <div class="ms-metric-val">${_fmtNum(d.arrests_7d) || '0'}</div>
              <div class="ms-metric-lbl">7d Arrests</div>
            </div>
            <div class="ms-metric">
              <div class="ms-metric-val">${_fmtNum(d.total_arrests) || '0'}</div>
              <div class="ms-metric-lbl">Total</div>
            </div>
          </div>
          <div class="ms-state-card-footer">
            <span class="ms-state-card-footer-stat">🔥 <strong style="color:#ef4444">${_fmtNum(d.hot_leads)||'0'}</strong> hot</span>
            <span class="ms-state-card-footer-stat">🟡 <strong style="color:#f59e0b">${_fmtNum(d.warm_leads)||'0'}</strong> warm</span>
            ${d.avg_bond ? `<span class="ms-state-card-footer-stat">💰 <strong style="color:#10b981">$${Number(d.avg_bond).toLocaleString(undefined,{maximumFractionDigits:0})}</strong> avg bond</span>` : ''}
          </div>
        </div>
      `;
    }).join('');
  }

  function _renderStateChart(states) {
    const el = document.getElementById('msStateChart');
    if (!el || typeof ApexCharts === 'undefined') return;
    if (_registryChart) { _registryChart.destroy(); _registryChart = null; }
    const labels = Object.keys(states);
    const values = labels.map(s => states[s].total_counties || 0);
    const colors = labels.map(s => STATE_COLORS[s] || '#64748b');
    _registryChart = new ApexCharts(el, {
      chart: { type: 'donut', background: 'transparent', height: 220 },
      series: values,
      labels: labels.map(s => STATE_NAMES[s] || s),
      colors,
      legend: { labels: { colors: '#94a3b8' } },
      dataLabels: { style: { colors: ['#0f172a'] } },
      theme: { mode: 'dark' },
      plotOptions: { pie: { donut: { size: '65%' } } },
    });
    _registryChart.render();
  }

  function _renderArrestsChart(states) {
    const el = document.getElementById('msArrestsChart');
    if (!el || typeof ApexCharts === 'undefined') return;
    const series = STATE_ORDER.map(s => ({
      name: STATE_NAMES[s] || s,
      data: [states[s]?.arrests_7d || 0],
    }));
    new ApexCharts(el, {
      chart: { type: 'bar', background: 'transparent', height: 220, toolbar: { show: false } },
      series,
      xaxis: { categories: ['Last 7 Days'], labels: { style: { colors: '#94a3b8' } } },
      yaxis: { labels: { style: { colors: '#94a3b8' } } },
      colors: STATE_ORDER.map(s => STATE_COLORS[s]),
      plotOptions: { bar: { columnWidth: '50%', borderRadius: 4 } },
      legend: { labels: { colors: '#94a3b8' } },
      theme: { mode: 'dark' },
      grid: { borderColor: '#1e293b' },
    }).render();
  }

  // ─── PLATFORM CHART ───────────────────────────────────────────────────────
  async function _loadPlatformChart() {
    try {
      const res = await fetch('/api/ops/platform-breakdown');
      if (!res.ok) return;
      const data = await res.json();
      const el = document.getElementById('msPlatformChart');
      if (!el || typeof ApexCharts === 'undefined') return;
      if (_platformChart) { _platformChart.destroy(); _platformChart = null; }
      const top = data.platforms.slice(0, 10);
      _platformChart = new ApexCharts(el, {
        chart: { type: 'bar', background: 'transparent', height: 220, toolbar: { show: false } },
        series: [{ name: 'Counties', data: top.map(p => p.total) }],
        xaxis: {
          categories: top.map(p => p.platform),
          labels: { style: { colors: '#94a3b8', fontSize: '10px' }, rotate: -30 },
        },
        yaxis: { labels: { style: { colors: '#94a3b8' } } },
        colors: top.map(p => PLATFORM_COLORS[p.platform] || '#64748b'),
        plotOptions: { bar: { distributed: true, borderRadius: 4, columnWidth: '60%' } },
        legend: { show: false },
        theme: { mode: 'dark' },
        grid: { borderColor: '#1e293b' },
      });
      _platformChart.render();
    } catch (e) {
      console.error('[SLMultiState] platform chart error:', e);
    }
  }

  // ─── REGISTRY TABLE ───────────────────────────────────────────────────────
  async function _loadRegistry() {
    try {
      const res = await fetch('/api/ops/scraper-registry');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      _registryData = data.scrapers || [];
      _renderRegistry();
      _loadPlatformChart();
      const countEl = document.getElementById('msRegistryCount');
      if (countEl) countEl.textContent = `${_registryData.length} scrapers`;
    } catch (e) {
      console.error('[SLMultiState] registry error:', e);
    }
  }

  function _renderRegistry() {
    const tbody = document.getElementById('msRegistryBody');
    if (!tbody) return;

    let filtered = _registryData;
    if (_stateFilter !== 'ALL') filtered = filtered.filter(r => r.state === _stateFilter);
    if (_statusFilter) filtered = filtered.filter(r => (r.status || 'never_run') === _statusFilter);
    if (_searchQuery) {
      const q = _searchQuery.toLowerCase();
      filtered = filtered.filter(r => r.county.toLowerCase().includes(q) || r.platform.toLowerCase().includes(q));
    }

    if (!filtered.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="ms-empty">No scrapers match the current filters.</td></tr>`;
      return;
    }

    const stateColor = r => STATE_COLORS[r.state] || '#64748b';
    const platColor = p => PLATFORM_COLORS[p] || '#64748b';

    tbody.innerHTML = filtered.map(r => {
      const sc = _statusCfg(r.status || 'never_run');
      const cEsc = (r.county || '').replace(/'/g, "\\'");
      const sEsc = (r.state || '').replace(/'/g, "\\'");
      return `
        <tr class="ms-registry-row" data-county="${r.county}" data-state="${r.state}">
          <td class="ms-county-cell">
            <span class="ms-county-name">${r.county}</span>
          </td>
          <td>
            <span class="ms-state-pill" style="background:${stateColor(r)}22;color:${stateColor(r)};border:1px solid ${stateColor(r)}44">${r.state}</span>
          </td>
          <td>
            <span class="ms-platform-pill" style="background:${platColor(r.platform)}22;color:${platColor(r.platform)}">${r.platform}</span>
          </td>
          <td>
            <span class="ms-status-badge ${sc.cls}">${sc.icon} ${sc.label}</span>
          </td>
          <td class="ms-muted">${_fmtRelative(r.last_run_iso)}</td>
          <td class="ms-muted">${_fmtNum(r.total_records)}</td>
          <td>
            <button class="ms-action-btn" onclick="SLMultiState.runCounty('${cEsc}','${sEsc}')" title="Run Now">▶</button>
            <button class="ms-action-btn ms-action-btn-secondary" onclick="SLMultiState.viewCountyArrests('${cEsc}','${sEsc}')" title="View Arrests">🔍</button>
          </td>
        </tr>
      `;
    }).join('');
  }

  // ─── LIVE FEED ────────────────────────────────────────────────────────────
  async function _loadLiveFeed() {
    try {
      const res = await fetch('/api/ops/live-feed?limit=60');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      _arrestsData = data.feed || [];
      _renderFeed();
      const countEl = document.getElementById('msFeedCount');
      if (countEl) countEl.textContent = `${_arrestsData.length} recent`;
      const refreshEl = document.getElementById('msLastRefresh');
      if (refreshEl) refreshEl.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    } catch (e) {
      console.error('[SLMultiState] live-feed error:', e);
    }
  }

  function _renderFeed() {
    const list = document.getElementById('msFeedList');
    if (!list) return;
    if (!_arrestsData.length) {
      list.innerHTML = `<div class="ms-empty">No recent arrests in the database yet.<br><span style="font-size:11px;color:#475569">Run scrapers to populate data.</span></div>`;
      return;
    }
    list.innerHTML = _arrestsData.map(a => {
      const state = a.state || '??';
      const color = STATE_COLORS[state] || '#64748b';
      const bail = a.bail_amount ? `$${Number(a.bail_amount).toLocaleString()}` : 'No Bail';
      const charge = a.charges ? (a.charges.length > 45 ? a.charges.substring(0, 45) + '…' : a.charges) : 'Unknown Charge';
      const time = _fmtRelative(a.scraped_at);
      return `
        <div class="ms-feed-item">
          <div class="ms-feed-state-dot" style="background:${color}" title="${state}"></div>
          <div class="ms-feed-content">
            <div class="ms-feed-name">${a.full_name || 'Unknown'}</div>
            <div class="ms-feed-meta">
              <span class="ms-feed-county" style="color:${color}">${a.county || '?'}, ${state}</span>
              <span class="ms-feed-charge">${charge}</span>
            </div>
          </div>
          <div class="ms-feed-right">
            <div class="ms-feed-bail">${bail}</div>
            <div class="ms-feed-time">${time}</div>
          </div>
        </div>
      `;
    }).join('');
  }

  // ─── ACTIONS ──────────────────────────────────────────────────────────────
  async function runCounty(county, state) {
    try {
      const res = await fetch('/api/scraper/run-now', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ county, state: state || undefined }),
      });
      const data = await res.json();
      if (data.ok) {
        const label = state ? `${county} (${state})` : county;
        _showToast(`▶ Run queued for ${label}`, 'success');
      } else {
        _showToast(`Error: ${data.error || 'Unknown'}`, 'error');
      }
    } catch (e) {
      _showToast(`Failed to trigger ${county}`, 'error');
    }
  }

  async function runAll() {
    if (!confirm('Trigger an immediate run for ALL registered scrapers (FL/GA/SC/NC/TN/TX/LA)? This will put significant load on the server.')) return;
    try {
      const res = await fetch('/api/scraper/run-all', { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        _showToast(`▶ Run queued for all ${data.triggered} scrapers`, 'success');
      }
    } catch (e) {
      _showToast('Failed to trigger run-all', 'error');
    }
  }

  function viewCountyArrests(county, state) {
    // Switch to Lead Explorer and filter by county (+ state when available)
    const leadsBtn = document.querySelector('[data-tab="tabLeads"]');
    if (leadsBtn) leadsBtn.click();
    setTimeout(() => {
      const stateSel = document.getElementById('stateFilter');
      if (stateSel && state) {
        stateSel.value = state;
      }
      const searchEl = document.getElementById('searchInput') || document.getElementById('leadSearch');
      if (searchEl) {
        searchEl.value = county;
        searchEl.dispatchEvent(new Event('input'));
      }
      if (window.SL && typeof window.SL.applyFilters === 'function') {
        if (stateSel) window.SL.applyFilters();
      } else if (typeof applyFilters === 'function') {
        applyFilters();
      }
    }, 300);
  }

  function _showToast(msg, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `ms-toast ms-toast-${type}`;
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.classList.add('ms-toast-visible'), 10);
    setTimeout(() => { toast.classList.remove('ms-toast-visible'); setTimeout(() => toast.remove(), 300); }, 3000);
  }

  // ─── FILTER SETTERS ───────────────────────────────────────────────────────
  function setStateFilter(v) {
    _stateFilter = v || 'ALL';
    const sel = document.getElementById('msStateFilter');
    if (sel) sel.value = _stateFilter;
    _renderRegistry();
  }
  function setStatusFilter(v) { _statusFilter = v; _renderRegistry(); }
  function setSearch(v) { _searchQuery = v; _renderRegistry(); }
  function refresh() { _refresh(); }

  return { init, refresh, runCounty, runAll, viewCountyArrests, setStateFilter, setStatusFilter, setSearch };
})();
