/* ══════════════════════════════════════════════════════════════════
   sl-calendar-ext.js
   Extends the Court Calendar tab with:
   • Vanilla Calendar Pro mini date-picker (jump to date)
   • GCal Sync button (POST /api/calendar/sync-gcal)
   • Auto-Scan Reminders button (POST /api/court-reminders/auto-scan)
   • Discharge Monitor status widget
   Loaded AFTER sl-calendar.js
   ══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var _vcalInstance = null;

  /* ── Inject extra toolbar buttons ───────────────────────────── */
  function _injectCalendarToolbar() {
    var filtersBar = document.querySelector('#tabCalendar .filters');
    if (!filtersBar || document.getElementById('calGcalSyncBtn')) return;

    // GCal Sync button
    var gcalBtn = document.createElement('button');
    gcalBtn.id = 'calGcalSyncBtn';
    gcalBtn.className = 'btn-export';
    gcalBtn.style.cssText = 'background:#4285f4;color:#fff;font-size:12px;padding:6px 12px';
    gcalBtn.innerHTML = '📅 Sync to Google Cal';
    gcalBtn.title = 'Sync upcoming court dates to Google Calendar';
    gcalBtn.onclick = _syncGcal;

    // Auto-scan reminders button
    var scanBtn = document.createElement('button');
    scanBtn.id = 'calAutoScanBtn';
    scanBtn.className = 'btn-export';
    scanBtn.style.cssText = 'background:#7c3aed;color:#fff;font-size:12px;padding:6px 12px';
    scanBtn.innerHTML = '🔔 Auto-Scan Reminders';
    scanBtn.title = 'Scan active bonds and schedule court date reminders';
    scanBtn.onclick = _autoScanReminders;

    // Discharge monitor status
    var dischargeBtn = document.createElement('button');
    dischargeBtn.id = 'calDischargeBtn';
    dischargeBtn.className = 'btn-export';
    dischargeBtn.style.cssText = 'background:#0f766e;color:#fff;font-size:12px;padding:6px 12px';
    dischargeBtn.innerHTML = '📧 Check Discharge Emails';
    dischargeBtn.title = 'Scan Gmail for discharge/exoneration emails';
    dischargeBtn.onclick = _checkDischarge;

    filtersBar.appendChild(gcalBtn);
    filtersBar.appendChild(scanBtn);
    filtersBar.appendChild(dischargeBtn);
  }

  /* ── Inject Vanilla Calendar Pro mini-picker sidebar ────────── */
  function _injectMiniCalendar() {
    var calTab = document.getElementById('tabCalendar');
    if (!calTab || document.getElementById('calVcalContainer')) return;

    // Inject a two-column layout: mini-picker on left, main grid on right
    var panel = calTab.querySelector('.panel');
    if (!panel) return;

    // Wrap the existing panel in a flex container with the mini-picker
    var wrapper = document.createElement('div');
    wrapper.id = 'calLayoutWrapper';
    wrapper.style.cssText = 'display:flex;gap:16px;align-items:flex-start';

    var sidebar = document.createElement('div');
    sidebar.id = 'calVcalContainer';
    sidebar.style.cssText = 'width:280px;flex-shrink:0;background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden';

    // Insert sidebar before the main panel
    panel.parentNode.insertBefore(wrapper, panel);
    wrapper.appendChild(sidebar);
    wrapper.appendChild(panel);

    // Initialize Vanilla Calendar Pro
    if (typeof VanillaCalendar !== 'undefined') {
      try {
        _vcalInstance = new VanillaCalendar('#calVcalContainer', {
          settings: {
            lang: 'en',
            visibility: {
              theme: 'dark',
            },
          },
          actions: {
            clickDay: function (e, self) {
              var selectedDate = self.selectedDates && self.selectedDates[0];
              if (!selectedDate) return;
              // Jump the main calendar to the selected date
              if (window.SLCalendar && window.SLCalendar._currentDate) {
                window.SLCalendar._currentDate = new Date(selectedDate);
              }
              if (window.SLCalendar) window.SLCalendar.load();
            },
          },
        });
        _vcalInstance.init();
      } catch (e) {
        console.warn('[sl-calendar-ext] VanillaCalendar init failed:', e);
        sidebar.innerHTML = '<div style="padding:16px;font-size:12px;color:var(--muted)">📅 Mini calendar unavailable</div>';
      }
    } else {
      sidebar.innerHTML = '<div style="padding:16px;font-size:12px;color:var(--muted)">📅 Loading mini calendar…</div>';
    }
  }

  /* ── GCal Sync ───────────────────────────────────────────────── */
  function _syncGcal() {
    var btn = document.getElementById('calGcalSyncBtn');
    if (btn) { btn.disabled = true; btn.innerHTML = '⏳ Syncing…'; }

    fetch('/api/calendar/sync-gcal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ days_ahead: 30 }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.success) {
          _toast('✅ Synced ' + data.synced + ' court dates to Google Calendar', 'success');
        } else if (data.code === 'GCAL_NOT_CONFIGURED') {
          _toast('⚠️ Google Calendar not configured — see docs/GCAL_SYNC_SETUP.md', 'info', 6000);
        } else {
          _toast('❌ Sync failed: ' + (data.error || 'Unknown error'), 'error');
        }
      })
      .catch(function () { _toast('Network error during GCal sync', 'error'); })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.innerHTML = '📅 Sync to Google Cal'; }
      });
  }

  /* ── Auto-Scan Reminders ─────────────────────────────────────── */
  function _autoScanReminders() {
    var btn = document.getElementById('calAutoScanBtn');
    if (btn) { btn.disabled = true; btn.innerHTML = '⏳ Scanning…'; }

    fetch('/api/court-reminders/auto-scan', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.success) {
          var msg = '🔔 Auto-scan complete: ' + (data.scheduled || 0) + ' scheduled, ' + (data.skipped || 0) + ' skipped';
          _toast(msg, 'success', 5000);
          if (window.SLCalendar) window.SLCalendar.load();
        } else {
          _toast('❌ Auto-scan failed: ' + (data.error || 'Unknown error'), 'error');
        }
      })
      .catch(function () { _toast('Network error during auto-scan', 'error'); })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.innerHTML = '🔔 Auto-Scan Reminders'; }
      });
  }

  /* ── Check Discharge Emails ──────────────────────────────────── */
  function _checkDischarge() {
    var btn = document.getElementById('calDischargeBtn');
    if (btn) { btn.disabled = true; btn.innerHTML = '⏳ Checking…'; }

    fetch('/api/discharge-monitor/scan', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.success) {
          var msg = '📧 Discharge scan: ' + (data.found || 0) + ' emails found, ' + (data.processed || 0) + ' bonds queued for discharge';
          _toast(msg, data.found > 0 ? 'success' : 'info', 5000);
          if (data.found > 0 && window.loadActiveBonds) loadActiveBonds();
        } else if (data.code === 'GMAIL_NOT_CONFIGURED') {
          _toast('⚠️ Gmail not configured — see docs/GMAIL_DISCHARGE_SETUP.md', 'info', 6000);
        } else {
          _toast('❌ Discharge scan failed: ' + (data.error || 'Unknown error'), 'error');
        }
      })
      .catch(function () { _toast('Network error during discharge scan', 'error'); })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.innerHTML = '📧 Check Discharge Emails'; }
      });
  }

  /* ── Toast helper (mirrors sl-active-bonds.js) ───────────────── */
  function _toast(msg, type, duration) {
    if (typeof toast === 'function') { toast(msg, type, duration); return; }
    var t = document.createElement('div');
    t.style.cssText = 'position:fixed;bottom:24px;right:24px;padding:12px 18px;border-radius:8px;font-size:13px;z-index:9999;max-width:380px;background:' + (type === 'error' ? '#ef4444' : type === 'success' ? '#22c55e' : '#3b82f6') + ';color:#fff';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, duration || 4000);
  }

  /* ── Expose _currentDate on SLCalendar for mini-picker ──────── */
  function _patchSLCalendar() {
    if (!window.SLCalendar) return;
    if (!window.SLCalendar._currentDate) {
      window.SLCalendar._currentDate = new Date();
    }
  }

  /* ── Init ────────────────────────────────────────────────────── */
  function _init() {
    _patchSLCalendar();
    _injectCalendarToolbar();
    // Defer mini-calendar injection until the Calendar tab is first opened
    var calBtn = document.querySelector('[data-tab="tabCalendar"]');
    if (calBtn) {
      var _origClick = calBtn.onclick;
      calBtn.onclick = function (e) {
        if (_origClick) _origClick.call(this, e);
        setTimeout(_injectMiniCalendar, 100);
      };
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    setTimeout(_init, 400);
  }
})();
