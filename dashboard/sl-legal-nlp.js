/**
 * sl-legal-nlp.js — ShamrockLeads Legal NLP Intelligence Panel
 * ==============================================================
 * Premium "Fortune 50" dashboard for charge analysis, FTA risk
 * scoring, statute tracking, and NLP enrichment pipeline health.
 *
 * Dependencies: sl-core.js (ShamrockAPI, showToast)
 * API: /api/legal-nlp/stats, /api/legal-nlp/analyze-charges
 */

window.SLLegalNLP = (function () {
  "use strict";

  const API = window.ShamrockAPI || { fetchJSON: (u, o) => fetch(u, o).then(r => r.json()) };

  // ── Severity Color Map ───────────────────────────────────────────────
  const SEV_COLORS = {
    capital:        { bg: "#dc262622", color: "#dc2626", icon: "💀", label: "Capital" },
    felony_1:       { bg: "#ef444422", color: "#ef4444", icon: "🔴", label: "Felony 1" },
    felony_2:       { bg: "#f9731622", color: "#f97316", icon: "🟠", label: "Felony 2" },
    felony_3:       { bg: "#eab30822", color: "#eab308", icon: "🟡", label: "Felony 3" },
    misdemeanor_1:  { bg: "#3b82f622", color: "#3b82f6", icon: "🔵", label: "Misd. 1" },
    misdemeanor_2:  { bg: "#6366f122", color: "#6366f1", icon: "🟣", label: "Misd. 2" },
    varies:         { bg: "#6b728022", color: "#6b7280", icon: "⚪", label: "Varies" },
    unknown:        { bg: "#37415122", color: "#374151", icon: "❓", label: "Unknown" },
  };

  // ── State ────────────────────────────────────────────────────────────
  let _stats = null;
  let _loading = false;

  // ── Main render ──────────────────────────────────────────────────────
  function renderPanel(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;

    el.innerHTML = `
      <div class="lnlp-root" style="display:flex;flex-direction:column;gap:24px">
        <!-- Header -->
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
          <div style="display:flex;align-items:center;gap:12px">
            <div style="width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#6366f1,#8b5cf6);
              display:flex;align-items:center;justify-content:center;font-size:22px;box-shadow:0 4px 12px #6366f133">🧬</div>
            <div>
              <h3 style="margin:0;font-size:20px;font-weight:800;color:var(--text-primary,#f1f5f9);
                letter-spacing:-0.3px">Legal NLP Intelligence</h3>
              <span style="font-size:12px;color:var(--text-secondary,#94a3b8)">
                Charge analysis · FTA risk · Statute tracking · Pipeline health
              </span>
            </div>
          </div>
          <div style="display:flex;gap:8px">
            <button id="lnlp-refresh-btn" onclick="SLLegalNLP.load()"
              style="padding:8px 16px;border-radius:8px;border:1px solid var(--border,#334155);
              background:linear-gradient(135deg,#1e293b,#0f172a);color:#f1f5f9;font-size:13px;
              font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px;transition:all .2s">
              ↻ Refresh
            </button>
          </div>
        </div>

        <!-- KPI Ring Cards -->
        <div id="lnlp-kpi-row" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px">
          ${_kpiPlaceholders()}
        </div>

        <!-- Two-column grid -->
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
          <!-- Severity Distribution -->
          <div class="lnlp-panel" id="lnlp-severity-panel">
            <div class="lnlp-panel-title">⚖️ Severity Distribution</div>
            <div id="lnlp-severity-bars" style="padding:12px 0">
              <div style="text-align:center;padding:30px;color:#64748b"><span class="spinner-sm"></span></div>
            </div>
          </div>

          <!-- Top Statutes -->
          <div class="lnlp-panel" id="lnlp-statutes-panel">
            <div class="lnlp-panel-title">📜 Top Florida Statutes</div>
            <div id="lnlp-statute-list" style="padding:12px 0">
              <div style="text-align:center;padding:30px;color:#64748b"><span class="spinner-sm"></span></div>
            </div>
          </div>
        </div>

        <!-- FTA Risk Leaderboard -->
        <div class="lnlp-panel">
          <div class="lnlp-panel-title">🔥 Highest FTA Risk Defendants <span class="lnlp-badge-accent">Top 10</span></div>
          <div id="lnlp-fta-leaderboard" style="padding:8px 0">
            <div style="text-align:center;padding:30px;color:#64748b"><span class="spinner-sm"></span></div>
          </div>
        </div>

        <!-- Two-column: Risk Factors + County Coverage -->
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
          <!-- Risk Factor Frequency -->
          <div class="lnlp-panel">
            <div class="lnlp-panel-title">🎯 Risk Factor Frequency</div>
            <div id="lnlp-risk-factors" style="padding:12px 0">
              <div style="text-align:center;padding:30px;color:#64748b"><span class="spinner-sm"></span></div>
            </div>
          </div>

          <!-- County Enrichment Coverage -->
          <div class="lnlp-panel">
            <div class="lnlp-panel-title">📍 Enrichment Coverage by County</div>
            <div id="lnlp-county-coverage" style="padding:12px 0;max-height:380px;overflow-y:auto">
              <div style="text-align:center;padding:30px;color:#64748b"><span class="spinner-sm"></span></div>
            </div>
          </div>
        </div>
      </div>
    `;

    load();
  }

  // ── Load data ────────────────────────────────────────────────────────
  async function load() {
    if (_loading) return;
    _loading = true;
    const btn = document.getElementById("lnlp-refresh-btn");
    if (btn) btn.style.opacity = ".5";

    try {
      const data = await API.fetchJSON("/api/legal-nlp/stats");
      if (!data.success) throw new Error(data.error || "API error");
      _stats = data;

      _renderKPIs(data);
      _renderSeverityBars(data.severity_distribution);
      _renderStatutes(data.top_statutes);
      _renderFTALeaderboard(data.high_fta_records);
      _renderRiskFactors(data.risk_factor_frequency);
      _renderCountyCoverage(data.county_coverage);
    } catch (e) {
      console.error("[LegalNLP] Load error:", e);
    } finally {
      _loading = false;
      if (btn) btn.style.opacity = "1";
    }
  }

  // ── KPI Cards ────────────────────────────────────────────────────────
  function _kpiPlaceholders() {
    return Array(5).fill(`<div style="padding:20px;border-radius:12px;background:var(--card,#1e293b);
      border:1px solid var(--border,#334155);min-height:90px">
      <div style="text-align:center;color:#64748b;padding:16px"><span class="spinner-sm"></span></div>
    </div>`).join("");
  }

  function _renderKPIs(d) {
    const el = document.getElementById("lnlp-kpi-row");
    if (!el) return;

    const pct = d.coverage_pct || 0;
    const ringColor = pct >= 80 ? "#10b981" : pct >= 50 ? "#f59e0b" : "#ef4444";

    el.innerHTML = [
      _kpiRingCard("Enriched", d.enriched_count, d.total_arrests, ringColor, "🧬"),
      _kpiSimple("Avg Severity", d.avg_severity_level?.toFixed(1) || "0", "/10", "#8b5cf6", "📊"),
      _kpiSimple("FTA Flagged", d.high_fta_records?.length || 0, "records", "#ef4444", "🔥"),
      _kpiSimple("Statutes Found", d.top_statutes?.length || 0, "unique", "#3b82f6", "📜"),
      _kpiSimple("Risk Factors", d.risk_factor_frequency?.length || 0, "types", "#f59e0b", "🎯"),
    ].join("");
  }

  function _kpiRingCard(label, value, total, color, icon) {
    const pct = total > 0 ? Math.round(value / total * 100) : 0;
    const circ = 2 * Math.PI * 28; // r=28
    const offset = circ - (pct / 100) * circ;
    return `<div style="padding:16px;border-radius:12px;background:var(--card,#1e293b);
      border:1px solid var(--border,#334155);display:flex;align-items:center;gap:14px">
      <div style="position:relative;width:64px;height:64px;flex-shrink:0">
        <svg width="64" height="64" viewBox="0 0 64 64">
          <circle cx="32" cy="32" r="28" fill="none" stroke="#1e293b" stroke-width="4"/>
          <circle cx="32" cy="32" r="28" fill="none" stroke="${color}" stroke-width="4"
            stroke-dasharray="${circ}" stroke-dashoffset="${offset}"
            stroke-linecap="round" transform="rotate(-90 32 32)"
            style="transition:stroke-dashoffset 1.2s cubic-bezier(.4,0,.2,1)"/>
        </svg>
        <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
          font-size:14px;font-weight:800;color:${color}">${pct}%</div>
      </div>
      <div>
        <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px">${icon} ${label}</div>
        <div style="font-size:20px;font-weight:800;color:var(--text-primary,#e2e8f0)">${_fmt(value)}</div>
        <div style="font-size:11px;color:#475569">of ${_fmt(total)}</div>
      </div>
    </div>`;
  }

  function _kpiSimple(label, value, sub, color, icon) {
    return `<div style="padding:16px;border-radius:12px;background:var(--card,#1e293b);
      border:1px solid var(--border,#334155)">
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
        <span style="font-size:16px">${icon}</span>
        <span style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px">${label}</span>
      </div>
      <div style="font-size:24px;font-weight:800;color:${color}">${value}</div>
      <div style="font-size:11px;color:#475569;margin-top:2px">${sub}</div>
    </div>`;
  }

  // ── Severity Bars ────────────────────────────────────────────────────
  function _renderSeverityBars(dist) {
    const el = document.getElementById("lnlp-severity-bars");
    if (!el || !dist) return;

    const entries = Object.entries(dist).sort((a, b) => b[1] - a[1]);
    if (entries.length === 0) {
      el.innerHTML = `<div style="text-align:center;padding:30px;color:#64748b">No enriched data yet</div>`;
      return;
    }
    const max = Math.max(...entries.map(e => e[1]));

    el.innerHTML = entries.map(([sev, count]) => {
      const cfg = SEV_COLORS[sev] || SEV_COLORS.unknown;
      const pct = max > 0 ? (count / max * 100) : 0;
      return `<div style="display:flex;align-items:center;gap:10px;padding:6px 0">
        <span style="min-width:80px;font-size:12px;color:${cfg.color};font-weight:600;text-align:right">
          ${cfg.icon} ${cfg.label}
        </span>
        <div style="flex:1;height:22px;background:#0f172a;border-radius:6px;overflow:hidden;position:relative">
          <div style="height:100%;width:${pct}%;background:linear-gradient(90deg,${cfg.color}88,${cfg.color});
            border-radius:6px;transition:width .8s cubic-bezier(.4,0,.2,1)"></div>
        </div>
        <span style="min-width:40px;font-size:13px;font-weight:700;color:var(--text-primary,#e2e8f0);text-align:right">${_fmt(count)}</span>
      </div>`;
    }).join("");
  }

  // ── Top Statutes ─────────────────────────────────────────────────────
  function _renderStatutes(statutes) {
    const el = document.getElementById("lnlp-statute-list");
    if (!el) return;

    if (!statutes || statutes.length === 0) {
      el.innerHTML = `<div style="text-align:center;padding:30px;color:#64748b">No statutes extracted yet</div>`;
      return;
    }

    el.innerHTML = `<div style="display:flex;flex-direction:column;gap:6px">
      ${statutes.slice(0, 10).map((s, i) => {
        const opacity = 1 - (i * 0.06);
        return `<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-radius:8px;
          background:rgba(99,102,241,${0.05 + i * 0.01});opacity:${opacity}">
          <span style="font-size:13px;font-weight:800;color:#6366f1;min-width:24px">#${i + 1}</span>
          <span style="font-size:13px;font-weight:600;color:var(--text-primary,#e2e8f0);flex:1;
            font-family:'SF Mono',Monaco,monospace">${_escHtml(s.statute || "—")}</span>
          <span style="font-size:12px;font-weight:700;color:#94a3b8;background:#1e293b;
            padding:2px 8px;border-radius:4px">${s.count}</span>
        </div>`;
      }).join("")}
    </div>`;
  }

  // ── FTA Leaderboard ──────────────────────────────────────────────────
  function _renderFTALeaderboard(records) {
    const el = document.getElementById("lnlp-fta-leaderboard");
    if (!el) return;

    if (!records || records.length === 0) {
      el.innerHTML = `<div style="text-align:center;padding:30px;color:#64748b">
        No high-FTA records detected
      </div>`;
      return;
    }

    el.innerHTML = `<div style="overflow-x:auto">
      <table style="width:100%;border-collapse:separate;border-spacing:0 4px;font-size:13px">
        <thead>
          <tr style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.5px">
            <th style="text-align:left;padding:8px 12px">Defendant</th>
            <th style="text-align:left;padding:8px">County</th>
            <th style="text-align:left;padding:8px">Charges</th>
            <th style="text-align:center;padding:8px">Severity</th>
            <th style="text-align:center;padding:8px">FTA Risk</th>
          </tr>
        </thead>
        <tbody>
          ${records.map(r => {
            const fta = (r.nlp_fta_risk * 100).toFixed(0);
            const ftaColor = fta >= 25 ? "#ef4444" : fta >= 15 ? "#f59e0b" : "#3b82f6";
            const sev = SEV_COLORS[r.nlp_severity] || SEV_COLORS.unknown;
            const charges = (r.charges || "").substring(0, 50);
            return `<tr style="background:var(--card,#1e293b);border-radius:8px">
              <td style="padding:10px 12px;border-radius:8px 0 0 8px;font-weight:600;color:var(--text-primary,#e2e8f0)">
                ${_escHtml(r.full_name || "Unknown")}
                <div style="font-size:11px;color:#475569">${_escHtml(r.booking_number || "")}</div>
              </td>
              <td style="padding:10px 8px;color:#94a3b8">${_escHtml(r.county || "")}</td>
              <td style="padding:10px 8px;color:#94a3b8;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                  title="${_escHtml(r.charges || "")}">${_escHtml(charges)}</td>
              <td style="padding:10px 8px;text-align:center">
                <span style="padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;
                  background:${sev.bg};color:${sev.color}">${sev.icon} ${sev.label}</span>
              </td>
              <td style="padding:10px 8px;text-align:center;border-radius:0 8px 8px 0">
                <span style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;
                  border-radius:6px;font-size:12px;font-weight:800;letter-spacing:.3px;
                  background:${ftaColor}18;color:${ftaColor};border:1px solid ${ftaColor}33">
                  ${fta}%
                </span>
              </td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>
    </div>`;
  }

  // ── Risk Factors ─────────────────────────────────────────────────────
  function _renderRiskFactors(factors) {
    const el = document.getElementById("lnlp-risk-factors");
    if (!el) return;

    if (!factors || factors.length === 0) {
      el.innerHTML = `<div style="text-align:center;padding:30px;color:#64748b">No risk factors detected</div>`;
      return;
    }

    const max = Math.max(...factors.map(f => f.count));
    const colors = ["#ef4444", "#f97316", "#eab308", "#10b981", "#3b82f6", "#6366f1", "#8b5cf6", "#ec4899", "#14b8a6", "#f43f5e"];

    el.innerHTML = factors.map((f, i) => {
      const pct = max > 0 ? (f.count / max * 100) : 0;
      const clr = colors[i % colors.length];
      const label = f.factor.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
      return `<div style="display:flex;align-items:center;gap:8px;padding:5px 0">
        <span style="min-width:140px;font-size:12px;color:#cbd5e1;text-align:right;white-space:nowrap;
          overflow:hidden;text-overflow:ellipsis" title="${_escHtml(f.factor)}">${_escHtml(label)}</span>
        <div style="flex:1;height:18px;background:#0f172a;border-radius:4px;overflow:hidden">
          <div style="height:100%;width:${pct}%;background:${clr};border-radius:4px;
            transition:width .6s cubic-bezier(.4,0,.2,1)"></div>
        </div>
        <span style="min-width:30px;font-size:12px;font-weight:700;color:#e2e8f0;text-align:right">${f.count}</span>
      </div>`;
    }).join("");
  }

  // ── County Coverage ──────────────────────────────────────────────────
  function _renderCountyCoverage(counties) {
    const el = document.getElementById("lnlp-county-coverage");
    if (!el) return;

    if (!counties || counties.length === 0) {
      el.innerHTML = `<div style="text-align:center;padding:30px;color:#64748b">No county data</div>`;
      return;
    }

    el.innerHTML = `<div style="display:flex;flex-direction:column;gap:6px">
      ${counties.map(c => {
        const pct = c.coverage_pct;
        const barColor = pct >= 80 ? "#10b981" : pct >= 50 ? "#f59e0b" : "#ef4444";
        return `<div style="display:flex;align-items:center;gap:8px;padding:4px 0">
          <span style="min-width:110px;font-size:12px;color:#cbd5e1;font-weight:600;text-align:right">
            ${_escHtml(c.county || "Unknown")}
          </span>
          <div style="flex:1;height:14px;background:#0f172a;border-radius:3px;overflow:hidden">
            <div style="height:100%;width:${pct}%;background:${barColor};border-radius:3px;
              transition:width .5s"></div>
          </div>
          <span style="min-width:46px;font-size:11px;font-weight:700;color:${barColor};text-align:right">
            ${pct}%
          </span>
          <span style="min-width:50px;font-size:11px;color:#475569;text-align:right">
            ${c.enriched}/${c.total}
          </span>
        </div>`;
      }).join("")}
    </div>`;
  }

  // ── Helpers ──────────────────────────────────────────────────────────
  function _fmt(n) {
    if (n == null) return "0";
    return Number(n).toLocaleString();
  }

  function _escHtml(s) {
    if (!s) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // ── Init ─────────────────────────────────────────────────────────────
  function init(containerId) {
    renderPanel(containerId || "legalNlpContainer");
  }

  // ── Public API ──────────────────────────────────────────────────────
  return { init, load, renderPanel };
})();
