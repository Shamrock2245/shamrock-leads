/* ══════════════════════════════════════════════════════════════════
   sl-active-bonds-ext.js
   Court Countdown · CSV Export · Column Sort · Bulk Exonerate
   Has-Indemnitor Filter · Duplicate Phone Detection
   Loaded AFTER sl-active-bonds.js
   ══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ── Sort state ─────────────────────────────────────────────── */
  var _sortKey = 'court_date';
  var _sortDir = 1;

  /* ── Court countdown helpers ────────────────────────────────── */
  function _courtDays(bond) {
    var cd = bond.court_date;
    if (!cd) return null;
    try {
      var d = new Date(cd);
      if (isNaN(d.getTime())) return null;
      var today = new Date();
      today.setHours(0, 0, 0, 0);
      d.setHours(0, 0, 0, 0);
      return Math.round((d - today) / 86400000);
    } catch (e) { return null; }
  }

  function _courtCountdownBadge(days) {
    if (days === null) return '<span style="color:var(--muted);font-size:11px">—</span>';
    if (days < 0) return '<span style="background:#7c3aed;color:#fff;padding:2px 7px;border-radius:10px;font-size:11px;font-weight:600">⚠️ ' + Math.abs(days) + 'd ago</span>';
    if (days === 0) return '<span style="background:#ef4444;color:#fff;padding:2px 7px;border-radius:10px;font-size:11px;font-weight:700">TODAY</span>';
    if (days <= 3) return '<span style="background:#ef4444;color:#fff;padding:2px 7px;border-radius:10px;font-size:11px;font-weight:600">' + days + 'd</span>';
    if (days <= 7) return '<span style="background:#f97316;color:#fff;padding:2px 7px;border-radius:10px;font-size:11px;font-weight:600">' + days + 'd</span>';
    if (days <= 14) return '<span style="background:#eab308;color:#000;padding:2px 7px;border-radius:10px;font-size:11px">' + days + 'd</span>';
    return '<span style="background:var(--panel);border:1px solid var(--border);padding:2px 7px;border-radius:10px;font-size:11px;color:var(--muted)">' + days + 'd</span>';
  }

  /* ── Override renderActiveBondsTable ─────────────────────────── */
  var _origRender = null;

  function _installRenderOverride() {
    _origRender = window.renderActiveBondsTable;
    window.renderActiveBondsTable = function () {
      var tbody = document.getElementById('abTableBody');
      if (!tbody) { if (_origRender) _origRender(); return; }

      var bonds = [].concat(window._abBonds || []);

      // Search
      var searchEl = document.getElementById('abSearch');
      var q = (searchEl ? searchEl.value : '').trim().toLowerCase();
      if (q) {
        bonds = bonds.filter(function (b) {
          var indName = (b.indemnitor && b.indemnitor.name) ? b.indemnitor.name : (b.indemnitor_name || '');
          return (b.defendant_name || '').toLowerCase().indexOf(q) >= 0
            || (b.booking_number || '').toLowerCase().indexOf(q) >= 0
            || (b.county || '').toLowerCase().indexOf(q) >= 0
            || indName.toLowerCase().indexOf(q) >= 0
            || (b.charges_raw || b.charges || '').toString().toLowerCase().indexOf(q) >= 0;
        });
      }

      // Status filter
      var f = window._abFilter || 'all';
      if (f !== 'all') {
        bonds = bonds.filter(function (b) {
          if (f === 'active') return b.status === 'active';
          if (f === 'alert') return (b.alert_count || (b.alerts || []).length) > 0;
          if (f === 'monitoring') return b.status === 'monitoring';
          if (f === 'exonerated') return b.status === 'exonerated';
          return true;
        });
      }

      // Has-indemnitor filter
      if (window._abHasIndemFilter) {
        bonds = bonds.filter(function (b) {
          var name = ((b.indemnitor && b.indemnitor.name) ? b.indemnitor.name : '') || b.indemnitor_name || '';
          return name.trim().length > 0;
        });
      }

      // Sort
      bonds.sort(function (a, b) {
        var av, bv;
        if (_sortKey === 'court_days') {
          av = _courtDays(a); if (av === null) av = 99999;
          bv = _courtDays(b); if (bv === null) bv = 99999;
        } else if (_sortKey === 'bond_amount') {
          av = a.bond_amount || 0; bv = b.bond_amount || 0;
        } else if (_sortKey === 'risk_score') {
          av = a.risk_score || 0; bv = b.risk_score || 0;
        } else if (_sortKey === 'fta_risk_score') {
          av = a.fta_risk_score || 0; bv = b.fta_risk_score || 0;
        } else if (_sortKey === 'court_date') {
          av = a.court_date ? new Date(a.court_date).getTime() : 9e15;
          bv = b.court_date ? new Date(b.court_date).getTime() : 9e15;
        } else {
          av = (a[_sortKey] || '').toString().toLowerCase();
          bv = (b[_sortKey] || '').toString().toLowerCase();
        }
        if (av < bv) return -1 * _sortDir;
        if (av > bv) return 1 * _sortDir;
        return 0;
      });

      if (!bonds.length) {
        tbody.innerHTML = '<tr><td colspan="15" style="text-align:center;padding:32px;color:var(--muted)">No bonds match current filters</td></tr>';
        return;
      }

      tbody.innerHTML = bonds.map(function (b) {
        var days = _courtDays(b);
        var cdStr = '—';
        if (b.court_date) {
          try { cdStr = new Date(b.court_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' }); } catch (e) { cdStr = b.court_date; }
        }

        var risk = b.risk_score || 0;
        var rCls = risk >= 75 ? 'score-hot' : risk >= 50 ? 'score-warm' : 'score-cold';
        var statusMap = { alert: 'status-offline', active: 'status-healthy', monitoring: 'status-stale', exonerated: 'status-healthy', forfeited: 'status-offline', surrendered: 'status-stale' };
        var sCls = statusMap[b.status] || 'status-stale';
        var overdue = b.check_in_overdue;
        var hoursOver = b.hours_overdue || 0;
        var lastCI = b.last_check_in ? (function () {
          var diff = Date.now() - new Date(b.last_check_in).getTime();
          var m = Math.floor(diff / 60000);
          if (m < 60) return m + 'm ago';
          var h = Math.floor(m / 60);
          if (h < 24) return h + 'h ago';
          return Math.floor(h / 24) + 'd ago';
        })() : '<span style="color:var(--danger)">Never</span>';

        var chargesRaw = b.charges_raw || b.charges || '';
        var charges = typeof chargesRaw === 'string'
          ? (chargesRaw.length > 50 ? chargesRaw.slice(0, 47) + '…' : (chargesRaw || '—'))
          : (Array.isArray(chargesRaw) ? (chargesRaw.slice(0, 2).join(', ') + (chargesRaw.length > 2 ? ' +' + (chargesRaw.length - 2) : '')) : '—');

        var indemnitorName = ((b.indemnitor && b.indemnitor.name) ? b.indemnitor.name : '') || b.indemnitor_name || (((b.indemnitor && b.indemnitor.firstName) ? b.indemnitor.firstName : '') + ' ' + ((b.indemnitor && b.indemnitor.lastName) ? b.indemnitor.lastName : '')).trim() || '—';
        var alerts = (b.alerts || []).length + (b.alert_count || 0);
        var alertBadge = alerts > 0 ? '<span style="background:var(--danger);color:#fff;border-radius:10px;padding:1px 6px;font-size:10px;margin-left:4px">' + alerts + '</span>' : '';
        var ins = (b.insurance_company || b.surety || '').toUpperCase();
        var insBadge = (ins.indexOf('PALM') >= 0 || ins.indexOf('PSC') >= 0)
          ? '<span style="font-size:10px;background:#166534;color:#86efac;padding:2px 6px;border-radius:4px">🌴 PSC</span>'
          : '<span style="font-size:10px;background:#1e3a5f;color:#93c5fd;padding:2px 6px;border-radius:4px">🛡️ OSI</span>';

        var bkSafe = (b.booking_number || '').replace(/'/g, "\\'");
        var nameSafe = (b.defendant_name || '').replace(/'/g, "\\'");
        var factorsSafe = encodeURIComponent(JSON.stringify(b.risk_factors || {}));
        var overdueLabel = overdue ? '<br><span style="color:var(--danger);font-size:10px">⚠️ ' + hoursOver + 'h overdue</span>' : '';

        return '<tr class="' + (overdue ? 'row-alert' : '') + '" style="' + (overdue ? 'background:rgba(239,68,68,0.05)' : '') + '">'
          + '<td><div style="font-weight:600">' + escHtml(b.defendant_name || '—') + alertBadge + '</div><div style="font-size:11px;color:var(--muted)">' + escHtml(b.booking_number || '—') + '</div></td>'
          + '<td>' + escHtml(b.county || '—') + '</td>'
          + '<td><strong>$' + (b.bond_amount || 0).toLocaleString() + '</strong></td>'
          + '<td>' + insBadge + '</td>'
          + '<td style="font-size:11px;max-width:120px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="' + escHtml(indemnitorName) + '">' + (indemnitorName && indemnitorName !== '—' ? '<a href="#" style="color:var(--accent);text-decoration:none" onclick="event.preventDefault();crossLinkToDefendants(\'' + escHtml(indemnitorName).replace(/'/g, "\\'") + '\')">' + escHtml(indemnitorName) + '</a>' : '—') + '</td>'
          + '<td style="font-size:11px;max-width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + escHtml(charges) + '</td>'
          + '<td><span class="score-pill ' + rCls + '" style="cursor:pointer" onclick="showRiskBreakdown(\'' + bkSafe + '\',\'' + nameSafe + '\',' + risk + ',\'' + factorsSafe + '\')">' + risk + ' ' + (risk >= 75 ? '🔴' : risk >= 50 ? '🟡' : '🟢') + '</span></td>'
          + '<td style="text-align:center">' + (typeof _abFtaBadge === 'function' ? _abFtaBadge(b) : '—') + '</td>'
          + '<td style="font-size:11px;white-space:nowrap">' + cdStr + '</td>'
          + '<td style="text-align:center">' + _courtCountdownBadge(days) + '</td>'
          + '<td>' + lastCI + overdueLabel + '</td>'
          + '<td><span class="status-badge ' + sCls + '">' + (b.status || 'active') + '</span></td>'
          + '<td><div style="display:flex;gap:4px;flex-wrap:wrap;min-width:280px">'
          + '<button class="btn-export" style="font-size:10px;padding:3px 8px;background:#7c3aed;color:#fff" onclick="openEditDrawer(\'' + bkSafe + '\')">✏️ Edit</button>'
          + '<button class="btn-export" style="font-size:10px;padding:3px 8px" onclick="openCheckinModal(\'' + bkSafe + '\',\'' + nameSafe + '\')">📍 Check-In</button>'
          + '<button class="btn-export" style="font-size:10px;padding:3px 8px" onclick="showLocationHistory(\'' + bkSafe + '\',\'' + nameSafe + '\')">🗺️ History</button>'
          + '<button class="btn-export" style="font-size:10px;padding:3px 8px;background:#3b82f6;color:#fff" onclick="openInTracking(\'' + bkSafe + '\')">📡 Track</button>'
          + '<button class="btn-export" style="font-size:10px;padding:3px 8px;background:var(--danger)" onclick="addManualAlert(\'' + bkSafe + '\',\'' + nameSafe + '\')">🚨 Alert</button>'
          + (b.status !== 'exonerated' ? '<button class="btn-export" style="font-size:10px;padding:3px 8px;background:#22c55e;color:#fff" onclick="exonerateFromActiveBonds(\'' + bkSafe + '\',\'' + nameSafe + '\')">✅ Exonerate</button>' : '')
          + '<button class="btn-export" style="font-size:10px;padding:3px 8px;background:#10b981;color:#fff" onclick="SLLifecycle&&SLLifecycle.open(\'' + bkSafe + '\',{name:\'' + nameSafe + '\'})">☘️ Lifecycle</button>'
          + '<select style="font-size:10px;padding:3px;background:var(--panel);border:1px solid var(--border);border-radius:4px;color:var(--text)" onchange="updateBondStatus(\'' + bkSafe + '\',this.value);this.value=\'\'"><option value="">Status…</option><option value="active">Active</option><option value="monitoring">Monitoring</option><option value="alert">Alert</option><option value="exonerated">Exonerated</option><option value="surrendered">Surrendered</option><option value="forfeited">Forfeited</option></select>'
          + '</div></td></tr>';
      }).join('');
    };
  }

  /* ── Sort ────────────────────────────────────────────────────── */
  function sortTable(key) {
    if (_sortKey === key) { _sortDir *= -1; } else { _sortKey = key; _sortDir = (key === 'court_days' || key === 'court_date') ? 1 : -1; }
    renderActiveBondsTable();
  }

  /* ── CSV Export ──────────────────────────────────────────────── */
  function exportCSV() {
    var bonds = window._abBonds || [];
    if (!bonds.length) { toast('No bonds to export', 'info'); return; }
    var headers = ['Booking Number', 'Defendant Name', 'County', 'Bond Amount', 'Surety', 'Indemnitor Name', 'Indemnitor Phone', 'Charges', 'Risk Score', 'FTA Risk Level', 'FTA Risk Score', 'Court Date', 'Days to Court', 'Status', 'Last Check-In', 'Case Number'];
    var rows = bonds.map(function (b) {
      var days = _courtDays(b);
      var cdStr = '';
      if (b.court_date) { try { cdStr = new Date(b.court_date).toLocaleDateString(); } catch (e) { cdStr = b.court_date; } }
      var indName = ((b.indemnitor && b.indemnitor.name) ? b.indemnitor.name : '') || b.indemnitor_name || '';
      var indPhone = ((b.indemnitor && b.indemnitor.phone) ? b.indemnitor.phone : '') || b.indemnitor_phone || '';
      var rawCharges = b.charges_raw || b.charges;
      var charges = typeof rawCharges === 'string' ? rawCharges : (Array.isArray(rawCharges) ? rawCharges.join('; ') : '');
      return [b.booking_number || '', b.defendant_name || '', b.county || '', b.bond_amount || 0, b.insurance_company || b.surety || '', indName, indPhone, charges, b.risk_score || 0, b.fta_risk_level || '', b.fta_risk_score || '', cdStr, days !== null ? days : '', b.status || '', b.last_check_in ? new Date(b.last_check_in).toLocaleString() : '', b.case_number || ''].map(function (v) { return '"' + String(v).replace(/"/g, '""') + '"'; }).join(',');
    });
    var csv = [headers.map(function (h) { return '"' + h + '"'; }).join(',')].concat(rows).join('\n');
    var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'shamrock-active-bonds-' + new Date().toISOString().slice(0, 10) + '.csv';
    a.click();
    URL.revokeObjectURL(url);
    toast('📥 Exported ' + bonds.length + ' bonds to CSV', 'success');
  }

  /* ── Bulk Exonerate ──────────────────────────────────────────── */
  function openBulkExonerate() {
    var bonds = (window._abBonds || []).filter(function (b) { return b.status !== 'exonerated'; });
    var listEl = document.getElementById('abBulkExonList');
    if (!listEl) return;
    if (!bonds.length) { toast('No active bonds to exonerate', 'info'); return; }
    listEl.innerHTML = '<label style="display:flex;align-items:center;gap:8px;padding:6px 8px;font-size:11px;color:var(--muted);border-bottom:2px solid var(--border)"><input type="checkbox" id="abBulkExonSelectAll" style="width:14px;height:14px"> Select All</label>'
      + bonds.map(function (b) {
        var days = _courtDays(b);
        return '<label style="display:flex;align-items:center;gap:10px;padding:8px;border-bottom:1px solid var(--border);cursor:pointer;font-size:12px"><input type="checkbox" class="bulk-exon-chk" value="' + escHtml(b.booking_number || '') + '" style="width:16px;height:16px;flex-shrink:0"><span style="flex:1"><strong>' + escHtml(b.defendant_name || '—') + '</strong><span style="color:var(--muted);margin-left:6px">' + escHtml(b.booking_number || '') + '</span></span><span style="color:var(--muted)">' + escHtml(b.county || '') + '</span><span style="font-weight:600">$' + (b.bond_amount || 0).toLocaleString() + '</span><span>' + (days !== null ? _courtCountdownBadge(days) : '<span style="color:var(--muted)">—</span>') + '</span></label>';
      }).join('');
    var saEl = document.getElementById('abBulkExonSelectAll');
    if (saEl) saEl.addEventListener('change', function () { document.querySelectorAll('.bulk-exon-chk').forEach(function (c) { c.checked = saEl.checked; }); });
    var modal = document.getElementById('abBulkExonModal');
    if (modal) modal.style.display = 'flex';
  }

  function submitBulkExonerate() {
    var checked = Array.from(document.querySelectorAll('.bulk-exon-chk:checked')).map(function (c) { return c.value; });
    if (!checked.length) { toast('Select at least one bond', 'info'); return; }
    var noteEl = document.getElementById('abBulkExonNote');
    var note = noteEl ? noteEl.value : 'Bulk exoneration from Active Bonds tab';
    var notifyEl = document.getElementById('abBulkExonNotify');
    var notifyIndem = notifyEl ? notifyEl.checked : false;
    fetch('/api/active-bonds/bulk-exonerate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ booking_numbers: checked, note: note || 'Bulk exoneration', notify_indemnitor: notifyIndem }),
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data.success) {
        toast('✅ Exonerated ' + (data.exonerated || checked.length) + ' bonds', 'success');
        var modal = document.getElementById('abBulkExonModal');
        if (modal) modal.style.display = 'none';
        loadActiveBonds();
        if (window.SLTracking) SLTracking.refresh();
      } else {
        toast('❌ ' + (data.error || 'Bulk exoneration failed'), 'error');
      }
    }).catch(function () { toast('Network error during bulk exoneration', 'error'); });
  }

  /* ── Has-Indemnitor Filter ───────────────────────────────────── */
  window._abHasIndemFilter = false;
  function toggleHasIndemFilter(btn) {
    window._abHasIndemFilter = !window._abHasIndemFilter;
    if (btn) { btn.style.background = window._abHasIndemFilter ? 'var(--accent)' : ''; btn.style.color = window._abHasIndemFilter ? '#fff' : ''; }
    renderActiveBondsTable();
  }

  /* ── Duplicate Phone Detection ───────────────────────────────── */
  function detectDuplicatePhones() {
    var bonds = window._abBonds || [];
    var phoneMap = {};
    bonds.forEach(function (b) {
      var phone = (((b.indemnitor && b.indemnitor.phone) ? b.indemnitor.phone : '') || b.indemnitor_phone || '').replace(/\D/g, '');
      if (!phone) return;
      if (!phoneMap[phone]) phoneMap[phone] = [];
      phoneMap[phone].push(b.defendant_name || b.booking_number || '?');
    });
    var dupes = Object.keys(phoneMap).filter(function (p) { return phoneMap[p].length > 1; });
    if (!dupes.length) { toast('✅ No duplicate indemnitor phones found', 'success'); return; }
    var msg = dupes.map(function (p) { return '📞 ' + p.replace(/(\d{3})(\d{3})(\d{4})/, '($1) $2-$3') + ': ' + phoneMap[p].join(', '); }).join('\n');
    alert('⚠️ Duplicate Indemnitor Phones Detected:\n\n' + msg);
  }

  /* ── Inject filter chips ─────────────────────────────────────── */
  function _injectFilterChips() {
    var filterBar = document.querySelector('#tabActiveBonds .filters');
    if (!filterBar || document.getElementById('abHasIndemChip')) return;
    var chip = document.createElement('button');
    chip.id = 'abHasIndemChip'; chip.className = 'btn-export';
    chip.style.cssText = 'font-size:12px;padding:6px 12px;border-radius:20px;border:1px solid var(--border)';
    chip.textContent = '🤝 Has Indemnitor'; chip.title = 'Show only bonds with an indemnitor on file';
    chip.onclick = function () { toggleHasIndemFilter(chip); };
    var dupeBtn = document.createElement('button');
    dupeBtn.id = 'abDupePhoneBtn'; dupeBtn.className = 'btn-export';
    dupeBtn.style.cssText = 'font-size:12px;padding:6px 12px';
    dupeBtn.textContent = '🔍 Dupe Phones'; dupeBtn.title = 'Detect duplicate indemnitor phone numbers';
    dupeBtn.onclick = detectDuplicatePhones;
    filterBar.appendChild(chip); filterBar.appendChild(dupeBtn);
  }

  /* ── Indemnitor Cross-Link ───────────────────────────────────── */
  function openIndemInDefendants(indemnitorName, indemnitorPhone) {
    // Navigate to Defendants tab and pre-fill the search with indemnitor name/phone
    var defBtn = document.querySelector('[data-tab="tabDefendants"]');
    if (!defBtn) { toast('Defendants tab not found', 'error'); return; }
    defBtn.click();
    setTimeout(function () {
      var searchEl = document.getElementById('defSearch') || document.getElementById('defendantSearch');
      if (searchEl) {
        searchEl.value = indemnitorPhone || indemnitorName || '';
        var ev = new Event('input', { bubbles: true });
        searchEl.dispatchEvent(ev);
      }
    }, 300);
  }

  /* ── Public API ──────────────────────────────────────────────── */
  window.SLActiveBonds = Object.assign(window.SLActiveBonds || {}, {
    sortTable: sortTable,
    exportCSV: exportCSV,
    openBulkExonerate: openBulkExonerate,
    submitBulkExonerate: submitBulkExonerate,
    toggleHasIndemFilter: toggleHasIndemFilter,
    detectDuplicatePhones: detectDuplicatePhones,
    openIndemInDefendants: openIndemInDefendants,
    openAddModal: function () { var m = document.getElementById('abAddBondModal'); if (m) m.style.display = 'flex'; },
    closeAddModal: function () { var m = document.getElementById('abAddBondModal'); if (m) m.style.display = 'none'; },
    closeEditDrawer: function () { if (typeof closeEditDrawer === 'function') closeEditDrawer(); },
  });

  /* ── Install override after DOM ready ────────────────────────── */
  function _init() {
    _installRenderOverride();
    _injectFilterChips();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    setTimeout(_init, 300);
  }
})();
