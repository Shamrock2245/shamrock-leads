/**
 * ShamrockLeads — Compliance Tasks Frontend
 * Handles the "Compliance Tasks" panel in the Command Center.
 */
window.SLTasks = (() => {
  'use strict';

  const API = window.SL?.apiBase || '';

  async function load() {
    const bodyEl = document.getElementById('tasksBody');
    const countEl = document.getElementById('tasksCount');
    if (!bodyEl) return;

    try {
      const resp = await fetch(`${API}/api/tasks`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      
      const tasks = data.tasks || [];
      if (countEl) countEl.textContent = tasks.length;
      
      if (!tasks.length) {
        bodyEl.innerHTML = `
          <div class="ra-empty">
            <span class="ra-empty-icon">🎉</span>
            <div class="ra-empty-text">No pending tasks</div>
            <div class="ra-empty-sub">You're all caught up!</div>
          </div>
        `;
        return;
      }

      bodyEl.innerHTML = tasks.map(t => {
        const d = new Date(t.due_date);
        const overdue = d < new Date();
        const dateStr = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        
        return `
          <div class="ra-item" style="border-left: 4px solid ${overdue ? '#ef4444' : '#f59e0b'}; padding-left: 12px; margin-bottom: 12px; background: rgba(255,255,255,0.02); padding: 12px; border-radius: 8px;">
            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
              <div>
                <div style="font-weight:600; font-size:14px;">${_esc(t.title)} <span style="font-size:11px; padding:2px 6px; border-radius:10px; background:rgba(255,255,255,0.1); margin-left:6px">${t.booking_number}</span></div>
                <div style="font-size:13px; color:var(--text-muted); margin-top:4px;">${_esc(t.description)}</div>
                <div style="font-size:12px; margin-top:8px; color:${overdue ? '#ef4444' : '#10b981'}; font-weight:500;">
                  ⏰ Due: ${dateStr} ${overdue ? '(Overdue)' : ''}
                </div>
              </div>
              <button class="sl-btn sl-btn-secondary" style="font-size:12px; padding:4px 10px;" onclick="SLTasks.complete('${t._id}')">✓ Mark Done</button>
            </div>
          </div>
        `;
      }).join('');

    } catch (err) {
      bodyEl.innerHTML = `<div class="ra-empty"><div class="ra-empty-text" style="color:#ef4444">Error loading tasks</div></div>`;
    }
  }

  async function complete(taskId) {
    try {
      const resp = await fetch(`${API}/api/tasks/${taskId}/complete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes: "Completed from dashboard" })
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      if (window.SL?.toast) SL.toast('Task completed', 'success');
      load(); // Refresh the list
    } catch (err) {
      if (window.SL?.toast) SL.toast('Failed to complete task', 'error');
    }
  }

  function _esc(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // Load automatically when switching to Command Center
  document.addEventListener('DOMContentLoaded', () => {
    // Also hook into SL.refresh
    const origRefresh = window.SL?.refresh;
    if (window.SL) {
      window.SL.refresh = function() {
        if (origRefresh) origRefresh.apply(this, arguments);
        load();
      };
    }
  });

  return { load, complete };
})();
