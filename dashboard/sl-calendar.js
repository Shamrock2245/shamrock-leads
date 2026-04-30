/* ShamrockLeads — Court Calendar Module
   Provides month/week/day calendar views with urgency color-coding.
   Namespace: window.SLCalendar
*/
(function() {
  'use strict';

  let _events = [];
  let _view = 'month';   // month | week | list
  let _currentDate = new Date();
  let _filterCounty = '';
  let _filterUrgency = '';

  const URGENCY_COLORS = {
    today:     '#ef4444',
    this_week: '#f97316',
    upcoming:  '#3b82f6',
    overdue:   '#7c3aed',
    unknown:   '#6b7280',
  };

  const URGENCY_LABELS = {
    today:     '🔴 Today',
    this_week: '🟠 This Week',
    upcoming:  '🔵 Upcoming',
    overdue:   '🟣 Overdue',
    unknown:   '⚪ Unknown',
  };

  // ── Load events from API ─────────────────────────────────────────────────
  async function load() {
    const container = document.getElementById('tabCalendar');
    if (!container) return;

    // Compute date range based on current view
    const start = new Date(_currentDate);
    start.setDate(1);
    const end = new Date(_currentDate);
    end.setMonth(end.getMonth() + 2);

    const params = new URLSearchParams({
      start: start.toISOString(),
      end: end.toISOString(),
    });
    if (_filterCounty) params.set('county', _filterCounty);

    try {
      const res = await fetch('/api/calendar/events?' + params.toString()).then(r => r.json());
      if (res.success) {
        _events = res.events;
        renderSummaryBadges(res.summary);
        render();
      }
    } catch (err) {
      console.error('Calendar load error:', err);
    }
  }

  // ── Summary badges ───────────────────────────────────────────────────────
  function renderSummaryBadges(summary) {
    const el = document.getElementById('calSummary');
    if (!el || !summary) return;
    el.innerHTML = `
      <span class="cal-badge cal-badge-today">${summary.today} Today</span>
      <span class="cal-badge cal-badge-week">${summary.this_week} This Week</span>
      <span class="cal-badge cal-badge-upcoming">${summary.upcoming} Upcoming</span>
      ${summary.overdue > 0 ? `<span class="cal-badge cal-badge-overdue">${summary.overdue} Overdue</span>` : ''}
    `;
    // Update tab badge
    const badge = document.getElementById('calendarBadge');
    if (badge) badge.textContent = summary.today + summary.this_week || '';
  }

  // ── Main render dispatcher ───────────────────────────────────────────────
  function render() {
    const filtered = getFiltered();
    if (_view === 'month') renderMonth(filtered);
    else if (_view === 'week') renderWeek(filtered);
    else renderList(filtered);
  }

  function getFiltered() {
    return _events.filter(e => {
      if (_filterCounty && e.county !== _filterCounty) return false;
      if (_filterUrgency && e.urgency !== _filterUrgency) return false;
      return true;
    });
  }

  // ── Month View ───────────────────────────────────────────────────────────
  function renderMonth(events) {
    const grid = document.getElementById('calGrid');
    if (!grid) return;

    const year = _currentDate.getFullYear();
    const month = _currentDate.getMonth();
    const firstDay = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const today = new Date();

    // Group events by date string
    const byDate = {};
    events.forEach(e => {
      const d = e.court_date ? e.court_date.slice(0, 10) : null;
      if (d) { if (!byDate[d]) byDate[d] = []; byDate[d].push(e); }
    });

    let html = '<div class="cal-month-grid">';
    // Day headers
    ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].forEach(d => {
      html += `<div class="cal-day-header">${d}</div>`;
    });

    // Empty cells before first day
    for (let i = 0; i < firstDay; i++) html += '<div class="cal-day cal-day-empty"></div>';

    // Day cells
    for (let day = 1; day <= daysInMonth; day++) {
      const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
      const isToday = today.getFullYear() === year && today.getMonth() === month && today.getDate() === day;
      const dayEvents = byDate[dateStr] || [];
      const hasUrgent = dayEvents.some(e => e.urgency === 'today' || e.urgency === 'this_week');

      html += `<div class="cal-day ${isToday ? 'cal-day-today' : ''} ${hasUrgent ? 'cal-day-urgent' : ''}">
        <div class="cal-day-num">${day}</div>
        <div class="cal-day-events">`;

      dayEvents.slice(0, 3).forEach(e => {
        html += `<div class="cal-event" style="border-left:3px solid ${e.color}"
          onclick="SLCalendar.showDetail('${e.booking_number}')"
          title="${e.title} — ${e.county}">
          <span class="cal-event-name">${e.title.split(' ')[0]}</span>
          <span class="cal-event-county">${e.county}</span>
        </div>`;
      });

      if (dayEvents.length > 3) {
        html += `<div class="cal-event-more">+${dayEvents.length - 3} more</div>`;
      }

      html += '</div></div>';
    }

    html += '</div>';
    grid.innerHTML = html;

    // Update month title
    const titleEl = document.getElementById('calMonthTitle');
    if (titleEl) {
      titleEl.textContent = _currentDate.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    }
  }

  // ── Week View ────────────────────────────────────────────────────────────
  function renderWeek(events) {
    const grid = document.getElementById('calGrid');
    if (!grid) return;

    const startOfWeek = new Date(_currentDate);
    const day = startOfWeek.getDay();
    startOfWeek.setDate(startOfWeek.getDate() - day);

    const days = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(startOfWeek);
      d.setDate(d.getDate() + i);
      days.push(d);
    }

    const byDate = {};
    events.forEach(e => {
      const d = e.court_date ? e.court_date.slice(0, 10) : null;
      if (d) { if (!byDate[d]) byDate[d] = []; byDate[d].push(e); }
    });

    const today = new Date();
    let html = '<div class="cal-week-grid">';
    days.forEach(d => {
      const dateStr = d.toISOString().slice(0, 10);
      const isToday = d.toDateString() === today.toDateString();
      const dayEvents = byDate[dateStr] || [];
      html += `<div class="cal-week-day ${isToday ? 'cal-day-today' : ''}">
        <div class="cal-week-day-header">
          <span class="cal-week-dow">${d.toLocaleDateString('en-US', { weekday: 'short' })}</span>
          <span class="cal-week-date">${d.getDate()}</span>
        </div>
        <div class="cal-week-events">`;
      dayEvents.forEach(e => {
        html += `<div class="cal-event cal-event-full" style="border-left:3px solid ${e.color};background:${e.color}18"
          onclick="SLCalendar.showDetail('${e.booking_number}')">
          <div class="cal-event-name">${e.title}</div>
          <div class="cal-event-meta">${e.county} · $${(e.bond_amount || 0).toLocaleString()}</div>
        </div>`;
      });
      if (dayEvents.length === 0) html += '<div class="cal-week-empty">—</div>';
      html += '</div></div>';
    });
    html += '</div>';
    grid.innerHTML = html;

    const titleEl = document.getElementById('calMonthTitle');
    if (titleEl) {
      const s = days[0].toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      const e = days[6].toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
      titleEl.textContent = `${s} – ${e}`;
    }
  }

  // ── List View ────────────────────────────────────────────────────────────
  function renderList(events) {
    const grid = document.getElementById('calGrid');
    if (!grid) return;

    if (events.length === 0) {
      grid.innerHTML = '<div class="cal-empty">No court dates found for this period.</div>';
      return;
    }

    const sorted = [...events].sort((a, b) => new Date(a.court_date) - new Date(b.court_date));

    let html = '<div class="cal-list">';
    let lastDate = '';
    sorted.forEach(e => {
      const dateStr = e.court_date ? e.court_date.slice(0, 10) : 'Unknown';
      if (dateStr !== lastDate) {
        const dt = new Date(dateStr);
        const label = isNaN(dt) ? dateStr : dt.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
        html += `<div class="cal-list-date-header">${label}</div>`;
        lastDate = dateStr;
      }
      html += `<div class="cal-list-item" onclick="SLCalendar.showDetail('${e.booking_number}')"
        style="border-left:4px solid ${e.color}">
        <div class="cal-list-main">
          <span class="cal-list-name">${e.title}</span>
          <span class="cal-list-county">${e.county}</span>
          <span class="cal-urgency-badge" style="background:${e.color}22;color:${e.color}">${URGENCY_LABELS[e.urgency] || e.urgency}</span>
        </div>
        <div class="cal-list-meta">
          <span>Bond: $${(e.bond_amount || 0).toLocaleString()}</span>
          <span>Case: ${e.case_number || '—'}</span>
          <span>Surety: ${e.surety || '—'}</span>
          <span class="cal-reminder-status ${e.reminder_status === 'none' ? 'cal-reminder-none' : 'cal-reminder-active'}">
            ${e.reminders_sent > 0 ? `✅ ${e.reminders_sent} reminders sent` : '⏳ No reminders'}
          </span>
        </div>
      </div>`;
    });
    html += '</div>';
    grid.innerHTML = html;
  }

  // ── Event Detail Panel ───────────────────────────────────────────────────
  function showDetail(bookingNumber) {
    const event = _events.find(e => e.booking_number === bookingNumber);
    if (!event) return;

    const panel = document.getElementById('calDetailPanel');
    if (!panel) return;

    const dt = event.court_date ? new Date(event.court_date) : null;
    const dateLabel = dt ? dt.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'Unknown';

    panel.innerHTML = `
      <div class="cal-detail-header" style="border-left:4px solid ${event.color}">
        <div class="cal-detail-title">${event.title}</div>
        <button class="cal-detail-close" onclick="document.getElementById('calDetailPanel').style.display='none'">✕</button>
      </div>
      <div class="cal-detail-body">
        <div class="cal-detail-row"><span>Court Date</span><strong>${dateLabel}</strong></div>
        <div class="cal-detail-row"><span>Urgency</span><span class="cal-urgency-badge" style="background:${event.color}22;color:${event.color}">${URGENCY_LABELS[event.urgency] || event.urgency}</span></div>
        <div class="cal-detail-row"><span>County</span><strong>${event.county}</strong></div>
        <div class="cal-detail-row"><span>Bond Amount</span><strong>$${(event.bond_amount || 0).toLocaleString()}</strong></div>
        <div class="cal-detail-row"><span>Case #</span><strong>${event.case_number || '—'}</strong></div>
        <div class="cal-detail-row"><span>Booking #</span><strong>${event.booking_number}</strong></div>
        <div class="cal-detail-row"><span>Surety</span><strong>${event.surety || '—'}</strong></div>
        <div class="cal-detail-row"><span>Risk Score</span><strong>${event.risk_score || '—'}</strong></div>
        <div class="cal-detail-row"><span>Indemnitor</span><strong>${event.indemnitor_name || '—'}</strong></div>
        <div class="cal-detail-row"><span>Indemnitor Phone</span><strong>${event.indemnitor_phone || '—'}</strong></div>
        <div class="cal-detail-row"><span>Reminders Sent</span><strong>${event.reminders_sent || 0}</strong></div>
        <div class="cal-detail-actions">
          <button class="btn-primary" onclick="SLTracking && SLTracking.scheduleReminder('${event.booking_number}')">📅 Schedule Reminder</button>
          <button class="btn-secondary" onclick="window.open('/api/appearance-bond-pdf?booking=${event.booking_number}','_blank')">📄 View Bond</button>
        </div>
      </div>
    `;
    panel.style.display = 'block';
  }

  // ── Navigation ───────────────────────────────────────────────────────────
  function prevPeriod() {
    if (_view === 'month') _currentDate.setMonth(_currentDate.getMonth() - 1);
    else _currentDate.setDate(_currentDate.getDate() - 7);
    load();
  }

  function nextPeriod() {
    if (_view === 'month') _currentDate.setMonth(_currentDate.getMonth() + 1);
    else _currentDate.setDate(_currentDate.getDate() + 7);
    load();
  }

  function setView(view, btn) {
    _view = view;
    document.querySelectorAll('.cal-view-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    render();
  }

  function setCountyFilter(county) {
    _filterCounty = county;
    render();
  }

  function setUrgencyFilter(urgency) {
    _filterUrgency = urgency;
    render();
  }

  // ── Public API ───────────────────────────────────────────────────────────
  window.SLCalendar = { load, render, setView, prevPeriod, nextPeriod, showDetail, setCountyFilter, setUrgencyFilter };

})();
