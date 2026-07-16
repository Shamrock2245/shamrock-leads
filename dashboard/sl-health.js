/**
 * SLHealth — Scraper Health Tab Module
 * Features: Run Now, Run All, Enable/Disable, Health Check, View Logs, Error Detail, Drill-Down
 */
const SLHealth = (() => {
  let _data = [];
  let _filter = 'all';
  let _search = '';
  let _sortKey = 'status';
  let _sortAsc = true;
  let _refreshTimer = null;
  let _initialized = false;

  const STATUS_CONFIG = {
    healthy:   { label: '🟢 Healthy',   cls: 'status-ok',    order: 0 },
    ok:        { label: '🟢 Active',    cls: 'status-ok',    order: 1 },
    stale:     { label: '🟡 Stale',     cls: 'status-warn',  order: 2 },
    warning:   { label: '🟡 Warning',   cls: 'status-warn',  order: 3 },
    offline:   { label: '🔴 Offline',   cls: 'status-error', order: 4 },
    error:     { label: '🔴 Error',     cls: 'status-error', order: 5 },
    never_run: { label: '⏳ Never Run', cls: 'status-never', order: 6 },
    disabled:  { label: '⏸ Disabled',  cls: 'status-never', order: 7 },
  };

  function _statusCfg(s) { return STATUS_CONFIG[s] || { label: s, cls: 'status-never', order: 9 }; }
  function _fmtDuration(secs) {
    if (!secs) return '—';
    if (secs < 60) return `${Math.round(secs)}s`;
    return `${Math.floor(secs/60)}m ${Math.round(secs%60)}s`;
  }
  function _fmtRelative(isoStr) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    if (isNaN(d)) return '—';
    const mins = Math.round((Date.now() - d) / 60000);
    if (mins < 2) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs/24)}d ago`;
  }
  function _fmtNum(n) { return (n == null || n === 0) ? '—' : n.toLocaleString(); }

  async function load() {
    try {
      const res = await fetch('/api/scraper-health');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      _data = await res.json();
      _render();
      _updateLastRefresh();
      if (!_initialized) { _initialized = true; _startAutoRefresh(); }
    } catch (err) {
      console.error('[SLHealth] load error:', err);
      const body = document.getElementById('healthBody');
      if (body) body.innerHTML = `<tr><td colspan="9" style="color:var(--danger);text-align:center;padding:24px">Failed to load scraper data: ${err.message}</td></tr>`;
    }
  }

  function refresh() { load(); }

  function _startAutoRefresh() {
    if (_refreshTimer) clearInterval(_refreshTimer);
    _refreshTimer = setInterval(() => {
      const tab = document.getElementById('tabHealth');
      if (tab && tab.classList.contains('active')) load();
    }, 60000);
  }

  function _updateLastRefresh() {
    const el = document.getElementById('healthLastRefresh');
    if (el) el.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  }

  function setFilter(filter, btn) {
    _filter = filter;
    document.querySelectorAll('.health-filter-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    _render();
  }

  function search(val) { _search = val.toLowerCase().trim(); _render(); }

  function sortBy(key) {
    if (_sortKey === key) { _sortAsc = !_sortAsc; }
    else { _sortKey = key; _sortAsc = key !== 'total_records'; }
    _render();
  }

  function _getFiltered() {
    let rows = [..._data];
    if (_filter !== 'all') {
      if (_filter === 'ok') rows = rows.filter(r => r.status === 'ok' || r.status === 'healthy');
      else if (_filter === 'stale') rows = rows.filter(r => ['stale','warning','offline'].includes(r.status));
      else rows = rows.filter(r => r.status === _filter);
    }
    if (_search) rows = rows.filter(r => (r.county||'').toLowerCase().includes(_search));
    rows.sort((a, b) => {
      let av = a[_sortKey], bv = b[_sortKey];
      if (_sortKey === 'status') { av = _statusCfg(av).order; bv = _statusCfg(bv).order; }
      else if (_sortKey === 'last_run') { av = av ? new Date(av).getTime() : 0; bv = bv ? new Date(bv).getTime() : 0; }
      else if (typeof av === 'string') { av = av.toLowerCase(); bv = (bv||'').toLowerCase(); }
      if (av < bv) return _sortAsc ? -1 : 1;
      if (av > bv) return _sortAsc ? 1 : -1;
      return 0;
    });
    return rows;
  }

  function _render() { _renderKpis(); _renderTable(); }

  function _renderKpis() {
    const kpiEl = document.getElementById('healthKpis');
    if (!kpiEl) return;
    const total = _data.length;
    const active = _data.filter(r => r.status === 'ok' || r.status === 'healthy').length;
    const errors = _data.filter(r => r.status === 'error').length;
    const neverRun = _data.filter(r => r.status === 'never_run').length;
    const stale = _data.filter(r => ['stale','warning','offline'].includes(r.status)).length;
    const disabled = _data.filter(r => r.status === 'disabled' || r.enabled === false).length;
    const totalRecords = _data.reduce((s,r) => s + (r.total_records||0), 0);
    const totalHot = _data.reduce((s,r) => s + (r.hot_leads||0), 0);
    kpiEl.innerHTML = `
      <div class="stat-card" onclick="SLHealth.setFilter('all',this)" style="cursor:pointer">
        <div class="stat-label">Total Registered</div>
        <div class="stat-value">${total}</div>
        <div class="stat-sub">counties in fleet</div>
      </div>
      <div class="stat-card" style="border-color:var(--success,#00c896);cursor:pointer" onclick="SLHealth.setFilter('ok',this)">
        <div class="stat-label">🟢 Active</div>
        <div class="stat-value" style="color:var(--success,#00c896)">${active}</div>
        <div class="stat-sub">running successfully</div>
      </div>
      <div class="stat-card" style="border-color:var(--danger,#ff4757);cursor:pointer" onclick="SLHealth.setFilter('error',this)">
        <div class="stat-label">🔴 Errors</div>
        <div class="stat-value" style="color:var(--danger,#ff4757)">${errors}</div>
        <div class="stat-sub">need attention</div>
      </div>
      <div class="stat-card" style="border-color:#ffa502;cursor:pointer" onclick="SLHealth.setFilter('stale',this)">
        <div class="stat-label">🟡 Stale</div>
        <div class="stat-value" style="color:#ffa502">${stale}</div>
        <div class="stat-sub">no recent data</div>
      </div>
      <div class="stat-card" style="border-color:#ffa502;cursor:pointer" onclick="SLHealth.setFilter('never_run',this)">
        <div class="stat-label">⏳ Never Run</div>
        <div class="stat-value" style="color:#ffa502">${neverRun}</div>
        <div class="stat-sub">not yet triggered</div>
      </div>
      ${disabled > 0 ? `<div class="stat-card" style="border-color:#6b7280;cursor:pointer" onclick="SLHealth.setFilter('disabled',this)">
        <div class="stat-label">⏸ Disabled</div>
        <div class="stat-value" style="color:#9ca3af">${disabled}</div>
        <div class="stat-sub">paused scrapers</div>
      </div>` : ''}
      <div class="stat-card">
        <div class="stat-label">📊 Total Records</div>
        <div class="stat-value">${totalRecords.toLocaleString()}</div>
        <div class="stat-sub">across all counties</div>
      </div>
      <div class="stat-card" style="border-color:var(--danger,#ff4757)">
        <div class="stat-label">🔥 Hot Leads</div>
        <div class="stat-value" style="color:var(--danger,#ff4757)">${totalHot.toLocaleString()}</div>
        <div class="stat-sub">score ≥ 70</div>
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
      body.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:32px">No counties match the current filter.</td></tr>`;
      return;
    }
    const STATE_COLORS_H = { FL: '#00d4aa', GA: '#f59e0b', SC: '#8b5cf6', NC: '#3b82f6' };
    body.innerHTML = rows.map(r => {
      const cfg = _statusCfg(r.status);
      const lastRun = _fmtRelative(r.last_run || r.latest_record);
      const hasError = r.status === 'error' && r.error;
      const isNeverRun = r.status === 'never_run';
      const isDisabled = r.enabled === false || r.status === 'disabled';
      const st = (r.state || 'FL').toUpperCase();
      const stColor = STATE_COLORS_H[st] || '#64748b';
      const statePill = `<span style="background:${stColor}22;color:${stColor};border:1px solid ${stColor}44;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:700;margin-left:5px">${st}</span>`;
      return `
        <tr class="${hasError ? 'row-error' : ''}" style="${isDisabled ? 'opacity:0.6' : ''}">
          <td>${r.county&&r.county!=='—'?`<span class="county-badge" data-county="${r.county}">${r.county}</span>${statePill}`:'—'}</td>
          <td>
            <span class="status-badge ${cfg.cls}">${cfg.label}</span>
            ${hasError ? `<div style="font-size:11px;color:var(--danger);margin-top:3px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${r.error}">${r.error}</div>` : ''}
          </td>
          <td>${_fmtNum(r.total_records)}</td>
          <td style="color:var(--danger)">${_fmtNum(r.hot_leads)}</td>
          <td style="color:${isNeverRun ? 'var(--text-muted)' : 'inherit'}">${lastRun}</td>
          <td>${_fmtDuration(r.duration_seconds)}</td>
          <td>${r.run_count || '—'}</td>
          <td>
            <div style="display:flex;gap:4px;flex-wrap:wrap">
              ${!isDisabled ? `<button class="btn btn-xs" onclick="SLHealth.runNow('${r.county}')" style="background:var(--accent);color:#000;font-weight:600;padding:3px 8px;font-size:11px" title="Trigger immediate run">⚡ Run</button>` : ''}
              ${hasError ? `<button class="btn btn-xs" onclick="SLHealth.showError('${r.county}')" style="background:var(--danger);color:#fff;padding:3px 8px;font-size:11px">🔍 Error</button>` : ''}
              <button class="btn btn-xs" onclick="SLHealth.showDrill('${r.county}')" style="background:var(--panel-bg);border:1px solid var(--border);padding:3px 8px;font-size:11px" title="View detailed stats">📊 Detail</button>
              <button class="btn btn-xs" onclick="SLHealth.healthCheck('${r.county}')" style="background:#1a3a5c;border:1px solid #2563eb;color:#93c5fd;padding:3px 8px;font-size:11px" title="Queue a URL health check">🩺 Check</button>
              <button class="btn btn-xs" onclick="SLHealth.viewLogs('${r.county}')" style="background:var(--panel-bg);border:1px solid var(--border);padding:3px 8px;font-size:11px" title="View recent run logs">📋 Logs</button>
              ${!isDisabled
                ? `<button class="btn btn-xs" onclick="SLHealth.disableScraper('${r.county}')" style="background:#3b1a1a;border:1px solid #7f1d1d;color:#fca5a5;padding:3px 8px;font-size:11px" title="Pause this scraper">⏸ Pause</button>`
                : `<button class="btn btn-xs" onclick="SLHealth.enableScraper('${r.county}')" style="background:#1a3b1a;border:1px solid #166534;color:#86efac;padding:3px 8px;font-size:11px" title="Re-enable this scraper">▶ Enable</button>`
              }
            </div>
          </td>
        </tr>`;
    }).join('');
  }

  async function runNow(county) {
    const btn = event && event.target;
    if (btn) { btn.textContent = '⏳'; btn.disabled = true; }
    try {
      const res = await fetch('/api/scraper/run-now', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({county}) });
      const data = await res.json();
      data.ok ? _showToast(`⚡ Run triggered for ${county}. Will execute within 60s.`, 'success') : _showToast(`❌ ${data.error || 'Failed to trigger run'}`, 'error');
    } catch (err) { _showToast(`❌ Network error: ${err.message}`, 'error'); }
    finally { if (btn) { btn.textContent = '⚡ Run'; btn.disabled = false; } setTimeout(load, 5000); }
  }

  async function runAll() {
    if (!confirm(`Trigger immediate runs for ALL ${_data.length} registered scrapers?\n\nThis may take several minutes to complete.`)) return;
    try {
      const res = await fetch('/api/scraper/run-all', { method:'POST' });
      const data = await res.json();
      data.ok ? _showToast(`⚡ Run-All triggered for ${data.triggered} counties.`, 'success') : _showToast(`❌ ${data.error || 'Failed'}`, 'error');
    } catch (err) { _showToast(`❌ Network error: ${err.message}`, 'error'); }
    setTimeout(load, 10000);
  }

  async function enableScraper(county) {
    try {
      const res = await fetch('/api/scraper/enable', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({county}) });
      const data = await res.json();
      if (data.ok) { _showToast(`▶ ${county} scraper enabled.`, 'success'); setTimeout(load, 1500); }
      else _showToast(`❌ ${data.error || 'Failed to enable'}`, 'error');
    } catch (err) { _showToast(`❌ Network error: ${err.message}`, 'error'); }
  }

  async function disableScraper(county) {
    if (!confirm(`Pause the ${county} scraper?\n\nIt will stop auto-running until you re-enable it.`)) return;
    try {
      const res = await fetch('/api/scraper/disable', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({county}) });
      const data = await res.json();
      if (data.ok) { _showToast(`⏸ ${county} scraper paused.`, 'info'); setTimeout(load, 1500); }
      else _showToast(`❌ ${data.error || 'Failed to disable'}`, 'error');
    } catch (err) { _showToast(`❌ Network error: ${err.message}`, 'error'); }
  }

  async function healthCheck(county) {
    _showToast(`🩺 Health check queued for ${county}...`, 'info');
    try {
      const res = await fetch('/api/scraper/health-check', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({county}) });
      const data = await res.json();
      data.ok ? _showToast(`🩺 Health check triggered for ${county}. Check back in ~60s.`, 'success') : _showToast(`❌ ${data.error || 'Health check failed'}`, 'error');
    } catch (err) { _showToast(`❌ Network error: ${err.message}`, 'error'); }
  }

  async function viewLogs(county) {
    const panel = document.getElementById('countyDrillPanel');
    const title = document.getElementById('drillTitle');
    const content = document.getElementById('drillContent');
    if (!panel || !content) return;
    title.textContent = `📋 Loading logs for ${county}...`;
    content.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">Loading logs...</div>';
    panel.style.display = 'block';
    panel.scrollIntoView({ behavior:'smooth', block:'nearest' });
    try {
      const res = await fetch(`/api/scraper/logs/${encodeURIComponent(county)}?limit=15`);
      const data = await res.json();
      const logs = data.logs || [];
      title.textContent = `📋 ${county} — Run Logs (${logs.length})`;
      if (!logs.length) {
        content.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-muted)">No run logs found for ${county} yet.</div>
          <div style="margin-top:12px;display:flex;gap:8px;justify-content:center">
            <button class="btn" onclick="SLHealth.showDrill('${county}')" style="background:var(--panel-bg);border:1px solid var(--border);padding:6px 14px">📊 Full Detail</button>
            <button class="btn" onclick="document.getElementById('countyDrillPanel').style.display='none'" style="background:var(--panel-bg);border:1px solid var(--border);padding:6px 14px">✕ Close</button>
          </div>`;
        return;
      }
      content.innerHTML = `
        <div style="overflow-x:auto">
          <table style="width:100%;border-collapse:collapse;font-size:12px">
            <thead>
              <tr style="border-bottom:1px solid var(--border);color:var(--text-muted)">
                <th style="text-align:left;padding:6px 8px">Time</th>
                <th style="text-align:left;padding:6px 8px">Status</th>
                <th style="text-align:right;padding:6px 8px">Records</th>
                <th style="text-align:right;padding:6px 8px">Duration</th>
                <th style="text-align:left;padding:6px 8px">Error</th>
              </tr>
            </thead>
            <tbody>
              ${logs.map(l => {
                const ts = l.started_at || l.last_run || '';
                const st = l.status || 'unknown';
                const sc = (st==='ok'||st==='healthy') ? 'var(--success,#00c896)' : st==='error' ? 'var(--danger,#ff4757)' : '#ffa502';
                return `<tr style="border-bottom:1px solid var(--border)">
                  <td style="padding:6px 8px;color:var(--text-muted)">${ts ? _fmtRelative(ts) : '—'}</td>
                  <td style="padding:6px 8px;color:${sc};font-weight:600">${st}</td>
                  <td style="padding:6px 8px;text-align:right">${(l.records||l.total_records||0).toLocaleString()}</td>
                  <td style="padding:6px 8px;text-align:right">${_fmtDuration(l.duration_seconds)}</td>
                  <td style="padding:6px 8px;color:var(--danger);font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${l.error||''}">${l.error||'—'}</td>
                </tr>`;
              }).join('')}
            </tbody>
          </table>
        </div>
        <div style="margin-top:12px;display:flex;gap:8px">
          <button class="btn" onclick="SLHealth.showDrill('${county}')" style="background:var(--panel-bg);border:1px solid var(--border);padding:6px 14px">📊 Full Detail</button>
          <button class="btn" onclick="document.getElementById('countyDrillPanel').style.display='none'" style="background:var(--panel-bg);border:1px solid var(--border);padding:6px 14px">✕ Close</button>
        </div>`;
    } catch (err) {
      content.innerHTML = `<div style="color:var(--danger);padding:12px">Failed to load logs: ${err.message}</div>`;
    }
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
        <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn" onclick="SLHealth.runNow('${county}')" style="background:var(--accent);color:#000;font-weight:700">⚡ Retry Now</button>
          <button class="btn" onclick="SLHealth.healthCheck('${county}')" style="background:#1a3a5c;border:1px solid #2563eb;color:#93c5fd">🩺 Health Check</button>
          <button class="btn" onclick="SLHealth.viewLogs('${county}')" style="background:var(--panel-bg);border:1px solid var(--border)">📋 View Logs</button>
          <button class="btn" onclick="document.getElementById('countyDrillPanel').style.display='none'" style="background:var(--panel-bg);border:1px solid var(--border)">✕ Close</button>
        </div>
      </div>`;
    panel.style.display = 'block';
    panel.scrollIntoView({ behavior:'smooth', block:'nearest' });
  }

  async function showDrill(county) {
    const panel = document.getElementById('countyDrillPanel');
    const title = document.getElementById('drillTitle');
    const content = document.getElementById('drillContent');
    if (!panel || !content) return;
    title.textContent = `📊 Loading ${county}...`;
    content.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">Loading...</div>';
    panel.style.display = 'block';
    panel.scrollIntoView({ behavior:'smooth', block:'nearest' });
    try {
      const row = _data.find(r => r.county === county) || {};
      const cfg = _statusCfg(row.status || 'never_run');
      const isDisabled = row.enabled === false || row.status === 'disabled';
      title.textContent = `📊 ${county} County — ${cfg.label}`;
      content.innerHTML = `
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;padding:12px 0">
          <div class="stat-card" style="padding:12px"><div class="stat-label" style="font-size:11px">Total Records</div><div class="stat-value" style="font-size:22px">${(row.total_records||0).toLocaleString()}</div></div>
          <div class="stat-card" style="padding:12px"><div class="stat-label" style="font-size:11px">In Custody</div><div class="stat-value" style="font-size:22px">${(row.in_custody||0).toLocaleString()}</div></div>
          <div class="stat-card" style="padding:12px"><div class="stat-label" style="font-size:11px">Records (24h)</div><div class="stat-value" style="font-size:22px">${(row.records_24h||0).toLocaleString()}</div></div>
          <div class="stat-card" style="padding:12px;border-color:var(--danger)"><div class="stat-label" style="font-size:11px">🔥 Hot Leads</div><div class="stat-value" style="font-size:22px;color:var(--danger)">${(row.hot_leads||0).toLocaleString()}</div></div>
          <div class="stat-card" style="padding:12px;border-color:#ffa502"><div class="stat-label" style="font-size:11px">🟡 Warm Leads</div><div class="stat-value" style="font-size:22px;color:#ffa502">${(row.warm_leads||0).toLocaleString()}</div></div>
          <div class="stat-card" style="padding:12px"><div class="stat-label" style="font-size:11px">Avg Bond</div><div class="stat-value" style="font-size:22px">$${((row.avg_bond||0)/1000).toFixed(1)}K</div></div>
          <div class="stat-card" style="padding:12px"><div class="stat-label" style="font-size:11px">Max Bond</div><div class="stat-value" style="font-size:22px">$${((row.max_bond||0)/1000).toFixed(1)}K</div></div>
          <div class="stat-card" style="padding:12px"><div class="stat-label" style="font-size:11px">Total Runs</div><div class="stat-value" style="font-size:22px">${row.run_count||'—'}</div></div>
          <div class="stat-card" style="padding:12px"><div class="stat-label" style="font-size:11px">Avg Duration</div><div class="stat-value" style="font-size:22px">${_fmtDuration(row.duration_seconds)}</div></div>
        </div>
        <div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          ${!isDisabled ? `<button class="btn" onclick="SLHealth.runNow('${county}')" style="background:var(--accent);color:#000;font-weight:700;padding:6px 14px">⚡ Run Now</button>` : ''}
          <button class="btn" onclick="SLHealth.healthCheck('${county}')" style="background:#1a3a5c;border:1px solid #2563eb;color:#93c5fd;padding:6px 14px">🩺 Health Check</button>
          <button class="btn" onclick="SLHealth.viewLogs('${county}')" style="background:var(--panel-bg);border:1px solid var(--border);padding:6px 14px">📋 View Logs</button>
          <button class="btn" onclick="SL && SL.switchTab && SL.switchTab(document.querySelector('[data-tab=tabLeads]'))" style="background:var(--panel-bg);border:1px solid var(--border);padding:6px 14px">🔍 View Leads</button>
          ${!isDisabled
            ? `<button class="btn" onclick="SLHealth.disableScraper('${county}')" style="background:#3b1a1a;border:1px solid #7f1d1d;color:#fca5a5;padding:6px 14px">⏸ Pause Scraper</button>`
            : `<button class="btn" onclick="SLHealth.enableScraper('${county}')" style="background:#1a3b1a;border:1px solid #166534;color:#86efac;padding:6px 14px">▶ Enable Scraper</button>`
          }
          <button class="btn" onclick="document.getElementById('countyDrillPanel').style.display='none'" style="background:var(--panel-bg);border:1px solid var(--border);padding:6px 14px">✕ Close</button>
        </div>
        ${row.error ? `<div style="margin-top:12px;background:var(--input-bg,#0d1117);border:1px solid var(--danger);border-radius:8px;padding:12px;font-family:monospace;font-size:12px;color:var(--danger)">Last error: ${row.error}</div>` : ''}
      `;
    } catch (err) {
      content.innerHTML = `<div style="color:var(--danger);padding:12px">Failed to load detail: ${err.message}</div>`;
    }
  }

  function _showToast(msg, type = 'info') {
    const colors = { success:'#00c896', error:'#ff4757', info:'#1e90ff', warn:'#ffa502' };
    const color = colors[type] || colors.info;
    const el = document.createElement('div');
    el.style.cssText = `position:fixed;bottom:24px;right:24px;z-index:9999;background:#1a1a2e;border:1px solid ${color};border-radius:10px;padding:12px 18px;color:#fff;font-size:14px;max-width:380px;box-shadow:0 4px 20px rgba(0,0,0,.4);`;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  // ── MongoDB Atlas DB Storage Widget ──────────────────────────────────────
  async function refreshDbWidget() {
    const bar = document.getElementById('dbStorageBar');
    const label = document.getElementById('dbStorageLabel');
    const usedEl = document.getElementById('dbStorageUsed');
    const purgeBtn = document.getElementById('dbPurgeBtn');
    if (!bar) return;
    try {
      const r = await fetch('/api/retention/widget');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      const pct = Math.min(data.usage_pct || 0, 100);
      const totalMb = data.total_size_mb || 0;
      const atRisk = data.at_risk || false;
      bar.style.width = pct + '%';
      bar.style.background = atRisk ? '#c0392b' : pct > 60 ? '#f39c12' : 'var(--accent,#00d4aa)';
      if (label) {
        label.textContent = pct + '% used' + (atRisk ? ' \u26a0\ufe0f Near limit!' : '');
        label.style.color = atRisk ? '#ff6b6b' : 'var(--text-muted)';
      }
      if (usedEl) usedEl.textContent = totalMb + ' MB used';
      if (purgeBtn) purgeBtn.style.display = atRisk ? 'inline-flex' : 'none';
    } catch (err) {
      if (label) label.textContent = 'DB stats unavailable';
      console.warn('[SLHealth] DB widget error:', err);
    }
  }

  async function runRetentionDryRun() {
    _showToast('Running purge estimate...', 'info');
    try {
      const r = await fetch('/api/retention/purge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dry_run: true }),
      });
      const data = await r.json();
      const est = data.results || {};
      const total = est.total_purgeable || 0;
      const prot = est.protected_booking_numbers || 0;
      _showToast(
        'Estimate: ' + total + ' records purgeable (' + prot + ' booking numbers protected)',
        total > 0 ? 'warn' : 'success'
      );
    } catch (err) {
      _showToast('Estimate failed: ' + err.message, 'error');
    }
  }

  async function runRetentionPurge() {
    if (!confirm('\u26a0\ufe0f This will permanently delete old records.\nActive bonds are always protected.\n\nContinue?')) return;
    _showToast('Running purge...', 'warn');
    try {
      const r = await fetch('/api/retention/purge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dry_run: false }),
      });
      const data = await r.json();
      const res = data.results || {};
      const total = res.total_purged || 0;
      _showToast('Purge complete: ' + total + ' records deleted', 'success');
      await refreshDbWidget();
    } catch (err) {
      _showToast('Purge failed: ' + err.message, 'error');
    }
  }

  // Wrap original load to also refresh DB widget
  const _origLoad = load;
  async function loadWithWidget() {
    await _origLoad();
    refreshDbWidget();
  }

  return {
    load: loadWithWidget,
    refresh,
    setFilter,
    search,
    sortBy,
    runNow,
    runAll,
    enableScraper,
    disableScraper,
    healthCheck,
    viewLogs,
    showError,
    showDrill,
    refreshDbWidget,
    runRetentionDryRun,
    runRetentionPurge,
  };
})();

window.renderHealth = function() { SLHealth.load(); };
