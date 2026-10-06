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
  let _arrestsChart = null;
  let _initialized = false;
  let _stateOrder = [];

  // Card order matches ACTIVE_STATE_CODES. Ohio is the guarded pilot.
  const STATE_ORDER = ['FL', 'GA', 'SC', 'NC', 'TN', 'TX', 'LA', 'AL', 'CT', 'MS', 'OH'];
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
    OH: 'Ohio',
  };
  const STATE_EMOJI = {
    FL: '🌴', GA: '🍑', SC: '🌙', NC: '🦅',
    TN: '🎸', TX: '⭐',  LA: '🎷', CT: '⚓',
    AL: '🌻', MS: '🎶', OH: '📍',
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
    OH: '#94a3b8',
  };
  // States that are scaffolded (no live data yet) — shown with a dimmed card style.
  // 2026-07-22: All 10 states now have live scrapers registered. None scaffolded.
  const SCAFFOLDED_STATES = new Set([]);

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
    'DCN':           '#22d3ee',
    'Custom HTML':   '#94a3b8',
  };

  const STATUS_CONFIG = {
    ok:        { label: 'Active',    cls: 'ms-badge-ok',      icon: '●' },
    healthy:   { label: 'Healthy',   cls: 'ms-badge-ok',      icon: '●' },
    empty:     { label: 'Empty',     cls: 'ms-badge-pending', icon: '○' },
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

  function _fmtCount(n) {
    const v = Number(n);
    if (!Number.isFinite(v)) return '0';
    return v.toLocaleString();
  }

  function _esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function _cardOrder() {
    return _stateOrder.length ? _stateOrder : STATE_ORDER;
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
    // 12s while tab is active so state KPIs / live feed track production
    _feedTimer = setInterval(() => {
      const tab = document.getElementById('tabMultiState');
      if (tab && tab.classList.contains('active')) _refresh();
    }, 12000);
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
            <p class="ms-title-sub" id="msTitleSub">Live scraper network across FL · GA · SC · NC · TN · TX · LA · AL · CT · MS · OH</p>
          </div>
        </div>
        <div class="ms-header-actions">
          <span id="msLastRefresh" class="ms-last-refresh">—</span>
          <button class="sl-btn sl-btn-secondary" onclick="SLMultiState.refresh()">↻ Refresh</button>
          <button class="sl-btn sl-btn-danger" onclick="SLMultiState.runAll()">▶ Run All</button>
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
              <option value="OH">📍 Ohio</option>
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
      const res = await fetch('/api/ops/state-summary', { credentials: 'same-origin', cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (Array.isArray(data.state_order) && data.state_order.length) {
        _stateOrder = data.state_order.slice();
      }
      _renderStateCards(data.states || {});
      _renderStateChart(data.states || {});
      _renderArrestsChart(data.states || {});
      _updateFleetSubtitle(data);
    } catch (e) {
      console.error('[SLMultiState] state-summary error:', e);
    }
  }

  function _updateFleetSubtitle(data) {
    const el = document.getElementById('msTitleSub');
    if (!el) return;
    const fleet = data.fleet || {};
    const states = data.states || {};
    const parts = _cardOrder()
      .filter(s => (states[s]?.total_counties || 0) > 0)
      .map(s => `${s} ${states[s].total_counties}`);
    const total = fleet.total_registered
      || Object.values(states).reduce((n, s) => n + (s.total_counties || 0), 0);
    const active = fleet.active != null ? fleet.active : '—';
    const arrests = fleet.total_arrests != null
      ? Number(fleet.total_arrests).toLocaleString()
      : '—';
    el.innerHTML = `<strong>${total}</strong> registered scrapers`
      + ` · <span style="color:var(--success,#00d4aa)">${active} active</span>`
      + ` · ${arrests} arrests in DB`
      + (parts.length ? ` · ${parts.join(' · ')}` : '');
  }

  function _renderStateCards(states) {
    const container = document.getElementById('msStateCards');
    if (!container) return;

    container.innerHTML = _cardOrder().map(s => {
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
              <div class="ms-state-name">${STATE_NAMES[s] || s}${s === 'OH' ? ' <span class="ms-muted">guarded</span>' : ''}</div>
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
            <button type="button" class="ms-metric" title="Open these defendants and start paperwork" onclick="event.stopPropagation(); SLMultiState.openPeople('${s}','24h')">
              <div class="ms-metric-val">${_fmtCount(d.arrests_24h)}</div>
              <div class="ms-metric-lbl">24h Arrests</div>
            </button>
            <button type="button" class="ms-metric" title="Open these defendants and start paperwork" onclick="event.stopPropagation(); SLMultiState.openPeople('${s}','7d')">
              <div class="ms-metric-val">${_fmtCount(d.arrests_7d)}</div>
              <div class="ms-metric-lbl">7d Arrests</div>
            </button>
            <button type="button" class="ms-metric" title="Open these defendants and start paperwork" onclick="event.stopPropagation(); SLMultiState.openPeople('${s}','all')">
              <div class="ms-metric-val">${_fmtCount(d.total_arrests)}</div>
              <div class="ms-metric-lbl">Total</div>
            </button>
          </div>
          <div class="ms-state-card-footer">
            <button type="button" class="ms-state-card-footer-stat" onclick="event.stopPropagation(); SLMultiState.openPeople('${s}','hot')">🔥 <strong style="color:#ef4444">${_fmtCount(d.hot_leads)}</strong> hot</button>
            <button type="button" class="ms-state-card-footer-stat" onclick="event.stopPropagation(); SLMultiState.openPeople('${s}','warm')">🟡 <strong style="color:#f59e0b">${_fmtCount(d.warm_leads)}</strong> warm</button>
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
    // Prefer fixed STATE_ORDER so chart order is stable across refreshes
    const labels = _cardOrder().filter(s => (states[s]?.total_counties || 0) > 0);
    Object.keys(states || {}).forEach(s => {
      if (!labels.includes(s) && (states[s]?.total_counties || 0) > 0) labels.push(s);
    });
    const values = labels.map(s => states[s]?.total_counties || 0);
    if (!values.length || values.every(v => !v)) {
      el.innerHTML = '<div class="ms-empty" style="padding:40px;text-align:center">No registry data</div>';
      return;
    }
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
    if (_arrestsChart) { _arrestsChart.destroy(); _arrestsChart = null; }
    const order = _cardOrder();
    const series = order.map(s => ({
      name: STATE_NAMES[s] || s,
      data: [states[s]?.arrests_7d || 0],
    }));
    _arrestsChart = new ApexCharts(el, {
      chart: { type: 'bar', background: 'transparent', height: 220, toolbar: { show: false } },
      series,
      xaxis: { categories: ['Last 7 Days'], labels: { style: { colors: '#94a3b8' } } },
      yaxis: { labels: { style: { colors: '#94a3b8' } } },
      colors: order.map(s => STATE_COLORS[s] || '#64748b'),
      plotOptions: { bar: { columnWidth: '50%', borderRadius: 4 } },
      legend: { labels: { colors: '#94a3b8' } },
      theme: { mode: 'dark' },
      grid: { borderColor: '#1e293b' },
    });
    _arrestsChart.render();
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
      const top = (data.platforms || []).slice(0, 10);
      if (!top.length) return;
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
      const res = await fetch('/api/ops/scraper-registry', { credentials: 'same-origin', cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      _registryData = data.scrapers || [];
      _renderRegistry();
      _loadPlatformChart();
      const countEl = document.getElementById('msRegistryCount');
      if (countEl) {
        const byState = data.by_state || {};
        const stateBits = Object.keys(byState).sort().map(s => `${s}:${byState[s]}`).join(' ');
        countEl.textContent = `${_registryData.length} scrapers${stateBits ? ` (${stateBits})` : ''}`;
        countEl.title = stateBits || '';
      }
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
      const res = await fetch('/api/ops/live-feed?limit=60', { credentials: 'same-origin', cache: 'no-store' });
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
      const state = (a.state || '??').toString().toUpperCase();
      const color = STATE_COLORS[state] || '#64748b';
      const bondVal = a.bond_amount ?? a.bail_amount ?? 0;
      const bail = Number(bondVal) > 0
        ? `$${Number(bondVal).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
        : 'No Bail';
      const rawCharge = String(a.charges || a.Charges || '');
      const charge = rawCharge
        ? (rawCharge.length > 45 ? rawCharge.substring(0, 45) + '…' : rawCharge)
        : 'Unknown Charge';
      const time = _fmtRelative(a.scraped_at || a.created_at);
      const name = a.full_name || a.Full_Name || 'Unknown';
      const county = a.county || a.County || '?';
      const score = Number(a.lead_score) || 0;
      const scoreBadge = score >= 70
        ? `<span style="color:#ef4444;font-size:10px;font-weight:700">🔥${score}</span>`
        : (score >= 40 ? `<span style="color:#f59e0b;font-size:10px">${score}</span>` : '');
      const bk = String(a.booking_number || '');
      const write = bk
        ? `<button type="button" class="ms-action-btn ms-write-btn" data-bk="${_esc(bk)}" data-county="${_esc(county)}" data-state="${_esc(state)}" data-name="${_esc(name)}" onclick="SLMultiState.writeDefendant(this.getAttribute('data-bk'), this.getAttribute('data-county'), this.getAttribute('data-state'), this.getAttribute('data-name'))">☘️ Write / Print</button>`
        : `<span class="ms-muted">No booking #</span>`;
      return `
        <div class="ms-feed-item">
          <div class="ms-feed-state-dot" style="background:${color}" title="${_esc(state)}"></div>
          <div class="ms-feed-content">
            <div class="ms-feed-name">${_esc(name)} ${scoreBadge}</div>
            <div class="ms-feed-meta">
              <span class="ms-feed-county" style="color:${color}">${_esc(county)}, ${_esc(state)}</span>
              <span class="ms-feed-charge">${_esc(charge)}</span>
            </div>
          </div>
          <div class="ms-feed-right">
            <div class="ms-feed-bail">${bail}</div>
            <div class="ms-feed-time">${time}</div>
            ${write}
          </div>
        </div>
      `;
    }).join('');
  }

  // ─── ACTIONS ──────────────────────────────────────────────────────────────
  async function _parseJsonRes(res) {
    const text = await res.text();
    if (!text) return { ok: false, error: `Empty response (HTTP ${res.status})` };
    try {
      return JSON.parse(text);
    } catch (_) {
      return {
        ok: false,
        error: `Server returned non-JSON (HTTP ${res.status}): ${text.replace(/\s+/g, ' ').slice(0, 120)}`,
      };
    }
  }

  async function runCounty(county, state) {
    try {
      const res = await fetch('/api/scraper/run-now', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ county, state: state || undefined }),
      });
      const data = await _parseJsonRes(res);
      if (data.ok) {
        const label = state ? `${county} (${state})` : county;
        _showToast(`▶ Run queued for ${data.county || label}`, 'success');
      } else {
        _showToast(`Error: ${data.error || 'Unknown'}`, 'error');
      }
    } catch (e) {
      _showToast(`Failed to trigger ${county}: ${e.message}`, 'error');
    }
  }

  async function runAll() {
    if (!confirm('Trigger an immediate run for ALL registered scrapers, including the guarded Ohio pilot? This will put significant load on the server.')) return;
    try {
      const res = await fetch('/api/scraper/run-all', { method: 'POST', credentials: 'same-origin' });
      const data = await _parseJsonRes(res);
      if (data.ok) {
        _showToast(`▶ Run queued for all ${data.triggered} scrapers`, 'success');
      } else {
        _showToast(`Error: ${data.error || 'Run-all failed'}`, 'error');
      }
    } catch (e) {
      _showToast(`Failed to trigger run-all: ${e.message}`, 'error');
    }
  }

  function viewCountyArrests(county, state) {
    // Switch to Lead Explorer, select the county, trigger catch-up scrape, newest first
    const label = state ? `${county} (${state})` : county;
    const leadsBtn = document.querySelector('[data-tab="tabLeads"]');
    if (leadsBtn) leadsBtn.click();

    // Queue catch-up immediately so scraper fills anything behind
    if (typeof triggerCountyScraperAuto === 'function') {
      triggerCountyScraperAuto(label, { force: true });
    } else {
      runCounty(county, state);
    }

    setTimeout(() => {
      const stateSel = document.getElementById('stateFilter');
      if (stateSel && state) stateSel.value = state;

      // Prefer multi-select county filter over free-text search
      if (window.SL_STATE) {
        SL_STATE.selectedCounties = [label];
        SL_STATE.sort = 'scraped_at';
        SL_STATE.order = 'desc';
        SL_STATE.page = 1;
        if (typeof buildCountyOptions === 'function') {
          buildCountyOptions(SL_STATE.counties || []);
        } else if (typeof updateCountyLabel === 'function') {
          updateCountyLabel();
        }
      }

      if (typeof applyFilters === 'function') applyFilters();
      else if (window.SL && typeof window.SL.applyFilters === 'function') window.SL.applyFilters();
    }, 200);
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

  function writeDefendant(bk, county, state, name) {
    if (!bk) { _showToast('This arrest has no booking number', 'error'); return; }
    window._leadMap = window._leadMap || {};
    const prior = window._leadMap[bk] || {};
    window._leadMap[bk] = Object.assign({}, prior, {
      booking_number: bk,
      county: county || prior.county || '',
      state: state || prior.state || '',
      full_name: name || prior.full_name || '',
    });
    if (typeof openDefendantWritePrint === 'function') openDefendantWritePrint(bk);
    else _showToast('Write / Print is not loaded', 'error');
  }

  function openPeople(state, preset) {
    const name = STATE_NAMES[state] || state;
    const labels = {
      '24h': 'last 24 hours',
      '7d': 'last 7 days',
      all: 'all arrests',
      hot: 'hot leads',
      warm: 'warm leads',
    };
    if (window.SLIntel) {
      SLIntel.open({ preset, state, title: `${name} · ${labels[preset] || preset}` });
    }
  }

  return {
    init, refresh, runCounty, runAll, viewCountyArrests,
    setStateFilter, setStatusFilter, setSearch, openPeople, writeDefendant,
  };
})();

