/**
 * SLHealth — Scraper Health Tab Module
 * Replaces the old renderHealth() in sl-features.js.
 *
 * Features:
 *  - Shows ALL 49 registered counties (never_run, error, ok, stale)
 *  - Filter by status + text search
 *  - Sortable columns
 *  - Run Now / Run All buttons (via /api/scraper/run-now)
 *  - Error detail drill-down panel
 *  - KPI cards: total registered, active, errors, never run
 *  - Auto-refreshes every 60s when tab is visible
 */

const SLHealth = (() => {
  // ── State ──────────────────────────────────────────────────────────────────
  let _data = [];           // Raw array from /api/scraper-health
  let _filter = 'all';      // Current status filter
  let _search = '';         // Current text search
  let _sortKey = 'status';  // Current sort column
  let _sortAsc = true;      // Sort direction
  let _refreshTimer = null;
  let _initialized = false;

  // ── Status helpers ─────────────────────────────────────────────────────────
  const STATUS_CONFIG = {
    healthy:   { label: '🟢 Healthy',   cls: 'status-ok',      order: 0 },
    ok:        { label: '🟢 Active',    cls: 'status-ok',      order: 1 },
    stale:     { label: '🟡 Stale',     cls: 'status-warn',    order: 2 },
    warning:   { label: '🟡 Warning',   cls: 'status-warn',    order: 3 },
    offline:   { label: '🔴 Offline',   cls: 'status-error',   order: 4 },
    error:     { label: '🔴 Error',     cls: 'status-error',   order: 5 },
    never_run: { label: '⏳ Never Run', cls: 'status-never',   order: 6 },
  };

  function _statusCfg(s) {
    return STATUS_CONFIG[s] || { label: s, cls: 'status-never', order: 9 };
  }

  function _fmtDuration(secs) {
    if (!secs) return '—';
    if (secs < 60) return `${Math.round(secs)}s`;
    return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
  }

  function _fmtRelative(isoStr) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    if (isNaN(d)) return '—';
    const mins = Math.round((Date.now() - d) / 60000);
    if (mins < 2)   return 'just now';
    if (mins < 60)  return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24)   return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  }

  function _fmtNum(n) {
    if (n == null || n === 0) return '—';
    return n.toLocaleString();
  }

  // ── Data loading ───────────────────────────────────────────────────────────
  async function load() {
    try {
      const res = await fetch('/api/scraper-health');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      _data = await res.json();
      _render();
      _updateLastRefresh();
      if (!_initialized) {
        _initialized = true;
        _startAutoRefresh();
      }
    } catch (err) {
      console.error('[SLHealth] load error:', err);
      const body = document.getElementById('healthBody');
      if (body) body.innerHTML = `<tr><td colspan="8" style="color:var(--danger);text-align:center">Failed to load scraper data: ${err.message}</td></tr>`;
    }
  }

  function refresh() {
    load();
  }

  function _startAutoRefresh() {
    if (_refreshTimer) clearInterval(_refreshTimer);
    _refreshTimer = setInterval(() => {
      // Only refresh if the health tab is visible
      const tab = document.getElementById('tabHealth');
      if (tab && tab.classList.contains('active')) {
        load();
      }
    }, 60000);
  }

  function _updateLastRefresh() {
    const el = document.getElementById('healthLastRefresh');
    if (el) el.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  }

  // ── Filtering & Sorting ────────────────────────────────────────────────────
  function setFilter(filter, btn) {
    _filter = filter;
    // Update button active states
    document.querySelectorAll('.health-filter-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    _render();
  }

  function search(val) {
    _search = val.toLowerCase().trim();
    _render();
  }

  function sortBy(key) {
    if (_sortKey === key) {
      _sortAsc = !_sortAsc;
    } else {
      _sortKey = key;
      _sortAsc = key !== 'total_records'; // Default desc for numbers
    }
    _render();
  }

  function _getFiltered() {
    let rows = [..._data];

    // Status filter
    if (_filter !== 'all') {
      if (_filter === 'ok') {
        rows = rows.filter(r => r.status === 'ok' || r.status === 'healthy');
      } else if (_filter === 'stale') {
        rows = rows.filter(r => r.status === 'stale' || r.status === 'warning' || r.status === 'offline');
      } else {
        rows = rows.filter(r => r.status === _filter);
      }
    }

    // Text search
    if (_search) {
      rows = rows.filter(r => (r.county || '').toLowerCase().includes(_search));
    }

    // Sort
    rows.sort((a, b) => {
      let av = a[_sortKey];
      let bv = b[_sortKey];
      if (_sortKey === 'status') {
        av = _statusCfg(av).order;
        bv = _statusCfg(bv).order;
      } else if (_sortKey === 'last_run') {
        av = av ? new Date(av).getTime() : 0;
        bv = bv ? new Date(bv).getTime() : 0;
      } else if (typeof av === 'string') {
        av = av.toLowerCase();
        bv = (bv || '').toLowerCase();
      }
      if (av < bv) return _sortAsc ? -1 : 1;
      if (av > bv) return _sortAsc ? 1 : -1;
      return 0;
    });

    return rows;
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  function _render() {
    _renderKpis();
    _renderTable();
  }

  function _renderKpis() {
    const kpiEl = document.getElementById('healthKpis');
    if (!kpiEl) return;

    const total = _data.length;
    const active = _data.filter(r => r.status === 'ok' || r.status === 'healthy').length;
    const errors = _data.filter(r => r.status === 'error').length;
    const neverRun = _data.filter(r => r.status === 'never_run').length;
    const stale = _data.filter(r => ['stale', 'warning', 'offline'].includes(r.status)).length;
    const totalRecords = _data.reduce((s, r) => s + (r.total_records || 0), 0);

    kpiEl.innerHTML = `
      <div class="stat-card" onclick="SLHealth.setFilter('all', document.querySelector('[data-filter=all]'))">
        <div class="stat-label">Total Registered</div>
        <div class="stat-value">${total}</div>
        <div class="stat-sub">counties in fleet</div>
      </div>
      <div class="stat-card" style="border-color:var(--success,#00c896)" onclick="SLHealth.setFilter('ok', document.querySelector('[data-filter=ok]'))">
        <div class="stat-label">🟢 Active</div>
        <div class="stat-value" style="color:var(--success,#00c896)">${active}</div>
        <div class="stat-sub">running successfully</div>
      </div>
      <div class="stat-card" style="border-color:var(--danger,#ff4757)" onclick="SLHealth.setFilter('error', document.querySelector('[data-filter=error]'))">
        <div class="stat-label">🔴 Errors</div>
        <div class="stat-value" style="color:var(--danger,#ff4757)">${errors}</div>
        <div class="stat-sub">need attention</div>
      </div>
      <div class="stat-card" style="border-color:var(--warning,#ffa502)" onclick="SLHealth.setFilter('never_run', document.querySelector('[data-filter=never_run]'))">
        <div class="stat-label">⏳ Never Run</div>
        <div class="stat-value" style="color:var(--warning,#ffa502)">${neverRun}</div>
        <div class="stat-sub">not yet triggered</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">📊 Total Records</div>
        <div class="stat-value">${totalRecords.toLocaleString()}</div>
        <div class="stat-sub">across all counties</div>
      </div>
    `;
  }

  function _renderTable() {
    const body = document.getElementById('healthBody');
    const label = document.getElementById('healthCountLabel');
    if (!body) return;

    const rows = _getFiltered();
    if (label) label.textContent = `${rows.length} of ${_data.length} counties`;

    if (rows.length === 0) {
      body.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:32px">No counties match the current filter.</td></tr>`;
      return;
    }

    body.innerHTML = rows.map(r => {
      const cfg = _statusCfg(r.status);
      const lastRun = _fmtRelative(r.last_run || r.latest_record);
      const hasError = r.status === 'error' && r.error;
      const isNeverRun = r.status === 'never_run';

      return `
        <tr class="${hasError ? 'row-error' : ''}">
          <td style="font-weight:600">${r.county || '—'}</td>
          <td>
            <span class="status-badge ${cfg.cls}">${cfg.label}</span>
            ${hasError ? `<div style="font-size:11px;color:var(--danger);margin-top:3px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${r.error}">${r.error}</div>` : ''}
          </td>
          <td>${_fmtNum(r.total_records)}</td>
          <td>${_fmtNum(r.hot_leads)}</td>
          <td style="color:${isNeverRun ? 'var(--text-muted)' : 'inherit'}">${lastRun}</td>
          <td>${_fmtDuration(r.duration_seconds)}</td>
          <td>${r.run_count || '—'}</td>
          <td>
            <div style="display:flex;gap:6px;flex-wrap:wrap">
              <button class="btn btn-xs" onclick="SLHealth.runNow('${r.county}')" style="background:var(--accent);color:#000;font-weight:600;padding:3px 8px;font-size:11px" title="Trigger immediate run">⚡ Run</button>
              ${hasError ? `<button class="btn btn-xs" onclick="SLHealth.showError('${r.county}')" style="background:var(--danger);color:#fff;padding:3px 8px;font-size:11px">🔍 Error</button>` : ''}
              <button class="btn btn-xs" onclick="SLHealth.showDrill('${r.county}')" style="background:var(--panel-bg);border:1px solid var(--border);padding:3px 8px;font-size:11px">📊 Detail</button>
            </div>
          </td>
        </tr>
      `;
    }).join('');
  }

  // ── Actions ────────────────────────────────────────────────────────────────
  async function runNow(county) {
    const btn = event && event.target;
    if (btn) { btn.textContent = '⏳'; btn.disabled = true; }

    try {
      const res = await fetch('/api/scraper/run-now', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ county }),
      });
      const data = await res.json();
      if (data.ok) {
        _showToast(`⚡ Run triggered for ${county}. Will execute within 60s.`, 'success');
      } else {
        _showToast(`❌ ${data.error || 'Failed to trigger run'}`, 'error');
      }
    } catch (err) {
      _showToast(`❌ Network error: ${err.message}`, 'error');
    } finally {
      if (btn) { btn.textContent = '⚡ Run'; btn.disabled = false; }
      // Refresh after 5s to show updated status
      setTimeout(load, 5000);
    }
  }

  async function runAll() {
    if (!confirm(`Trigger immediate runs for ALL ${_data.length} registered scrapers?\n\nThis may take several minutes to complete.`)) return;

    try {
      const res = await fetch('/api/scraper/run-all', { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        _showToast(`⚡ Run-All triggered for ${data.triggered} counties. Executing over next ~5 minutes.`, 'success');
      } else {
        _showToast(`❌ ${data.error || 'Failed'}`, 'error');
      }
    } catch (err) {
      _showToast(`❌ Network error: ${err.message}`, 'error');
    }
    setTimeout(load, 10000);
  }

  function showError(county) {
    const row = _data.find(r => r.county === county);
    if (!row) return;
    const panel = document.getElementById('countyDrillPanel');
    const title = document.getElementById('drillTitle');
    const content = document.getElementById('drillContent');
    if (!panel || !content) return;

    title.textContent = `🔴 Error Detail — ${county}`;
    content.innerHTML = `
      <div style="padding:12px 0">
        <div style="font-size:13px;color:var(--text-muted);margin-bottom:8px">Last run: ${_fmtRelative(row.last_run)}</div>
        <div style="background:var(--input-bg,#0d1117);border:1px solid var(--danger);border-radius:8px;padding:14px;font-family:monospace;font-size:13px;color:var(--danger);white-space:pre-wrap;word-break:break-all">${row.error || 'No error details available'}</div>
        <div style="margin-top:12px;display:flex;gap:8px">
          <button class="btn" onclick="SLHealth.runNow('${county}')" style="background:var(--accent);color:#000;font-weight:700">⚡ Retry Now</button>
          <button class="btn" onclick="document.getElementById('countyDrillPanel').style.display='none'" style="background:var(--panel-bg);border:1px solid var(--border)">✕ Close</button>
        </div>
      </div>
    `;
    panel.style.display = 'block';
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function showDrill(county) {
    const panel = document.getElementById('countyDrillPanel');
    const title = document.getElementById('drillTitle');
    const content = document.getElementById('drillContent');
    if (!panel || !content) return;

    title.textContent = `📊 Loading ${county}...`;
    content.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">Loading...</div>';
    panel.style.display = 'block';
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    try {
      const res = await fetch(`/api/counties?county=${encodeURIComponent(county)}`);
      const row = _data.find(r => r.county === county) || {};
      const cfg = _statusCfg(row.status || 'never_run');

      title.textContent = `📊 ${county} County — ${cfg.label}`;
      content.innerHTML = `
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;padding:12px 0">
          <div class="stat-card" style="padding:12px">
            <div class="stat-label" style="font-size:11px">Total Records</div>
            <div class="stat-value" style="font-size:22px">${(row.total_records || 0).toLocaleString()}</div>
          </div>
          <div class="stat-card" style="padding:12px">
            <div class="stat-label" style="font-size:11px">In Custody</div>
            <div class="stat-value" style="font-size:22px">${(row.in_custody || 0).toLocaleString()}</div>
          </div>
          <div class="stat-card" style="padding:12px">
            <div class="stat-label" style="font-size:11px">Records (24h)</div>
            <div class="stat-value" style="font-size:22px">${(row.records_24h || 0).toLocaleString()}</div>
          </div>
          <div class="stat-card" style="padding:12px">
            <div class="stat-label" style="font-size:11px">Hot Leads</div>
            <div class="stat-value" style="font-size:22px;color:var(--danger)">${(row.hot_leads || 0).toLocaleString()}</div>
          </div>
          <div class="stat-card" style="padding:12px">
            <div class="stat-label" style="font-size:11px">Avg Bond</div>
            <div class="stat-value" style="font-size:22px">$${((row.avg_bond || 0) / 1000).toFixed(1)}K</div>
          </div>
          <div class="stat-card" style="padding:12px">
            <div class="stat-label" style="font-size:11px">Max Bond</div>
            <div class="stat-value" style="font-size:22px">$${((row.max_bond || 0) / 1000).toFixed(1)}K</div>
          </div>
          <div class="stat-card" style="padding:12px">
            <div class="stat-label" style="font-size:11px">Total Runs</div>
            <div class="stat-value" style="font-size:22px">${row.run_count || '—'}</div>
          </div>
          <div class="stat-card" style="padding:12px">
            <div class="stat-label" style="font-size:11px">Avg Duration</div>
            <div class="stat-value" style="font-size:22px">${_fmtDuration(row.duration_seconds)}</div>
          </div>
        </div>
        <div style="margin-top:8px;display:flex;gap:8px;align-items:center">
          <button class="btn" onclick="SLHealth.runNow('${county}')" style="background:var(--accent);color:#000;font-weight:700;padding:6px 14px">⚡ Run Now</button>
          <button class="btn" onclick="SL.applyFilters && (document.getElementById('countyFilter') ? (document.getElementById('countyFilter').value='${county}', SL.applyFilters()) : null); SL.switchTab(document.querySelector('[data-tab=tabLeads]'))" style="background:var(--panel-bg);border:1px solid var(--border);padding:6px 14px">🔍 View Leads</button>
          <button class="btn" onclick="document.getElementById('countyDrillPanel').style.display='none'" style="background:var(--panel-bg);border:1px solid var(--border);padding:6px 14px">✕ Close</button>
        </div>
        ${row.error ? `<div style="margin-top:12px;background:var(--input-bg,#0d1117);border:1px solid var(--danger);border-radius:8px;padding:12px;font-family:monospace;font-size:12px;color:var(--danger)">Last error: ${row.error}</div>` : ''}
      `;
    } catch (err) {
      content.innerHTML = `<div style="color:var(--danger);padding:12px">Failed to load detail: ${err.message}</div>`;
    }
  }

  // ── Toast notifications ────────────────────────────────────────────────────
  function _showToast(msg, type = 'info') {
    const id = 'sl-health-toast-' + Date.now();
    const colors = { success: '#00c896', error: '#ff4757', info: '#1e90ff' };
    const color = colors[type] || colors.info;

    const el = document.createElement('div');
    el.id = id;
    el.style.cssText = `
      position:fixed;bottom:24px;right:24px;z-index:9999;
      background:#1a1a2e;border:1px solid ${color};border-radius:10px;
      padding:12px 18px;color:#fff;font-size:14px;max-width:380px;
      box-shadow:0 4px 20px rgba(0,0,0,.4);
      animation:fadeIn .2s ease;
    `;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  return { load, refresh, setFilter, search, sortBy, runNow, runAll, showError, showDrill };
})();

// ── Override the old renderHealth() in sl-features.js ──────────────────────
// sl-core.js calls renderHealth() when the health tab is clicked.
// We replace it here so SLHealth.load() is called instead.
window.renderHealth = function() {
  SLHealth.load();
};
