/* ═══════════════════════════════════════════════════════════════════
   ShamrockLeads — OSINT Intelligence Module
   Admin-Only · Defendant & Indemnitor Deep Background Research
   Integrates: Maigret · Blackbird · Trape
   ═══════════════════════════════════════════════════════════════════ */
/* global SL */
(function () {
  'use strict';

  const API = window.API || '';
  const ADMIN_KEY = window.OSINT_ADMIN_KEY || '';

  // ── State ──────────────────────────────────────────────────────────
  let _reports = [];
  let _activeReport = null;
  let _pollTimer = null;
  let _toolStatus = null;
  let _accountFilter = 'all';

  // ── Public API ─────────────────────────────────────────────────────
  window.SLOSINT = {
    init,
    load,
    runScan,
    openReport,
    closeReport,
    createTrapeSession,
    filterAccounts,
    copyToClipboard,
  };

  // ── Helpers ────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);
  const toast = (msg, type) => { if (window.SL?.toast) SL.toast(msg, type); else console.log(msg); };
  const fmt = ts => ts ? new Date(ts).toLocaleString() : '—';
  const headers = () => {
    const h = { 'Content-Type': 'application/json' };
    if (ADMIN_KEY) h['X-Admin-Key'] = ADMIN_KEY;
    return h;
  };

  // ── Init ───────────────────────────────────────────────────────────
  async function init() {
    await _checkToolStatus();
    await load();
    _bindUI();
  }

  function _bindUI() {
    const scanBtn = $('osintScanBtn');
    if (scanBtn) scanBtn.addEventListener('click', runScan);
    const trapeBtn = $('osintTrapeBtn');
    if (trapeBtn) trapeBtn.addEventListener('click', createTrapeSession);
  }

  // ── Tool Status Check ──────────────────────────────────────────────
  async function _checkToolStatus() {
    try {
      const r = await fetch(`${API}/api/osint/status`, {
        headers: headers(),
        credentials: 'same-origin',
      });
      if (!r.ok) {
        const container = $('osintToolStatus');
        if (container) {
          container.innerHTML = `<span class="osint-tool-pill missing" title="HTTP ${r.status}">
            <span class="dot"></span>Status unavailable (${r.status})
          </span>`;
        }
        return;
      }
      _toolStatus = await r.json();
      _renderToolStatus();
      _syncScanButtonState();
    } catch (e) {
      console.warn('OSINT status check failed:', e);
    }
  }

  function _renderToolStatus() {
    const container = $('osintToolStatus');
    if (!container || !_toolStatus) return;
    const tools = [
      { key: 'maigret',   label: 'Maigret' },
      { key: 'blackbird', label: 'Blackbird' },
      { key: 'trape',     label: 'Trape' },
    ];
    const ready = !!_toolStatus.ready_for_scans;
    container.innerHTML = tools.map(t => {
      const info = _toolStatus[t.key] || {};
      const ok = info.available;
      const tip = info.error || info.version || info.path || info.note || '';
      return `<span class="osint-tool-pill ${ok ? 'ready' : 'missing'}" title="${_esc(tip)}">
        <span class="dot"></span>${t.label}${ok && info.version ? ` · ${info.version}` : ''}
      </span>`;
    }).join('') + (ready
      ? ''
      : `<span class="osint-tool-pill missing" title="Rebuild dashboard image with OSINT tools">
          <span class="dot"></span>Scans blocked — tools missing
        </span>`);
  }

  function _syncScanButtonState() {
    const btn = $('osintScanBtn');
    if (!btn || !_toolStatus) return;
    if (!_toolStatus.ready_for_scans) {
      btn.disabled = true;
      btn.title = 'Maigret and Blackbird are not installed on this server.';
    }
  }

  function _esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  // ── Load Reports ───────────────────────────────────────────────────
  async function load() {
    const list = $('osintReportList');
    if (!list) return;
    list.innerHTML = '<div class="osint-loading"><div class="osint-spinner"></div> Loading reports...</div>';
    try {
      const r = await fetch(`${API}/api/osint/reports?limit=30`, {
        headers: headers(),
        credentials: 'same-origin',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      _reports = d.reports || [];
      _renderReportList();
    } catch (e) {
      list.innerHTML = `<div class="osint-empty"><div class="osint-empty-icon">⚠️</div>Could not load reports: ${_esc(e.message)}</div>`;
    }
  }

  function _renderReportList() {
    const list = $('osintReportList');
    if (!list) return;
    if (!_reports.length) {
      list.innerHTML = '<div class="osint-empty"><div class="osint-empty-icon">🔍</div>No intelligence reports yet.<br>Select a defendant or indemnitor and run a scan.</div>';
      return;
    }
    list.innerHTML = _reports.map(r => _reportRowHTML(r)).join('');
  }

  function _reportRowHTML(r) {
    const isDefendant = r.subject_type === 'defendant';
    const icon = isDefendant ? '🔴' : '🟡';
    const typeLabel = isDefendant ? 'Defendant' : 'Indemnitor';
    const riskClass = _riskClass(r.osint_risk_score || 0);
    const isActive = _activeReport && _activeReport._id === r._id;

    const statusLabel = r.status || 'unknown';
    const acctLabel = (statusLabel === 'failed')
      ? (r.error ? 'tool error' : 'failed')
      : `${r.total_accounts_found || 0} accounts`;

    return `<div class="osint-report-row ${isActive ? 'active' : ''}" onclick="SLOSINT.openReport('${r._id}')">
      <div class="osint-report-icon ${r.subject_type}">${icon}</div>
      <div class="osint-report-meta">
        <div class="osint-report-name">${_esc(r.full_name || r.subject_id)}</div>
        <div class="osint-report-sub">${typeLabel} · ${acctLabel} · ${fmt(r.scan_completed_at || r.scan_started_at)}</div>
      </div>
      <div class="osint-risk-gauge ${riskClass}">${r.osint_risk_score || 0}</div>
      <span class="osint-status-badge ${statusLabel}">${statusLabel}</span>
    </div>`;
  }

  function _riskClass(score) {
    if (score >= 30) return 'critical';
    if (score >= 20) return 'high';
    if (score >= 10) return 'medium';
    return 'low';
  }

  // ── Run Scan ───────────────────────────────────────────────────────
  async function runScan() {
    const subjectType = $('osintSubjectType')?.value;
    const subjectId   = $('osintSubjectId')?.value?.trim();
    const fullName    = $('osintFullName')?.value?.trim();
    const usernames   = ($('osintUsernames')?.value || '').split(',').map(u => u.trim()).filter(Boolean);
    const email       = $('osintEmail')?.value?.trim();
    const deepScan    = $('osintDeepScan')?.checked || false;
    const runMaigret  = $('osintRunMaigret')?.checked !== false;
    const runBlackbird = $('osintRunBlackbird')?.checked !== false;
    const notes       = $('osintNotes')?.value?.trim();

    if (!subjectId) { toast('Subject ID is required.', 'error'); return; }
    if (!fullName && !usernames.length && !email) {
      toast('At least one identifier is required: name, username, or email.', 'error');
      return;
    }

    if (_toolStatus && !_toolStatus.ready_for_scans) {
      toast('OSINT tools are not installed on this server. Rebuild the dashboard image.', 'error');
      return;
    }

    const btn = $('osintScanBtn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spin">⟳</span> Scanning...'; }

    try {
      const r = await fetch(`${API}/api/osint/scan`, {
        method: 'POST',
        headers: headers(),
        credentials: 'same-origin',
        body: JSON.stringify({
          subject_type: subjectType,
          subject_id: subjectId,
          full_name: fullName || null,
          usernames,
          email: email || null,
          deep_scan: deepScan,
          run_maigret: runMaigret,
          run_blackbird: runBlackbird,
          notes: notes || null,
        }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = typeof d.detail === 'string' ? d.detail
          : (Array.isArray(d.detail) ? d.detail.map(x => x.msg || x).join('; ') : null);
        throw new Error(detail || d.error || `Scan failed (HTTP ${r.status})`);
      }

      toast(`Scan initiated. Report ID: ${d.report_id}`, 'success');
      await load();
      await openReport(d.report_id);
    } catch (e) {
      toast(`Scan error: ${e.message}`, 'error');
    } finally {
      if (btn) {
        btn.disabled = !(_toolStatus && _toolStatus.ready_for_scans);
        btn.innerHTML = '🔍 Run OSINT Scan';
        _syncScanButtonState();
      }
    }
  }

  // ── Open Report ────────────────────────────────────────────────────
  async function openReport(reportId) {
    // Stop any existing poll
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }

    const panel = $('osintDetailPanel');
    if (!panel) return;
    panel.innerHTML = '<div class="osint-loading"><div class="osint-spinner"></div> Loading report...</div>';
    panel.style.display = 'block';

    try {
      const r = await fetch(`${API}/api/osint/report/${reportId}`, {
        headers: headers(),
        credentials: 'same-origin',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const report = await r.json();
      _activeReport = report;
      _renderReport(report);
      _renderReportList();

      if (report.status === 'running' || report.status === 'pending') {
        _pollTimer = setInterval(() => _pollReport(reportId), 4000);
      }
    } catch (e) {
      panel.innerHTML = `<div class="osint-empty"><div class="osint-empty-icon">❌</div>Failed to load report: ${_esc(e.message)}</div>`;
    }
  }

  async function _pollReport(reportId) {
    try {
      const r = await fetch(`${API}/api/osint/report/${reportId}`, {
        headers: headers(),
        credentials: 'same-origin',
      });
      if (!r.ok) return;
      const report = await r.json();
      _activeReport = report;
      _renderReport(report);
      _renderReportList();

      if (report.status !== 'running' && report.status !== 'pending') {
        clearInterval(_pollTimer);
        _pollTimer = null;
        if (report.status === 'complete') {
          const n = report.total_accounts_found || 0;
          if (n === 0) {
            toast('OSINT scan finished — 0 accounts found (tools ran OK).', 'info');
          } else {
            toast(`OSINT scan complete — ${n} accounts found.`, 'success');
          }
        } else if (report.status === 'partial') {
          toast(`Partial OSINT results — ${report.total_accounts_found || 0} accounts. Check errors.`, 'error');
        } else if (report.status === 'failed') {
          toast(`OSINT scan FAILED: ${report.error || 'see report'}`, 'error');
        }
      }
    } catch (e) {
      console.warn('Poll error:', e);
    }
  }

  function closeReport() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    _activeReport = null;
    const panel = $('osintDetailPanel');
    if (panel) panel.style.display = 'none';
    _renderReportList();
  }

  // ── Render Report ──────────────────────────────────────────────────
  function _renderReport(report) {
    const panel = $('osintDetailPanel');
    if (!panel) return;

    const isRunning = report.status === 'running' || report.status === 'pending';
    const riskClass = _riskClass(report.osint_risk_score || 0);
    const allAccounts = [...(report.maigret_accounts || []), ...(report.blackbird_accounts || [])];

    panel.innerHTML = `
      <div class="osint-detail-panel">
        <div class="osint-detail-header">
          <div>
            <div class="osint-detail-title">
              ${report.subject_type === 'defendant' ? '🔴' : '🟡'}
              ${report.full_name || report.subject_id}
              <span class="osint-status-badge ${report.status}" style="margin-left:8px">${report.status}</span>
            </div>
            <div class="osint-detail-sub">
              ${report.subject_type.toUpperCase()} · Scanned ${fmt(report.scan_started_at)}
              ${report.scan_completed_at ? ` · Completed ${fmt(report.scan_completed_at)}` : ''}
            </div>
          </div>
          <button class="osint-close-btn" onclick="SLOSINT.closeReport()">✕ Close</button>
        </div>

        ${isRunning ? `
          <div style="margin-bottom:16px">
            <div style="font-size:0.78rem;color:var(--muted);margin-bottom:6px">
              ⟳ Scan in progress — checking ${report.run_maigret !== false ? 'Maigret + ' : ''}${report.run_blackbird !== false ? 'Blackbird' : ''}...
            </div>
            <div class="osint-progress-bar"><div class="osint-progress-fill"></div></div>
          </div>` : ''}

        <!-- KPI Row -->
        <div class="osint-kpi-row">
          <div class="osint-kpi-card" style="--accent:#ef4444">
            <div class="osint-kpi-label">OSINT Risk Delta</div>
            <div class="osint-kpi-value" style="color:${_riskColor(riskClass)}">+${report.osint_risk_score || 0}</div>
            <div class="osint-kpi-sub">added to bond risk score</div>
          </div>
          <div class="osint-kpi-card" style="--accent:#3b82f6">
            <div class="osint-kpi-label">Accounts Found</div>
            <div class="osint-kpi-value">${report.total_accounts_found || 0}</div>
            <div class="osint-kpi-sub">across all platforms</div>
          </div>
          <div class="osint-kpi-card" style="--accent:#8b5cf6">
            <div class="osint-kpi-label">Maigret Hits</div>
            <div class="osint-kpi-value">${(report.maigret_accounts || []).length}</div>
            <div class="osint-kpi-sub">username matches</div>
          </div>
          <div class="osint-kpi-card" style="--accent:#f59e0b">
            <div class="osint-kpi-label">Blackbird Hits</div>
            <div class="osint-kpi-value">${(report.blackbird_accounts || []).length}</div>
            <div class="osint-kpi-sub">email/username matches</div>
          </div>
        </div>

        ${report.ai_summary ? `
          <div class="osint-ai-summary">
            <div class="osint-ai-text">${report.ai_summary}</div>
          </div>` : ''}

        ${report.error ? `
          <div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.25);border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:0.8rem;color:#ef4444">
            ⚠️ Scan error: ${_esc(report.error)}
          </div>` : ''}

        ${(report.warnings && report.warnings.length) ? `
          <div style="background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.3);border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:0.8rem;color:#f59e0b">
            ${(report.warnings || []).map(w => `⚡ ${_esc(w)}`).join('<br>')}
          </div>` : ''}

        ${_renderToolResults(report.tool_results || {})}

        <!-- Risk Signals -->
        ${_renderSignals(report.risk_signals || [])}

        <!-- Accounts -->
        ${_renderAccounts(allAccounts, report)}

        <!-- Trape Section -->
        ${_renderTrapeSection(report)}

        ${report.notes ? `
          <div style="margin-top:16px;padding:12px 16px;background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:10px;font-size:0.78rem;color:var(--muted)">
            📝 Notes: ${report.notes}
          </div>` : ''}
      </div>`;
  }

  function _riskColor(cls) {
    return { low: '#10b981', medium: '#f59e0b', high: '#ef4444', critical: '#dc2626' }[cls] || '#f1f5f9';
  }

  function _renderSignals(signals) {
    if (!signals.length) {
      return `<div class="osint-signals-section">
        <div class="osint-section-title">⚡ Risk Signals</div>
        <div class="osint-empty" style="padding:16px">No risk signals detected.</div>
      </div>`;
    }
    return `<div class="osint-signals-section">
      <div class="osint-section-title">⚡ Risk Signals (${signals.length})</div>
      <div class="osint-signal-list">
        ${signals.map(s => `
          <div class="osint-signal-item ${s.severity}">
            <span class="osint-signal-sev">${s.severity}</span>
            <div>
              <div class="osint-signal-detail">${s.detail}</div>
              <div class="osint-signal-source">${s.signal_type} · via ${s.source}</div>
            </div>
          </div>`).join('')}
      </div>
    </div>`;
  }

  function _renderToolResults(toolResults) {
    const keys = Object.keys(toolResults || {});
    if (!keys.length) return '';
    return `<div class="osint-signals-section">
      <div class="osint-section-title">🛠 Tool Results</div>
      <div class="osint-signal-list">
        ${keys.map(k => {
          const t = toolResults[k] || {};
          const ok = !!t.ok;
          const sev = ok ? 'low' : 'high';
          const detail = t.error || t.warning || (ok ? `${t.accounts || 0} accounts` : 'failed');
          return `<div class="osint-signal-item ${sev}">
            <span class="osint-signal-sev">${ok ? 'ok' : 'fail'}</span>
            <div>
              <div class="osint-signal-detail"><strong>${_esc(k)}</strong> — ${_esc(detail)}</div>
              <div class="osint-signal-source">${t.attempted === false ? 'not attempted' : 'attempted'}${t.username ? ' · ' + _esc(t.username) : ''}</div>
            </div>
          </div>`;
        }).join('')}
      </div>
    </div>`;
  }

  function _renderAccounts(accounts, report) {
    if (!accounts.length) {
      const failed = report && (report.status === 'failed' || report.status === 'partial');
      const msg = failed
        ? 'No accounts — scan did not complete successfully. See errors above (tools missing, wrong CLI, or timeout).'
        : 'No accounts found. Tools finished without hits — try other usernames, email, or deep scan.';
      return `<div class="osint-accounts-section">
        <div class="osint-section-title">🌐 Social Footprint</div>
        <div class="osint-empty" style="padding:16px">${msg}</div>
      </div>`;
    }
    const filtered = _accountFilter === 'all' ? accounts
      : accounts.filter(a => a.source === _accountFilter);

    return `<div class="osint-accounts-section">
      <div class="osint-section-title">🌐 Social Footprint (${accounts.length} accounts)</div>
      <div class="osint-accounts-filter">
        <button class="osint-filter-btn ${_accountFilter === 'all' ? 'active' : ''}" onclick="SLOSINT.filterAccounts('all')">All (${accounts.length})</button>
        <button class="osint-filter-btn ${_accountFilter === 'maigret' ? 'active' : ''}" onclick="SLOSINT.filterAccounts('maigret')">Maigret (${accounts.filter(a=>a.source==='maigret').length})</button>
        <button class="osint-filter-btn ${_accountFilter === 'blackbird' ? 'active' : ''}" onclick="SLOSINT.filterAccounts('blackbird')">Blackbird (${accounts.filter(a=>a.source==='blackbird').length})</button>
      </div>
      <div class="osint-accounts-grid">
        ${filtered.map(a => `
          <a class="osint-account-card" href="${a.url || '#'}" target="_blank" rel="noopener noreferrer" title="${a.url || ''}">
            <div class="osint-account-platform">${a.platform}</div>
            <div class="osint-account-url">${a.url ? new URL(a.url).hostname : '—'}</div>
            <span class="osint-account-source ${a.source}">${a.source}</span>
          </a>`).join('')}
      </div>
    </div>`;
  }

  function _renderTrapeSection(report) {
    return `<div class="osint-trape-section">
      <div class="osint-trape-header">
        <span class="osint-trape-title">📡 Trape — Location & Session Tracking</span>
      </div>
      <div class="osint-trape-warning">
        ⚠️ <strong>Operational Use Only.</strong> Trape requires a publicly accessible server (ngrok or static IP) and the subject must visit the generated tracking URL. Use only for legitimate skip-trace operations. All sessions are audited.
      </div>
      <button class="osint-trape-btn" onclick="SLOSINT.createTrapeSession('${report.subject_type}', '${report.subject_id}')">
        📡 Generate Tracking Session
      </button>
      <div id="osintTrapeSessionResult"></div>
    </div>`;
  }

  // ── Filter Accounts ────────────────────────────────────────────────
  function filterAccounts(source) {
    _accountFilter = source;
    if (_activeReport) _renderReport(_activeReport);
  }

  // ── Trape Session ──────────────────────────────────────────────────
  async function createTrapeSession(subjectType, subjectId) {
    const lureUrl = prompt('Enter the lure URL to clone (e.g. a court notice page):');
    if (!lureUrl) return;

    try {
      const r = await fetch(`${API}/api/osint/trape/session`, {
        method: 'POST',
        headers: headers(),
        credentials: 'same-origin',
        body: JSON.stringify({
          subject_type: subjectType || $('osintSubjectType')?.value,
          subject_id: subjectId || $('osintSubjectId')?.value?.trim(),
          lure_url: lureUrl,
        }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Session creation failed');

      const resultEl = $('osintTrapeSessionResult');
      if (resultEl) {
        resultEl.innerHTML = `
          <div class="osint-trape-session-card">
            <strong>Session Created</strong><br>
            Session ID: <code>${d.session_id}</code><br>
            ${d.tracking_url ? `Tracking URL: <a href="${d.tracking_url}" target="_blank" style="color:#f59e0b">${d.tracking_url}</a>` : '<em style="color:var(--muted)">Set TRAPE_SERVER_URL env var to generate a public URL.</em>'}<br>
            <div class="cmd">${d.trape_command}
              <button class="osint-copy-btn" onclick="SLOSINT.copyToClipboard('${d.trape_command.replace(/'/g, "\\'")}')">📋 Copy</button>
            </div>
          </div>`;
      }
      toast('Trape session created.', 'success');
    } catch (e) {
      toast(`Trape error: ${e.message}`, 'error');
    }
  }

  // ── Utility ────────────────────────────────────────────────────────
  function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => toast('Copied!', 'success'));
  }

})();
