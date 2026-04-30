/* ShamrockLeads — Revenue Analytics Module
   Requires Chart.js (loaded via CDN in index.html)
   Namespace: window.SLAnalytics
*/
(function() {
  'use strict';

  let _charts = {};
  let _currentDays = 30;
  let _data = {};

  // ── Color palette aligned with CSS custom properties ────────────────────
  function getColors() {
    const style = getComputedStyle(document.documentElement);
    return {
      accent:  style.getPropertyValue('--accent').trim()  || '#10b981',
      accent2: style.getPropertyValue('--accent2').trim() || '#6366f1',
      panel:   style.getPropertyValue('--panel').trim()   || '#1e293b',
      border:  style.getPropertyValue('--border').trim()  || '#334155',
      text:    style.getPropertyValue('--text').trim()    || '#f1f5f9',
      muted:   style.getPropertyValue('--muted').trim()   || '#94a3b8',
    };
  }

  function chartDefaults() {
    const c = getColors();
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: c.text, font: { family: 'Inter', size: 12 } } },
        tooltip: {
          backgroundColor: c.panel,
          borderColor: c.border,
          borderWidth: 1,
          titleColor: c.text,
          bodyColor: c.muted,
          padding: 10,
        }
      },
      scales: {
        x: { ticks: { color: c.muted, font: { size: 11 } }, grid: { color: c.border } },
        y: { ticks: { color: c.muted, font: { size: 11 } }, grid: { color: c.border } }
      }
    };
  }

  function destroyChart(id) {
    if (_charts[id]) { _charts[id].destroy(); delete _charts[id]; }
  }

  // ── Format helpers ───────────────────────────────────────────────────────
  function fmtMoney(n) {
    if (!n) return '$0';
    if (n >= 1_000_000) return '$' + (n / 1_000_000).toFixed(2) + 'M';
    if (n >= 1_000)     return '$' + (n / 1_000).toFixed(1) + 'K';
    return '$' + Math.round(n).toLocaleString();
  }

  function fmtNum(n) {
    if (!n) return '0';
    return Number(n).toLocaleString();
  }

  // ── Animated counter ─────────────────────────────────────────────────────
  function animateCounter(el, target, prefix = '', suffix = '', duration = 800) {
    if (!el) return;
    const start = 0;
    const startTime = performance.now();
    function step(now) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
      const current = Math.round(start + (target - start) * eased);
      el.textContent = prefix + current.toLocaleString() + suffix;
      if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function animateMoney(el, target, duration = 900) {
    if (!el) return;
    const startTime = performance.now();
    function step(now) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = target * eased;
      el.textContent = fmtMoney(current);
      if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  // ── Load all analytics data ──────────────────────────────────────────────
  async function load(days) {
    if (days !== undefined) _currentDays = days;
    const container = document.getElementById('tabAnalytics');
    if (!container) return;

    // Show loading state
    document.querySelectorAll('.analytics-kpi-value').forEach(el => {
      el.innerHTML = '<span class="spinner-sm"></span>';
    });

    try {
      const [revRes, funnelRes, countyRes, suretyRes, forecastRes, distRes] = await Promise.all([
        fetch(`/api/analytics/revenue?days=${_currentDays}`).then(r => r.json()),
        fetch('/api/analytics/funnel').then(r => r.json()),
        fetch(`/api/analytics/county-performance?days=${_currentDays}`).then(r => r.json()),
        fetch('/api/analytics/surety-breakdown').then(r => r.json()),
        fetch('/api/analytics/forecast').then(r => r.json()),
        fetch('/api/analytics/bond-distribution').then(r => r.json()),
      ]);

      _data = { revRes, funnelRes, countyRes, suretyRes, forecastRes, distRes };

      renderKPIs(revRes, forecastRes);
      renderRevenueChart(revRes);
      renderFunnelChart(funnelRes);
      renderCountyChart(countyRes);
      renderSuretyChart(suretyRes);
      renderDistributionChart(distRes);
      renderCountyTable(countyRes);

    } catch (err) {
      console.error('Analytics load error:', err);
      document.getElementById('analyticsError').textContent = 'Failed to load analytics data. ' + err.message;
      document.getElementById('analyticsError').style.display = 'block';
    }
  }

  // ── KPI Cards ────────────────────────────────────────────────────────────
  function renderKPIs(rev, forecast) {
    if (!rev.success) return;
    const k = rev.kpis;

    const setKpi = (id, val, isAnimated = true) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (isAnimated) {
        animateMoney(el, val);
      } else {
        el.textContent = val;
      }
    };

    setKpi('anlKpiCollected', k.total_collected);
    setKpi('anlKpiCollected30', k.collected_30d);
    setKpi('anlKpiCollected7', k.collected_7d);
    setKpi('anlKpiLiability', k.total_liability);
    setKpi('anlKpiAvgPremium', k.avg_premium);

    const convEl = document.getElementById('anlKpiConversion');
    if (convEl) animateCounter(convEl, k.conversion_rate, '', '%');

    const bondsEl = document.getElementById('anlKpiBonds');
    if (bondsEl) animateCounter(bondsEl, k.total_bonds);

    // Forecast
    if (forecast.success) {
      const fEl = document.getElementById('anlKpiForecast');
      if (fEl) animateMoney(fEl, forecast.forecast);
      const mtdEl = document.getElementById('anlKpiMTD');
      if (mtdEl) animateMoney(mtdEl, forecast.mtd);
      const pctEl = document.getElementById('anlForecastPct');
      if (pctEl) pctEl.textContent = `${forecast.pct_complete}% of month elapsed`;
    }
  }

  // ── Revenue Over Time Chart ──────────────────────────────────────────────
  function renderRevenueChart(rev) {
    if (!rev.success || !rev.time_series) return;
    destroyChart('revenue');
    const ctx = document.getElementById('revenueChart');
    if (!ctx) return;
    const c = getColors();
    const labels = rev.time_series.map(d => {
      const dt = new Date(d.date);
      return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    });
    const amounts = rev.time_series.map(d => d.amount);

    _charts.revenue = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'Revenue',
          data: amounts,
          borderColor: c.accent,
          backgroundColor: c.accent + '22',
          fill: true,
          tension: 0.4,
          pointRadius: 3,
          pointHoverRadius: 6,
          borderWidth: 2,
        }]
      },
      options: {
        ...chartDefaults(),
        plugins: {
          ...chartDefaults().plugins,
          tooltip: {
            ...chartDefaults().plugins.tooltip,
            callbacks: {
              label: ctx => ' ' + fmtMoney(ctx.parsed.y)
            }
          }
        },
        scales: {
          x: { ...chartDefaults().scales.x },
          y: {
            ...chartDefaults().scales.y,
            ticks: {
              ...chartDefaults().scales.y.ticks,
              callback: v => fmtMoney(v)
            }
          }
        }
      }
    });
  }

  // ── Funnel Chart ─────────────────────────────────────────────────────────
  function renderFunnelChart(funnel) {
    if (!funnel.success || !funnel.stages) return;
    destroyChart('funnel');
    const ctx = document.getElementById('funnelChart');
    if (!ctx) return;
    const c = getColors();
    const stages = funnel.stages;

    _charts.funnel = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: stages.map(s => s.stage),
        datasets: [{
          label: 'Count',
          data: stages.map(s => s.count),
          backgroundColor: stages.map(s => s.color + 'cc'),
          borderColor: stages.map(s => s.color),
          borderWidth: 1,
          borderRadius: 6,
        }]
      },
      options: {
        ...chartDefaults(),
        indexAxis: 'y',
        plugins: {
          ...chartDefaults().plugins,
          legend: { display: false }
        },
        scales: {
          x: { ...chartDefaults().scales.x, ticks: { ...chartDefaults().scales.x.ticks, callback: v => fmtNum(v) } },
          y: { ...chartDefaults().scales.y }
        }
      }
    });
  }

  // ── County Revenue Chart ─────────────────────────────────────────────────
  function renderCountyChart(county) {
    if (!county.success || !county.counties) return;
    destroyChart('county');
    const ctx = document.getElementById('countyRevenueChart');
    if (!ctx) return;
    const c = getColors();
    const top = county.counties.slice(0, 12);

    _charts.county = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: top.map(c => c.county),
        datasets: [{
          label: 'Premium Collected',
          data: top.map(c => c.total_premium),
          backgroundColor: c.accent + 'bb',
          borderColor: c.accent,
          borderWidth: 1,
          borderRadius: 4,
        }, {
          label: 'Lead Volume',
          data: top.map(c => c.leads),
          backgroundColor: c.accent2 + 'bb',
          borderColor: c.accent2,
          borderWidth: 1,
          borderRadius: 4,
          yAxisID: 'y2',
        }]
      },
      options: {
        ...chartDefaults(),
        indexAxis: 'y',
        plugins: { ...chartDefaults().plugins },
        scales: {
          x: { ...chartDefaults().scales.x, ticks: { ...chartDefaults().scales.x.ticks, callback: v => fmtMoney(v) } },
          y: { ...chartDefaults().scales.y },
          y2: {
            type: 'linear',
            position: 'right',
            ticks: { color: c.muted, font: { size: 11 } },
            grid: { drawOnChartArea: false },
          }
        }
      }
    });
  }

  // ── Surety Donut Chart ───────────────────────────────────────────────────
  function renderSuretyChart(surety) {
    if (!surety.success || !surety.sureties) return;
    destroyChart('surety');
    const ctx = document.getElementById('suretyChart');
    if (!ctx) return;
    const c = getColors();
    const colors = ['#10b981', '#6366f1', '#f59e0b', '#ec4899', '#3b82f6'];

    _charts.surety = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: surety.sureties.map(s => s.surety),
        datasets: [{
          data: surety.sureties.map(s => s.count),
          backgroundColor: surety.sureties.map((_, i) => colors[i % colors.length] + 'cc'),
          borderColor: surety.sureties.map((_, i) => colors[i % colors.length]),
          borderWidth: 2,
        }]
      },
      options: {
        ...chartDefaults(),
        cutout: '65%',
        plugins: {
          ...chartDefaults().plugins,
          legend: { position: 'bottom', labels: { color: c.text, padding: 16 } },
          tooltip: {
            ...chartDefaults().plugins.tooltip,
            callbacks: {
              label: ctx => ` ${ctx.label}: ${ctx.parsed} bonds`
            }
          }
        },
        scales: {}
      }
    });

    // Render surety stats below chart
    const statsEl = document.getElementById('suretyStats');
    if (statsEl) {
      statsEl.innerHTML = surety.sureties.map((s, i) => `
        <div class="surety-stat-row">
          <span class="surety-dot" style="background:${colors[i % colors.length]}"></span>
          <span class="surety-name">${s.surety}</span>
          <span class="surety-val">${s.count} bonds · ${fmtMoney(s.total_premium)}</span>
        </div>
      `).join('');
    }
  }

  // ── Bond Distribution Histogram ──────────────────────────────────────────
  function renderDistributionChart(dist) {
    if (!dist.success || !dist.buckets) return;
    destroyChart('distribution');
    const ctx = document.getElementById('distributionChart');
    if (!ctx) return;
    const c = getColors();

    _charts.distribution = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: dist.buckets.map(b => b.label),
        datasets: [{
          label: 'Bonds',
          data: dist.buckets.map(b => b.count),
          backgroundColor: c.accent2 + 'bb',
          borderColor: c.accent2,
          borderWidth: 1,
          borderRadius: 6,
        }]
      },
      options: {
        ...chartDefaults(),
        plugins: { ...chartDefaults().plugins, legend: { display: false } },
        scales: {
          x: { ...chartDefaults().scales.x },
          y: { ...chartDefaults().scales.y, ticks: { ...chartDefaults().scales.y.ticks, stepSize: 1 } }
        }
      }
    });
  }

  // ── County Performance Table ─────────────────────────────────────────────
  function renderCountyTable(county) {
    if (!county.success || !county.counties) return;
    const tbody = document.getElementById('countyPerfBody');
    if (!tbody) return;
    tbody.innerHTML = county.counties.map(c => `
      <tr>
        <td><strong>${c.county}</strong></td>
        <td>${fmtNum(c.leads)}</td>
        <td>${fmtNum(c.bonds)}</td>
        <td>${fmtMoney(c.total_premium)}</td>
        <td>${fmtMoney(c.avg_bond)}</td>
        <td>
          <div class="conv-bar-wrap">
            <div class="conv-bar" style="width:${Math.min(c.conversion, 100)}%"></div>
            <span>${c.conversion}%</span>
          </div>
        </td>
      </tr>
    `).join('');
  }

  // ── Date range toggle ────────────────────────────────────────────────────
  function setDays(days, btn) {
    _currentDays = days;
    document.querySelectorAll('.anl-range-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    load(days);
  }

  // ── Public API ───────────────────────────────────────────────────────────
  window.SLAnalytics = { load, setDays };

})();
