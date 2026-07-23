/* ═══════════════════════════════════════════════════════════════════
   ShamrockLeads — OSINT Intelligence Workstation v2
   Admin-Only · 4-Engine Platform
   Maigret · Sherlock · Blackbird · SpiderFoot
   ═══════════════════════════════════════════════════════════════════ */
/* global SL */
(function () {
  'use strict';

  const API = window.API || '';
  const ADMIN_KEY = window.OSINT_ADMIN_KEY || '';

  // ── State ──────────────────────────────────────────────────────────
  let _scans = [];
  let _activeScan = null;
  let _pollTimer = null;
  let _toolStatus = null;
  let _activeTab = 'summary';
  let _accountFilter = { source: 'all', category: 'all' };
  let _selectedEngines = new Set(['maigret', 'sherlock']);

  // ── Public API ─────────────────────────────────────────────────────
  window.SLOSINT = {
    init,
    load,
    runScan,
    openScan,
    closeScan,
    toggleEngine,
    switchTab,
    exportJSON,
    exportCSV,
    exportPDF,
    attachToSubject,
    markRelevant,
    markIrrelevant,
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

    // Engine chips
    document.querySelectorAll('.osint-engine-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const engine = chip.dataset.engine;
        if (engine) toggleEngine(engine);
      });
    });

    // Search/filter
    const searchInput = $('osintSearchInput');
    if (searchInput) {
      searchInput.addEventListener('input', _debounce(() => load(), 400));
    }
    const sortSelect = $('osintSortSelect');
    if (sortSelect) sortSelect.addEventListener('change', () => load());
    const statusFilter = $('osintStatusFilter');
    if (statusFilter) statusFilter.addEventListener('change', () => load());
  }

  function _debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  // ── Tool Status ────────────────────────────────────────────────────
  async function _checkToolStatus() {
    try {
      const r = await fetch(`${API}/api/osint/status`, { headers: headers(), credentials: 'same-origin' });
      if (!r.ok) return;
      _toolStatus = await r.json();
      _renderToolStatus();
    } catch (e) {
      console.warn('OSINT status check failed:', e);
    }
  }

  function _renderToolStatus() {
    const container = $('osintEnginePills');
    if (!container || !_toolStatus) return;

    const engines = ['maigret', 'sherlock', 'blackbird', 'spiderfoot'];
    container.innerHTML = engines.map(eng => {
      const info = _toolStatus[eng] || {};
      const available = info.available;
      const cls = available ? 'available' : 'unavailable';
      const version = info.version ? ` v${info.version}` : '';
      return `<span class="osint-engine-pill ${cls}" title="${eng}${version}${info.error ? ' — ' + info.error : ''}">
        <span class="dot"></span>${eng.charAt(0).toUpperCase() + eng.slice(1)}${version}
      </span>`;
    }).join('');

    // Queue info
    const queueEl = $('osintQueueInfo');
    if (queueEl && _toolStatus.queue) {
      const q = _toolStatus.queue;
      queueEl.textContent = `${q.running || 0} running · ${q.total_scans || 0} total`;
    }
  }

  // ── Engine Toggle ──────────────────────────────────────────────────
  function toggleEngine(engine) {
    if (_selectedEngines.has(engine)) {
      if (_selectedEngines.size > 1) _selectedEngines.delete(engine);
    } else {
      _selectedEngines.add(engine);
    }
    _updateEngineChips();
    _updateAdaptiveFields();
  }

  function _updateEngineChips() {
    document.querySelectorAll('.osint-engine-chip').forEach(chip => {
      const eng = chip.dataset.engine;
      chip.classList.toggle('active', _selectedEngines.has(eng));
    });
  }

  function _updateAdaptiveFields() {
    const needsEmail = _selectedEngines.has('blackbird') || _selectedEngines.has('spiderfoot');
    const needsPhone = _selectedEngines.has('spiderfoot');
    const emailField = $('osintEmailField');
    const phoneField = $('osintPhoneField');
    if (emailField) emailField.style.display = needsEmail ? '' : 'none';
    if (phoneField) phoneField.style.display = needsPhone ? '' : 'none';
  }

  // ── Load Scans ─────────────────────────────────────────────────────
  async function load() {
    const search = $('osintSearchInput')?.value || '';
    const sort = $('osintSortSelect')?.value || 'newest';
    const status = $('osintStatusFilter')?.value || '';

    let url = `${API}/api/osint/scans?limit=30&sort=${sort}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (status) url += `&status=${status}`;

    try {
      const r = await fetch(url, { headers: headers(), credentials: 'same-origin' });
      if (!r.ok) return;
      const data = await r.json();
      _scans = data.scans || [];
      _renderScanList();
    } catch (e) {
      console.warn('Failed to load scans:', e);
    }
  }

  function _renderScanList() {
    const container = $('osintScanList');
    if (!container) return;

    if (!_scans.length) {
      container.innerHTML = `<div class="osint-empty">
        <div class="empty-icon">🔍</div>
        <div class="empty-text">No scans yet. Run your first OSINT scan to begin intelligence gathering.</div>
      </div>`;
      return;
    }

    container.innerHTML = _scans.map(scan => {
      const id = scan._id;
      const name = scan.full_name || 'Unknown Subject';
      const engines = (scan.engines_requested || []).join(', ');
      const status = scan.status || 'unknown';
      const count = scan.total_accounts || 0;
      const date = fmt(scan.created_at);
      const active = _activeScan && _activeScan._id === id ? 'active' : '';

      return `<div class="osint-report-row ${active}" onclick="SLOSINT.openScan('${id}')">
        <div class="report-info">
          <div class="report-name">${_esc(name)}</div>
          <div class="report-meta">${_esc(engines)} · ${date}</div>
        </div>
        <span class="report-count">${count}</span>
        <span class="report-status ${status}">${status}</span>
      </div>`;
    }).join('');
  }

  // ── Run Scan ───────────────────────────────────────────────────────
  async function runScan() {
    const btn = $('osintScanBtn');
    if (!btn || btn.disabled) return;

    const subjectType = $('osintSubjectType')?.value || 'defendant';
    const subjectId = $('osintSubjectId')?.value?.trim();
    const fullName = $('osintFullName')?.value?.trim();
    const usernames = ($('osintUsernames')?.value || '').split(',').map(s => s.trim()).filter(Boolean);
    const email = $('osintEmail')?.value?.trim() || null;
    const phone = $('osintPhone')?.value?.trim() || null;
    const deepScan = $('osintDeepScan')?.checked || false;
    const secondOpinion = $('osintSecondOpinion')?.checked || false;
    const notes = $('osintNotes')?.value?.trim() || null;

    if (!subjectId) {
      toast('Subject ID is required', 'error');
      return;
    }
    if (!fullName && !usernames.length && !email && !phone) {
      toast('At least one identifier required (name, username, email, or phone)', 'error');
      return;
    }

    const engines = Array.from(_selectedEngines);

    btn.disabled = true;
    btn.classList.add('running');
    btn.textContent = '⟳ Scanning...';

    try {
      const r = await fetch(`${API}/api/osint/scan`, {
        method: 'POST',
        headers: headers(),
        credentials: 'same-origin',
        body: JSON.stringify({
          subject_type: subjectType,
          subject_id: subjectId,
          full_name: fullName || null,
          usernames: usernames.length ? usernames : null,
          email,
          phone,
          engines,
          deep_scan: deepScan,
          second_opinion: secondOpinion,
          notes,
        }),
      });

      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        toast(err.detail || `Scan failed (${r.status})`, 'error');
        return;
      }

      const data = await r.json();
      toast(`Scan initiated (${engines.join(', ')})`, 'success');

      // Poll for results
      setTimeout(() => {
        load();
        if (data.scan_id) openScan(data.scan_id);
      }, 1000);
    } catch (e) {
      toast(`Network error: ${e.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.classList.remove('running');
      btn.textContent = '🔍 Run OSINT Scan';
    }
  }

  // ── Open Scan Detail ───────────────────────────────────────────────
  async function openScan(scanId) {
    _stopPoll();

    try {
      const r = await fetch(`${API}/api/osint/scan/${scanId}`, { headers: headers(), credentials: 'same-origin' });
      if (!r.ok) return;
      _activeScan = await r.json();
      _renderDetail();
      _renderScanList(); // Update active state

      // Poll if still running
      if (['running', 'queued'].includes(_activeScan.status)) {
        _startPoll(scanId);
      }
    } catch (e) {
      console.warn('Failed to open scan:', e);
    }
  }

  function closeScan() {
    _stopPoll();
    _activeScan = null;
    const panel = $('osintDetailPanel');
    const empty = $('osintDetailEmpty');
    if (panel) panel.style.display = 'none';
    if (empty) empty.style.display = '';
    _renderScanList();
  }

  function _startPoll(scanId) {
    _pollTimer = setInterval(async () => {
      try {
        const r = await fetch(`${API}/api/osint/scan/${scanId}`, { headers: headers(), credentials: 'same-origin' });
        if (!r.ok) return;
        _activeScan = await r.json();
        _renderDetail();
        if (!['running', 'queued'].includes(_activeScan.status)) {
          _stopPoll();
          load(); // Refresh list
        }
      } catch (e) { /* ignore */ }
    }, 3000);
  }

  function _stopPoll() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }

  // ── Render Detail ──────────────────────────────────────────────────
  function _renderDetail() {
    const panel = $('osintDetailPanel');
    const empty = $('osintDetailEmpty');
    if (!panel || !_activeScan) return;

    panel.style.display = '';
    if (empty) empty.style.display = 'none';

    const scan = _activeScan;
    const name = scan.full_name || 'Unknown Subject';
    const status = scan.status || 'unknown';

    panel.innerHTML = `
      <div class="osint-detail-header">
        <div>
          <h3>${_esc(name)} <span class="report-status ${status}">${status}</span></h3>
          <div style="font-size:0.68rem;color:var(--osint-muted);margin-top:2px">
            ${(scan.engines_requested || []).join(' · ')} · ${fmt(scan.created_at)}
          </div>
        </div>
        <div class="osint-detail-actions">
          <button onclick="SLOSINT.exportJSON()">JSON</button>
          <button onclick="SLOSINT.exportCSV()">CSV</button>
          <button onclick="SLOSINT.exportPDF()">PDF</button>
          <button class="primary" onclick="SLOSINT.attachToSubject()">Attach</button>
          <button onclick="SLOSINT.closeScan()">✕</button>
        </div>
      </div>
      <div class="osint-detail-tabs">
        <div class="osint-detail-tab ${_activeTab === 'summary' ? 'active' : ''}" onclick="SLOSINT.switchTab('summary')">Summary</div>
        <div class="osint-detail-tab ${_activeTab === 'accounts' ? 'active' : ''}" onclick="SLOSINT.switchTab('accounts')">Accounts (${scan.total_accounts || 0})</div>
        <div class="osint-detail-tab ${_activeTab === 'entities' ? 'active' : ''}" onclick="SLOSINT.switchTab('entities')">Entities (${scan.total_entities || 0})</div>
        <div class="osint-detail-tab ${_activeTab === 'risk' ? 'active' : ''}" onclick="SLOSINT.switchTab('risk')">Risk</div>
        <div class="osint-detail-tab ${_activeTab === 'progress' ? 'active' : ''}" onclick="SLOSINT.switchTab('progress')">Engines</div>
      </div>
      <div class="osint-detail-content" id="osintDetailContent">
        ${_renderTabContent()}
      </div>
    `;
  }

  function switchTab(tab) {
    _activeTab = tab;
    _renderDetail();
  }

  function _renderTabContent() {
    const scan = _activeScan;
    if (!scan) return '';

    switch (_activeTab) {
      case 'summary': return _renderSummary(scan);
      case 'accounts': return _renderAccounts(scan);
      case 'entities': return _renderEntities(scan);
      case 'risk': return _renderRisk(scan);
      case 'progress': return _renderProgress(scan);
      default: return '';
    }
  }

  function _renderSummary(scan) {
    const accounts = scan.total_accounts || 0;
    const entities = scan.total_entities || 0;
    const risk = scan.osint_risk_score || 0;
    const platforms = (scan.platforms_found || []).length;

    let html = `<div class="osint-kpi-grid">
      <div class="osint-kpi"><div class="kpi-value">${accounts}</div><div class="kpi-label">Accounts</div></div>
      <div class="osint-kpi"><div class="kpi-value">${entities}</div><div class="kpi-label">Entities</div></div>
      <div class="osint-kpi"><div class="kpi-value">${platforms}</div><div class="kpi-label">Platforms</div></div>
      <div class="osint-kpi"><div class="kpi-value">+${risk}</div><div class="kpi-label">Risk (Advisory)</div></div>
    </div>`;

    // Engine progress
    const progress = scan.progress || {};
    if (Object.keys(progress).length) {
      html += `<div class="osint-engine-progress">`;
      for (const [engine, info] of Object.entries(progress)) {
        const st = info.status || 'pending';
        const count = (info.accounts_found || 0) + (info.entities_found || 0);
        html += `<div class="osint-ep-item">
          <span class="ep-dot ${st}"></span>
          <span class="ep-name">${engine}</span>
          <span class="ep-count">${count} found</span>
        </div>`;
      }
      html += `</div>`;
    }

    // AI Summary
    if (scan.ai_summary) {
      html += `<div style="background:rgba(163,113,247,0.08);border:1px solid rgba(163,113,247,0.2);border-radius:8px;padding:12px;margin-top:10px">
        <div style="font-size:0.63rem;font-weight:600;color:var(--osint-purple);text-transform:uppercase;margin-bottom:6px">AI Analysis</div>
        <div style="font-size:0.78rem;color:var(--osint-text);line-height:1.5">${_esc(scan.ai_summary)}</div>
      </div>`;
    }

    // Warnings
    if (scan.warnings?.length) {
      html += `<div style="margin-top:10px">`;
      scan.warnings.forEach(w => {
        html += `<div style="font-size:0.68rem;color:var(--osint-warning);padding:4px 0">⚠ ${_esc(w)}</div>`;
      });
      html += `</div>`;
    }

    return html;
  }

  function _renderAccounts(scan) {
    const accounts = scan.accounts || [];
    if (!accounts.length) {
      return `<div class="osint-empty"><div class="empty-icon">📭</div><div class="empty-text">No accounts discovered</div></div>`;
    }

    // Toolbar
    const sources = [...new Set(accounts.map(a => a.source))];
    const categories = [...new Set(accounts.map(a => a.category).filter(Boolean))];

    let html = `<div class="osint-accounts-toolbar">
      <select onchange="window._osintFilterSource=this.value;SLOSINT.switchTab('accounts')">
        <option value="all">All Sources</option>
        ${sources.map(s => `<option value="${s}" ${_accountFilter.source === s ? 'selected' : ''}>${s}</option>`).join('')}
      </select>
      <select onchange="window._osintFilterCat=this.value;SLOSINT.switchTab('accounts')">
        <option value="all">All Categories</option>
        ${categories.map(c => `<option value="${c}" ${_accountFilter.category === c ? 'selected' : ''}>${c}</option>`).join('')}
      </select>
      <span style="font-size:0.65rem;color:var(--osint-muted);margin-left:auto">${accounts.length} total</span>
    </div>`;

    // Filter
    let filtered = accounts;
    const srcFilter = window._osintFilterSource || 'all';
    const catFilter = window._osintFilterCat || 'all';
    if (srcFilter !== 'all') filtered = filtered.filter(a => a.source === srcFilter);
    if (catFilter !== 'all') filtered = filtered.filter(a => a.category === catFilter);

    html += `<div class="osint-accounts-grid">`;
    filtered.slice(0, 100).forEach((acct, idx) => {
      const icon = _platformIcon(acct.platform);
      html += `<div class="osint-account-card">
        <div class="acct-icon">${icon}</div>
        <div class="acct-info">
          <div class="acct-platform">${_esc(acct.platform)}</div>
          <div class="acct-url" title="${_esc(acct.url)}">${_esc(acct.url || acct.username || '')}</div>
        </div>
        <span class="acct-source">${acct.source}</span>
        <div class="acct-actions">
          ${acct.url ? `<button onclick="window.open('${_esc(acct.url)}','_blank')" title="Open">↗</button>` : ''}
          <button onclick="navigator.clipboard.writeText('${_esc(acct.url || '')}');window.SL?.toast?.('Copied','success')" title="Copy">📋</button>
        </div>
      </div>`;
    });
    html += `</div>`;

    if (filtered.length > 100) {
      html += `<div style="text-align:center;font-size:0.68rem;color:var(--osint-muted);margin-top:8px">Showing 100 of ${filtered.length} — export for full list</div>`;
    }

    return html;
  }

  function _renderEntities(scan) {
    const entities = scan.entities || [];
    if (!entities.length) {
      return `<div class="osint-empty"><div class="empty-icon">📭</div><div class="empty-text">No entities discovered. SpiderFoot returns entities like emails, phones, and addresses.</div></div>`;
    }

    let html = `<div class="osint-entity-list">`;
    entities.forEach(ent => {
      html += `<div class="osint-entity-row">
        <span class="entity-type">${_esc(ent.type)}</span>
        <span class="entity-value">${_esc(ent.value)}</span>
        <span class="entity-source">${_esc(ent.module || ent.source)}</span>
      </div>`;
    });
    html += `</div>`;
    return html;
  }

  function _renderRisk(scan) {
    const signals = scan.risk_signals || [];
    const score = scan.osint_risk_score || 0;

    let html = `<div class="osint-kpi-grid" style="margin-bottom:14px">
      <div class="osint-kpi"><div class="kpi-value" style="color:${score > 20 ? 'var(--osint-danger)' : score > 10 ? 'var(--osint-warning)' : 'var(--osint-accent)'}">+${score}</div><div class="kpi-label">Risk Delta (Advisory)</div></div>
    </div>`;

    if (!signals.length) {
      html += `<div style="font-size:0.75rem;color:var(--osint-muted)">No risk signals detected.</div>`;
      return html;
    }

    html += `<div class="osint-signal-list">`;
    signals.forEach(sig => {
      const sev = sig.severity || 'medium';
      html += `<div class="osint-signal-card ${sev}">
        <div class="signal-header">
          <span class="signal-severity ${sev}">${sev}</span>
          <span class="signal-type">${_esc(sig.signal_type)}</span>
        </div>
        <div class="signal-detail">${_esc(sig.detail)}</div>
      </div>`;
    });
    html += `</div>`;
    return html;
  }

  function _renderProgress(scan) {
    const progress = scan.progress || {};
    if (!Object.keys(progress).length) {
      return `<div style="font-size:0.75rem;color:var(--osint-muted)">No engine progress data available.</div>`;
    }

    let html = `<div style="display:flex;flex-direction:column;gap:10px">`;
    for (const [engine, info] of Object.entries(progress)) {
      const st = info.status || 'pending';
      const stColor = st === 'completed' ? 'var(--osint-accent)' : st === 'failed' ? 'var(--osint-danger)' : st === 'running' ? 'var(--osint-warning)' : 'var(--osint-muted)';
      html += `<div style="background:var(--osint-bg);border:1px solid var(--osint-border);border-radius:8px;padding:12px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
          <span class="ep-dot ${st}" style="width:8px;height:8px;border-radius:50%;background:${stColor}"></span>
          <span style="font-size:0.8rem;font-weight:600;color:var(--osint-text)">${engine.charAt(0).toUpperCase() + engine.slice(1)}</span>
          <span style="font-size:0.65rem;color:${stColor};margin-left:auto;text-transform:uppercase">${st}</span>
        </div>
        <div style="font-size:0.68rem;color:var(--osint-muted)">
          Accounts: ${info.accounts_found || 0} · Entities: ${info.entities_found || 0}
          ${info.started_at ? ` · Started: ${fmt(info.started_at)}` : ''}
          ${info.completed_at ? ` · Completed: ${fmt(info.completed_at)}` : ''}
        </div>
        ${info.error ? `<div style="font-size:0.65rem;color:var(--osint-danger);margin-top:4px">${_esc(info.error)}</div>` : ''}
        ${info.warning ? `<div style="font-size:0.65rem;color:var(--osint-warning);margin-top:4px">${_esc(info.warning)}</div>` : ''}
      </div>`;
    }
    html += `</div>`;
    return html;
  }

  // ── Export Actions ─────────────────────────────────────────────────
  function exportJSON() {
    if (!_activeScan) return;
    _downloadFile(`${API}/api/osint/scan/${_activeScan._id}/export/json`, `osint_${_activeScan._id}.json`);
  }

  function exportCSV() {
    if (!_activeScan) return;
    _downloadFile(`${API}/api/osint/scan/${_activeScan._id}/export/csv`, `osint_${_activeScan._id}.csv`);
  }

  function exportPDF() {
    if (!_activeScan) return;
    _downloadFile(`${API}/api/osint/scan/${_activeScan._id}/export/pdf`, `osint_report_${_activeScan._id}.pdf`);
  }

  async function _downloadFile(url, filename) {
    try {
      const r = await fetch(url, { headers: headers(), credentials: 'same-origin' });
      if (!r.ok) { toast('Export failed', 'error'); return; }
      const blob = await r.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      URL.revokeObjectURL(a.href);
      toast('Export downloaded', 'success');
    } catch (e) {
      toast(`Export error: ${e.message}`, 'error');
    }
  }

  async function attachToSubject() {
    if (!_activeScan) return;
    try {
      const r = await fetch(`${API}/api/osint/scan/${_activeScan._id}/attach`, {
        method: 'POST',
        headers: headers(),
        credentials: 'same-origin',
      });
      if (!r.ok) { toast('Attach failed', 'error'); return; }
      const data = await r.json();
      toast(data.success ? 'OSINT summary attached to subject record' : 'Attach failed', data.success ? 'success' : 'error');
    } catch (e) {
      toast(`Error: ${e.message}`, 'error');
    }
  }

  // ── Relevance Marking ──────────────────────────────────────────────
  async function markRelevant(indices) {
    await _updateRelevance(indices, [], 'relevant');
  }

  async function markIrrelevant(indices) {
    await _updateRelevance(indices, [], 'irrelevant');
  }

  async function _updateRelevance(accountIndices, entityIndices, relevance) {
    if (!_activeScan) return;
    try {
      await fetch(`${API}/api/osint/scan/${_activeScan._id}/findings`, {
        method: 'PATCH',
        headers: headers(),
        credentials: 'same-origin',
        body: JSON.stringify({ account_indices: accountIndices, entity_indices: entityIndices, relevance }),
      });
      toast('Updated', 'success');
      openScan(_activeScan._id);
    } catch (e) {
      toast('Update failed', 'error');
    }
  }

  // ── Utilities ──────────────────────────────────────────────────────
  function _esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function _platformIcon(platform) {
    const p = (platform || '').toLowerCase();
    if (p.includes('twitter') || p.includes('x.com')) return '🐦';
    if (p.includes('facebook')) return '📘';
    if (p.includes('instagram')) return '📷';
    if (p.includes('linkedin')) return '💼';
    if (p.includes('github')) return '🐙';
    if (p.includes('reddit')) return '🤖';
    if (p.includes('tiktok')) return '🎵';
    if (p.includes('youtube')) return '▶️';
    if (p.includes('pinterest')) return '📌';
    if (p.includes('snapchat')) return '👻';
    if (p.includes('telegram')) return '✈️';
    if (p.includes('discord')) return '💬';
    return '🌐';
  }
})();
