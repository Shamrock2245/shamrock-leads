/**
 * sl-tracking.js
 * ShamrockLeads — Defendant Tracking Tab
 *
 * Handles:
 *  - Leaflet map with defendant markers (risk-color-coded)
 *  - List view with sortable table
 *  - Detail panel with location history
 *  - Court reminder scheduler (4-touch: 7d, 3d, 1d, morning-of)
 *  - Contact discovery trigger
 *
 * Depends on:
 *  - Leaflet 1.9.x (loaded via CDN in index.html)
 *  - SL.toast() from sl-core.js
 */

const SLTracking = (() => {
  let _map = null;
  let _markers = {};
  let _defendants = [];
  let _currentView = 'map';
  let _initialized = false;

  // ── Risk badge helper ──────────────────────────────────────────────────────
  function riskBadge(score) {
    if (score >= 75) return `<span class="risk-badge risk-high">${score}</span>`;
    if (score >= 45) return `<span class="risk-badge risk-med">${score}</span>`;
    return `<span class="risk-badge risk-low">${score}</span>`;
  }

  // ── Marker color by risk ───────────────────────────────────────────────────
  function markerColor(score, overdue) {
    if (overdue) return '#ef4444';       // red — overdue
    if (score >= 75) return '#f97316';   // orange — high risk
    if (score >= 45) return '#f59e0b';   // yellow — medium
    return '#10b981';                    // green — low
  }

  // ── Format relative time ───────────────────────────────────────────────────
  function relTime(isoStr) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  // ── Init Leaflet map ───────────────────────────────────────────────────────
  function _initMap() {
    if (_map) return;
    _map = L.map('trkMap', {
      center: [27.5, -81.5],  // Florida center
      zoom: 7
    });

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© OpenStreetMap contributors',
      maxZoom: 18
    }).addTo(_map);
  }

  // ── Render markers on map ──────────────────────────────────────────────────
  function _renderMarkers(defendants) {
    // Clear old markers
    Object.values(_markers).forEach(m => _map.removeLayer(m));
    _markers = {};

    defendants.forEach(def => {
      const loc = def.latest_location;
      if (!loc || !loc.lat || !loc.lng) return;

      const color = markerColor(def.risk_score, def.check_in_overdue);
      const icon = L.divIcon({
        className: '',
        html: `<div style="
          width:14px;height:14px;border-radius:50%;
          background:${color};border:2px solid #fff;
          box-shadow:0 0 6px ${color};
          cursor:pointer;
        "></div>`,
        iconSize: [14, 14],
        iconAnchor: [7, 7]
      });

      const marker = L.marker([loc.lat, loc.lng], { icon })
        .addTo(_map)
        .bindPopup(`
          <div style="font-family:Inter,sans-serif;min-width:180px">
            <div style="font-weight:700;font-size:14px;margin-bottom:6px">${def.defendant_name}</div>
            <div style="font-size:12px;color:#666">${def.county} County</div>
            <div style="font-size:12px;margin-top:4px">
              Bond: <strong>$${(def.bond_amount || 0).toLocaleString()}</strong>
            </div>
            <div style="font-size:12px">
              Risk: <strong style="color:${color}">${def.risk_score}</strong>
            </div>
            <div style="font-size:12px">
              Last check-in: ${relTime(def.last_check_in)}
            </div>
            ${def.check_in_overdue ? '<div style="color:#ef4444;font-size:12px;font-weight:600;margin-top:4px">⚠️ OVERDUE</div>' : ''}
            <button onclick="SLTracking.openDetail('${def.booking_number}')"
              style="margin-top:8px;padding:4px 10px;background:#10b981;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px;width:100%">
              View Detail
            </button>
          </div>
        `);

      _markers[def.booking_number] = marker;
    });
  }

  // ── Render list view ───────────────────────────────────────────────────────
  function _renderList(defendants) {
    const tbody = document.getElementById('trkListBody');
    if (!tbody) return;

    if (!defendants.length) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:24px;color:var(--muted)">No active bonds found</td></tr>';
      return;
    }

    tbody.innerHTML = defendants.map(def => `
      <tr style="cursor:pointer" onclick="SLTracking.openDetail('${def.booking_number}')">
        <td><strong>${def.defendant_name}</strong></td>
        <td>${def.county}</td>
        <td>$${(def.bond_amount || 0).toLocaleString()}</td>
        <td>${riskBadge(def.risk_score)}</td>
        <td>${relTime(def.last_check_in)}</td>
        <td style="color:${def.check_in_overdue ? 'var(--danger)' : 'inherit'}">
          ${def.next_check_in_due ? new Date(def.next_check_in_due).toLocaleDateString() : '—'}
          ${def.check_in_overdue ? ' ⚠️' : ''}
        </td>
        <td>${def.status || 'active'}</td>
        <td style="color:${def.missed_check_ins > 0 ? 'var(--warning)' : 'inherit'}">${def.missed_check_ins}</td>
        <td>
          <button class="btn-export" style="padding:4px 10px;font-size:11px"
            onclick="event.stopPropagation();SLTracking.openDetail('${def.booking_number}')">
            Detail
          </button>
        </td>
      </tr>
    `).join('');
  }

  // ── Update KPI badges ──────────────────────────────────────────────────────
  function _updateKpis(summary) {
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('trkKpiTotal', summary.total_active || 0);
    set('trkKpiOverdue', summary.overdue || 0);
    set('trkKpiHighRisk', summary.high_risk || 0);
    set('trkKpiOutOfArea', summary.out_of_area || 0);

    // Update tab badge
    const badge = document.getElementById('trackingBadge');
    if (badge) {
      const alerts = (summary.overdue || 0) + (summary.high_risk || 0);
      badge.textContent = alerts > 0 ? alerts : summary.total_active || 0;
    }
  }

  // ── Fetch data from API ────────────────────────────────────────────────────
  async function _fetchData() {
    try {
      const resp = await fetch('/api/tracking/map-data');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return await resp.json();
    } catch (e) {
      console.error('[SLTracking] fetch error:', e);
      return null;
    }
  }

  // ── Public: init ──────────────────────────────────────────────────────────
  async function init() {
    if (_currentView === 'map') {
      // Small delay to ensure tab is visible before Leaflet measures container
      setTimeout(() => {
        _initMap();
        if (_map) _map.invalidateSize();
      }, 100);
    }

    await refresh();
    _initialized = true;
  }

  // ── Public: refresh ───────────────────────────────────────────────────────
  async function refresh() {
    const data = await _fetchData();
    if (!data) return;

    _defendants = data.defendants || [];
    _updateKpis(data.summary || {});

    if (_currentView === 'map') {
      if (_map) _renderMarkers(_defendants);
    } else {
      _renderList(_defendants);
    }
  }

  // ── Public: showView ──────────────────────────────────────────────────────
  function showView(view) {
    _currentView = view;

    const mapView = document.getElementById('trkMapView');
    const listView = document.getElementById('trkListView');
    const btnMap = document.getElementById('trkBtnMap');
    const btnList = document.getElementById('trkBtnList');

    if (view === 'map') {
      if (mapView) mapView.style.display = '';
      if (listView) listView.style.display = 'none';
      if (btnMap) btnMap.classList.add('active');
      if (btnList) btnList.classList.remove('active');
      setTimeout(() => { if (_map) { _initMap(); _map.invalidateSize(); } }, 50);
      _renderMarkers(_defendants);
    } else {
      if (mapView) mapView.style.display = 'none';
      if (listView) listView.style.display = '';
      if (btnMap) btnMap.classList.remove('active');
      if (btnList) btnList.classList.add('active');
      _renderList(_defendants);
    }
  }

  // ── Public: openDetail ────────────────────────────────────────────────────
  async function openDetail(bookingNumber) {
    const panel = document.getElementById('trkDetailPanel');
    const title = document.getElementById('trkDetailTitle');
    const body = document.getElementById('trkDetailBody');
    if (!panel || !body) return;

    title.textContent = `📍 Loading ${bookingNumber}…`;
    panel.style.display = '';
    body.innerHTML = '<div style="padding:16px;color:var(--muted)">Loading…</div>';
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

    try {
      const resp = await fetch(`/api/tracking/${encodeURIComponent(bookingNumber)}/history`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      title.textContent = `📍 ${data.defendant_name || bookingNumber}`;

      // Location history table
      const locs = data.location_history || [];
      const locHtml = locs.length
        ? `<table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr>
              <th style="text-align:left;padding:6px 8px;border-bottom:1px solid var(--border)">Time</th>
              <th style="text-align:left;padding:6px 8px;border-bottom:1px solid var(--border)">Lat</th>
              <th style="text-align:left;padding:6px 8px;border-bottom:1px solid var(--border)">Lng</th>
              <th style="text-align:left;padding:6px 8px;border-bottom:1px solid var(--border)">County</th>
              <th style="text-align:left;padding:6px 8px;border-bottom:1px solid var(--border)">Source</th>
            </tr></thead>
            <tbody>
              ${locs.slice(-20).reverse().map(l => `
                <tr>
                  <td style="padding:5px 8px">${l.timestamp ? new Date(l.timestamp).toLocaleString() : '—'}</td>
                  <td style="padding:5px 8px">${l.lat || '—'}</td>
                  <td style="padding:5px 8px">${l.lng || '—'}</td>
                  <td style="padding:5px 8px">${l.county || '—'}</td>
                  <td style="padding:5px 8px">${l.source || '—'}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>`
        : '<p style="color:var(--muted);font-size:13px;padding:8px 0">No location history recorded.</p>';

      // Alerts
      const alerts = data.alerts || [];
      const alertsHtml = alerts.length
        ? alerts.map(a => `<div style="padding:6px 10px;background:rgba(239,68,68,.1);border-left:3px solid var(--danger);border-radius:4px;margin-bottom:6px;font-size:13px">
            <strong>${a.type || 'Alert'}</strong> — ${a.note || ''} <span style="color:var(--muted)">${a.created_at ? new Date(a.created_at).toLocaleString() : ''}</span>
          </div>`).join('')
        : '<p style="color:var(--muted);font-size:13px">No alerts.</p>';

      // Court dates
      const courts = data.court_dates || [];
      const courtsHtml = courts.length
        ? courts.map(c => `<div style="padding:6px 10px;background:rgba(59,130,246,.1);border-left:3px solid var(--blue);border-radius:4px;margin-bottom:6px;font-size:13px">
            <strong>${c.date ? new Date(c.date).toLocaleDateString() : '—'}</strong> — ${c.location || ''} (Case: ${c.case_number || '—'})
          </div>`).join('')
        : '<p style="color:var(--muted);font-size:13px">No court dates recorded.</p>';

      body.innerHTML = `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px">
          <div>
            <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">Alerts</div>
            ${alertsHtml}
          </div>
          <div>
            <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">Court Dates</div>
            ${courtsHtml}
          </div>
        </div>
        <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">Location History (last 20)</div>
        ${locHtml}
        <div style="margin-top:16px;display:flex;gap:10px">
          <button class="btn-export" onclick="SLTracking.discoverContacts('${bookingNumber}', '${(data.defendant_name || '').replace(/'/g, "\\'")}')">
            🔍 Discover Contacts
          </button>
        </div>
        <div id="trkContactResult_${bookingNumber}" style="margin-top:10px;font-size:13px"></div>
      `;
    } catch (e) {
      body.innerHTML = `<div style="color:var(--danger);padding:12px">Error loading detail: ${e.message}</div>`;
    }
  }

  // ── Public: closeDetail ───────────────────────────────────────────────────
  function closeDetail() {
    const panel = document.getElementById('trkDetailPanel');
    if (panel) panel.style.display = 'none';
  }

  // ── Public: scheduleReminders ─────────────────────────────────────────────
  async function scheduleReminders() {
    const get = id => (document.getElementById(id) || {}).value || '';
    const bookingNumber = get('crBookingNum').trim();
    const defName = get('crDefName').trim();
    const phone = get('crPhone').trim();
    const courtDate = get('crCourtDate');
    const courtLocation = get('crCourtLocation').trim();
    const caseNumber = get('crCaseNumber').trim();
    const resultEl = document.getElementById('crResult');

    if (!bookingNumber || !defName || !phone || !courtDate || !courtLocation || !caseNumber) {
      if (resultEl) resultEl.innerHTML = '<span style="color:var(--danger)">Please fill in all fields.</span>';
      return;
    }

    if (resultEl) resultEl.innerHTML = '<span style="color:var(--muted)">Scheduling…</span>';

    try {
      const resp = await fetch('/api/court-reminders/schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          booking_number: bookingNumber,
          defendant_name: defName,
          phone,
          court_date: new Date(courtDate).toISOString(),
          court_location: courtLocation,
          case_number: caseNumber
        })
      });
      const data = await resp.json();

      if (data.error) throw new Error(data.error);

      if (resultEl) resultEl.innerHTML = `<span style="color:var(--accent)">✅ Scheduled ${data.scheduled_count} reminders (7d, 3d, 1d, morning-of)</span>`;
      if (typeof SL !== 'undefined' && SL.toast) SL.toast('✅ Court reminders scheduled', 'success');
    } catch (e) {
      if (resultEl) resultEl.innerHTML = `<span style="color:var(--danger)">Error: ${e.message}</span>`;
    }
  }

  // ── Public: discoverContacts ──────────────────────────────────────────────
  async function discoverContacts(bookingNumber, defName) {
    const resultEl = document.getElementById(`trkContactResult_${bookingNumber}`);
    if (resultEl) resultEl.innerHTML = '<span style="color:var(--muted)">Running discovery…</span>';

    try {
      const resp = await fetch('/api/contacts/discover', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_number: bookingNumber, full_name: defName })
      });
      const data = await resp.json();

      if (data.error) throw new Error(data.error);

      const contacts = data.contacts || [];
      if (!contacts.length) {
        if (resultEl) resultEl.innerHTML = '<span style="color:var(--muted)">No contacts discovered.</span>';
        return;
      }

      const html = contacts.map(c => `
        <div style="padding:8px 12px;background:var(--panel);border:1px solid var(--border);border-radius:8px;margin-bottom:6px">
          <div style="font-weight:600;font-size:13px">${c.name || '—'}</div>
          <div style="font-size:12px;color:var(--muted)">${c.relationship || '—'} · ${c.source || '—'} · Confidence: ${Math.round((c.confidence || 0) * 100)}%</div>
          ${c.phone ? `<div style="font-size:12px">📞 ${c.phone}</div>` : ''}
          ${c.address ? `<div style="font-size:12px">📍 ${c.address}</div>` : ''}
          ${c.notes ? `<div style="font-size:12px;color:var(--muted)">${c.notes}</div>` : ''}
        </div>
      `).join('');

      if (resultEl) resultEl.innerHTML = `
        <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">
          Discovered Contacts (${contacts.length})
        </div>
        ${html}
      `;
    } catch (e) {
      if (resultEl) resultEl.innerHTML = `<span style="color:var(--danger)">Error: ${e.message}</span>`;
    }
  }

  // ── SSE handler: called from sl-core.js on bond_written event ────────────
  function onBondWritten(payload) {
    // Refresh tracking data when a new bond is written
    if (_initialized) refresh();
  }

  return { init, refresh, showView, openDetail, closeDetail, scheduleReminders, discoverContacts, onBondWritten };
})();
