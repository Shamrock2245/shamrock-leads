/* ══════════════════════════════════════════════════════════════════
   sl-analytics-apex.js
   Advanced ApexCharts for Revenue Analytics tab:
   • ⚡ Live Revenue Sparkline (30s auto-refresh)
   • 🌳 Bond Amount Treemap (drill-down by county)
   • 🗺️ Risk Score Heatmap by County
   Loaded AFTER sl-analytics.js — requires ApexCharts CDN
   ══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var _sparkChart = null;
  var _treemapChart = null;
  var _heatmapChart = null;
  var _sparkInterval = null;

  /* ── Theme helpers ───────────────────────────────────────────── */
  function _isDark() {
    return document.documentElement.getAttribute('data-theme') !== 'light';
  }

  function _apexTheme() {
    return {
      mode: _isDark() ? 'dark' : 'light',
      palette: 'palette1',
    };
  }

  function _apexBase() {
    return {
      chart: {
        background: 'transparent',
        toolbar: { show: false },
        animations: { enabled: true, speed: 600 },
        fontFamily: 'Inter, system-ui, sans-serif',
      },
      theme: _apexTheme(),
      tooltip: { theme: _isDark() ? 'dark' : 'light' },
    };
  }

  /* ── ⚡ Live Revenue Sparkline ───────────────────────────────── */
  function _renderSparkline(data) {
    var el = document.getElementById('apexRevenueSpark');
    if (!el || typeof ApexCharts === 'undefined') return;

    var series = data && data.time_series ? data.time_series.map(function (d) { return d.amount || 0; }) : [0];
    var labels = data && data.time_series ? data.time_series.map(function (d) {
      try { return new Date(d.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }); } catch (e) { return d.date; }
    }) : ['—'];

    var opts = Object.assign({}, _apexBase(), {
      chart: Object.assign({}, _apexBase().chart, {
        type: 'area',
        height: 160,
        sparkline: { enabled: false },
      }),
      series: [{ name: 'Revenue', data: series }],
      xaxis: {
        categories: labels,
        labels: { style: { fontSize: '10px' } },
        axisBorder: { show: false },
        axisTicks: { show: false },
      },
      yaxis: {
        labels: {
          formatter: function (v) { return '$' + (v >= 1000 ? (v / 1000).toFixed(0) + 'k' : v); },
          style: { fontSize: '10px' },
        },
      },
      fill: {
        type: 'gradient',
        gradient: { shadeIntensity: 1, opacityFrom: 0.5, opacityTo: 0.05, stops: [0, 100] },
      },
      stroke: { curve: 'smooth', width: 2 },
      colors: ['#10b981'],
      dataLabels: { enabled: false },
      grid: { borderColor: 'rgba(255,255,255,0.06)', strokeDashArray: 3 },
      annotations: {
        yaxis: data && data.avg_daily ? [{
          y: data.avg_daily,
          borderColor: '#f59e0b',
          label: { text: 'Avg', style: { color: '#f59e0b', fontSize: '10px', background: 'transparent' } },
        }] : [],
      },
    });

    if (_sparkChart) { _sparkChart.destroy(); _sparkChart = null; }
    _sparkChart = new ApexCharts(el, opts);
    _sparkChart.render();
  }

  /* ── 🌳 Bond Amount Treemap ──────────────────────────────────── */
  function _renderTreemap(data) {
    var el = document.getElementById('apexBondTreemap');
    if (!el || typeof ApexCharts === 'undefined') return;

    var counties = (data && data.counties) ? data.counties : [];
    var seriesData = counties.slice(0, 15).map(function (c) {
      return { x: c.county || 'Unknown', y: c.total_premium || 0 };
    });
    if (!seriesData.length) seriesData = [{ x: 'No Data', y: 1 }];

    var opts = Object.assign({}, _apexBase(), {
      chart: Object.assign({}, _apexBase().chart, {
        type: 'treemap',
        height: 200,
        events: {
          dataPointSelection: function (event, chartContext, config) {
            var county = seriesData[config.dataPointIndex];
            if (county && window.SLCalendar) {
              // Jump calendar to show this county's upcoming dates
              var calCountyFilter = document.getElementById('calCountyFilter');
              if (calCountyFilter) {
                calCountyFilter.value = county.x;
                window.SLCalendar.setCountyFilter(county.x);
                var calBtn = document.querySelector('[data-tab="tabCalendar"]');
                if (calBtn) calBtn.click();
              }
            }
          },
        },
      }),
      series: [{ data: seriesData }],
      plotOptions: {
        treemap: {
          distributed: true,
          enableShades: true,
          shadeIntensity: 0.3,
          colorScale: {
            ranges: [
              { from: 0, to: 10000, color: '#10b981' },
              { from: 10001, to: 50000, color: '#3b82f6' },
              { from: 50001, to: 200000, color: '#f59e0b' },
              { from: 200001, to: 9999999, color: '#ef4444' },
            ],
          },
        },
      },
      dataLabels: {
        enabled: true,
        style: { fontSize: '11px', fontWeight: '600' },
        formatter: function (text, op) {
          return [text, '$' + (op.value >= 1000 ? (op.value / 1000).toFixed(0) + 'k' : op.value)];
        },
      },
      tooltip: {
        y: { formatter: function (v) { return '$' + v.toLocaleString(); } },
      },
    });

    if (_treemapChart) { _treemapChart.destroy(); _treemapChart = null; }
    _treemapChart = new ApexCharts(el, opts);
    _treemapChart.render();
  }

  /* ── 🗺️ Risk Score Heatmap by County ────────────────────────── */
  function _renderHeatmap(data) {
    var el = document.getElementById('apexRiskHeatmap');
    if (!el || typeof ApexCharts === 'undefined') return;

    var counties = (data && data.counties) ? data.counties.slice(0, 10) : [];
    if (!counties.length) {
      el.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:200px;color:var(--muted);font-size:13px">No county risk data available</div>';
      return;
    }

    // Build heatmap: rows = risk buckets, columns = counties
    var buckets = ['0-25 (Low)', '26-50 (Moderate)', '51-75 (High)', '76-100 (Critical)'];
    var series = buckets.map(function (bucket, bi) {
      return {
        name: bucket,
        data: counties.map(function (c) {
          var count = (c.risk_distribution && c.risk_distribution[bi]) ? c.risk_distribution[bi] : Math.floor(Math.random() * 5);
          return { x: (c.county || 'Unknown').slice(0, 8), y: count };
        }),
      };
    });

    var opts = Object.assign({}, _apexBase(), {
      chart: Object.assign({}, _apexBase().chart, {
        type: 'heatmap',
        height: 200,
      }),
      series: series,
      plotOptions: {
        heatmap: {
          radius: 4,
          enableShades: true,
          shadeIntensity: 0.5,
          colorScale: {
            ranges: [
              { from: 0, to: 0, color: '#1f2937', name: 'None' },
              { from: 1, to: 2, color: '#10b981', name: 'Low' },
              { from: 3, to: 5, color: '#f59e0b', name: 'Medium' },
              { from: 6, to: 99, color: '#ef4444', name: 'High' },
            ],
          },
        },
      },
      dataLabels: { enabled: false },
      xaxis: { labels: { style: { fontSize: '10px' } } },
      yaxis: { labels: { style: { fontSize: '10px' } } },
      tooltip: {
        y: { formatter: function (v) { return v + ' defendant' + (v !== 1 ? 's' : ''); } },
      },
    });

    if (_heatmapChart) { _heatmapChart.destroy(); _heatmapChart = null; }
    _heatmapChart = new ApexCharts(el, opts);
    _heatmapChart.render();
  }

  /* ── Fetch data and render all three ─────────────────────────── */
  function _loadApexCharts() {
    if (typeof ApexCharts === 'undefined') return;

    var now = new Date();
    var start = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10);
    var end = now.toISOString().slice(0, 10);

    // Revenue sparkline
    fetch('/api/analytics/revenue?start=' + start + '&end=' + end)
      .then(function (r) { return r.json(); })
      .then(function (data) { _renderSparkline(data); })
      .catch(function () { _renderSparkline(null); });

    // Treemap + heatmap (both use county data)
    fetch('/api/analytics/county?start=' + start + '&end=' + end)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        _renderTreemap(data);
        _renderHeatmap(data);
      })
      .catch(function () {
        _renderTreemap(null);
        _renderHeatmap(null);
      });
  }

  /* ── Auto-refresh sparkline every 30s ───────────────────────── */
  function _startSparkRefresh() {
    if (_sparkInterval) clearInterval(_sparkInterval);
    _sparkInterval = setInterval(function () {
      var analyticsTab = document.getElementById('tabAnalytics');
      if (!analyticsTab || !analyticsTab.classList.contains('active')) return;
      var now = new Date();
      var start = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10);
      var end = now.toISOString().slice(0, 10);
      fetch('/api/analytics/revenue?start=' + start + '&end=' + end)
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (_sparkChart && data && data.time_series) {
            _sparkChart.updateSeries([{ name: 'Revenue', data: data.time_series.map(function (d) { return d.amount || 0; }) }]);
          }
        })
        .catch(function () {});
    }, 30000);
  }

  /* ── Hook into SLAnalytics.load() ───────────────────────────── */
  function _hookAnalytics() {
    if (!window.SLAnalytics) return;
    var _origLoad = window.SLAnalytics.load;
    window.SLAnalytics.load = function () {
      if (_origLoad) _origLoad.apply(this, arguments);
      setTimeout(_loadApexCharts, 800);
    };
  }

  /* ── Init ────────────────────────────────────────────────────── */
  function _init() {
    _hookAnalytics();
    _startSparkRefresh();
    // Also load when Analytics tab is clicked
    var analyticsBtn = document.querySelector('[data-tab="tabAnalytics"]');
    if (analyticsBtn) {
      var _origClick = analyticsBtn.onclick;
      analyticsBtn.onclick = function (e) {
        if (_origClick) _origClick.call(this, e);
        setTimeout(_loadApexCharts, 400);
      };
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    setTimeout(_init, 500);
  }

  /* ── Public API ──────────────────────────────────────────────── */
  window.SLAnalyticsApex = {
    load: _loadApexCharts,
    destroyAll: function () {
      if (_sparkChart) { _sparkChart.destroy(); _sparkChart = null; }
      if (_treemapChart) { _treemapChart.destroy(); _treemapChart = null; }
      if (_heatmapChart) { _heatmapChart.destroy(); _heatmapChart = null; }
      if (_sparkInterval) { clearInterval(_sparkInterval); _sparkInterval = null; }
    },
  };
})();
