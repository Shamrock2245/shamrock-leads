/* ═══════════════════════════════════════════════════════════════════
   ShamrockLeads — OSINT Intelligence Workstation v2
   Admin-Only · Multi-Engine Platform
   Maigret · Sherlock · Blackbird · SpiderFoot · Ignorant · Holehe · Toutatis
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
  let _activeSubtab = 'scan';
  let _accountFilter = { source: 'all', category: 'all' };
  let _selectedEngines = new Set(['maigret', 'tookie', 'sherlock']);

  // ── Public API ─────────────────────────────────────────────────────
  window.SLOSINT = {
    init,
    load,
    runScan,
    openScan,
    closeScan,
    toggleEngine,
    switchTab,
    switchSubtab,
    runSingleEngineTest,
    analyzeExifUrl,
    createTrapeSession,
    onTrapeLureTemplateChange,
    sendTrapeViaIMessage,
    exportJSON,
    exportCSV,
    exportPDF,
    exportPDFForId,
    attachToSubject,
    importToActiveForm,
    markRelevant,
    markIrrelevant,
    deleteScan,
    clearAllScans,
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

    const phoneInput = $('osintPhone');
    if (phoneInput) {
      phoneInput.addEventListener('input', _debounce(() => _updateAdaptiveFields(), 300));
      phoneInput.addEventListener('change', () => _updateAdaptiveFields());
    }
    const emailInput = $('osintEmail');
    if (emailInput) {
      emailInput.addEventListener('input', _debounce(() => _updateAdaptiveFields(), 300));
      emailInput.addEventListener('change', () => _updateAdaptiveFields());
    }
    const plateInput = $('osintPlate');
    if (plateInput) {
      plateInput.addEventListener('input', _debounce(() => _updateAdaptiveFields(), 300));
      plateInput.addEventListener('change', () => _updateAdaptiveFields());
    }
    const userInput = $('osintUsernames');
    if (userInput) {
      userInput.addEventListener('input', _debounce(() => _updateAdaptiveFields(), 300));
      userInput.addEventListener('change', () => _updateAdaptiveFields());
    }
  }

  function _debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  // ── Tool Status ────────────────────────────────────────────────────
  async function _checkToolStatus() {
    try {
      const r = await fetch(`${API}/api/osint/status`, { headers: headers(), credentials: 'same-origin' });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        _toolStatus = {
          worker_reachable: false,
          worker_auth_ok: false,
          ready_for_scans: false,
          error: err.detail || `status HTTP ${r.status}`,
        };
        _renderToolStatus();
        return;
      }
      _toolStatus = await r.json();
      _renderToolStatus();
    } catch (e) {
      console.warn('OSINT status check failed:', e);
      _toolStatus = {
        worker_reachable: false,
        worker_auth_ok: false,
        ready_for_scans: false,
        error: e.message || 'network error',
      };
      _renderToolStatus();
    }
  }

  function _engineAvailable(info) {
    return !!(info && (info.available === true || info.available === 'true'));
  }

  function _renderConnectionBanner() {
    const banner = $('osintConnectionBanner');
    if (!banner || !_toolStatus) return;

    const reachable = _toolStatus.worker_reachable === true;
    const authOk = _toolStatus.worker_auth_ok === true;
    const ready = _toolStatus.ready_for_scans === true;
    const trape = _toolStatus.trape || {};
    const engines = ['maigret', 'tookie', 'sherlock', 'blackbird', 'spiderfoot', 'ignorant', 'holehe', 'hibf', 'toutatis', 'instaloader', 'exiftool'];
    const live = engines.filter(id => _engineAvailable(_toolStatus[id]));
    const pending = [];
    if ((_toolStatus.toutatis || {}).package_installed && !_engineAvailable(_toolStatus.toutatis)) {
      pending.push('Toutatis needs INSTAGRAM_SESSION_ID');
    }
    if ((_toolStatus.holehe || {}).error && !_engineAvailable(_toolStatus.holehe)) {
      pending.push('Holehe not installed');
    }

    let cls = 'ok';
    let text = `Worker connected · ${live.length} engines live`;
    if (trape.available) text += ' · Trape lure on dashboard /track';
    if (!reachable) {
      cls = 'down';
      text = `OSINT worker unreachable${_toolStatus.error ? ' — ' + _esc(_toolStatus.error) : ''}`;
    } else if (!authOk) {
      cls = 'auth';
      text = `Worker is up but not authenticated (${_esc(_toolStatus.error || 'OSINT_WORKER_KEY missing or mismatch')})`;
    } else if (!ready) {
      cls = 'warn';
      text = `Worker authenticated but no scan engines reported available`;
    }
    if (pending.length && cls === 'ok') {
      text += ` · ${pending.join(' · ')}`;
    }

    banner.hidden = false;
    banner.className = `osint-connection-banner ${cls}`;
    banner.textContent = text;

    const scanBtn = $('osintScanBtn');
    if (scanBtn) {
      const blocked = !ready;
      scanBtn.disabled = blocked;
      scanBtn.title = blocked ? text : 'Run OSINT Scan';
    }
  }

  function _renderToolStatus() {
    _renderConnectionBanner();

    const container = $('osintEnginePills');
    if (!container || !_toolStatus) return;

    const engines = ['maigret', 'tookie', 'sherlock', 'blackbird', 'spiderfoot', 'ignorant', 'holehe', 'hibf', 'toutatis', 'instaloader', 'exiftool'];
    container.innerHTML = engines.map(eng => {
      const info = _toolStatus[eng] || {};
      const available = _engineAvailable(info);
      const needsSession = eng === 'toutatis' && info.session_configured === false;
      const cls = available ? 'available' : (needsSession ? 'needs-config' : 'unavailable');
      const version = info.version ? ` v${info.version}` : '';
      const note = info.note ? ` — ${info.note}` : '';
      const sess = needsSession ? ' — needs INSTAGRAM_SESSION_ID' : '';
      const label = eng === 'tookie' ? '🚀 Tookie-OSINT' : eng.charAt(0).toUpperCase() + eng.slice(1);
      return `<span class="osint-engine-pill ${cls}" title="${eng}${version}${info.error ? ' — ' + info.error : ''}${note}${sess}">
        <span class="dot"></span>${label}${version}
      </span>`;
    }).join('');

    // Queue info
    const queueEl = $('osintQueueInfo');
    if (queueEl && _toolStatus.queue) {
      const q = _toolStatus.queue;
      queueEl.textContent = `${q.running || 0} running · ${q.total_scans || 0} total`;
    }

    _renderEngineMatrix();
  }

  function _renderEngineMatrix() {
    const matrixEl = $('osintEngineMatrix');
    if (!matrixEl || !_toolStatus) return;

    const engineMeta = [
      { id: 'tookie', name: '🚀 Tookie-OSINT V4', rank: '#1 Top Rank', desc: 'High-performance username discovery engine optimized for Python 3.12 (80%+ discovery rate across 300+ platforms)' },
      { id: 'sherlock', name: '🔎 Sherlock', rank: '#2 Rank', desc: 'Cross-checks username availability & account registration across major social networks' },
      { id: 'maigret', name: '🕵️ Maigret', rank: 'Core Engine', desc: 'Deep recursive search across 800+ sites with parsing & ID extraction' },
      { id: 'blackbird', name: '🐦 Blackbird', rank: 'Email/User', desc: 'WhatsMyName data-based fast username & email footprinting engine' },
      { id: 'spiderfoot', name: '🕷️ SpiderFoot', rank: 'OSINT Suite', desc: 'Multi-source entity correlation & OSINT footprinting' },
      { id: 'ignorant', name: '📱 Ignorant', rank: 'Phone Check', desc: 'Passive phone registration checks on Instagram, Snapchat, Amazon (no target SMS)' },
      { id: 'holehe', name: '✉️ Holehe', rank: 'Email Check', desc: 'Passive email registration across 120+ sites including Instagram (no target email)' },
      { id: 'hibf', name: '🚘 HIBF', rank: 'Plate Audit', desc: 'Public Flock LE search audit logs via Have I Been Flocked (FOIA data — not a live camera hit)' },
      { id: 'toutatis', name: '📸 Toutatis', rank: 'IG Enrichment', desc: 'Instagram handle → recovers public & obfuscated email, phone number & WhatsApp links' },
      { id: 'instaloader', name: '🖼️ Instaloader', rank: 'IG Media', desc: 'Extracts Instagram bio text, HD avatars, external profile links & follower counts' },
      { id: 'exiftool', name: '🔍 ExifTool', rank: 'EXIF / GPS', desc: 'Extracts camera fingerprints, timestamp, and GPS coordinates from evidence photos' },
      { id: 'trape', name: '🎯 Trape Lure', rank: 'Skip-Trace', desc: 'Native dashboard /track/{session} lure captures IP/UA then redirects to the court/portal page' },
    ];

    matrixEl.innerHTML = engineMeta.map(item => {
      const info = _toolStatus[item.id] || {};
      const avail = _engineAvailable(info);
      const needsCfg = item.id === 'toutatis' && info.session_configured === false && info.package_installed;
      const isTop = item.id === 'tookie';
      const badgeCls = isTop && avail ? 'top-rank' : (avail ? 'ready' : (needsCfg ? 'needs-config' : 'offline'));
      const badgeText = isTop && avail ? '🚀 Rank #1 (Top)' : (avail ? 'ACTIVE' : (needsCfg ? 'NEEDS COOKIE' : (_toolStatus.worker_auth_ok === false ? 'NOT AUTHENTICATED' : 'UNAVAILABLE')));
      const pathText = item.id === 'trape'
        ? (info.server_url || 'https://leads.shamrockbailbonds.biz') + '/track/{session}'
        : (info.path || (needsCfg ? 'Installed — set INSTAGRAM_SESSION_ID' : (_toolStatus.worker_auth_ok === false ? 'Worker not authenticated' : 'Not installed')));

      return `<div class="osint-matrix-card ${isTop ? 'ranked-top' : ''}">
        <div class="matrix-header">
          <div class="matrix-title">${item.name}</div>
          <span class="matrix-badge ${badgeCls}">${badgeText}</span>
        </div>
        <div class="matrix-desc">${item.desc}</div>
        <div class="matrix-path">Path: ${_esc(pathText)} ${info.version ? '· ' + _esc(info.version) : ''}</div>
      </div>`;
    }).join('');
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
    const emailField = $('osintEmailField');
    const phoneField = $('osintPhoneField');
    if (emailField) emailField.style.display = '';
    if (phoneField) phoneField.style.display = '';

    // Auto-suggest Ignorant when a phone is present (phone-registration engine)
    const phone = ($('osintPhone')?.value || '').trim();
    if (phone && phone.replace(/\D/g, '').length >= 10) {
      if (!_selectedEngines.has('ignorant')) {
        _selectedEngines.add('ignorant');
        _updateEngineChips();
      }
    }

    const email = ($('osintEmail')?.value || '').trim();
    if (email && email.includes('@') && !_selectedEngines.has('holehe')) {
      _selectedEngines.add('holehe');
      _updateEngineChips();
    }

    const plate = ($('osintPlate')?.value || '').trim();
    if (/^[A-Za-z0-9-]{2,10}$/.test(plate) && !_selectedEngines.has('hibf')) {
      _selectedEngines.add('hibf');
      _updateEngineChips();
    }

    // Auto-suggest Toutatis when usernames look like handles and session is ready
    const unames = ($('osintUsernames')?.value || '').split(',').map(s => s.trim()).filter(Boolean);
    const toutatisReady = _toolStatus?.toutatis?.available === true;
    if (unames.length && toutatisReady && !_selectedEngines.has('toutatis')) {
      _selectedEngines.add('toutatis');
      _updateEngineChips();
    }
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
      const name = scan.full_name || (scan.scan_params?.email || scan.scan_params?.usernames?.[0] || 'Ad-Hoc Subject');
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
        <div class="report-row-right">
          <span class="report-count" title="${count} accounts found">${count}</span>
          <span class="report-status ${status}">${status}</span>
          <button class="osint-row-pdf-btn" onclick="event.stopPropagation();SLOSINT.exportPDFForId('${id}')" title="Download PDF Report">📄 PDF</button>
          <button class="osint-row-del-btn" onclick="event.stopPropagation();SLOSINT.deleteScan('${id}')" title="Delete scan permanently">🗑️</button>
        </div>
      </div>`;
    }).join('');
  }

  // ── Run Scan ───────────────────────────────────────────────────────
  async function runScan() {
    const btn = $('osintScanBtn');
    if (!btn || btn.disabled) return;

    const subjectType = $('osintSubjectType')?.value || 'defendant';
    let subjectId = $('osintSubjectId')?.value?.trim();
    const fullName = $('osintFullName')?.value?.trim();
    const usernames = ($('osintUsernames')?.value || '').split(',').map(s => s.trim()).filter(Boolean);
    const email = $('osintEmail')?.value?.trim() || null;
    const phone = $('osintPhone')?.value?.trim() || null;
    const licensePlate = $('osintPlate')?.value?.trim() || null;
    const deepScan = $('osintDeepScan')?.checked || false;
    const secondOpinion = $('osintSecondOpinion')?.checked || false;
    const notes = $('osintNotes')?.value?.trim() || null;

    if (!fullName && !usernames.length && !email && !phone && !licensePlate) {
      toast('At least one identifier required (name, username, email, phone, or plate)', 'error');
      return;
    }

    if (!subjectId) {
      subjectId = 'adhoc_' + Date.now();
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
          license_plate: licensePlate,
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
          <button class="danger" onclick="SLOSINT.deleteScan('${scan._id}')" style="background:rgba(248,81,73,0.15);color:#f85149;border:1px solid rgba(248,81,73,0.3)" title="Delete scan record permanently">🗑️ Delete</button>
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

  function exportPDFForId(scanId) {
    if (!scanId) return;
    _downloadFile(`${API}/api/osint/scan/${scanId}/export/pdf`, `osint_report_${scanId}.pdf`);
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
      if (data.success) {
        const count = (data.hydrated_fields || []).length;
        const detail = count ? ` (${data.hydrated_fields.join(', ')})` : '';
        toast(`✅ OSINT summary attached & ${count} fields hydrated${detail}`, 'success');
      } else {
        toast('Attach failed: ' + (data.error || 'unknown'), 'error');
      }
    } catch (e) {
      toast(`Error: ${e.message}`, 'error');
    }
  }

  async function importToActiveForm(scanId) {
    const id = scanId || _activeScan?._id;
    if (!id) return null;
    try {
      const r = await fetch(`${API}/api/osint/scan/${id}/import-fields`, {
        headers: headers(),
        credentials: 'same-origin',
      });
      if (!r.ok) return null;
      return await r.json();
    } catch (e) {
      console.warn('Import fields failed:', e);
      return null;
    }
  }

  // ── Scan Deletion ──────────────────────────────────────────────────
  async function deleteScan(scanId) {
    const id = scanId || _activeScan?._id;
    if (!id) return;

    if (!confirm('Are you sure you want to permanently delete this scan history record? It will be removed from the database completely.')) {
      return;
    }

    try {
      const r = await fetch(`${API}/api/osint/scan/${id}`, {
        method: 'DELETE',
        headers: headers(),
        credentials: 'same-origin',
      });
      if (r.ok) {
        toast('Scan deleted permanently', 'success');
        if (_activeScan && _activeScan._id === id) {
          closeScan();
        }
        await load();
      } else {
        const data = await r.json().catch(() => ({}));
        toast('Delete failed: ' + (data.detail || 'unknown error'), 'error');
      }
    } catch (e) {
      toast(`Delete error: ${e.message}`, 'error');
    }
  }

  async function clearAllScans() {
    if (!_scans || !_scans.length) {
      toast('No scan history to clear', 'info');
      return;
    }

    if (!confirm('Are you sure you want to permanently delete ALL scan history? All past subject searches will be completely erased from the database.')) {
      return;
    }

    try {
      const r = await fetch(`${API}/api/osint/scans`, {
        method: 'DELETE',
        headers: headers(),
        credentials: 'same-origin',
      });
      if (r.ok) {
        const data = await r.json().catch(() => ({}));
        toast(`Cleared ${data.deleted_count || 0} scans from database`, 'success');
        closeScan();
        await load();
      } else {
        const data = await r.json().catch(() => ({}));
        toast('Clear history failed: ' + (data.detail || 'unknown error'), 'error');
      }
    } catch (e) {
      toast(`Clear error: ${e.message}`, 'error');
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
    if (p.includes('github') || p.includes('gitlab')) return '🐙';
    if (p.includes('reddit')) return '🤖';
    if (p.includes('tiktok')) return '🎵';
    if (p.includes('youtube')) return '▶️';
    if (p.includes('pinterest')) return '📌';
    if (p.includes('snapchat')) return '👻';
    if (p.includes('telegram')) return '✈️';
    if (p.includes('discord')) return '💬';
    if (p.includes('binance') || p.includes('coinbase') || p.includes('kraken') || p.includes('crypto')) return '🪙';
    if (p.includes('steam') || p.includes('twitch') || p.includes('epic')) return '🎮';
    if (p.includes('spotify') || p.includes('soundcloud') || p.includes('bandcamp')) return '🎧';
    if (p.includes('medium') || p.includes('substacks') || p.includes('wordpress')) return '✍️';
    if (p.includes('keybase') || p.includes('signal')) return '🔑';
    return '🌐';
  }

  // ── Workstation Subtab Switcher ────────────────────────────────────
  function switchSubtab(subtab) {
    _activeSubtab = subtab;

    // Toggle subtab buttons
    document.querySelectorAll('.osint-subtab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.subtab === subtab);
    });

    // Toggle view elements
    const subtabs = ['scan', 'engines', 'geo', 'trape'];
    subtabs.forEach(st => {
      const view = $('osintSubtabView' + st.charAt(0).toUpperCase() + st.slice(1));
      if (view) {
        view.classList.toggle('active', st === subtab);
        view.style.display = st === subtab ? 'block' : 'none';
      }
    });

    if (subtab === 'engines') {
      _checkToolStatus();
    } else if (subtab === 'trape') {
      _loadTrapeSessions();
    }
  }

  // ── Single-Engine Test Tool ────────────────────────────────────────
  async function runSingleEngineTest() {
    const engine = $('osintTestEngineSelect')?.value || 'tookie';
    const target = ($('osintTestTargetInput')?.value || '').trim();
    const box = $('osintTestResultBox');

    if (!target) {
      toast('Please enter a target username, phone number, or URL', 'error');
      return;
    }

    if (box) {
      box.style.display = 'block';
      box.textContent = `⏳ Running single-engine test for engine=${engine} target=${target}...`;
    }

    try {
      const isEmail = target.includes('@') && !target.startsWith('http');
      const isPhone = !isEmail && target.replace(/\D/g, '').length >= 10;
      const isUrl = target.startsWith('http://') || target.startsWith('https://');
      const isPlate = engine === 'hibf' || (!isEmail && !isPhone && !isUrl && /^[A-Za-z0-9-]{2,8}$/.test(target) && /[0-9]/.test(target));

      const payload = {
        subject_type: 'defendant',
        subject_id: 'test_' + Date.now(),
        full_name: 'Test Subject',
        usernames: (!isPhone && !isUrl && !isEmail && !isPlate) ? [target] : null,
        phone: (isPhone || engine === 'ignorant') ? target : null,
        email: (isEmail || isUrl || engine === 'holehe' || engine === 'exiftool') ? target : null,
        license_plate: (isPlate || engine === 'hibf') ? target : null,
        engines: [engine],
        deep_scan: false,
        notes: `Diagnostic test for ${engine}`,
      };

      const r = await fetch(`${API}/api/osint/scan`, {
        method: 'POST',
        headers: headers(),
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      });

      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        if (box) box.textContent = `❌ Error starting scan: ${err.detail || r.statusText}`;
        return;
      }

      const data = await r.json();
      const scanId = data.scan_id;

      if (box) box.textContent = `⚡ Scan initiated (${scanId}). Polling worker for results...`;

      // Poll until complete
      let attempts = 0;
      const pollTimer = setInterval(async () => {
        attempts++;
        try {
          const res = await fetch(`${API}/api/osint/scan/${scanId}`, { headers: headers(), credentials: 'same-origin' });
          if (!res.ok) return;
          const scan = await res.json();
          if (['completed', 'failed', 'partial'].includes(scan.status) || attempts > 20) {
            clearInterval(pollTimer);
            if (box) {
              box.textContent = JSON.stringify(scan, null, 2);
            }
            toast(`Diagnostic test for ${engine} complete (${scan.total_accounts || 0} accounts found)`, 'success');
          } else if (box) {
            box.textContent = `⟳ Running ${engine} (${scan.status})... attempt ${attempts}/20`;
          }
        } catch (e) {
          clearInterval(pollTimer);
          if (box) box.textContent = `❌ Error polling diagnostic scan: ${e.message}`;
        }
      }, 2500);

    } catch (e) {
      if (box) box.textContent = `❌ Network error: ${e.message}`;
    }
  }

  // ── Image EXIF Analyzer ────────────────────────────────────────────
  async function analyzeExifUrl() {
    const url = ($('osintExifUrlInput')?.value || '').trim();
    const box = $('osintExifOutput');

    if (!url || !url.startsWith('http')) {
      toast('Please enter a valid image HTTP/HTTPS URL', 'error');
      return;
    }

    if (box) {
      box.style.display = 'block';
      box.innerHTML = `<div style="font-size:0.75rem;color:var(--osint-muted)">⏳ Analyzing EXIF metadata via ExifTool...</div>`;
    }

    try {
      const payload = {
        subject_type: 'defendant',
        subject_id: 'exif_' + Date.now(),
        email: url, // image URL passed in email context
        engines: ['exiftool'],
      };

      const r = await fetch(`${API}/api/osint/scan`, {
        method: 'POST',
        headers: headers(),
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      });

      if (!r.ok) {
        if (box) box.innerHTML = `<div style="color:var(--osint-danger)">EXIF analysis request failed (${r.status})</div>`;
        return;
      }

      const data = await r.json();
      const scanId = data.scan_id;

      let attempts = 0;
      const pollTimer = setInterval(async () => {
        attempts++;
        try {
          const res = await fetch(`${API}/api/osint/scan/${scanId}`, { headers: headers(), credentials: 'same-origin' });
          if (!res.ok) return;
          const scan = await res.json();
          if (['completed', 'failed', 'partial'].includes(scan.status) || attempts > 15) {
            clearInterval(pollTimer);
            if (box) {
              const entities = scan.entities || [];
              if (entities.length) {
                box.innerHTML = `<div style="font-size:0.75rem;font-weight:600;color:var(--osint-accent);margin-bottom:8px">EXIF Metadata Discovered:</div>` +
                  entities.map(e => `<div style="font-size:0.68rem;padding:4px 0;border-bottom:1px solid var(--osint-border)"><strong>${_esc(e.type)}:</strong> ${_esc(e.value)}</div>`).join('');
              } else {
                box.innerHTML = `<div style="font-size:0.75rem;color:var(--osint-muted)">No EXIF metadata found in target image.</div>`;
              }
            }
          }
        } catch (e) {
          clearInterval(pollTimer);
        }
      }, 2000);
    } catch (e) {
      if (box) box.innerHTML = `<div style="color:var(--osint-danger)">Network error: ${e.message}</div>`;
    }
  }

  // ── Trape Session Helpers & 1-Click iMessage Dispatch ─────────────
  function onTrapeLureTemplateChange() {
    const sel = $('trapeLureTemplateSelect')?.value;
    const customRow = $('trapeCustomUrlRow');
    const urlInput = $('trapeLureUrl');

    if (sel === 'custom') {
      if (customRow) customRow.style.display = 'block';
    } else {
      if (customRow) customRow.style.display = 'none';
      if (urlInput && sel) urlInput.value = sel;
    }
  }

  async function createTrapeSession(sendIMessage = false) {
    const subjectId = ($('trapeSubjectId')?.value || '').trim();
    const subjectType = $('trapeSubjectType')?.value || 'defendant';
    const selTemplate = $('trapeLureTemplateSelect')?.value;
    const lureUrl = (selTemplate === 'custom' ? $('trapeLureUrl')?.value : selTemplate) || 'https://shamrockbailbonds.biz/court-notice';
    const phone = ($('trapeSubjectPhone')?.value || '').trim();

    if (!subjectId) {
      toast('Subject Name / ID required for Trape tracking session', 'error');
      return;
    }

    if (sendIMessage && !phone) {
      toast('Subject Phone Number required to dispatch via iMessage', 'error');
      return;
    }

    try {
      const r = await fetch(`${API}/api/osint/trape/session`, {
        method: 'POST',
        headers: headers(),
        credentials: 'same-origin',
        body: JSON.stringify({
          subject_type: subjectType,
          subject_id: subjectId,
          lure_url: lureUrl,
          notes: phone ? `Target Phone: ${phone}` : null,
        }),
      });

      if (!r.ok) {
        toast('Trape session creation failed', 'error');
        return;
      }

      const session = await r.json();
      const trackUrl = session.tracking_url || `${window.location.origin}/track/${session.session_id}`;
      toast(`✅ Trape lure link generated!`, 'success');

      if (sendIMessage && phone) {
        await sendTrapeViaIMessage(phone, trackUrl, lureUrl);
      }

      _loadTrapeSessions();
    } catch (e) {
      toast(`Error: ${e.message}`, 'error');
    }
  }

  async function sendTrapeViaIMessage(phone, trackingUrl, lureUrl = '') {
    const cleanPhone = (phone || '').trim();
    if (!cleanPhone) {
      toast('Phone number required for iMessage dispatch', 'error');
      return;
    }

    let defaultMsg = `Shamrock Bail Bonds Notice: Please verify your scheduled appearance status and case records at: ${trackingUrl}`;
    if (lureUrl.includes('client-portal')) {
      defaultMsg = `Shamrock Bail Bonds Portal: Please complete your required bond document sign-off here: ${trackingUrl}`;
    } else if (lureUrl.includes('verify-checkin')) {
      defaultMsg = `Shamrock Bail Bonds Alert: Complete your weekly GPS verification check-in here: ${trackingUrl}`;
    }

    const msg = prompt(`Confirm iMessage text to dispatch to ${cleanPhone}:`, defaultMsg);
    if (!msg) return;

    toast(`💬 Sending iMessage via BlueBubbles to ${cleanPhone}...`, 'info');

    try {
      const r = await fetch(`${API}/api/imessage/send`, {
        method: 'POST',
        headers: headers(),
        credentials: 'same-origin',
        body: JSON.stringify({ phone: cleanPhone, message: msg }),
      });

      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        toast(`❌ Failed to send iMessage: ${err.detail || r.statusText}`, 'error');
        return;
      }

      toast(`✅ iMessage dispatched to ${cleanPhone} via BlueBubbles!`, 'success');
    } catch (e) {
      toast(`Network error sending iMessage: ${e.message}`, 'error');
    }
  }

  async function _loadTrapeSessions() {
    const listEl = $('trapeSessionList');
    if (!listEl) return;

    try {
      const r = await fetch(`${API}/api/osint/trape/sessions`, { headers: headers(), credentials: 'same-origin' });
      if (!r.ok) return;
      const data = await r.json();
      const sessions = data.sessions || [];

      if (!sessions.length) {
        listEl.innerHTML = `<div class="osint-empty"><div class="empty-icon">🎯</div><div class="empty-text">No active Trape tracking sessions. Create a new lure link above.</div></div>`;
        return;
      }

      listEl.innerHTML = `<div style="font-size:0.78rem;font-weight:700;color:var(--osint-text);margin-bottom:10px">Active Skip-Trace Sessions (${sessions.length}):</div>` +
        `<div style="display:flex;flex-direction:column;gap:10px">` +
        sessions.map(s => {
          const trackUrl = s.tracking_url || `${window.location.origin}/track/${s.session_id}`;
          const isTriggered = s.status === 'triggered' || s.ip_address;
          const statusCls = isTriggered ? 'color:var(--osint-accent)' : 'color:var(--osint-warning)';
          const phoneMatch = (s.notes || '').match(/Target Phone:\s*(\+?[\d\s\-()]+)/i);
          const phone = phoneMatch ? phoneMatch[1].trim() : '';

          return `<div style="background:var(--osint-bg);border:1px solid ${isTriggered ? 'rgba(0,200,83,0.4)' : 'var(--osint-border)'};padding:12px 14px;border-radius:8px;box-shadow:0 2px 6px rgba(0,0,0,0.2)">
            <div style="font-size:0.78rem;font-weight:700;color:var(--osint-text);display:flex;justify-content:space-between;align-items:center">
              <span>🎯 Subject: ${_esc(s.subject_id)} (${s.subject_type})</span>
              <span style="${statusCls};font-size:0.68rem;text-transform:uppercase;font-weight:700;padding:2px 8px;background:rgba(255,255,255,0.05);border-radius:10px">● ${s.status}</span>
            </div>
            <div style="font-size:0.7rem;color:var(--osint-muted);margin-top:6px">Lure: <a href="${_esc(s.lure_url)}" target="_blank" style="color:var(--osint-info)">${_esc(s.lure_url)}</a></div>
            <div style="font-size:0.72rem;font-family:monospace;color:var(--osint-accent);margin-top:4px;word-break:break-all">Tracking URL: ${_esc(trackUrl)}</div>
            ${s.ip_address ? `<div style="font-size:0.72rem;font-weight:700;color:var(--osint-warning);margin-top:6px;padding:4px 8px;background:rgba(255,109,0,0.1);border:1px solid rgba(255,109,0,0.3);border-radius:4px">🎯 Target IP Captured: ${s.ip_address} ${s.geolocation ? '· Geo: ' + JSON.stringify(s.geolocation) : ''}</div>` : ''}
            <div style="display:flex;gap:8px;margin-top:10px">
              <button onclick="navigator.clipboard.writeText('${_esc(trackUrl)}');window.SL?.toast?.('Link copied to clipboard','success');" style="font-size:0.68rem;padding:4px 10px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.15);color:#fff;border-radius:4px;cursor:pointer">📋 Copy Link</button>
              <button onclick="SLOSINT.sendTrapeViaIMessage('${_esc(phone)}', '${_esc(trackUrl)}', '${_esc(s.lure_url)}')" style="font-size:0.68rem;padding:4px 10px;background:rgba(37,99,235,0.2);border:1px solid #3b82f6;color:#60a5fa;border-radius:4px;cursor:pointer;font-weight:600">📱 Dispatch via iMessage</button>
            </div>
          </div>`;
        }).join('') + `</div>`;
    } catch (e) {
      console.warn('Failed to load Trape sessions:', e);
    }
  }
})();
