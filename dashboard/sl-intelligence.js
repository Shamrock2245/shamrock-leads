/* ShamrockLeads — AI Intelligence Module
   Namespace: window.SLIntelligence
   Endpoints: /api/intelligence/*, /api/ml/*
   Requires: Chart.js 4.x (loaded via CDN in index.html)
*/
(function () {
  'use strict';

  let _charts = {};
  let _forecastHorizon = 14;
  let _historyDays = 90;
  let _modelStatus = null;

  // ── Theme-aware colors ──────────────────────────────────────────────────
  function C() {
    const s = getComputedStyle(document.documentElement);
    return {
      accent:  s.getPropertyValue('--accent').trim()  || '#10b981',
      accent2: s.getPropertyValue('--accent2').trim() || '#6366f1',
      panel:   s.getPropertyValue('--panel').trim()    || '#1e293b',
      border:  s.getPropertyValue('--border').trim()   || '#334155',
      text:    s.getPropertyValue('--text').trim()      || '#f1f5f9',
      muted:   s.getPropertyValue('--muted').trim()     || '#94a3b8',
      danger:  '#ef4444',
      warning: '#f59e0b',
      info:    '#3b82f6',
      purple:  '#8b5cf6',
    };
  }

  function chartBase() {
    const c = C();
    return {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: c.text, font: { family: 'Inter', size: 11 } } },
        tooltip: { backgroundColor: c.panel, borderColor: c.border, borderWidth: 1,
                   titleColor: c.text, bodyColor: c.muted, padding: 10 }
      },
      scales: {
        x: { ticks: { color: c.muted, font: { size: 10 } }, grid: { color: c.border + '44' } },
        y: { ticks: { color: c.muted, font: { size: 10 } }, grid: { color: c.border + '44' } }
      }
    };
  }

  function kill(id) { if (_charts[id]) { _charts[id].destroy(); delete _charts[id]; } }

  // ── Formatters ──────────────────────────────────────────────────────────
  function $(id) { return document.getElementById(id); }
  function fmtK(n) {
    if (!n && n !== 0) return '—';
    if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e3) return '$' + (n / 1e3).toFixed(1) + 'K';
    return '$' + Math.round(n).toLocaleString();
  }
  function pct(n) { return (n * 100).toFixed(1) + '%'; }

  function animVal(el, target, prefix, suffix, dur) {
    if (!el) return;
    prefix = prefix || ''; suffix = suffix || ''; dur = dur || 800;
    const t0 = performance.now();
    (function step(now) {
      const p = Math.min((now - t0) / dur, 1);
      const e = 1 - Math.pow(1 - p, 3);
      el.textContent = prefix + Math.round(target * e).toLocaleString() + suffix;
      if (p < 1) requestAnimationFrame(step);
    })(t0);
  }

  function animMoney(el, target) {
    if (!el) return;
    const t0 = performance.now();
    (function step(now) {
      const p = Math.min((now - t0) / 900, 1);
      el.textContent = fmtK(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(step);
    })(t0);
  }

  function spinner() { return '<span class="spinner-sm"></span>'; }

  // ── Safe fetch ──────────────────────────────────────────────────────────
  async function api(url) {
    try {
      const r = await fetch(url);
      if (!r.ok) return { success: false, error: `HTTP ${r.status}` };
      const ct = r.headers.get('content-type') || '';
      if (!ct.includes('json')) return { success: false, error: 'Non-JSON' };
      return await r.json();
    } catch (e) { return { success: false, error: e.message }; }
  }
  async function apiPost(url, body) {
    try {
      const r = await fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!r.ok) return { success: false, error: `HTTP ${r.status}` };
      return await r.json();
    } catch (e) { return { success: false, error: e.message }; }
  }

  // ── Main loader ─────────────────────────────────────────────────────────
  async function load() {
    // Show spinners
    ['intKpiForecast','intKpiP50','intKpiP90','intKpiTrend',
     'intKpiModels','intKpiAccuracy','intKpiCounties','intKpiHotRate'].forEach(id => {
      const el = $(id); if (el) el.innerHTML = spinner();
    });

    const [forecast, heatmap, temporal, mlStatus, riskTrend] = await Promise.all([
      api(`/api/intelligence/forecast?history=${_historyDays}&horizon=${_forecastHorizon}`),
      api('/api/intelligence/heatmap/counties'),
      api('/api/intelligence/heatmap/temporal'),
      api('/api/ml/model-status'),
      api('/api/intelligence/risk-trend'),
    ]);

    _modelStatus = mlStatus;

    renderForecastKPIs(forecast);
    renderMLKPIs(mlStatus, heatmap);
    renderForecastChart(forecast);
    renderConfidenceBands(forecast);
    renderCountyHeatmap(heatmap);
    renderTemporalHeatmap(temporal);
    renderRiskTrend(riskTrend);
    renderModelCards(mlStatus);
  }

  // ── Forecast KPIs ───────────────────────────────────────────────────────
  function renderForecastKPIs(d) {
    if (!d || !d.success) {
      ['intKpiForecast','intKpiP50','intKpiP90','intKpiTrend'].forEach(id => {
        const el = $(id); if (el) el.textContent = '—';
      });
      return;
    }
    const f = d.forecast;
    if (f.point_forecast) animMoney($('intKpiForecast'), f.point_forecast.reduce((a, b) => a + b, 0));
    if (f.confidence_intervals) {
      const ci = f.confidence_intervals;
      const p50sum = ci.filter(c => c.level === 'p50').reduce((a, c) => a + (c.values ? c.values.reduce((x, y) => x + y, 0) : 0), 0);
      const p90sum = ci.filter(c => c.level === 'p90').reduce((a, c) => a + (c.values ? c.values.reduce((x, y) => x + y, 0) : 0), 0);
      if (p50sum) animMoney($('intKpiP50'), p50sum);
      if (p90sum) animMoney($('intKpiP90'), p90sum);
    }
    const trendEl = $('intKpiTrend');
    if (trendEl && f.trend_direction) {
      const arrow = f.trend_direction === 'up' ? '↗' : f.trend_direction === 'down' ? '↘' : '→';
      const color = f.trend_direction === 'up' ? '#10b981' : f.trend_direction === 'down' ? '#ef4444' : '#94a3b8';
      trendEl.innerHTML = `<span style="color:${color}">${arrow} ${f.trend_direction}</span>`;
    }
  }

  // ── ML KPIs ─────────────────────────────────────────────────────────────
  function renderMLKPIs(ml, hm) {
    const modelsEl = $('intKpiModels');
    if (modelsEl) {
      if (ml && ml.success) {
        const models = ml.models || {};
        const count = Object.keys(models).length;
        modelsEl.textContent = count;
        modelsEl.style.color = count > 0 ? '#10b981' : '#f59e0b';
      } else { modelsEl.textContent = '0'; }
    }
    const accEl = $('intKpiAccuracy');
    if (accEl && ml && ml.success) {
      const models = ml.models || {};
      const accs = Object.values(models).map(m => m.accuracy || m.cv_score || 0).filter(a => a > 0);
      accEl.textContent = accs.length > 0 ? (accs.reduce((a, b) => a + b, 0) / accs.length * 100).toFixed(1) + '%' : 'N/A';
    }
    const countiesEl = $('intKpiCounties');
    if (countiesEl && hm && hm.success) {
      animVal(countiesEl, (hm.counties || []).length, '', '');
    }
    const hotEl = $('intKpiHotRate');
    if (hotEl && hm && hm.success) {
      const counties = hm.counties || [];
      const hot = counties.filter(c => (c.risk_score || c.composite_score || 0) >= 70).length;
      hotEl.textContent = hot;
      hotEl.style.color = hot > 5 ? '#ef4444' : hot > 2 ? '#f59e0b' : '#10b981';
    }
  }

  // ── Forecast Chart (line + confidence bands) ────────────────────────────
  function renderForecastChart(d) {
    kill('forecast');
    const ctx = $('intForecastChart');
    if (!ctx || !d || !d.success) return;
    const c = C();
    const f = d.forecast;
    if (!f || !f.point_forecast) return;

    const labels = f.dates || f.point_forecast.map((_, i) => `Day ${i + 1}`);
    const datasets = [{
      label: 'Forecast',
      data: f.point_forecast,
      borderColor: '#10b981',
      backgroundColor: '#10b98133',
      fill: false,
      tension: 0.3,
      borderWidth: 2.5,
      pointRadius: 2,
      pointHoverRadius: 5,
    }];

    // Add historical if available
    if (f.historical) {
      datasets.unshift({
        label: 'Historical',
        data: f.historical,
        borderColor: c.muted,
        backgroundColor: c.muted + '22',
        fill: true,
        tension: 0.3,
        borderWidth: 1.5,
        pointRadius: 0,
        borderDash: [4, 3],
      });
    }

    _charts.forecast = new Chart(ctx, {
      type: 'line',
      data: { labels, datasets },
      options: {
        ...chartBase(),
        plugins: {
          ...chartBase().plugins,
          legend: { ...chartBase().plugins.legend, position: 'top' },
          tooltip: {
            ...chartBase().plugins.tooltip,
            callbacks: { label: ctx => ' ' + fmtK(ctx.parsed.y) }
          }
        },
        scales: {
          x: { ...chartBase().scales.x },
          y: { ...chartBase().scales.y, ticks: { ...chartBase().scales.y.ticks, callback: v => fmtK(v) } }
        }
      }
    });
  }

  // ── Confidence Bands (bar range) ────────────────────────────────────────
  function renderConfidenceBands(d) {
    kill('confidence');
    const ctx = $('intConfidenceChart');
    if (!ctx || !d || !d.success) return;
    const f = d.forecast;
    if (!f || !f.confidence_intervals) return;
    const c = C();

    const ci = f.confidence_intervals;
    const p10 = ci.find(x => x.level === 'p10');
    const p50 = ci.find(x => x.level === 'p50');
    const p90 = ci.find(x => x.level === 'p90');
    if (!p50 || !p50.values) return;

    const labels = p50.values.map((_, i) => `Day ${i + 1}`);
    const datasets = [];
    if (p10 && p10.values) {
      datasets.push({
        label: 'P10 (Pessimistic)',
        data: p10.values, borderColor: '#ef444488', backgroundColor: '#ef444411',
        fill: false, tension: 0.3, borderWidth: 1, pointRadius: 0, borderDash: [3, 3],
      });
    }
    datasets.push({
      label: 'P50 (Median)',
      data: p50.values, borderColor: '#3b82f6', backgroundColor: '#3b82f622',
      fill: false, tension: 0.3, borderWidth: 2, pointRadius: 1,
    });
    if (p90 && p90.values) {
      datasets.push({
        label: 'P90 (Optimistic)',
        data: p90.values, borderColor: '#10b98188', backgroundColor: '#10b98111',
        fill: '-1', tension: 0.3, borderWidth: 1, pointRadius: 0, borderDash: [3, 3],
      });
    }

    _charts.confidence = new Chart(ctx, {
      type: 'line',
      data: { labels, datasets },
      options: {
        ...chartBase(),
        plugins: {
          ...chartBase().plugins,
          legend: { position: 'bottom', labels: { color: c.text, usePointStyle: true, padding: 12 } },
        },
        scales: {
          x: { ...chartBase().scales.x },
          y: { ...chartBase().scales.y, ticks: { ...chartBase().scales.y.ticks, callback: v => fmtK(v) } }
        }
      }
    });
  }

  // ── County Risk Heatmap (horizontal bar) ────────────────────────────────
  function renderCountyHeatmap(d) {
    kill('heatmap');
    const ctx = $('intHeatmapChart');
    if (!ctx || !d || !d.success) return;
    const c = C();
    const counties = (d.counties || [])
      .sort((a, b) => (b.risk_score || b.composite_score || 0) - (a.risk_score || a.composite_score || 0))
      .slice(0, 20);
    if (!counties.length) return;

    const scores = counties.map(x => x.risk_score || x.composite_score || 0);
    const colors = scores.map(s =>
      s >= 75 ? '#ef4444cc' : s >= 50 ? '#f59e0bcc' : s >= 25 ? '#3b82f6cc' : '#10b981cc'
    );

    _charts.heatmap = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: counties.map(x => x.county || x._id || 'Unknown'),
        datasets: [{
          label: 'Risk Score',
          data: scores,
          backgroundColor: colors,
          borderColor: colors.map(c => c.replace('cc', '')),
          borderWidth: 1, borderRadius: 4,
        }]
      },
      options: {
        ...chartBase(),
        indexAxis: 'y',
        plugins: { ...chartBase().plugins, legend: { display: false } },
        scales: {
          x: { ...chartBase().scales.x, max: 100,
            ticks: { ...chartBase().scales.x.ticks, callback: v => v } },
          y: { ...chartBase().scales.y,
            ticks: { ...chartBase().scales.y.ticks, font: { size: 11, weight: '600' } } }
        }
      }
    });
  }

  // ── Temporal Heatmap (24×7 grid via canvas) ─────────────────────────────
  function renderTemporalHeatmap(d) {
    const container = $('intTemporalGrid');
    if (!container) return;
    if (!d || !d.success || !d.grid) {
      container.innerHTML = '<div style="padding:24px;text-align:center;color:var(--muted)">No temporal data available</div>';
      return;
    }

    const grid = d.grid; // 24 rows × 7 cols
    const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const maxVal = Math.max(...grid.flat(), 1);

    let html = '<div class="int-temporal-wrap">';
    html += '<div class="int-temporal-header"><div class="int-temporal-corner"></div>';
    days.forEach(d => { html += `<div class="int-temporal-day">${d}</div>`; });
    html += '</div>';

    for (let h = 0; h < 24; h++) {
      html += '<div class="int-temporal-row">';
      html += `<div class="int-temporal-hour">${h.toString().padStart(2, '0')}:00</div>`;
      for (let dow = 0; dow < 7; dow++) {
        const val = grid[h] ? (grid[h][dow] || 0) : 0;
        const intensity = val / maxVal;
        const hue = 120 - (intensity * 120); // green→red
        const alpha = 0.15 + intensity * 0.75;
        html += `<div class="int-temporal-cell" style="background:hsla(${hue},80%,50%,${alpha})" title="${days[dow]} ${h}:00 — ${val} arrests">${val || ''}</div>`;
      }
      html += '</div>';
    }
    html += '</div>';
    container.innerHTML = html;
  }

  // ── Risk Trend (sparkline) ──────────────────────────────────────────────
  function renderRiskTrend(d) {
    kill('riskTrend');
    const ctx = $('intRiskTrendChart');
    if (!ctx || !d || !d.success) return;
    const c = C();
    const trend = d.trend || d.daily_risk || [];
    if (!trend.length) return;

    _charts.riskTrend = new Chart(ctx, {
      type: 'line',
      data: {
        labels: trend.map(t => {
          const dt = new Date(t.date || t._id);
          return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        }),
        datasets: [{
          label: 'Avg Risk',
          data: trend.map(t => t.avg_score || t.avg_risk || 0),
          borderColor: '#f59e0b',
          backgroundColor: '#f59e0b22',
          fill: true, tension: 0.4, borderWidth: 2, pointRadius: 0,
        }, {
          label: '7d Moving Avg',
          data: trend.map(t => t.moving_avg || t.ma_7d || 0),
          borderColor: '#8b5cf6',
          fill: false, tension: 0.4, borderWidth: 2, pointRadius: 0, borderDash: [5, 3],
        }]
      },
      options: {
        ...chartBase(),
        plugins: { ...chartBase().plugins,
          legend: { position: 'bottom', labels: { color: c.text, usePointStyle: true, padding: 12 } }
        },
        scales: {
          x: { ...chartBase().scales.x, ticks: { ...chartBase().scales.x.ticks, maxTicksLimit: 10 } },
          y: { ...chartBase().scales.y, min: 0, max: 100 }
        }
      }
    });
  }

  // ── ML Model Status Cards ───────────────────────────────────────────────
  function renderModelCards(d) {
    const container = $('intModelCards');
    if (!container) return;
    if (!d || !d.success || !d.models || !Object.keys(d.models).length) {
      container.innerHTML = `
        <div class="int-model-empty">
          <div style="font-size:2rem;margin-bottom:8px">🧠</div>
          <div style="font-weight:700;margin-bottom:4px">No ML Models Trained</div>
          <div style="color:var(--muted);font-size:12px;margin-bottom:12px">Train your first model to unlock predictive lead scoring</div>
          <button class="int-train-btn" onclick="SLIntelligence.trainModel('lead_quality')">
            ⚡ Train Lead Quality Model
          </button>
        </div>`;
      return;
    }

    const models = d.models;
    container.innerHTML = Object.entries(models).map(([key, m]) => {
      const acc = m.accuracy || m.cv_score || 0;
      const accPct = (acc * 100).toFixed(1);
      const accColor = acc >= 0.8 ? '#10b981' : acc >= 0.6 ? '#f59e0b' : '#ef4444';
      const trained = m.trained_at ? new Date(m.trained_at).toLocaleDateString() : 'Unknown';
      const samples = m.training_samples || m.n_samples || '?';
      const algo = m.algorithm || 'RandomForest';

      return `
        <div class="int-model-card">
          <div class="int-model-header">
            <span class="int-model-name">${key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</span>
            <span class="int-model-algo">${algo}</span>
          </div>
          <div class="int-model-acc">
            <div class="int-acc-ring" style="--acc-pct:${accPct};--acc-color:${accColor}">
              <span>${accPct}%</span>
            </div>
            <div class="int-acc-label">Accuracy</div>
          </div>
          <div class="int-model-meta">
            <div><span class="int-meta-label">Trained</span><span>${trained}</span></div>
            <div><span class="int-meta-label">Samples</span><span>${Number(samples).toLocaleString()}</span></div>
          </div>
          <div class="int-model-actions">
            <button class="int-action-btn" onclick="SLIntelligence.trainModel('${key}')">↻ Retrain</button>
            <button class="int-action-btn int-action-secondary" onclick="SLIntelligence.showFeatures('${key}')">📊 Features</button>
          </div>
        </div>`;
    }).join('');
  }

  // ── Train Model ─────────────────────────────────────────────────────────
  async function trainModel(target) {
    const btn = event ? event.target : null;
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Training...'; }

    const result = await apiPost('/api/ml/train', {
      target: target || 'lead_quality',
      algorithm: 'random_forest',
      limit: 5000
    });

    if (btn) { btn.disabled = false; btn.textContent = '↻ Retrain'; }

    if (result.success) {
      showToast('✅ Model trained successfully', 'success');
      load(); // Refresh all data
    } else {
      showToast('❌ Training failed: ' + (result.error || 'Unknown error'), 'error');
    }
  }

  // ── Feature Importance Modal ────────────────────────────────────────────
  async function showFeatures(target) {
    const d = await api(`/api/ml/feature-importance?target=${target}`);
    if (!d || !d.success) { showToast('Could not load feature data', 'error'); return; }

    const features = d.features || d.feature_importance || [];
    const modal = $('intFeatureModal');
    if (!modal) return;

    const body = $('intFeatureBody');
    if (body) {
      body.innerHTML = features.slice(0, 15).map((f, i) => {
        const barWidth = Math.max(f.importance * 100 / (features[0]?.importance || 1), 5);
        return `
          <div class="int-feat-row">
            <span class="int-feat-rank">#${i + 1}</span>
            <span class="int-feat-name">${f.feature || f.name}</span>
            <div class="int-feat-bar-wrap">
              <div class="int-feat-bar" style="width:${barWidth}%"></div>
            </div>
            <span class="int-feat-val">${(f.importance * 100).toFixed(1)}%</span>
          </div>`;
      }).join('');
    }
    modal.classList.add('show');
  }

  // ── Forecast range toggle ───────────────────────────────────────────────
  function setHorizon(days, btn) {
    _forecastHorizon = days;
    document.querySelectorAll('.int-range-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    load();
  }

  // ── Toast helper ────────────────────────────────────────────────────────
  function showToast(msg, type) {
    if (window.SLOverhaul && SLOverhaul.toast) { SLOverhaul.toast(msg, type); return; }
    console.log(`[Intelligence] ${type}: ${msg}`);
  }

  // ── Public API ──────────────────────────────────────────────────────────
  window.SLIntelligence = { load, trainModel, showFeatures, setHorizon };
})();
