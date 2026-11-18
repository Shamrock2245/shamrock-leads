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

    const [forecast, heatmap, temporal, mlStatus, riskTrend, courtPred, forfeit] = await Promise.all([
      api(`/api/intelligence/forecast?history=${_historyDays}&horizon=${_forecastHorizon}`),
      api('/api/intelligence/heatmap/counties'),
      api('/api/intelligence/heatmap/temporal'),
      api('/api/ml/model-status'),
      api('/api/intelligence/risk-trend'),
      api('/api/intelligence/court-prediction?limit=15'),
      api('/api/intelligence/forfeiture-risk?limit=20'),
    ]);

    _modelStatus = mlStatus;

    // Fault-tolerant rendering: each panel renders independently
    const renders = [
      ['ForecastKPIs',       () => renderForecastKPIs(forecast)],
      ['MLKPIs',             () => renderMLKPIs(mlStatus, heatmap)],
      ['ForecastChart',      () => renderForecastChart(forecast)],
      ['ConfidenceBands',    () => renderConfidenceBands(forecast)],
      ['CountyHeatmap',      () => renderCountyHeatmap(heatmap)],
      ['TemporalHeatmap',    () => renderTemporalHeatmap(temporal)],
      ['RiskTrend',          () => renderRiskTrend(riskTrend)],
      ['ModelCards',         () => renderModelCards(mlStatus)],
      ['CourtPredictions',   () => renderCourtPredictions(courtPred)],
      ['ForfeitureRisk',     () => renderForfeitureRisk(forfeit)],
      ['CourtIntel',         () => loadCourtIntel()],
      ['DocketMonitor',      () => { if (window.SLDocketMonitor) SLDocketMonitor.init(); }],
    ];
    for (const [name, fn] of renders) {
      try { fn(); } catch (e) { console.error(`[Intelligence] ${name} render failed:`, e); }
    }
  }

  // ── Forecast KPIs ───────────────────────────────────────────────────────
  function renderForecastKPIs(d) {
    if (!d || !d.success) {
      ['intKpiForecast','intKpiP50','intKpiP90','intKpiTrend'].forEach(id => {
        const el = $(id); if (el) el.textContent = '—';
      });
      return;
    }
    // Map from actual API shape: {exponential_smoothing, monte_carlo, summary, historical}
    const s = d.summary || {};
    const mc = d.monte_carlo || {};
    const es = d.exponential_smoothing || {};

    // 30d blended forecast
    if (s.forecast_next_30d != null) {
      animMoney($('intKpiForecast'), s.forecast_next_30d);
    } else if (es.forecast) {
      animMoney($('intKpiForecast'), es.forecast.reduce((a, b) => a + b, 0));
    }

    // P10 (pessimistic) and P90 (optimistic) from Monte Carlo
    if (mc.p10_total != null) animMoney($('intKpiP50'), mc.p10_total);
    if (mc.p90_total != null) animMoney($('intKpiP90'), mc.p90_total);

    // Confidence band fallback from summary
    if (s.confidence_band) {
      if (!mc.p10_total && s.confidence_band.low) animMoney($('intKpiP50'), s.confidence_band.low);
      if (!mc.p90_total && s.confidence_band.high) animMoney($('intKpiP90'), s.confidence_band.high);
    }

    // Trend from exponential smoothing or summary
    const trend = s.trend || es.trend;
    const trendEl = $('intKpiTrend');
    if (trendEl && trend) {
      const arrow = trend === 'up' ? '↗' : trend === 'down' ? '↘' : '→';
      const color = trend === 'up' ? '#10b981' : trend === 'down' ? '#ef4444' : '#94a3b8';
      trendEl.innerHTML = `<span style="color:${color}">${arrow} ${trend}</span>`;
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
      const accs = Object.values(models).map(m => (m.metrics || {}).accuracy || m.accuracy || m.cv_score || 0).filter(a => a > 0);
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
    const es = d.exponential_smoothing || {};
    const forecastArr = es.forecast || [];
    if (!forecastArr.length) return;

    const datasets = [];

    // Historical daily revenue (last 30 days from API)
    const hist = d.historical || [];
    if (hist.length) {
      datasets.push({
        label: 'Historical',
        data: hist.map(h => h.amount || 0),
        borderColor: c.muted,
        backgroundColor: c.muted + '22',
        fill: true,
        tension: 0.3,
        borderWidth: 1.5,
        pointRadius: 0,
        borderDash: [4, 3],
      });
    }

    // Forecast line
    // Offset forecast data after historical points
    const pad = hist.length ? new Array(hist.length).fill(null) : [];
    datasets.push({
      label: 'Forecast',
      data: [...pad, ...forecastArr],
      borderColor: '#10b981',
      backgroundColor: '#10b98133',
      fill: false,
      tension: 0.3,
      borderWidth: 2.5,
      pointRadius: 2,
      pointHoverRadius: 5,
      spanGaps: true,
    });

    // Build labels: historical dates + forecast days
    const histLabels = hist.map(h => h.date ? h.date.slice(5) : '');
    const fcastLabels = forecastArr.map((_, i) => `+${i + 1}d`);
    const labels = [...histLabels, ...fcastLabels];

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

  // ── Confidence Bands (forecast + upper/lower bounds) ────────────────────
  function renderConfidenceBands(d) {
    kill('confidence');
    const ctx = $('intConfidenceChart');
    if (!ctx || !d || !d.success) return;
    const es = d.exponential_smoothing || {};
    const forecastArr = es.forecast || [];
    if (!forecastArr.length) return;
    const c = C();

    const upper = es.upper_bound || [];
    const lower = es.lower_bound || [];
    const labels = forecastArr.map((_, i) => `Day ${i + 1}`);

    const datasets = [];
    if (lower.length) {
      datasets.push({
        label: 'Lower Bound (P10)',
        data: lower, borderColor: '#ef444488', backgroundColor: '#ef444411',
        fill: false, tension: 0.3, borderWidth: 1, pointRadius: 0, borderDash: [3, 3],
      });
    }
    datasets.push({
      label: 'Forecast (Median)',
      data: forecastArr, borderColor: '#3b82f6', backgroundColor: '#3b82f622',
      fill: false, tension: 0.3, borderWidth: 2, pointRadius: 1,
    });
    if (upper.length) {
      datasets.push({
        label: 'Upper Bound (P90)',
        data: upper, borderColor: '#10b98188', backgroundColor: '#10b98111',
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
          <div style="color:var(--muted);font-size:12px;margin-bottom:16px">Train your first model to unlock predictive scoring</div>
          <div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center">
            <button class="int-train-btn" onclick="SLIntelligence.trainModel('lead_quality')">
              ⚡ Train Lead Quality
            </button>
            <button class="int-train-btn int-train-fta" onclick="SLIntelligence.bootstrapFTA()">
              🧬 Bootstrap FTA Risk (COMPAS)
            </button>
          </div>
        </div>`;
      return;
    }

    const models = d.models;
    let html = '';

    // Add bootstrap FTA button if no FTA model exists yet
    const hasFTA = Object.keys(models).some(k => k.includes('fta_risk'));
    if (!hasFTA) {
      html += `<div class="int-fta-bootstrap-banner">
        <div style="display:flex;align-items:center;gap:10px">
          <span style="font-size:1.3rem">🧬</span>
          <div>
            <div style="font-weight:700;font-size:13px">FTA Risk Model Available</div>
            <div style="font-size:11px;color:var(--muted)">Bootstrap from ProPublica COMPAS dataset (~6K labeled pretrial outcomes)</div>
          </div>
        </div>
        <button class="int-train-btn int-train-fta" onclick="SLIntelligence.bootstrapFTA()" style="white-space:nowrap">
          🧬 Bootstrap FTA
        </button>
      </div>`;
    }

    html += Object.entries(models).map(([key, m]) => {
      const metrics = m.metrics || {};
      const acc = metrics.accuracy || m.accuracy || m.cv_score || 0;
      const accPct = (acc * 100).toFixed(1);
      const accColor = acc >= 0.8 ? '#10b981' : acc >= 0.6 ? '#f59e0b' : '#ef4444';
      const trained = m.trained_at ? new Date(m.trained_at).toLocaleDateString() : 'Unknown';
      const samples = m.training_samples || m.n_samples || '?';
      const algo = (m.algorithm || 'random_forest').replace(/_/g, ' ');
      const target = m.target || key.split('_').slice(0, -2).join('_') || key;
      const posRate = m.positive_rate != null ? m.positive_rate.toFixed(1) + '%' : '—';
      const isFTA = key.includes('fta_risk');
      const cardAccent = isFTA ? '#f59e0b' : '#10b981';

      // Confusion matrix mini-display
      let cmHtml = '';
      if (m.confusion_matrix) {
        const cm = m.confusion_matrix;
        const total = cm.tp + cm.tn + cm.fp + cm.fn;
        const precision = cm.tp + cm.fp > 0 ? (cm.tp / (cm.tp + cm.fp) * 100).toFixed(0) : '—';
        const recall = cm.tp + cm.fn > 0 ? (cm.tp / (cm.tp + cm.fn) * 100).toFixed(0) : '—';
        cmHtml = `
          <div class="int-cm-mini">
            <div class="int-cm-grid">
              <div class="int-cm-cell int-cm-tn" title="True Negative">${cm.tn}</div>
              <div class="int-cm-cell int-cm-fp" title="False Positive">${cm.fp}</div>
              <div class="int-cm-cell int-cm-fn" title="False Negative">${cm.fn}</div>
              <div class="int-cm-cell int-cm-tp" title="True Positive">${cm.tp}</div>
            </div>
            <div class="int-cm-labels">
              <span>Prec: ${precision}%</span><span>Recall: ${recall}%</span>
            </div>
          </div>`;
      }

      // Metric pills
      const f1 = metrics.f1_score != null ? (metrics.f1_score * 100).toFixed(1) : '—';
      const auc = metrics.auc_roc != null ? (metrics.auc_roc * 100).toFixed(1) : '—';
      const aucColor = (metrics.auc_roc || 0) >= 0.8 ? '#10b981' : (metrics.auc_roc || 0) >= 0.65 ? '#f59e0b' : '#ef4444';

      return `
        <div class="int-model-card" style="border-left:3px solid ${cardAccent}">
          <div class="int-model-header">
            <div>
              <span class="int-model-name">${isFTA ? '⚠️ FTA Risk' : '📊 Lead Quality'}</span>
              <span class="int-model-algo">${algo}</span>
            </div>
            ${m.training_source === 'compas_bootstrap' ? '<span class="int-badge-compas">COMPAS</span>' : ''}
          </div>

          <div class="int-model-metrics-row">
            <div class="int-metric-pill">
              <div class="int-metric-value" style="color:${accColor}">${accPct}%</div>
              <div class="int-metric-label">Accuracy</div>
            </div>
            <div class="int-metric-pill">
              <div class="int-metric-value" style="color:#8b5cf6">${f1}%</div>
              <div class="int-metric-label">F1 Score</div>
            </div>
            <div class="int-metric-pill">
              <div class="int-metric-value" style="color:${aucColor}">${auc}%</div>
              <div class="int-metric-label">AUC-ROC</div>
            </div>
          </div>

          ${cmHtml}

          <div class="int-model-meta">
            <div><span class="int-meta-label">Trained</span><span>${trained}</span></div>
            <div><span class="int-meta-label">Samples</span><span>${Number(samples).toLocaleString()}</span></div>
            <div><span class="int-meta-label">Positive Rate</span><span>${posRate}</span></div>
          </div>
          <div class="int-model-actions">
            <button class="int-action-btn" onclick="SLIntelligence.trainModel('${target}')" data-target="${target}">↻ Retrain</button>
            <button class="int-action-btn int-action-secondary" onclick="SLIntelligence.showFeatures('${target}')">📊 Features</button>
            ${m.roc_curve ? `<button class="int-action-btn int-action-secondary" onclick="SLIntelligence.showROC('${key}')">📈 ROC</button>` : ''}
          </div>
        </div>`;
    }).join('');

    container.innerHTML = html;
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

  // ── Bootstrap FTA Model via COMPAS ──────────────────────────────────────
  async function bootstrapFTA() {
    const btn = event ? event.target : null;
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-sm"></span> Bootstrapping…'; }

    showToast('🧬 Downloading COMPAS dataset & training FTA model…', 'info');

    const result = await apiPost('/api/ml/bootstrap-fta', {
      algorithm: 'random_forest',
      max_samples: 6000,
      include_internal: true,
    });

    if (btn) { btn.disabled = false; btn.textContent = '🧬 Bootstrap FTA Risk (COMPAS)'; }

    if (result.success) {
      const m = result.metrics || {};
      showToast(
        `✅ FTA model bootstrapped — ${result.training_samples} samples, ` +
        `F1=${(m.f1_score*100||0).toFixed(1)}%, AUC=${(m.auc_roc*100||0).toFixed(1)}%`,
        'success'
      );
      load();
    } else {
      showToast('❌ Bootstrap failed: ' + (result.error || 'Unknown'), 'error');
    }
  }

  // ── Show ROC Curve Modal ────────────────────────────────────────────────
  function showROC(modelKey) {
    if (!_modelStatus || !_modelStatus.models) return;
    const m = _modelStatus.models[modelKey];
    if (!m || !m.roc_curve) { showToast('No ROC data available', 'error'); return; }

    const modal = $('intFeatureModal');
    if (!modal) return;

    const body = $('intFeatureBody');
    if (body) {
      body.innerHTML = `
        <div style="margin-bottom:12px;font-weight:700;font-size:14px">ROC Curve — ${modelKey.replace(/_/g,' ')}</div>
        <div style="height:280px;position:relative">
          <canvas id="intROCCanvas"></canvas>
        </div>
        <div style="margin-top:10px;font-size:12px;color:var(--muted);text-align:center">
          AUC = ${((m.metrics||{}).auc_roc*100||0).toFixed(1)}%
        </div>`;

      // Render ROC chart after DOM update
      setTimeout(() => {
        const ctx = document.getElementById('intROCCanvas');
        if (!ctx) return;
        kill('rocCurve');
        const c = C();
        _charts.rocCurve = new Chart(ctx, {
          type: 'line',
          data: {
            labels: m.roc_curve.fpr.map(v => v.toFixed(2)),
            datasets: [{
              label: 'ROC Curve',
              data: m.roc_curve.tpr,
              borderColor: '#8b5cf6',
              backgroundColor: '#8b5cf622',
              fill: true, tension: 0.3, borderWidth: 2, pointRadius: 0,
            }, {
              label: 'Random (AUC=0.5)',
              data: m.roc_curve.fpr,
              borderColor: c.muted + '66',
              borderDash: [5, 5], borderWidth: 1, pointRadius: 0, fill: false,
            }]
          },
          options: {
            ...chartBase(),
            plugins: { ...chartBase().plugins,
              legend: { display: true, position: 'bottom', labels: { color: c.text, usePointStyle: true, padding: 10 } }
            },
            scales: {
              x: { ...chartBase().scales.x, title: { display: true, text: 'False Positive Rate', color: c.muted } },
              y: { ...chartBase().scales.y, title: { display: true, text: 'True Positive Rate', color: c.muted }, min: 0, max: 1 },
            }
          }
        });
      }, 100);
    }
    modal.classList.add('show');
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

  // ── Court Outcome Predictions Table ──────────────────────────────────────
  function renderCourtPredictions(d) {
    const container = $('intCourtPredictions');
    if (!container) return;
    if (!d || !d.success || !d.predictions || !d.predictions.length) {
      container.innerHTML = '<div style="padding:20px;text-align:center;color:var(--muted)">No court predictions available</div>';
      return;
    }
    const preds = d.predictions.slice(0, 12);
    const riskColor = { critical: '#ef4444', high: '#f59e0b', medium: '#3b82f6', low: '#10b981' };

    let html = '<div class="int-pred-table"><div class="int-pred-header">';
    html += '<span>Defendant</span><span>County</span><span>Bond</span><span>FTA Risk</span><span>Risk Level</span><span>Interventions</span>';
    html += '</div>';

    preds.forEach(p => {
      const ftaPct = (p.fta_probability * 100).toFixed(1);
      const color = riskColor[p.risk_level] || '#94a3b8';
      const interventionCount = (p.interventions || []).length;
      const topIntervention = (p.interventions || ['—'])[0];
      html += `<div class="int-pred-row">
        <span class="int-pred-name">${p.defendant_name || 'Unknown'}</span>
        <span class="int-pred-county">${(p.county || '').replace(/ county/i, '')}</span>
        <span class="int-pred-bond">${fmtK(p.bond_amount)}</span>
        <span class="int-pred-fta" style="color:${color};font-weight:700">${ftaPct}%</span>
        <span><span class="int-risk-badge" style="background:${color}22;color:${color};border:1px solid ${color}44">${p.risk_level}</span></span>
        <span class="int-pred-interv" title="${(p.interventions||[]).join('\\n')}">${topIntervention}${interventionCount > 1 ? ` +${interventionCount-1}` : ''}</span>
      </div>`;
    });
    html += '</div>';
    if (d.avg_fta) {
      const avgColor = d.avg_fta >= 0.25 ? '#ef4444' : d.avg_fta >= 0.15 ? '#f59e0b' : '#10b981';
      html += `<div style="margin-top:10px;font-size:12px;color:var(--muted)">Portfolio Avg FTA: <span style="color:${avgColor};font-weight:700">${(d.avg_fta*100).toFixed(1)}%</span> across ${d.total} leads</div>`;
    }
    container.innerHTML = html;
  }

  // ── Forfeiture Early Warning ────────────────────────────────────────────
  function renderForfeitureRisk(d) {
    const container = $('intForfeitureRisk');
    if (!container) return;
    if (!d || !d.success || !d.results || !d.results.length) {
      container.innerHTML = '<div style="padding:20px;text-align:center;color:var(--muted)">No active bonds to score</div>';
      return;
    }

    // Summary bar
    const crit = d.critical_count || 0;
    const high = d.high_risk_count || 0;
    const exposure = d.total_at_risk_exposure || 0;
    let html = `<div class="int-forfeit-summary">
      <div class="int-forfeit-stat"><span class="int-forfeit-num" style="color:#ef4444">${crit}</span><span class="int-forfeit-label">Critical</span></div>
      <div class="int-forfeit-stat"><span class="int-forfeit-num" style="color:#f59e0b">${high}</span><span class="int-forfeit-label">High Risk</span></div>
      <div class="int-forfeit-stat"><span class="int-forfeit-num" style="color:#3b82f6">${d.bonds_scored || 0}</span><span class="int-forfeit-label">Scored</span></div>
      <div class="int-forfeit-stat"><span class="int-forfeit-num" style="color:#ef4444">${fmtK(exposure)}</span><span class="int-forfeit-label">At-Risk $</span></div>
    </div>`;

    const riskColor = { critical: '#ef4444', high: '#f59e0b', medium: '#3b82f6', low: '#10b981' };
    const topRisks = d.results.filter(r => r.risk_tier !== 'low').slice(0, 10);

    if (topRisks.length) {
      html += '<div class="int-forfeit-list">';
      topRisks.forEach(r => {
        const color = riskColor[r.risk_tier] || '#94a3b8';
        const barWidth = Math.min(r.forfeiture_probability * 100, 100);
        html += `<div class="int-forfeit-item">
          <div class="int-forfeit-top">
            <span class="int-forfeit-name">${r.defendant_name || 'Unknown'}</span>
            <span class="int-risk-badge" style="background:${color}22;color:${color};border:1px solid ${color}44">${r.risk_tier} · ${(r.forfeiture_probability*100).toFixed(0)}%</span>
          </div>
          <div class="int-forfeit-bar-wrap">
            <div class="int-forfeit-bar" style="width:${barWidth}%;background:${color}"></div>
          </div>
          <div class="int-forfeit-meta">
            <span>${fmtK(r.bond_amount)} · ${(r.county||'').replace(/ county/i,'')}</span>
            <span>${r.days_active}d active</span>
          </div>
          ${r.warning_signals && r.warning_signals.length ? `<div class="int-forfeit-signals">${r.warning_signals.slice(0,2).map(s => `<span class="int-signal">⚠ ${s}</span>`).join('')}</div>` : ''}
        </div>`;
      });
      html += '</div>';
    } else {
      html += '<div style="padding:16px;text-align:center;color:#10b981;font-weight:600">✅ All active bonds in healthy range</div>';
    }
    container.innerHTML = html;
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

  // ── CourtListener v4 State & Helpers ────────────────────────────────────
  let _courtCoverage = null;
  let _courtAnalytics = null;
  let _courtApiHealth = null;
  let _courtActiveTab = 'analytics';
  let _courtSelectedState = '';
  let _courtScorecardMode = 'courts';
  let _liveSearchResponse = null;
  let _liveSearchResults = [];
  let _liveSearchForm = {
    q: '', preset: '', type: 'o', state: '', court: '', judge: '', filed_after: '', filed_before: '',
  };
  let _docketLookupResults = null;
  let _lastDossier = null;

  function escHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function _dispColor(disp, cat) {
    if (cat === 'bail_critical') return '#ef4444';
    if (cat === 'favorable') return '#10b981';
    if (cat === 'adverse') return '#6366f1';
    const fallback = { bond_reduced: '#10b981', fta: '#ef4444', bond_forfeiture: '#dc2626' };
    return fallback[disp] || '#64748b';
  }

  function _impactBadge(score) {
    const sc = Number(score || 0);
    const col = sc >= 80 ? '#ef4444' : sc >= 60 ? '#f59e0b' : sc >= 40 ? '#3b82f6' : '#64748b';
    return `<span style="display:inline-flex;align-items:center;padding:2px 8px;border-radius:12px;
      font-size:11px;font-weight:700;background:${col}22;color:${col};border:1px solid ${col}44">${sc}/100</span>`;
  }

  function _dispBadge(disp, cat) {
    const label = (disp || 'unknown').replace(/_/g, ' ');
    const col = _dispColor(disp, cat);
    return `<span style="display:inline-flex;align-items:center;padding:2px 8px;border-radius:6px;
      font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;
      background:${col}22;color:${col};border:1px solid ${col}44">${escHtml(label)}</span>`;
  }

  // ── Regional Court Intelligence Panel (CourtListener v4) ────────────────
  async function loadCourtIntel() {
    const panel = $('intCourtIntelPanel');
    if (!panel) return;

    const stateParam = _courtSelectedState ? `?state=${encodeURIComponent(_courtSelectedState)}` : '';
    try {
      const [coverageRes, analyticsRes, healthRes] = await Promise.all([
        _courtCoverage ? Promise.resolve(_courtCoverage) : api('/api/court-intel/coverage'),
        api('/api/court-intel/analytics' + stateParam),
        api('/api/court-intel/api-health'),
      ]);

      if (coverageRes && coverageRes.total_states) _courtCoverage = coverageRes;
      if (analyticsRes && analyticsRes.success) _courtAnalytics = analyticsRes;
      if (healthRes && healthRes.success) _courtApiHealth = healthRes;

      renderCourtIntel(panel);
    } catch (e) {
      panel.innerHTML = `<div style="padding:16px;color:var(--muted);text-align:center">Court intelligence unavailable: ${escHtml(e.message)}</div>`;
    }
  }

  function renderCourtIntel(panel) {
    panel = panel || $('intCourtIntelPanel');
    if (!panel) return;
    const c = C();

    const tabs = [
      { id: 'analytics', label: '📊 Analytics & Scorecards' },
      { id: 'explorer',  label: '🔎 Live CourtListener v4 Explorer' },
      { id: 'lookup',    label: '🗂️ Docket # & Case URL Resolver' },
    ];

    const h = _courtApiHealth || {};
    const healthPill = h.in_maintenance
      ? `<span style="font-size:11px;padding:3px 9px;border-radius:12px;background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b44">🛠️ FLP Maintenance</span>`
      : h.token_configured
        ? `<span style="font-size:11px;padding:3px 9px;border-radius:12px;background:#10b98122;color:#10b981;border:1px solid #10b98144">🟢 API Token Active</span>`
        : `<span style="font-size:11px;padding:3px 9px;border-radius:12px;background:#3b82f622;color:#3b82f6;border:1px solid #3b82f644">🟡 Public Rate Limit</span>`;

    let html = `
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;
        margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid ${c.border}">
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          ${tabs.map(t => `
            <button onclick="SLIntelligence.switchCourtTab('${t.id}')"
              class="int-range-btn ${_courtActiveTab === t.id ? 'active' : ''}"
              style="font-size:12px;padding:6px 12px">${t.label}</button>
          `).join('')}
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          ${healthPill}
          <button onclick="SLIntelligence.triggerIngestion()" class="int-range-btn" style="font-size:11px;padding:5px 10px"
            title="Ingest bail-relevant opinions across all 15 covered states">🔄 Ingest 15 States</button>
          <button onclick="SLIntelligence.triggerIngestion(['FL'])" class="int-range-btn" style="font-size:11px;padding:5px 10px"
            title="Ingest recent Florida state &amp; federal opinions">🌴 Ingest FL</button>
        </div>
      </div>
    `;

    if (_courtActiveTab === 'analytics') {
      html += _renderCourtAnalyticsTab(c);
    } else if (_courtActiveTab === 'explorer') {
      html += _renderCourtExplorerTab(c);
    } else {
      html += _renderCourtLookupTab(c);
    }

    panel.innerHTML = html;
  }

  function _renderCourtAnalyticsTab(c) {
    const a = _courtAnalytics;
    const cov = _courtCoverage;
    let html = '';

    // State Filter Chips
    if (cov && cov.by_state) {
      const stateCounts = {};
      ((a && a.by_state) || []).forEach(s => { stateCounts[s.state] = s.count; });
      html += `<div style="margin-bottom:14px">
        <div style="font-size:11px;color:${c.muted};text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;font-weight:600">
          Jurisdiction Filter · ${cov.total_courts} Courts · ${cov.total_states} States
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:6px">
          <button onclick="SLIntelligence.filterCourtState('')"
            class="int-range-btn ${!_courtSelectedState ? 'active' : ''}" style="font-size:11px;padding:4px 10px">
            All States
          </button>
          ${cov.by_state.map(s => {
            const totalCourts = (s.state_count || 0) + (s.federal_count || 0);
            const active = _courtSelectedState === s.state;
            const cnt = stateCounts[s.state];
            return `<button onclick="SLIntelligence.filterCourtState('${escHtml(s.state)}')"
              class="int-range-btn ${active ? 'active' : ''}" style="font-size:11px;padding:4px 10px"
              title="${escHtml(s.state)}: ${s.state_count} state + ${s.federal_count} federal courts">
              ${escHtml(s.state)} <span style="opacity:0.7">(${totalCourts}${cnt ? ' · ' + cnt : ''})</span>
            </button>`;
          }).join('')}
        </div>
      </div>`;
    }

    if (!a || !a.success || !a.kpi || a.kpi.total_outcomes === 0) {
      html += `<div style="padding:28px 20px;text-align:center;color:${c.muted};background:rgba(255,255,255,0.02);
        border:1px dashed ${c.border};border-radius:12px">
        <div style="font-size:2rem;margin-bottom:8px">📡</div>
        <div style="font-weight:700;font-size:15px;color:${c.text};margin-bottom:6px">
          ${_courtSelectedState ? `No court outcomes ingested yet for ${_courtSelectedState}` : 'No court outcomes ingested into local corpus yet'}
        </div>
        <div style="font-size:12px;margin-bottom:14px">
          Ingest bail-relevant opinions across ${_courtSelectedState || '15 states'} or search CourtListener live right now.
        </div>
        <div style="display:flex;justify-content:center;gap:10px;flex-wrap:wrap">
          <button onclick="SLIntelligence.triggerIngestion(${_courtSelectedState ? `['${escHtml(_courtSelectedState)}']` : ''})"
            class="btn-primary" style="font-size:12px;padding:8px 18px">🚀 Run Ingestion</button>
          <button onclick="SLIntelligence.switchCourtTab('explorer')"
            class="btn-secondary" style="font-size:12px;padding:8px 18px">🔎 Open Live v4 Explorer</button>
        </div>
      </div>`;
      return html;
    }

    const k = a.kpi;
    const kpiCards = [
      { label: 'Total Outcomes', value: (k.total_outcomes || 0).toLocaleString(), sub: k.last_ingestion ? 'Updated ' + new Date(k.last_ingestion).toLocaleDateString() : 'Corpus ready', icon: '📊', color: c.accent2 },
      { label: 'Bail-Critical', value: (k.bail_critical_count || 0).toLocaleString(), sub: `${k.bail_critical_pct || 0}% of outcomes`, icon: '🚨', color: c.danger },
      { label: 'Avg Bail Impact', value: `${k.avg_bail_impact || 0}/100`, sub: 'Forfeiture / FTA weight', icon: '⚖️', color: c.warning },
      { label: 'Matched Defendants', value: (k.matched_defendants || 0).toLocaleString(), sub: 'Linked to arrest leads', icon: '🔗', color: c.accent },
      { label: 'Active Coverage', value: `${k.active_states || 0} / ${k.unique_courts || 0}`, sub: 'States / Unique Courts', icon: '🗺️', color: c.info },
      { label: 'Judges Indexed', value: (k.unique_judges || 0).toLocaleString(), sub: 'Scorecards tracked', icon: '👨‍⚖️', color: c.purple },
    ];

    html += `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:18px">
      ${kpiCards.map(card => `
        <div style="background:rgba(255,255,255,0.02);border:1px solid ${c.border};border-radius:10px;padding:12px">
          <div style="font-size:11px;color:${c.muted};font-weight:600;text-transform:uppercase;letter-spacing:0.04em">${card.icon} ${card.label}</div>
          <div style="font-size:20px;font-weight:800;color:${card.color};margin:4px 0 2px">${card.value}</div>
          <div style="font-size:11px;color:${c.muted}">${escHtml(card.sub)}</div>
        </div>
      `).join('')}
    </div>`;

    // Two-column visual analytics
    html += `<div class="int-grid-2" style="margin-bottom:18px">`;

    // Col 1: Disposition Breakdown
    html += `<div style="background:rgba(255,255,255,0.02);border:1px solid ${c.border};border-radius:10px;padding:14px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <span style="font-size:12px;font-weight:700;text-transform:uppercase;color:${c.muted}">Disposition &amp; Bail Risk Distribution</span>
        <span style="font-size:10px;color:${c.muted}">Click bar to search</span>
      </div>`;
    if (a.by_disposition && a.by_disposition.length > 0) {
      a.by_disposition.slice(0, 10).forEach(d => {
        const col = _dispColor(d.disposition, d.category);
        const label = (d.disposition || 'unknown').replace(/_/g, ' ');
        html += `<div onclick="SLIntelligence.exploreDisposition('${escHtml(d.disposition)}')"
          style="display:flex;align-items:center;gap:8px;margin-bottom:7px;cursor:pointer" title="Explore ${escHtml(label)} in CourtListener">
          <div style="width:118px;font-size:11px;color:${c.text};font-weight:600;text-transform:capitalize;
            overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(label)}</div>
          <div style="flex:1;background:${c.border}44;border-radius:4px;height:16px;overflow:hidden">
            <div style="width:${Math.max(d.pct, 2)}%;height:100%;background:${col};border-radius:4px;transition:width 0.5s ease"></div>
          </div>
          <div style="width:62px;text-align:right;font-size:11px;color:${c.muted}">${d.count} (${d.pct}%)</div>
        </div>`;
      });
    } else {
      html += `<div style="color:${c.muted};font-size:12px;padding:12px 0">No classified dispositions yet.</div>`;
    }
    html += `</div>`;

    // Col 2: Court / Judge Bail Scorecard
    html += `<div style="background:rgba(255,255,255,0.02);border:1px solid ${c.border};border-radius:10px;padding:14px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;gap:8px">
        <span style="font-size:12px;font-weight:700;text-transform:uppercase;color:${c.muted}">Court &amp; Judge Bail Scorecard</span>
        <div style="display:flex;gap:4px">
          <button onclick="SLIntelligence.setCourtScorecardMode('courts')" class="int-range-btn ${_courtScorecardMode === 'courts' ? 'active' : ''}" style="font-size:10px;padding:3px 8px">Courts</button>
          <button onclick="SLIntelligence.setCourtScorecardMode('judges')" class="int-range-btn ${_courtScorecardMode === 'judges' ? 'active' : ''}" style="font-size:10px;padding:3px 8px">Judges</button>
        </div>
      </div>`;
    if (_courtScorecardMode === 'judges') {
      const judges = a.by_judge || [];
      if (judges.length === 0) {
        html += `<div style="color:${c.muted};font-size:12px;padding:12px 0">No judge metadata populated in current filter.</div>`;
      } else {
        html += `<div style="max-height:245px;overflow-y:auto;display:flex;flex-direction:column;gap:6px">
          ${judges.map(j => `
            <div onclick="SLIntelligence.exploreJudge('${escHtml(j.judge)}')"
              style="display:flex;justify-content:space-between;align-items:center;padding:7px 10px;border-radius:7px;
              background:rgba(255,255,255,0.02);border:1px solid ${c.border};cursor:pointer;font-size:12px"
              title="Search CourtListener cases for Judge ${escHtml(j.judge)}">
              <div style="min-width:0">
                <div style="font-weight:600;color:${c.text};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(j.judge)}</div>
                <div style="font-size:10px;color:${c.muted}">${j.count} cases · ${j.favorable} fav / ${j.adverse} adv</div>
              </div>
              <div style="display:flex;align-items:center;gap:6px;flex-shrink:0">
                ${j.bail_critical > 0 ? `<span style="font-size:10px;color:#ef4444;font-weight:700">${j.bail_critical} crit</span>` : ''}
                ${_impactBadge(j.avg_impact)}
              </div>
            </div>
          `).join('')}
        </div>`;
      }
    } else {
      const courts = a.by_court || [];
      if (courts.length === 0) {
        html += `<div style="color:${c.muted};font-size:12px;padding:12px 0">No courts in current filter.</div>`;
      } else {
        html += `<div style="max-height:245px;overflow-y:auto;display:flex;flex-direction:column;gap:6px">
          ${courts.map(ct => `
            <div style="display:flex;justify-content:space-between;align-items:center;padding:7px 10px;border-radius:7px;
              background:rgba(255,255,255,0.02);border:1px solid ${c.border};font-size:12px">
              <div style="min-width:0">
                <div style="font-weight:600;color:${c.text};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                  <span style="color:${c.accent};margin-right:4px">${escHtml(ct.state || '')}</span>${escHtml(ct.court_name)}
                </div>
                <div style="font-size:10px;color:${c.muted}">${ct.count} opinions · ${ct.forfeiture_fta_count || 0} forfeiture/FTA</div>
              </div>
              <span style="font-size:11px;font-weight:700;color:${ct.bail_critical > 0 ? '#ef4444' : c.muted};flex-shrink:0">
                ${ct.bail_critical} bail-crit
              </span>
            </div>
          `).join('')}
        </div>`;
      }
    }
    html += `</div></div>`;

    // Monthly Trend Strip
    if (a.monthly_trend && a.monthly_trend.length > 0) {
      const maxM = Math.max(...a.monthly_trend.map(m => m.total), 1);
      html += `<div style="background:rgba(255,255,255,0.02);border:1px solid ${c.border};border-radius:10px;padding:14px;margin-bottom:18px">
        <div style="font-size:12px;font-weight:700;text-transform:uppercase;color:${c.muted};margin-bottom:10px">
          📅 Filing Timeline (Last ${a.monthly_trend.length} Months)
        </div>
        <div style="display:flex;align-items:flex-end;gap:8px;height:78px;padding-top:8px">
          ${a.monthly_trend.map(m => {
            const hPct = Math.max(Math.round((m.total / maxM) * 100), 10);
            const critPct = m.total > 0 ? Math.round((m.bail_critical / m.total) * 100) : 0;
            return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;min-width:26px"
              title="${escHtml(m.month)}: ${m.total} total, ${m.bail_critical} bail-critical">
              <div style="font-size:10px;color:${c.text};font-weight:700">${m.total}</div>
              <div style="width:100%;max-width:28px;height:42px;display:flex;align-items:flex-end;background:${c.border}33;border-radius:4px;overflow:hidden">
                <div style="width:100%;height:${hPct}%;background:linear-gradient(180deg,${critPct > 30 ? '#ef4444' : '#6366f1'},#3b82f6);border-radius:4px 4px 0 0"></div>
              </div>
              <div style="font-size:9px;color:${c.muted}">${escHtml(m.month.slice(2))}</div>
            </div>`;
          }).join('')}
        </div>
      </div>`;
    }

    // Top Bail Precedents Table
    const precedents = a.top_precedents || [];
    html += `<div style="background:rgba(255,255,255,0.02);border:1px solid ${c.border};border-radius:10px;padding:14px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <span style="font-size:12px;font-weight:700;text-transform:uppercase;color:${c.muted}">⭐ Highest-Impact Bail Precedents</span>
        <span style="font-size:11px;color:${c.muted}">Top ${precedents.length} scored cases</span>
      </div>`;
    if (precedents.length === 0) {
      html += `<div style="color:${c.muted};font-size:12px">No scored precedents yet.</div>`;
    } else {
      html += `<div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <thead>
            <tr style="border-bottom:1px solid ${c.border};color:${c.muted};font-size:10px;text-transform:uppercase;text-align:left">
              <th style="padding:8px 6px">Case Name</th>
              <th style="padding:8px 6px">Court</th>
              <th style="padding:8px 6px">Docket #</th>
              <th style="padding:8px 6px">Filed</th>
              <th style="padding:8px 6px">Disposition</th>
              <th style="padding:8px 6px">Impact</th>
              <th style="padding:8px 6px;text-align:right">Inspect</th>
            </tr>
          </thead>
          <tbody>
            ${precedents.map((p, idx) => `
              <tr style="border-bottom:1px solid rgba(255,255,255,0.04)">
                <td style="padding:8px 6px;font-weight:600;color:${c.text};max-width:260px">
                  <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(p.case_name)}">${escHtml(p.case_name)}</div>
                  ${p.matched_defendant_name ? `<div style="font-size:10px;color:${c.accent}">🔗 Match: ${escHtml(p.matched_defendant_name)}</div>` : ''}
                </td>
                <td style="padding:8px 6px;color:${c.muted}">${escHtml(p.court_name || p.court_id || '—')}</td>
                <td style="padding:8px 6px;font-family:monospace;color:${c.text}">${escHtml(p.docket_number || '—')}</td>
                <td style="padding:8px 6px;color:${c.muted};white-space:nowrap">${escHtml(p.date_filed || '—')}</td>
                <td style="padding:8px 6px">${_dispBadge(p.disposition, p.disposition_category)}</td>
                <td style="padding:8px 6px">${_impactBadge((p.bail_impact || {}).score)}</td>
                <td style="padding:8px 6px;text-align:right">
                  <button onclick="SLIntelligence.openCaseDossier('${escHtml(String(p.cluster_id || p.source_id || ''))}')"
                    class="int-range-btn" style="font-size:11px;padding:3px 9px">📖 Dossier</button>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>`;
    }
    html += `</div>`;
    return html;
  }

  function _renderCourtExplorerTab(c) {
    const presets = (_courtCoverage && _courtCoverage.presets) || (_courtAnalytics && _courtAnalytics.presets) || [];
    const states = (_courtCoverage && _courtCoverage.states) || [];
    const selectedState = _liveSearchForm.state || '';
    let courtsForDropdown = [];
    if (_courtCoverage && _courtCoverage.by_state) {
      _courtCoverage.by_state.forEach(s => {
        if (!selectedState || s.state === selectedState || s.state === 'MULTI') {
          (s.courts || []).forEach(ct => courtsForDropdown.push(ct));
        }
      });
    }

    let html = '';

    // 1-Click Bail Law Presets
    if (presets.length > 0) {
      html += `<div style="margin-bottom:14px">
        <div style="font-size:11px;color:${c.muted};text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;font-weight:600">
          ⚡ 1-Click Bail Law Presets
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:6px">
          ${presets.map(p => `
            <button onclick="SLIntelligence.applyCourtPreset('${escHtml(p.key)}')"
              class="int-range-btn ${_liveSearchForm.preset === p.key ? 'active' : ''}"
              style="font-size:11px;padding:5px 10px">${escHtml(p.label)}</button>
          `).join('')}
        </div>
      </div>`;
    }

    // Live Search Form
    const inputStyle = `padding:7px 10px;border-radius:7px;border:1px solid ${c.border};background:rgba(15,23,42,0.65);color:${c.text};font-size:12px;width:100%;box-sizing:border-box`;
    html += `<div style="background:rgba(255,255,255,0.02);border:1px solid ${c.border};border-radius:10px;padding:14px;margin-bottom:16px">
      <div style="display:grid;grid-template-columns:2fr 1.2fr 1fr 1.3fr;gap:10px;margin-bottom:10px">
        <div>
          <label style="display:block;font-size:10px;color:${c.muted};text-transform:uppercase;margin-bottom:4px">Search Query / Boolean Terms</label>
          <input id="clLiveQuery" type="text" value="${escHtml(_liveSearchForm.q)}"
            placeholder='e.g. "bond forfeiture" OR "failure to appear"' style="${inputStyle}"
            onkeydown="if(event.key==='Enter')SLIntelligence.runCourtLiveSearch()">
        </div>
        <div>
          <label style="display:block;font-size:10px;color:${c.muted};text-transform:uppercase;margin-bottom:4px">API Endpoint Type</label>
          <select id="clLiveType" style="${inputStyle}">
            <option value="o" ${_liveSearchForm.type === 'o' ? 'selected' : ''}>Case Law Opinions (type=o)</option>
            <option value="r" ${_liveSearchForm.type === 'r' ? 'selected' : ''}>RECAP Federal Dockets (type=r)</option>
            <option value="d" ${_liveSearchForm.type === 'd' ? 'selected' : ''}>PACER Dockets (type=d)</option>
            <option value="p" ${_liveSearchForm.type === 'p' ? 'selected' : ''}>Judges &amp; People (type=p)</option>
            <option value="oa" ${_liveSearchForm.type === 'oa' ? 'selected' : ''}>Oral Arguments (type=oa)</option>
          </select>
        </div>
        <div>
          <label style="display:block;font-size:10px;color:${c.muted};text-transform:uppercase;margin-bottom:4px">State</label>
          <select id="clLiveState" style="${inputStyle}" onchange="SLIntelligence.onExplorerStateChange(this.value)">
            <option value="">All Covered States</option>
            ${states.map(st => `<option value="${escHtml(st)}" ${selectedState === st ? 'selected' : ''}>${escHtml(st)}</option>`).join('')}
          </select>
        </div>
        <div>
          <label style="display:block;font-size:10px;color:${c.muted};text-transform:uppercase;margin-bottom:4px">Court</label>
          <select id="clLiveCourt" style="${inputStyle}">
            <option value="">All Courts in Selection</option>
            ${courtsForDropdown.map(ct => `<option value="${escHtml(ct.id)}" ${_liveSearchForm.court === ct.id ? 'selected' : ''}>${escHtml(ct.name)}</option>`).join('')}
          </select>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1.2fr 1fr 1fr auto;gap:10px;align-items:end">
        <div>
          <label style="display:block;font-size:10px;color:${c.muted};text-transform:uppercase;margin-bottom:4px">Judge Filter</label>
          <input id="clLiveJudge" type="text" value="${escHtml(_liveSearchForm.judge)}" placeholder="Judge last name (optional)" style="${inputStyle}"
            onkeydown="if(event.key==='Enter')SLIntelligence.runCourtLiveSearch()">
        </div>
        <div>
          <label style="display:block;font-size:10px;color:${c.muted};text-transform:uppercase;margin-bottom:4px">Filed After</label>
          <input id="clLiveAfter" type="date" value="${escHtml(_liveSearchForm.filed_after)}" style="${inputStyle}">
        </div>
        <div>
          <label style="display:block;font-size:10px;color:${c.muted};text-transform:uppercase;margin-bottom:4px">Filed Before</label>
          <input id="clLiveBefore" type="date" value="${escHtml(_liveSearchForm.filed_before)}" style="${inputStyle}">
        </div>
        <button id="clLiveSearchBtn" onclick="SLIntelligence.runCourtLiveSearch()" class="btn-primary" style="font-size:12px;padding:8px 16px;white-space:nowrap">
          🔍 Run Live v4 Search
        </button>
      </div>
    </div>`;

    // Live Search Results + On-the-Fly Analytics Banner
    if (_liveSearchResponse) {
      if (!_liveSearchResponse.success) {
        html += `<div style="padding:14px;border-radius:8px;border:1px solid #ef444444;background:#ef444415;color:#ef4444;font-size:12px">
          ⚠️ Live Search Error: ${escHtml(_liveSearchResponse.error || 'Unknown error')}
        </div>`;
      } else {
        const an = _liveSearchResponse.analytics || {};
        html += `<div style="background:linear-gradient(135deg,rgba(99,102,241,0.08),rgba(16,185,129,0.05));
          border:1px solid ${c.border};border-radius:10px;padding:12px 14px;margin-bottom:14px">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px">
            <div style="font-size:12px;font-weight:700;color:${c.text}">
              📈 Live Result Analytics · ${(_liveSearchResponse.count || 0).toLocaleString()} total matches (analyzing top ${_liveSearchResults.length})
            </div>
            <div style="display:flex;gap:12px;font-size:11px;color:${c.muted}">
              <span>Bail-Critical: <strong style="color:#ef4444">${an.bail_critical_pct || 0}%</strong></span>
              <span>Avg Impact: <strong style="color:${c.warning}">${an.avg_bail_impact || 0}/100</strong></span>
              <span>Total Citations: <strong style="color:${c.accent}">${(an.total_citations || 0).toLocaleString()}</strong></span>
            </div>
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:6px">
            ${(an.by_disposition || []).slice(0, 6).map(d => `
              <span style="font-size:10px;padding:2px 8px;border-radius:10px;background:rgba(255,255,255,0.04);
                border:1px solid ${c.border};color:${c.text}">
                ${escHtml(d.disposition.replace(/_/g, ' '))}: <strong>${d.count}</strong>
              </span>
            `).join('')}
            ${(an.by_court || []).slice(0, 3).map(ct => `
              <span style="font-size:10px;padding:2px 8px;border-radius:10px;background:rgba(99,102,241,0.12);
                border:1px solid #6366f144;color:#a5b4fc">
                🏛️ ${escHtml(ct.court)}: <strong>${ct.count}</strong>
              </span>
            `).join('')}
          </div>
        </div>`;

        if (_liveSearchResults.length === 0) {
          html += `<div style="padding:24px;text-align:center;color:${c.muted};font-size:12px">No matching records returned by CourtListener. Try broader keywords or remove date bounds.</div>`;
        } else {
          html += `<div style="display:flex;flex-direction:column;gap:10px">
            ${_liveSearchResults.map((r, idx) => `
              <div style="background:rgba(255,255,255,0.02);border:1px solid ${c.border};border-radius:10px;padding:12px 14px">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap;margin-bottom:6px">
                  <div style="min-width:0;flex:1">
                    <div style="font-size:14px;font-weight:700;color:${c.text}">${escHtml(r.case_name)}</div>
                    <div style="font-size:11px;color:${c.muted};margin-top:2px;display:flex;flex-wrap:wrap;gap:8px">
                      <span>🏛️ ${escHtml(r.court_name || r.court_id || 'Court')}</span>
                      ${r.docket_number ? `<span style="font-family:monospace">📁 ${escHtml(r.docket_number)}</span>` : ''}
                      ${r.date_filed ? `<span>📅 ${escHtml(r.date_filed)}</span>` : ''}
                      ${r.judges ? `<span>👨‍⚖️ ${escHtml(r.judges)}</span>` : ''}
                      ${r.citation_count ? `<span>📚 ${r.citation_count} cites</span>` : ''}
                    </div>
                  </div>
                  <div style="display:flex;align-items:center;gap:6px;flex-shrink:0">
                    ${_dispBadge(r.disposition, r.disposition_category)}
                    ${_impactBadge((r.bail_impact || {}).score)}
                  </div>
                </div>
                ${r.snippet ? `<div style="font-size:12px;color:${c.muted};line-height:1.45;margin-bottom:10px">${escHtml(r.snippet)}</div>` : ''}
                <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
                  ${r.search_type !== 'p' ? `
                    <button onclick="SLIntelligence.openCaseDossier('${escHtml(String(r.cluster_id || r.source_id || ''))}', ${idx})"
                      class="int-range-btn" style="font-size:11px;padding:4px 10px">📖 Full Dossier</button>
                    <button id="clSaveBtn-${idx}" onclick="SLIntelligence.saveCourtCase(${idx})"
                      class="int-range-btn" style="font-size:11px;padding:4px 10px">📥 Save to Corpus</button>
                  ` : ''}
                  ${r.absolute_url ? `
                    <a href="https://www.courtlistener.com${escHtml(r.absolute_url)}" target="_blank" rel="noopener noreferrer"
                      style="font-size:11px;color:${c.info};text-decoration:none;margin-left:auto">↗ Open on CourtListener</a>
                  ` : ''}
                </div>
              </div>
            `).join('')}
          </div>`;
        }
      }
    } else {
      html += `<div style="padding:20px;text-align:center;color:${c.muted};font-size:12px;border:1px dashed ${c.border};border-radius:10px">
        Select a bail preset above or enter a custom query / court / judge filter to run live analytics directly against CourtListener REST API v4.
      </div>`;
    }

    return html;
  }

  function _renderCourtLookupTab(c) {
    const states = (_courtCoverage && _courtCoverage.states) || [];
    const inputStyle = `padding:7px 10px;border-radius:7px;border:1px solid ${c.border};background:rgba(15,23,42,0.65);color:${c.text};font-size:12px;width:100%;box-sizing:border-box`;

    let lookupHtml = '';
    if (_docketLookupResults) {
      if (!_docketLookupResults.success) {
        lookupHtml = `<div style="margin-top:12px;color:#ef4444;font-size:12px">⚠️ ${escHtml(_docketLookupResults.error)}</div>`;
      } else {
        const dockets = _docketLookupResults.dockets || [];
        const clusters = _docketLookupResults.clusters || [];
        const hits = _docketLookupResults.search_hits || [];
        lookupHtml = `<div style="margin-top:14px;display:flex;flex-direction:column;gap:10px">
          <div style="font-size:11px;color:${c.muted};font-weight:700">
            Found ${dockets.length} Dockets · ${clusters.length} Clusters · ${hits.length} Search Hits
          </div>
          ${clusters.map(cl => `
            <div style="padding:10px;border-radius:8px;border:1px solid ${c.border};background:rgba(255,255,255,0.02);display:flex;justify-content:space-between;align-items:center;gap:8px">
              <div>
                <div style="font-size:12px;font-weight:700;color:${c.text}">${escHtml(cl.case_name || 'Cluster #' + cl.cluster_id)}</div>
                <div style="font-size:11px;color:${c.muted}">Filed ${escHtml(cl.date_filed || '—')} · ${cl.citation_count || 0} cites</div>
              </div>
              <button onclick="SLIntelligence.openCaseDossier('${escHtml(String(cl.cluster_id))}')" class="int-range-btn" style="font-size:11px;padding:4px 10px">📖 Full Dossier</button>
            </div>
          `).join('')}
          ${dockets.map(d => `
            <div style="padding:10px;border-radius:8px;border:1px solid ${c.border};background:rgba(255,255,255,0.02);font-size:12px">
              <div style="font-weight:700;color:${c.text}">${escHtml(d.case_name || d.docket_number)}</div>
              <div style="font-size:11px;color:${c.muted}">🏛️ ${escHtml(d.court_name)} · 📁 ${escHtml(d.docket_number)} · Judge: ${escHtml(d.assigned_to_str || '—')}</div>
              ${d.cause || d.nature_of_suit ? `<div style="font-size:11px;color:${c.muted};margin-top:2px">${escHtml(d.cause)} ${escHtml(d.nature_of_suit)}</div>` : ''}
            </div>
          `).join('')}
          ${hits.slice(0, 5).map(h => `
            <div style="padding:10px;border-radius:8px;border:1px solid ${c.border};background:rgba(255,255,255,0.02);display:flex;justify-content:space-between;align-items:center;gap:8px">
              <div>
                <div style="font-size:12px;font-weight:600;color:${c.text}">${escHtml(h.case_name)}</div>
                <div style="font-size:11px;color:${c.muted}">${escHtml(h.court_name)} · ${escHtml(h.docket_number || '')} · ${escHtml(h.date_filed || '')}</div>
              </div>
              <button onclick="SLIntelligence.openCaseDossier('${escHtml(String(h.cluster_id || h.source_id || ''))}')" class="int-range-btn" style="font-size:11px;padding:4px 10px">📖 Dossier</button>
            </div>
          `).join('')}
        </div>`;
      }
    }

    return `<div class="int-grid-2">
      <div style="background:rgba(255,255,255,0.02);border:1px solid ${c.border};border-radius:10px;padding:16px">
        <div style="font-size:13px;font-weight:700;color:${c.text};margin-bottom:6px">📁 Lookup by Docket Number</div>
        <div style="font-size:11px;color:${c.muted};margin-bottom:12px">
          Queries <code>/dockets/?docket_number=</code>, <code>/clusters/?docket__docket_number=</code>, and fuzzy docket search simultaneously.
        </div>
        <div style="display:grid;grid-template-columns:1.5fr 1fr;gap:8px;margin-bottom:10px">
          <input id="clDocketNumInput" type="text" placeholder="e.g. 23A994 or 2D23-1234" style="${inputStyle}"
            onkeydown="if(event.key==='Enter')SLIntelligence.runDocketLookup()">
          <select id="clDocketState" style="${inputStyle}">
            <option value="">Any State</option>
            ${states.map(st => `<option value="${escHtml(st)}">${escHtml(st)}</option>`).join('')}
          </select>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <input id="clDocketCourt" type="text" placeholder="Optional court ID (e.g. flsd, fladistctapp_2)" style="${inputStyle}">
          <button onclick="SLIntelligence.runDocketLookup()" class="btn-primary" style="font-size:12px;padding:7px 14px;white-space:nowrap">🔍 Lookup</button>
        </div>
        <div id="clDocketLookupResults">${lookupHtml}</div>
      </div>

      <div style="background:rgba(255,255,255,0.02);border:1px solid ${c.border};border-radius:10px;padding:16px">
        <div style="font-size:13px;font-weight:700;color:${c.text};margin-bottom:6px">🔗 Inspect Case by URL or Cluster ID</div>
        <div style="font-size:11px;color:${c.muted};margin-bottom:12px">
          Follows the v4 hierarchy (<strong>Cluster → Docket → Opinion</strong> with <code>html_with_citations</code> &amp; citation graph) for any CourtListener link or ID.
        </div>
        <div style="display:flex;gap:8px;margin-bottom:10px">
          <input id="clUrlInput" type="text" placeholder="https://www.courtlistener.com/opinion/2812209/... or 2812209" style="${inputStyle}"
            onkeydown="if(event.key==='Enter')SLIntelligence.openCaseDossierFromInput()">
          <button onclick="SLIntelligence.openCaseDossierFromInput()" class="btn-primary" style="font-size:12px;padding:7px 14px;white-space:nowrap">📖 Load Dossier</button>
        </div>
        <div style="font-size:11px;color:${c.muted};line-height:1.5;background:rgba(255,255,255,0.02);padding:10px;border-radius:8px;border:1px solid ${c.border}">
          <strong>Pro Tip:</strong> Paste any CourtListener case link from an attorney brief or Slack alert. ShamrockLeads resolves the Cluster ID, extracts the Docket filing/termination dates and judge assignment, pulls the opinion text, and auto-scores forfeiture / FTA signals.
        </div>
      </div>
    </div>`;
  }

  function switchCourtTab(tabId) {
    _courtActiveTab = tabId || 'analytics';
    renderCourtIntel();
  }

  async function filterCourtState(st) {
    _courtSelectedState = st || '';
    await loadCourtIntel();
  }

  function setCourtScorecardMode(mode) {
    _courtScorecardMode = mode === 'judges' ? 'judges' : 'courts';
    renderCourtIntel();
  }

  function onExplorerStateChange(st) {
    _liveSearchForm.state = st || '';
    _liveSearchForm.court = '';
    renderCourtIntel();
  }

  function exploreDisposition(disp) {
    const queryMap = {
      bond_forfeiture: '"bond forfeiture" OR estreat',
      fta: '"failure to appear" OR "bench warrant"',
      bond_revoked: '"revoke bond" OR "bond revocation"',
      bond_reduced: '"bond reduced" OR "reduce bond"',
      bond_increased: '"bond increased" OR "increase bond"',
      probation_violation: '"violation of probation"',
      pretrial_order: '"pretrial detention" OR "pretrial release"',
    };
    _courtActiveTab = 'explorer';
    _liveSearchForm.q = queryMap[disp] || disp.replace(/_/g, ' ');
    _liveSearchForm.preset = '';
    _liveSearchForm.type = 'o';
    renderCourtIntel();
    runCourtLiveSearch();
  }

  function exploreJudge(judgeName) {
    _courtActiveTab = 'explorer';
    _liveSearchForm.judge = judgeName || '';
    _liveSearchForm.q = '';
    _liveSearchForm.type = 'o';
    renderCourtIntel();
    runCourtLiveSearch();
  }

  function applyCourtPreset(presetKey) {
    const presets = (_courtCoverage && _courtCoverage.presets) || (_courtAnalytics && _courtAnalytics.presets) || [];
    const found = presets.find(p => p.key === presetKey);
    if (!found) return;
    _liveSearchForm.preset = presetKey;
    _liveSearchForm.q = found.query || '';
    _liveSearchForm.type = found.type || 'o';
    renderCourtIntel();
    runCourtLiveSearch();
  }

  async function runCourtLiveSearch() {
    const qEl = $('clLiveQuery');
    const tEl = $('clLiveType');
    const sEl = $('clLiveState');
    const cEl = $('clLiveCourt');
    const jEl = $('clLiveJudge');
    const aEl = $('clLiveAfter');
    const bEl = $('clLiveBefore');

    if (qEl) _liveSearchForm.q = qEl.value;
    if (tEl) _liveSearchForm.type = tEl.value;
    if (sEl) _liveSearchForm.state = sEl.value;
    if (cEl) _liveSearchForm.court = cEl.value;
    if (jEl) _liveSearchForm.judge = jEl.value;
    if (aEl) _liveSearchForm.filed_after = aEl.value;
    if (bEl) _liveSearchForm.filed_before = bEl.value;

    const btn = $('clLiveSearchBtn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Searching...'; }

    const qs = new URLSearchParams();
    if (_liveSearchForm.q) qs.set('q', _liveSearchForm.q);
    if (_liveSearchForm.preset && !_liveSearchForm.q) qs.set('preset', _liveSearchForm.preset);
    if (_liveSearchForm.type) qs.set('type', _liveSearchForm.type);
    if (_liveSearchForm.state) qs.set('state', _liveSearchForm.state);
    if (_liveSearchForm.court) qs.set('court', _liveSearchForm.court);
    if (_liveSearchForm.judge) qs.set('judge', _liveSearchForm.judge);
    if (_liveSearchForm.filed_after) qs.set('filed_after', _liveSearchForm.filed_after);
    if (_liveSearchForm.filed_before) qs.set('filed_before', _liveSearchForm.filed_before);

    const res = await api('/api/court-intel/live-search?' + qs.toString());
    _liveSearchResponse = res;
    _liveSearchResults = (res && res.success && res.results) ? res.results : [];
    renderCourtIntel();
  }

  async function runDocketLookup() {
    const dEl = $('clDocketNumInput');
    const sEl = $('clDocketState');
    const cEl = $('clDocketCourt');
    const docketNumber = dEl ? dEl.value.trim() : '';
    if (!docketNumber) {
      showToast('Enter a docket number to search', 'error');
      return;
    }
    const qs = new URLSearchParams({ docket_number: docketNumber });
    if (sEl && sEl.value) qs.set('state', sEl.value);
    if (cEl && cEl.value.trim()) qs.set('court', cEl.value.trim());

    const container = $('clDocketLookupResults');
    if (container) container.innerHTML = `<div style="padding:12px;color:var(--muted)"><span class="spinner-sm"></span> Resolving Docket &amp; Clusters...</div>`;

    _docketLookupResults = await api('/api/court-intel/docket-lookup?' + qs.toString());
    renderCourtIntel();
  }

  function openCaseDossierFromInput() {
    const inp = $('clUrlInput');
    const val = inp ? inp.value.trim() : '';
    if (!val) {
      showToast('Paste a CourtListener opinion URL or Cluster ID', 'error');
      return;
    }
    openCaseDossier(val);
  }

  async function openCaseDossier(target, liveIdx) {
    const modal = $('intCaseDossierModal');
    const titleEl = $('intDossierTitle');
    const bodyEl = $('intDossierBody');
    const footerEl = $('intDossierFooter');
    if (!modal || !bodyEl) return;

    modal.classList.add('show');
    if (titleEl) titleEl.textContent = '📖 Loading Case Law Dossier...';
    bodyEl.innerHTML = `<div style="padding:36px;text-align:center;color:var(--muted)"><span class="spinner-sm"></span> Resolving Court → Docket → Cluster → Opinion...</div>`;

    const val = String(target || '').trim();
    const paramKey = val.includes('/') ? 'url' : 'cluster_id';
    const res = await api(`/api/court-intel/case-dossier?${paramKey}=${encodeURIComponent(val)}`);

    if (!res || !res.success || !res.dossier) {
      if (titleEl) titleEl.textContent = '⚠️ Dossier Unavailable';
      bodyEl.innerHTML = `<div style="padding:20px;color:#ef4444">${escHtml((res && res.error) || 'Could not load case dossier')}</div>`;
      return;
    }

    const d = res.dossier;
    _lastDossier = d;
    const c = C();
    if (titleEl) titleEl.textContent = `📖 ${d.case_name}`;

    const dck = d.docket || {};
    const cl = d.cluster || {};
    const op = d.opinion || {};
    const ct = d.court || {};

    let bodyHtml = '';
    if (res.offline_fallback) {
      bodyHtml += `<div style="margin-bottom:12px;padding:8px 12px;border-radius:8px;background:#f59e0b22;border:1px solid #f59e0b44;color:#f59e0b;font-size:12px">
        Showing cached local corpus summary (live CourtListener detail unavailable: ${escHtml(res.warning || '')})
      </div>`;
    }

    bodyHtml += `
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:14px">
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          ${_dispBadge(d.disposition, d.disposition_category)}
          ${_impactBadge((d.bail_impact || {}).score)}
          <span style="font-size:12px;color:${c.muted}">🏛️ ${escHtml(ct.name || ct.id || 'Court')} (${escHtml(ct.state || '—')})</span>
        </div>
        ${d.absolute_url ? `<a href="https://www.courtlistener.com${escHtml(d.absolute_url)}" target="_blank" rel="noopener noreferrer"
          style="font-size:12px;color:${c.info}">↗ View on CourtListener</a>` : ''}
      </div>

      ${(d.bail_impact && d.bail_impact.factors && d.bail_impact.factors.length) ? `
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px">
          ${d.bail_impact.factors.map(f => `<span style="font-size:11px;padding:2px 8px;border-radius:6px;background:rgba(99,102,241,0.12);color:#a5b4fc">${escHtml(f)}</span>`).join('')}
        </div>` : ''}

      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin-bottom:14px">
        <div style="padding:10px;border-radius:8px;border:1px solid ${c.border};background:rgba(255,255,255,0.02);font-size:12px">
          <div style="font-size:10px;color:${c.muted};text-transform:uppercase;margin-bottom:4px">Docket Metadata</div>
          <div><strong>Docket #:</strong> ${escHtml(dck.docket_number || '—')}</div>
          <div><strong>Filed:</strong> ${escHtml(dck.date_filed || '—')}</div>
          <div><strong>Terminated:</strong> ${escHtml(dck.date_terminated || 'Open/Unset')}</div>
          <div><strong>Assigned Judge:</strong> ${escHtml(dck.assigned_to_str || cl.judges || '—')}</div>
        </div>
        <div style="padding:10px;border-radius:8px;border:1px solid ${c.border};background:rgba(255,255,255,0.02);font-size:12px">
          <div style="font-size:10px;color:${c.muted};text-transform:uppercase;margin-bottom:4px">Cluster &amp; Citations</div>
          <div><strong>Status:</strong> ${escHtml(cl.precedential_status || '—')}</div>
          <div><strong>Cited By:</strong> ${cl.citation_count || 0} decisions</div>
          <div><strong>Citations:</strong> ${escHtml((cl.citations || []).join(', ') || '—')}</div>
          <div><strong>Cause / Suit:</strong> ${escHtml(dck.cause || dck.nature_of_suit || '—')}</div>
        </div>
        <div style="padding:10px;border-radius:8px;border:1px solid ${c.border};background:rgba(255,255,255,0.02);font-size:12px">
          <div style="font-size:10px;color:${c.muted};text-transform:uppercase;margin-bottom:4px">Opinion &amp; Authorities</div>
          <div><strong>Author:</strong> ${escHtml(op.author_str || (op.per_curiam ? 'Per Curiam' : '—'))}</div>
          <div><strong>Type:</strong> ${escHtml(op.type || 'Combined')}</div>
          <div><strong>Authorities Cited:</strong> ${op.opinions_cited_count || 0} opinions</div>
          <div><strong>Corpus Status:</strong> ${d.saved_in_corpus ? '✅ Saved locally' : '☁️ Live API only'}</div>
        </div>
      </div>

      ${(d.ai_analysis || d.matched_defendant_name) ? `
        <div style="padding:12px;border-radius:8px;border:1px solid #10b98144;background:#10b98112;margin-bottom:14px;font-size:12px">
          <div style="font-weight:700;color:#10b981;margin-bottom:4px">🤖 Stored AI &amp; Defendant Match Intelligence</div>
          ${d.matched_defendant_name ? `<div><strong>Matched Defendant:</strong> ${escHtml(d.matched_defendant_name)} (${Math.round((d.match_confidence || 0) * 100)}% confidence)</div>` : ''}
          ${(d.ai_analysis && d.ai_analysis.bail_implications) ? `<div style="margin-top:4px">${escHtml(d.ai_analysis.bail_implications)}</div>` : ''}
        </div>` : ''}

      <div style="font-size:11px;color:${c.muted};text-transform:uppercase;margin-bottom:6px;font-weight:700">Opinion / Syllabus Text</div>
      <div style="padding:12px;border-radius:8px;border:1px solid ${c.border};background:rgba(0,0,0,0.25);
        font-size:12px;line-height:1.55;color:${c.text};white-space:pre-wrap;max-height:320px;overflow-y:auto">${escHtml(op.text || cl.syllabus || 'No full opinion text available on this record.')}</div>
    `;

    bodyEl.innerHTML = bodyHtml;
    if (footerEl) {
      footerEl.innerHTML = `
        <button onclick="SLIntelligence.saveCurrentDossier()" class="btn-primary" style="font-size:12px;padding:7px 14px">📥 Save to Corpus</button>
        <button class="btn-cancel" onclick="document.getElementById('intCaseDossierModal').classList.remove('show')">Close</button>
      `;
    }
  }

  async function saveCourtCase(liveIdx) {
    const record = _liveSearchResults[liveIdx];
    if (!record) return;
    const btn = $('clSaveBtn-' + liveIdx);
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Saving...'; }
    const res = await apiPost('/api/court-intel/save-case', record);
    if (res && res.success) {
      showToast('Case saved to local court intelligence corpus', 'success');
      if (btn) btn.textContent = '✅ Saved';
    } else {
      showToast('Save failed: ' + ((res && res.error) || 'Unknown error'), 'error');
      if (btn) { btn.disabled = false; btn.textContent = '📥 Save to Corpus'; }
    }
  }

  async function saveCurrentDossier() {
    if (!_lastDossier) return;
    const d = _lastDossier;
    const payload = {
      source_id: d.source_id || String(d.cluster_id || ''),
      cluster_id: d.cluster_id,
      docket_id: (d.docket || {}).id || '',
      court_id: (d.court || {}).id || '',
      court_name: (d.court || {}).name || '',
      state: (d.court || {}).state || '',
      case_name: d.case_name,
      docket_number: (d.docket || {}).docket_number || '',
      date_filed: (d.docket || {}).date_filed || (d.cluster || {}).date_filed || '',
      disposition: d.disposition,
      snippet: ((d.opinion || {}).text || '').slice(0, 500),
      judges: (d.cluster || {}).judges || (d.docket || {}).assigned_to_str || '',
      assigned_to_str: (d.docket || {}).assigned_to_str || '',
      citation: (d.cluster || {}).citations || [],
      citation_count: (d.cluster || {}).citation_count || 0,
      absolute_url: d.absolute_url || '',
    };
    const res = await apiPost('/api/court-intel/save-case', payload);
    if (res && res.success) {
      showToast('Dossier saved to local corpus', 'success');
      _lastDossier.saved_in_corpus = true;
    } else {
      showToast('Save failed: ' + ((res && res.error) || 'Unknown error'), 'error');
    }
  }

  async function triggerIngestion(states) {
    const panel = $('intCourtIntelPanel');
    const stateLabel = states ? states.join(', ') : 'all 15 covered states';
    if (panel) panel.innerHTML = `<div style="padding:24px;text-align:center;color:var(--muted)">
      <span class="spinner-sm"></span> Ingesting bail-relevant court opinions from ${escHtml(stateLabel)}...<br>
      <small style="opacity:0.6;margin-top:8px;display:block">This may take 15-30 seconds</small>
    </div>`;
    try {
      const body = { days_back: 180 };
      if (states) body.states = states;
      const data = await apiPost('/api/court-intel/ingest', body);
      if (data && data.success) {
        showToast(`Ingested ${data.ingested} opinions (${data.duplicates} dupes) from ${data.total_fetched || 0} fetched`, 'success');
      } else {
        showToast('Ingestion failed: ' + ((data && data.error) || 'Unknown'), 'error');
      }
      await loadCourtIntel();
    } catch (e) {
      showToast('Ingestion error: ' + e.message, 'error');
      await loadCourtIntel();
    }
  }

  // ── Public API ──────────────────────────────────────────────────────────
  window.SLIntelligence = {
    load, trainModel, bootstrapFTA, showFeatures, showROC, setHorizon,
    loadCourtIntel, triggerIngestion, switchCourtTab, filterCourtState,
    setCourtScorecardMode, onExplorerStateChange, exploreDisposition,
    exploreJudge, applyCourtPreset, runCourtLiveSearch, runDocketLookup,
    openCaseDossier, openCaseDossierFromInput, saveCourtCase, saveCurrentDossier,
  };
})();
