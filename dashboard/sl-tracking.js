/* ShamrockLeads — Bond Tracking Module v4 (Fully Wired & Intuitive)
   ─────────────────────────────────────────────────────────────────────────
   All buttons wired to correct API endpoints. All functions work.
   • /api/tracking/map-data          — map + list with full location history
   • /api/tracking/<bk>/history      — detail drawer
   • /api/tracking/<bk>/exonerate    — stop tracking, cancel reminders, notify
   • /api/tracking/<bk>/send-geo-link — send GPS capture link via iMessage
   • /api/tracking/exonerations      — exoneration log panel
   • /api/court-reminders/schedule   — 4-touch SMS court reminder scheduler
   • /api/contacts/discover          — contact discovery for indemnitor
   ─────────────────────────────────────────────────────────────────────────
*/
const SLTracking = (() => {
  'use strict';

  // ── State ─────────────────────────────────────────────────────────────────
  let _map          = null;
  let _markers      = {};
  let _trails       = {};
  let _data         = [];
  let _filtered     = [];
  let _view         = 'map';
  let _initialized  = false;
  let _searchTimer  = null;
  let _activeFilter = 'all';
  let _selectedBk   = null;
  let _exonLog      = [];

  const API = window.API_BASE || '';

  // ── Helpers ───────────────────────────────────────────────────────────────
  const riskClass  = s => s >= 75 ? 'score-hot' : s >= 50 ? 'score-warm' : 'score-cold';
  const riskColor  = s => s >= 75 ? '#ef4444'   : s >= 50 ? '#f59e0b'    : '#22c55e';
  const riskLabel  = s => s >= 75 ? 'HIGH'       : s >= 50 ? 'MED'        : 'LOW';
  const statusColor = st => {
    switch ((st || '').toLowerCase()) {
      case 'alert':      return '#ef4444';
      case 'active':     return '#22c55e';
      case 'monitoring': return '#f59e0b';
      case 'exonerated': return '#6366f1';
      default:           return '#64748b';
    }
  };
  function timeAgo(ts) {
    if (!ts) return '—';
    const m = Math.floor((Date.now() - new Date(ts).getTime()) / 60000);
    if (m < 1)  return 'just now';
    if (m < 60) return m + 'm ago';
    const h = Math.floor(m / 60);
    if (h < 24) return h + 'h ago';
    return Math.floor(h / 24) + 'd ago';
  }
  function fmtDate(ts) {
    if (!ts) return '—';
    return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }
  function fmtDateTime(ts) {
    if (!ts) return '—';
    return new Date(ts).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }
  function esc(s)    { return (s || '').replace(/'/g, "\\'"); }
  function escH(s)   { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function toast(msg, type) { if (window.SL && SL.toast) SL.toast(msg, type); }
  function getVal(id) { return ((document.getElementById(id) || {}).value || '').trim(); }
  function setEl(id, html) { const e = document.getElementById(id); if (e) e.innerHTML = html; }

  // ── Init ──────────────────────────────────────────────────────────────────
  async function init() {
    if (_initialized) { await refresh(); return; }
    _initialized = true;

    // Search box
    const searchEl = document.getElementById('trkSearch');
    if (searchEl) {
      searchEl.addEventListener('input', () => {
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(_applyFilters, 300);
      });
    }

    // Filter buttons
    document.querySelectorAll('[data-trk-filter]').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('[data-trk-filter]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        _activeFilter = btn.dataset.trkFilter;
        _applyFilters();
      });
    });

    await refresh();
    await _loadExonLog();
  }

  // ── Data Refresh ──────────────────────────────────────────────────────────
  async function refresh() {
    try {
      const r = await fetch(API + '/api/tracking/map-data');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const json = await r.json();
      _data = json.defendants || [];
      const s = json.summary || {};

      // KPI cards
      _setKpi('trkKpiTotal',    s.total_active);
      _setKpi('trkKpiOverdue',  s.overdue,    s.overdue    > 0 ? 'var(--danger)' : null);
      _setKpi('trkKpiHighRisk', s.high_risk,  s.high_risk  > 0 ? 'var(--gold)'   : null);
      _setKpi('trkKpiOutOfArea', s.out_of_area);

      // Tab badge
      const badge = document.getElementById('trackingBadge');
      if (badge) badge.textContent = s.total_active || 0;

      // No-location warning
      const noLoc = _data.filter(d => !d.latest_location && !(d.location_count > 0)).length;
      setEl('trkNoLocationCount', noLoc > 0
        ? '<span style="color:var(--gold)">⚠ ' + noLoc + ' defendant' + (noLoc !== 1 ? 's' : '') + ' have no location data yet</span>'
        : '');

      _applyFilters();
    } catch (e) {
      console.error('[SLTracking] refresh error:', e);
      toast('Tracking data load failed: ' + e.message, 'error');
    }
  }

  function _setKpi(id, val, color) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = (val !== undefined && val !== null) ? val : '—';
    if (color) el.style.color = color;
  }

  // ── Filter + Search ───────────────────────────────────────────────────────
  function _applyFilters() {
    const q = ((document.getElementById('trkSearch') || {}).value || '').toLowerCase().trim();

    _filtered = _data.filter(d => {
      // Text search
      if (q) {
        const hay = [d.defendant_name, d.booking_number, d.case_number, d.county, d.indemnitor_name].join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      // Category filter
      switch (_activeFilter) {
        case 'overdue':     return !!d.check_in_overdue;
        case 'high_risk':   return (d.risk_score || 0) >= 75;
        case 'alert':       return (d.status || '').toLowerCase() === 'alert' || (d.alerts_count || 0) > 0;
        case 'no_location': return !d.latest_location && !(d.location_count > 0);
        default:            return true;
      }
    });

    if (_view === 'map') _renderMap(); else _renderList();
  }

  // ── Map View ──────────────────────────────────────────────────────────────
  function _renderMap() {
    const container = document.getElementById('trkMap');
    if (!container) return;
    if (typeof L === 'undefined') { _loadLeaflet(_renderMap); return; }

    if (!_map) {
      _map = L.map('trkMap', { zoomControl: true }).setView([26.6, -81.8], 9);
      L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '© OpenStreetMap contributors © CARTO', maxZoom: 19,
      }).addTo(_map);
    }

    // Remove stale markers/trails
    const cur = new Set(_filtered.map(d => d.booking_number));
    Object.keys(_markers).forEach(bk => { if (!cur.has(bk)) { _map.removeLayer(_markers[bk]); delete _markers[bk]; } });
    Object.keys(_trails).forEach(bk => { if (!cur.has(bk)) { _map.removeLayer(_trails[bk]); delete _trails[bk]; } });

    _filtered.forEach(d => {
      const loc = d.latest_location;
      if (!loc || loc.lat == null || loc.lng == null) return;
      const lat = parseFloat(loc.lat), lng = parseFloat(loc.lng);
      if (isNaN(lat) || isNaN(lng)) return;

      const risk = d.risk_score || 0;
      const color = riskColor(risk);
      const isOverdue = d.check_in_overdue;

      // Trail polyline
      const history = (d.location_history || []).filter(h => h.lat != null && h.lng != null);
      if (history.length > 1) {
        const lls = history.map(h => [parseFloat(h.lat), parseFloat(h.lng)]);
        if (_trails[d.booking_number]) _trails[d.booking_number].setLatLngs(lls);
        else _trails[d.booking_number] = L.polyline(lls, { color, weight: 2, opacity: 0.5, dashArray: '4 4' }).addTo(_map);
      }

      // Marker icon
      const icon = L.divIcon({
        className: '',
        html: '<div style="width:32px;height:32px;border-radius:50%;background:' + color +
              ';border:3px solid ' + (isOverdue ? '#ef4444' : '#fff') +
              ';display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.5)">' +
              risk + '</div>',
        iconSize: [32, 32], iconAnchor: [16, 16],
      });

      if (_markers[d.booking_number]) {
        _markers[d.booking_number].setLatLng([lat, lng]).setIcon(icon);
      } else {
        const m = L.marker([lat, lng], { icon }).addTo(_map).bindPopup(_buildPopup(d), { maxWidth: 320 });
        m.on('click', () => openDetail(d.booking_number));
        _markers[d.booking_number] = m;
      }
    });

    if (Object.keys(_markers).length > 0) {
      try { _map.fitBounds(L.featureGroup(Object.values(_markers)).getBounds().pad(0.2)); } catch (e) {}
    }
  }

  function _buildPopup(d) {
    const loc = d.latest_location || {};
    const ts = loc.ts || loc.timestamp || '';
    const bkSafe = esc(d.booking_number);
    return '<div style="font-family:var(--font,sans-serif);min-width:220px">' +
      '<div style="font-weight:700;font-size:14px;margin-bottom:4px">' + escH(d.defendant_name || '—') + '</div>' +
      '<div style="font-size:11px;color:#94a3b8;margin-bottom:8px">' + escH(d.booking_number || '') + ' · ' + escH(d.county || '—') + '</div>' +
      '<div style="display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap">' +
        '<span style="background:' + riskColor(d.risk_score || 0) + ';color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700">' + riskLabel(d.risk_score || 0) + '</span>' +
        '<span style="background:' + statusColor(d.status) + ';color:#fff;padding:2px 8px;border-radius:10px;font-size:11px">' + escH((d.status || '').toUpperCase()) + '</span>' +
        (d.check_in_overdue ? '<span style="background:#ef4444;color:#fff;padding:2px 8px;border-radius:10px;font-size:11px">OVERDUE</span>' : '') +
      '</div>' +
      '<div style="font-size:12px;margin-bottom:4px">Last ping: ' + (ts ? timeAgo(ts) : 'unknown') + '</div>' +
      '<div style="font-size:12px;margin-bottom:4px">Bond: $' + (d.bond_amount || 0).toLocaleString() + '</div>' +
      '<div style="font-size:12px;margin-bottom:8px">' + (d.location_count || 0) + ' total pings</div>' +
      '<button onclick="SLTracking.openDetail(\'' + bkSafe + '\')" ' +
        'style="width:100%;padding:6px;background:#22c55e;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600">' +
        '📋 View Full Detail</button>' +
      '</div>';
  }

  function _loadLeaflet(cb) {
    if (document.getElementById('leaflet-css')) { if (window.L) cb(); else { const s = document.createElement('script'); s.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'; s.onload = cb; document.head.appendChild(s); } return; }
    const css = document.createElement('link');
    css.id = 'leaflet-css'; css.rel = 'stylesheet';
    css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
    document.head.appendChild(css);
    const js = document.createElement('script');
    js.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    js.onload = cb;
    document.head.appendChild(js);
  }

  // ── List View ─────────────────────────────────────────────────────────────
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
      const latest = d.latest_location;
      const latestTs = latest ? (latest.ts || latest.timestamp || '') : '';
      const bkSafe = esc(d.booking_number);
      const nameSafe = esc(d.defendant_name);
      return '<tr class="' + (isOverdue ? 'row-alert' : '') + '">' +
        '<td>' +
          '<div style="font-weight:600">' + escH(d.defendant_name || '—') + '</div>' +
          '<div style="font-size:11px;color:var(--muted)">' + escH(d.booking_number || '—') + '</div>' +
          (d.case_number ? '<div style="font-size:10px;color:var(--muted)">Case: ' + escH(d.case_number) + '</div>' : '') +
        '</td>' +
        '<td>' + escH(d.county || '—') + '</td>' +
        '<td>$' + (d.bond_amount || 0).toLocaleString() + '</td>' +
        '<td><span class="score-pill ' + riskClass(risk) + '">' + risk + ' <small>' + riskLabel(risk) + '</small></span></td>' +
        '<td>' +
          (latestTs
            ? '<span style="font-size:12px">' + timeAgo(latestTs) + '</span><div style="font-size:10px;color:var(--muted)">' + locCount + ' ping' + (locCount !== 1 ? 's' : '') + '</div>'
            : '<span style="color:var(--muted);font-size:12px">📵 No location</span>') +
        '</td>' +
        '<td style="' + (isOverdue ? 'color:var(--danger);font-weight:700' : '') + '">' +
          (d.next_check_in_due ? new Date(d.next_check_in_due).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '—') +
          (isOverdue ? '<br><span style="font-size:10px;background:var(--danger);color:#fff;padding:1px 5px;border-radius:4px">OVERDUE</span>' : '') +
        '</td>' +
        '<td><span style="background:' + statusColor(d.status) + '20;color:' + statusColor(d.status) + ';border:1px solid ' + statusColor(d.status) + '40;padding:2px 8px;border-radius:10px;font-size:11px">' + escH((d.status || '—').toUpperCase()) + '</span></td>' +
        '<td>' + (d.missed_check_ins || 0) + '</td>' +
        '<td>' +
          '<div style="display:flex;gap:4px;flex-wrap:wrap">' +
            '<button class="btn-sm btn-primary" onclick="SLTracking.openDetail(\'' + bkSafe + '\')">📋 Detail</button>' +
            '<button class="btn-sm" style="background:#3b82f6;color:#fff;border:none;border-radius:4px;padding:3px 8px;cursor:pointer;font-size:11px" onclick="SLTracking.sendGeoLink(\'' + bkSafe + '\',\'' + nameSafe + '\')">📍 Ping</button>' +
            '<button class="btn-sm btn-danger" onclick="SLTracking.confirmExonerate(\'' + bkSafe + '\',\'' + nameSafe + '\')">✅ Exonerate</button>' +
          '</div>' +
        '</td></tr>';
    }).join('');
  }

  // ── View Toggle ───────────────────────────────────────────────────────────
  function showView(v) {
    _view = v;
    const mapView  = document.getElementById('trkMapView');
    const listView = document.getElementById('trkListView');
    const btnMap   = document.getElementById('trkBtnMap');
    const btnList  = document.getElementById('trkBtnList');
    if (v === 'map') {
      if (mapView)  mapView.style.display  = '';
      if (listView) listView.style.display = 'none';
      if (btnMap)   btnMap.classList.add('active');
      if (btnList)  btnList.classList.remove('active');
      setTimeout(() => { if (_map) _map.invalidateSize(); }, 50);
      _renderMap();
    } else {
      if (mapView)  mapView.style.display  = 'none';
      if (listView) listView.style.display = '';
      if (btnMap)   btnMap.classList.remove('active');
      if (btnList)  btnList.classList.add('active');
      _renderList();
    }
  }

  // ── Detail Drawer ─────────────────────────────────────────────────────────
  async function openDetail(bookingNumber) {
    _selectedBk = bookingNumber;
    const panel = document.getElementById('trkDetailPanel');
    const title = document.getElementById('trkDetailTitle');
    const body  = document.getElementById('trkDetailBody');
    if (!panel || !body) return;
    panel.style.display = '';
    if (title) title.textContent = '📋 Loading…';
    body.innerHTML = '<div class="loading" style="padding:24px">Loading defendant data…</div>';
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

    try {
      const r = await fetch(API + '/api/tracking/' + encodeURIComponent(bookingNumber) + '/history');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      if (data.error) throw new Error(data.error);

      if (title) title.textContent = '📋 ' + escH(data.defendant_name || bookingNumber);

      const isExonerated = data.status === 'exonerated' || !!data.exonerated_at;
      const bkSafe   = esc(bookingNumber);
      const nameSafe = esc(data.defendant_name || '');
      const risk     = data.risk_score || 0;
      const locs     = data.location_history || [];
      const alerts   = data.alerts || [];
      const courts   = data.court_dates || [];

      // Location history HTML
      let locHtml = '<p style="color:var(--muted);font-size:13px">No location pings recorded yet. Send a GPS link to capture the first location.</p>';
      if (locs.length) {
        locHtml = locs.slice().reverse().slice(0, 20).map(l => {
          const ts = l.ts || l.timestamp || '';
          const srcLabel = l.source === 'manual' ? '✏️ Manual' : l.source === 'geo_link' ? '🔗 Geo Link' : l.source === 'check_in' ? '📍 Check-In' : escH(l.source || 'unknown');
          const coords = (l.lat && l.lng) ? parseFloat(l.lat).toFixed(5) + ', ' + parseFloat(l.lng).toFixed(5) : '—';
          const mapsUrl = (l.lat && l.lng) ? 'https://maps.google.com/?q=' + l.lat + ',' + l.lng : null;
          return '<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 10px;background:var(--input-bg);border-radius:6px;margin-bottom:5px;font-size:12px">' +
            '<div>' +
              '<span style="font-weight:600">' + timeAgo(ts) + '</span>' +
              '<span style="color:var(--muted);margin-left:8px">' + srcLabel + '</span>' +
              (l.address ? '<div style="color:var(--muted);font-size:11px;margin-top:2px">' + escH(l.address) + '</div>' : '') +
            '</div>' +
            '<div style="text-align:right">' +
              (mapsUrl ? '<a href="' + mapsUrl + '" target="_blank" style="color:var(--accent);font-size:11px">' + coords + ' 🗺</a>' : '<span style="color:var(--muted)">' + coords + '</span>') +
            '</div>' +
          '</div>';
        }).join('') +
        (locs.length > 20 ? '<div style="color:var(--muted);font-size:11px;text-align:center;padding:6px">… and ' + (locs.length - 20) + ' older pings</div>' : '');
      }

      // Court dates HTML
      let courtHtml = '<p style="color:var(--muted);font-size:13px">No court reminders scheduled. Use the form below to schedule 4-touch SMS reminders.</p>';
      if (courts.length) {
        courtHtml = courts.map(c => {
          const cd = c.court_date || c.send_at || '';
          const isPast = cd && new Date(cd) < new Date();
          return '<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 10px;background:var(--input-bg);border-radius:6px;margin-bottom:5px;font-size:12px;' + (isPast ? 'opacity:.6' : '') + '">' +
            '<div><span style="font-weight:600">' + fmtDate(cd) + '</span><span style="color:var(--muted);margin-left:8px">' + escH(c.court_location || c.county || '—') + '</span></div>' +
            '<span style="background:' + (isPast ? '#64748b' : '#22c55e') + '20;color:' + (isPast ? '#64748b' : '#22c55e') + ';padding:2px 8px;border-radius:10px;font-size:10px">' + escH((c.status || '—').toUpperCase()) + '</span>' +
          '</div>';
        }).join('');
      }

      // Alerts HTML
      let alertsHtml = '<p style="color:var(--muted);font-size:13px">No alerts.</p>';
      if (alerts.length) {
        alertsHtml = alerts.map(a => {
          const sev = (a.severity || 'medium').toLowerCase();
          const sevColor = sev === 'high' ? '#ef4444' : sev === 'medium' ? '#f59e0b' : '#64748b';
          return '<div style="padding:8px 10px;background:' + sevColor + '15;border-left:3px solid ' + sevColor + ';border-radius:6px;margin-bottom:5px;font-size:12px">' +
            '<div style="font-weight:600;color:' + sevColor + '">' + escH((a.type || 'Alert').toUpperCase()) + '</div>' +
            '<div>' + escH(a.message || '—') + '</div>' +
            '<div style="color:var(--muted);font-size:11px;margin-top:2px">' + fmtDateTime(a.created_at || a.timestamp) + '</div>' +
          '</div>';
        }).join('');
      }

      // Assemble full drawer
      body.innerHTML =
        // KPI row
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:18px">' +
          _kpiCard('Risk Score', '<span class="score-pill ' + riskClass(risk) + '">' + risk + ' ' + riskLabel(risk) + '</span>') +
          _kpiCard('Bond Amount', '$' + (data.bond_amount || 0).toLocaleString()) +
          _kpiCard('Status', '<span style="color:' + statusColor(data.status) + ';font-weight:700">' + escH((data.status || '—').toUpperCase()) + '</span>') +
          _kpiCard('Location Pings', locs.length) +
          _kpiCard('County', escH(data.county || '—')) +
          _kpiCard('Check-In Freq.', (data.check_in_frequency_days || '—') + 'd') +
        '</div>' +

        // Indemnitor + Case info
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:18px">' +
          '<div class="panel" style="padding:14px">' +
            '<div class="panel-title" style="margin-bottom:8px">👤 Indemnitor</div>' +
            '<div style="font-size:13px;font-weight:600">' + escH(data.indemnitor_name || '—') + '</div>' +
            '<div style="font-size:12px;color:var(--muted);margin-top:2px">' + escH(data.indemnitor_phone || 'No phone on file') + '</div>' +
            (data.indemnitor_phone
              ? '<button class="btn-sm btn-primary" style="margin-top:10px" onclick="SLTracking.sendGeoLink(\'' + bkSafe + '\',\'' + nameSafe + '\',\'' + esc(data.indemnitor_phone) + '\')">📍 Send GPS Link to Indemnitor</button>'
              : '<div style="font-size:11px;color:var(--muted);margin-top:8px">No phone — add via Active Bonds tab</div>') +
          '</div>' +
          '<div class="panel" style="padding:14px">' +
            '<div class="panel-title" style="margin-bottom:8px">📋 Case Info</div>' +
            '<div style="font-size:13px;font-weight:600">' + escH(data.case_number || '—') + '</div>' +
            '<div style="font-size:12px;color:var(--muted);margin-top:2px">Booking: ' + escH(bookingNumber) + '</div>' +
            (data.exonerated_at ? '<div style="font-size:11px;color:var(--accent);margin-top:6px;font-weight:600">✅ Exonerated ' + fmtDate(data.exonerated_at) + ' via ' + escH(data.exoneration_source || '—') + '</div>' : '') +
          '</div>' +
        '</div>' +

        // Location history
        '<div class="panel" style="padding:14px;margin-bottom:18px">' +
          '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
            '<div class="panel-title" style="margin:0">📍 Location History (' + locs.length + ' pings)</div>' +
            '<button class="btn-sm btn-primary" onclick="SLTracking.sendGeoLink(\'' + bkSafe + '\',\'' + nameSafe + '\')">Send New GPS Link</button>' +
          '</div>' +
          locHtml +
        '</div>' +

        // Court dates
        '<div class="panel" style="padding:14px;margin-bottom:18px">' +
          '<div class="panel-title" style="margin-bottom:8px">⚖️ Court Dates & Reminders (' + courts.length + ')</div>' +
          courtHtml +
        '</div>' +

        // Alerts
        '<div class="panel" style="padding:14px;margin-bottom:18px">' +
          '<div class="panel-title" style="margin-bottom:8px">🚨 Alerts (' + alerts.length + ')</div>' +
          alertsHtml +
        '</div>' +

        // Actions panel (only if not exonerated)
        (!isExonerated
          ? '<div class="panel" style="padding:14px;margin-bottom:18px">' +
              '<div class="panel-title" style="margin-bottom:12px">⚡ Actions</div>' +
              '<div style="display:flex;gap:8px;flex-wrap:wrap">' +
                '<button class="btn-primary" onclick="SLTracking.sendGeoLink(\'' + bkSafe + '\',\'' + nameSafe + '\')">📍 Send GPS Link</button>' +
                '<button class="btn-export" onclick="SLTracking.discoverContacts(\'' + bkSafe + '\',\'' + nameSafe + '\')">🔍 Discover Contacts</button>' +
                '<button class="btn-export" style="background:#3b82f6;color:#fff;border:none" onclick="SLTracking._openInActiveBonds(\'' + bkSafe + '\')">🔒 View in Active Bonds</button>' +
                '<button class="btn-danger" onclick="SLTracking.confirmExonerate(\'' + bkSafe + '\',\'' + nameSafe + '\')">✅ Exonerate Bond</button>' +
              '</div>' +
            '</div>'
          : '<div class="panel" style="padding:14px;margin-bottom:18px;border:2px solid var(--accent)">' +
              '<div style="color:var(--accent);font-weight:700;font-size:14px">✅ Bond Exonerated — Tracking Stopped</div>' +
              '<div style="font-size:12px;color:var(--muted);margin-top:6px">' +
                fmtDateTime(data.exonerated_at) + ' via ' + escH(data.exoneration_source || '—') +
                (data.exoneration_note ? ' — ' + escH(data.exoneration_note) : '') +
              '</div>' +
            '</div>') +

        // Court reminder scheduler (pre-filled with this defendant's data)
        '<div class="panel" style="padding:14px;margin-bottom:18px">' +
          '<div class="panel-title" style="margin-bottom:12px">📅 Schedule Court Reminders (4-Touch SMS)</div>' +
          '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:12px">' +
            _inputField('drw_crBookingNum',   'Booking #',      bookingNumber) +
            _inputField('drw_crDefName',      'Defendant Name', data.defendant_name || '') +
            _inputField('drw_crPhone',        'Phone (indemnitor)', data.indemnitor_phone || '', '+12395551234') +
            _inputField('drw_crCourtDate',    'Court Date', '', '', 'datetime-local') +
            _inputField('drw_crCourtLocation','Court Location', data.county || '', 'e.g. Lee County') +
            _inputField('drw_crCaseNumber',   'Case Number', data.case_number || '', 'e.g. 25-CF-001234') +
          '</div>' +
          '<button class="btn-primary" onclick="SLTracking._scheduleRemindersFromDrawer()">📅 Schedule 4-Touch Reminders</button>' +
          '<div id="drw_crResult" style="margin-top:10px;font-size:13px"></div>' +
        '</div>' +

        // Contact discovery result area
        '<div id="trkContactResult_' + escH(bookingNumber) + '" style="margin-top:10px;font-size:13px"></div>';

    } catch (e) {
      if (body) body.innerHTML = '<div style="color:var(--danger);padding:12px">Error loading detail: ' + e.message + '</div>';
    }
  }

  function closeDetail() {
    const panel = document.getElementById('trkDetailPanel');
    if (panel) panel.style.display = 'none';
    _selectedBk = null;
  }

  // ── Cross-tab navigation ──────────────────────────────────────────────────
  function _openInActiveBonds(bookingNumber) {
    const tab = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.textContent.includes('Active Bonds'));
    if (tab) tab.click();
    setTimeout(() => {
      const searchEl = document.getElementById('abSearch') || document.getElementById('activeBondsSearch');
      if (searchEl) { searchEl.value = bookingNumber; searchEl.dispatchEvent(new Event('input')); }
    }, 350);
  }

  // ── Schedule Reminders (from detail drawer — uses drw_ IDs) ──────────────
  async function _scheduleRemindersFromDrawer() {
    const bookingNumber  = getVal('drw_crBookingNum');
    const defName        = getVal('drw_crDefName');
    const phone          = getVal('drw_crPhone');
    const courtDate      = getVal('drw_crCourtDate');
    const courtLocation  = getVal('drw_crCourtLocation');
    const caseNumber     = getVal('drw_crCaseNumber');
    const resultEl       = document.getElementById('drw_crResult');

    if (!bookingNumber || !defName || !phone || !courtDate || !courtLocation || !caseNumber) {
      if (resultEl) resultEl.innerHTML = '<span style="color:var(--danger)">⚠ Please fill in all fields before scheduling.</span>';
      return;
    }
    if (resultEl) resultEl.innerHTML = '<span style="color:var(--muted)">Scheduling…</span>';
    try {
      const resp = await fetch(API + '/api/court-reminders/schedule', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_number: bookingNumber, defendant_name: defName, phone, court_date: new Date(courtDate).toISOString(), court_location: courtLocation, case_number: caseNumber }),
      });
      const data = await resp.json();
      if (data.error) throw new Error(data.error);
      if (resultEl) resultEl.innerHTML = '<span style="color:var(--accent)">✅ Scheduled ' + (data.scheduled_count || 4) + ' reminders (7d, 3d, 1d, morning-of)</span>';
      toast('Court reminders scheduled for ' + defName, 'success');
    } catch (e) {
      if (resultEl) resultEl.innerHTML = '<span style="color:var(--danger)">Error: ' + e.message + '</span>';
    }
  }

  // ── Schedule Reminders (from standalone panel — uses crXxx IDs) ───────────
  async function scheduleReminders() {
    const bookingNumber  = getVal('crBookingNum');
    const defName        = getVal('crDefName');
    const phone          = getVal('crPhone');
    const courtDate      = getVal('crCourtDate');
    const courtLocation  = getVal('crCourtLocation');
    const caseNumber     = getVal('crCaseNumber');
    const resultEl       = document.getElementById('crResult');

    if (!bookingNumber || !defName || !phone || !courtDate || !courtLocation || !caseNumber) {
      if (resultEl) resultEl.innerHTML = '<span style="color:var(--danger)">⚠ Please fill in all fields.</span>';
      return;
    }
    if (resultEl) resultEl.innerHTML = '<span style="color:var(--muted)">Scheduling…</span>';
    try {
      const resp = await fetch(API + '/api/court-reminders/schedule', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_number: bookingNumber, defendant_name: defName, phone, court_date: new Date(courtDate).toISOString(), court_location: courtLocation, case_number: caseNumber }),
      });
      const data = await resp.json();
      if (data.error) throw new Error(data.error);
      if (resultEl) resultEl.innerHTML = '<span style="color:var(--accent)">✅ Scheduled ' + (data.scheduled_count || 4) + ' reminders (7d, 3d, 1d, morning-of)</span>';
      toast('Court reminders scheduled', 'success');
    } catch (e) {
      if (resultEl) resultEl.innerHTML = '<span style="color:var(--danger)">Error: ' + e.message + '</span>';
    }
  }

  // ── Send Geo Link ─────────────────────────────────────────────────────────
  async function sendGeoLink(bookingNumber, defName, phone) {
    if (!confirm('📍 Send GPS capture link for ' + (defName || bookingNumber) + '?\n\nThis sends an iMessage/SMS to the indemnitor with a one-tap location link.')) return;
    try {
      const r = await fetch(API + '/api/tracking/' + encodeURIComponent(bookingNumber) + '/send-geo-link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: phone || '', recipient: 'indemnitor' }),
      });
      const data = await r.json();
      if (data.success) {
        toast('📍 GPS link sent via ' + (data.channel || 'iMessage') + (data.phone ? ' to ' + data.phone : ''), 'success');
      } else {
        toast('❌ ' + (data.error || 'Send failed — check phone number'), 'error');
      }
    } catch (e) {
      toast('Network error sending geo link', 'error');
    }
  }

  // ── Confirm Exonerate ─────────────────────────────────────────────────────
  async function confirmExonerate(bookingNumber, defName) {
    const note = prompt(
      '✅ Exonerate bond for ' + (defName || bookingNumber) + '?\n\n' +
      'This will:\n' +
      '  • Stop all location tracking immediately\n' +
      '  • Cancel all pending GPS capture links\n' +
      '  • Cancel all pending court reminders\n\n' +
      'Enter a note (e.g. "Discharge email from Lee County Clerk") or leave blank:'
    );
    if (note === null) return; // User pressed Cancel
    const notifyIndem = confirm('Notify indemnitor via iMessage that the bond is officially discharged?');
    try {
      const r = await fetch(API + '/api/tracking/' + encodeURIComponent(bookingNumber) + '/exonerate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source: 'manual',
          note: note || 'Manual exoneration from dashboard',
          notify_indemnitor: notifyIndem,
        }),
      });
      const data = await r.json();
      if (data.success) {
        toast('✅ ' + (defName || bookingNumber) + ' exonerated — tracking stopped', 'success');
        closeDetail();
        await refresh();
        await _loadExonLog();
        // Also refresh active bonds tab if loaded
        if (typeof loadActiveBonds === 'function') loadActiveBonds();
      } else if (data.already_exonerated) {
        toast((defName || bookingNumber) + ' was already exonerated on ' + fmtDate(data.exonerated_at), 'info');
      } else {
        toast('❌ ' + (data.error || 'Exoneration failed'), 'error');
      }
    } catch (e) {
      toast('Network error during exoneration', 'error');
    }
  }

  // ── Contact Discovery ─────────────────────────────────────────────────────
  async function discoverContacts(bookingNumber, defName) {
    const resultEl = document.getElementById('trkContactResult_' + bookingNumber);
    if (resultEl) resultEl.innerHTML = '<div style="color:var(--muted);font-size:13px">🔍 Running contact discovery for ' + escH(defName || bookingNumber) + '…</div>';
    try {
      const resp = await fetch(API + '/api/contacts/discover', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_number: bookingNumber, full_name: defName }),
      });
      const data = await resp.json();
      if (data.error) throw new Error(data.error);
      const contacts = data.contacts || [];
      if (!contacts.length) {
        if (resultEl) resultEl.innerHTML = '<div style="color:var(--muted);font-size:13px">No contacts discovered for this defendant.</div>';
        return;
      }
      const html = '<div class="panel" style="padding:14px;margin-top:12px">' +
        '<div class="panel-title" style="margin-bottom:10px">🔍 Discovered Contacts (' + contacts.length + ')</div>' +
        contacts.map(c =>
          '<div style="padding:10px 12px;background:var(--input-bg);border-radius:8px;margin-bottom:6px">' +
            '<div style="font-weight:600;font-size:13px">' + escH(c.name || '—') + '</div>' +
            '<div style="font-size:12px;color:var(--muted)">' + escH(c.relationship || '—') + ' · ' + escH(c.source || '—') + ' · Confidence: ' + Math.round((c.confidence || 0) * 100) + '%</div>' +
            (c.phone ? '<div style="font-size:12px;margin-top:3px">📞 ' + escH(c.phone) + '</div>' : '') +
            (c.address ? '<div style="font-size:12px;color:var(--muted)">' + escH(c.address) + '</div>' : '') +
          '</div>'
        ).join('') +
      '</div>';
      if (resultEl) resultEl.innerHTML = html;
    } catch (e) {
      if (resultEl) resultEl.innerHTML = '<div style="color:var(--danger);font-size:13px">Error: ' + e.message + '</div>';
    }
  }

  // ── Exoneration Log ───────────────────────────────────────────────────────
  async function _loadExonLog() {
    try {
      const r = await fetch(API + '/api/tracking/exonerations?limit=25');
      if (!r.ok) return;
      const data = await r.json();
      _exonLog = data.exonerations || [];
      _renderExonLog();
    } catch (e) {
      console.warn('[SLTracking] exoneration log load failed:', e);
    }
  }

  function _renderExonLog() {
    const container = document.getElementById('trkExonerationLog');
    if (!container) return;
    if (!_exonLog.length) {
      container.innerHTML = '<p style="color:var(--muted);font-size:13px">No exonerations recorded yet. When discharge emails arrive from courts or bonds are manually exonerated, they will appear here automatically.</p>';
      return;
    }
    container.innerHTML =
      '<div class="table-wrap"><table><thead><tr>' +
        '<th>Defendant</th><th>Booking #</th><th>Case #</th><th>Source</th><th>Note</th><th>Date</th>' +
      '</tr></thead><tbody>' +
      _exonLog.map(e => {
        const bk = e.entity_id || e.booking_number || '—';
        const srcLabel = e.source === 'court_email' ? '📧 Court Email' : e.source === 'manual' ? '✏️ Manual' : escH(e.source || '—');
        return '<tr>' +
          '<td style="font-weight:600">' + escH(e.defendant_name || '—') + '</td>' +
          '<td style="font-size:12px;color:var(--muted)">' + escH(bk) + '</td>' +
          '<td style="font-size:12px;color:var(--muted)">' + escH(e.case_number || '—') + '</td>' +
          '<td><span style="background:var(--accent)20;color:var(--accent);padding:2px 8px;border-radius:10px;font-size:11px">' + srcLabel + '</span></td>' +
          '<td style="font-size:12px;color:var(--muted)">' + escH(e.note || '—') + '</td>' +
          '<td style="font-size:12px;color:var(--muted)">' + fmtDate(e.exonerated_at || e.timestamp) + '</td>' +
        '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  // ── SSE Event Handlers ────────────────────────────────────────────────────
  function onBondWritten(payload) {
    if (_initialized) refresh();
  }

  function onBondExonerated(payload) {
    const bk = payload.booking_number;
    // Remove from map immediately
    if (_markers[bk]) { if (_map) _map.removeLayer(_markers[bk]); delete _markers[bk]; }
    if (_trails[bk])  { if (_map) _map.removeLayer(_trails[bk]);  delete _trails[bk];  }
    // Remove from data
    _data     = _data.filter(d => d.booking_number !== bk);
    _filtered = _filtered.filter(d => d.booking_number !== bk);
    // Re-render
    if (_view === 'map') _renderMap(); else _renderList();
    // Reload exoneration log
    _loadExonLog();
    // Close detail drawer if it was showing this bond
    if (_selectedBk === bk) closeDetail();
    toast('✅ ' + (payload.defendant_name || bk) + ' exonerated — tracking stopped', 'success');
  }

  // ── Utility Builders ──────────────────────────────────────────────────────
  function _kpiCard(label, value) {
    return '<div class="stat-card" style="padding:12px">' +
           '<div class="stat-label">' + label + '</div>' +
           '<div style="font-weight:700;font-size:15px;margin-top:4px">' + value + '</div>' +
           '</div>';
  }

  function _inputField(id, label, value, placeholder, type) {
    return '<div>' +
      '<label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:4px">' + escH(label) + '</label>' +
      '<input type="' + (type || 'text') + '" id="' + id + '" value="' + escH(value || '') + '" placeholder="' + escH(placeholder || '') + '" ' +
        'style="width:100%;padding:8px 12px;background:var(--input-bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:13px;font-family:inherit;box-sizing:border-box">' +
    '</div>';
  }

  // ── Public API ────────────────────────────────────────────────────────────
  return {
    init,
    refresh,
    showView,
    openDetail,
    closeDetail,
    sendGeoLink,
    confirmExonerate,
    scheduleReminders,
    _scheduleRemindersFromDrawer,
    _openInActiveBonds,
    discoverContacts,
    onBondWritten,
    onBondExonerated,
  };
})();
