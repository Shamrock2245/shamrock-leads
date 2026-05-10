/**
 * sl-docket-monitor.js — ShamrockLeads Docket Intelligence Panel
 * ================================================================
 * Real-time court docket surveillance UI for the Intelligence tab.
 * Shows live event feed, alert badges, risk timeline, and scan controls.
 *
 * Dependencies: sl-core.js (ShamrockAPI, showToast)
 */

window.SLDocketMonitor = (function () {
  "use strict";

  const API = window.ShamrockAPI || { fetchJSON: (u, o) => fetch(u, o).then(r => r.json()) };

  // ── Severity config ──────────────────────────────────────────────────
  const SEV = {
    critical: { icon: "🚨", color: "#ef4444", bg: "rgba(239,68,68,.12)", label: "CRITICAL" },
    high:     { icon: "⚠️",  color: "#f59e0b", bg: "rgba(245,158,11,.12)", label: "HIGH" },
    medium:   { icon: "📋", color: "#3b82f6", bg: "rgba(59,130,246,.10)", label: "MEDIUM" },
    low:      { icon: "ℹ️",  color: "#10b981", bg: "rgba(16,185,129,.10)", label: "LOW" },
    info:     { icon: "📎", color: "#6b7280", bg: "rgba(107,114,128,.08)", label: "INFO" },
  };

  const EVENT_LABELS = {
    fta_warrant: "FTA Warrant", bench_warrant: "Bench Warrant",
    motion_revoke_bond: "Revoke Bond", bond_forfeiture: "Bond Forfeiture",
    fugitive_status: "Fugitive", sentencing: "Sentencing",
    guilty_plea: "Guilty Plea", probation_violation: "VOP",
    bond_increased: "Bond ↑", new_warrant: "New Warrant",
    bond_reduced: "Bond ↓", continuance: "Continuance",
    motion_dismiss: "Motion to Dismiss", pretrial_hearing: "Pretrial",
    trial_scheduled: "Trial Set", case_dismissed: "Dismissed",
    acquittal: "Acquittal", attorney_event: "Attorney", discovery: "Discovery",
  };

  // ── State ────────────────────────────────────────────────────────────
  let _events = [];
  let _stats = {};
  let _scanning = false;

  // ── Render helpers ───────────────────────────────────────────────────
  function _severityBadge(sev) {
    const s = SEV[sev] || SEV.info;
    return `<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 10px;
      border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.5px;
      background:${s.bg};color:${s.color};border:1px solid ${s.color}22">
      ${s.icon} ${s.label}</span>`;
  }

  function _riskBadge(adj) {
    if (adj === 0) return "";
    const sign = adj > 0 ? "+" : "";
    const col = adj > 0 ? "#ef4444" : "#10b981";
    return `<span style="font-size:11px;font-weight:600;color:${col};
      padding:1px 6px;border-radius:4px;background:${col}11">
      ${sign}${(adj * 100).toFixed(0)}%</span>`;
  }

  function _timeAgo(iso) {
    if (!iso) return "—";
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  }

  // ── Panel HTML ───────────────────────────────────────────────────────
  function renderPanel(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;

    el.innerHTML = `
      <div class="docket-monitor" style="display:flex;flex-direction:column;gap:20px">
        <!-- Header bar -->
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
          <div style="display:flex;align-items:center;gap:10px">
            <span style="font-size:24px">⚖️</span>
            <div>
              <h3 style="margin:0;font-size:18px;font-weight:700;color:var(--text-primary,#f1f5f9)">
                Docket Monitor
              </h3>
              <span style="font-size:12px;color:var(--text-secondary,#94a3b8)"
                    id="dm-last-scan">Last scan: loading...</span>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <div id="dm-alert-badges" style="display:flex;gap:6px"></div>
            <button id="dm-scan-btn" onclick="SLDocketMonitor.triggerScan()"
              style="padding:8px 16px;border-radius:8px;border:1px solid var(--border,#334155);
              background:linear-gradient(135deg,#1e293b,#0f172a);color:#f1f5f9;font-size:13px;
              font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px;
              transition:all .2s">
              <span id="dm-scan-icon">🔍</span> Scan Now
            </button>
          </div>
        </div>

        <!-- KPI strip -->
        <div id="dm-kpi-strip" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px"></div>

        <!-- Filter bar -->
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <select id="dm-filter-severity" onchange="SLDocketMonitor.loadEvents()"
            style="padding:6px 12px;border-radius:6px;border:1px solid var(--border,#334155);
            background:var(--card,#1e293b);color:var(--text-primary,#f1f5f9);font-size:13px">
            <option value="">All Severities</option>
            <option value="critical">🚨 Critical</option>
            <option value="high">⚠️ High</option>
            <option value="medium">📋 Medium</option>
            <option value="low">ℹ️ Low</option>
          </select>
          <select id="dm-filter-ack" onchange="SLDocketMonitor.loadEvents()"
            style="padding:6px 12px;border-radius:6px;border:1px solid var(--border,#334155);
            background:var(--card,#1e293b);color:var(--text-primary,#f1f5f9);font-size:13px">
            <option value="false">Unacknowledged</option>
            <option value="">All</option>
            <option value="true">Acknowledged</option>
          </select>
          <button onclick="SLDocketMonitor.acknowledgeAll()"
            style="margin-left:auto;padding:6px 14px;border-radius:6px;border:1px solid #334155;
            background:transparent;color:#94a3b8;font-size:12px;cursor:pointer">
            ✓ Acknowledge All
          </button>
        </div>

        <!-- Event feed -->
        <div id="dm-event-feed" style="display:flex;flex-direction:column;gap:8px">
          <div style="text-align:center;padding:40px;color:#64748b">Loading events...</div>
        </div>

        <!-- Event type breakdown -->
        <div id="dm-type-breakdown" style="display:none">
          <h4 style="margin:0 0 12px;font-size:14px;font-weight:600;color:var(--text-secondary,#94a3b8)">
            Event Distribution
          </h4>
          <div id="dm-type-bars" style="display:flex;flex-direction:column;gap:6px"></div>
        </div>
      </div>
    `;

    // Initial load
    loadStats();
    loadEvents();
  }

  // ── Data loading ─────────────────────────────────────────────────────
  async function loadStats() {
    try {
      const data = await API.fetchJSON("/api/docket-monitor/status");
      if (!data.success) return;
      _stats = data;

      // Last scan
      const lsEl = document.getElementById("dm-last-scan");
      if (lsEl) lsEl.textContent = `Last scan: ${_timeAgo(data.last_scan)} · ${data.active_bonds_monitored || 0} bonds monitored`;

      // Alert badges
      const badgeEl = document.getElementById("dm-alert-badges");
      if (badgeEl) {
        let html = "";
        if (data.critical_alerts > 0) html += `<span style="background:#ef4444;color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;animation:pulse 2s infinite">${data.critical_alerts} CRIT</span>`;
        if (data.high_alerts > 0) html += `<span style="background:#f59e0b;color:#000;font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px">${data.high_alerts} HIGH</span>`;
        badgeEl.innerHTML = html;
      }

      // KPI strip
      const kpiEl = document.getElementById("dm-kpi-strip");
      if (kpiEl) {
        kpiEl.innerHTML = [
          _kpiCard("Total Events", data.total_events, "📊"),
          _kpiCard("Unacked", data.unacknowledged, "🔔", data.unacknowledged > 0 ? "#ef4444" : null),
          _kpiCard("Critical", data.critical_alerts, "🚨", data.critical_alerts > 0 ? "#ef4444" : null),
          _kpiCard("This Week", data.events_last_7d, "📅"),
          _kpiCard("Bonds Watched", data.active_bonds_monitored, "👁️"),
        ].join("");
      }

      // Type breakdown
      if (data.by_event_type && data.by_event_type.length > 0) {
        const bd = document.getElementById("dm-type-breakdown");
        if (bd) bd.style.display = "block";
        const bars = document.getElementById("dm-type-bars");
        if (bars) {
          const max = Math.max(...data.by_event_type.map(t => t.count));
          bars.innerHTML = data.by_event_type.map(t => {
            const pct = max > 0 ? (t.count / max * 100) : 0;
            const label = EVENT_LABELS[t.type] || t.type;
            return `<div style="display:flex;align-items:center;gap:8px">
              <span style="min-width:120px;font-size:12px;color:#94a3b8;text-align:right">${label}</span>
              <div style="flex:1;height:18px;background:#1e293b;border-radius:4px;overflow:hidden">
                <div style="height:100%;width:${pct}%;background:linear-gradient(90deg,#3b82f6,#6366f1);
                  border-radius:4px;transition:width .5s"></div>
              </div>
              <span style="min-width:30px;font-size:12px;font-weight:600;color:#e2e8f0">${t.count}</span>
            </div>`;
          }).join("");
        }
      }
    } catch (e) {
      console.error("[DocketMonitor] Stats error:", e);
    }
  }

  async function loadEvents() {
    try {
      const sevFilter = document.getElementById("dm-filter-severity")?.value || "";
      const ackFilter = document.getElementById("dm-filter-ack")?.value || "";
      let url = "/api/docket-monitor/events?limit=50";
      if (sevFilter) url += `&severity=${sevFilter}`;
      if (ackFilter !== "") url += `&acknowledged=${ackFilter}`;

      const data = await API.fetchJSON(url);
      if (!data.success) return;
      _events = data.events || [];

      const feed = document.getElementById("dm-event-feed");
      if (!feed) return;

      if (_events.length === 0) {
        feed.innerHTML = `<div style="text-align:center;padding:40px;color:#64748b">
          <span style="font-size:32px">⚖️</span>
          <p style="margin:8px 0 0;font-size:14px">No docket events found</p>
          <p style="margin:4px 0 0;font-size:12px">Click "Scan Now" to check active bonds</p>
        </div>`;
        return;
      }

      feed.innerHTML = _events.map(e => _renderEventCard(e)).join("");
    } catch (e) {
      console.error("[DocketMonitor] Events error:", e);
    }
  }

  function _renderEventCard(e) {
    const s = SEV[e.event_severity] || SEV.info;
    const label = EVENT_LABELS[e.event_type] || e.event_type;
    const ackClass = e.acknowledged ? "opacity:.5;" : "";
    return `<div class="docket-event-card" style="padding:14px 16px;border-radius:10px;
      border:1px solid ${s.color}22;background:${s.bg};${ackClass}
      display:flex;flex-direction:column;gap:6px;transition:all .2s;position:relative">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
        <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:0">
          ${_severityBadge(e.event_severity)}
          <span style="font-size:13px;font-weight:700;color:var(--text-primary,#f1f5f9)">${label}</span>
          ${_riskBadge(e.risk_adjustment)}
        </div>
        <span style="font-size:11px;color:#64748b;white-space:nowrap">${_timeAgo(e.detected_at)}</span>
      </div>
      <div style="display:flex;align-items:center;gap:12px;font-size:13px;color:var(--text-secondary,#cbd5e1)">
        <span style="font-weight:600">${e.defendant_name || "Unknown"}</span>
        ${e.bond_amount ? `<span style="color:#64748b">$${Number(e.bond_amount).toLocaleString()}</span>` : ""}
        ${e.court_name ? `<span style="color:#64748b">· ${e.court_name}</span>` : ""}
      </div>
      <div style="font-size:12px;color:#94a3b8">${e.description || ""}</div>
      ${e.docket_number ? `<div style="font-size:11px;color:#475569">Docket: ${e.docket_number}</div>` : ""}
      ${!e.acknowledged ? `<button onclick="SLDocketMonitor.ackEvent('${e._id}')" style="position:absolute;
        top:12px;right:12px;padding:4px 10px;border-radius:6px;border:1px solid #334155;
        background:transparent;color:#94a3b8;font-size:11px;cursor:pointer">✓</button>` : ""}
    </div>`;
  }

  function _kpiCard(label, value, icon, accentColor) {
    const col = accentColor || "var(--text-primary,#e2e8f0)";
    return `<div style="padding:14px 16px;border-radius:10px;
      background:var(--card,#1e293b);border:1px solid var(--border,#334155)">
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
        <span style="font-size:16px">${icon}</span>
        <span style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px">${label}</span>
      </div>
      <div style="font-size:22px;font-weight:800;color:${col}">${value ?? 0}</div>
    </div>`;
  }

  // ── Actions ──────────────────────────────────────────────────────────
  async function triggerScan() {
    if (_scanning) return;
    _scanning = true;
    const btn = document.getElementById("dm-scan-btn");
    const ico = document.getElementById("dm-scan-icon");
    if (btn) btn.style.opacity = ".6";
    if (ico) ico.textContent = "⏳";
    try {
      const data = await API.fetchJSON("/api/docket-monitor/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ limit: 50 }) });
      if (data.success) {
        window.showToast?.(`Scan complete: ${data.events_found} events, ${data.alerts_created} alerts`, "success");
      } else {
        window.showToast?.("Scan failed: " + (data.error || "unknown"), "error");
      }
      await loadStats();
      await loadEvents();
    } catch (e) {
      window.showToast?.("Scan error: " + e.message, "error");
    } finally {
      _scanning = false;
      if (btn) btn.style.opacity = "1";
      if (ico) ico.textContent = "🔍";
    }
  }

  async function ackEvent(eventId) {
    try {
      await API.fetchJSON("/api/docket-monitor/acknowledge", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_id: eventId }),
      });
      await loadEvents();
      await loadStats();
    } catch (e) { console.error("Ack error:", e); }
  }

  async function acknowledgeAll() {
    const sev = document.getElementById("dm-filter-severity")?.value || undefined;
    try {
      const data = await API.fetchJSON("/api/docket-monitor/acknowledge-all", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ severity: sev }),
      });
      window.showToast?.(`Acknowledged ${data.acknowledged_count || 0} events`, "success");
      await loadEvents();
      await loadStats();
    } catch (e) { console.error("Ack all error:", e); }
  }

  // ── Init shorthand ───────────────────────────────────────────────────
  function init(containerId) {
    renderPanel(containerId || "docketMonitorContainer");
  }

  // ── Public API ───────────────────────────────────────────────────────
  return {
    init,
    renderPanel,
    loadStats,
    loadEvents,
    triggerScan,
    ackEvent,
    acknowledgeAll,
  };
})();
