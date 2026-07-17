/**
 * sl-automations.js — Automations Engine UI
 * Loads /api/automation/status and renders a fully wired automation panel.
 *
 * Features:
 *  - Category headers with live enabled/total counts
 *  - Node-RED-only badge (read-only visibility, toggle locked)
 *  - Utilitarian badge for new NR-orchestrated automations
 *  - Run-history row showing last 5 run timestamps
 *  - GSAP micro-animation on toggle state change
 *  - NR status indicator in header bar
 */
const SLAutomations = {
  _last: null,
  _history: {},   // { [id]: string[] } — last 5 run timestamps per automation

  // ── Category display metadata ─────────────────────────────────────────────
  _catMeta: {
    revenue:   { label: 'Revenue',       icon: '💰', order: 0 },
    lifecycle: { label: 'Lifecycle',     icon: '🔄', order: 1 },
    intel:     { label: 'Intelligence',  icon: '🧠', order: 2 },
    monitor:   { label: 'Monitoring',    icon: '📡', order: 3 },
    geo:       { label: 'Geo',           icon: '📍', order: 4 },
    content:   { label: 'Content',       icon: '✍️', order: 5 },
    other:     { label: 'Other',         icon: '⚙️', order: 6 },
  },

  // ── Entry point ───────────────────────────────────────────────────────────
  async load() {
    const grid = document.getElementById('automationsGrid');
    if (grid) {
      grid.innerHTML = `<div class="auto-loading-state">
        <div class="auto-skeleton"></div><div class="auto-skeleton"></div>
        <div class="auto-skeleton"></div><div class="auto-skeleton"></div>
        <div class="auto-skeleton"></div><div class="auto-skeleton"></div>
      </div>`;
    }
    try {
      const res = await fetch('/api/automation/status', { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.success === false) throw new Error(data.error || 'API error');

      const statusMap = data.status || {};
      const automations = Object.entries(statusMap).map(([id, info]) => ({ id, ...info }));

      // Preserve run history from previous load
      automations.forEach(a => {
        if (a.last_run_at && this._history[a.id]) {
          const prev = this._history[a.id];
          if (!prev.includes(a.last_run_at)) {
            this._history[a.id] = [a.last_run_at, ...prev].slice(0, 5);
          }
        } else if (a.last_run_at) {
          this._history[a.id] = [a.last_run_at];
        }
      });

      this._last = automations;
      this.render(automations);
      this._updateBadge(automations);
      this._updateNRStatusBar(data);
    } catch (e) {
      console.error('Failed to load automations:', e);
      if (grid) {
        grid.innerHTML = `<div class="auto-error-state">
          <strong>Failed to load automations.</strong><br>
          <span>${this._esc(e.message)}</span>
          <div style="margin-top:12px"><button class="btn btn-primary" onclick="SLAutomations.load()">Retry</button></div>
        </div>`;
      }
      if (window.SL && SL.notify) SL.notify('Automations load failed: ' + e.message, 'error');
    }
  },

  _updateBadge(automations) {
    const badge = document.getElementById('automationsBadge');
    if (badge) {
      const on = automations.filter(a => a.enabled && !a.nr_only).length;
      badge.textContent = on ? String(on) : '';
    }
  },

  _updateNRStatusBar(data) {
    const bar = document.getElementById('nrStatusBar');
    if (!bar) return;
    const nrOnline = data.nr_online !== false;  // default assume online
    const nrCount = data.nr_flow_count ||
      (this._last || []).filter(a => a.nr_only || a.nr_orchestrated).length;
    bar.innerHTML = `
      <span class="nr-status-dot ${nrOnline ? 'nr-online' : 'nr-offline'}"></span>
      <span>Node-RED</span>
      <span class="nr-status-label">${nrOnline ? 'Connected' : 'Offline'}</span>
      <span class="nr-status-count">${nrCount} flows</span>
    `;
  },

  // ── Helpers ───────────────────────────────────────────────────────────────
  _esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  },

  _fmtInterval(sec) {
    if (!sec) return '—';
    if (sec < 60) return `${sec}s`;
    if (sec < 3600) return `${Math.round(sec / 60)}m`;
    if (sec < 86400) return `${(sec / 3600).toFixed(1)}h`;
    return `${(sec / 86400).toFixed(1)}d`;
  },

  _fmtTime(ts) {
    if (!ts) return null;
    try {
      const d = new Date(ts);
      return d.toLocaleString('en-US', {
        month: 'short', day: 'numeric',
        hour: 'numeric', minute: '2-digit', hour12: true
      });
    } catch (_) { return ts; }
  },

  _timeAgo(ts) {
    if (!ts) return '—';
    try {
      const diff = Date.now() - new Date(ts).getTime();
      const m = Math.floor(diff / 60000);
      if (m < 1) return 'just now';
      if (m < 60) return `${m}m ago`;
      const h = Math.floor(m / 60);
      if (h < 24) return `${h}h ago`;
      return `${Math.floor(h / 24)}d ago`;
    } catch (_) { return '—'; }
  },

  // ── Main render ───────────────────────────────────────────────────────────
  render(automations) {
    const grid = document.getElementById('automationsGrid');
    if (!grid) return;
    if (!automations.length) {
      grid.innerHTML = `<div class="auto-empty-state">No automations found.</div>`;
      return;
    }

    // Group by category
    const cats = {};
    automations.forEach(a => {
      const c = a.category || 'other';
      if (!cats[c]) cats[c] = [];
      cats[c].push(a);
    });

    // Sort categories by order
    const sortedCats = Object.keys(cats).sort((a, b) => {
      const oa = (this._catMeta[a] || { order: 99 }).order;
      const ob = (this._catMeta[b] || { order: 99 }).order;
      return oa - ob;
    });

    let html = '';
    sortedCats.forEach(cat => {
      const meta = this._catMeta[cat] || { label: cat, icon: '⚙️' };
      const items = cats[cat];
      const enabledCount = items.filter(a => a.enabled).length;
      const nrCount = items.filter(a => a.nr_only || a.nr_orchestrated).length;

      html += `
        <div class="auto-cat-header" style="grid-column:1/-1">
          <span class="auto-cat-icon">${meta.icon}</span>
          <span class="auto-cat-label">${meta.label}</span>
          <span class="auto-cat-count">${enabledCount}/${items.length} active</span>
          ${nrCount ? `<span class="auto-cat-nr-badge">⚡ ${nrCount} Node-RED</span>` : ''}
        </div>`;

      items.forEach(auto => {
        html += this._renderCard(auto);
      });
    });

    grid.innerHTML = html;

    // Animate in with GSAP if available
    if (window.gsap) {
      gsap.fromTo('.auto-card', { opacity: 0, y: 12 }, {
        opacity: 1, y: 0, duration: 0.3, stagger: 0.04, ease: 'power2.out'
      });
    }
  },

  _renderCard(auto) {
    const enabled = !!auto.enabled;
    const isNrOnly = !!(auto.nr_only);
    const isNrOrchestrated = !!(auto.nr_orchestrated);
    const hasTrigger = !!auto.has_trigger;

    const statusColor = enabled ? 'var(--success,#22c55e)' : 'var(--muted)';
    const statusText  = enabled ? 'Active' : 'Disabled';

    const lastRunText = this._timeAgo(auto.last_run_at);
    const lastRunFull = this._fmtTime(auto.last_run_at) || 'Never';

    const history = (this._history[auto.id] || []).slice(0, 5);
    const historyHtml = history.length > 1
      ? `<div class="auto-run-history">
          <span class="auto-run-history-label">Recent runs:</span>
          ${history.map(ts => `<span class="auto-run-pip" title="${this._fmtTime(ts) || ts}"></span>`).join('')}
         </div>`
      : '';

    const errHtml = auto.last_error
      ? `<div class="auto-card-error">⚠ ${this._esc(String(auto.last_error).slice(0, 140))}</div>`
      : '';

    // Badge row
    let badges = '';
    if (isNrOnly)        badges += `<span class="auto-badge auto-badge-nr">Node-RED</span>`;
    if (isNrOrchestrated) badges += `<span class="auto-badge auto-badge-util">Utilitarian</span>`;
    if (!hasTrigger && !isNrOnly) badges += `<span class="auto-badge auto-badge-sched">Schedule Only</span>`;

    // Toggle: locked for NR-only flows (they are managed in Node-RED, not Python)
    const toggleHtml = isNrOnly
      ? `<span class="auto-nr-lock" title="Managed in Node-RED — toggle from the NR editor">🔒</span>`
      : `<label class="toggle-switch" title="${enabled ? 'Disable' : 'Enable'}">
           <input type="checkbox" ${enabled ? 'checked' : ''}
             onchange="SLAutomations.toggle('${auto.id}', this.checked)">
           <span class="slider"></span>
         </label>`;

    // Run Now: disabled for NR-only; enabled for NR-orchestrated (they have a FastAPI trigger)
    const canRun = hasTrigger && !isNrOnly;
    const runBtn = `<button class="btn btn-secondary auto-run-btn"
      onclick="SLAutomations.runNow('${auto.id}')"
      ${!canRun ? `disabled title="${isNrOnly ? 'Trigger from Node-RED editor' : 'No live trigger'}"` : ''}>
      ▶ Run Now
    </button>`;

    return `
    <div class="auto-card ${enabled ? 'auto-card-active' : ''} ${isNrOnly ? 'auto-card-nr' : ''}"
         data-id="${auto.id}" id="auto-card-${auto.id}">
      <div class="auto-card-header">
        <div class="auto-card-title-block">
          <h3 class="auto-card-title">${auto.icon || '⚙️'} ${this._esc(auto.name || auto.id)}</h3>
          <p class="auto-card-desc">${this._esc(auto.description || '')}</p>
          ${badges ? `<div class="auto-badge-row">${badges}</div>` : ''}
        </div>
        ${toggleHtml}
      </div>
      <div class="auto-card-meta">
        <span><strong>Status:</strong> <span style="color:${statusColor}">${statusText}</span></span>
        <span><strong>Interval:</strong> ${this._fmtInterval(auto.interval_seconds)}</span>
        <span title="${lastRunFull}"><strong>Last run:</strong> ${this._esc(lastRunText)}</span>
      </div>
      ${errHtml}
      ${historyHtml}
      <div class="auto-card-footer">
        ${runBtn}
      </div>
    </div>`;
  },

  // ── Toggle ────────────────────────────────────────────────────────────────
  async toggle(id, enabled) {
    const card = document.getElementById(`auto-card-${id}`);
    try {
      const res = await fetch(`/api/automation/toggle/${id}`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      const data = await res.json();
      if (data.success) {
        if (window.SL && SL.notify) SL.notify(`${enabled ? 'Enabled' : 'Disabled'} automation.`);
        // Micro-animation: flash border
        if (card && window.gsap) {
          gsap.to(card, {
            boxShadow: enabled
              ? '0 0 0 2px rgba(34,197,94,.6)'
              : '0 0 0 2px rgba(239,68,68,.4)',
            duration: 0.2, yoyo: true, repeat: 1, ease: 'power1.inOut',
            onComplete: () => this.load()
          });
        } else {
          this.load();
        }
      } else {
        throw new Error(data.error || 'Toggle failed');
      }
    } catch (e) {
      console.error(e);
      if (window.SL && SL.notify) SL.notify('Failed to update automation: ' + e.message, 'error');
      this.load();
    }
  },

  // ── Run Now ───────────────────────────────────────────────────────────────
  async runNow(id) {
    const btn = document.querySelector(`#auto-card-${id} .auto-run-btn`);
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Running…'; }
    try {
      if (window.SL && SL.notify) SL.notify('Triggering automation…', 'info');
      const res = await fetch(`/api/automation/trigger/${id}`, {
        method: 'POST',
        credentials: 'same-origin',
      });
      const data = await res.json();
      if (data.success) {
        // Record in local history
        const now = new Date().toISOString();
        this._history[id] = [now, ...(this._history[id] || [])].slice(0, 5);
        if (window.SL && SL.notify) SL.notify(data.message || 'Triggered.', 'success');
        setTimeout(() => this.load(), 1500);
      } else {
        throw new Error(data.error || 'Trigger failed');
      }
    } catch (e) {
      console.error(e);
      if (window.SL && SL.notify) SL.notify('Failed to trigger: ' + e.message, 'error');
      if (btn) { btn.disabled = false; btn.textContent = '▶ Run Now'; }
    }
  },
};

// Expose globally for sidebar onclick
window.SLAutomations = SLAutomations;
