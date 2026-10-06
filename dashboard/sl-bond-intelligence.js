/**
 * Bond Intelligence — desk: premium, writable custody, counties to call.
 */
const SLBondIntel = (() => {
  let _charts = {};
  let _data = null;
  let _state = '';
  let _county = '';
  let _days = 30;
  let _queuePage = 1;
  let _refreshTimer = null;
  let _initialized = false;

  const STATE_META = {
    FL: { name: 'Florida', color: '#f59e0b' },
    GA: { name: 'Georgia', color: '#34d399' },
    SC: { name: 'South Carolina', color: '#60a5fa' },
    NC: { name: 'North Carolina', color: '#a78bfa' },
    TN: { name: 'Tennessee', color: '#f87171' },
    TX: { name: 'Texas', color: '#facc15' },
    LA: { name: 'Louisiana', color: '#f472b6' },
    AL: { name: 'Alabama', color: '#fb923c' },
    MS: { name: 'Mississippi', color: '#2dd4bf' },
    CT: { name: 'Connecticut', color: '#38bdf8' },
    OH: { name: 'Ohio', color: '#94a3b8' },
  };
  const STATE_ORDER = ['FL', 'GA', 'SC', 'NC', 'TN', 'TX', 'LA', 'AL', 'MS', 'CT', 'OH'];

  function _fmt$(n) {
    if (!n || n === 0) return '—';
    if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
    return `$${Math.round(n).toLocaleString()}`;
  }
  function _fmtN(n) { return (n == null) ? '—' : Number(n).toLocaleString(); }
  function _pct(a, b) { return b ? Math.round((a / b) * 100) : 0; }
  function _esc(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }
  function _prem(bond) {
    if (window.SLPremium && SLPremium.statutoryPremium) {
      return SLPremium.statutoryPremium(bond, { chargeCount: 1 });
    }
    const b = parseFloat(bond) || 0;
    return b > 0 ? Math.round(Math.max(100, b * 0.10)) : 0;
  }

  async function load() {
    const container = document.getElementById('bondIntelContainer');
    if (!container) return;
    try {
      const q = new URLSearchParams({ days: String(_days) });
      if (_state) q.set('state', _state);
      if (_county) q.set('county', _county);
      const [intelRes, multiRes] = await Promise.all([
        fetch(`/api/bond-intelligence?${q}`, { credentials: 'same-origin' }),
        fetch('/api/arrests/stats/multi-state', { credentials: 'same-origin' }),
      ]);
      if (intelRes.status === 401 || multiRes.status === 401) {
        throw new Error('session expired (401) — re-enter your dashboard PIN');
      }
      if (!intelRes.ok || !multiRes.ok) {
        throw new Error(!intelRes.ok
          ? `bond-intelligence: ${intelRes.status}`
          : `multi-state: ${multiRes.status}`);
      }
      const intel = await intelRes.json();
      const multi = await multiRes.json();
      if (intel.error || multi.error) throw new Error(intel.error || multi.error);
      _data = { intel, multi };
      _render(intel, multi);
      await _loadQueue();
      if (!_initialized) { _initialized = true; _startAutoRefresh(); }
    } catch (err) {
      console.error('[SLBondIntel] load error:', err);
      const cmd = document.getElementById('biCommand');
      if (cmd) {
        cmd.innerHTML = `<div class="bi-empty">Failed to load: ${_esc(err.message)}</div>`;
      }
    }
  }

  function _render(intel, multi) {
    _renderCommand(intel.summary || {});
    _renderStates(multi, intel.by_state || []);
    _renderCounties(intel.by_county || []);
    _renderCharges(intel.top_charges || []);
    _renderDistributionChart(intel.distribution);
    _renderTrendChart(intel.trend);
  }

  function _renderCommand(s) {
    const el = document.getElementById('biCommand');
    if (!el) return;
    const windowLabel = _days === 7 ? 'this week' : _days === 90 ? '90 days' : '30 days';
    el.innerHTML = `
      <article class="bi-stat accent">
        <div class="lbl">Est. premium</div>
        <div class="val">${_fmt$(s.est_premium)}</div>
        <div class="hint">$100 min / 10% over $1k · ${windowLabel}</div>
      </article>
      <article class="bi-stat warn bi-stat-hit" onclick="SLBondIntel.focusWritable()" title="Show every writable defendant and start paperwork">
        <div class="lbl">Writable now</div>
        <div class="val" id="biWritableVal">${_fmtN(s.writable)}</div>
        <div class="hint">In custody with a bond set · click to list them</div>
      </article>
      <article class="bi-stat bi-stat-hit" onclick="SLBondIntel.openHot()" title="Open every hot lead in this window">
        <div class="lbl">Hot leads</div>
        <div class="val">${_fmtN(s.hot_leads)}</div>
        <div class="hint">Score 70+ in this window · click to list them</div>
      </article>
      <article class="bi-stat">
        <div class="lbl">Capture</div>
        <div class="val">${s.bond_capture_rate || 0}%</div>
        <div class="hint">${_fmtN(s.with_bond)} of ${_fmtN(s.total_arrests)} have a bond</div>
      </article>`;
  }

  function _renderStates(multi, intelStates) {
    const el = document.getElementById('biStateMap');
    if (!el) return;
    const byState = {};
    (multi.by_state || []).forEach(s => { byState[s.state] = s; });
    (intelStates || []).forEach(s => {
      byState[s.state] = Object.assign({}, byState[s.state] || {}, s);
    });
    el.innerHTML = STATE_ORDER.map(code => {
      const m = STATE_META[code];
      const d = byState[code] || {};
      const on = _state === code ? ' on' : '';
      const people = Number(d.total_arrests) || 0;
      return `<div class="bi-state${on}" onclick="SLBondIntel.setState('${code}')">
        <div class="nm">${m.name}${code === 'OH' ? ' · guarded' : ''}</div>
        <div class="bd">${_fmt$(d.est_premium || 0)}</div>
        <div class="sm"><button type="button" class="bi-people" onclick="event.stopPropagation(); SLBondIntel.openPeople('${code}')">${_fmtN(people)} in this window</button> · ${_fmtN(d.hot_leads || 0)} hot</div>
      </div>`;
    }).join('');
  }

  function _renderCounties(counties) {
    const el = document.getElementById('biCounties');
    if (!el) return;
    if (!counties.length) {
      el.innerHTML = '<div class="bi-empty">No county volume in this window</div>';
      return;
    }
    el.innerHTML = counties.slice(0, 8).map((c, i) => `
      <div class="bi-county" onclick="SLBondIntel.setCounty('${_esc(c.county)}','${_esc(c.state)}')">
        <span class="rk">${String(i + 1).padStart(2, '0')}</span>
        <span><span class="cn">${_esc(c.county)}</span><br><span class="cs">${_esc(c.state)} · ${_fmtN(c.writable || c.with_bond)} writable</span></span>
        <span class="pr">${_fmt$(c.est_premium || c.total_bond)}</span>
      </div>`).join('');
  }

  function _renderCharges(charges) {
    const el = document.getElementById('biCharges');
    if (!el) return;
    if (!charges.length) {
      el.innerHTML = '<div class="bi-empty">No charge mix yet</div>';
      return;
    }
    const max = charges[0]?.total_bond || 1;
    el.innerHTML = charges.slice(0, 8).map(c => `
      <div class="bi-charge">
        <span title="${_esc(c.charge)}">${_esc((c.charge || '').slice(0, 42))}</span>
        <span>${_fmt$(c.total_bond)}</span>
        <div class="bar"><i style="width:${_pct(c.total_bond, max)}%"></i></div>
      </div>`).join('');
  }

  async function _loadQueue() {
    const el = document.getElementById('biWorkQueue');
    if (!el) return;
    const hotOnly = document.getElementById('biHotOnly')?.checked;
    const min1k = document.getElementById('biMinBond1k')?.checked;
    const q = new URLSearchParams({
      preset: 'writable',
      days: String(_days),
      page: String(_queuePage),
      limit: '25',
    });
    if (hotOnly) q.set('min_score', '70');
    if (min1k) q.set('min_bond', '1000');
    if (_state) q.set('state', _state);
    if (_county) q.set('county', _county);
    try {
      const res = await fetch(`/api/ops/defendants?${q}`, { credentials: 'same-origin', cache: 'no-store' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const arrests = data.defendants || [];
      const total = Number(data.total) || 0;
      if (!hotOnly && !min1k) {
        const val = document.getElementById('biWritableVal');
        if (val) val.textContent = _fmtN(total);
      }
      if (!arrests.length) {
        el.innerHTML = `<div class="bi-empty">No writable defendants in this window${_state ? ' for ' + _esc(_state) : ''}.</div>`;
        return;
      }
      const pages = Number(data.pages) || 1;
      const page = Number(data.page) || 1;
      el.innerHTML = arrests.map(a => {
        const bond = parseFloat(a.bond_amount) || 0;
        const prem = _prem(bond);
        const score = a.lead_score || 0;
        const tier = score >= 70 ? 'hot' : score >= 40 ? 'warm' : 'cold';
        const charges = Array.isArray(a.charges) ? a.charges.slice(0, 2).join(' · ') : (a.charges || '');
        const bk = String(a.booking_number || '');
        const name = a.full_name || 'Unknown';
        const county = a.county || '';
        const st = a.state || '';
        return `<article class="bi-row" data-bk="${_esc(bk)}">
          <div>
            <div class="who">${_esc(name)} <span class="bi-chip ${tier}">${score}</span></div>
            <div class="meta">${_esc(county)}${st ? ', ' + _esc(st) : ''} · ${_esc(charges).slice(0, 72)}</div>
          </div>
          <div class="money">
            <div class="bond">${_fmt$(bond)}</div>
            <div class="prem">${_fmt$(prem)} premium</div>
          </div>
          <div class="act">
            <button type="button" class="bi-btn" data-write="1" data-bk="${_esc(bk)}" data-county="${_esc(county)}" data-state="${_esc(st)}" data-name="${_esc(name)}">Write / Print</button>
          </div>
        </article>`;
      }).join('') + (pages > 1
        ? `<div class="bi-pager"><button type="button" class="bi-btn" ${page <= 1 ? 'disabled' : ''} onclick="SLBondIntel.queuePage(${page - 1})">Prev</button><span>${page} / ${pages} · ${_fmtN(total)}</span><button type="button" class="bi-btn" ${page >= pages ? 'disabled' : ''} onclick="SLBondIntel.queuePage(${page + 1})">Next</button></div>`
        : `<div class="bi-pager"><span>${_fmtN(total)} defendant${total === 1 ? '' : 's'}</span></div>`);
      el.querySelectorAll('.bi-row').forEach(row => {
        row.addEventListener('click', () => openLead(row.dataset.bk));
      });
      el.querySelectorAll('[data-write]').forEach(btn => {
        btn.addEventListener('click', (ev) => {
          ev.stopPropagation();
          writeBond(btn.dataset.name, 0, btn.dataset.county, btn.dataset.bk, btn.dataset.state);
        });
      });
    } catch (err) {
      el.innerHTML = `<div class="bi-empty">Queue failed: ${_esc(err.message)}</div>`;
    }
  }

  function _renderDistributionChart(distribution) {
    const ctx = document.getElementById('bondDistChart');
    if (!ctx || typeof ApexCharts === 'undefined') return;
    if (!distribution || !distribution.length) { ctx.innerHTML = ''; return; }
    if (_charts.dist) _charts.dist.destroy();
    _charts.dist = new ApexCharts(ctx, {
      chart: { type: 'bar', height: 220, background: 'transparent', toolbar: { show: false } },
      series: [{ name: 'Arrests', data: distribution.map(d => d.count) }],
      xaxis: { categories: distribution.map(d => d.range), labels: { style: { colors: '#94a3b8', fontSize: '10px' }, rotate: -28 } },
      yaxis: { labels: { style: { colors: '#94a3b8' } } },
      colors: ['#d4af37'],
      plotOptions: { bar: { borderRadius: 3, columnWidth: '55%' } },
      dataLabels: { enabled: false },
      grid: { borderColor: '#1f2937' },
      theme: { mode: 'dark' },
      tooltip: { theme: 'dark' },
    });
    _charts.dist.render();
  }

  function _renderTrendChart(trend) {
    const ctx = document.getElementById('bondTrendChart');
    if (!ctx || typeof ApexCharts === 'undefined') return;
    if (!trend || !trend.length) { ctx.innerHTML = ''; return; }
    if (_charts.trend) _charts.trend.destroy();
    _charts.trend = new ApexCharts(ctx, {
      chart: { type: 'area', height: 220, background: 'transparent', toolbar: { show: false } },
      series: [{ name: 'Arrests', data: trend.map(t => ({ x: t.date, y: t.arrests })) }],
      xaxis: { type: 'datetime', labels: { style: { colors: '#94a3b8', fontSize: '10px' } } },
      yaxis: { labels: { style: { colors: '#94a3b8' } } },
      colors: ['#34d399'],
      fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.35, opacityTo: 0.04 } },
      stroke: { width: 2, curve: 'smooth' },
      grid: { borderColor: '#1f2937' },
      theme: { mode: 'dark' },
      tooltip: { theme: 'dark', x: { format: 'MMM dd' } },
    });
    _charts.trend.render();
  }

  function setState(s) {
    _state = s || '';
    _county = '';
    _queuePage = 1;
    const sel = document.getElementById('biStateSelect');
    if (sel && sel.value !== _state) sel.value = _state;
    load();
  }

  function setCounty(county, state) {
    _county = county || '';
    _queuePage = 1;
    if (state) _state = state;
    const sel = document.getElementById('biStateSelect');
    if (sel && _state) sel.value = _state;
    load();
  }

  function setDays(d) {
    _days = d;
    _queuePage = 1;
    document.querySelectorAll('#biWindowSeg button').forEach(b => {
      b.classList.toggle('on', parseInt(b.dataset.days, 10) === d);
    });
    load();
  }

  function reloadQueue() {
    _queuePage = 1;
    return _loadQueue();
  }

  function queuePage(page) {
    _queuePage = Math.max(1, page || 1);
    return _loadQueue();
  }

  function focusWritable() {
    const hot = document.getElementById('biHotOnly');
    const min = document.getElementById('biMinBond1k');
    if (hot) hot.checked = false;
    if (min) min.checked = false;
    _queuePage = 1;
    const queue = document.getElementById('biWorkQueue');
    if (queue) queue.scrollIntoView({ behavior: 'smooth', block: 'start' });
    return _loadQueue();
  }

  function openPeople(code) {
    const name = (STATE_META[code] && STATE_META[code].name) || code;
    if (window.SLIntel) {
      SLIntel.open({
        preset: 'range',
        state: code,
        days: _days,
        title: `${name} · last ${_days} days`,
      });
    }
  }

  function openHot() {
    const name = _state ? ((STATE_META[_state] && STATE_META[_state].name) || _state) : 'All states';
    if (window.SLIntel) {
      SLIntel.open({
        preset: 'hot',
        state: _state,
        county: _county,
        days: _days,
        title: `${name} · hot leads · last ${_days} days`,
      });
    }
  }

  function openLead(booking) {
    if (window.SL && typeof SL.openLeadDetail === 'function' && booking) {
      SL.openLeadDetail(booking);
    }
  }

  function writeBond(name, bond, county, booking, state) {
    if (typeof openDefendantWritePrint === 'function' && booking) {
      window._leadMap = window._leadMap || {};
      const prior = window._leadMap[booking] || {};
      window._leadMap[booking] = Object.assign({}, prior, {
        booking_number: booking,
        full_name: name || prior.full_name || '',
        county: county || prior.county || '',
        state: state || prior.state || '',
        bond_amount: bond || prior.bond_amount || 0,
      });
      openDefendantWritePrint(booking);
      return;
    }
    if (typeof openBondModal === 'function') {
      openBondModal(name, parseFloat(bond) || 0, county, booking);
    }
  }

  function _startAutoRefresh() {
    if (_refreshTimer) clearInterval(_refreshTimer);
    _refreshTimer = setInterval(() => {
      const tab = document.getElementById('tabBondIntel');
      if (tab && tab.classList.contains('active')) load();
    }, 120000);
  }

  return {
    load, setState, setCounty, setDays, reloadQueue, queuePage,
    focusWritable, openPeople, openHot, openLead, writeBond,
  };
})();
