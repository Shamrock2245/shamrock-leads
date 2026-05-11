/**
 * ShamrockLeads — Service Control Center
 * =======================================
 * Premium UI module for managing all 16 background services.
 * Provides real-time status, animated toggle switches, and batch controls.
 *
 * Dependencies: SL.toast() from sl-core.js, SL.switchTab()
 */
/* eslint-disable no-unused-vars */
const SLServiceControl = (() => {
  'use strict';

  let _inited = false;
  let _autoRefreshId = null;
  let _services = {};      // key → { enabled, name, icon, category, desc, last_run_at, ... }
  const AUTO_REFRESH_MS = 30_000;

  // ── Category → grid element mapping ──
  const CATEGORY_GRIDS = {
    revenue: 'svcRevenueGrid',
    intel:   'svcIntelGrid',
    monitor: 'svcMonitorGrid',
    geo:     'svcGeoGrid',
    content: 'svcContentGrid',
  };

  // ── Public API ──

  function init() {
    if (!_inited) {
      _inited = true;
      _startAutoRefresh();
    }
    refresh();
  }

  async function refresh() {
    try {
      const resp = await fetch('/api/automation/status');
      const data = await resp.json();
      if (!data.success) throw new Error(data.error || 'Failed to load status');
      _services = data.status || {};
      _render();
    } catch (err) {
      console.error('[SvcControl] refresh error:', err);
      const bar = document.getElementById('svcSummaryKpi');
      if (bar) bar.innerHTML = `<span style="color:var(--red)">⚠ Failed to load service status</span>`;
    }
  }

  async function toggle(key) {
    const card = document.getElementById(`svc-${key}`);
    if (card) card.classList.add('svc-toggling');
    try {
      const resp = await fetch(`/api/automation/toggle/${key}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const data = await resp.json();
      if (!data.success) throw new Error(data.error);
      const label = data.enabled ? 'STARTED' : 'STOPPED';
      const meta = _services[key] || {};
      if (typeof SL !== 'undefined' && SL.toast) {
        SL.toast(`${meta.icon || '⚙️'} ${meta.name || key} → ${label}`, data.enabled ? 'success' : 'info');
      }
      await refresh();
    } catch (err) {
      console.error('[SvcControl] toggle error:', err);
      if (typeof SL !== 'undefined' && SL.toast) {
        SL.toast(`Toggle failed: ${err.message}`, 'error');
      }
    } finally {
      if (card) card.classList.remove('svc-toggling');
    }
  }

  async function toggleCategory(category, enabled) {
    const keys = Object.entries(_services)
      .filter(([, v]) => v.category === category && v.enabled !== enabled)
      .map(([k]) => k);
    if (!keys.length) {
      if (typeof SL !== 'undefined' && SL.toast) {
        SL.toast(`All ${category} services already ${enabled ? 'running' : 'stopped'}`, 'info');
      }
      return;
    }
    for (const key of keys) {
      const card = document.getElementById(`svc-${key}`);
      if (card) card.classList.add('svc-toggling');
      try {
        await fetch(`/api/automation/toggle/${key}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled }),
        });
      } catch (e) {
        console.warn(`[SvcControl] batch toggle ${key} error:`, e);
      }
    }
    await refresh();
    if (typeof SL !== 'undefined' && SL.toast) {
      SL.toast(`${enabled ? '▶' : '■'} ${keys.length} ${category} service(s) ${enabled ? 'started' : 'stopped'}`, 'success');
    }
  }

  // ── Rendering ──

  function _render() {
    // 1. Summary KPI bar
    const total = Object.keys(_services).length;
    const running = Object.values(_services).filter(s => s.enabled).length;
    const stopped = total - running;
    const bar = document.getElementById('svcSummaryKpi');
    if (bar) {
      bar.innerHTML = `
        <span><strong>${total}</strong> services</span>
        <span style="color:var(--accent)">● <strong>${running}</strong> running</span>
        <span style="color:var(--muted)">○ <strong>${stopped}</strong> stopped</span>
        <span style="margin-left:auto;opacity:.6">Auto-refresh: 30s</span>
      `;
    }

    // 2. Category grids
    const buckets = {};
    for (const [key, svc] of Object.entries(_services)) {
      const cat = svc.category || 'other';
      if (!buckets[cat]) buckets[cat] = [];
      buckets[cat].push({ key, ...svc });
    }

    for (const [cat, gridId] of Object.entries(CATEGORY_GRIDS)) {
      const el = document.getElementById(gridId);
      if (!el) continue;
      const items = buckets[cat] || [];
      if (!items.length) {
        el.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:8px">No services in this category</div>';
        continue;
      }
      el.innerHTML = items.map(svc => _renderCard(svc)).join('');
    }
  }

  function _renderCard(svc) {
    const on = svc.enabled;
    const ledCls = on ? 'svc-led--on' : 'svc-led--off';
    const cardCls = on ? 'svc-on' : 'svc-off';
    const statusLabel = on ? 'RUNNING' : 'STOPPED';
    const lastRun = svc.last_run_at ? _timeAgo(svc.last_run_at) : 'Never';
    const checked = on ? 'checked' : '';

    return `
      <div class="svc-card ${cardCls}" id="svc-${svc.key}">
        <div class="svc-card-top">
          <div class="svc-card-identity">
            <span class="svc-card-icon">${svc.icon || '⚙️'}</span>
            <div>
              <div class="svc-card-name">${svc.name || svc.key}</div>
              <div class="svc-card-desc">${svc.description || ''}</div>
            </div>
          </div>
          <label class="svc-switch" title="${on ? 'Stop' : 'Start'} ${svc.name || svc.key}">
            <input type="checkbox" ${checked} onchange="SLServiceControl.toggle('${svc.key}')">
            <span class="svc-switch-track"><span class="svc-switch-knob"></span></span>
          </label>
        </div>
        <div class="svc-card-bottom">
          <span class="svc-led ${ledCls}"></span>
          <span class="svc-status-label">${statusLabel}</span>
          <span class="svc-meta">Last run: ${lastRun}</span>
          <button
            id="svc-run-${svc.key}"
            class="svc-run-btn"
            title="Run ${svc.name || svc.key} now"
            onclick="SLServiceControl.runNow('${svc.key}')"
          >▶</button>
        </div>
      </div>
    `;
  }

  function _timeAgo(iso) {
    try {
      const d = new Date(iso);
      const now = Date.now();
      const diffMs = now - d.getTime();
      if (diffMs < 0) return 'just now';
      const sec = Math.floor(diffMs / 1000);
      if (sec < 60) return `${sec}s ago`;
      const min = Math.floor(sec / 60);
      if (min < 60) return `${min}m ago`;
      const hr = Math.floor(min / 60);
      if (hr < 24) return `${hr}h ago`;
      const days = Math.floor(hr / 24);
      return `${days}d ago`;
    } catch {
      return iso;
    }
  }

  function _startAutoRefresh() {
    if (_autoRefreshId) clearInterval(_autoRefreshId);
    _autoRefreshId = setInterval(() => {
      // Only refresh if the Health tab is visible
      const tab = document.getElementById('tabHealth');
      if (tab && tab.style.display !== 'none') {
        refresh();
      }
    }, AUTO_REFRESH_MS);
  }

  // ── Run Now (manual trigger) ──
  async function runNow(key) {
    const card = document.getElementById(`svc-${key}`);
    const btn = document.getElementById(`svc-run-${key}`);
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳';
    }
    try {
      const res = await fetch(`/api/automation/trigger/${key}`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        if (typeof SL !== 'undefined' && SL.toast) {
          SL.toast(data.message || `▶ ${key} triggered`, 'success');
        }
        // Brief visual feedback on the card
        if (card) {
          card.classList.add('svc-triggered');
          setTimeout(() => card.classList.remove('svc-triggered'), 2000);
        }
      } else {
        if (typeof SL !== 'undefined' && SL.toast) {
          SL.toast(`Trigger failed: ${data.error || 'unknown'}`, 'error');
        }
      }
    } catch (err) {
      console.error('[SvcControl] runNow error:', err);
      if (typeof SL !== 'undefined' && SL.toast) {
        SL.toast(`Trigger error: ${err.message}`, 'error');
      }
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = '▶';
      }
    }
  }
  // ── Expose public API ──
  return { init, refresh, toggle, toggleCategory, runNow };
})();
