/* ═══════════════════════════════════════════════════════════
   ShamrockLeads — Reports Module
   Agency compliance, surety liability, agent production
   ═══════════════════════════════════════════════════════════ */

const SLReports = (() => {
  const API = window.API || '';
  const $ = id => document.getElementById(id);
  const money = n => '$' + (parseFloat(n)||0).toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0});
  const moneyDec = n => '$' + (parseFloat(n)||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
  const toast = (m,t) => { if(window.SL?.toast) SL.toast(m,t); else alert(m); };

  let _currentReport = null;
  let _currentData = null;
  let _loaded = false;

  const REPORT_META = {
    'discharged':        { title: 'Discharged Bonds', icon: '🏛️' },
    'surety-liability':  { title: 'Surety Liability Statement', icon: '🛡️' },
    'forfeitures':       { title: 'Forfeitures Report', icon: '⚠️' },
    'agent-production':  { title: 'Agent Production', icon: '👤' },
    'voided-powers':     { title: 'Voided Powers', icon: '❌' },
    'expired-powers':    { title: 'Expired Powers', icon: '⏰' },
    'check-in-compliance': { title: 'Check-In Compliance', icon: '📍' },
    'poa-inventory':     { title: 'POA Inventory Summary', icon: '📦' },
  };

  function _qs() {
    const p = new URLSearchParams();
    const s = $('rptStartDate')?.value;
    const e = $('rptEndDate')?.value;
    const sur = $('rptSuretyFilter')?.value;
    if (s) p.set('start_date', s);
    if (e) p.set('end_date', e);
    if (sur) p.set('surety', sur);
    return p.toString() ? '?' + p.toString() : '';
  }

  async function _fetch(path) {
    try {
      const r = await fetch(`${API}/api/reports/${path}${_qs()}`);
      return await r.json();
    } catch(e) { return { success: false, error: e.message }; }
  }

  // ── Load tab (fetch summary counts) ─────────────────────────────────────
  async function load() {
    if (_loaded) return;
    _loaded = true;
    // Fetch counts in parallel
    const [dis, forf, void_, exp, comp, poa, agents] = await Promise.all([
      _fetch('discharged'), _fetch('forfeitures'), _fetch('voided-powers'),
      _fetch('expired-powers'), _fetch('check-in-compliance'),
      _fetch('poa-inventory'), _fetch('agent-production'),
    ]);
    if (dis.success) $('rptStatDischarged').textContent = `${dis.count} bonds`;
    if (forf.success) $('rptStatForfeitures').textContent = forf.count > 0 ? `${forf.count} · ${money(forf.total_liability)}` : '0';
    if (void_.success) $('rptStatVoided').textContent = `${void_.count} voided`;
    if (exp.success) $('rptStatExpired').textContent = `${exp.expired_count} expired · ${exp.expiring_soon_count} soon`;
    if (comp.success) $('rptStatCompliance').textContent = `${comp.compliance_rate}% compliant`;
    if (agents.success) $('rptStatAgents').textContent = `${agents.grand_totals?.total_bonds || 0} bonds`;
    // Liability — quick fetch
    const liab = await _fetch('surety-liability');
    if (liab.success) $('rptStatLiability').textContent = money(liab.grand_totals?.total_bond_amount || 0);
    // POA
    if (poa.success) {
      let total = 0;
      (poa.sureties||[]).forEach(s => Object.values(s.totals||{}).forEach(v => total += v));
      $('rptStatPOA').textContent = `${total} total`;
    }
  }

  function onDateChange() { _loaded = false; load(); if (_currentReport) generate(_currentReport); }

  // ── Generate specific report ────────────────────────────────────────────
  async function generate(type) {
    _currentReport = type;
    const meta = REPORT_META[type] || { title: type, icon: '📋' };
    const panel = $('rptResultsPanel');
    const tableWrap = $('rptTableWrap');
    const strip = $('rptSummaryStrip');
    panel.style.display = 'block';
    $('rptResultsTitle').textContent = `${meta.icon} ${meta.title}`;
    $('rptResultsCount').textContent = 'Loading...';
    $('rptExportBtn').style.display = 'none';
    strip.style.display = 'none';
    tableWrap.innerHTML = '<div style="text-align:center;padding:30px;color:var(--muted)">☘️ Generating report...</div>';

    // Highlight active card
    document.querySelectorAll('.rpt-card').forEach(c => c.classList.remove('rpt-card-active'));
    document.querySelector(`.rpt-card[data-report="${type}"]`)?.classList.add('rpt-card-active');

    const data = await _fetch(type);
    _currentData = data;
    if (!data.success) {
      tableWrap.innerHTML = `<div style="text-align:center;padding:30px;color:#ef4444">❌ ${data.error||'Failed to load'}</div>`;
      return;
    }
    $('rptExportBtn').style.display = '';

    // Route to renderer
    switch(type) {
      case 'discharged': _renderDischarged(data); break;
      case 'surety-liability': _renderLiability(data); break;
      case 'forfeitures': _renderForfeitures(data); break;
      case 'agent-production': _renderAgentProd(data); break;
      case 'voided-powers': _renderVoidedPowers(data); break;
      case 'expired-powers': _renderExpiredPowers(data); break;
      case 'check-in-compliance': _renderCompliance(data); break;
      case 'poa-inventory': _renderPOAInventory(data); break;
    }
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // ── Table builder ───────────────────────────────────────────────────────
  function _table(cols, rows) {
    if (!rows.length) return '<div style="text-align:center;padding:20px;color:var(--muted)">No records found</div>';
    let h = '<table class="rpt-table"><thead><tr>';
    cols.forEach(c => h += `<th>${c.label}</th>`);
    h += '</tr></thead><tbody>';
    rows.forEach(r => {
      h += '<tr>';
      cols.forEach(c => {
        let v = c.fn ? c.fn(r) : (r[c.key] ?? '');
        h += `<td>${v}</td>`;
      });
      h += '</tr>';
    });
    h += '</tbody></table>';
    return h;
  }

  function _summaryCards(items) {
    let h = '<div class="rpt-summary-strip">';
    items.forEach(i => h += `<div class="rpt-summary-item"><div class="rpt-summary-value">${i.value}</div><div class="rpt-summary-label">${i.label}</div></div>`);
    h += '</div>';
    return h;
  }

  // ── Renderers ───────────────────────────────────────────────────────────
  function _renderDischarged(d) {
    $('rptResultsCount').textContent = `${d.count} discharged bonds · Total: ${money(d.total_bond_amount)}`;
    const strip = $('rptSummaryStrip');
    strip.style.display = 'block';
    strip.innerHTML = _summaryCards([
      { value: d.count, label: 'Bonds' },
      { value: money(d.total_bond_amount), label: 'Bond Amount' },
      { value: money(d.total_premium), label: 'Premium' },
    ]);
    $('rptTableWrap').innerHTML = _table([
      { label: 'Defendant', key: 'defendant_name' },
      { label: 'County', key: 'county' },
      { label: 'Bond', fn: r => money(r.bond_amount) },
      { label: 'Premium', fn: r => moneyDec(r.split?.premium) },
      { label: 'Status', key: 'status' },
      { label: 'Date', fn: r => (r.bond_date||'').slice(0,10) },
      { label: 'Surety', fn: r => (r.surety||r.insurance_company||'').toUpperCase() },
    ], d.records||[]);
  }

  function _renderLiability(d) {
    const gt = d.grand_totals||{};
    $('rptResultsCount').textContent = `${gt.total_bonds} bonds across ${(d.sureties||[]).length} sureties`;
    const strip = $('rptSummaryStrip');
    strip.style.display = 'block';
    strip.innerHTML = _summaryCards([
      { value: money(gt.total_bond_amount), label: 'Total Liability' },
      { value: money(gt.total_premium), label: 'Total Premium' },
      { value: money(gt.total_surety_owed), label: 'Surety Owed' },
      { value: money(gt.total_buf_owed), label: 'BUF Owed' },
      { value: money(gt.total_agent_retains), label: 'Agent Retains' },
    ]);
    let html = '';
    (d.sureties||[]).forEach(s => {
      html += `<h4 style="margin:18px 0 8px;color:var(--accent)">${s.surety} — ${s.bond_count} Bonds · ${money(s.total_bond_amount)} Liability</h4>`;
      html += _table([
        { label: 'Defendant', key: 'defendant_name' },
        { label: 'County', key: 'county' },
        { label: 'Bond', fn: r => money(r.bond_amount) },
        { label: 'Premium', fn: r => moneyDec(r.premium) },
        { label: 'Surety Owed', fn: r => moneyDec(r.surety_owed) },
        { label: 'BUF Owed', fn: r => moneyDec(r.buf_owed) },
        { label: 'Agent', fn: r => moneyDec(r.agent_retains) },
        { label: 'Case #', key: 'case_number' },
        { label: 'Date', fn: r => (r.bond_date||'').slice(0,10) },
      ], s.bonds||[]);
    });
    $('rptTableWrap').innerHTML = html;
  }

  function _renderForfeitures(d) {
    $('rptResultsCount').textContent = `${d.count} forfeited bonds · ${money(d.total_liability)} liability`;
    $('rptSummaryStrip').style.display = 'none';
    $('rptTableWrap').innerHTML = _table([
      { label: 'Defendant', key: 'defendant_name' },
      { label: 'County', key: 'county' },
      { label: 'Bond', fn: r => money(r.bond_amount) },
      { label: 'Surety', fn: r => (r.surety||r.insurance_company||'').toUpperCase() },
      { label: 'Date', fn: r => (r.bond_date||'').slice(0,10) },
      { label: 'Case #', key: 'case_number' },
      { label: 'Agent', key: 'agent_name' },
    ], d.records||[]);
  }

  function _renderAgentProd(d) {
    const gt = d.grand_totals||{};
    $('rptResultsCount').textContent = `${gt.total_bonds} bonds · ${money(gt.total_premium)} premium`;
    const strip = $('rptSummaryStrip');
    strip.style.display = 'block';
    strip.innerHTML = _summaryCards([
      { value: gt.total_bonds, label: 'Total Bonds' },
      { value: money(gt.total_premium), label: 'Total Premium' },
      { value: money(gt.total_bond_amount), label: 'Total Bond Amount' },
    ]);
    $('rptTableWrap').innerHTML = _table([
      { label: 'Agent', key: 'agent_name' },
      { label: 'Bonds', key: 'bond_count' },
      { label: 'Total Premium', fn: r => money(r.total_premium) },
      { label: 'Total Bond $', fn: r => money(r.total_bond_amount) },
      { label: 'Avg Bond', fn: r => money(r.avg_bond) },
      { label: 'Avg Premium', fn: r => money(r.avg_premium) },
      { label: 'Counties', fn: r => (r.counties||[]).join(', ') },
    ], d.agents||[]);
  }

  function _renderVoidedPowers(d) {
    $('rptResultsCount').textContent = `${d.count} voided POAs`;
    $('rptSummaryStrip').style.display = 'none';
    $('rptTableWrap').innerHTML = _table([
      { label: 'POA #', key: 'poa_number' },
      { label: 'Full', key: 'poa_full' },
      { label: 'Surety', key: 'surety_id' },
      { label: 'Prefix', key: 'poa_prefix' },
      { label: 'Max Bond', fn: r => money(r.max_bond_value) },
      { label: 'Void Reason', key: 'void_reason' },
      { label: 'Voided', fn: r => (r.voided_at||'').slice(0,10) },
    ], d.records||[]);
  }

  function _renderExpiredPowers(d) {
    $('rptResultsCount').textContent = `${d.expired_count} expired · ${d.expiring_soon_count} expiring within 30 days`;
    const strip = $('rptSummaryStrip');
    strip.style.display = 'block';
    strip.innerHTML = _summaryCards([
      { value: d.expired_count, label: 'Expired' },
      { value: d.expiring_soon_count, label: 'Expiring Soon (30d)' },
    ]);
    let html = '<h4 style="margin:8px 0;color:#ef4444">Expired</h4>';
    html += _table([
      { label: 'POA #', key: 'poa_number' },
      { label: 'Full', key: 'poa_full' },
      { label: 'Surety', key: 'surety_id' },
      { label: 'Status', key: 'status' },
      { label: 'Expiration', fn: r => (r.expiration||'').slice(0,10) },
    ], d.expired||[]);
    if ((d.expiring_soon||[]).length > 0) {
      html += '<h4 style="margin:18px 0 8px;color:#f59e0b">⚠️ Expiring Within 30 Days</h4>';
      html += _table([
        { label: 'POA #', key: 'poa_number' },
        { label: 'Full', key: 'poa_full' },
        { label: 'Surety', key: 'surety_id' },
        { label: 'Status', key: 'status' },
        { label: 'Expiration', fn: r => (r.expiration||'').slice(0,10) },
      ], d.expiring_soon||[]);
    }
    $('rptTableWrap').innerHTML = html;
  }

  function _renderCompliance(d) {
    $('rptResultsCount').textContent = `${d.count} active bonds · ${d.compliance_rate}% compliant · ${d.overdue} overdue`;
    const strip = $('rptSummaryStrip');
    strip.style.display = 'block';
    const pct = d.compliance_rate||0;
    const color = pct >= 90 ? '#10b981' : pct >= 70 ? '#f59e0b' : '#ef4444';
    strip.innerHTML = _summaryCards([
      { value: `<span style="color:${color}">${pct}%</span>`, label: 'Compliance Rate' },
      { value: d.compliant, label: 'Compliant' },
      { value: `<span style="color:#ef4444">${d.overdue}</span>`, label: 'Overdue' },
    ]);
    $('rptTableWrap').innerHTML = _table([
      { label: 'Defendant', key: 'defendant_name' },
      { label: 'County', key: 'county' },
      { label: 'Bond', fn: r => money(r.bond_amount) },
      { label: 'Missed', key: 'missed_check_ins' },
      { label: 'Next Due', fn: r => (r.next_check_in_due||'').slice(0,10) },
      { label: 'Status', fn: r => {
        const s = r.compliance_status;
        const c = s==='overdue'?'#ef4444':s==='warning'?'#f59e0b':'#10b981';
        return `<span style="color:${c};font-weight:600">${s.toUpperCase()}</span>`;
      }},
    ], d.records||[]);
  }

  function _renderPOAInventory(d) {
    $('rptResultsCount').textContent = `${d.expired_count} expired across all sureties`;
    $('rptSummaryStrip').style.display = 'none';
    let html = '';
    (d.sureties||[]).forEach(s => {
      const totals = s.totals||{};
      const totalAll = Object.values(totals).reduce((a,b)=>a+b,0);
      html += `<h4 style="margin:14px 0 8px;color:var(--accent)">${(s.surety_id||'').toUpperCase()} — ${totalAll} POAs (Available: ${totals.available||0} · Assigned: ${totals.assigned||0} · Voided: ${totals.voided||0})</h4>`;
      const rows = (s.tiers||[]).map(t => ({
        prefix: t.prefix, max_bond: t.max_bond_value,
        available: t.statuses?.available||0, assigned: t.statuses?.assigned||0, voided: t.statuses?.voided||0,
        total: Object.values(t.statuses||{}).reduce((a,b)=>a+b,0),
      }));
      html += _table([
        { label: 'Tier/Prefix', key: 'prefix' },
        { label: 'Max Bond', fn: r => money(r.max_bond) },
        { label: 'Available', fn: r => `<span style="color:#10b981;font-weight:600">${r.available}</span>` },
        { label: 'Assigned', fn: r => `<span style="color:#3b82f6">${r.assigned}</span>` },
        { label: 'Voided', fn: r => `<span style="color:#ef4444">${r.voided}</span>` },
        { label: 'Total', key: 'total' },
      ], rows);
    });
    $('rptTableWrap').innerHTML = html || '<div style="padding:20px;text-align:center;color:var(--muted)">No POA inventory data</div>';
  }

  // ── CSV Export ──────────────────────────────────────────────────────────
  function exportCSV() {
    if (!_currentData || !_currentReport) return;
    const meta = REPORT_META[_currentReport]||{};
    let rows = [];

    // Extract rows based on report type
    if (_currentReport === 'surety-liability') {
      rows = []; (_currentData.sureties||[]).forEach(s => (s.bonds||[]).forEach(b => rows.push(b)));
    } else if (_currentReport === 'agent-production') {
      rows = _currentData.agents||[];
    } else if (_currentReport === 'expired-powers') {
      rows = [...(_currentData.expired||[]), ...(_currentData.expiring_soon||[])];
    } else if (_currentReport === 'poa-inventory') {
      rows = [];
      (_currentData.sureties||[]).forEach(s => (s.tiers||[]).forEach(t => {
        rows.push({ surety: s.surety_id, prefix: t.prefix, max_bond: t.max_bond_value,
          available: t.statuses?.available||0, assigned: t.statuses?.assigned||0, voided: t.statuses?.voided||0 });
      }));
    } else {
      rows = _currentData.records||[];
    }

    if (!rows.length) { toast('No data to export', 'warning'); return; }

    // Build CSV
    const keys = Object.keys(rows[0]).filter(k => k !== 'split' && k !== 'location_history' && k !== 'alerts');
    let csv = keys.join(',') + '\n';
    rows.forEach(r => {
      csv += keys.map(k => {
        let v = r[k];
        if (v === null || v === undefined) v = '';
        if (Array.isArray(v)) v = v.join('; ');
        if (typeof v === 'object') v = JSON.stringify(v);
        v = String(v).replace(/"/g, '""');
        return `"${v}"`;
      }).join(',') + '\n';
    });

    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `shamrock_${_currentReport}_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    toast(`📥 ${meta.title} exported`, 'success');
  }

  function closeResults() {
    $('rptResultsPanel').style.display = 'none';
    document.querySelectorAll('.rpt-card').forEach(c => c.classList.remove('rpt-card-active'));
    _currentReport = null;
    _currentData = null;
  }

  return { load, generate, exportCSV, closeResults, onDateChange };
})();
