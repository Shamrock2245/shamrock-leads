/* ═══════════════════════════════════════════════════════════════════════════
   ShamrockLeads — Reports UI Upgrade  v2.0
   Upgrades the Reports tab with modern layout, trend indicators,
   last-run timestamps, PDF/CSV export, and consistent card styling.
   Patches into the existing SLReports module without breaking it.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const toast = (m, t) => { if (window.SL?.toast) SL.toast(m, t); };

  /* ── Track last-run times per report ───────────────────────────────────── */
  var _lastRun = {};
  var _lastData = {};

  /* ── 1. Upgrade the Reports toolbar ────────────────────────────────────── */
  function upgradeToolbar() {
    var toolbar = document.querySelector('.rpt-toolbar');
    if (!toolbar || toolbar.dataset.upgraded) return;
    toolbar.dataset.upgraded = '1';

    // Wrap preset buttons in sl-segmented
    var presetBtns = toolbar.querySelectorAll('.rpt-preset-btn');
    if (presetBtns.length) {
      var seg = document.createElement('div');
      seg.className = 'sl-segmented';
      presetBtns.forEach(function (btn) {
        btn.className = btn.classList.contains('rpt-preset-active') ? 'active' : '';
        seg.appendChild(btn);
      });
      toolbar.insertBefore(seg, toolbar.firstChild);
    }

    // Add Today and Week presets if missing
    var seg2 = toolbar.querySelector('.sl-segmented');
    if (seg2 && !seg2.querySelector('[data-preset="today"]')) {
      var todayBtn = document.createElement('button');
      todayBtn.dataset.preset = 'today';
      todayBtn.textContent = 'Today';
      todayBtn.onclick = function () { SLReports.setPreset('today'); };
      seg2.insertBefore(todayBtn, seg2.firstChild);

      var weekBtn = document.createElement('button');
      weekBtn.dataset.preset = 'week';
      weekBtn.textContent = 'Week';
      weekBtn.onclick = function () { SLReports.setPreset('week'); };
      seg2.insertBefore(weekBtn, seg2.children[1]);
    }

    // Add export buttons to toolbar right side
    var spacer = document.createElement('span');
    spacer.style.flex = '1';
    toolbar.appendChild(spacer);

    var pdfBtn = document.createElement('button');
    pdfBtn.className = 'sl-btn sl-btn-secondary';
    pdfBtn.innerHTML = '⬇ PDF';
    pdfBtn.title = 'Export current report as PDF';
    pdfBtn.onclick = function () { SLReportsUI.exportPDF(); };
    toolbar.appendChild(pdfBtn);

    var csvBtn = document.createElement('button');
    csvBtn.className = 'sl-btn sl-btn-secondary';
    csvBtn.innerHTML = '⬇ CSV';
    csvBtn.title = 'Export current report as CSV';
    csvBtn.onclick = function () { SLReportsUI.exportCSV(); };
    toolbar.appendChild(csvBtn);

    var printBtn = document.createElement('button');
    printBtn.className = 'sl-btn sl-btn-ghost sl-btn-icon';
    printBtn.innerHTML = '🖨';
    printBtn.title = 'Print report';
    printBtn.onclick = function () { window.print(); };
    toolbar.appendChild(printBtn);

    var refreshBtn = document.createElement('button');
    refreshBtn.className = 'sl-btn sl-btn-ghost sl-btn-icon';
    refreshBtn.innerHTML = '↻';
    refreshBtn.title = 'Refresh';
    refreshBtn.onclick = function () { if (window.SLReports) { SLReports.setPreset(SLReports._currentPreset || 'mtd'); } };
    toolbar.appendChild(refreshBtn);
  }

  /* ── 2. Upgrade KPI strip ──────────────────────────────────────────────── */
  function upgradeKPIStrip() {
    var strip = $('rptKpiStrip');
    if (!strip || strip.dataset.upgraded) return;
    strip.dataset.upgraded = '1';

    // Add trend arrows and accent colors
    var kpiConfigs = [
      { id: 'rptKpiLiability', accent: '#3b82f6', trend: null },
      { id: 'rptKpiBonds',     accent: '#10b981', trend: 'up' },
      { id: 'rptKpiDischarged', accent: '#06b6d4', trend: 'up' },
      { id: 'rptKpiForfeitures', accent: '#ef4444', trend: 'down', alert: true },
      { id: 'rptKpiCompliance', accent: '#8b5cf6', trend: 'up' },
      { id: 'rptKpiPOA',       accent: '#f59e0b', trend: null }
    ];

    strip.querySelectorAll('.rpt-kpi-item').forEach(function (item, i) {
      var cfg = kpiConfigs[i];
      if (!cfg) return;
      item.style.setProperty('--rpt-accent', cfg.accent);
      item.style.borderBottom = '2px solid ' + cfg.accent;
      item.style.borderRadius = 'var(--r-sm)';
      item.style.padding = '14px';
      item.style.background = 'var(--card)';
      item.style.border = '1px solid var(--border)';
      item.style.borderBottomColor = cfg.accent;
      item.style.transition = 'all .2s';
      item.style.cursor = 'default';

      // Add trend indicator placeholder
      var trendEl = document.createElement('div');
      trendEl.className = 'sl-rpt-kpi-trend ' + (cfg.trend || 'flat');
      trendEl.id = cfg.id + 'Trend';
      trendEl.style.fontSize = '11px';
      trendEl.style.fontWeight = '700';
      trendEl.style.marginTop = '3px';
      item.appendChild(trendEl);

      if (cfg.alert) item.classList.add('rpt-kpi-alert');
    });
  }

  /* ── 3. Upgrade report cards ───────────────────────────────────────────── */
  var REPORT_COLORS = {
    'surety-liability': '#3b82f6',
    'agent-production': '#10b981',
    'discharged':       '#06b6d4',
    'forfeitures':      '#ef4444',
    'court-compliance': '#8b5cf6',
    'poa-inventory':    '#f59e0b',
    'revenue':          '#10b981',
    'county-breakdown': '#f97316',
    'risk-analysis':    '#ec4899'
  };

  var REPORT_ICONS = {
    'surety-liability': '🛡️',
    'agent-production': '👤',
    'discharged':       '🏛️',
    'forfeitures':      '⚠️',
    'court-compliance': '⚖️',
    'poa-inventory':    '📋',
    'revenue':          '💰',
    'county-breakdown': '🗺️',
    'risk-analysis':    '🎯'
  };

  function upgradeReportCards() {
    var grid = $('rptCardGrid');
    if (!grid || grid.dataset.upgraded) return;
    grid.dataset.upgraded = '1';

    grid.querySelectorAll('.rpt-card').forEach(function (card) {
      var reportType = card.dataset.report;
      var color = REPORT_COLORS[reportType] || 'var(--accent)';

      // Apply color accent
      card.style.setProperty('--rpt-color', color);
      card.style.borderTop = '3px solid ' + color;
      card.style.transition = 'all .2s';
      card.style.position = 'relative';

      // Upgrade icon wrap
      var iconWrap = card.querySelector('.rpt-card-icon-wrap');
      if (iconWrap) {
        iconWrap.style.background = color + '18';
        iconWrap.style.border = '1px solid ' + color + '30';
        iconWrap.style.borderRadius = '10px';
        iconWrap.style.width = '44px';
        iconWrap.style.height = '44px';
        iconWrap.style.display = 'flex';
        iconWrap.style.alignItems = 'center';
        iconWrap.style.justifyContent = 'center';
        iconWrap.style.fontSize = '22px';
      }

      // Add last-run timestamp
      if (!card.querySelector('.sl-rpt-last-run')) {
        var footer = card.querySelector('.rpt-card-footer');
        if (footer) {
          var lastRunEl = document.createElement('span');
          lastRunEl.className = 'sl-rpt-last-run';
          lastRunEl.id = 'rptLastRun_' + reportType;
          lastRunEl.style.cssText = 'font-size:10px;color:var(--muted);display:block;margin-top:4px';
          lastRunEl.textContent = 'Never run';
          footer.parentNode.insertBefore(lastRunEl, footer);
        }
      }

      // Style the "Run Report →" text with color
      var runEl = card.querySelector('.rpt-card-run');
      if (runEl) {
        runEl.style.color = color;
        runEl.style.fontWeight = '700';
        runEl.style.fontSize = '12px';
      }

      // Add hover glow
      card.addEventListener('mouseenter', function () {
        this.style.boxShadow = '0 4px 20px ' + color + '25';
        this.style.transform = 'translateY(-2px)';
      });
      card.addEventListener('mouseleave', function () {
        this.style.boxShadow = '';
        this.style.transform = '';
      });
    });

    // Add "Section" label above grid
    var label = grid.previousElementSibling;
    if (label && label.classList.contains('rpt-section-label')) {
      label.style.cssText = 'font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:12px;display:flex;align-items:center;gap:8px';
      label.innerHTML = '<span>SELECT A REPORT TO RUN</span><span style="flex:1;height:1px;background:var(--border)"></span>';
    }
  }

  /* ── 4. Upgrade report output panel ────────────────────────────────────── */
  function upgradeOutputPanel() {
    var output = $('rptOutput');
    if (!output || output.dataset.upgraded) return;
    output.dataset.upgraded = '1';

    // Add a header bar to the output panel
    var header = document.createElement('div');
    header.id = 'rptOutputHeader';
    header.style.cssText = 'display:flex;align-items:center;gap:8px;padding:12px 16px;background:var(--surface);border-bottom:1px solid var(--border);border-radius:var(--r-md) var(--r-md) 0 0';
    header.innerHTML = `
      <span id="rptOutputTitle" style="font-size:14px;font-weight:700;color:var(--text);flex:1">Report Output</span>
      <button class="sl-btn sl-btn-ghost sl-btn-icon" onclick="SLReportsUI.exportPDF()" title="Export as PDF">⬇ PDF</button>
      <button class="sl-btn sl-btn-ghost sl-btn-icon" onclick="SLReportsUI.exportCSV()" title="Export as CSV">⬇ CSV</button>
      <button class="sl-btn sl-btn-ghost sl-btn-icon" onclick="window.print()" title="Print">🖨</button>
    `;
    output.insertBefore(header, output.firstChild);
  }

  /* ── 5. Intercept SLReports.generate() to track last-run ───────────────── */
  function patchSLReports() {
    if (!window.SLReports || window.SLReports._uiPatched) return;
    window.SLReports._uiPatched = true;

    var origGenerate = SLReports.generate;
    SLReports.generate = function (reportType) {
      // Track last run
      _lastRun[reportType] = new Date();
      var el = $('rptLastRun_' + reportType);
      if (el) el.textContent = 'Running…';

      // Highlight active card
      document.querySelectorAll('.rpt-card').forEach(function (c) {
        c.classList.toggle('active', c.dataset.report === reportType);
      });

      // Update output panel title
      var titleEl = $('rptOutputTitle');
      if (titleEl) {
        var card = document.querySelector('.rpt-card[data-report="' + reportType + '"]');
        var cardTitle = card ? (card.querySelector('.rpt-card-title') || {}).textContent : reportType;
        titleEl.textContent = cardTitle || reportType;
      }

      // Call original
      var result = origGenerate.call(SLReports, reportType);

      // Update last-run after a tick
      setTimeout(function () {
        var el2 = $('rptLastRun_' + reportType);
        if (el2) el2.textContent = 'Last run: just now';
      }, 2000);

      return result;
    };
  }

  /* ── 6. Upgrade the section label ──────────────────────────────────────── */
  function upgradeLayout() {
    var tab = $('tabReports');
    if (!tab || tab.dataset.layoutUpgraded) return;
    tab.dataset.layoutUpgraded = '1';

    // Add a proper toolbar at the top if not already using sl-toolbar
    var container = tab.querySelector('.container');
    if (!container) return;

    // Ensure the toolbar has the right class
    var toolbar = container.querySelector('.rpt-toolbar');
    if (toolbar) {
      toolbar.style.display = 'flex';
      toolbar.style.alignItems = 'center';
      toolbar.style.gap = '8px';
      toolbar.style.padding = '12px 16px';
      toolbar.style.background = 'var(--surface)';
      toolbar.style.border = '1px solid var(--border)';
      toolbar.style.borderRadius = 'var(--r-md)';
      toolbar.style.marginBottom = '16px';
      toolbar.style.flexWrap = 'wrap';
    }

    // Add a title to the toolbar
    if (toolbar && !toolbar.querySelector('.sl-toolbar-title')) {
      var title = document.createElement('span');
      title.className = 'sl-toolbar-title';
      title.innerHTML = '📊 Reports <span>& Analytics</span>';
      toolbar.insertBefore(title, toolbar.firstChild);

      var divider = document.createElement('span');
      divider.className = 'sl-toolbar-divider';
      toolbar.insertBefore(divider, toolbar.children[1]);
    }

    // Style the KPI strip
    var kpiStrip = $('rptKpiStrip');
    if (kpiStrip) {
      kpiStrip.style.display = 'grid';
      kpiStrip.style.gridTemplateColumns = 'repeat(6, 1fr)';
      kpiStrip.style.gap = '12px';
      kpiStrip.style.marginBottom = '20px';
    }

    // Style the card grid
    var cardGrid = $('rptCardGrid');
    if (cardGrid) {
      cardGrid.style.display = 'grid';
      cardGrid.style.gridTemplateColumns = 'repeat(auto-fill, minmax(260px, 1fr))';
      cardGrid.style.gap = '12px';
      cardGrid.style.marginBottom = '20px';
    }
  }

  /* ── 7. Export functions ────────────────────────────────────────────────── */
  function exportPDF() {
    var output = $('rptOutput');
    if (!output || output.style.display === 'none') {
      toast('Run a report first, then export', 'warning');
      return;
    }
    toast('Preparing PDF…', 'info');
    // Use browser print with print-specific CSS
    var style = document.createElement('style');
    style.id = 'sl-print-style';
    style.textContent = '@media print { body > *:not(#tabReports) { display: none !important; } #tabReports { display: block !important; } .tab-nav, .outreach-action-bar, .rpt-toolbar, .rpt-kpi-strip, .rpt-card-grid, #rptOutputHeader { display: none !important; } }';
    document.head.appendChild(style);
    setTimeout(function () {
      window.print();
      setTimeout(function () {
        var s = $('sl-print-style');
        if (s) s.remove();
      }, 1000);
    }, 300);
  }

  function exportCSV() {
    var output = $('rptOutput');
    if (!output || output.style.display === 'none') {
      toast('Run a report first, then export', 'warning');
      return;
    }
    // Find the table in the output
    var table = output.querySelector('table');
    if (!table) {
      toast('No table data to export', 'warning');
      return;
    }
    var rows = [];
    table.querySelectorAll('tr').forEach(function (tr) {
      var cells = Array.from(tr.querySelectorAll('th, td')).map(function (cell) {
        return '"' + cell.textContent.trim().replace(/"/g, '""') + '"';
      });
      rows.push(cells.join(','));
    });
    var csv = rows.join('\n');
    var blob = new Blob([csv], { type: 'text/csv' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'shamrock-report-' + new Date().toISOString().slice(0, 10) + '.csv';
    a.click();
    URL.revokeObjectURL(url);
    toast('CSV exported', 'success');
  }

  /* ── 8. Add trend indicators to KPI values ─────────────────────────────── */
  function updateKPITrends() {
    // Forfeitures — always show as warning if > 0
    var forfEl = $('rptKpiForfeitures');
    if (forfEl) {
      var val = parseInt(forfEl.textContent.replace(/\D/g, '')) || 0;
      var trendEl = $('rptKpiForfeituresTrend');
      if (trendEl) {
        if (val > 0) {
          trendEl.textContent = '⚠ ' + val + ' active';
          trendEl.className = 'sl-rpt-kpi-trend down';
        } else {
          trendEl.textContent = '✓ Clear';
          trendEl.className = 'sl-rpt-kpi-trend up';
        }
      }
    }

    // Compliance rate
    var compEl = $('rptKpiCompliance');
    if (compEl) {
      var pct = parseFloat(compEl.textContent) || 0;
      var trendEl2 = $('rptKpiComplianceTrend');
      if (trendEl2) {
        if (pct >= 80) {
          trendEl2.textContent = '↑ On track';
          trendEl2.className = 'sl-rpt-kpi-trend up';
        } else if (pct >= 60) {
          trendEl2.textContent = '→ Needs attention';
          trendEl2.className = 'sl-rpt-kpi-trend flat';
        } else {
          trendEl2.textContent = '↓ Below target';
          trendEl2.className = 'sl-rpt-kpi-trend down';
        }
      }
    }
  }

  /* ── 9. Watch for KPI value changes ────────────────────────────────────── */
  function watchKPIs() {
    var strip = $('rptKpiStrip');
    if (!strip) return;
    var observer = new MutationObserver(function () {
      updateKPITrends();
    });
    observer.observe(strip, { childList: true, subtree: true, characterData: true });
  }

  /* ── 9b. Fetch real period-over-period trend data from API ─────────────── */
  function fetchAndApplyTrends() {
    fetch('/api/reports/kpi-trends?days=30')
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) {
        if (!d || !d.success) return;
        function applyTrend(elId, data) {
          var el = $(elId + 'Trend');
          if (!el) return;
          var pct = data && data.pct_change;
          if (pct == null) { el.textContent = '—'; el.className = 'sl-rpt-kpi-trend flat'; return; }
          var arrow = pct > 0 ? '↑' : pct < 0 ? '↓' : '→';
          var cls   = pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
          el.textContent = arrow + ' ' + Math.abs(pct).toFixed(1) + '% vs prior 30d';
          el.className = 'sl-rpt-kpi-trend ' + cls;
        }
        applyTrend('rptKpiBonds',     d.bonds);
        applyTrend('rptKpiDischarged', d.discharged);
        applyTrend('rptKpiLiability',  d.surety_liability);
        applyTrend('rptKpiPOA',        d.poa_used);
      })
      .catch(function() {});
  }
  /* ── 10. Init ───────────────────────────────────────────────────────────── */
  function init() {
    upgradeLayout();
    upgradeToolbar();
    upgradeKPIStrip();
    upgradeReportCards();
    upgradeOutputPanel();
    patchSLReports();
    watchKPIs();
    setTimeout(fetchAndApplyTrends, 800);
  }

  // Run after DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(init, 400); });
  } else {
    setTimeout(init, 400);
  }

  // Re-run when Reports tab is activated
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[onclick*="tabReports"], [data-tab="tabReports"]');
    if (btn) setTimeout(init, 150);
  });

  /* ── Public API ─────────────────────────────────────────────────────────── */
  window.SLReportsUI = {
    exportPDF: exportPDF,
    exportCSV: exportCSV,
    refresh: init
  };

})();
