/* ═══════════════════════════════════════════════════════════════════════════
   ShamrockLeads — UI/UX Overhaul  v2.0  (sl-overhaul.js)
   Loaded LAST — after all other scripts.
   Covers:
     1. Favicon injection (shamrock SVG)
     2. Unified toast system (replaces all toast variants)
     3. Global search / Command Palette (Cmd+K)
     4. Header search pill injection
     5. Tab bar grouping (nav separators + group labels)
     6. County badge renderer (upgrades plain county text)
     7. KPI counter animations (count-up on tab switch)
     8. Loading skeleton helpers
     9. Empty state helpers
    10. Table row stagger animation
    11. Duplicate button cleanup (Active Bonds)
    12. Number formatting helpers
    13. Tab content fade-in on switch
    14. Risk score bar renderer
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── Tiny DOM helpers ─────────────────────────────────────────────────── */
  const $ = id => document.getElementById(id);
  const $$ = sel => Array.from(document.querySelectorAll(sel));
  const el = (tag, cls, html) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html) e.innerHTML = html;
    return e;
  };

  /* ══════════════════════════════════════════════════════════════════════════
     1. FAVICON — shamrock SVG injected into <head>
     ══════════════════════════════════════════════════════════════════════════ */
  function injectFavicon() {
    if (document.querySelector('link[rel="icon"]')) return;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
      <rect width="32" height="32" rx="8" fill="#0f172a"/>
      <text y="24" x="4" font-size="22">☘️</text>
    </svg>`;
    const blob = new Blob([svg], { type: 'image/svg+xml' });
    const url  = URL.createObjectURL(blob);
    const link = document.createElement('link');
    link.rel = 'icon';
    link.type = 'image/svg+xml';
    link.href = url;
    document.head.appendChild(link);
  }

  /* ══════════════════════════════════════════════════════════════════════════
     2. UNIFIED TOAST SYSTEM
     ══════════════════════════════════════════════════════════════════════════ */
  const TOAST_ICONS = {
    success: '✅',
    error:   '❌',
    warn:    '⚠️',
    warning: '⚠️',
    info:    'ℹ️',
  };

  let _toastContainer = null;

  function getToastContainer() {
    if (_toastContainer) return _toastContainer;
    _toastContainer = $('sl-toast-container');
    if (!_toastContainer) {
      _toastContainer = el('div');
      _toastContainer.id = 'sl-toast-container';
      document.body.appendChild(_toastContainer);
    }
    return _toastContainer;
  }

  /**
   * Show a toast notification.
   * @param {string} msg     - Message text
   * @param {string} type    - 'success' | 'error' | 'warn' | 'info'
   * @param {number} duration - ms to show (0 = sticky)
   */
  function SLToast(msg, type = 'info', duration = 3500) {
    const container = getToastContainer();
    const t = el('div', `sl-toast sl-toast-${type}`);
    t.style.setProperty('--toast-duration', duration + 'ms');
    t.innerHTML = `
      <span class="sl-toast-icon">${TOAST_ICONS[type] || 'ℹ️'}</span>
      <span class="sl-toast-body">
        <span class="sl-toast-msg">${escHtml(msg)}</span>
      </span>
      <button class="sl-toast-close" aria-label="Dismiss">✕</button>
    `;
    container.appendChild(t);
    // Trigger entrance
    requestAnimationFrame(() => {
      requestAnimationFrame(() => t.classList.add('show'));
    });
    // Dismiss on click
    t.addEventListener('click', () => dismissToast(t));
    // Auto-dismiss
    if (duration > 0) {
      setTimeout(() => dismissToast(t), duration);
    }
    return t;
  }

  function dismissToast(t) {
    if (!t || t.classList.contains('hide')) return;
    t.classList.add('hide');
    t.classList.remove('show');
    setTimeout(() => t.remove(), 400);
  }

  function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  }

  // Override ALL existing toast implementations globally
  window.toast = SLToast;
  window.showToast = (msg, type, dur) => SLToast(msg, type, dur);
  window.SLToast = SLToast;

  // Also patch SL.toast if SL exists
  if (window.SL) window.SL.toast = SLToast;
  document.addEventListener('DOMContentLoaded', () => {
    if (window.SL) window.SL.toast = SLToast;
  });

  /* ══════════════════════════════════════════════════════════════════════════
     3. GLOBAL SEARCH / COMMAND PALETTE (Cmd+K)
     ══════════════════════════════════════════════════════════════════════════ */

  // Tab navigation commands
  const NAV_COMMANDS = [
    { icon: '🏠', title: 'Command Center',    sub: 'Dashboard overview & bond queue',   tab: 'tabCommand',      key: '1' },
    { icon: '🔍', title: 'Lead Explorer',     sub: 'Browse & filter new leads',         tab: 'tabLeads',        key: '2' },
    { icon: '📋', title: 'Defendants',        sub: 'Defendant profiles & lifecycle',    tab: 'tabDefendants',   key: '3' },
    { icon: '🏥', title: 'Scraper Health',    sub: 'County scraper status',             tab: 'tabHealth',       key: '4' },
    { icon: '🔒', title: 'Active Bonds',      sub: 'Bond portfolio & geolocation',      tab: 'tabActiveBonds',  key: '5' },
    { icon: '📍', title: 'Tracking',          sub: 'Map view & check-in tracking',      tab: 'tabTracking',     key: '6' },
    { icon: '📥', title: 'Intake Queue',      sub: 'Indemnitor intake from all sources',tab: 'tabIntake',       key: '7' },
    { icon: '🤝', title: 'Indemnitors',       sub: 'Indemnitor database & history',     tab: 'tabIndemnitor',   key: '8' },
    { icon: '💰', title: 'Revenue',           sub: 'Revenue intelligence & charts',     tab: 'tabAnalytics',    key: '9' },
    { icon: '📅', title: 'Calendar',          sub: 'Court dates & reminders',           tab: 'tabCalendar',     key: '' },
    { icon: '📋', title: 'Reports',           sub: 'Bond & compliance reports',         tab: 'tabReports',      key: '' },
    { icon: '🎯', title: 'Outreach CRM',      sub: 'Lead pipeline & outreach',          tab: 'tabOutreach',     key: '' },
    { icon: '📦', title: 'POA Inventory',     sub: 'Power of attorney stock',           tab: 'tabInventory',    key: '' },
  ];

  let _cmdOpen = false;
  let _cmdActiveIdx = -1;
  let _cmdResults = [];

  function buildCmdPalette() {
    if ($('sl-cmd-overlay')) return;

    const overlay = el('div');
    overlay.id = 'sl-cmd-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Command Palette');

    overlay.innerHTML = `
      <div id="sl-cmd-palette" role="listbox">
        <div class="cmd-search-row">
          <span class="cmd-search-icon">🔍</span>
          <input id="sl-cmd-input" type="text" placeholder="Search tabs, leads, defendants…" autocomplete="off" spellcheck="false">
          <span class="cmd-kbd-hint"><kbd>Esc</kbd> to close</span>
        </div>
        <div class="cmd-results" id="sl-cmd-results"></div>
        <div class="cmd-footer">
          <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
          <span><kbd>↵</kbd> select</span>
          <span><kbd>⌘K</kbd> toggle</span>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);

    // Close on overlay click
    overlay.addEventListener('click', e => {
      if (e.target === overlay) closeCmdPalette();
    });

    // Input handler
    const input = $('sl-cmd-input');
    input.addEventListener('input', () => renderCmdResults(input.value));
    input.addEventListener('keydown', onCmdKeydown);
  }

  function openCmdPalette() {
    buildCmdPalette();
    const overlay = $('sl-cmd-overlay');
    const input   = $('sl-cmd-input');
    if (!overlay) return;
    _cmdOpen = true;
    _cmdActiveIdx = -1;
    overlay.classList.add('open');
    input.value = '';
    renderCmdResults('');
    setTimeout(() => input.focus(), 50);
  }

  function closeCmdPalette() {
    const overlay = $('sl-cmd-overlay');
    if (!overlay) return;
    _cmdOpen = false;
    overlay.classList.remove('open');
  }

  function renderCmdResults(query) {
    const container = $('sl-cmd-results');
    if (!container) return;
    const q = (query || '').toLowerCase().trim();

    // Filter nav commands
    const navMatches = NAV_COMMANDS.filter(c =>
      !q ||
      c.title.toLowerCase().includes(q) ||
      c.sub.toLowerCase().includes(q) ||
      (c.key && c.key === q)
    );

    _cmdResults = navMatches;
    _cmdActiveIdx = navMatches.length > 0 ? 0 : -1;

    if (navMatches.length === 0) {
      container.innerHTML = `<div class="cmd-empty">No results for "<strong>${escHtml(query)}</strong>"</div>`;
      return;
    }

    container.innerHTML = `
      <div class="cmd-section-label">Navigation</div>
      ${navMatches.map((c, i) => `
        <div class="cmd-result-item${i === 0 ? ' active' : ''}" data-idx="${i}" data-tab="${c.tab}" role="option">
          <div class="cmd-result-icon">${c.icon}</div>
          <div class="cmd-result-body">
            <div class="cmd-result-title">${c.title}</div>
            <div class="cmd-result-sub">${c.sub}</div>
          </div>
          ${c.key ? `<div class="cmd-result-badge">${c.key}</div>` : ''}
        </div>
      `).join('')}
    `;

    // Click handlers
    container.querySelectorAll('.cmd-result-item').forEach(item => {
      item.addEventListener('click', () => {
        const tab = item.dataset.tab;
        closeCmdPalette();
        navigateToTab(tab);
      });
      item.addEventListener('mouseenter', () => {
        _cmdActiveIdx = parseInt(item.dataset.idx);
        updateCmdActive();
      });
    });
  }

  function updateCmdActive() {
    const items = $$('#sl-cmd-results .cmd-result-item');
    items.forEach((item, i) => {
      item.classList.toggle('active', i === _cmdActiveIdx);
    });
    const active = items[_cmdActiveIdx];
    if (active) active.scrollIntoView({ block: 'nearest' });
  }

  function onCmdKeydown(e) {
    const items = $$('#sl-cmd-results .cmd-result-item');
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _cmdActiveIdx = Math.min(_cmdActiveIdx + 1, items.length - 1);
      updateCmdActive();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      _cmdActiveIdx = Math.max(_cmdActiveIdx - 1, 0);
      updateCmdActive();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const active = items[_cmdActiveIdx];
      if (active) {
        closeCmdPalette();
        navigateToTab(active.dataset.tab);
      }
    } else if (e.key === 'Escape') {
      closeCmdPalette();
    }
  }

  function navigateToTab(tabId) {
    if (!tabId) return;
    // POA Inventory is a modal, not a tab — handle specially
    if (tabId === 'tabInventory' && window.SLInventory) {
      SLInventory.open();
      return;
    }
    const btn = document.querySelector(`[data-tab="${tabId}"]`);
    if (btn) {
      btn.click();
    }
  }

  // Override Cmd+K keyboard shortcut
  document.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      e.stopImmediatePropagation();
      if (_cmdOpen) {
        closeCmdPalette();
      } else {
        openCmdPalette();
      }
    }
    if (e.key === 'Escape' && _cmdOpen) {
      closeCmdPalette();
    }
  }, true); // capture phase to override existing handler

  window.SLCmdPalette = { open: openCmdPalette, close: closeCmdPalette };

  /* ══════════════════════════════════════════════════════════════════════════
     4. HEADER SEARCH PILL INJECTION
     ══════════════════════════════════════════════════════════════════════════ */
  function injectHeaderSearchPill() {
    const header = document.querySelector('.header');
    if (!header || header.querySelector('.header-search-pill')) return;

    const pill = el('div', 'header-search-pill');
    pill.innerHTML = `
      <div class="hsp-inner" role="button" tabindex="0" aria-label="Open command palette (⌘K)" onclick="SLCmdPalette.open()">
        <span class="hsp-icon">🔍</span>
        <span class="hsp-text">Search tabs, leads, defendants…</span>
        <span class="hsp-kbd">
          <kbd>⌘</kbd><kbd>K</kbd>
        </span>
      </div>
    `;
    pill.querySelector('.hsp-inner').addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openCmdPalette(); }
    });

    // Insert between brand and right section
    const right = header.querySelector('.header-right');
    if (right) {
      header.insertBefore(pill, right);
    } else {
      header.appendChild(pill);
    }
  }

  /* ══════════════════════════════════════════════════════════════════════════
     5. TAB BAR GROUPING — nav separators + group labels
     ══════════════════════════════════════════════════════════════════════════ */
  const TAB_GROUPS = [
    {
      label: 'Intelligence',
      tabs: ['tabCommand', 'tabLeads', 'tabDefendants', 'tabHealth'],
    },
    {
      label: 'Operations',
      tabs: ['tabActiveBonds', 'tabTracking', 'tabIntake', 'tabIndemnitor'],
    },
    {
      label: 'Business',
      // tabProspective is the actual Outreach tab ID; inv-tab-trigger has no data-tab (modal)
      tabs: ['tabAnalytics', 'tabCalendar', 'tabReports', 'tabProspective'],
    },
  ];

  function upgradeTabBar() {
    const tabBar = document.querySelector('.tab-bar');
    if (!tabBar || tabBar.dataset.grouped) return;
    tabBar.dataset.grouped = '1';

    // Collect existing tab buttons
    const existingBtns = $$('.tab-bar .tab-btn');
    if (!existingBtns.length) return;

    // Build a map of tab-id → button
    const btnMap = {};
    existingBtns.forEach(btn => {
      const tab = btn.dataset.tab;
      if (tab) btnMap[tab] = btn;
    });

    // Clear tab bar
    tabBar.innerHTML = '';

    // Rebuild with group labels and separators
    let firstGroup = true;
    TAB_GROUPS.forEach(group => {
      // Check if any tabs in this group exist in the DOM
      const groupBtns = group.tabs
        .map(t => btnMap[t])
        .filter(Boolean);

      if (!groupBtns.length) return;

      // Separator between groups
      if (!firstGroup) {
        const sep = el('div', 'tab-nav-sep');
        tabBar.appendChild(sep);
      }
      firstGroup = false;

      // Group label
      const label = el('div', 'tab-nav-group-label');
      label.textContent = group.label;
      tabBar.appendChild(label);

      // Buttons
      groupBtns.forEach(btn => tabBar.appendChild(btn));
    });

    // Append any ungrouped buttons at the end (e.g., POA Inventory modal trigger)
    existingBtns.forEach(btn => {
      if (!tabBar.contains(btn)) {
        // Add a separator before orphaned buttons if there are grouped buttons
        if (tabBar.children.length > 0 && !tabBar.lastElementChild?.classList?.contains('tab-nav-sep')) {
          tabBar.appendChild(el('div', 'tab-nav-sep'));
        }
        tabBar.appendChild(btn);
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     6. COUNTY BADGE RENDERER
     ══════════════════════════════════════════════════════════════════════════ */
  const COUNTY_COLORS = {
    'Lee':       'Lee',
    'Charlotte': 'Charlotte',
    'Collier':   'Collier',
    'DeSoto':    'DeSoto',
    'Hendry':    'Hendry',
    'Manatee':   'Manatee',
    'Sarasota':  'Sarasota',
  };

  /**
   * Render a county as a colored badge span.
   * @param {string} county
   * @returns {string} HTML string
   */
  function countyBadge(county) {
    if (!county || county === '—') return '<span class="county-badge">—</span>';
    const name = county.trim();
    return `<span class="county-badge" data-county="${escHtml(name)}">${escHtml(name)}</span>`;
  }

  window.countyBadge = countyBadge;

  // Upgrade existing .county-count spans to county badges
  function upgradeCountySpans() {
    $$('.county-count, td[data-county]').forEach(el => {
      if (el.dataset.badged) return;
      el.dataset.badged = '1';
      const name = el.textContent.trim();
      if (name && name !== '—') {
        el.outerHTML = countyBadge(name);
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     7. KPI COUNTER ANIMATION (count-up)
     ══════════════════════════════════════════════════════════════════════════ */
  function animateCounter(el, target, duration = 800) {
    if (!el || isNaN(target)) return;
    const start = performance.now();
    const from  = 0;
    const hasDollar  = el.dataset.prefix === '$';
    const hasPercent = el.dataset.suffix === '%';

    function tick(now) {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      // Ease out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = Math.round(from + (target - from) * eased);
      let text = current.toLocaleString();
      if (hasDollar)  text = '$' + text;
      if (hasPercent) text = text + '%';
      el.textContent = text;
      if (progress < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  function animateVisibleCounters() {
    $$('.stat-value, .okpi-value, .analytics-kpi-value').forEach(el => {
      if (el.dataset.animated) return;
      const text = el.textContent.trim();
      if (!text || text === '—') return;
      const cleaned = text.replace(/[$,%\s]/g, '').replace(/[KMB]/g, '').replace(/,/g, '');
      const num = parseFloat(cleaned);
      if (isNaN(num) || num === 0) return;
      el.dataset.animated = '1';
      animateCounter(el, num, 700);
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     8. LOADING SKELETON HELPERS
     ══════════════════════════════════════════════════════════════════════════ */

  /**
   * Replace element content with skeleton rows.
   * @param {string|Element} target - selector or element
   * @param {number} rows
   */
  function showSkeleton(target, rows = 5) {
    const el = typeof target === 'string' ? document.querySelector(target) : target;
    if (!el) return;
    el.innerHTML = `
      <div class="sl-skeleton-table-body">
        ${Array.from({ length: rows }).map(() => `
          <div class="sl-skeleton-table-row">
            <div class="sl-skeleton" style="flex:2"></div>
            <div class="sl-skeleton" style="flex:1"></div>
            <div class="sl-skeleton" style="flex:1"></div>
            <div class="sl-skeleton" style="flex:1.5"></div>
            <div class="sl-skeleton" style="flex:.8"></div>
          </div>
        `).join('')}
      </div>
    `;
  }

  /**
   * Show skeleton KPI cards.
   * @param {string|Element} target
   * @param {number} count
   */
  function showKpiSkeleton(target, count = 4) {
    const el = typeof target === 'string' ? document.querySelector(target) : target;
    if (!el) return;
    el.innerHTML = Array.from({ length: count }).map(() => `
      <div class="stat-card">
        <div class="sl-skeleton sl-skeleton-text short" style="margin-bottom:12px"></div>
        <div class="sl-skeleton sl-skeleton-text wide" style="height:28px;margin-bottom:8px"></div>
        <div class="sl-skeleton sl-skeleton-text med"></div>
      </div>
    `).join('');
  }

  window.SLSkeleton = { showSkeleton, showKpiSkeleton };

  /* ══════════════════════════════════════════════════════════════════════════
     9. EMPTY STATE HELPERS
     ══════════════════════════════════════════════════════════════════════════ */

  /**
   * Render an empty state into a container.
   * @param {string|Element} target
   * @param {object} opts - { icon, title, sub, action, onAction }
   */
  function showEmptyState(target, opts = {}) {
    const container = typeof target === 'string' ? document.querySelector(target) : target;
    if (!container) return;
    const {
      icon   = '📭',
      title  = 'Nothing here yet',
      sub    = 'Data will appear here once available.',
      action = null,
      onAction = null,
    } = opts;
    container.innerHTML = `
      <div class="sl-empty-state">
        <div class="sl-empty-icon">${icon}</div>
        <div class="sl-empty-title">${escHtml(title)}</div>
        <div class="sl-empty-sub">${escHtml(sub)}</div>
        ${action ? `<button class="sl-empty-action" id="sl-empty-action-btn">${escHtml(action)}</button>` : ''}
      </div>
    `;
    if (action && onAction) {
      const btn = container.querySelector('#sl-empty-action-btn');
      if (btn) btn.addEventListener('click', onAction);
    }
  }

  window.SLEmptyState = { show: showEmptyState };

  /* ══════════════════════════════════════════════════════════════════════════
     10. TABLE ROW STAGGER ANIMATION
     ══════════════════════════════════════════════════════════════════════════ */
  function staggerTableRows(tbody) {
    if (!tbody) return;
    const rows = tbody.querySelectorAll('tr');
    rows.forEach((row, i) => {
      row.style.animationDelay = `${i * 30}ms`;
      row.classList.add('sl-row-enter');
    });
  }

  window.SLStaggerRows = staggerTableRows;

  /* ══════════════════════════════════════════════════════════════════════════
     11. DUPLICATE BUTTON CLEANUP (Active Bonds)
     ══════════════════════════════════════════════════════════════════════════ */
  function cleanupDuplicateButtons() {
    // Active Bonds tab has two "Export CSV" buttons — remove the old one
    const activeBondsFilters = document.querySelector('#tabActiveBonds .filters');
    if (!activeBondsFilters) return;

    // The old btn-export with onclick="exportActiveBondsCSV()" is the duplicate
    const oldExport = activeBondsFilters.querySelector('[onclick="exportActiveBondsCSV()"]');
    if (oldExport) oldExport.remove();
  }

  /* ══════════════════════════════════════════════════════════════════════════
     12. NUMBER FORMATTING HELPERS
     ══════════════════════════════════════════════════════════════════════════ */

  /**
   * Format a number with K/M suffix.
   * @param {number} n
   * @param {string} prefix - e.g. '$'
   * @returns {string}
   */
  function fmtCompact(n, prefix = '') {
    if (n == null || isNaN(n)) return '—';
    if (n >= 1_000_000) return prefix + (n / 1_000_000).toFixed(1) + 'M';
    if (n >= 1_000)     return prefix + (n / 1_000).toFixed(1) + 'K';
    return prefix + n.toLocaleString();
  }

  /**
   * Format a currency value.
   * @param {number} n
   * @returns {string}
   */
  function fmtMoney(n) {
    if (n == null || isNaN(n)) return '—';
    return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }

  window.SLFmt = { compact: fmtCompact, money: fmtMoney };

  /* ══════════════════════════════════════════════════════════════════════════
     13. TAB CONTENT FADE-IN ON SWITCH
     ══════════════════════════════════════════════════════════════════════════ */
  function patchTabSwitcher() {
    // Observe tab-content becoming active and trigger animation
    const observer = new MutationObserver(mutations => {
      mutations.forEach(m => {
        if (m.type === 'attributes' && m.attributeName === 'class') {
          const target = m.target;
          if (target.classList.contains('tab-content') && target.classList.contains('active')) {
            // Re-trigger animation
            target.style.animation = 'none';
            target.offsetHeight; // reflow
            target.style.animation = '';
            // Animate counters after a short delay
            setTimeout(animateVisibleCounters, 200);
          }
        }
      });
    });

    $$('.tab-content').forEach(tc => {
      observer.observe(tc, { attributes: true });
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     14. RISK SCORE BAR RENDERER
     ══════════════════════════════════════════════════════════════════════════ */

  /**
   * Render a risk score as a colored bar + number.
   * @param {number} score - 0-100
   * @returns {string} HTML
   */
  function riskBar(score) {
    const s = parseInt(score) || 0;
    const cls = s >= 70 ? 'risk-high' : s >= 40 ? 'risk-mid' : 'risk-low';
    const color = s >= 70 ? 'var(--danger)' : s >= 40 ? 'var(--warning)' : 'var(--accent)';
    return `
      <div class="risk-bar-wrap ${cls}">
        <span style="font-size:12px;font-weight:700;color:${color};min-width:28px">${s}</span>
        <div class="risk-bar">
          <div class="risk-bar-fill" style="width:${s}%;background:${color}"></div>
        </div>
      </div>
    `;
  }

  window.SLRiskBar = riskBar;

  /* ══════════════════════════════════════════════════════════════════════════
     15. UPGRADE INLINE COUNTY CELLS TO BADGES
     ══════════════════════════════════════════════════════════════════════════ */
  function upgradeTableCountyCells() {
    // Find all table cells that contain just a county name
    $$('table tbody td').forEach(td => {
      if (td.dataset.countyUpgraded) return;
      const text = td.textContent.trim();
      if (COUNTY_COLORS[text]) {
        td.dataset.countyUpgraded = '1';
        td.innerHTML = countyBadge(text);
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     16. UPGRADE INTAKE STATS ROW
     ══════════════════════════════════════════════════════════════════════════ */
  function upgradeIntakeStatsRow() {
    const row = $('intakeStatsRow');
    if (!row || row.dataset.upgraded) return;
    // The stats row is populated by sl-intake.js — just add stat-card class to children
    const observer = new MutationObserver(() => {
      row.querySelectorAll(':scope > div:not(.stat-card)').forEach(d => {
        d.classList.add('stat-card');
      });
    });
    observer.observe(row, { childList: true });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     17. PERIODIC TABLE UPGRADE (for dynamically rendered tables)
     ══════════════════════════════════════════════════════════════════════════ */
  function periodicUpgrade() {
    upgradeTableCountyCells();
    upgradeCountySpans();
  }

  /* ══════════════════════════════════════════════════════════════════════════
     18. INIT — run after DOM is ready
     ══════════════════════════════════════════════════════════════════════════ */
  function init() {
    injectFavicon();
    injectHeaderSearchPill();
    upgradeTabBar();
    cleanupDuplicateButtons();
    upgradeIntakeStatsRow();
    patchTabSwitcher();
    animateVisibleCounters();
    periodicUpgrade();

    // Run periodic upgrades every 3 seconds for dynamic content
    setInterval(periodicUpgrade, 3000);

    // Stagger table rows on initial load
    $$('tbody').forEach(staggerTableRows);

    // Observe DOM for new table bodies
    const bodyObserver = new MutationObserver(mutations => {
      mutations.forEach(m => {
        m.addedNodes.forEach(node => {
          if (node.nodeType !== 1) return;
          if (node.tagName === 'TBODY') staggerTableRows(node);
          node.querySelectorAll && node.querySelectorAll('tbody').forEach(staggerTableRows);
        });
      });
    });
    bodyObserver.observe(document.body, { childList: true, subtree: true });

    console.log('[SL Overhaul v2.0] Initialized ✓');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    // DOM already ready
    setTimeout(init, 0);
  }

})();
