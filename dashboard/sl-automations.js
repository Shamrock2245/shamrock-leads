/**
 * sl-automations.js
 * Frontend logic for the Automations Engine.
 */

const SLAutomations = {
  async load() {
    try {
      const res = await fetch('/api/automation/status');
      const data = await res.json();
      const automations = data.status ? Object.entries(data.status).map(([id, info]) => ({ id, ...info })) : [];
      
      // Sort automations: group by category, then alphabetically
      automations.sort((a, b) => {
        if (a.category !== b.category) return a.category.localeCompare(b.category);
        return a.name.localeCompare(b.name);
      });
      
      this.render(automations);
    } catch (e) {
      console.error('Failed to load automations:', e);
      document.getElementById('automationsGrid').innerHTML = `<div style="color:var(--danger); padding:20px;">Failed to load automations.</div>`;
    }
  },

  render(automations) {
    const grid = document.getElementById('automationsGrid');
    if (!automations.length) {
      grid.innerHTML = `<div style="text-align:center; padding: 40px; color: var(--muted); grid-column: 1 / -1;">No automations found.</div>`;
      return;
    }

    grid.innerHTML = automations.map(auto => {
      let statusColor = auto.enabled ? 'var(--success)' : 'var(--muted)';
      let statusText = auto.enabled ? 'Active' : 'Disabled';
      
      let lastRunText = 'Never';
      if (auto.last_run_at) {
        const d = new Date(auto.last_run_at);
        lastRunText = d.toLocaleString();
      }

      return `
      <div class="card" style="padding: 20px; display: flex; flex-direction: column; gap: 12px; background: var(--card); border: 1px solid var(--border); border-radius: 12px;">
        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
          <div>
            <h3 style="margin: 0; font-size: 16px; color: var(--text);">${auto.icon || '⚙️'} ${auto.name}</h3>
            <p style="margin: 4px 0 0; font-size: 12px; color: var(--muted);">${auto.description || ''}</p>
          </div>
          <label class="toggle-switch">
            <input type="checkbox" ${auto.enabled ? 'checked' : ''} onchange="SLAutomations.toggle('${auto.id}', this.checked)">
            <span class="slider"></span>
          </label>
        </div>
        
        <div style="display: flex; justify-content: space-between; font-size: 11px; color: var(--text-secondary); background: rgba(255,255,255,0.03); padding: 8px; border-radius: 6px;">
          <span><strong>Category:</strong> ${auto.category || 'other'}</span>
          <span><strong>Status:</strong> <span style="color: ${statusColor}">${statusText}</span></span>
        </div>
        
        <div style="display: flex; justify-content: space-between; align-items: center; margin-top: auto;">
          <span style="font-size: 10px; color: var(--muted);">Last run: ${lastRunText}</span>
          <button class="btn btn-secondary" style="font-size: 11px; padding: 4px 10px;" onclick="SLAutomations.runNow('${auto.id}')" ${!auto.has_trigger ? 'disabled title="No trigger active"' : ''}>▶ Run Now</button>
        </div>
      </div>
    `}).join('');
  },

  async toggle(id, enabled) {
    try {
      const res = await fetch(`/api/automation/toggle/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled })
      });
      const data = await res.json();
      if (data.success) {
        SL.notify(`Automation ${enabled ? 'enabled' : 'disabled'}.`);
        this.load();
      } else {
        throw new Error(data.error);
      }
    } catch (e) {
      console.error('Failed to toggle automation:', e);
      SL.notify('Failed to update automation state: ' + e.message, 'error');
    }
  },

  async runNow(id) {
    try {
      SL.notify('Triggering automation...', 'info');
      const res = await fetch(`/api/automation/trigger/${id}`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        SL.notify(data.message || 'Automation triggered successfully.', 'success');
        this.load();
      } else {
        throw new Error(data.error);
      }
    } catch (e) {
      console.error('Failed to run automation:', e);
      SL.notify('Failed to trigger automation: ' + e.message, 'error');
    }
  }
};
