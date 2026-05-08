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

  // ── Regional Court Intelligence Panel ───────────────────────────────────
  async function loadCourtIntel() {
    const panel = $('intCourtIntelPanel');
    if (!panel) return;

    try {
      const [coverageRes, statsRes] = await Promise.all([
        fetch('/api/court-intel/coverage').then(r => r.json()).catch(() => null),
        fetch('/api/court-intel/stats').then(r => r.json()).catch(() => null),
      ]);

      renderCourtIntel(panel, coverageRes, statsRes);
    } catch (e) {
      panel.innerHTML = `<div style="padding:16px;color:var(--muted);text-align:center">Court intelligence unavailable</div>`;
    }
  }

  function renderCourtIntel(panel, coverage, stats) {
    const c = C();
    let html = '';

    // ── Coverage Map (state chips) ──────────────────────────────────────
    if (coverage && coverage.total_states) {
      html += `<div style="margin-bottom:16px">
        <div style="font-size:12px;color:${c.muted};text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;font-weight:600">
          Coverage · ${coverage.total_courts} Courts · ${coverage.total_states} States
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:6px">`;

      const stateColors = {
        FL: '#10b981', GA: '#6366f1', AL: '#f59e0b', MS: '#ec4899',
        LA: '#8b5cf6', TN: '#3b82f6', KY: '#14b8a6', NC: '#f97316',
        SC: '#06b6d4', VA: '#a855f7', WV: '#64748b', AR: '#ef4444',
      };

      (coverage.by_state || []).forEach(s => {
        const color = stateColors[s.state] || c.accent;
        const total = s.state_count + s.federal_count;
        html += `<div style="background:${color}18;border:1px solid ${color}44;border-radius:6px;
          padding:6px 12px;font-size:12px;font-weight:600;color:${color};cursor:default"
          title="${s.state}: ${s.state_count} state + ${s.federal_count} federal courts">
          ${s.state} <span style="opacity:0.7;font-weight:400">${total}</span>
        </div>`;
      });
      html += '</div></div>';
    }

    // ── Stats Grid ──────────────────────────────────────────────────────
    if (stats && stats.success) {
      html += `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:16px">`;

      const kpis = [
        { label: 'Total Outcomes', value: (stats.total_outcomes || 0).toLocaleString(), icon: '📊', color: c.accent2 },
        { label: 'Matched Defendants', value: (stats.matched_defendants || 0).toLocaleString(), icon: '🔗', color: c.accent },
        { label: 'States Active', value: (stats.by_state || []).length, icon: '🗺️', color: '#f59e0b' },
        { label: 'Last Ingestion', value: stats.last_ingestion ? new Date(stats.last_ingestion).toLocaleDateString() : 'Never', icon: '🕐', color: c.muted },
      ];

      kpis.forEach(k => {
        html += `<div style="background:${c.panel};border:1px solid ${c.border};border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:18px;margin-bottom:4px">${k.icon}</div>
          <div style="font-size:18px;font-weight:700;color:${k.color}">${k.value}</div>
          <div style="font-size:11px;color:${c.muted};margin-top:2px">${k.label}</div>
        </div>`;
      });
      html += '</div>';

      // ── Disposition Breakdown ──────────────────────────────────────────
      if (stats.by_disposition && stats.by_disposition.length > 0) {
        html += `<div style="margin-bottom:12px">
          <div style="font-size:12px;color:${c.muted};text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;font-weight:600">Disposition Breakdown</div>`;

        const dispColors = {
          affirmed: '#10b981', conviction: '#ef4444', dismissed: '#3b82f6',
          plea: '#f59e0b', reversed: '#8b5cf6', remanded: '#ec4899',
          acquittal: '#14b8a6', vacated: '#64748b', denied: '#f97316', unknown: '#475569',
        };
        const totalDisp = stats.by_disposition.reduce((a, d) => a + d.count, 0);

        stats.by_disposition.slice(0, 8).forEach(d => {
          const pct = totalDisp > 0 ? ((d.count / totalDisp) * 100) : 0;
          const color = dispColors[d.disposition] || c.muted;
          html += `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
            <div style="width:90px;font-size:12px;color:${c.text};font-weight:500;text-transform:capitalize">${d.disposition}</div>
            <div style="flex:1;background:${c.border}44;border-radius:4px;height:18px;overflow:hidden">
              <div style="width:${pct}%;height:100%;background:${color};border-radius:4px;transition:width 0.6s ease"></div>
            </div>
            <div style="width:50px;text-align:right;font-size:11px;color:${c.muted}">${pct.toFixed(1)}%</div>
          </div>`;
        });
        html += '</div>';
      }

      // ── Ingest Button ─────────────────────────────────────────────────
      html += `<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px">
        <button onclick="SLIntelligence.triggerIngestion()" class="btn-secondary" style="font-size:12px;padding:6px 14px">
          🔄 Run Ingestion
        </button>
        <button onclick="SLIntelligence.triggerIngestion(['FL'])" class="btn-primary" style="font-size:12px;padding:6px 14px">
          🌴 Florida Only
        </button>
      </div>`;
    } else if (!stats || stats.total_outcomes === 0) {
      html += `<div style="padding:20px;text-align:center;color:${c.muted}">
        <div style="font-size:2rem;margin-bottom:8px">📡</div>
        <div style="font-weight:600;margin-bottom:4px">No court data ingested yet</div>
        <div style="font-size:12px;margin-bottom:12px">Click below to pull recent opinions from CourtListener</div>
        <button onclick="SLIntelligence.triggerIngestion()" class="btn-primary" style="font-size:13px;padding:8px 20px">
          🚀 Run First Ingestion
        </button>
      </div>`;
    }

    panel.innerHTML = html;
  }

  async function triggerIngestion(states) {
    const panel = $('intCourtIntelPanel');
    if (panel) panel.innerHTML = '<div style="padding:20px;text-align:center;color:var(--muted)"><span class="spinner-sm"></span> Ingesting court opinions...</div>';
    try {
      const body = { days_back: 30 };
      if (states) body.states = states;
      const res = await fetch('/api/court-intel/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (data.success) {
        showToast(`Ingested ${data.ingested} opinions (${data.duplicates} dupes)`, 'success');
      } else {
        showToast('Ingestion failed: ' + (data.error || 'Unknown'), 'error');
      }
      // Reload panel
      await loadCourtIntel();
    } catch (e) {
      showToast('Ingestion error: ' + e.message, 'error');
    }
  }

  // ── Public API ──────────────────────────────────────────────────────────
  window.SLIntelligence = { load, trainModel, showFeatures, setHorizon, loadCourtIntel, triggerIngestion };
})();
