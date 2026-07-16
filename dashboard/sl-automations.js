/**
 * sl-automations.js — Automations Engine UI
 * Loads /api/automation/status and renders cards with toggle + run-now.
 */
const SLAutomations = {
  _last: null,

  async load() {
    const grid = document.getElementById('automationsGrid');
    if (grid) {
      grid.innerHTML = `<div style="text-align:center;padding:40px;color:var(--muted);grid-column:1/-1">
        <div class="loading">Loading automations…</div>
      </div>`;
    }
    try {
      const res = await fetch('/api/automation/status', { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.success === false) throw new Error(data.error || 'API error');

      const statusMap = data.status || {};
      const automations = Object.entries(statusMap).map(([id, info]) => ({ id, ...info }));

      automations.sort((a, b) => {
        const ca = (a.category || 'other').localeCompare(b.category || 'other');
        if (ca !== 0) return ca;
        return (a.name || a.id).localeCompare(b.name || b.id);
      });

      this._last = automations;
      this.render(automations);

      const badge = document.getElementById('automationsBadge');
      if (badge) {
        const on = automations.filter(a => a.enabled).length;
        badge.textContent = on ? String(on) : '';
      }
    } catch (e) {
      console.error('Failed to load automations:', e);
      if (grid) {
        grid.innerHTML = `<div style="color:var(--danger);padding:24px;grid-column:1/-1;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.25);border-radius:10px">
          <strong>Failed to load automations.</strong><br>
          <span style="font-size:12px;opacity:.85">${this._esc(e.message)}</span>
          <div style="margin-top:12px"><button class="btn btn-primary" onclick="SLAutomations.load()">Retry</button></div>
        </div>`;
      }
      if (window.SL && SL.notify) SL.notify('Automations load failed: ' + e.message, 'error');
    }
  },

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

  render(automations) {
    const grid = document.getElementById('automationsGrid');
    if (!grid) return;
    if (!automations.length) {
      grid.innerHTML = `<div style="text-align:center;padding:40px;color:var(--muted);grid-column:1/-1">No automations found.</div>`;
      return;
    }

    // Group by category
    const cats = {};
    automations.forEach(a => {
      const c = a.category || 'other';
      if (!cats[c]) cats[c] = [];
      cats[c].push(a);
    });

    const catLabels = {
      revenue: '💰 Revenue',
      lifecycle: '🔄 Lifecycle',
      intel: '🧠 Intelligence',
      monitor: '📡 Monitoring',
      geo: '📍 Geo',
      content: '✍️ Content',
      other: '⚙️ Other',
    };

    let html = '';
    Object.keys(cats).sort().forEach(cat => {
      html += `<div style="grid-column:1/-1;margin:8px 0 4px;font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)">${catLabels[cat] || cat}</div>`;
      cats[cat].forEach(auto => {
        const enabled = !!auto.enabled;
        const statusColor = enabled ? 'var(--success,#22c55e)' : 'var(--muted)';
        const statusText = enabled ? 'Active' : 'Disabled';
        let lastRunText = 'Never';
        if (auto.last_run_at) {
          try { lastRunText = new Date(auto.last_run_at).toLocaleString(); } catch (_) {}
        }
        const err = auto.last_error
          ? `<div style="font-size:11px;color:var(--danger);margin-top:4px">⚠ ${this._esc(String(auto.last_error).slice(0, 120))}</div>`
          : '';
        html += `
        <div class="card" style="padding:18px;display:flex;flex-direction:column;gap:10px;background:var(--card,var(--panel));border:1px solid var(--border);border-radius:12px">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
            <div style="min-width:0">
              <h3 style="margin:0;font-size:15px;color:var(--text)">${auto.icon || '⚙️'} ${this._esc(auto.name || auto.id)}</h3>
              <p style="margin:4px 0 0;font-size:12px;color:var(--muted);line-height:1.4">${this._esc(auto.description || '')}</p>
            </div>
            <label class="toggle-switch" title="${enabled ? 'Disable' : 'Enable'}" style="flex-shrink:0">
              <input type="checkbox" ${enabled ? 'checked' : ''} onchange="SLAutomations.toggle('${auto.id}', this.checked)">
              <span class="slider"></span>
            </label>
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:8px 14px;font-size:11px;color:var(--text-secondary,var(--muted));background:rgba(255,255,255,0.03);padding:8px 10px;border-radius:8px">
            <span><strong>Status:</strong> <span style="color:${statusColor}">${statusText}</span></span>
            <span><strong>Interval:</strong> ${this._fmtInterval(auto.interval_seconds)}</span>
            <span><strong>Last run:</strong> ${this._esc(lastRunText)}</span>
          </div>
          ${err}
          <div style="display:flex;justify-content:flex-end;margin-top:auto">
            <button class="btn btn-secondary" style="font-size:11px;padding:5px 12px"
              onclick="SLAutomations.runNow('${auto.id}')"
              ${!auto.has_trigger ? 'disabled title="No live trigger (runs on schedule only)"' : ''}>
              ▶ Run Now
            </button>
          </div>
        </div>`;
      });
    });
    grid.innerHTML = html;
  },

  async toggle(id, enabled) {
    try {
      const res = await fetch(`/api/automation/toggle/${id}`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      const data = await res.json();
      if (data.success) {
        if (window.SL && SL.notify) SL.notify(`Automation ${enabled ? 'enabled' : 'disabled'}.`);
        this.load();
      } else {
        throw new Error(data.error || 'Toggle failed');
      }
    } catch (e) {
      console.error(e);
      if (window.SL && SL.notify) SL.notify('Failed to update automation: ' + e.message, 'error');
      this.load();
    }
  },

  async runNow(id) {
    try {
      if (window.SL && SL.notify) SL.notify('Triggering automation…', 'info');
      const res = await fetch(`/api/automation/trigger/${id}`, {
        method: 'POST',
        credentials: 'same-origin',
      });
      const data = await res.json();
      if (data.success) {
        if (window.SL && SL.notify) SL.notify(data.message || 'Triggered.', 'success');
        this.load();
      } else {
        throw new Error(data.error || 'Trigger failed');
      }
    } catch (e) {
      console.error(e);
      if (window.SL && SL.notify) SL.notify('Failed to trigger: ' + e.message, 'error');
    }
  },
};

// Expose globally for sidebar onclick
window.SLAutomations = SLAutomations;
