/* ShamrockLeads — Alpha Intelligence Module
   The "Bloomberg Terminal" for lead source performance.
   Namespace: window.SLAlphaIntel
   API Base: /api/alpha/*
*/
(function() {
  'use strict';

  let _loaded = false;
  let _leaderboard = [];
  let _stats = {};
  let _tiers = {};
  let _recs = [];
  let _selectedCounty = null;
  let _sortCol = 'score';
  let _sortDir = -1;
  let _charts = {};

  // ── Helpers ──────────────────────────────────────────────────────────
  function $(id) { return document.getElementById(id); }
  function fmtScore(n) { return (n || 0).toFixed(1); }
  function fmtPct(n) { return ((n || 0) * 100).toFixed(1) + '%'; }
  function fmtMoney(n) {
    if (!n) return '$0';
    if (n >= 1_000_000) return '$' + (n / 1_000_000).toFixed(2) + 'M';
    if (n >= 1_000) return '$' + (n / 1_000).toFixed(1) + 'K';
    return '$' + Math.round(n).toLocaleString();
  }
  function fmtNum(n) { return (n || 0).toLocaleString(); }
  function ago(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    const diff = Date.now() - d.getTime();
    if (diff < 60000) return 'just now';
    if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
    if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
    return Math.floor(diff / 86400000) + 'd ago';
  }
  function tierColor(t) {
    return { alpha: '#10b981', growth: '#3b82f6', prospect: '#f59e0b', dormant: '#64748b' }[t] || '#64748b';
  }
  function tierEmoji(t) {
    return { alpha: '🟢', growth: '🔵', prospect: '🟡', dormant: '⚫' }[t] || '⚫';
  }
  function tierLabel(t) {
    return { alpha: 'ALPHA', growth: 'GROWTH', prospect: 'PROSPECT', dormant: 'DORMANT' }[t] || t?.toUpperCase();
  }
  function trendArrow(v) {
    if (!v) return '<span style="color:var(--muted)">—</span>';
    if (v > 0) return `<span style="color:#10b981">▲ +${v.toFixed(1)}</span>`;
    return `<span style="color:#ef4444">▼ ${v.toFixed(1)}</span>`;
  }

  function destroyChart(id) {
    if (_charts[id]) { _charts[id].destroy(); delete _charts[id]; }
  }

  // ── API Layer ────────────────────────────────────────────────────────
  async function api(path, method = 'GET', body = null) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch('/api' + path, opts);
    return r.json();
  }

  // ── Load All Data ────────────────────────────────────────────────────
  async function load() {
    const container = $('alphaIntelContent');
    if (!container) return;

    // Show skeleton while loading
    if (!_loaded) {
      container.querySelectorAll('.ai-kpi-value').forEach(el => { el.textContent = '…'; });
    }

    try {
      const [lbRes, tierRes, recRes] = await Promise.all([
        api('/alpha/leaderboard?limit=67'),
        api('/alpha/tiers'),
        api('/alpha/recommendations?limit=15'),
      ]);

      if (lbRes.success) {
        _leaderboard = lbRes.leaderboard || [];
        _stats = lbRes.system_stats || {};
      }
      if (tierRes.success) _tiers = tierRes.tiers || {};
      if (recRes.success) _recs = recRes.recommendations || [];

      renderKPIs();
      renderTierCards();
      renderLeaderboard();
      renderRecommendations();
      _loaded = true;

      $('alphaLastCalc').textContent = lbRes.generated_at ? ago(lbRes.generated_at) : '—';
    } catch (err) {
      console.error('[AlphaIntel] Load error:', err);
      if ($('alphaStatus')) $('alphaStatus').textContent = 'Error loading data';
    }
  }

  // ── KPI Cards ────────────────────────────────────────────────────────
  function renderKPIs() {
    setText('aiKpiCounties', fmtNum(_stats.total_counties));
    setText('aiKpiAlpha', fmtNum(_stats.alpha_counties));
    setText('aiKpiGrowth', fmtNum(_stats.growth_counties));
    setText('aiKpiAvgScore', fmtScore(_stats.avg_score));
    setText('aiKpiConversions', fmtNum(_stats.total_conversions_90d));

    // Color-code avg score
    const avgEl = $('aiKpiAvgScore');
    if (avgEl) {
      const s = _stats.avg_score || 0;
      avgEl.style.color = s >= 75 ? '#10b981' : s >= 50 ? '#3b82f6' : s >= 25 ? '#f59e0b' : '#ef4444';
    }
  }

  function setText(id, val) {
    const el = $(id);
    if (el) el.textContent = val;
  }

  // ── Tier Cards ───────────────────────────────────────────────────────
  function renderTierCards() {
    const grid = $('alphaTierGrid');
    if (!grid) return;

    const tiers = ['alpha', 'growth', 'prospect', 'dormant'];
    grid.innerHTML = tiers.map(t => {
      const data = _tiers[t] || { count: 0, avg_score: 0 };
      const color = tierColor(t);
      const pct = _stats.total_counties ? Math.round((data.count / _stats.total_counties) * 100) : 0;
      return `
        <div class="ai-tier-card" style="--tier-color:${color}" onclick="SLAlphaIntel.filterByTier('${t}')">
          <div class="ai-tier-header">
            <span class="ai-tier-badge" style="background:${color}20;color:${color};border:1px solid ${color}40">
              ${tierEmoji(t)} ${tierLabel(t)}
            </span>
            <span class="ai-tier-pct">${pct}%</span>
          </div>
          <div class="ai-tier-count">${data.count}</div>
          <div class="ai-tier-sub">counties · avg ${fmtScore(data.avg_score)} pts</div>
          <div class="ai-tier-bar">
            <div class="ai-tier-bar-fill" style="width:${pct}%;background:${color}"></div>
          </div>
        </div>`;
    }).join('');
  }

  // ── Leaderboard Table ────────────────────────────────────────────────
  let _tierFilter = null;

  function renderLeaderboard() {
    const body = $('alphaLeaderboardBody');
    if (!body) return;

    let data = [..._leaderboard];

    // Apply tier filter
    if (_tierFilter) {
      data = data.filter(r => r.tier === _tierFilter);
    }

    // Sort
    data.sort((a, b) => {
      const av = a[_sortCol] ?? 0, bv = b[_sortCol] ?? 0;
      if (typeof av === 'string') return _sortDir * av.localeCompare(bv);
      return _sortDir * (bv - av);
    });

    // Update count
    const label = $('alphaLbCount');
    if (label) label.textContent = `${data.length} source${data.length !== 1 ? 's' : ''}`;

    if (!data.length) {
      body.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:40px;color:var(--muted)">
        <div style="font-size:2rem;margin-bottom:8px">📡</div>
        No source performance data yet. Run a scoring cycle to populate.
      </td></tr>`;
      return;
    }

    body.innerHTML = data.map((r, i) => {
      const rank = i + 1;
      const color = tierColor(r.tier);
      const isSelected = _selectedCounty === r.county;
      const medalIcon = rank === 1 ? '🥇' : rank === 2 ? '🥈' : rank === 3 ? '🥉' : `<span style="color:var(--muted)">${rank}</span>`;
      const scoreBarWidth = Math.min(r.score || 0, 100);

      return `
        <tr class="ai-lb-row ${isSelected ? 'ai-lb-selected' : ''}" onclick="SLAlphaIntel.selectCounty('${r.county}')">
          <td class="ai-lb-rank">${medalIcon}</td>
          <td class="ai-lb-county">
            <span class="ai-lb-county-name">${r.county}</span>
          </td>
          <td>
            <span class="ai-tier-pill" style="background:${color}18;color:${color};border:1px solid ${color}30">
              ${tierEmoji(r.tier)} ${tierLabel(r.tier)}
            </span>
          </td>
          <td class="ai-lb-score">
            <div class="ai-score-bar-wrap">
              <div class="ai-score-bar-fill" style="width:${scoreBarWidth}%;background:${color}"></div>
              <span class="ai-score-text">${fmtScore(r.score)}</span>
            </div>
          </td>
          <td>${trendArrow(r.trend_vs_prior)}</td>
          <td class="ai-lb-metric">${fmtNum(r.lead_volume_30d)}</td>
          <td class="ai-lb-metric">${fmtPct(r.hot_lead_ratio)}</td>
          <td class="ai-lb-metric">${fmtPct(r.conversion_rate)}</td>
          <td class="ai-lb-metric">${fmtMoney(r.total_premium_90d)}</td>
        </tr>`;
    }).join('');
  }

  // ── Recommendations Panel ────────────────────────────────────────────
  function renderRecommendations() {
    const container = $('alphaRecList');
    if (!container) return;

    if (!_recs.length) {
      container.innerHTML = '<div style="text-align:center;padding:24px;color:var(--muted)">No recommendations available yet.</div>';
      return;
    }

    container.innerHTML = _recs.map(r => {
      const color = tierColor(r.tier);
      return `
        <div class="ai-rec-item" onclick="SLAlphaIntel.selectCounty('${r.county}')">
          <div class="ai-rec-left">
            <span class="ai-rec-county" style="color:${color}">${r.county}</span>
            <span class="ai-rec-action">${r.action}</span>
          </div>
          <div class="ai-rec-right">
            <span class="ai-tier-pill" style="background:${color}18;color:${color};border:1px solid ${color}30;font-size:10px;padding:2px 8px">
              ${fmtScore(r.score)}
            </span>
          </div>
        </div>`;
    }).join('');
  }

  // ── County Deep-Dive ─────────────────────────────────────────────────
  async function selectCounty(county) {
    if (!county || _selectedCounty === county) {
      _selectedCounty = null;
      hideDeepDive();
      renderLeaderboard();
      return;
    }
    _selectedCounty = county;
    renderLeaderboard();

    const panel = $('alphaDeepDive');
    if (!panel) return;

    panel.style.display = 'block';
    panel.querySelector('.ai-dd-title').textContent = `📊 ${county} — Deep Dive`;
    panel.querySelector('.ai-dd-body').innerHTML = '<div style="text-align:center;padding:24px;color:var(--muted)">Loading…</div>';

    try {
      const [detailRes, trendRes] = await Promise.all([
        api(`/alpha/county/${encodeURIComponent(county)}`),
        api(`/alpha/trend/${encodeURIComponent(county)}`),
      ]);

      if (!detailRes.success) {
        panel.querySelector('.ai-dd-body').innerHTML = `<div style="color:#ef4444;padding:16px">Error: ${detailRes.error}</div>`;
        return;
      }

      const d = detailRes.county;
      const trend = trendRes.success ? trendRes : {};

      renderDeepDiveContent(d, trend);
    } catch (err) {
      panel.querySelector('.ai-dd-body').innerHTML = `<div style="color:#ef4444;padding:16px">Failed to load: ${err.message}</div>`;
    }
  }

  function renderDeepDiveContent(d, trend) {
    const body = $('alphaDeepDive')?.querySelector('.ai-dd-body');
    if (!body) return;

    const color = tierColor(d.tier);

    body.innerHTML = `
      <!-- Score Header -->
      <div class="ai-dd-header-row">
        <div class="ai-dd-score-ring" style="--ring-color:${color}">
          <svg viewBox="0 0 120 120">
            <circle cx="60" cy="60" r="52" fill="none" stroke="${color}15" stroke-width="8"/>
            <circle cx="60" cy="60" r="52" fill="none" stroke="${color}" stroke-width="8"
              stroke-dasharray="${(d.score / 100) * 327} 327"
              stroke-linecap="round" transform="rotate(-90 60 60)" style="transition:stroke-dasharray .8s ease"/>
          </svg>
          <div class="ai-dd-score-label">
            <div class="ai-dd-score-num">${fmtScore(d.score)}</div>
            <div class="ai-dd-score-tier" style="color:${color}">${tierLabel(d.tier)}</div>
          </div>
        </div>
        <div class="ai-dd-meta-grid">
          <div class="ai-dd-meta-item">
            <span class="ai-dd-meta-label">Trend</span>
            <span class="ai-dd-meta-value">${trendArrow(d.trend_vs_prior)}</span>
          </div>
          <div class="ai-dd-meta-item">
            <span class="ai-dd-meta-label">Recommended Freq</span>
            <span class="ai-dd-meta-value">${d.recommended_frequency_min || '—'}m</span>
          </div>
          <div class="ai-dd-meta-item">
            <span class="ai-dd-meta-label">Outreach Tier</span>
            <span class="ai-dd-meta-value" style="color:${color}">${(d.recommended_outreach_tier || '—').toUpperCase()}</span>
          </div>
          <div class="ai-dd-meta-item">
            <span class="ai-dd-meta-label">Last Scrape</span>
            <span class="ai-dd-meta-value">${ago(d.last_scrape_at)}</span>
          </div>
        </div>
      </div>

      <!-- Component Scores -->
      <div class="ai-dd-section-title">Component Scores</div>
      <div class="ai-dd-components">
        ${renderComponentBar('Lead Volume', d.score_lead_volume, '#10b981')}
        ${renderComponentBar('Hot Lead Ratio', d.score_hot_ratio, '#f59e0b')}
        ${renderComponentBar('Outreach Reply', d.score_outreach, '#8b5cf6')}
        ${renderComponentBar('Conversion Rate', d.score_conversion, '#3b82f6')}
        ${renderComponentBar('Revenue/Lead', d.score_revenue, '#ec4899')}
        ${renderComponentBar('Scraper Health', d.score_health, '#06b6d4')}
      </div>

      <!-- Raw Metrics -->
      <div class="ai-dd-section-title">Raw Metrics (30d / 90d)</div>
      <div class="ai-dd-metrics-grid">
        <div class="ai-dd-metric">
          <span class="ai-dd-metric-val">${fmtNum(d.lead_volume_30d)}</span>
          <span class="ai-dd-metric-lbl">Leads (30d)</span>
        </div>
        <div class="ai-dd-metric">
          <span class="ai-dd-metric-val">${fmtNum(d.hot_leads_30d)}</span>
          <span class="ai-dd-metric-lbl">Hot Leads</span>
        </div>
        <div class="ai-dd-metric">
          <span class="ai-dd-metric-val">${fmtPct(d.outreach_reply_rate)}</span>
          <span class="ai-dd-metric-lbl">Reply Rate</span>
        </div>
        <div class="ai-dd-metric">
          <span class="ai-dd-metric-val">${fmtNum(d.bonds_written_90d)}</span>
          <span class="ai-dd-metric-lbl">Bonds (90d)</span>
        </div>
        <div class="ai-dd-metric">
          <span class="ai-dd-metric-val">${fmtPct(d.conversion_rate)}</span>
          <span class="ai-dd-metric-lbl">Conversion</span>
        </div>
        <div class="ai-dd-metric">
          <span class="ai-dd-metric-val">${fmtMoney(d.total_premium_90d)}</span>
          <span class="ai-dd-metric-lbl">Revenue (90d)</span>
        </div>
        <div class="ai-dd-metric">
          <span class="ai-dd-metric-val">${fmtMoney(d.revenue_per_lead)}</span>
          <span class="ai-dd-metric-lbl">Rev/Lead</span>
        </div>
        <div class="ai-dd-metric">
          <span class="ai-dd-metric-val">${(d.scraper_uptime_pct || 0).toFixed(0)}%</span>
          <span class="ai-dd-metric-lbl">Uptime</span>
        </div>
      </div>

      <!-- Actions -->
      ${d.actions && d.actions.length ? `
        <div class="ai-dd-section-title">Recommended Actions</div>
        <div class="ai-dd-actions">
          ${d.actions.map(a => `<div class="ai-dd-action-item">💡 ${a}</div>`).join('')}
        </div>
      ` : ''}

      <!-- Recent Conversions -->
      ${trend.recent_conversions && trend.recent_conversions.length ? `
        <div class="ai-dd-section-title">Recent Conversions</div>
        <div class="ai-dd-conversions">
          ${trend.recent_conversions.slice(0, 8).map(c => `
            <div class="ai-dd-conv-item">
              <span class="ai-dd-conv-booking">${c.booking_number || '—'}</span>
              <span class="ai-dd-conv-amt">${fmtMoney(c.bond_amount)}</span>
              <span class="ai-dd-conv-prem">${fmtMoney(c.premium)} premium</span>
              <span class="ai-dd-conv-time">${ago(c.recorded_at)}</span>
            </div>
          `).join('')}
        </div>
      ` : ''}
    `;
  }

  function renderComponentBar(label, score, color) {
    const val = score || 0;
    return `
      <div class="ai-comp-row">
        <span class="ai-comp-label">${label}</span>
        <div class="ai-comp-bar">
          <div class="ai-comp-bar-fill" style="width:${val}%;background:${color}"></div>
        </div>
        <span class="ai-comp-score">${val.toFixed(0)}</span>
      </div>`;
  }

  function hideDeepDive() {
    const panel = $('alphaDeepDive');
    if (panel) panel.style.display = 'none';
  }

  // ── Sorting ──────────────────────────────────────────────────────────
  function sortBy(col) {
    if (_sortCol === col) { _sortDir *= -1; }
    else { _sortCol = col; _sortDir = -1; }
    renderLeaderboard();
  }

  // ── Tier Filter ──────────────────────────────────────────────────────
  function filterByTier(tier) {
    if (_tierFilter === tier) { _tierFilter = null; }
    else { _tierFilter = tier; }
    renderLeaderboard();

    // Visual feedback on tier cards
    document.querySelectorAll('.ai-tier-card').forEach(card => {
      card.classList.toggle('ai-tier-active', card.querySelector('.ai-tier-badge')?.textContent.trim().toLowerCase().includes(tier));
    });
  }

  // ── Recalculate ──────────────────────────────────────────────────────
  async function recalculate() {
    const btn = $('alphaRecalcBtn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Computing…'; }

    try {
      const r = await api('/alpha/recalculate', 'POST');
      if (r.success) {
        SL?.toast?.('Alpha Engine scoring cycle complete', 'success');
        await load();
      } else {
        SL?.toast?.('Recalculate failed: ' + (r.error || 'unknown'), 'error');
      }
    } catch (err) {
      SL?.toast?.('Recalculate failed: ' + err.message, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '⚡ Recalculate'; }
    }
  }

  // ── Public API ───────────────────────────────────────────────────────
  window.SLAlphaIntel = {
    load,
    sortBy,
    filterByTier,
    selectCounty,
    recalculate,
  };
})();
