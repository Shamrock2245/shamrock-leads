/**
 * ShamrockLeads — Compliance & Agent Analytics Frontend Module
 * ═══════════════════════════════════════════════════════════════
 * Wires FLDFS compliance reports, Agent Workforce analytics,
 * and POA depletion forecasts into the Reports tab.
 *
 * Dependencies: SLReports (sl-reports.js / sl-reports-ui.js)
 * @version 1.0
 */
window.SLCompliance = (() => {
  'use strict';

  const API = window.SL?.apiBase || '';

  // ── Helpers ─────────────────────────────────────────────────────────────
  async function _fetch(url) {
    const resp = await fetch(`${API}${url}`, { credentials: 'include' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  function _fmt(n, decimals = 0) {
    if (n == null) return '—';
    return Number(n).toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  }

  function _fmtCurrency(n) {
    if (n == null) return '—';
    return '$' + _fmt(n, 2);
  }

  function _fmtPct(n) {
    if (n == null) return '—';
    return n.toFixed(1) + '%';
  }

  function _statusBadge(status) {
    const colors = {
      active: '#10b981',
      idle: '#6b7280',
      critical: '#ef4444',
      warning: '#f59e0b',
      ok: '#10b981',
    };
    const bg = colors[status] || '#6b7280';
    return `<span style="display:inline-block;padding:2px 10px;border-radius:12px;
      font-size:11px;font-weight:600;color:#fff;background:${bg};text-transform:uppercase">
      ${status}</span>`;
  }

  // ── Show results in the shared report panel ─────────────────────────────
  function _showInPanel(title, icon, html, count) {
    const panel = document.getElementById('rptResultsPanel');
    const grid = document.querySelector('.rpt-grid')?.closest('.panel');
    if (!panel || !grid) return;

    grid.style.display = 'none';
    panel.style.display = 'block';

    const titleEl = document.getElementById('rptResultsTitle');
    const iconEl = document.getElementById('rptResultsIcon');
    const countEl = document.getElementById('rptResultsCount');
    // Use rptTableWrap — the actual content container in the results panel
    const bodyEl = document.getElementById('rptTableWrap');

    if (titleEl) titleEl.textContent = title;
    if (iconEl) iconEl.textContent = icon;
    if (countEl) countEl.textContent = count || '';
    if (bodyEl) bodyEl.innerHTML = html;

    // Hide loading skeleton & empty state
    const skeleton = document.getElementById('rptLoadingSkeleton');
    const empty = document.getElementById('rptEmptyState');
    if (skeleton) skeleton.style.display = 'none';
    if (empty) empty.style.display = 'none';

    // Show export buttons
    const csvBtn = document.getElementById('rptExportCSVBtn');
    const pdfBtn = document.getElementById('rptExportPDFBtn');
    if (csvBtn) csvBtn.style.display = 'inline-flex';
    if (pdfBtn) pdfBtn.style.display = 'inline-flex';
  }

  // ═══════════════════════════════════════════════════════════════════════
  // FLDFS COMPLIANCE FILING
  // ═══════════════════════════════════════════════════════════════════════
  async function generateFLDFS() {
    const stat = document.getElementById('rptStatFLDFS');
    if (stat) stat.textContent = 'Generating…';

    try {
      const data = await _fetch('/api/compliance/full-filing');
      if (!data.success) throw new Error(data.error || 'Failed');

      const summary = data.monthly_summary || {};
      const agents = data.agent_commissions || {};
      const poa = data.poa_utilization || {};
      const forf = data.forfeiture_log || {};

      const totals = summary.totals || {};
      const sureties = summary.surety_breakdown || [];

      let html = `
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px">
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">Bonds Written</div>
            <div class="rpt-kpi-value">${_fmt(totals.bonds_written)}</div>
          </div>
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">Total Premium</div>
            <div class="rpt-kpi-value">${_fmtCurrency(totals.total_premium)}</div>
          </div>
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">Active Liability</div>
            <div class="rpt-kpi-value">${_fmtCurrency(totals.active_liability)}</div>
          </div>
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">Forfeiture Exposure</div>
            <div class="rpt-kpi-value" style="color:#ef4444">${_fmtCurrency(totals.forfeiture_exposure)}</div>
          </div>
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">BUF Owed</div>
            <div class="rpt-kpi-value">${_fmtCurrency(totals.total_buf_owed)}</div>
          </div>
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">Agent Retains</div>
            <div class="rpt-kpi-value" style="color:#10b981">${_fmtCurrency(totals.total_agent_retains)}</div>
          </div>
        </div>`;

      // Surety breakdown table
      if (sureties.length) {
        html += `<h3 style="margin:20px 0 12px;font-size:15px;color:var(--text-primary,#fff)">Surety Breakdown</h3>
        <div style="overflow-x:auto">
          <table class="rpt-table">
            <thead><tr>
              <th>Surety</th><th>Bonds</th><th>Bond Amount</th><th>Premium</th>
              <th>Surety Owed</th><th>BUF</th><th>Agent Retains</th>
            </tr></thead><tbody>`;
        for (const s of sureties) {
          html += `<tr>
            <td><strong>${s.surety}</strong></td>
            <td>${_fmt(s.bonds_written)}</td>
            <td>${_fmtCurrency(s.total_bond_amount)}</td>
            <td>${_fmtCurrency(s.total_premium)}</td>
            <td>${_fmtCurrency(s.total_surety_owed)}</td>
            <td>${_fmtCurrency(s.total_buf_owed)}</td>
            <td style="color:#10b981">${_fmtCurrency(s.total_agent_retains)}</td>
          </tr>`;
        }
        html += `</tbody></table></div>`;
      }

      // Agent commissions
      const agentList = agents.agents || [];
      if (agentList.length) {
        html += `<h3 style="margin:24px 0 12px;font-size:15px;color:var(--text-primary,#fff)">Agent 1099 Commissions</h3>
        <div style="overflow-x:auto">
          <table class="rpt-table">
            <thead><tr>
              <th>Agent</th><th>License</th><th>Bonds</th><th>Premium</th><th>Commission</th>
            </tr></thead><tbody>`;
        for (const a of agentList) {
          html += `<tr>
            <td><strong>${a.agent_name}</strong></td>
            <td>${a.license || '—'}</td>
            <td>${_fmt(a.bond_count)}</td>
            <td>${_fmtCurrency(a.total_premium)}</td>
            <td style="color:#10b981">${_fmtCurrency(a.total_commission)}</td>
          </tr>`;
        }
        html += `</tbody></table></div>`;
      }

      // Forfeiture log
      const forfEntries = forf.entries || [];
      if (forfEntries.length) {
        html += `<h3 style="margin:24px 0 12px;font-size:15px;color:var(--text-primary,#fff)">
          Estreature Log (${forfEntries.length} records · ${_fmtCurrency(forf.total_exposure)} exposure)</h3>
        <div style="overflow-x:auto">
          <table class="rpt-table">
            <thead><tr>
              <th>Defendant</th><th>County</th><th>Bond</th><th>Status</th><th>Days Out</th><th>Surety</th>
            </tr></thead><tbody>`;
        for (const e of forfEntries.slice(0, 50)) {
          html += `<tr>
            <td>${e.defendant_name || '—'}</td>
            <td>${e.county || '—'}</td>
            <td>${_fmtCurrency(e.bond_amount)}</td>
            <td>${_statusBadge(e.status)}</td>
            <td>${e.days_outstanding}</td>
            <td>${e.surety}</td>
          </tr>`;
        }
        html += `</tbody></table></div>`;
      }

      _showInPanel(
        'FLDFS Compliance Filing Package',
        '🏛️',
        html,
        `${summary.period || 'Current Period'} · ${_fmt(totals.bonds_written)} bonds`
      );
      if (stat) stat.textContent = `${_fmt(totals.bonds_written)} bonds`;
    } catch (err) {
      console.error('FLDFS report error:', err);
      if (stat) stat.textContent = 'Error';
      if (window.SL?.toast) SL.toast(`Compliance report failed: ${err.message}`, 'error');
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  // AGENT WORKFORCE ANALYTICS
  // ═══════════════════════════════════════════════════════════════════════
  async function generateAgentScorecard() {
    const stat = document.getElementById('rptStatWorkforce');
    if (stat) stat.textContent = 'Analyzing…';

    try {
      const data = await _fetch('/api/analytics/agent-performance?days=30');
      if (!data.success) throw new Error(data.error || 'Failed');

      const health = data.workforce_health || {};
      const agents = data.agents || [];

      let html = `
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:24px">
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">Total Agents</div>
            <div class="rpt-kpi-value">${health.total_agents || 0}</div>
          </div>
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">Active</div>
            <div class="rpt-kpi-value" style="color:#10b981">${health.active || 0}</div>
          </div>
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">Idle</div>
            <div class="rpt-kpi-value" style="color:#6b7280">${health.idle || 0}</div>
          </div>
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">Utilization</div>
            <div class="rpt-kpi-value">${_fmtPct(health.utilization_pct)}</div>
          </div>
        </div>`;

      // Agent cards grid
      html += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px">`;
      for (const a of agents) {
        const kpis = a.kpis || {};
        const kpiRows = Object.entries(kpis).map(([k, v]) => {
          const label = k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
            .replace('Pct', '%');
          const val = typeof v === 'number'
            ? (k.includes('pct') ? _fmtPct(v) : (k.includes('collected') ? _fmtCurrency(v) : _fmt(v)))
            : v;
          return `<div style="display:flex;justify-content:space-between;padding:4px 0;
            border-bottom:1px solid rgba(255,255,255,0.05)">
            <span style="color:var(--text-secondary,#9ca3af);font-size:12px">${label}</span>
            <span style="font-weight:600;font-size:13px">${val}</span>
          </div>`;
        }).join('');

        html += `
          <div style="background:var(--bg-card,#1e1e2e);border-radius:12px;padding:16px;
            border:1px solid rgba(255,255,255,0.06)">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
              <span style="font-size:24px">${a.icon || '🤖'}</span>
              <div>
                <div style="font-weight:600;font-size:14px">${a.name}</div>
                <div style="font-size:11px;color:var(--text-secondary,#9ca3af)">${a.role}</div>
              </div>
              <div style="margin-left:auto">${_statusBadge(a.status)}</div>
            </div>
            ${kpiRows}
          </div>`;
      }
      html += `</div>`;

      _showInPanel(
        'Digital Workforce Scorecard',
        '🤖',
        html,
        `${health.active}/${health.total_agents} active · 30-day window`
      );
      if (stat) stat.textContent = `${_fmtPct(health.utilization_pct)} util`;
    } catch (err) {
      console.error('Agent analytics error:', err);
      if (stat) stat.textContent = 'Error';
      if (window.SL?.toast) SL.toast(`Agent analytics failed: ${err.message}`, 'error');
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  // POA DEPLETION FORECAST
  // ═══════════════════════════════════════════════════════════════════════
  async function generatePOADepletion() {
    const stat = document.getElementById('rptStatDepletion');
    if (stat) stat.textContent = 'Forecasting…';

    try {
      const data = await _fetch('/api/compliance/poa-utilization');
      if (!data.success) throw new Error(data.error || 'Failed');

      const tiers = data.tiers || [];
      const velocity = data.daily_velocity || 0;

      let html = `
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:24px">
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">Daily Velocity</div>
            <div class="rpt-kpi-value">${velocity}/day</div>
          </div>
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">Month Bonds</div>
            <div class="rpt-kpi-value">${_fmt(data.month_bonds_written)}</div>
          </div>
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">Tiers Tracked</div>
            <div class="rpt-kpi-value">${tiers.length}</div>
          </div>
          <div class="rpt-kpi-card">
            <div class="rpt-kpi-label">Critical Tiers</div>
            <div class="rpt-kpi-value" style="color:${tiers.filter(t => t.depletion_risk === 'critical').length ? '#ef4444' : '#10b981'}">
              ${tiers.filter(t => t.depletion_risk === 'critical').length}
            </div>
          </div>
        </div>`;

      html += `<div style="overflow-x:auto">
        <table class="rpt-table">
          <thead><tr>
            <th>Surety</th><th>Prefix</th><th>Available</th><th>Assigned</th>
            <th>Voided</th><th>Utilization</th><th>Days to Depletion</th><th>Risk</th>
          </tr></thead><tbody>`;

      for (const t of tiers) {
        const riskColor = t.depletion_risk === 'critical' ? '#ef4444'
          : t.depletion_risk === 'warning' ? '#f59e0b' : '#10b981';
        html += `<tr>
          <td><strong>${(t.surety_id || '').toUpperCase()}</strong></td>
          <td>${t.poa_prefix}</td>
          <td>${_fmt(t.available)}</td>
          <td>${_fmt(t.assigned)}</td>
          <td>${_fmt(t.voided)}</td>
          <td>${_fmtPct(t.utilization_pct)}</td>
          <td style="font-weight:600;color:${riskColor}">${t.days_until_depleted === 999 ? '∞' : _fmt(t.days_until_depleted, 0) + 'd'}</td>
          <td>${_statusBadge(t.depletion_risk)}</td>
        </tr>`;
      }
      html += `</tbody></table></div>`;

      // Trigger backend alert check as well
      fetch(`${API}/api/poa/alert-check`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      }).catch(() => {});

      _showInPanel(
        'POA Depletion Forecast',
        '📉',
        html,
        `${tiers.length} tiers · ${velocity}/day velocity`
      );
      if (stat) stat.textContent = `${velocity}/day`;
    } catch (err) {
      console.error('POA depletion error:', err);
      if (stat) stat.textContent = 'Error';
      if (window.SL?.toast) SL.toast(`POA forecast failed: ${err.message}`, 'error');
    }
  }

  // ── Public API ─────────────────────────────────────────────────────────
  return {
    generateFLDFS,
    generateAgentScorecard,
    generatePOADepletion,
  };
})();
