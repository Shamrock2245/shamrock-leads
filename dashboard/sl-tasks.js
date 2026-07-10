/**
 * ShamrockLeads — Compliance Tasks Frontend
 * Handles the "Compliance Tasks" panel in the Command Center.
 *
 * Phase 4 Polish — v2:
 *  - Filter tabs: All / Overdue / Pending (with live counts)
 *  - Surety-aware task badge (OSI vs Palmetto)
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
    checkin_enroll:   '📍',
    traccar_install:  '📡',
    collect_payment:  '💵',
    court_reminder:   '🏛️',
    payment:          '💵',
    payment_reminder: '💵',
    paperwork:        '📄',
    general:          '✅',
  };

  // ── Active filter state ────────────────────────────────────────────────────
  let _activeFilter = 'all';   // 'all' | 'overdue' | 'pending'
  let _allTasks = [];

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

  /** Build the surety badge HTML for a task */
  function _suretyBadge(task) {
    const surety = (task.surety_id || task.insurance_company || '').toLowerCase();
    if (surety.includes('palm') || surety.includes('psc')) {
      return '<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:rgba(34,197,94,0.12);color:#22c55e;font-weight:600;margin-left:4px">🌴 Palmetto</span>';
    }
    if (surety.includes('osi') || surety.includes('old')) {
      return '<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:rgba(59,130,246,0.12);color:#60a5fa;font-weight:600;margin-left:4px">🛡️ OSI</span>';
    }
    return '';
  }

  /** Render the filter tab bar above the task list */
  function _renderFilterTabs(tasks) {
    const tabsEl = document.getElementById('tasksFilterTabs');
    if (!tabsEl) return;
    const overdue = tasks.filter(t => new Date(t.due_date) < new Date()).length;
    const pending = tasks.filter(t => new Date(t.due_date) >= new Date()).length;
    const tabs = [
      { id: 'all',     label: 'All',     count: tasks.length },
      { id: 'overdue', label: 'Overdue', count: overdue },
      { id: 'pending', label: 'Pending', count: pending },
    ];
    tabsEl.innerHTML = tabs.map(tab => {
      const active = tab.id === _activeFilter;
      const countColor = tab.id === 'overdue' && tab.count ? '#ef4444' : (active ? '#fff' : 'var(--muted)');
      return `<button class="sl-task-tab${active ? ' active' : ''}" data-filter="${tab.id}"
        style="padding:4px 12px;border-radius:16px;border:1px solid ${active ? 'var(--accent)' : 'var(--border)'};
               background:${active ? 'var(--accent)' : 'transparent'};color:${active ? '#fff' : 'var(--text)'};
               font-size:12px;cursor:pointer;font-weight:${active ? '600' : '400'};transition:all .15s">
        ${_esc(tab.label)}
        <span style="font-size:10px;margin-left:4px;color:${countColor}">${tab.count}</span>
      </button>`;
    }).join('');
    tabsEl.querySelectorAll('.sl-task-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        _activeFilter = btn.getAttribute('data-filter');
        _renderTaskList(document.getElementById('tasksBody'), _allTasks);
        _renderFilterTabs(_allTasks);
      });
    });
  }

  /** Render the filtered task list into bodyEl */
  function _renderTaskList(bodyEl, tasks) {
    if (!bodyEl) return;
    const now = new Date();
    const filtered = tasks.filter(t => {
      const overdue = new Date(t.due_date) < now;
      if (_activeFilter === 'overdue') return overdue;
      if (_activeFilter === 'pending') return !overdue;
      return true;
    });
    if (!filtered.length) {
      bodyEl.innerHTML = '<div class="ra-empty sl-tasks-empty-state">'
        + '<div class="sl-tasks-empty-icon">☘️</div>'
        + '<div class="ra-empty-text">' + (_activeFilter === 'overdue' ? 'No overdue tasks' : _activeFilter === 'pending' ? 'No upcoming tasks' : 'All clear!') + '</div>'
        + '<div class="ra-empty-sub">No compliance tasks in this view</div>'
        + '</div>';
      return;
    }
    var html = '';
    filtered.forEach(function(t) {
      var overdue  = new Date(t.due_date) < now;
      var icon     = TYPE_ICONS[t.task_type] || TYPE_ICONS.general;
      var relTime  = _relativeTime(t.due_date);
      var absTime  = _formatDate(t.due_date);
      var dueBadge = overdue
        ? '<span class="sl-task-due sl-task-due-overdue"><span class="sl-task-overdue-dot"></span>' + _esc(relTime) + '</span>'
        : '<span class="sl-task-due sl-task-due-ok">⏰ ' + _esc(relTime) + '</span>';
      var suretyBadge = _suretyBadge(t);

      html += '<div class="sl-task-item' + (overdue ? ' sl-task-overdue' : '') + '" data-task-id="' + _esc(t._id) + '" role="listitem">'
        + '<div class="sl-task-icon">' + icon + '</div>'
        + '<div class="sl-task-content">'
        + '<div class="sl-task-header">'
        + '<span class="sl-task-title">' + _esc(t.title) + '</span>'
        + suretyBadge
        + '<span class="sl-task-booking">#' + _esc(t.booking_number) + '</span>'
        + '</div>'
        + '<div class="sl-task-desc">' + _esc(t.description) + '</div>'
        + '<div class="sl-task-meta">' + dueBadge
        + '<span class="sl-task-abs-time" title="' + _esc(absTime) + '">' + _esc(absTime) + '</span>'
        + '</div>'
        + '</div>'
        + '<button class="sl-btn sl-btn-secondary sl-task-done-btn" data-task-id="' + _esc(t._id) + '" title="Mark as done" aria-label="Mark task done">'
        + '<span class="sl-task-done-icon">✓</span>'
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
  }

  // ── Main load ─────────────────────────────────────────────────────────────
  async function load() {
    const bodyEl  = document.getElementById('tasksBody');
    const countEl = document.getElementById('tasksCount');
    if (!bodyEl) return;
    bodyEl.innerHTML = '<div class="sl-tasks-loading"><span class="sl-tasks-spinner"></span> Loading tasks…</div>';
    try {
      // Fetch both pending and overdue in parallel — overdue tasks are flagged
      // by the cron job and stored with status='overdue', so we need both.
      const [pendingResp, overdueResp] = await Promise.all([
        fetch(API + '/api/tasks/?status=pending&limit=200'),
        fetch(API + '/api/tasks/?status=overdue&limit=200'),
      ]);
      if (!pendingResp.ok) throw new Error('HTTP ' + pendingResp.status);
      const pendingData = await pendingResp.json();
      const overdueData = overdueResp.ok ? await overdueResp.json() : { tasks: [] };
      // Merge: overdue first (highest urgency), then pending
      const overdueIds = new Set((overdueData.tasks || []).map(t => String(t._id)));
      const pendingOnly = (pendingData.tasks || []).filter(t => !overdueIds.has(String(t._id)));
      _allTasks = [...(overdueData.tasks || []), ...pendingOnly];

      // Update badge count (capped at 99+)
      const overdueCnt = _allTasks.filter(t => new Date(t.due_date) < new Date()).length;
      if (countEl) {
        countEl.textContent = _allTasks.length > 99 ? '99+' : _allTasks.length;
        // Badge turns red when there are overdue tasks
        countEl.style.background = overdueCnt ? '#ef4444' : (_allTasks.length ? '#f59e0b' : 'var(--surface)');
        countEl.title = overdueCnt ? overdueCnt + ' overdue' : _allTasks.length + ' pending';
      }

      if (!_allTasks.length) {
        // Clear filter tabs
        const tabsEl = document.getElementById('tasksFilterTabs');
        if (tabsEl) tabsEl.innerHTML = '';
        bodyEl.innerHTML = '<div class="ra-empty sl-tasks-empty-state">'
          + '<div class="sl-tasks-empty-icon">☘️</div>'
          + '<div class="ra-empty-text">All clear!</div>'
          + '<div class="ra-empty-sub">No pending compliance tasks</div>'
          + '</div>';
        return;
      }

      // Render filter tabs and task list
      _renderFilterTabs(_allTasks);
      _renderTaskList(bodyEl, _allTasks);

    } catch (err) {
      bodyEl.innerHTML = '<div class="ra-empty">'
        + '<div class="ra-empty-text" style="color:#ef4444">⚠ Error loading tasks</div>'
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
      btnEl.innerHTML = '<span class="sl-task-done-icon">…</span>';
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

      // Remove from in-memory list
      _allTasks = _allTasks.filter(t => String(t._id) !== String(taskId));

      // Micro-animation: flash green, then slide out
      itemEl.classList.add('sl-task-completing');
      if (btnEl) btnEl.innerHTML = '<span class="sl-task-done-icon">✓</span>';

      setTimeout(function() {
        itemEl.classList.add('sl-task-done-exit');
        itemEl.addEventListener('transitionend', function() {
          itemEl.remove();
          // Refresh tabs and count with updated list
          const countEl = document.getElementById('tasksCount');
          if (countEl) {
            const overdueCnt = _allTasks.filter(t => new Date(t.due_date) < new Date()).length;
            countEl.textContent = _allTasks.length > 99 ? '99+' : _allTasks.length;
            countEl.style.background = overdueCnt ? '#ef4444' : (_allTasks.length ? '#f59e0b' : 'var(--surface)');
          }
          _renderFilterTabs(_allTasks);
          if (!_allTasks.length) {
            const bodyEl = document.getElementById('tasksBody');
            if (bodyEl) bodyEl.innerHTML = '<div class="ra-empty sl-tasks-empty-state">'
              + '<div class="sl-tasks-empty-icon">☘️</div>'
              + '<div class="ra-empty-text">All clear!</div>'
              + '<div class="ra-empty-sub">No pending compliance tasks</div>'
              + '</div>';
          }
        }, { once: true });
      }, 400);

      if (window.SL && window.SL.toast) SL.toast('Task marked as complete', 'success');
      else if (window.toast) toast('Task marked as complete', 'success');

    } catch (err) {
      if (btnEl) {
        btnEl.disabled = false;
        btnEl.innerHTML = '<span class="sl-task-done-icon">✓</span>';
      }
      if (window.SL && window.SL.toast) SL.toast('Failed to complete task', 'error');
      else if (window.toast) toast('Failed to complete task', 'error');
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
