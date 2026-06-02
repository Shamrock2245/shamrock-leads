/**
 * ShamrockLeads — Compliance Tasks Frontend
 * Handles the "Compliance Tasks" panel in the Command Center.
 *
 * Improvements (Phase 4 Polish):
 *  - Micro-animation on task completion (flash green → slide-out)
 *  - data-task-id event delegation instead of inline onclick (XSS-safe)
 *  - Overdue badge with pulsing red dot
 *  - Task type icon mapping (all engine-generated types covered)
 *  - Graceful empty-state with shamrock branding
 *  - Capped badge count (99+)
 *  - Loading skeleton state
 *  - Agent identity resolved from slcAgent input, SL.currentAgent, or localStorage
 *  - Auto-loads on DOMContentLoaded (not just on SL.refresh)
 */
window.SLTasks = (() => {
  'use strict';

  const API = window.SL?.apiBase || '';

  // ── Task type → icon mapping (covers all types emitted by task_engine.py) ──
  const TYPE_ICONS = {
    check_in:         '📋',
    check_in_30d:     '📅',
    court_reminder:   '🏛️',
    payment:          '💵',
    payment_reminder: '💵',
    paperwork:        '📄',
    general:          '✅',
  };

  // ── Render helpers ────────────────────────────────────────────────────────
  function _esc(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _relativeTime(dateStr) {
    const d = new Date(dateStr);
    if (isNaN(d)) return '—';
    const now = new Date();
    const diffMs = d - now;
    const diffDays = Math.round(diffMs / 86400000);
    if (diffDays < 0) return Math.abs(diffDays) + 'd overdue';
    if (diffDays === 0) return 'Due today';
    if (diffDays === 1) return 'Due tomorrow';
    return 'Due in ' + diffDays + 'd';
  }

  function _formatDate(dateStr) {
    const d = new Date(dateStr);
    if (isNaN(d)) return '—';
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
      + ' · '
      + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
  }

  /** Resolve the current agent name from the DOM, SL global, or localStorage. */
  function _currentAgent() {
    return (document.getElementById('slcAgent') || {}).value
      || window.SL?.currentAgent
      || localStorage.getItem('sl_agent_name')
      || 'Dashboard Agent';
  }

  // ── Main load ─────────────────────────────────────────────────────────────
  async function load() {
    const bodyEl  = document.getElementById('tasksBody');
    const countEl = document.getElementById('tasksCount');
    if (!bodyEl) return;

    bodyEl.innerHTML = '<div class="sl-tasks-loading"><span class="sl-tasks-spinner"></span> Loading tasks\u2026</div>';

    try {
      const resp = await fetch(API + '/api/tasks');
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data  = await resp.json();
      const tasks = data.tasks || [];

      // Update badge count (capped at 99+)
      if (countEl) {
        countEl.textContent = tasks.length > 99 ? '99+' : tasks.length;
        countEl.style.background = tasks.length ? '#f59e0b' : 'var(--surface)';
      }

      if (!tasks.length) {
        bodyEl.innerHTML = '<div class="ra-empty sl-tasks-empty-state">'
          + '<div class="sl-tasks-empty-icon">\u2618\uFE0F</div>'
          + '<div class="ra-empty-text">All clear!</div>'
          + '<div class="ra-empty-sub">No pending compliance tasks</div>'
          + '</div>';
        return;
      }

      var html = '';
      tasks.forEach(function(t) {
        var overdue  = new Date(t.due_date) < new Date();
        var icon     = TYPE_ICONS[t.task_type] || TYPE_ICONS.general;
        var relTime  = _relativeTime(t.due_date);
        var absTime  = _formatDate(t.due_date);
        var dueBadge = overdue
          ? '<span class="sl-task-due sl-task-due-overdue"><span class="sl-task-overdue-dot"></span>' + _esc(relTime) + '</span>'
          : '<span class="sl-task-due sl-task-due-ok">\u23F0 ' + _esc(relTime) + '</span>';

        html += '<div class="sl-task-item' + (overdue ? ' sl-task-overdue' : '') + '" data-task-id="' + _esc(t._id) + '" role="listitem">'
          + '<div class="sl-task-icon">' + icon + '</div>'
          + '<div class="sl-task-content">'
          + '<div class="sl-task-header">'
          + '<span class="sl-task-title">' + _esc(t.title) + '</span>'
          + '<span class="sl-task-booking">#' + _esc(t.booking_number) + '</span>'
          + '</div>'
          + '<div class="sl-task-desc">' + _esc(t.description) + '</div>'
          + '<div class="sl-task-meta">' + dueBadge
          + '<span class="sl-task-abs-time" title="' + _esc(absTime) + '">' + _esc(absTime) + '</span>'
          + '</div>'
          + '</div>'
          + '<button class="sl-btn sl-btn-secondary sl-task-done-btn" data-task-id="' + _esc(t._id) + '" title="Mark as done" aria-label="Mark task done">'
          + '<span class="sl-task-done-icon">\u2713</span>'
          + '</button>'
          + '</div>';
      });
      bodyEl.innerHTML = html;

      // Attach click handlers via event delegation (XSS-safe — no inline onclick)
      bodyEl.querySelectorAll('.sl-task-done-btn').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
          e.stopPropagation();
          var taskId = btn.getAttribute('data-task-id');
          if (taskId) complete(taskId, btn);
        });
      });

    } catch (err) {
      bodyEl.innerHTML = '<div class="ra-empty">'
        + '<div class="ra-empty-text" style="color:#ef4444">\u26A0 Error loading tasks</div>'
        + '<div class="ra-empty-sub" style="font-size:11px">' + _esc(err.message) + '</div>'
        + '</div>';
    }
  }

  // ── Complete task with micro-animation ────────────────────────────────────
  async function complete(taskId, btnEl) {
    var itemEl = document.querySelector('.sl-task-item[data-task-id="' + taskId + '"]');
    if (!itemEl) return;

    // Disable button immediately to prevent double-click
    if (btnEl) {
      btnEl.disabled = true;
      btnEl.innerHTML = '<span class="sl-task-done-icon">\u2026</span>';
    }

    try {
      var resp = await fetch(API + '/api/tasks/' + taskId + '/complete', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          notes: 'Completed from dashboard',
          agent: _currentAgent(),
        }),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);

      // Micro-animation: flash green, then slide out
      itemEl.classList.add('sl-task-completing');
      if (btnEl) btnEl.innerHTML = '<span class="sl-task-done-icon">\u2713</span>';

      setTimeout(function() {
        itemEl.classList.add('sl-task-done-exit');
        itemEl.addEventListener('transitionend', function() {
          itemEl.remove();
          // Update count badge
          var remaining = document.querySelectorAll('.sl-task-item').length;
          var countEl   = document.getElementById('tasksCount');
          if (countEl) {
            countEl.textContent = remaining > 99 ? '99+' : remaining;
            if (!remaining) {
              var bodyEl = document.getElementById('tasksBody');
              if (bodyEl) bodyEl.innerHTML = '<div class="ra-empty sl-tasks-empty-state">'
                + '<div class="sl-tasks-empty-icon">\u2618\uFE0F</div>'
                + '<div class="ra-empty-text">All clear!</div>'
                + '<div class="ra-empty-sub">No pending compliance tasks</div>'
                + '</div>';
            }
          }
        }, { once: true });
      }, 400);

      if (window.SL && window.SL.toast) SL.toast('Task marked as complete', 'success');

    } catch (err) {
      if (btnEl) {
        btnEl.disabled = false;
        btnEl.innerHTML = '<span class="sl-task-done-icon">\u2713</span>';
      }
      if (window.SL && window.SL.toast) SL.toast('Failed to complete task', 'error');
    }
  }

  // ── Auto-load on DOMContentLoaded ─────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function() {
    // Load immediately on page ready (not just on SL.refresh)
    load();
    // Also hook into SL.refresh so tasks reload whenever the dashboard refreshes
    var origRefresh = window.SL && window.SL.refresh;
    if (window.SL) {
      window.SL.refresh = function() {
        if (origRefresh) origRefresh.apply(this, arguments);
        load();
      };
    }
  });

  return { load: load, complete: complete };
})();