// Defendant list behind a count. Every row starts the Write / Print desk.
const SLIntel = (() => {
  let _opts = { preset: 'all', title: 'Defendants' };
  let _page = 1;
  let _query = '';
  let _searchTimer = null;

  function _ensure() {
    if (document.getElementById('slIntelDrawer')) return;
    const root = document.createElement('div');
    root.id = 'slIntelDrawer';
    root.className = 'sl-intel-drawer';
    root.hidden = true;
    root.innerHTML = `
      <div class="sl-intel-backdrop" onclick="SLIntel.close()"></div>
      <aside class="sl-intel-panel" role="dialog" aria-labelledby="slIntelTitle">
        <header class="sl-intel-head">
          <div>
            <h3 id="slIntelTitle">Defendants</h3>
            <p id="slIntelSub">Each person opens Write / Print.</p>
          </div>
          <button type="button" class="ms-action-btn" onclick="SLIntel.close()">Close</button>
        </header>
        <input id="slIntelSearch" class="ms-search" type="search" placeholder="Search name or booking number" oninput="SLIntel.search(this.value)">
        <div id="slIntelList" class="sl-intel-list"></div>
        <div id="slIntelPager" class="sl-intel-pager"></div>
      </aside>`;
    document.body.appendChild(root);
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') close();
    });
  }

  function open(opts) {
    _ensure();
    _opts = Object.assign({ preset: 'all', title: 'Defendants' }, opts || {});
    _page = 1;
    _query = '';
    const search = document.getElementById('slIntelSearch');
    if (search) search.value = '';
    const title = document.getElementById('slIntelTitle');
    if (title) title.textContent = _opts.title || 'Defendants';
    const root = document.getElementById('slIntelDrawer');
    if (root) root.hidden = false;
    load();
  }

  function openFromAttrs(el) {
    if (!el) return;
    open({
      preset: el.dataset.preset || 'all',
      state: el.dataset.state || '',
      county: el.dataset.county || '',
      days: el.dataset.days ? Number(el.dataset.days) : undefined,
      title: el.dataset.title || 'Defendants',
    });
  }

  function close() {
    const root = document.getElementById('slIntelDrawer');
    if (root) root.hidden = true;
  }

  function search(value) {
    _query = value || '';
    _page = 1;
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(load, 250);
  }

  function go(page) {
    _page = page;
    load();
  }

  async function load() {
    const list = document.getElementById('slIntelList');
    const sub = document.getElementById('slIntelSub');
    if (list) list.innerHTML = '<div class="ms-loading">Loading defendants…</div>';
    const params = new URLSearchParams({
      preset: _opts.preset || 'all',
      page: String(_page),
      limit: '40',
    });
    if (_opts.state) params.set('state', _opts.state);
    if (_opts.county) params.set('county', _opts.county);
    if (_opts.days) params.set('days', String(_opts.days));
    if (_query.trim()) params.set('q', _query.trim());
    try {
      const res = await fetch(`/api/ops/defendants?${params}`, { credentials: 'same-origin', cache: 'no-store' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const total = Number(data.total) || 0;
      if (sub) {
        sub.textContent = `${total.toLocaleString()} defendant${total === 1 ? '' : 's'}. Write / Print starts paperwork for that person.`;
      }
      const rows = data.defendants || [];
      if (!list) return;
      if (!rows.length) {
        list.innerHTML = '<div class="ms-empty">No defendants in this count.</div>';
      } else {
        list.innerHTML = rows.map(a => {
          const bk = String(a.booking_number || '');
          const bond = Number(a.bond_amount) || 0;
          const bail = bond > 0 ? `$${bond.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : 'No bail';
          const write = bk
            ? `<button type="button" class="ms-action-btn ms-write-btn" data-bk="${_escAttr(bk)}" data-county="${_escAttr(a.county)}" data-state="${_escAttr(a.state)}" data-name="${_escAttr(a.full_name)}" onclick="SLMultiState.writeDefendant(this.getAttribute('data-bk'), this.getAttribute('data-county'), this.getAttribute('data-state'), this.getAttribute('data-name'))">☘️ Write / Print</button>`
            : '<span class="ms-muted">No booking #</span>';
          return `<article class="sl-intel-row">
            <div>
              <div class="ms-feed-name">${_escAttr(a.full_name)} <span class="ms-muted">${a.lead_score || 0}</span></div>
              <div class="ms-feed-meta"><span>${_escAttr(a.county)}${a.state ? ', ' + _escAttr(a.state) : ''}</span><span class="ms-feed-charge">${_escAttr(a.charges || '')}</span></div>
            </div>
            <div class="ms-feed-right">
              <div class="ms-feed-bail">${bail}</div>
              ${write}
            </div>
          </article>`;
        }).join('');
      }
      const pager = document.getElementById('slIntelPager');
      if (pager) {
        const pages = Number(data.pages) || 1;
        pager.innerHTML = pages > 1
          ? `<button type="button" class="ms-action-btn" ${data.page <= 1 ? 'disabled' : ''} onclick="SLIntel.go(${(data.page || 1) - 1})">Prev</button>
             <span class="ms-muted">${data.page || 1} / ${pages}</span>
             <button type="button" class="ms-action-btn" ${data.page >= pages ? 'disabled' : ''} onclick="SLIntel.go(${(data.page || 1) + 1})">Next</button>`
          : '';
      }
    } catch (err) {
      if (list) list.innerHTML = `<div class="ms-empty">Could not load defendants. ${_escAttr(err.message)}</div>`;
    }
  }

  function _escAttr(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  return { open, openFromAttrs, close, search, go };
})();
window.SLIntel = SLIntel;
