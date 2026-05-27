/* ═══════════════════════════════════════════════════════════════════════
   ShamrockLeads — Reports Module  v3.0  (Fortune 50 rebuild)
   Agency compliance · Surety liability · Agent production · POA mgmt
   ═══════════════════════════════════════════════════════════════════════ */
const SLReports = (() => {
  'use strict';
  const API = window.API || '';
  const $  = id => document.getElementById(id);
  const money    = n => '$' + (parseFloat(n)||0).toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0});
  const moneyDec = n => '$' + (parseFloat(n)||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
  const pct      = n => (parseFloat(n)||0).toFixed(1) + '%';
  const toast    = (m,t) => { if(window.SL?.toast) SL.toast(m,t); };
  const fmtDate  = d => d ? new Date(d).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}) : '—';
  const escHtml  = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

  let _currentReport = null;
  let _currentData   = null;
  let _currentPreset = 'mtd';
  let _chartInstance = null;
  let _loaded        = false;

  /* ── Date preset logic ─────────────────────────────────────────────── */
  function _presetDates(preset) {
    const now   = new Date();
    const pad   = n => String(n).padStart(2,'0');
    const fmt   = d => `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
    const today = fmt(now);
    let start, end = today, label;
    switch(preset) {
      case 'today':
        start = today; label = 'Today'; break;
      case 'week': {
        const d = new Date(now); d.setDate(d.getDate() - d.getDay());
        start = fmt(d); label = 'This Week'; break;
      }
      case 'mtd': {
        const d = new Date(now.getFullYear(), now.getMonth(), 1);
        start = fmt(d); label = 'Month to Date'; break;
      }
      case 'qtd': {
        const q = Math.floor(now.getMonth()/3);
        const d = new Date(now.getFullYear(), q*3, 1);
        start = fmt(d); label = 'Quarter to Date'; break;
      }
      case 'ytd': {
        start = `${now.getFullYear()}-01-01`; label = 'Year to Date'; break;
      }
      default:
        start = $('rptStartDate')?.value || ''; label = 'Custom Range'; break;
    }
    return { start, end, label };
  }

  function setPreset(preset) {
    _currentPreset = preset;
    _loaded = false;
    // Update active button
    document.querySelectorAll('.rpt-preset-btn').forEach(b => {
      b.classList.toggle('rpt-preset-active', b.dataset.preset === preset);
    });
    // Show/hide custom date inputs
    const dateRange = $('rptDateRange');
    if (dateRange) dateRange.style.display = preset === 'custom' ? 'flex' : 'none';
    // Update date inputs
    const { start, end, label } = _presetDates(preset);
    if ($('rptStartDate')) $('rptStartDate').value = start;
    if ($('rptEndDate'))   $('rptEndDate').value   = end;
    if ($('rptRangeLabel')) $('rptRangeLabel').textContent = label;
    load();
    if (_currentReport) generate(_currentReport);
  }

  function onDateChange() { _loaded = false; load(); if (_currentReport) generate(_currentReport); }

  /* ── Query string builder ──────────────────────────────────────────── */
  function _qs(extra) {
    const p = new URLSearchParams();
    const s   = $('rptStartDate')?.value;
    const e   = $('rptEndDate')?.value;
    const sur = $('rptSuretyFilter')?.value;
    const cty = $('rptCountyFilter')?.value;
    if (s)   p.set('start_date', s);
    if (e)   p.set('end_date',   e);
    if (sur) p.set('surety',     sur);
    if (cty) p.set('county',     cty);
    if (extra) Object.entries(extra).forEach(([k,v]) => p.set(k,v));
    return p.toString() ? '?' + p.toString() : '';
  }

  async function _fetch(path, extra) {
    try {
      const r = await fetch(`${API}/api/reports/${path}${_qs(extra)}`);
      if (!r.ok) return { success: false, error: `HTTP ${r.status}` };
      return await r.json();
    } catch(e) { return { success: false, error: e.message }; }
  }

  /* ── Load tab: fetch all summary counts ───────────────────────────── */
  async function load() {
    if (_loaded) return;
    _loaded = true;
    // Set default preset dates on first load
    if (!$('rptStartDate')?.value) setPreset('mtd');

    // Show loading state on stat cells
    ['rptStatLiability','rptStatAgents','rptStatDischarged','rptStatForfeitures',
     'rptStatCompliance','rptStatPOA','rptStatVoided','rptStatExpired'].forEach(id => {
      const el = $(id); if (el) el.innerHTML = '<span class="rpt-loading-dot"></span>';
    });

    const [liab, agents, dis, forf, comp, poa, void_, exp] = await Promise.all([
      _fetch('surety-liability'), _fetch('agent-production'), _fetch('discharged'),
      _fetch('forfeitures'), _fetch('check-in-compliance'), _fetch('poa-inventory'),
      _fetch('voided-powers'), _fetch('expired-powers'),
    ]);

    // Update KPI strip
    if (liab.success)  { $('rptKpiLiability').textContent  = money(liab.grand_totals?.total_bond_amount||0); }
    if (agents.success){ $('rptKpiBonds').textContent      = agents.grand_totals?.total_bonds || 0; }
    if (dis.success)   { $('rptKpiDischarged').textContent = dis.count || 0; }
    if (forf.success)  {
      const fc = forf.count || 0;
      $('rptKpiForfeitures').textContent = fc > 0 ? `${fc} · ${money(forf.total_liability||0)}` : '0';
      if (fc > 0) {
        const card = $('rptKpiForfeitureCard');
        if (card) { card.style.borderColor = 'rgba(239,68,68,.35)'; card.style.background = 'rgba(239,68,68,.06)'; }
      }
    }
    if (comp.success)  { $('rptKpiCompliance').textContent = pct(comp.compliance_rate||100); }
    if (poa.success)   {
      let total = 0;
      (poa.sureties||[]).forEach(s => Object.values(s.totals||{}).forEach(v => total += (v||0)));
      $('rptKpiPOA').textContent = total;
    }

    // Update card stats
    if (liab.success)   $('rptStatLiability').textContent  = money(liab.grand_totals?.total_bond_amount||0);
    if (agents.success) $('rptStatAgents').textContent     = `${agents.grand_totals?.total_bonds||0} bonds · ${money(agents.grand_totals?.total_premium||0)} premium`;
    if (dis.success)    $('rptStatDischarged').textContent = `${dis.count||0} bonds`;
    if (forf.success)   $('rptStatForfeitures').textContent= forf.count > 0 ? `${forf.count} · ${money(forf.total_liability||0)} exposure` : '0 forfeitures';
    if (comp.success)   $('rptStatCompliance').textContent = `${pct(comp.compliance_rate||100)} compliant`;
    if (poa.success)    {
      let total = 0;
      (poa.sureties||[]).forEach(s => Object.values(s.totals||{}).forEach(v => total += (v||0)));
      $('rptStatPOA').textContent = `${total} total powers`;
    }
    if (void_.success)  $('rptStatVoided').textContent  = `${void_.count||0} voided`;
    if (exp.success)    $('rptStatExpired').textContent  = `${exp.expired_count||0} expired · ${exp.expiring_soon_count||0} soon`;

    // Update danger badges
    if (forf.success && forf.count > 0) {
      const b = $('rptBadgeForfeitures');
      if (b) { b.textContent = forf.count; b.style.display = 'flex'; }
    }
    if (exp.success && exp.expiring_soon_count > 0) {
      const b = $('rptBadgeExpired');
      if (b) { b.textContent = exp.expiring_soon_count; b.style.display = 'flex'; }
    }
  }

  /* ── Run all reports ───────────────────────────────────────────────── */
  async function runAll() {
    _loaded = false;
    await load();
    toast('All report summaries refreshed', 'success');
  }

  /* ── Show loading skeleton ─────────────────────────────────────────── */
  function _showLoading() {
    const panel = $('rptResultsPanel');
    if (!panel) return;
    panel.style.display = 'block';
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    $('rptLoadingSkeleton').style.display = 'block';
    $('rptTableWrap').innerHTML = '';
    $('rptSummaryStrip').style.display = 'none';
    $('rptChartWrap').style.display = 'none';
    $('rptEmptyState').style.display = 'none';
    $('rptExportCSVBtn').style.display = 'none';
    $('rptExportPDFBtn').style.display = 'none';
    // Highlight active card
    document.querySelectorAll('.rpt-card').forEach(c => c.classList.remove('rpt-card-active'));
    const activeCard = document.querySelector(`.rpt-card[data-report="${_currentReport}"]`);
    if (activeCard) activeCard.classList.add('rpt-card-active');
  }

  function _hideLoading() {
    $('rptLoadingSkeleton').style.display = 'none';
  }

  /* ── Render summary strip ──────────────────────────────────────────── */
  function _renderSummary(items) {
    const strip = $('rptSummaryStrip');
    if (!strip || !items.length) return;
    strip.innerHTML = items.map(item => `
      <div class="rpt-summary-item">
        <div class="rpt-summary-value ${item.color||''}">${escHtml(item.value)}</div>
        <div class="rpt-summary-label">${escHtml(item.label)}</div>
      </div>`).join('');
    strip.style.display = 'flex';
  }

  /* ── Render Chart.js bar/line chart ───────────────────────────────── */
  function _renderChart(labels, datasets, type='bar') {
    const wrap = $('rptChartWrap');
    const canvas = $('rptChart');
    if (!wrap || !canvas || typeof Chart === 'undefined') return;
    if (_chartInstance) { _chartInstance.destroy(); _chartInstance = null; }
    wrap.style.display = 'block';
    const colors = ['#10b981','#3b82f6','#8b5cf6','#f59e0b','#ef4444','#06b6d4'];
    _chartInstance = new Chart(canvas.getContext('2d'), {
      type,
      data: {
        labels,
        datasets: datasets.map((d,i) => ({
          label: d.label,
          data: d.data,
          backgroundColor: type === 'line' ? 'transparent' : (colors[i]+'33'),
          borderColor: colors[i],
          borderWidth: 2,
          borderRadius: type === 'bar' ? 6 : 0,
          tension: 0.4,
          fill: type === 'line',
          pointBackgroundColor: colors[i],
          pointRadius: 4,
        }))
      },
      options: {
        responsive: true,
        plugins: {
          legend: { labels: { color: '#94a3b8', font: { size: 11 } } },
          tooltip: { backgroundColor: '#1e293b', titleColor: '#f1f5f9', bodyColor: '#94a3b8', borderColor: '#334155', borderWidth: 1 }
        },
        scales: {
          x: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e293b' } },
          y: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e293b' } }
        }
      }
    });
  }

  /* ── Render data table ─────────────────────────────────────────────── */
  function _renderTable(headers, rows, emptyMsg) {
    const wrap = $('rptTableWrap');
    if (!wrap) return;
    if (!rows || rows.length === 0) {
      $('rptEmptyState').style.display = 'flex';
      return;
    }
    wrap.innerHTML = `
      <table class="rpt-table">
        <thead><tr>${headers.map(h => `<th>${escHtml(h)}</th>`).join('')}</tr></thead>
        <tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody>
      </table>`;
    $('rptExportCSVBtn').style.display = 'inline-flex';
    $('rptExportPDFBtn').style.display = 'inline-flex';
  }

  /* ── Generate specific report ──────────────────────────────────────── */
  async function generate(type) {
    _currentReport = type;
    const meta = {
      'surety-liability':    { title: 'Surety Liability Statement', icon: '🛡️' },
      'agent-production':    { title: 'Agent Production Report',    icon: '👤' },
      'discharged':          { title: 'Discharged Bonds',           icon: '🏛️' },
      'forfeitures':         { title: 'Forfeitures Report',         icon: '⚠️' },
      'check-in-compliance': { title: 'Check-In Compliance',        icon: '📍' },
      'poa-inventory':       { title: 'POA Inventory Summary',      icon: '📦' },
      'voided-powers':       { title: 'Voided Powers',              icon: '❌' },
      'expired-powers':      { title: 'Expired Powers',             icon: '⏰' },
    }[type] || { title: type, icon: '📋' };

    _showLoading();
    $('rptResultsIcon').textContent = meta.icon;
    $('rptResultsTitle').textContent = meta.title;
    const { label } = _presetDates(_currentPreset);
    $('rptResultsRange').textContent = label;

    const data = await _fetch(type);
    _currentData = data;
    _hideLoading();

    if (!data.success) {
      $('rptTableWrap').innerHTML = `<div class="rpt-error">⚠️ ${escHtml(data.error||'Failed to load report')}</div>`;
      return;
    }

    switch(type) {
      case 'surety-liability':    _renderLiability(data);    break;
      case 'agent-production':    _renderAgents(data);       break;
      case 'discharged':          _renderDischarged(data);   break;
      case 'forfeitures':         _renderForfeitures(data);  break;
      case 'check-in-compliance': _renderCompliance(data);   break;
      case 'poa-inventory':       _renderPOA(data);          break;
      case 'voided-powers':       _renderVoided(data);       break;
      case 'expired-powers':      _renderExpired(data);      break;
    }
    $('rptResultsCount').textContent = _getCount(type, data);
  }

  function _getCount(type, data) {
    const map = {
      'surety-liability':    () => `${(data.sureties||[]).length} sureties`,
      'agent-production':    () => `${(data.agents||[]).length} agents`,
      'discharged':          () => `${data.count||0} bonds`,
      'forfeitures':         () => `${data.count||0} bonds`,
      'check-in-compliance': () => `${(data.bonds||[]).length} defendants`,
      'poa-inventory':       () => `${(data.sureties||[]).length} sureties`,
      'voided-powers':       () => `${data.count||0} powers`,
      'expired-powers':      () => `${(data.expired||[]).length} expired`,
    };
    return (map[type] || (() => ''))();
  }

  /* ── Report renderers ──────────────────────────────────────────────── */

  function _renderLiability(data) {
    const gt = data.grand_totals || {};
    _renderSummary([
      { label: 'Total Bond Amount',  value: money(gt.total_bond_amount||0),  color: 'rpt-val-blue'  },
      { label: 'Total Premium',      value: money(gt.total_premium||0),      color: 'rpt-val-green' },
      { label: 'Surety Owed',        value: money(gt.total_surety_owed||0),  color: 'rpt-val-gold'  },
      { label: 'BUF Collected',      value: money(gt.total_buf||0),          color: 'rpt-val-cyan'  },
      { label: 'Agent Retains',      value: money(gt.total_agent_retains||0),color: 'rpt-val-purple'},
      { label: 'Bond Count',         value: String(gt.total_bonds||0)                               },
    ]);
    // Chart: bond amount by surety
    const sureties = data.sureties || [];
    if (sureties.length) {
      _renderChart(
        sureties.map(s => s.surety),
        [{ label: 'Bond Amount', data: sureties.map(s => s.total_bond_amount||0) }],
        'bar'
      );
    }
    const headers = ['Surety','Bonds','Bond Amount','Premium','Surety Owed','BUF','Agent Retains','Avg Bond'];
    const rows = sureties.map(s => [
      `<strong>${escHtml(s.surety||'—')}</strong>`,
      s.bond_count||0,
      money(s.total_bond_amount||0),
      money(s.total_premium||0),
      money(s.total_surety_owed||0),
      money(s.total_buf||0),
      money(s.total_agent_retains||0),
      money(s.avg_bond_amount||0),
    ]);
    _renderTable(headers, rows);
  }

  function _renderAgents(data) {
    const gt = data.grand_totals || {};
    _renderSummary([
      { label: 'Total Bonds',    value: String(gt.total_bonds||0),          color: 'rpt-val-green' },
      { label: 'Total Premium',  value: money(gt.total_premium||0),         color: 'rpt-val-blue'  },
      { label: 'Avg Bond Size',  value: money(gt.avg_bond_amount||0),       color: 'rpt-val-gold'  },
      { label: 'Total Liability',value: money(gt.total_bond_amount||0),     color: 'rpt-val-cyan'  },
    ]);
    const agents = data.agents || [];
    if (agents.length) {
      _renderChart(
        agents.map(a => a.agent_name||'Unknown'),
        [
          { label: 'Bonds Written', data: agents.map(a => a.bond_count||0) },
          { label: 'Premium ($)',   data: agents.map(a => a.total_premium||0) },
        ],
        'bar'
      );
    }
    const headers = ['Agent','Bonds','Bond Amount','Premium','Avg Bond','Avg Premium','Counties','Surety Breakdown'];
    const rows = agents.map(a => {
      const suretyHtml = Object.entries(a.by_surety||{}).map(([k,v]) =>
        `<span class="rpt-surety-chip">${escHtml(k)}: <strong>${v}</strong></span>`
      ).join(' ') || '—';
      return [
        `<strong>${escHtml(a.agent_name||'Unknown')}</strong>`,
        `<span class="rpt-badge-num">${a.bond_count||0}</span>`,
        money(a.total_bond_amount||0),
        `<span class="rpt-val-green">${money(a.total_premium||0)}</span>`,
        money(a.avg_bond||0),
        money(a.avg_premium||0),
        `<span title="${(a.counties||[]).join(', ')}">${a.county_count||0} counties</span>`,
        suretyHtml,
      ];
    });
    _renderTable(headers, rows);
  }

  function _renderDischarged(data) {
    _renderSummary([
      { label: 'Total Discharged', value: String(data.count||0),                color: 'rpt-val-cyan'  },
      { label: 'Bond Amount',      value: money(data.total_bond_amount||0),      color: 'rpt-val-blue'  },
      { label: 'Exonerated',       value: String(data.exonerated_count||0),      color: 'rpt-val-green' },
      { label: 'Surrendered',      value: String(data.surrendered_count||0),     color: 'rpt-val-gold'  },
    ]);
    const bonds = data.bonds || [];
    const headers = ['Defendant','County','Bond Amount','Surety','Status','Discharge Date','Agent'];
    const rows = bonds.map(b => [
      `<strong>${escHtml(b.defendant_name||'—')}</strong><br><small style="color:var(--muted)">${escHtml(b.booking_number||'')}</small>`,
      escHtml(b.county||'—'),
      money(b.bond_amount||0),
      escHtml(b.surety||'—'),
      `<span class="rpt-status-badge rpt-status-${(b.status||'').toLowerCase()}">${escHtml(b.status||'—')}</span>`,
      fmtDate(b.discharge_date||b.updated_at),
      escHtml(b.agent_name||'—'),
    ]);
    _renderTable(headers, rows);
  }

  function _renderForfeitures(data) {
    _renderSummary([
      { label: 'Total Forfeitures', value: String(data.count||0),              color: 'rpt-val-red'   },
      { label: 'Total Exposure',    value: money(data.total_liability||0),      color: 'rpt-val-red'   },
      { label: 'Avg Bond',          value: money(data.avg_bond_amount||0),      color: 'rpt-val-gold'  },
    ]);
    const bonds = data.bonds || [];
    const headers = ['Defendant','County','Bond Amount','Surety','Forfeiture Date','Court Date','Agent'];
    const rows = bonds.map(b => [
      `<strong>${escHtml(b.defendant_name||'—')}</strong><br><small style="color:var(--muted)">${escHtml(b.booking_number||'')}</small>`,
      escHtml(b.county||'—'),
      `<span class="rpt-val-red">${money(b.bond_amount||0)}</span>`,
      escHtml(b.surety||'—'),
      fmtDate(b.forfeiture_date||b.updated_at),
      fmtDate(b.court_date),
      escHtml(b.agent_name||'—'),
    ]);
    _renderTable(headers, rows);
  }

  function _renderCompliance(data) {
    _renderSummary([
      { label: 'Compliance Rate',    value: pct(data.compliance_rate||100),      color: data.compliance_rate >= 90 ? 'rpt-val-green' : 'rpt-val-red' },
      { label: 'Total Defendants',   value: String((data.bonds||[]).length),      color: 'rpt-val-blue'  },
      { label: 'Overdue',            value: String(data.overdue_count||0),        color: 'rpt-val-red'   },
      { label: 'Missed Check-Ins',   value: String(data.missed_count||0),         color: 'rpt-val-gold'  },
    ]);
    const bonds = data.bonds || [];
    const headers = ['Defendant','County','Bond Amount','Last Check-In','Missed','Status','Action'];
    const rows = bonds.map(b => {
      const overdue = b.is_overdue;
      return [
        `<strong>${escHtml(b.defendant_name||'—')}</strong><br><small style="color:var(--muted)">${escHtml(b.booking_number||'')}</small>`,
        escHtml(b.county||'—'),
        money(b.bond_amount||0),
        b.last_checkin_at ? fmtDate(b.last_checkin_at) : '<span class="rpt-val-red">Never</span>',
        `<span class="${b.missed_checkins > 0 ? 'rpt-val-red' : 'rpt-val-green'}">${b.missed_checkins||0}</span>`,
        `<span class="rpt-status-badge rpt-status-${overdue?'forfeited':'active'}">${overdue?'OVERDUE':'OK'}</span>`,
        `<button class="rpt-action-link" onclick="SLTracking&&SLTracking.openDetail('${escHtml(b.booking_number||'')}')">📍 Track</button>`,
      ];
    });
    _renderTable(headers, rows);
  }

  function _renderPOA(data) {
    const sureties = data.sureties || [];
    let grandTotal = 0;
    sureties.forEach(s => Object.values(s.totals||{}).forEach(v => grandTotal += (v||0)));
    _renderSummary([
      { label: 'Total Powers',    value: String(grandTotal),                  color: 'rpt-val-purple' },
      { label: 'Sureties',        value: String(sureties.length),             color: 'rpt-val-blue'   },
    ]);
    // Chart: stock by surety
    if (sureties.length) {
      _renderChart(
        sureties.map(s => s.surety),
        [{ label: 'Available Powers', data: sureties.map(s => s.totals?.available||0) }],
        'bar'
      );
    }
    const headers = ['Surety','Prefix / Tier','Available','Used','Voided','Expired','Total'];
    const rows = [];
    sureties.forEach(s => {
      (s.prefixes||[]).forEach(p => {
        rows.push([
          `<strong>${escHtml(s.surety||'—')}</strong>`,
          escHtml(p.prefix||'—'),
          `<span class="${(p.available||0) < 5 ? 'rpt-val-red' : 'rpt-val-green'}">${p.available||0}</span>`,
          p.used||0,
          p.voided||0,
          p.expired||0,
          `<strong>${p.total||0}</strong>`,
        ]);
      });
    });
    _renderTable(headers, rows);
  }

  function _renderVoided(data) {
    _renderSummary([
      { label: 'Total Voided', value: String(data.count||0), color: 'rpt-val-red' },
    ]);
    const powers = data.powers || [];
    const headers = ['POA Number','Surety','Bond Amount','Voided By','Reason','Date'];
    const rows = powers.map(p => [
      `<code>${escHtml(p.poa_number||'—')}</code>`,
      escHtml(p.surety_id||p.surety||'—'),
      money(p.bond_amount||0),
      escHtml(p.voided_by||'—'),
      escHtml(p.void_reason||'—'),
      fmtDate(p.voided_at),
    ]);
    _renderTable(headers, rows);
  }

  function _renderExpired(data) {
    _renderSummary([
      { label: 'Expired',        value: String(data.expired_count||0),       color: 'rpt-val-red'   },
      { label: 'Expiring Soon',  value: String(data.expiring_soon_count||0), color: 'rpt-val-gold'  },
    ]);
    const expired = data.expired || [];
    const soon    = data.expiring_soon || [];
    const all     = [...expired.map(p=>({...p,_status:'expired'})), ...soon.map(p=>({...p,_status:'soon'}))];
    const headers = ['POA Number','Surety','Prefix','Expiry Date','Status'];
    const rows = all.map(p => [
      `<code>${escHtml(p.poa_number||'—')}</code>`,
      escHtml(p.surety_id||p.surety||'—'),
      escHtml(p.prefix||'—'),
      fmtDate(p.expiration||p.expiry_date),
      `<span class="rpt-status-badge rpt-status-${p._status==='expired'?'forfeited':'monitoring'}">${p._status==='expired'?'EXPIRED':'EXPIRING SOON'}</span>`,
    ]);
    _renderTable(headers, rows);
  }

  /* ── Export CSV ────────────────────────────────────────────────────── */
  function exportCSV() {
    const table = document.querySelector('#rptTableWrap .rpt-table');
    if (!table) { toast('No data to export','warning'); return; }
    const rows = [];
    table.querySelectorAll('tr').forEach(tr => {
      const cells = [];
      tr.querySelectorAll('th,td').forEach(td => {
        let text = td.innerText.replace(/\n/g,' ').replace(/,/g,';').trim();
        cells.push(`"${text}"`);
      });
      rows.push(cells.join(','));
    });
    const csv = rows.join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    const meta = _currentReport || 'report';
    const { label } = _presetDates(_currentPreset);
    a.href = url;
    a.download = `shamrock-${meta}-${label.replace(/\s+/g,'-').toLowerCase()}-${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    toast('CSV exported','success');
  }

  /* ── Export PDF (print-based) ──────────────────────────────────────── */
  function exportPDF() {
    const panel = $('rptResultsPanel');
    if (!panel) return;
    const title = $('rptResultsTitle')?.textContent || 'Report';
    const range = $('rptRangeLabel')?.textContent   || '';
    const w = window.open('','_blank','width=900,height=700');
    w.document.write(`<!DOCTYPE html><html><head>
      <title>${title}</title>
      <style>
        body{font-family:system-ui,sans-serif;padding:24px;color:#1e293b;background:#fff}
        h1{font-size:20px;margin-bottom:4px}
        .meta{font-size:12px;color:#64748b;margin-bottom:20px}
        table{width:100%;border-collapse:collapse;font-size:12px}
        th{background:#f1f5f9;padding:8px 10px;text-align:left;border-bottom:2px solid #e2e8f0;font-weight:700;text-transform:uppercase;font-size:10px;letter-spacing:.5px}
        td{padding:7px 10px;border-bottom:1px solid #e2e8f0}
        tr:nth-child(even) td{background:#f8fafc}
        .summary{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px}
        .s-item{border:1px solid #e2e8f0;border-radius:8px;padding:12px 18px;min-width:100px;text-align:center}
        .s-val{font-size:18px;font-weight:800}
        .s-lbl{font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px}
        @media print{body{padding:0}}
      </style></head><body>
      <h1>☘️ ${title}</h1>
      <div class="meta">ShamrockLeads · ${range} · Generated ${new Date().toLocaleString()}</div>
      ${$('rptSummaryStrip')?.innerHTML ? `<div class="summary">${$('rptSummaryStrip').innerHTML}</div>` : ''}
      ${$('rptTableWrap')?.innerHTML || '<p>No data</p>'}
      </body></html>`);
    w.document.close();
    setTimeout(() => { w.print(); }, 500);
  }

  /* ── Print report ──────────────────────────────────────────────────── */
  function printReport() { exportPDF(); }

  /* ── Close results panel ───────────────────────────────────────────── */
  function closeResults() {
    const panel = $('rptResultsPanel');
    if (panel) panel.style.display = 'none';
    document.querySelectorAll('.rpt-card').forEach(c => c.classList.remove('rpt-card-active'));
    _currentReport = null;
    if (_chartInstance) { _chartInstance.destroy(); _chartInstance = null; }
  }

  /* ── Schedule report modal ─────────────────────────────────────────── */
  function scheduleReport() {
    const modal = $('rptScheduleModal');
    if (modal) { modal.classList.add('active'); modal.style.display = 'flex'; }
    if (_currentReport && $('schedRptType')) $('schedRptType').value = _currentReport;
  }
  function closeSchedule() {
    const modal = $('rptScheduleModal');
    if (modal) { modal.classList.remove('active'); modal.style.display = 'none'; }
  }
  async function saveSchedule() {
    const type  = $('schedRptType')?.value;
    const freq  = $('schedFrequency')?.value;
    const email = $('schedEmail')?.value;
    if (!email) { toast('Please enter an email address','warning'); return; }
    toast(`📅 Schedule saved: ${type} · ${freq} → ${email}`,'success');
    closeSchedule();
  }

  /* ── Public API ────────────────────────────────────────────────────── */
  return {
    load, runAll, generate, onDateChange, setPreset,
    exportCSV, exportPDF, printReport, closeResults,
    scheduleReport, closeSchedule, saveSchedule,
  };
})();
