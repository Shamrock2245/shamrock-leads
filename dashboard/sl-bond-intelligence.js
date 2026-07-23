/**
 * SLBondIntelligence — Multi-State Bond Analytics Module
 * Connects to: /api/bond-intelligence, /api/arrests/recent, /api/arrests/stats/multi-state
 * Features: State KPI cards, bond distribution chart, top charges table,
 *           county leaderboard, daily trend chart, live recent arrests feed
 */
const SLBondIntel = (() => {
  let _charts = {};
  let _data = null;
  let _state = '';
  let _days = 30;
  let _refreshTimer = null;
  let _initialized = false;

  const STATE_META = {
    FL: { name: 'Florida',        flag: '🌴', color: '#f59e0b' },
    GA: { name: 'Georgia',        flag: '🍑', color: '#10b981' },
    SC: { name: 'South Carolina', flag: '🌙', color: '#3b82f6' },
    NC: { name: 'North Carolina', flag: '🦅', color: '#8b5cf6' },
    TN: { name: 'Tennessee',      flag: '🎸', color: '#ef4444' },
    TX: { name: 'Texas',          flag: '⭐', color: '#eab308' },
    LA: { name: 'Louisiana',      flag: '🎷', color: '#ec4899' },
  };
  const STATE_ORDER = ['FL', 'GA', 'SC', 'NC', 'TN', 'TX', 'LA'];

  function _fmt$(n) {
    if (!n || n === 0) return '—';
    if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
    return `$${Math.round(n).toLocaleString()}`;
  }
  function _fmtN(n) { return (n == null) ? '—' : Number(n).toLocaleString(); }
  function _pct(a, b) { return b ? Math.round((a / b) * 100) : 0; }

  async function load() {
    const container = document.getElementById('bondIntelContainer');
    if (!container) return;

    try {
      const [intelRes, multiRes] = await Promise.all([
        fetch(`/api/bond-intelligence?days=${_days}${_state ? '&state=' + _state : ''}`, { credentials: 'same-origin' }),
        fetch('/api/arrests/stats/multi-state', { credentials: 'same-origin' }),
      ]);
      if (intelRes.status === 401 || multiRes.status === 401) {
        // Global fetch wrapper redirects to /login; keep a clear in-tab message.
        throw new Error('session expired (401) — re-enter your dashboard PIN');
      }
      if (!intelRes.ok || !multiRes.ok) {
        const errText = !intelRes.ok
          ? `bond-intelligence: ${intelRes.status} ${intelRes.statusText}`
          : `multi-state: ${multiRes.status} ${multiRes.statusText}`;
        throw new Error(errText);
      }
      const intel = await intelRes.json();
      const multi = await multiRes.json();
      if (intel.error || multi.error) {
        throw new Error(intel.error || multi.error);
      }
      _data = { intel, multi };
      _render(intel, multi);
      if (!_initialized) { _initialized = true; _startAutoRefresh(); }
    } catch (err) {
      console.error('[SLBondIntel] load error:', err);
      if (container) container.innerHTML = `<div style="color:var(--danger);padding:24px;text-align:center">Failed to load bond intelligence: ${err.message}<br><a href="/login" style="color:var(--accent);margin-top:12px;display:inline-block">Sign in again →</a></div>`;
    }
  }

  function _render(intel, multi) {
    _renderStateKPIs(multi);
    _renderSummaryCards(intel.summary);
    _renderDistributionChart(intel.distribution);
    _renderTrendChart(intel.trend);
    _renderCountyLeaderboard(intel.by_county);
    _renderTopCharges(intel.top_charges);
    _loadRecentFeed();
  }

  function _renderStateKPIs(multi) {
    const el = document.getElementById('bondStateKPIs');
    if (!el) return;
    const byState = {};
    (multi.by_state || []).forEach(s => { byState[s.state] = s; });
    const totals = multi.totals || {};

    el.innerHTML = `
      <!-- Grand Total Card -->
      <div class="bond-kpi-card bond-kpi-grand">
        <div class="bond-kpi-label">ALL STATES</div>
        <div class="bond-kpi-value">${_fmtN(totals.all_time)}</div>
        <div class="bond-kpi-sub">Total Arrests</div>
        <div class="bond-kpi-meta">
          <span>${_fmtN(totals.last_24h)} today</span>
          <span style="color:var(--accent)">${_fmt$(totals.total_bond_value)} bond value</span>
        </div>
      </div>
      ${STATE_ORDER.map(code => {
        const m = STATE_META[code];
        const d = byState[code] || {};
        return `
          <div class="bond-kpi-card" style="border-top:3px solid ${m.color};cursor:pointer" onclick="SLBondIntel.setState('${code}')" title="Filter to ${m.name}">
            <div class="bond-kpi-label">${m.flag} ${m.name}</div>
            <div class="bond-kpi-value" style="color:${m.color}">${_fmtN(d.total || 0)}</div>
            <div class="bond-kpi-sub">Total Arrests</div>
            <div class="bond-kpi-meta">
              <span>${_fmtN(d.last_24h || 0)} today</span>
              <span>${_fmt$(d.avg_bond || 0)} avg bond</span>
            </div>
            <div class="bond-kpi-meta" style="margin-top:4px">
              <span style="color:#ef4444">🔥 ${_fmtN(d.hot_leads || 0)} hot</span>
              <span style="color:${m.color}">${_fmt$(d.total_bond || 0)} total</span>
            </div>
          </div>`;
      }).join('')}
    `;
  }

  function _renderSummaryCards(s) {
    const el = document.getElementById('bondSummaryCards');
    if (!el || !s) return;
    const cards = [
      { label: 'Total Bond Value', value: _fmt$(s.total_bond_value), icon: '💰', color: '#10b981' },
      { label: 'Average Bond', value: _fmt$(s.avg_bond), icon: '📊', color: '#3b82f6' },
      { label: 'Highest Bond', value: _fmt$(s.max_bond), icon: '🚨', color: '#ef4444' },
      { label: 'In Custody', value: _fmtN(s.in_custody), icon: '🔒', color: '#f59e0b' },
      { label: 'With Bond Set', value: _fmtN(s.with_bond), icon: '⚖️', color: '#8b5cf6' },
      { label: 'Bond Capture Rate', value: `${s.bond_capture_rate}%`, icon: '🎯', color: '#06b6d4' },
    ];
    el.innerHTML = cards.map(c => `
      <div class="bond-summary-card">
        <div class="bond-summary-icon" style="color:${c.color}">${c.icon}</div>
        <div class="bond-summary-value" style="color:${c.color}">${c.value}</div>
        <div class="bond-summary-label">${c.label}</div>
      </div>
    `).join('');
  }

  function _renderDistributionChart(distribution) {
    const ctx = document.getElementById('bondDistChart');
    if (!ctx || !distribution || !distribution.length) return;
    if (_charts.dist) { _charts.dist.destroy(); }
    const labels = distribution.map(d => d.range);
    const counts = distribution.map(d => d.count);
    const totals = distribution.map(d => d.total);
    _charts.dist = new ApexCharts(ctx, {
      chart: { type: 'bar', height: 260, background: 'transparent', toolbar: { show: false } },
      series: [
        { name: 'Arrests', data: counts },
        { name: 'Bond Value ($)', data: totals.map(t => Math.round(t)) },
      ],
      xaxis: { categories: labels, labels: { style: { colors: '#9ca3af', fontSize: '11px' }, rotate: -30 } },
      yaxis: [
        { title: { text: 'Arrests', style: { color: '#9ca3af' } }, labels: { style: { colors: '#9ca3af' } } },
        { opposite: true, title: { text: 'Bond Value', style: { color: '#9ca3af' } }, labels: { style: { colors: '#9ca3af' }, formatter: v => _fmt$(v) } },
      ],
      colors: ['#3b82f6', '#10b981'],
      plotOptions: { bar: { borderRadius: 4, columnWidth: '60%' } },
      dataLabels: { enabled: false },
      grid: { borderColor: '#1f2937' },
      theme: { mode: 'dark' },
      tooltip: { theme: 'dark', y: [{ formatter: v => _fmtN(v) }, { formatter: v => _fmt$(v) }] },
    });
    _charts.dist.render();
  }

  function _renderTrendChart(trend) {
    const ctx = document.getElementById('bondTrendChart');
    if (!ctx || !trend || !trend.length) return;
    if (_charts.trend) { _charts.trend.destroy(); }
    _charts.trend = new ApexCharts(ctx, {
      chart: { type: 'area', height: 220, background: 'transparent', toolbar: { show: false }, sparkline: { enabled: false } },
      series: [
        { name: 'Arrests', data: trend.map(t => ({ x: t.date, y: t.arrests })) },
        { name: 'Avg Bond', data: trend.map(t => ({ x: t.date, y: Math.round(t.avg_bond || 0) })) },
      ],
      xaxis: { type: 'datetime', labels: { style: { colors: '#9ca3af', fontSize: '11px' } } },
      yaxis: [
        { labels: { style: { colors: '#9ca3af' } } },
        { opposite: true, labels: { style: { colors: '#9ca3af' }, formatter: v => _fmt$(v) } },
      ],
      colors: ['#3b82f6', '#f59e0b'],
      fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.4, opacityTo: 0.05 } },
      stroke: { width: 2, curve: 'smooth' },
      grid: { borderColor: '#1f2937' },
      theme: { mode: 'dark' },
      tooltip: { theme: 'dark', x: { format: 'MMM dd' } },
    });
    _charts.trend.render();
  }

  function _renderCountyLeaderboard(counties) {
    const el = document.getElementById('bondCountyLeaderboard');
    if (!el || !counties) return;
    el.innerHTML = `
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead>
          <tr style="color:var(--text-muted);text-transform:uppercase;font-size:11px;letter-spacing:.05em">
            <th style="padding:6px 8px;text-align:left">#</th>
            <th style="padding:6px 8px;text-align:left">County</th>
            <th style="padding:6px 8px;text-align:left">State</th>
            <th style="padding:6px 8px;text-align:right">Arrests</th>
            <th style="padding:6px 8px;text-align:right">Total Bond</th>
            <th style="padding:6px 8px;text-align:right">Avg Bond</th>
            <th style="padding:6px 8px;text-align:right">Max Bond</th>
          </tr>
        </thead>
        <tbody>
          ${counties.slice(0, 20).map((c, i) => {
            const m = STATE_META[c.state] || { color: '#9ca3af', flag: '' };
            return `
              <tr style="border-top:1px solid var(--border);transition:background .15s" onmouseover="this.style.background='rgba(255,255,255,.03)'" onmouseout="this.style.background=''">
                <td style="padding:8px;color:var(--text-muted)">${i + 1}</td>
                <td style="padding:8px;font-weight:600">${c.county}</td>
                <td style="padding:8px"><span style="color:${m.color}">${m.flag} ${c.state}</span></td>
                <td style="padding:8px;text-align:right">${_fmtN(c.total_arrests)}</td>
                <td style="padding:8px;text-align:right;color:#10b981;font-weight:600">${_fmt$(c.total_bond)}</td>
                <td style="padding:8px;text-align:right;color:#3b82f6">${_fmt$(c.avg_bond)}</td>
                <td style="padding:8px;text-align:right;color:#ef4444">${_fmt$(c.max_bond)}</td>
              </tr>`;
          }).join('')}
        </tbody>
      </table>
    `;
  }

  function _renderTopCharges(charges) {
    const el = document.getElementById('bondTopCharges');
    if (!el || !charges) return;
    const max = charges[0]?.total_bond || 1;
    el.innerHTML = charges.slice(0, 15).map((c, i) => `
      <div style="margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;margin-bottom:3px;font-size:12px">
          <span style="color:var(--text);font-weight:500;max-width:65%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${c.charge}</span>
          <span style="color:#10b981;font-weight:600">${_fmt$(c.total_bond)}</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <div style="flex:1;height:6px;background:var(--border);border-radius:3px;overflow:hidden">
            <div style="height:100%;width:${_pct(c.total_bond, max)}%;background:linear-gradient(90deg,#3b82f6,#10b981);border-radius:3px;transition:width .4s"></div>
          </div>
          <span style="font-size:11px;color:var(--text-muted);min-width:40px;text-align:right">${_fmtN(c.count)} arrests</span>
        </div>
      </div>
    `).join('');
  }

  async function _loadRecentFeed() {
    const el = document.getElementById('bondRecentFeed');
    if (!el) return;
    try {
      const res = await fetch(`/api/arrests/recent?limit=30&hours=24${_state ? '&state=' + _state : ''}`);
      const data = await res.json();
      const arrests = data.arrests || [];
      if (!arrests.length) {
        el.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:24px">No arrests in the last 24 hours</div>';
        return;
      }
      el.innerHTML = arrests.map(a => {
        const bond = a.bond_amount > 0 ? `<span style="color:#10b981;font-weight:700">${_fmt$(a.bond_amount)}</span>` : '<span style="color:var(--text-muted)">No Bond</span>';
        const score = a.lead_score >= 70 ? '🔥' : a.lead_score >= 40 ? '🟡' : '❄️';
        const m = STATE_META[a.state] || { flag: '', color: '#9ca3af' };
        const charges = Array.isArray(a.charges) ? a.charges.slice(0, 2).join(', ') : (a.charges || '');
        return `
          <div class="bond-feed-row" onclick="SL.openLeadDetail && SL.openLeadDetail('${a.booking_number || ''}')">
            <div style="display:flex;justify-content:space-between;align-items:flex-start">
              <div>
                <span style="font-weight:700;color:var(--text)">${a.full_name || 'Unknown'}</span>
                <span style="margin-left:8px;font-size:11px;color:${m.color}">${m.flag} ${a.county || ''}, ${a.state || ''}</span>
              </div>
              <div style="text-align:right">${bond}</div>
            </div>
            <div style="display:flex;justify-content:space-between;margin-top:3px">
              <span style="font-size:11px;color:var(--text-muted);max-width:70%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${charges}</span>
              <span style="font-size:11px;color:var(--text-muted)">${score} ${a.lead_score || 0}</span>
            </div>
          </div>`;
      }).join('');
    } catch (err) {
      if (el) el.innerHTML = `<div style="color:var(--danger);padding:12px">Failed to load feed: ${err.message}</div>`;
    }
  }

  function setState(s) {
    _state = s;
    document.querySelectorAll('.bond-state-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.state === s);
    });
    load();
  }

  function setDays(d) {
    _days = d;
    document.querySelectorAll('.bond-days-btn').forEach(b => {
      b.classList.toggle('active', parseInt(b.dataset.days) === d);
    });
    load();
  }

  function _startAutoRefresh() {
    if (_refreshTimer) clearInterval(_refreshTimer);
    _refreshTimer = setInterval(() => {
      const tab = document.getElementById('tabBondIntel');
      if (tab && tab.classList.contains('active')) load();
    }, 120000); // 2-minute refresh
  }

  return { load, setState, setDays };
})();
