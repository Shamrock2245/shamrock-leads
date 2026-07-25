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
  const fmtDate  = d => {
    if (!d) return '—';
    try {
      const s = String(d).trim();
      if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
        const [y, m, day] = s.split('-').map(Number);
        return new Date(y, m - 1, day).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
      }
      const dt = new Date(s);
      return isNaN(dt) ? s : dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    } catch { return '—'; }
  };
  const escHtml  = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

  /* ── Chronological bond sort: oldest bond written first, newest last ──
     Rows with missing/unparseable dates sort to the end (never dropped).
     Graceful: any exception returns the original order and logs a warning. */
  function sortBondsOldestFirst(list, dateFields) {
    const fields = dateFields || ['bond_date','date_executed','posted_date','created_at'];
    try {
      const parse = b => {
        for (const f of fields) {
          const v = b && b[f];
          if (v) {
            const t = Date.parse(String(v).slice(0, 19));
            if (!isNaN(t)) return t;
          }
        }
        return Number.MAX_SAFE_INTEGER; // undated rows trail dated rows
      };
      return [...(list||[])].sort((a,b) => {
        const d = parse(a) - parse(b);
        if (d !== 0) return d;
        const pa = String(a.poa_number||a.power_number||''), pb = String(b.poa_number||b.power_number||'');
        return pa.localeCompare(pb);
      });
    } catch (e) {
      console.warn('[SLReports] chronological sort failed — keeping original order:', e);
      return list || [];
    }
  }

  let _currentReport = null;
  let _currentData   = null;
  let _currentPreset = 'mtd';
  let _chartInstance = null;
  let _loaded        = false;
  const PRESET_KEY = 'sl_reports_preset_v1';
  const SCOPE_KEY  = 'sl_reports_status_scope_v1';

  // Earliest supported bond record — reports may span 2012 → present.
  const REPORT_EPOCH = '2012-01-01';

  function _fmtBondDate(d) {
    if (!d) return '—';
    const s = String(d).trim();
    // Prefer date-only for surety readability
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) {
      const [y, m, day] = s.slice(0, 10).split('-');
      return `${m}/${day}/${y}`;
    }
    return fmtDate(d);
  }

  function _statusScope() {
    return $('rptStatusScope')?.value || localStorage.getItem(SCOPE_KEY) || 'open';
  }

  function _showQualityBanner(data) {
    const el = $('rptQualityBanner');
    if (!el) return;
    const parts = [];
    const q = data.data_quality || {};
    parts.push(`↕️ Oldest → newest`);
    if (data.status_scope) parts.push(`Scope: ${data.status_scope}`);
    if (q.bond_date_min || q.bond_date_max) {
      parts.push(`Window in data: ${q.bond_date_min || '—'} → ${q.bond_date_max || '—'}`);
    }
    if (typeof q.quality_score === 'number') parts.push(`Quality ${q.quality_score}%`);
    if (q.undated_count) parts.push(`⚠ ${q.undated_count} undated`);
    if (q.missing_power_count) parts.push(`⚠ ${q.missing_power_count} missing power #`);
    if (data.truncated) parts.push(`⚠ Hit ${q.row_count || 'row'} limit — narrow range`);
    if (Array.isArray(data.warnings) && data.warnings.length) {
      parts.push(data.warnings[0]);
    }
    el.innerHTML = parts.map(p => `<span class="rpt-quality-chip">${escHtml(p)}</span>`).join('');
    el.style.display = parts.length ? 'flex' : 'none';
  }

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
      case 'all': {
        start = REPORT_EPOCH; label = 'All Time (Since 2012)'; break;
      }
      default:
        start = $('rptStartDate')?.value || '';
        end   = $('rptEndDate')?.value || today;
        label = 'Custom Range'; break;
    }
    // Clamp any range to the supported record window (2012-01-01 → today)
    if (start && start < REPORT_EPOCH) {
      toast(`Start date clamped to ${REPORT_EPOCH} (earliest bond records)`, 'info');
      start = REPORT_EPOCH;
    }
    if (end && end > today) end = today;
    if (start && end && start > end) { const t = start; start = end; end = t; }
    return { start, end, label };
  }

  function setPreset(preset) {
    _currentPreset = preset;
    try { localStorage.setItem(PRESET_KEY, preset); } catch (_) {}
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

  function onScopeChange() {
    try { localStorage.setItem(SCOPE_KEY, _statusScope()); } catch (_) {}
    _loaded = false;
    load();
    if (_currentReport) generate(_currentReport);
  }

  /* ── Query string builder ──────────────────────────────────────────── */
  function _qs(extra) {
    const p = new URLSearchParams();
    const s   = $('rptStartDate')?.value;
    const e   = $('rptEndDate')?.value;
    const sur = $('rptSuretyFilter')?.value;
    const cty = $('rptCountyFilter')?.value;
    const scope = _statusScope();
    if (s)   p.set('start_date', s);
    if (e)   p.set('end_date',   e);
    if (sur) p.set('surety',     sur);
    if (cty) p.set('county',     cty);
    if (scope) p.set('status_scope', scope);
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
    // Restore last scope + preset (operators keep their working window)
    if ($('rptStatusScope') && !$('rptStatusScope').dataset.restored) {
      const savedScope = localStorage.getItem(SCOPE_KEY);
      if (savedScope) $('rptStatusScope').value = savedScope;
      $('rptStatusScope').dataset.restored = '1';
    }
    // Set default / restored preset dates on first load
    if (!$('rptStartDate')?.value) {
      let saved = 'mtd';
      try { saved = localStorage.getItem(PRESET_KEY) || 'mtd'; } catch (_) {}
      setPreset(saved);
      return; // setPreset → load again
    }

    // Show loading state on stat cells
    ['rptStatLiability','rptStatAgents','rptStatDischarged','rptStatForfeitures',
     'rptStatCompliance','rptStatPOA','rptStatVoided','rptStatExpired'].forEach(id => {
      const el = $(id); if (el) el.innerHTML = '<span class="rpt-loading-dot"></span>';
    });

    const [liab, agents, dis, forf, comp, poa, void_, exp, recent] = await Promise.all([
      _fetch('surety-liability'), _fetch('agent-production'), _fetch('discharged'),
      _fetch('forfeitures'), _fetch('check-in-compliance'), _fetch('poa-inventory'),
      _fetch('voided-powers'), _fetch('expired-powers'),
      _fetch('generated'),
    ]);
    _renderRecentReports(recent);

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
        <div class="rpt-summary-value ${item.color||''}">${item.html ? item.value : escHtml(item.value)}</div>
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
    if ($('rptExportXLSXBtn')) $('rptExportXLSXBtn').style.display = 'inline-flex';
    if ($('rptCopySummaryBtn')) $('rptCopySummaryBtn').style.display = 'inline-flex';
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
      const detail = data.hint ? ` — ${data.hint}` : '';
      $('rptTableWrap').innerHTML = `<div class="rpt-error">⚠️ ${escHtml(data.error||'Failed to load report')}${escHtml(detail)}</div>`;
      return;
    }

    // Graceful date-window diagnostics from the API (clamp / swap / invalid)
    if (Array.isArray(data.warnings) && data.warnings.length) {
      toast(data.warnings[0], 'info');
      if (data.warnings.length > 1) {
        console.info('[SLReports] date window warnings:', data.warnings);
      }
    }
    _showQualityBanner(data);

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

  function _fmtPct(p) {
    if (p == null || isNaN(p)) return '';
    const sign = p > 0 ? '+' : '';
    const cls = p > 0 ? 'rpt-val-green' : (p < 0 ? 'rpt-val-red' : '');
    return ` <small class="${cls}" style="font-weight:600">(${sign}${p}%)</small>`;
  }

  function _renderLiability(data) {
    const gt = data.grand_totals || {};
    const cmp = data.comparison || {};
    _renderSummary([
      { label: 'Total Liability',    value: escHtml(money(gt.total_bond_amount||0)) + _fmtPct(cmp.liability_pct_change),  color: 'rpt-val-blue', html: true },
      { label: 'Premium to Collect', value: escHtml(money(gt.total_premium||0)) + _fmtPct(cmp.premium_pct_change),      color: 'rpt-val-green', html: true },
      { label: 'Surety Owed (Insurer)', value: money(gt.total_surety_owed||0), color: 'rpt-val-gold'  },
      { label: 'BUF (5%)',           value: money(gt.total_buf_owed||gt.total_buf||0), color: 'rpt-val-cyan' },
      { label: 'Agent Retains',      value: money(gt.total_agent_retains||0),color: 'rpt-val-purple'},
      { label: 'Bond Count',         value: escHtml(String(gt.total_bonds||0)) + _fmtPct(cmp.bonds_pct_change), html: true },
    ]);
    // Prior-period strip (SuiteCRM / Salesforce reporting pattern)
    if (cmp.prior_start) {
      const strip = $('rptSummaryStrip');
      if (strip) {
        const note = document.createElement('div');
        note.className = 'rpt-prior-note';
        note.style.cssText = 'width:100%;font-size:11px;color:var(--muted);margin-top:6px';
        note.textContent = `vs prior ${cmp.prior_start} → ${cmp.prior_end}: ${cmp.prior_bonds||0} bonds · ${money(cmp.prior_bond_amount||0)} liability · ${money(cmp.prior_premium||0)} premium`;
        // append after summary render fills strip — schedule next tick
        setTimeout(() => {
          if ($('rptSummaryStrip') && !$('rptPriorNote')) {
            note.id = 'rptPriorNote';
            $('rptSummaryStrip').appendChild(note);
          }
        }, 0);
      }
    }
    const sureties = data.sureties || [];
    if (sureties.length) {
      _renderChart(
        sureties.map(s => s.surety),
        [{ label: 'Bond Amount', data: sureties.map(s => s.total_bond_amount||0) }],
        'bar'
      );
    }
    const headers = ['Surety','Bonds','Bond Liability','Premium to Collect','Surety Owed','BUF','Agent Retains'];
    const rows = sureties.map(s => [
      `<strong>${escHtml(s.surety||'—')}</strong>`,
      s.bond_count||0,
      money(s.total_bond_amount||0),
      money(s.total_premium||0),
      money(s.total_surety_owed||0),
      money(s.total_buf_owed||s.total_buf||0),
      money(s.total_agent_retains||0),
    ]);
    _renderTable(headers, rows);

    // Render Itemized Register with Audience Toggles & Total Row
    const wrap = $('rptTableWrap');
    if (!wrap) return;

    let itemRows = [];
    let idx = 0;

    // Flatten all surety groups and re-sort chronologically (oldest bond first)
    // so the register reads exactly like the official surety workbook.
    const allBonds = [];
    sureties.forEach(s => (s.bonds || []).forEach(b => allBonds.push({ ...b, _suretyGroup: s.surety })));
    const orderedBonds = sortBondsOldestFirst(allBonds);

    orderedBonds.forEach(b => {
      {
        const s = { surety: b._suretyGroup };
        idx++;
        const poa = b.poa_number || b.poa_full || '—';
        const def = b.defendant_name || `${b.defendant_first_name || ''} ${b.defendant_last_name || ''}`.trim() || '—';
        const dt = _fmtBondDate(b.bond_date);
        const liab = Number(b.bond_amount || 0);
        const gross = Number(b.premium || b.gross_premium || 0);
        const suretyOwed = Number(b.surety_owed || 0);
        const bufOwed = Number(b.buf_owed || b.buf || 0);
        const agentRetains = Number(b.agent_retains || 0);
        const st = (b.status || 'active').toLowerCase();
        const cty = b.county || '';
        const searchBlob = `${poa} ${def} ${cty} ${st} ${b.case_number || ''}`.toLowerCase();

        const suretyChipCls = (s.surety === 'OSI' || String(b.surety_id).toLowerCase() === 'osi') ? 'inv-chip-osi' : 'inv-chip-palm';
        const suretyIcon = (s.surety === 'OSI' || String(b.surety_id).toLowerCase() === 'osi') ? '🛡️ OSI' : '🌴 PSC';

        itemRows.push(`<tr class="rpt-item-row" data-idx="${idx}" data-liab="${liab}" data-gross="${gross}" data-surety="${suretyOwed}" data-buf="${bufOwed}" data-agent="${agentRetains}" data-search="${escHtml(searchBlob)}" data-bond-date="${escHtml(b.bond_date || '')}">
          <td style="text-align:center"><input type="checkbox" class="rpt-row-cb" checked onchange="SLReports.recalcLiabilityTotals()"></td>
          <td class="rpt-col-poa"><span class="mono" style="font-size:11px;font-weight:600">${escHtml(poa)}</span></td>
          <td class="rpt-col-def"><strong>${escHtml(def)}</strong>${cty ? `<br><small style="color:var(--muted)">${escHtml(cty)}</small>` : ''}</td>
          <td class="rpt-col-date" data-raw-date="${escHtml(b.bond_date || '')}">${escHtml(dt)}</td>
          <td class="rpt-col-surety"><span class="inv-surety-chip ${suretyChipCls}" style="font-size:10px;padding:2px 6px">${suretyIcon}</span></td>
          <td class="rpt-col-status"><span class="rpt-status-badge rpt-status-${escHtml(st)}">${escHtml(st || '—')}</span></td>
          <td class="rpt-col-liab" style="text-align:right;font-weight:600">${money(liab)}</td>
          <td class="rpt-col-gross" style="text-align:right;color:#34d399">${money(gross)}</td>
          <td class="rpt-col-surety-owed" style="text-align:right;color:#fcd34d">${money(suretyOwed)}</td>
          <td class="rpt-col-buf" style="text-align:right;color:#38bdf8">${money(bufOwed)}</td>
          <td class="rpt-col-agent" style="text-align:right;color:#c084fc">${money(agentRetains)}</td>
        </tr>`);
      }
    });

    if (itemRows.length > 0) {
      const itemizedHtml = `
        <div style="margin-top:28px;border-top:1px solid rgba(255,255,255,0.1);padding-top:20px">
          <!-- Audience / Column Customization Bar -->
          <div style="background:rgba(15,23,42,0.6);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:14px 18px;margin-bottom:16px">
            <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:12px">
              <div style="font-size:13px;font-weight:700;color:var(--text);display:flex;align-items:center;gap:6px">
                <span>⚙️ Report Customization & Audience Views</span>
                <span style="font-size:11px;font-weight:500;color:#34d399;background:rgba(52,211,153,.12);padding:2px 8px;border-radius:999px">Oldest → newest</span>
              </div>
              <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                <input type="search" id="rptRegisterSearch" class="rpt-input" placeholder="Filter POA / defendant / county…" oninput="SLReports.filterLiabilityRegister(this.value)" style="min-width:200px;font-size:12px;padding:4px 10px" />
                <span style="font-size:11px;color:var(--muted)">Presets:</span>
                <button type="button" class="inv-btn" onclick="SLReports.applyLiabilityPreset('full')" style="font-size:11px;padding:3px 8px">💼 Full Audit</button>
                <button type="button" class="inv-btn" onclick="SLReports.applyLiabilityPreset('insurer')" style="font-size:11px;padding:3px 8px;background:rgba(56,189,248,0.15);color:#38bdf8;border-color:rgba(56,189,248,0.3)">🛡️ Insurer View</button>
                <button type="button" class="inv-btn" onclick="SLReports.applyLiabilityPreset('summary')" style="font-size:11px;padding:3px 8px">📄 Basic View</button>
              </div>
            </div>

            <!-- Column Checkboxes -->
            <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;font-size:12px;color:var(--text-secondary,#cbd5e1)">
              <span style="font-size:11px;color:var(--muted);font-weight:600">Toggle Columns:</span>
              <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="checkbox" id="col_poa" checked onchange="SLReports.toggleLiabilityCol('rpt-col-poa', this.checked)"> POA #</label>
              <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="checkbox" id="col_def" checked onchange="SLReports.toggleLiabilityCol('rpt-col-def', this.checked)"> Defendant</label>
              <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="checkbox" id="col_date" checked onchange="SLReports.toggleLiabilityCol('rpt-col-date', this.checked)"> Date</label>
              <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="checkbox" id="col_surety" checked onchange="SLReports.toggleLiabilityCol('rpt-col-surety', this.checked)"> Surety</label>
              <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="checkbox" id="col_status" checked onchange="SLReports.toggleLiabilityCol('rpt-col-status', this.checked)"> Status</label>
              <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="checkbox" id="col_liab" checked onchange="SLReports.toggleLiabilityCol('rpt-col-liab', this.checked)"> Liability</label>
              <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="checkbox" id="col_gross" checked onchange="SLReports.toggleLiabilityCol('rpt-col-gross', this.checked)"> Premium (Collect)</label>
              <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="checkbox" id="col_surety_owed" checked onchange="SLReports.toggleLiabilityCol('rpt-col-surety-owed', this.checked)"> Premium (Insurer)</label>
              <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="checkbox" id="col_buf" checked onchange="SLReports.toggleLiabilityCol('rpt-col-buf', this.checked)"> BUF (5%)</label>
              <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="checkbox" id="col_agent" checked onchange="SLReports.toggleLiabilityCol('rpt-col-agent', this.checked)"> Agent Retains</label>
            </div>
          </div>

          <h4 style="margin:0 0 14px 0;font-size:14px;color:var(--text);display:flex;align-items:center;justify-content:space-between">
            <div style="display:flex;align-items:center;gap:8px">
              <span>📋 Itemized Bond Liability Register</span>
              <span id="rptSelectedCount" style="font-size:11px;color:#34d399;font-weight:600">(${itemRows.length} of ${itemRows.length} bonds included)</span>
            </div>
          </h4>

          <table class="rpt-table" id="rptItemizedTable">
            <thead>
              <tr>
                <th style="width:36px;text-align:center"><input type="checkbox" id="rptSelectAllRows" checked onchange="SLReports.toggleAllLiabilityRows(this.checked)" title="Select / Deselect all rows"></th>
                <th class="rpt-col-poa">POA #</th>
                <th class="rpt-col-def">Defendant Name</th>
                <th class="rpt-col-date">Bond Date</th>
                <th class="rpt-col-surety">Surety</th>
                <th class="rpt-col-status">Status</th>
                <th class="rpt-col-liab" style="text-align:right">Bond Liability</th>
                <th class="rpt-col-gross" style="text-align:right">Premium to Collect</th>
                <th class="rpt-col-surety-owed" style="text-align:right">Premium to Insurer</th>
                <th class="rpt-col-buf" style="text-align:right">BUF (5%)</th>
                <th class="rpt-col-agent" style="text-align:right">Agent Retains</th>
              </tr>
            </thead>
            <tbody>
              ${itemRows.join('')}
              <tr style="height:14px;background:transparent"><td colspan="11" style="border:none"></td></tr>
              <tr style="background:rgba(15,23,42,0.95);font-weight:700;border-top:2px solid #10b981;border-bottom:2px solid #10b981" id="rptTotalsRow">
                <td colspan="6" style="text-align:right;padding:12px;color:#f8fafc;letter-spacing:0.5px">TOTALS</td>
                <td class="rpt-col-liab" id="totCellLiab" style="text-align:right;padding:12px;color:#60a5fa">$0.00</td>
                <td class="rpt-col-gross" id="totCellGross" style="text-align:right;padding:12px;color:#34d399">$0.00</td>
                <td class="rpt-col-surety-owed" id="totCellSurety" style="text-align:right;padding:12px;color:#fcd34d">$0.00</td>
                <td class="rpt-col-buf" id="totCellBuf" style="text-align:right;padding:12px;color:#38bdf8">$0.00</td>
                <td class="rpt-col-agent" id="totCellAgent" style="text-align:right;padding:12px;color:#c084fc">$0.00</td>
              </tr>
            </tbody>
          </table>
        </div>
      `;
      wrap.innerHTML += itemizedHtml;
      recalcLiabilityTotals();
    }
  }

  function recalcLiabilityTotals() {
    const rows = document.querySelectorAll('.rpt-item-row');
    let totLiab = 0, totGross = 0, totSurety = 0, totBuf = 0, totAgent = 0;
    let checkedCount = 0;

    rows.forEach(r => {
      const cb = r.querySelector('.rpt-row-cb');
      if (cb && cb.checked) {
        checkedCount++;
        r.style.opacity = '1';
        totLiab += parseFloat(r.dataset.liab || 0);
        totGross += parseFloat(r.dataset.gross || 0);
        totSurety += parseFloat(r.dataset.surety || 0);
        totBuf += parseFloat(r.dataset.buf || 0);
        totAgent += parseFloat(r.dataset.agent || 0);
      } else {
        r.style.opacity = '0.35';
      }
    });

    const cntEl = $('rptSelectedCount');
    if (cntEl) cntEl.textContent = `(${checkedCount} of ${rows.length} bonds included)`;

    const setCell = (id, val) => { const el = $(id); if (el) el.textContent = money(val); };
    setCell('totCellLiab', totLiab);
    setCell('totCellGross', totGross);
    setCell('totCellSurety', totSurety);
    setCell('totCellBuf', totBuf);
    setCell('totCellAgent', totAgent);
  }

  function toggleAllLiabilityRows(checked) {
    document.querySelectorAll('.rpt-row-cb').forEach(cb => { cb.checked = checked; });
    recalcLiabilityTotals();
  }

  function toggleLiabilityCol(clsName, show) {
    document.querySelectorAll('.' + clsName).forEach(el => {
      el.style.display = show ? '' : 'none';
    });
  }

  function applyLiabilityPreset(preset) {
    const colMap = {
      full:    { col_poa: true, col_def: true, col_date: true, col_surety: true, col_status: true, col_liab: true, col_gross: true, col_surety_owed: true, col_buf: true, col_agent: true },
      insurer: { col_poa: true, col_def: true, col_date: true, col_surety: true, col_status: false, col_liab: true, col_gross: true, col_surety_owed: true, col_buf: true, col_agent: false },
      summary: { col_poa: true, col_def: true, col_date: true, col_surety: true, col_status: false, col_liab: true, col_gross: true, col_surety_owed: false, col_buf: false, col_agent: false },
    }[preset] || {};

    Object.entries(colMap).forEach(([id, show]) => {
      const cb = $(id);
      if (cb) { cb.checked = show; }
      const clsName = id.replace('col_', 'rpt-col-').replace(/_/g, '-');
      toggleLiabilityCol(clsName, show);
    });
  }

  function filterLiabilityRegister(q) {
    const needle = String(q || '').trim().toLowerCase();
    document.querySelectorAll('.rpt-item-row').forEach(tr => {
      const hay = tr.getAttribute('data-search') || tr.textContent.toLowerCase();
      const show = !needle || hay.includes(needle);
      tr.style.display = show ? '' : 'none';
      if (!show) {
        const cb = tr.querySelector('.rpt-row-cb');
        if (cb) cb.checked = false;
      }
    });
    recalcLiabilityTotals();
  }

  async function _downloadOneXlsx(suretyCode) {
    const url = `${API}/api/reports/bond-report.xlsx${_qs({ surety: suretyCode })}`;
    const r = await fetch(url, { credentials: 'same-origin' });
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { const j = await r.json(); msg = j.error || msg; } catch (_) {}
      throw new Error(msg);
    }
    const blob = await r.blob();
    const cd = r.headers.get('Content-Disposition') || '';
    const m = /filename="?([^"]+)"?/.exec(cd);
    const fname = m ? m[1] : `Shamrock_${suretyCode}_Bond_Report.xlsx`;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = fname;
    a.click();
    URL.revokeObjectURL(a.href);
    return fname;
  }

  async function exportXLSX() {
    const raw = ($('rptSuretyFilter')?.value || '').trim().toUpperCase();
    // One workbook per surety is the official submission format
    const sureties = !raw
      ? ['OSI', 'PALMETTO']
      : [(raw.includes('PALM') || raw === 'PSC') ? 'PALMETTO' : 'OSI'];
    toast(`Building official XLSX (${sureties.join(' + ')}) — oldest → newest…`, 'info');
    try {
      for (const s of sureties) {
        await _downloadOneXlsx(s);
      }
      toast(
        sureties.length > 1
          ? 'OSI + Palmetto official workbooks downloaded'
          : 'Official XLSX downloaded — sorted oldest → newest',
        'success'
      );
    } catch (e) {
      toast('XLSX export failed: ' + (e.message || e), 'error');
    }
  }

  function _renderRecentReports(data) {
    const el = $('rptRecentReports');
    if (!el) return;
    const reports = (data && data.success && data.reports) ? data.reports : [];
    if (!reports.length) {
      el.style.display = 'none';
      el.innerHTML = '';
      return;
    }
    el.style.display = 'block';
    el.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;flex-wrap:wrap;gap:8px">
        <div style="font-size:13px;font-weight:700;color:var(--text)">📁 Recent official reports</div>
        <div style="font-size:11px;color:var(--muted)">Re-download archived XLSX · sorted oldest→newest at generate time</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:6px">
        ${reports.slice(0, 8).map(r => {
          const when = r.created_at ? new Date(r.created_at).toLocaleString() : '—';
          const label = `${escHtml(r.surety || '—')} · ${escHtml(r.filename || r.report_type || 'report')} · ${escHtml(when)}`;
          const rows = r.active_rows != null ? `${r.active_rows} rows` : '';
          return `<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 12px;background:rgba(15,23,42,.5);border:1px solid rgba(255,255,255,.06);border-radius:8px;font-size:12px">
            <div style="min-width:0">
              <div style="color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${label}</div>
              <div style="color:var(--muted);font-size:11px">${escHtml(rows)}${r.start_date ? ` · ${escHtml(r.start_date)}→${escHtml(r.end_date || '…')}` : ''}</div>
            </div>
            <button type="button" class="inv-btn" style="font-size:11px;padding:3px 8px;flex-shrink:0" onclick="SLReports.downloadGenerated('${escHtml(r.id)}')">⬇ XLSX</button>
          </div>`;
        }).join('')}
      </div>`;
  }

  async function downloadGenerated(id) {
    if (!id) return;
    toast('Fetching archived report…', 'info');
    try {
      const r = await fetch(`${API}/api/reports/generated/${encodeURIComponent(id)}/download`, { credentials: 'same-origin' });
      if (!r.ok) {
        let msg = `HTTP ${r.status}`;
        try { const j = await r.json(); msg = j.error || msg; } catch (_) {}
        toast('Download failed: ' + msg, 'error');
        return;
      }
      const blob = await r.blob();
      const cd = r.headers.get('Content-Disposition') || '';
      const m = /filename="?([^"]+)"?/.exec(cd);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = m ? m[1] : 'Shamrock_Bond_Report.xlsx';
      a.click();
      URL.revokeObjectURL(a.href);
      toast('Archived report downloaded', 'success');
    } catch (e) {
      toast('Download failed: ' + (e.message || e), 'error');
    }
  }

  async function copySummary() {
    const d = _currentData || {};
    const gt = d.grand_totals || {};
    const q = d.data_quality || {};
    const { label } = _presetDates(_currentPreset);
    const lines = [
      'Shamrock Bail Bonds — Report Summary',
      `Report: ${_currentReport || '—'}`,
      `Range: ${label}`,
      `Sort: oldest bond written → newest`,
      `Scope: ${d.status_scope || _statusScope()}`,
      `Bonds: ${gt.total_bonds ?? d.count ?? q.row_count ?? '—'}`,
      `Liability: ${money(gt.total_bond_amount || d.total_bond_amount || d.total_liability || 0)}`,
      `Premium: ${money(gt.total_premium || d.total_premium || 0)}`,
      `Surety owed: ${money(gt.total_surety_owed || 0)}`,
      `BUF: ${money(gt.total_buf_owed || 0)}`,
      `Quality: ${q.quality_score != null ? q.quality_score + '%' : '—'}`,
      `Generated: ${new Date().toLocaleString()}`,
      'CONFIDENTIAL — Internal / surety use only',
    ];
    try {
      await navigator.clipboard.writeText(lines.join('\n'));
      toast('Summary copied to clipboard', 'success');
    } catch (_) {
      toast('Could not copy — browser blocked clipboard', 'warning');
    }
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
    const bonds = sortBondsOldestFirst(data.bonds || []);
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
    const bonds = sortBondsOldestFirst(data.bonds || []);
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
    // Same surety ordering as print/PDF: oldest bond written first
    resortForOutput();
    const itemized = document.querySelector('#rptItemizedTable');
    const tables = itemized ? [itemized] : Array.from(document.querySelectorAll('#rptTableWrap .rpt-table'));
    if (!tables.length) { toast('No data to export','warning'); return; }
    const rows = [];
    tables.forEach(table => {
      table.querySelectorAll('tr').forEach(tr => {
        if (tr.style.display === 'none' || tr.style.opacity === '0.35') return;
        const cells = [];
        tr.querySelectorAll('th,td').forEach(td => {
          if (td.style.display === 'none' || td.querySelector('input[type="checkbox"]')) return;
          let text = td.innerText.replace(/\n/g,' ').replace(/,/g,';').trim();
          cells.push(`"${text}"`);
        });
        if (cells.length) rows.push(cells.join(','));
      });
    });
    if (!rows.length) { toast('No visible data to export','warning'); return; }
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
  /* ── Re-sort visible report tables oldest → newest before print/save ──
     Walks every rendered report table, finds its date column, and reorders
     <tr> rows chronologically in the DOM. Idempotent and graceful: tables
     without a date column, totals rows, and undated rows are preserved in
     place / at the end. Returns a diagnostic summary object. */
  function resortForOutput() {
    const summary = { tables: 0, sorted: 0, undated: 0, skipped: 0, errors: [] };
    try {
      const tables = document.querySelectorAll('#rptTableWrap table');
      summary.tables = tables.length;
      tables.forEach(table => {
        try {
          const ths = Array.from(table.querySelectorAll('thead th'));
          const dateIdx = ths.findIndex(th => /date/i.test(th.textContent || ''));
          if (dateIdx < 0) { summary.skipped++; return; }
          const tbody = table.querySelector('tbody');
          if (!tbody) { summary.skipped++; return; }
          const rows = Array.from(tbody.querySelectorAll('tr'));
          const dataRows = [], tailRows = [];
          rows.forEach(tr => {
            // Keep totals / spacer rows pinned to the bottom
            if (tr.id === 'rptTotalsRow' || /\bTOTALS\b/.test(tr.textContent || '') || !tr.querySelector('td')) {
              tailRows.push(tr);
            } else {
              dataRows.push(tr);
            }
          });
          const keyOf = tr => {
            // Prefer machine-readable date on the row / cell (MM/DD/YYYY display still sorts correctly)
            const raw =
              tr.getAttribute('data-bond-date') ||
              tr.querySelector('[data-raw-date]')?.getAttribute('data-raw-date') ||
              '';
            if (raw) {
              const t = Date.parse(String(raw).slice(0, 19));
              if (!isNaN(t)) return t;
            }
            const cells = tr.querySelectorAll('td');
            const cell = cells[dateIdx];
            const t = cell ? Date.parse(String(cell.textContent || '').trim().slice(0, 24)) : NaN;
            if (isNaN(t)) { summary.undated++; return Number.MAX_SAFE_INTEGER; }
            return t;
          };
          dataRows
            .map(tr => ({ tr, k: keyOf(tr) }))
            .sort((a, b) => a.k - b.k)
            .forEach(({ tr }) => tbody.appendChild(tr));
          tailRows.forEach(tr => tbody.appendChild(tr));
          summary.sorted++;
        } catch (e) {
          summary.errors.push(String(e && e.message || e));
        }
      });
      // Renumber visible Count/index cells after reorder (first cell if numeric)
      document.querySelectorAll('#rptTableWrap table tbody').forEach(tb => {
        let n = 0;
        tb.querySelectorAll('tr').forEach(tr => {
          const first = tr.querySelector('td');
          if (first && /^\d+$/.test((first.textContent || '').trim())) first.textContent = String(++n);
        });
      });
    } catch (e) {
      summary.errors.push(String(e && e.message || e));
      console.warn('[SLReports] resortForOutput failed:', e);
    }
    if (summary.errors.length) {
      toast(`Report re-sort hit ${summary.errors.length} issue(s) — original order kept where needed. See console for details.`, 'warning');
      console.warn('[SLReports] resort diagnostics:', summary);
    }
    return summary;
  }

  function exportPDF() {
    const panel = $('rptResultsPanel');
    if (!panel) return;
    // Surety requirement: bonds print oldest → newest, each row keeping its
    // power #, bond date, and premium details.
    resortForOutput();
    const title = $('rptResultsTitle')?.textContent || 'Report';
    const range = $('rptRangeLabel')?.textContent   || '';
    const scope = _statusScope();
    const q = (_currentData && _currentData.data_quality) || {};
    const w = window.open('','_blank','width=900,height=700');
    if (!w) { toast('Pop-up blocked — allow pop-ups to print/export', 'warning'); return; }
    w.document.write(`<!DOCTYPE html><html><head>
      <title>${title}</title>
      <style>
        body{font-family:system-ui,sans-serif;padding:24px;color:#1e293b;background:#fff}
        .agency{font-size:11px;color:#0B3D2E;font-weight:700;letter-spacing:.3px;text-transform:uppercase}
        h1{font-size:20px;margin:6px 0 4px}
        .meta{font-size:12px;color:#64748b;margin-bottom:8px}
        .badge{display:inline-block;background:#ecfdf5;color:#065f46;border:1px solid #a7f3d0;border-radius:999px;padding:2px 10px;font-size:11px;font-weight:600;margin:0 6px 12px 0}
        table{width:100%;border-collapse:collapse;font-size:11px}
        th{background:#0f172a;color:#fff;padding:8px 10px;text-align:left;border-bottom:2px solid #e2e8f0;font-weight:700;text-transform:uppercase;font-size:9px;letter-spacing:.5px}
        td{padding:6px 10px;border-bottom:1px solid #e2e8f0}
        tr:nth-child(even) td{background:#f8fafc}
        .summary{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px}
        .s-item,.rpt-summary-item{border:1px solid #e2e8f0;border-radius:8px;padding:12px 18px;min-width:100px;text-align:center}
        .s-val,.rpt-summary-value{font-size:18px;font-weight:800}
        .s-lbl,.rpt-summary-label{font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px}
        .footer{margin-top:18px;font-size:10px;color:#94a3b8;border-top:1px solid #e2e8f0;padding-top:10px}
        input[type="checkbox"], button, .inv-btn, .rpt-row-cb, #rptSelectAllRows, #rptRegisterSearch,
        th:has(input), td:has(input), 
        div[style*="background:rgba(15,23,42"] { background: none !important; border: none !important; color: #1e293b !important; padding: 0 !important; }
        @media print{body{padding:12px} @page{margin:12mm;size:landscape}}
      </style></head><body>
      <div class="agency">Shamrock Bail Bonds · 1528 Broadway Ft. Myers, FL 33901 · Agent Lic. #P139768</div>
      <h1>☘️ ${title}</h1>
      <div class="meta">Range: ${range} · Scope: ${scope} · Generated ${new Date().toLocaleString()}</div>
      <span class="badge">Sorted oldest bond written → newest</span>
      ${q.bond_date_min ? `<span class="badge">Data ${q.bond_date_min} → ${q.bond_date_max || '—'}</span>` : ''}
      ${q.quality_score != null ? `<span class="badge">Quality ${q.quality_score}%</span>` : ''}
      ${$('rptSummaryStrip')?.innerHTML ? `<div class="summary">${$('rptSummaryStrip').innerHTML}</div>` : ''}
      ${$('rptTableWrap')?.innerHTML || '<p>No data</p>'}
      <div class="footer">CONFIDENTIAL — Internal / surety submission. Every line item retains power #, bond date, and premium details. Shamrock Super CRM.</div>
      </body></html>`);
    w.document.close();
    setTimeout(() => { w.print(); }, 500);
  }

  /* ── Print report ──────────────────────────────────────────────────── */
  function printReport() { exportPDF(); }

  /* ── Auto re-sort on print / save keystrokes ──
     ⌘S / Ctrl+S — re-sorts the on-screen report oldest → newest and opens
                    the printable copy (macOS "save" muscle memory covered).
     ⌘P / Ctrl+P — re-sorts in place, then lets the native print dialog open.
     window.onbeforeprint — safety net for menu-driven File ▸ Print. */
  function _reportsTabVisible() {
    const tab = $('tabReports');
    return !!(tab && tab.offsetParent !== null);
  }

  document.addEventListener('keydown', function (e) {
    if (!(e.metaKey || e.ctrlKey)) return;
    const k = String(e.key || '').toLowerCase();
    if (k !== 's' && k !== 'p') return;
    if (!_reportsTabVisible() || !_currentReport) return;
    // Don't steal Cmd/Ctrl+S while typing in form fields (schedule email, dates, etc.)
    const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || (e.target && e.target.isContentEditable)) {
      return;
    }
    try {
      if (k === 's') {
        e.preventDefault();
        // exportPDF() already re-sorts; avoid double toast/work
        toast('Report re-sorted oldest → newest — opening printable copy…', 'info');
        exportPDF();
      } else {
        // Let the browser print dialog open, but sort the DOM first
        resortForOutput();
      }
    } catch (err) {
      console.error('[SLReports] print/save hotkey handler failed:', err);
      toast('Could not auto-sort before output: ' + (err && err.message || err), 'error');
    }
  });

  window.addEventListener('beforeprint', function () {
    try {
      if (_reportsTabVisible() && _currentReport) resortForOutput();
    } catch (err) {
      console.warn('[SLReports] beforeprint resort failed:', err);
    }
  });

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
    load, runAll, generate, onDateChange, onScopeChange, setPreset,
    exportCSV, exportPDF, exportXLSX, copySummary, printReport, closeResults,
    scheduleReport, closeSchedule, saveSchedule,
    // Chronological output ordering (oldest bond → newest)
    resortForOutput, sortBondsOldestFirst,
    // Liability report customization & toggles
    recalcLiabilityTotals, toggleAllLiabilityRows, toggleLiabilityCol, applyLiabilityPreset,
    filterLiabilityRegister, downloadGenerated,
  };
})();

