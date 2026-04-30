/* ShamrockLeads — Utility Module
   Animated KPI counters, sparklines, lead intelligence panel, PWA install
   Namespace: window.SLUtils
*/
(function() {
  'use strict';

  // ── Animated Counter ────────────────────────────────────────────────────
  /**
   * Animate a DOM element's text content from its current value to `target`.
   * Supports currency ($), percentage (%), and plain numbers.
   */
  function animateCounter(el, target, options = {}) {
    if (!el) return;
    const {
      duration = 800,
      prefix = '',
      suffix = '',
      decimals = 0,
      currency = false,
    } = options;

    const start = parseFloat(el.dataset.value || '0') || 0;
    const end = parseFloat(target) || 0;
    const startTime = performance.now();

    el.dataset.value = end;
    el.classList.add('kpi-counting');
    setTimeout(() => el.classList.remove('kpi-counting'), duration + 100);

    function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

    function step(now) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const current = start + (end - start) * easeOutCubic(progress);

      let display;
      if (currency) {
        if (current >= 1000000) display = '$' + (current / 1000000).toFixed(2) + 'M';
        else if (current >= 1000) display = '$' + (current / 1000).toFixed(1) + 'K';
        else display = '$' + current.toFixed(decimals);
      } else {
        display = prefix + current.toFixed(decimals) + suffix;
      }

      el.textContent = display;
      if (progress < 1) requestAnimationFrame(step);
    }

    requestAnimationFrame(step);
  }

  /**
   * Animate all KPI value elements that have a `data-target` attribute.
   */
  function animateAllKpis() {
    document.querySelectorAll('[data-target]').forEach(el => {
      const target = el.dataset.target;
      const currency = el.dataset.currency === 'true';
      const decimals = parseInt(el.dataset.decimals || '0');
      animateCounter(el, target, { currency, decimals });
    });
  }

  // ── Sparkline (SVG inline) ──────────────────────────────────────────────
  /**
   * Render a tiny SVG sparkline into `containerEl`.
   * @param {HTMLElement} containerEl
   * @param {number[]} data - Array of values
   * @param {object} opts - { width, height, color, fill }
   */
  function renderSparkline(containerEl, data, opts = {}) {
    if (!containerEl || !data || data.length < 2) return;
    const {
      width = 80,
      height = 28,
      color = '#10b981',
      fill = true,
    } = opts;

    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const step = width / (data.length - 1);

    const points = data.map((v, i) => {
      const x = i * step;
      const y = height - ((v - min) / range) * (height - 4) - 2;
      return `${x},${y}`;
    });

    const polyline = points.join(' ');
    const lastPoint = points[points.length - 1];
    const [lastX] = lastPoint.split(',');

    let svg = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" class="sparkline">`;

    if (fill) {
      svg += `<polygon points="${polyline} ${lastX},${height} 0,${height}"
        fill="${color}" fill-opacity="0.12"/>`;
    }

    svg += `<polyline points="${polyline}"
      fill="none" stroke="${color}" stroke-width="1.5"
      stroke-linecap="round" stroke-linejoin="round"/>`;

    // Last point dot
    const [dotX, dotY] = lastPoint.split(',');
    svg += `<circle cx="${dotX}" cy="${dotY}" r="2.5" fill="${color}"/>`;

    svg += '</svg>';
    containerEl.innerHTML = svg;
  }

  // ── Trend Arrow ─────────────────────────────────────────────────────────
  /**
   * Append a trend indicator to a KPI card's `.stat-sub` element.
   * @param {string} cardId - ID of the stat-card element
   * @param {object} trend - { direction: 'up'|'down'|'flat', pct: number }
   */
  function applyTrendArrow(cardId, trend) {
    const card = document.getElementById(cardId);
    if (!card || !trend) return;

    // Remove existing trend badge
    const existing = card.querySelector('.kpi-trend');
    if (existing) existing.remove();

    const badge = document.createElement('div');
    badge.className = `kpi-trend ${trend.direction}`;
    badge.textContent = `${trend.pct}% vs prior 7d`;
    card.appendChild(badge);
  }

  // ── Lead Intelligence Panel ─────────────────────────────────────────────
  async function showLeadIntelligence(bookingNumber, anchorEl) {
    // Find or create the panel
    let panel = document.getElementById('leadIntelPanel');
    if (!panel) {
      panel = document.createElement('div');
      panel.id = 'leadIntelPanel';
      panel.className = 'intel-panel';
      panel.style.cssText = 'display:none;position:fixed;top:80px;right:24px;width:420px;max-height:80vh;overflow-y:auto;z-index:150;box-shadow:0 20px 60px rgba(0,0,0,.5)';
      document.body.appendChild(panel);
    }

    panel.innerHTML = '<div style="text-align:center;padding:32px;color:var(--muted)">⏳ Loading intelligence...</div>';
    panel.style.display = 'block';

    try {
      const r = await fetch(`/api/leads/${encodeURIComponent(bookingNumber)}/intelligence`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const ct = r.headers.get('content-type') || '';
      if (!ct.includes('application/json')) throw new Error('Non-JSON response');
      const res = await r.json();
      if (!res.success) throw new Error(res.error || 'Failed');

      const { score_explanation, classified_charges, similar_cases, optimal_contact } = res;
      const { factors, total_score, status, summary } = score_explanation;

      const statusClass = status === 'Hot' ? 'hot' : status === 'Warm' ? 'warm' : status === 'Disqualified' ? 'disqualified' : 'cold';

      let html = `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:16px 20px;border-bottom:1px solid var(--border)">
          <div>
            <div style="font-size:16px;font-weight:800;letter-spacing:-.3px">🧠 Lead Intelligence</div>
            <div style="font-size:11px;color:var(--muted);margin-top:2px">${bookingNumber}</div>
          </div>
          <button onclick="document.getElementById('leadIntelPanel').style.display='none'"
            style="background:none;border:none;color:var(--muted);font-size:18px;cursor:pointer;padding:4px 8px;border-radius:6px">✕</button>
        </div>
        <div style="padding:16px 20px">
          <!-- Score Ring + Summary -->
          <div style="display:flex;align-items:center;gap:16px;margin-bottom:20px">
            <div class="intel-score-ring ${statusClass}">${total_score}</div>
            <div>
              <div style="font-size:15px;font-weight:700">${status} Lead</div>
              <div style="font-size:12px;color:var(--muted);margin-top:2px">${summary}</div>
            </div>
          </div>

          <!-- Score Factors -->
          <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:8px">Score Breakdown</div>
          ${factors.map(f => `
            <div class="intel-factor">
              <span class="intel-factor-icon">${f.icon}</span>
              <div style="flex:1">
                <div class="intel-factor-name">${f.factor}</div>
                <div class="intel-factor-reason">${f.reason}</div>
              </div>
              <span class="intel-factor-pts ${f.points >= 0 ? 'pos' : 'neg'}">${f.points >= 0 ? '+' : ''}${f.points}</span>
            </div>
          `).join('')}

          <!-- Charge Severity -->
          ${classified_charges.length > 0 ? `
            <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin:16px 0 8px">Charge Severity</div>
            <div style="display:flex;flex-wrap:wrap;gap:6px">
              ${classified_charges.map(c => `
                <span class="charge-badge charge-${c.severity}" title="${c.charge}">${c.label} — ${c.charge.slice(0,30)}${c.charge.length>30?'…':''}</span>
              `).join('')}
            </div>
          ` : ''}

          <!-- Similar Cases -->
          <div style="margin-top:16px;padding:12px;background:var(--surface);border-radius:8px;border:1px solid var(--border)">
            <div style="font-size:11px;font-weight:700;color:var(--accent);margin-bottom:6px">📊 Market Context</div>
            <div style="font-size:12px;color:var(--text-secondary)">${similar_cases.insight}</div>
          </div>

          <!-- Optimal Contact -->
          <div style="margin-top:12px;padding:12px;background:var(--surface);border-radius:8px;border:1px solid var(--border)">
            <div style="font-size:11px;font-weight:700;color:var(--gold);margin-bottom:6px">⏰ Optimal Contact Times</div>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              ${optimal_contact.times.map(t => `<span style="background:rgba(245,158,11,.1);color:var(--gold);padding:3px 10px;border-radius:6px;font-size:12px;font-weight:600">${t}</span>`).join('')}
            </div>
            <div style="font-size:11px;color:var(--muted);margin-top:6px">${optimal_contact.insight}</div>
          </div>
        </div>
      `;

      panel.innerHTML = html;
    } catch (err) {
      panel.innerHTML = `<div style="padding:20px;color:var(--red)">❌ Failed to load intelligence: ${err.message}</div>`;
    }
  }

  // ── PWA Install Banner ──────────────────────────────────────────────────
  let _deferredInstallPrompt = null;

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    _deferredInstallPrompt = e;
    // Show banner after 5 seconds
    setTimeout(() => {
      const banner = document.getElementById('pwaInstallBanner');
      if (banner) banner.classList.add('show');
    }, 5000);
  });

  function installPWA() {
    if (!_deferredInstallPrompt) return;
    _deferredInstallPrompt.prompt();
    _deferredInstallPrompt.userChoice.then(() => {
      _deferredInstallPrompt = null;
      const banner = document.getElementById('pwaInstallBanner');
      if (banner) banner.classList.remove('show');
    });
  }

  function dismissPWA() {
    const banner = document.getElementById('pwaInstallBanner');
    if (banner) banner.classList.remove('show');
    _deferredInstallPrompt = null;
  }

  // ── Trend Stats Loader ──────────────────────────────────────────────────
  async function loadTrendStats() {
    try {
      const r = await fetch('/api/leads/trend-stats');
      if (!r.ok) return;
      const ct = r.headers.get('content-type') || '';
      if (!ct.includes('application/json')) return;
      const res = await r.json();
      if (!res.success) return;
      const { trends } = res;

      // Apply trend arrows to KPI cards
      const mapping = {
        'kpiHot': trends.hot_leads,
        'kpiTotal': trends.leads,
        'kpiBonds': trends.bonds_written,
        'anlKpiCollected7': trends.revenue,
      };
      Object.entries(mapping).forEach(([id, trend]) => {
        if (trend) applyTrendArrow(id, trend);
      });
    } catch(e) {
      // Silently fail — trend stats are enhancement only
    }
  }

  // ── Public API ──────────────────────────────────────────────────────────
  window.SLUtils = {
    animateCounter,
    animateAllKpis,
    renderSparkline,
    applyTrendArrow,
    showLeadIntelligence,
    loadTrendStats,
    installPWA,
    dismissPWA,
  };

  // Auto-load trend stats on page load
  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(loadTrendStats, 2000);
  });

})();
