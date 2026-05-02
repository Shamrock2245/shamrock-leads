/* ═══════════════════════════════════════════════════════════════════════════
   ShamrockLeads — Cross-Tab Polish  v1.0
   Applies consistent design system upgrades to all tabs:
   Command Center, Lead Explorer, Defendants, Active Bonds, Tracking,
   Intake Queue, Indemnitors, POA Inventory, Calendar, Analytics, Health
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const toast = (m, t) => { if (window.SL?.toast) SL.toast(m, t); };

  /* ══════════════════════════════════════════════════════════════════════════
     UNIVERSAL UPGRADES — applied to every tab
     ══════════════════════════════════════════════════════════════════════════ */

  /* 1. Upgrade all action bars to use sl-toolbar pattern */
  function upgradeActionBars() {
    var selectors = [
      '.leads-action-bar',
      '.defendants-action-bar',
      '.active-bonds-action-bar',
      '.tracking-action-bar',
      '.intake-action-bar',
      '.indemnitor-action-bar',
      '.inventory-action-bar',
      '.calendar-action-bar',
      '.analytics-action-bar',
      '.health-action-bar',
      '.command-action-bar'
    ];
    selectors.forEach(function (sel) {
      document.querySelectorAll(sel + ':not([data-polished])').forEach(function (bar) {
        bar.dataset.polished = '1';
        bar.style.display = 'flex';
        bar.style.alignItems = 'center';
        bar.style.gap = '8px';
        bar.style.padding = '10px 16px';
        bar.style.background = 'var(--surface)';
        bar.style.border = '1px solid var(--border)';
        bar.style.borderRadius = 'var(--r-md)';
        bar.style.marginBottom = '16px';
        bar.style.flexWrap = 'wrap';
      });
    });
  }

  /* 2. Upgrade all .btn-secondary and .btn-primary to sl-btn pattern */
  function upgradeButtons() {
    // Only upgrade buttons inside tab-content that haven't been upgraded
    document.querySelectorAll('.tab-content .btn-primary:not(.sl-btn)').forEach(function (btn) {
      btn.classList.add('sl-btn', 'sl-btn-primary');
    });
    document.querySelectorAll('.tab-content .btn-secondary:not(.sl-btn)').forEach(function (btn) {
      btn.classList.add('sl-btn', 'sl-btn-secondary');
    });
    document.querySelectorAll('.tab-content .btn-cancel:not(.sl-btn)').forEach(function (btn) {
      btn.classList.add('sl-btn', 'sl-btn-ghost');
    });
    document.querySelectorAll('.tab-content .btn-danger:not(.sl-btn)').forEach(function (btn) {
      btn.classList.add('sl-btn', 'sl-btn-danger');
    });
  }

  /* 3. Add loading spinners to all empty loading states */
  function upgradeLoadingStates() {
    document.querySelectorAll('[id$="Loading"]:not([data-polished])').forEach(function (el) {
      el.dataset.polished = '1';
      if (!el.querySelector('.sl-spinner')) {
        el.innerHTML = '<div class="sl-loading-overlay"><div class="sl-spinner"></div> Loading…</div>';
      }
    });
  }

  /* 4. Upgrade all filter bars */
  function upgradeFilterBars() {
    document.querySelectorAll('.filter-bar:not([data-polished]), .search-bar:not([data-polished])').forEach(function (bar) {
      bar.dataset.polished = '1';
      bar.style.display = 'flex';
      bar.style.alignItems = 'center';
      bar.style.gap = '8px';
      bar.style.marginBottom = '12px';
      bar.style.flexWrap = 'wrap';
    });
  }

  /* 5. Add section headers to tabs that are missing them */
  function addSectionHeaders() {
    var tabHeaders = {
      'tabLeads': { icon: '🔍', title: 'Lead Explorer', subtitle: 'Arrest records & booking data' },
      'tabDefendants': { icon: '👤', title: 'Defendants', subtitle: 'Active defendant profiles' },
      'tabActiveBonds': { icon: '🔗', title: 'Active Bonds', subtitle: 'All bonds currently in force' },
      'tabTracking': { icon: '📍', title: 'Bond Tracking', subtitle: 'Location & check-in monitoring' },
      'tabIntake': { icon: '📋', title: 'Intake Queue', subtitle: 'New inquiries & applications' },
      'tabIndemnitor': { icon: '🤝', title: 'Indemnitors', subtitle: 'Co-signers & guarantors' },
      'tabHealth': { icon: '💚', title: 'System Health', subtitle: 'Scraper & service status' }
    };

    Object.keys(tabHeaders).forEach(function (tabId) {
      var tab = $(tabId);
      if (!tab || tab.dataset.headerAdded) return;
      var container = tab.querySelector('.container');
      if (!container) return;

      // Check if there's already a title in the action bar
      var existingTitle = container.querySelector('.bar-title, .tab-title, .sl-toolbar-title');
      if (existingTitle) return;

      tab.dataset.headerAdded = '1';
      var cfg = tabHeaders[tabId];

      // Add title to the first action bar if it exists
      var actionBar = container.querySelector('[class*="action-bar"]');
      if (actionBar && !actionBar.querySelector('.bar-title')) {
        var titleEl = document.createElement('span');
        titleEl.className = 'bar-title';
        titleEl.style.cssText = 'font-size:15px;font-weight:700;color:var(--text);margin-right:8px;white-space:nowrap';
        titleEl.innerHTML = cfg.icon + ' ' + cfg.title;
        actionBar.insertBefore(titleEl, actionBar.firstChild);
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     COMMAND CENTER TAB
     ══════════════════════════════════════════════════════════════════════════ */
  function polishCommandCenter() {
    var tab = $('tabCommand');
    if (!tab || tab.dataset.polished) return;
    tab.dataset.polished = '1';

    // Upgrade KPI cards
    tab.querySelectorAll('.kpi-card:not([data-polished])').forEach(function (card) {
      card.dataset.polished = '1';
      card.style.transition = 'all .2s';
      card.style.cursor = 'default';
      card.addEventListener('mouseenter', function () {
        this.style.transform = 'translateY(-2px)';
        this.style.boxShadow = 'var(--shadow-md)';
      });
      card.addEventListener('mouseleave', function () {
        this.style.transform = '';
        this.style.boxShadow = '';
      });
    });

    // Add refresh button to command center if missing
    var actionBar = tab.querySelector('.command-action-bar, [class*="action-bar"]');
    if (actionBar && !actionBar.querySelector('[title="Refresh"]')) {
      var refreshBtn = document.createElement('button');
      refreshBtn.className = 'sl-btn sl-btn-ghost sl-btn-icon';
      refreshBtn.innerHTML = '↻';
      refreshBtn.title = 'Refresh dashboard';
      refreshBtn.onclick = function () {
        if (window.SLCommand) SLCommand.load();
        else if (window.SLDashboard) SLDashboard.load();
      };
      actionBar.appendChild(refreshBtn);
    }
  }

  /* ══════════════════════════════════════════════════════════════════════════
     LEAD EXPLORER TAB
     ══════════════════════════════════════════════════════════════════════════ */
  function polishLeadExplorer() {
    var tab = $('tabLeads');
    if (!tab || tab.dataset.polished) return;
    tab.dataset.polished = '1';

    // Add AI Score chip to lead cards
    tab.querySelectorAll('.lead-card:not([data-polished])').forEach(function (card) {
      card.dataset.polished = '1';
      card.style.transition = 'all .18s';
    });

    // Upgrade county filter chips
    tab.querySelectorAll('.county-chip:not([data-polished])').forEach(function (chip) {
      chip.dataset.polished = '1';
      chip.style.borderRadius = 'var(--r-pill)';
      chip.style.transition = 'all .15s';
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     DEFENDANTS TAB
     ══════════════════════════════════════════════════════════════════════════ */
  function polishDefendants() {
    var tab = $('tabDefendants');
    if (!tab || tab.dataset.polished) return;
    tab.dataset.polished = '1';

    // Add section header to defendant cards grid
    var cardsContainer = tab.querySelector('#defendantCards, .defendant-cards');
    if (cardsContainer && !cardsContainer.previousElementSibling?.classList.contains('sl-section-header')) {
      var header = document.createElement('div');
      header.className = 'sl-section-header';
      header.innerHTML = '<span class="sl-section-title">Defendants</span><span class="sl-section-count" id="defCountBadge">—</span><span class="sl-section-spacer"></span>';
      cardsContainer.parentNode.insertBefore(header, cardsContainer);
    }
  }

  /* ══════════════════════════════════════════════════════════════════════════
     ACTIVE BONDS TAB
     ══════════════════════════════════════════════════════════════════════════ */
  function polishActiveBonds() {
    var tab = $('tabActiveBonds');
    if (!tab || tab.dataset.polished) return;
    tab.dataset.polished = '1';

    // Upgrade the table header styling
    var table = tab.querySelector('table');
    if (table) {
      table.classList.add('sl-table');
    }

    // Add "Today's Bonds" section header
    var toolbar = tab.querySelector('[class*="action-bar"]');
    if (toolbar && !toolbar.querySelector('.bar-title')) {
      var title = document.createElement('span');
      title.className = 'bar-title';
      title.style.cssText = 'font-size:15px;font-weight:700;color:var(--text);margin-right:8px';
      title.innerHTML = '🔗 Active Bonds';
      toolbar.insertBefore(title, toolbar.firstChild);
    }
  }

  /* ══════════════════════════════════════════════════════════════════════════
     TRACKING TAB
     ══════════════════════════════════════════════════════════════════════════ */
  function polishTracking() {
    var tab = $('tabTracking');
    if (!tab || tab.dataset.polished) return;
    tab.dataset.polished = '1';

    // Add status legend for map markers
    var mapContainer = tab.querySelector('#trackingMap, .tracking-map');
    if (mapContainer && !$('trackingLegend')) {
      var legend = document.createElement('div');
      legend.id = 'trackingLegend';
      legend.style.cssText = 'display:flex;gap:12px;align-items:center;padding:8px 0;font-size:11px;color:var(--muted);flex-wrap:wrap;margin-bottom:8px';
      legend.innerHTML = `
        <span style="font-weight:700;color:var(--text)">Map Legend:</span>
        <span><span style="color:#10b981">●</span> Checked In</span>
        <span><span style="color:#f59e0b">●</span> Overdue</span>
        <span><span style="color:#ef4444">●</span> Missed</span>
        <span><span style="color:#3b82f6">●</span> Court Today</span>
        <span><span style="color:#8b5cf6">●</span> Warrant Risk</span>
      `;
      mapContainer.parentNode.insertBefore(legend, mapContainer);
    }
  }

  /* ══════════════════════════════════════════════════════════════════════════
     INTAKE QUEUE TAB
     ══════════════════════════════════════════════════════════════════════════ */
  function polishIntakeQueue() {
    var tab = $('tabIntake');
    if (!tab || tab.dataset.polished) return;
    tab.dataset.polished = '1';

    // Add priority badge styling
    tab.querySelectorAll('.intake-priority:not([data-polished])').forEach(function (badge) {
      badge.dataset.polished = '1';
      var text = badge.textContent.toLowerCase();
      if (text.includes('high') || text.includes('urgent')) {
        badge.className += ' sl-badge sl-badge-red';
      } else if (text.includes('medium') || text.includes('normal')) {
        badge.className += ' sl-badge sl-badge-yellow';
      } else {
        badge.className += ' sl-badge sl-badge-gray';
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     INDEMNITORS TAB
     ══════════════════════════════════════════════════════════════════════════ */
  function polishIndemnitors() {
    var tab = $('tabIndemnitor');
    if (!tab || tab.dataset.polished) return;
    tab.dataset.polished = '1';

    // Add "verified" badge styling
    tab.querySelectorAll('.indem-verified:not([data-polished])').forEach(function (el) {
      el.dataset.polished = '1';
      el.className += ' sl-badge sl-badge-green';
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     POA INVENTORY TAB
     ══════════════════════════════════════════════════════════════════════════ */
  function polishPOAInventory() {
    var tab = $('tabPOA') || document.querySelector('[id*="POA"], [id*="poa"], [id*="inventory"]');
    if (!tab || tab.dataset.polished) return;
    tab.dataset.polished = '1';

    // Color-code stock level cells
    tab.querySelectorAll('.poa-stock-count:not([data-polished])').forEach(function (el) {
      el.dataset.polished = '1';
      var count = parseInt(el.textContent) || 0;
      if (count <= 2) {
        el.style.color = '#ef4444';
        el.style.fontWeight = '800';
      } else if (count <= 5) {
        el.style.color = '#f59e0b';
        el.style.fontWeight = '700';
      } else {
        el.style.color = '#10b981';
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     CALENDAR TAB
     ══════════════════════════════════════════════════════════════════════════ */
  function polishCalendar() {
    var tab = $('tabCalendar');
    if (!tab || tab.dataset.polished) return;
    tab.dataset.polished = '1';

    // Add view toggle buttons if missing
    var toolbar = tab.querySelector('[class*="action-bar"], .calendar-toolbar');
    if (toolbar && !toolbar.querySelector('.sl-segmented')) {
      var viewSeg = document.createElement('div');
      viewSeg.className = 'sl-segmented';
      viewSeg.innerHTML = `
        <button onclick="SLCalendar&&SLCalendar.setView('month')" id="calViewMonth">Month</button>
        <button onclick="SLCalendar&&SLCalendar.setView('week')" id="calViewWeek">Week</button>
        <button onclick="SLCalendar&&SLCalendar.setView('list')" id="calViewList">List</button>
      `;
      toolbar.appendChild(viewSeg);
    }
  }

  /* ══════════════════════════════════════════════════════════════════════════
     ANALYTICS TAB
     ══════════════════════════════════════════════════════════════════════════ */
  function polishAnalytics() {
    var tab = $('tabAnalytics');
    if (!tab || tab.dataset.polished) return;
    tab.dataset.polished = '1';

    // Add date range selector to analytics toolbar
    var toolbar = tab.querySelector('[class*="action-bar"], .analytics-toolbar');
    if (toolbar && !toolbar.querySelector('.sl-segmented')) {
      var seg = document.createElement('div');
      seg.className = 'sl-segmented';
      seg.innerHTML = `
        <button onclick="SLAnalytics&&SLAnalytics.setRange('7d')">7D</button>
        <button class="active" onclick="SLAnalytics&&SLAnalytics.setRange('30d')">30D</button>
        <button onclick="SLAnalytics&&SLAnalytics.setRange('90d')">90D</button>
        <button onclick="SLAnalytics&&SLAnalytics.setRange('ytd')">YTD</button>
      `;
      toolbar.appendChild(seg);
    }

    // Style chart panels
    tab.querySelectorAll('.chart-panel:not([data-polished])').forEach(function (panel) {
      panel.dataset.polished = '1';
      panel.style.background = 'var(--card)';
      panel.style.border = '1px solid var(--border)';
      panel.style.borderRadius = 'var(--r-md)';
      panel.style.overflow = 'hidden';
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     HEALTH TAB
     ══════════════════════════════════════════════════════════════════════════ */
  function polishHealth() {
    var tab = $('tabHealth');
    if (!tab || tab.dataset.polished) return;
    tab.dataset.polished = '1';

    // Add status dot colors
    tab.querySelectorAll('.health-status:not([data-polished])').forEach(function (el) {
      el.dataset.polished = '1';
      var text = el.textContent.toLowerCase();
      if (text.includes('ok') || text.includes('up') || text.includes('healthy')) {
        el.style.color = '#10b981';
      } else if (text.includes('warn') || text.includes('slow')) {
        el.style.color = '#f59e0b';
      } else if (text.includes('down') || text.includes('error') || text.includes('fail')) {
        el.style.color = '#ef4444';
      }
    });

    // Add auto-refresh indicator
    var toolbar = tab.querySelector('[class*="action-bar"]');
    if (toolbar && !$('healthAutoRefresh')) {
      var indicator = document.createElement('span');
      indicator.id = 'healthAutoRefresh';
      indicator.style.cssText = 'font-size:11px;color:var(--muted);margin-left:auto;display:flex;align-items:center;gap:4px';
      indicator.innerHTML = '<span style="width:6px;height:6px;border-radius:50%;background:#10b981;animation:sl-live-pulse 2s infinite;display:inline-block"></span> Auto-refresh every 60s';
      toolbar.appendChild(indicator);
    }
  }

  /* ══════════════════════════════════════════════════════════════════════════
     TAB NAV — add active indicator line
     ══════════════════════════════════════════════════════════════════════════ */
  function polishTabNav() {
    var nav = document.querySelector('.tab-nav');
    if (!nav || nav.dataset.polished) return;
    nav.dataset.polished = '1';

    nav.querySelectorAll('.tab-btn:not([data-polished])').forEach(function (btn) {
      btn.dataset.polished = '1';
      btn.style.transition = 'all .15s';
      btn.style.borderRadius = 'var(--r-sm) var(--r-sm) 0 0';
      btn.style.touchAction = 'manipulation';
      btn.style.webkitTapHighlightColor = 'transparent';
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     MODAL CONSISTENCY
     ══════════════════════════════════════════════════════════════════════════ */
  function polishModals() {
    document.querySelectorAll('.modal:not([data-polished])').forEach(function (modal) {
      modal.dataset.polished = '1';

      // Ensure modal-footer buttons are styled
      modal.querySelectorAll('.modal-footer .btn-primary').forEach(function (btn) {
        btn.classList.add('sl-btn', 'sl-btn-primary');
      });
      modal.querySelectorAll('.modal-footer .btn-cancel, .modal-footer .btn-secondary').forEach(function (btn) {
        btn.classList.add('sl-btn', 'sl-btn-ghost');
      });

      // Add iOS touch fix to modal overlay
      var overlay = modal.closest('.modal-overlay');
      if (overlay) {
        overlay.style.willChange = 'opacity';
        overlay.style.isolation = 'isolate';
        overlay.style.webkitOverflowScrolling = 'touch';
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     NOTES MODAL — specific iOS touch fix
     ══════════════════════════════════════════════════════════════════════════ */
  function fixNotesModalTouch() {
    // Find all notes-related buttons and ensure they have proper touch handling
    document.querySelectorAll('[onclick*="Notes"], [onclick*="notes"], [title*="Note"], [title*="note"]').forEach(function (btn) {
      if (btn.dataset.touchFixed) return;
      btn.dataset.touchFixed = '1';
      btn.style.touchAction = 'manipulation';
      btn.style.webkitTapHighlightColor = 'transparent';
      btn.style.cursor = 'pointer';
      // Ensure minimum touch target
      var rect = btn.getBoundingClientRect();
      if (rect.height < 40) btn.style.minHeight = '40px';
      if (rect.width < 40) btn.style.minWidth = '40px';
    });

    // Fix the notes modal overlay itself
    var notesOverlay = document.querySelector('#notesModal, .notes-modal, [id*="Notes"]');
    if (notesOverlay) {
      notesOverlay.style.willChange = 'opacity, transform';
      notesOverlay.style.isolation = 'isolate';
      notesOverlay.style.transform = 'translateZ(0)';
    }
  }

  /* ══════════════════════════════════════════════════════════════════════════
     MOBILE RESPONSIVE FIXES
     ══════════════════════════════════════════════════════════════════════════ */
  function applyMobileFixes() {
    if (window.innerWidth > 768) return;

    // Make all action bars horizontally scrollable on mobile
    document.querySelectorAll('[class*="action-bar"]:not([data-mobile-fixed])').forEach(function (bar) {
      bar.dataset.mobileFxied = '1';
      bar.style.overflowX = 'auto';
      bar.style.flexWrap = 'nowrap';
      bar.style.webkitOverflowScrolling = 'touch';
      bar.style.scrollbarWidth = 'none';
    });

    // Ensure all interactive elements have 40px+ touch targets
    document.querySelectorAll('button:not([data-touch-fixed])').forEach(function (btn) {
      btn.dataset.touchFixed = '1';
      btn.style.touchAction = 'manipulation';
      btn.style.webkitTapHighlightColor = 'transparent';
      var h = btn.offsetHeight;
      if (h > 0 && h < 40) btn.style.minHeight = '40px';
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
     INIT — run all polishes
     ══════════════════════════════════════════════════════════════════════════ */
  function init() {
    upgradeActionBars();
    upgradeButtons();
    upgradeFilterBars();
    addSectionHeaders();
    polishCommandCenter();
    polishLeadExplorer();
    polishDefendants();
    polishActiveBonds();
    polishTracking();
    polishIntakeQueue();
    polishIndemnitors();
    polishPOAInventory();
    polishCalendar();
    polishAnalytics();
    polishHealth();
    polishTabNav();
    polishModals();
    fixNotesModalTouch();
    applyMobileFixes();
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(init, 500); });
  } else {
    setTimeout(init, 500);
  }

  // Re-run on tab switches (catch dynamically rendered content)
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.tab-btn');
    if (btn) setTimeout(init, 250);
  });

  // Re-run on any major DOM mutations (catches async renders)
  var _debounceTimer = null;
  var _observer = new MutationObserver(function () {
    clearTimeout(_debounceTimer);
    _debounceTimer = setTimeout(function () {
      polishModals();
      fixNotesModalTouch();
      upgradeButtons();
    }, 300);
  });
  _observer.observe(document.body, { childList: true, subtree: true });

  window.SLTabPolish = { refresh: init };

})();
