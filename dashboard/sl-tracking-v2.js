/* ShamrockLeads — Bond Tracking Module (Phase 3 — Full Sync)
   ─────────────────────────────────────────────────────────────────────────
   Synced with Active Bonds:
   • map-data pulls merged location_history from all 3 sources
   • Live search by defendant name / booking / case number
   • Rich Leaflet map markers with location trail polylines
   • Detail drawer: full history, geo pings, check-ins, court dates
   • Exoneration panel — manual exonerate + auto-exonerate log
   • Send Geo Link button (fresh GPS capture link via iMessage)
   • SSE: bond_written refreshes map, bond_exonerated removes from map
   ─────────────────────────────────────────────────────────────────────────
*/
const SLTracking = (() => {
  'use strict';

  let _map = null;
  let _markers = {};
  let _trails = {};
  let _data = [];
  let _filtered = [];
  let _view = 'map';
  let _initialized = false;
  let _searchTimeout = null;
  let _selectedBooking = null;
  let _exonerationLog = [];

  const API = window.API_BASE || '';

  function riskClass(s) { return s >= 75 ? 'score-hot' : s >= 50 ? 'score-warm' : 'score-cold'; }
  function riskColor(s) { return s >= 75 ? '#ef4444' : s >= 50 ? '#f59e0b' : '#22c55e'; }
  function riskLabel(s) { return s >= 75 ? 'HIGH' : s >= 50 ? 'MED' : 'LOW'; }
  function statusColor(st) {
    switch ((st||'').toLowerCase()) {
      case 'alert': return '#ef4444';
      case 'active': return '#22c55e';
      case 'monitoring': return '#f59e0b';
      case 'exonerated': return '#6366f1';
      default: return '#64748b';
    }
  }
  function timeAgo(ts) {
    if (!ts) return '—';
    const m = Math.floor((Date.now() - new Date(ts).getTime()) / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return m + 'm ago';
    const h = Math.floor(m / 60);
    if (h < 24) return h + 'h ago';
    return Math.floor(h / 24) + 'd ago';
  }
  function esc(s) { return (s||'').replace(/'/g, "\\'"); }

  async function init() {
    if (_initialized) { await refresh(); return; }
    _initialized = true;
    const searchEl = document.getElementById('trkSearch');
    if (searchEl) {
      searchEl.addEventListener('input', () => {
        clearTimeout(_searchTimeout);
        _searchTimeout = setTimeout(() => _applySearch(searchEl.value), 300);
      });
    }
    document.querySelectorAll('[data-trk-filter]').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('[data-trk-filter]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        _applySearch(searchEl ? searchEl.value : '');
      });
    });
    await refresh();
    await _loadExonerationLog();
  }

  async function refresh() {
    try {
      const r = await fetch(API + '/api/tracking/map-data');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const json = await r.json();
      _data = json.defendants || [];
      const s = json.summary || {};
      _setKpi('trkKpiTotal', s.total_active);
      _setKpi('trkKpiOverdue', s.overdue, s.overdue > 0 ? 'var(--danger)' : null);
      _setKpi('trkKpiHighRisk', s.high_risk, s.high_risk > 0 ? 'var(--gold)' : null);
      _setKpi('trkKpiOutOfArea', s.out_of_area);
      const badge = document.getElementById('trackingBadge');
      if (badge) badge.textContent = s.total_active || 0;
      _applySearch((document.getElementById('trkSearch')||{}).value || '');
    } catch (e) {
      console.error('[SLTracking] refresh error:', e);
      if (window.SL && SL.toast) SL.toast('Tracking refresh failed', 'error');
    }
  }

  function _setKpi(id, val, color) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = val != null ? val : '—';
    if (color) el.style.color = color;
  }

  function _applySearch(q) {
    const filterBtn = document.querySelector('[data-trk-filter].active');
    const filterVal = filterBtn ? filterBtn.dataset.trkFilter : 'all';
    let list = _data.slice();
    if (filterVal === 'overdue') list = list.filter(d => d.check_in_overdue);
    else if (filterVal === 'high_risk') list = list.filter(d => (d.risk_score||0) >= 75);
    else if (filterVal === 'alert') list = list.filter(d => d.status === 'alert');
    else if (filterVal === 'no_location') list = list.filter(d => !d.latest_location);
    if (q && q.trim()) {
      const lq = q.toLowerCase();
      list = list.filter(d =>
        (d.defendant_name||'').toLowerCase().includes(lq) ||
        (d.booking_number||'').toLowerCase().includes(lq) ||
        (d.case_number||'').toLowerCase().includes(lq) ||
        (d.county||'').toLowerCase().includes(lq)
      );
    }
    _filtered = list;
    if (_view === 'map') _renderMap(); else _renderList();
  }

  function _renderMap() {
    const container = document.getElementById('trkMap');
    if (!container) return;
    if (!_map) {
      if (typeof L === 'undefined') { _loadLeaflet(_renderMap); return; }
      _map = L.map('trkMap', { zoomControl: true }).setView([26.6, -81.8], 9);
      L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO', maxZoom: 19,
      }).addTo(_map);
    }
    const cur = new Set(_filtered.map(d => d.booking_number));
    Object.keys(_markers).forEach(bk => { if (!cur.has(bk)) { _map.removeLayer(_markers[bk]); delete _markers[bk]; }});
    Object.keys(_trails).forEach(bk => { if (!cur.has(bk)) { _map.removeLayer(_trails[bk]); delete _trails[bk]; }});
    let hasLoc = false;
    _filtered.forEach(d => {
      const loc = d.latest_location;
      if (!loc || loc.lat == null || loc.lng == null) return;
      hasLoc = true;
      const lat = parseFloat(loc.lat), lng = parseFloat(loc.lng);
      const risk = d.risk_score || 0;
      const color = riskColor(risk);
      const isOverdue = d.check_in_overdue;
      const history = (d.location_history||[]).filter(h => h.lat != null && h.lng != null);
      if (history.length > 1) {
        const lls = history.map(h => [parseFloat(h.lat), parseFloat(h.lng)]);
        if (_trails[d.booking_number]) _trails[d.booking_number].setLatLngs(lls);
        else _trails[d.booking_number] = L.polyline(lls, { color, weight: 2, opacity: 0.5, dashArray: '4 4' }).addTo(_map);
      }
      const icon = L.divIcon({
        className: '',
        html: '<div style="width:32px;height:32px;border-radius:50%;background:' + color +
              ';border:3px solid ' + (isOverdue ? '#ef4444' : '#fff') +
              ';display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.5)">' + risk + '</div>',
        iconSize: [32,32], iconAnchor: [16,16],
      });
      if (_markers[d.booking_number]) {
        _markers[d.booking_number].setLatLng([lat,lng]).setIcon(icon);
      } else {
        const m = L.marker([lat,lng],{icon}).addTo(_map).bindPopup(_buildPopup(d),{maxWidth:320});
        m.on('click', () => openDetail(d.booking_number));
        _markers[d.booking_number] = m;
      }
    });
    if (hasLoc && Object.keys(_markers).length > 0) {
      try { _map.fitBounds(L.featureGroup(Object.values(_markers)).getBounds().pad(0.2)); } catch(e){}
    }
    const noLoc = _filtered.filter(d => !d.latest_location).length;
    const noLocEl = document.getElementById('trkNoLocationCount');
    if (noLocEl) noLocEl.textContent = noLoc > 0 ? noLoc + ' bond(s) have no location data yet' : '';
  }

  function _buildPopup(d) {
    const loc = d.latest_location || {};
    const ts = loc.ts || loc.timestamp || '';
    return '<div style="font-family:var(--font,sans-serif);min-width:220px">' +
      '<div style="font-weight:700;font-size:14px;margin-bottom:4px">' + (d.defendant_name||'—') + '</div>' +
      '<div style="font-size:11px;color:#94a3b8;margin-bottom:8px">' + (d.booking_number||'') + ' · ' + (d.county||'—') + '</div>' +
      '<div style="display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap">' +
        '<span style="background:' + riskColor(d.risk_score||0) + ';color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700">' + riskLabel(d.risk_score||0) + '</span>' +
        '<span style="background:' + statusColor(d.status) + ';color:#fff;padding:2px 8px;border-radius:10px;font-size:11px">' + (d.status||'').toUpperCase() + '</span>' +
        (d.check_in_overdue ? '<span style="background:#ef4444;color:#fff;padding:2px 8px;border-radius:10px;font-size:11px">OVERDUE</span>' : '') +
      '</div>' +
      '<div style="font-size:12px;margin-bottom:4px">Last ping: ' + (ts ? timeAgo(ts) : 'unknown') + '</div>' +
      '<div style="font-size:12px;margin-bottom:4px">Bond: $' + (d.bond_amount||0).toLocaleString() + '</div>' +
      '<div style="font-size:12px;margin-bottom:8px">' + (d.location_count||0) + ' total pings</div>' +
      '<button onclick="SLTracking.openDetail(\'' + esc(d.booking_number) + '\');this.closest(\'.leaflet-popup\').style.display=\'none\'" ' +
        'style="width:100%;padding:6px;background:#22c55e;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600">' +
        'View Full Detail</button></div>';
  }

  function _loadLeaflet(cb) {
    if (document.getElementById('leaflet-css')) { cb(); return; }
    const css = document.createElement('link');
    css.id = 'leaflet-css'; css.rel = 'stylesheet';
    css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
    document.head.appendChild(css);
    const js = document.createElement('script');
    js.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    js.onload = cb;
    document.head.appendChild(js);
  }

  function _renderList() {
    const tbody = document.getElementById('trkListBody');
    if (!tbody) return;
    if (!_filtered.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="loading">No defendants match current filter.</td></tr>';
      return;
    }
    tbody.innerHTML = _filtered.map(d => {
      const risk = d.risk_score || 0;
      const isOverdue = d.check_in_overdue;
      const locCount = d.location_count || 0;
      const lastLoc = d.latest_location;
      const lastLocTs = lastLoc ? (lastLoc.ts || lastLoc.timestamp || '') : '';
      return '<tr class="' + (isOverdue ? 'row-alert' : '') + '">' +
        '<td><div style="font-weight:600">' + (d.defendant_name||'—') + '</div>' +
             '<div style="font-size:11px;color:var(--muted)">' + (d.booking_number||'—') + '</div></td>' +
        '<td>' + (d.county||'—') + '</td>' +
        '<td>$' + (d.bond_amount||0).toLocaleString() + '</td>' +
        '<td><span class="score-pill ' + riskClass(risk) + '">' + risk + '</span></td>' +
        '<td>' + (lastLocTs ? '<span style="font-size:12px">' + timeAgo(lastLocTs) + '</span>' : '<span style="color:var(--muted);font-size:12px">No location</span>') +
          '<div style="font-size:10px;color:var(--muted)">' + locCount + ' ping' + (locCount !== 1 ? 's' : '') + '</div></td>' +
        '<td style="' + (isOverdue ? 'color:var(--danger);font-weight:700' : '') + '">' +
          (d.next_check_in_due ? new Date(d.next_check_in_due).toLocaleDateString('en-US',{month:'short',day:'numeric'}) : '—') +
          (isOverdue ? '<br><span style="font-size:10px">OVERDUE</span>' : '') + '</td>' +
        '<td><span style="background:' + statusColor(d.status) + '20;color:' + statusColor(d.status) + ';border:1px solid ' + statusColor(d.status) + '40;padding:2px 8px;border-radius:10px;font-size:11px">' + (d.status||'—').toUpperCase() + '</span></td>' +
        '<td>' + (d.missed_check_ins||0) + '</td>' +
        '<td><div style="display:flex;gap:4px;flex-wrap:wrap">' +
          '<button class="btn-sm" onclick="SLTracking.openDetail(\'' + esc(d.booking_number) + '\')">Detail</button>' +
          '<button class="btn-sm btn-primary" onclick="SLTracking.sendGeoLink(\'' + esc(d.booking_number) + '\',\'' + esc(d.defendant_name) + '\')">Ping</button>' +
          '<button class="btn-sm btn-danger" onclick="SLTracking.confirmExonerate(\'' + esc(d.booking_number) + '\',\'' + esc(d.defendant_name) + '\')">Exonerate</button>' +
        '</div></td></tr>';
    }).join('');
  }

  function showView(v) {
    _view = v;
    const mapView = document.getElementById('trkMapView');
    const listView = document.getElementById('trkListView');
    const btnMap = document.getElementById('trkBtnMap');
    const btnList = document.getElementById('trkBtnList');
    if (v === 'map') {
      if (mapView) mapView.style.display = '';
      if (listView) listView.style.display = 'none';
      if (btnMap) btnMap.classList.add('active');
      if (btnList) btnList.classList.remove('active');
      setTimeout(() => { if (_map) _map.invalidateSize(); }, 50);
      _renderMap();
    } else {
      if (mapView) mapView.style.display = 'none';
      if (listView) listView.style.display = '';
      if (btnMap) btnMap.classList.remove('active');
      if (btnList) btnList.classList.add('active');
      _renderList();
    }
  }

  async function openDetail(bookingNumber) {
    _selectedBooking = bookingNumber;
    const panel = document.getElementById('trkDetailPanel');
    const title = document.getElementById('trkDetailTitle');
    const body = document.getElementById('trkDetailBody');
    if (!panel || !body) return;
    panel.style.display = 'block';
    body.innerHTML = '<div class="loading" style="padding:24px">Loading defendant detail…</div>';
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    try {
      const r = await fetch(API + '/api/tracking/' + encodeURIComponent(bookingNumber) + '/history');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      if (title) title.textContent = 'Location Detail: ' + (data.defendant_name || bookingNumber);
      const history = (data.location_history||[]).slice(0,30);
      const alerts = (data.alerts||[]).slice().reverse();
      const courtDates = data.court_dates || [];
      const isExonerated = data.status === 'exonerated';
      const bkSafe = esc(bookingNumber);
      const nameSafe = esc(data.defendant_name||'');

      const locHtml = history.length
        ? '<div class="table-wrap"><table>' +
            '<thead><tr><th>Timestamp</th><th>Lat</th><th>Lng</th><th>Source</th><th>Accuracy</th><th>Notes</th></tr></thead>' +
            '<tbody>' + history.map(h =>
              '<tr><td>' + (h.timestamp ? new Date(h.timestamp).toLocaleString() : '—') + '</td>' +
              '<td>' + (h.lat != null ? parseFloat(h.lat).toFixed(5) : '—') + '</td>' +
              '<td>' + (h.lng != null ? parseFloat(h.lng).toFixed(5) : '—') + '</td>' +
              '<td><span style="font-size:11px;background:var(--panel);padding:2px 6px;border-radius:4px">' + (h.source||'—') + '</span></td>' +
              '<td>' + (h.accuracy ? Math.round(h.accuracy) + 'm' : '—') + '</td>' +
              '<td style="font-size:11px;color:var(--muted)">' + (h.notes||'') + '</td></tr>'
            ).join('') + '</tbody></table></div>'
        : '<p style="color:var(--muted);padding:8px 0">No location data recorded yet. Send a geo link to capture location.</p>';

      const alertsHtml = alerts.length
        ? alerts.map(a =>
            '<div style="padding:8px 12px;margin:4px 0;background:var(--panel);border-radius:6px;border-left:3px solid ' +
            (a.severity==='high'||a.type==='missed_check_in' ? 'var(--danger)' : 'var(--warning)') + '">' +
            '<div style="font-size:12px;font-weight:600">' + (a.type||'ALERT').replace(/_/g,' ').toUpperCase() +
            ' <span style="font-size:10px;color:var(--muted)">' + (a.timestamp ? new Date(a.timestamp).toLocaleString() : '') + '</span></div>' +
            '<div style="font-size:11px;color:var(--muted)">' + (a.message||'') + '</div></div>'
          ).join('')
        : '<p style="color:var(--muted);font-size:13px">No alerts.</p>';

      const courtHtml = courtDates.length
        ? courtDates.map(cd =>
            '<div style="padding:8px 12px;margin:4px 0;background:var(--panel);border-radius:6px">' +
            '<div style="font-size:12px;font-weight:600">Court: ' + (cd.court_location||'—') + ' — ' +
            (cd.send_at ? new Date(cd.send_at).toLocaleDateString() : '—') + '</div>' +
            '<div style="font-size:11px;color:var(--muted)">' + (cd.status||'') + '</div></div>'
          ).join('')
        : '<p style="color:var(--muted);font-size:13px">No court dates scheduled.</p>';

      body.innerHTML =
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:18px">' +
          _kpiCard('Status', '<span style="color:' + statusColor(data.status) + '">' + (data.status||'—').toUpperCase() + '</span>') +
          _kpiCard('Bond Amount', '$' + (data.bond_amount||0).toLocaleString()) +
          _kpiCard('County', data.county||'—') +
          _kpiCard('Risk Score', '<span class="score-pill ' + riskClass(data.risk_score||0) + '">' + (data.risk_score||0) + '</span>') +
          _kpiCard('Location Pings', data.location_count||0) +
          _kpiCard('Check-In Freq', (data.check_in_frequency_days||30) + 'd') +
        '</div>' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:18px">' +
          '<div class="panel" style="padding:14px">' +
            '<div class="panel-title" style="margin-bottom:8px">Indemnitor</div>' +
            '<div style="font-size:13px">' + (data.indemnitor_name||'—') + '</div>' +
            '<div style="font-size:12px;color:var(--muted)">' + (data.indemnitor_phone||'No phone') + '</div>' +
            (data.indemnitor_phone ? '<button class="btn-sm btn-primary" style="margin-top:8px" onclick="SLTracking.sendGeoLink(\'' + bkSafe + '\',\'' + nameSafe + '\',\'' + esc(data.indemnitor_phone) + '\')">Send Geo Link</button>' : '') +
          '</div>' +
          '<div class="panel" style="padding:14px">' +
            '<div class="panel-title" style="margin-bottom:8px">Case Info</div>' +
            '<div style="font-size:13px">' + (data.case_number||'—') + '</div>' +
            '<div style="font-size:12px;color:var(--muted)">Booking: ' + bookingNumber + '</div>' +
            (data.exonerated_at ? '<div style="font-size:11px;color:var(--accent);margin-top:4px">Exonerated ' + new Date(data.exonerated_at).toLocaleDateString() + ' via ' + (data.exoneration_source||'—') + '</div>' : '') +
          '</div>' +
        '</div>' +
        '<div class="panel" style="padding:14px;margin-bottom:18px">' +
          '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
            '<div class="panel-title">Location History (' + (data.location_count||0) + ' pings)</div>' +
            '<button class="btn-sm btn-primary" onclick="SLTracking.sendGeoLink(\'' + bkSafe + '\',\'' + nameSafe + '\')">Send New Geo Link</button>' +
          '</div>' + locHtml + '</div>' +
        '<div class="panel" style="padding:14px;margin-bottom:18px">' +
          '<div class="panel-title" style="margin-bottom:8px">Court Dates (' + courtDates.length + ')</div>' + courtHtml + '</div>' +
        '<div class="panel" style="padding:14px;margin-bottom:18px">' +
          '<div class="panel-title" style="margin-bottom:8px">Alerts (' + alerts.length + ')</div>' + alertsHtml + '</div>' +
        (!isExonerated
          ? '<div class="panel" style="padding:14px;margin-bottom:18px"><div class="panel-title" style="margin-bottom:12px">Actions</div>' +
              '<div style="display:flex;gap:8px;flex-wrap:wrap">' +
                '<button class="btn-primary" onclick="SLTracking.sendGeoLink(\'' + bkSafe + '\',\'' + nameSafe + '\')">Send Geo Link</button>' +
                '<button class="btn-export" onclick="SLTracking.scheduleReminders()">Schedule Reminders</button>' +
                '<button class="btn-export" onclick="SLTracking.discoverContacts(\'' + bkSafe + '\',\'' + nameSafe + '\')">Discover Contacts</button>' +
                '<button class="btn-danger" onclick="SLTracking.confirmExonerate(\'' + bkSafe + '\',\'' + nameSafe + '\')">Exonerate Bond</button>' +
              '</div></div>'
          : '<div class="panel" style="padding:14px;margin-bottom:18px;border-color:var(--accent)">' +
              '<div style="color:var(--accent);font-weight:600">This bond has been exonerated — tracking is stopped.</div>' +
              '<div style="font-size:12px;color:var(--muted);margin-top:4px">Exonerated ' +
                (data.exonerated_at ? new Date(data.exonerated_at).toLocaleString() : '—') +
                ' via ' + (data.exoneration_source||'—') +
                (data.exoneration_note ? ' — ' + data.exoneration_note : '') +
              '</div></div>') +
        '<div class="panel" style="padding:14px;margin-bottom:18px">' +
          '<div class="panel-title" style="margin-bottom:12px">Schedule Court Reminders (4-Touch SMS)</div>' +
          '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:14px">' +
            _inputField('crBookingNum', 'Booking Number', bookingNumber) +
            _inputField('crDefName', 'Defendant Name', data.defendant_name||'') +
            _inputField('crPhone', 'Phone Number', data.indemnitor_phone||'', '+12395551234') +
            _inputField('crCourtDate', 'Court Date', '', '', 'datetime-local') +
            _inputField('crCourtLocation', 'Court Location', data.county||'', 'e.g. Lee') +
            _inputField('crCaseNumber', 'Case Number', data.case_number||'', 'e.g. 25-CF-001234') +
          '</div>' +
          '<button class="btn-primary" onclick="SLTracking.scheduleReminders()">Schedule 4-Touch Reminders</button>' +
          '<div id="crResult" style="margin-top:10px;font-size:13px"></div>' +
        '</div>' +
        '<div id="trkContactResult_' + bookingNumber + '" style="margin-top:10px;font-size:13px"></div>';
    } catch (e) {
      body.innerHTML = '<div style="color:var(--danger);padding:12px">Error loading detail: ' + e.message + '</div>';
    }
  }

  function _kpiCard(label, value) {
    return '<div class="stat-card" style="padding:12px"><div class="stat-label">' + label + '</div>' +
           '<div style="font-weight:700;font-size:15px">' + value + '</div></div>';
  }
  function _inputField(id, label, value, placeholder, type) {
    return '<div><label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:4px">' + label + '</label>' +
      '<input type="' + (type||'text') + '" id="' + id + '" value="' + (value||'') + '" placeholder="' + (placeholder||'') + '" ' +
      'style="width:100%;padding:8px 12px;background:var(--input-bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:13px;font-family:inherit;box-sizing:border-box"></div>';
  }

  function closeDetail() {
    const panel = document.getElementById('trkDetailPanel');
    if (panel) panel.style.display = 'none';
    _selectedBooking = null;
  }

  async function sendGeoLink(bookingNumber, defName, phone) {
    if (!confirm('Send GPS capture link for ' + defName + '?')) return;
    try {
      const r = await fetch(API + '/api/tracking/' + encodeURIComponent(bookingNumber) + '/send-geo-link', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: phone || '', recipient: 'indemnitor' }),
      });
      const data = await r.json();
      if (data.success) { if (window.SL && SL.toast) SL.toast('Geo link sent via ' + (data.channel||'iMessage'), 'success'); }
      else { if (window.SL && SL.toast) SL.toast(data.error || 'Send failed', 'error'); }
    } catch (e) { if (window.SL && SL.toast) SL.toast('Network error', 'error'); }
  }

  async function confirmExonerate(bookingNumber, defName) {
    const note = prompt(
      'Exonerate bond for ' + defName + '?\n\nThis will stop all location tracking, cancel pending geo links, and cancel court reminders.\n\nEnter a note or leave blank:'
    );
    if (note === null) return;
    const notifyIndem = confirm('Notify indemnitor via iMessage that the bond is discharged?');
    try {
      const r = await fetch(API + '/api/tracking/' + encodeURIComponent(bookingNumber) + '/exonerate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: 'manual', note: note || 'Manual exoneration from dashboard', notify_indemnitor: notifyIndem }),
      });
      const data = await r.json();
      if (data.success) {
        if (window.SL && SL.toast) SL.toast(defName + ' exonerated — tracking stopped', 'success');
        closeDetail(); await refresh(); await _loadExonerationLog();
      } else { if (window.SL && SL.toast) SL.toast(data.error || 'Exoneration failed', 'error'); }
    } catch (e) { if (window.SL && SL.toast) SL.toast('Network error', 'error'); }
  }

  async function _loadExonerationLog() {
    try {
      const r = await fetch(API + '/api/tracking/exonerations?limit=20');
      if (!r.ok) return;
      const data = await r.json();
      _exonerationLog = data.exonerations || [];
      _renderExonerationLog();
    } catch (e) { console.warn('[SLTracking] exoneration log load failed:', e); }
  }

  function _renderExonerationLog() {
    const container = document.getElementById('trkExonerationLog');
    if (!container) return;
    if (!_exonerationLog.length) {
      container.innerHTML = '<p style="color:var(--muted);font-size:13px">No exonerations recorded yet.</p>';
      return;
    }
    container.innerHTML = _exonerationLog.map(e =>
      '<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 12px;background:var(--panel);border-radius:8px;margin-bottom:6px;border-left:3px solid var(--accent)">' +
        '<div><div style="font-weight:600;font-size:13px">' + (e.defendant_name||'—') + '</div>' +
          '<div style="font-size:11px;color:var(--muted)">' + (e.entity_id||'—') + ' · Case: ' + (e.case_number||'—') + '</div>' +
          (e.note ? '<div style="font-size:11px;color:var(--muted);margin-top:2px">' + e.note + '</div>' : '') +
        '</div>' +
        '<div style="text-align:right;font-size:11px;color:var(--muted)">' +
          '<div>' + (e.source === 'court_email' ? 'Court Email' : 'Manual') + '</div>' +
          '<div>' + (e.exonerated_at ? new Date(e.exonerated_at).toLocaleDateString() : '—') + '</div>' +
        '</div></div>'
    ).join('');
  }

  async function scheduleReminders() {
    const get = id => ((document.getElementById(id)||{}).value||'').trim();
    const bookingNumber = get('crBookingNum'), defName = get('crDefName'), phone = get('crPhone');
    const courtDate = get('crCourtDate'), courtLocation = get('crCourtLocation'), caseNumber = get('crCaseNumber');
    const resultEl = document.getElementById('crResult');
    if (!bookingNumber || !defName || !phone || !courtDate || !courtLocation || !caseNumber) {
      if (resultEl) resultEl.innerHTML = '<span style="color:var(--danger)">Please fill in all fields.</span>';
      return;
    }
    if (resultEl) resultEl.innerHTML = '<span style="color:var(--muted)">Scheduling…</span>';
    try {
      const resp = await fetch('/api/court-reminders/schedule', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_number: bookingNumber, defendant_name: defName, phone, court_date: new Date(courtDate).toISOString(), court_location: courtLocation, case_number: caseNumber }),
      });
      const data = await resp.json();
      if (data.error) throw new Error(data.error);
      if (resultEl) resultEl.innerHTML = '<span style="color:var(--accent)">Scheduled ' + (data.scheduled_count||4) + ' reminders (7d, 3d, 1d, morning-of)</span>';
      if (window.SL && SL.toast) SL.toast('Court reminders scheduled', 'success');
    } catch (e) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--danger)">Error: ' + e.message + '</span>'; }
  }

  async function discoverContacts(bookingNumber, defName) {
    const resultEl = document.getElementById('trkContactResult_' + bookingNumber);
    if (resultEl) resultEl.innerHTML = '<span style="color:var(--muted)">Running discovery…</span>';
    try {
      const resp = await fetch('/api/contacts/discover', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_number: bookingNumber, full_name: defName }),
      });
      const data = await resp.json();
      if (data.error) throw new Error(data.error);
      const contacts = data.contacts || [];
      if (!contacts.length) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--muted)">No contacts discovered.</span>'; return; }
      const html = contacts.map(c =>
        '<div style="padding:8px 12px;background:var(--panel);border:1px solid var(--border);border-radius:8px;margin-bottom:6px">' +
          '<div style="font-weight:600;font-size:13px">' + (c.name||'—') + '</div>' +
          '<div style="font-size:12px;color:var(--muted)">' + (c.relationship||'—') + ' · ' + (c.source||'—') + ' · Confidence: ' + Math.round((c.confidence||0)*100) + '%</div>' +
          (c.phone ? '<div style="font-size:12px">' + c.phone + '</div>' : '') +
          (c.address ? '<div style="font-size:12px">' + c.address + '</div>' : '') +
        '</div>'
      ).join('');
      if (resultEl) resultEl.innerHTML = '<div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">Discovered Contacts (' + contacts.length + ')</div>' + html;
    } catch (e) { if (resultEl) resultEl.innerHTML = '<span style="color:var(--danger)">Error: ' + e.message + '</span>'; }
  }

  function onBondWritten(payload) { if (_initialized) refresh(); }

  function onBondExonerated(payload) {
    const bk = payload.booking_number;
    if (_markers[bk]) { _map && _map.removeLayer(_markers[bk]); delete _markers[bk]; }
    if (_trails[bk])  { _map && _map.removeLayer(_trails[bk]);  delete _trails[bk];  }
    _data     = _data.filter(d => d.booking_number !== bk);
    _filtered = _filtered.filter(d => d.booking_number !== bk);
    if (_view === 'map') _renderMap(); else _renderList();
    _loadExonerationLog();
    if (window.SL && SL.toast) SL.toast((payload.defendant_name||bk) + ' exonerated — tracking stopped', 'success');
    if (_selectedBooking === bk) closeDetail();
  }

  return { init, refresh, showView, openDetail, closeDetail, sendGeoLink, confirmExonerate, scheduleReminders, discoverContacts, onBondWritten, onBondExonerated };
})();
