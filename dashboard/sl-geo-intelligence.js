/* ShamrockLeads — Geo Intelligence Module v1.0
   ─────────────────────────────────────────────
   GPS Device Management, Vehicle Watch, Geofence Builder,
   Violation Alert Feed, Traccar Health.
   Extends sl-tracking.js with hardware GPS intelligence.
*/
const SLGeoIntel = (() => {
  'use strict';
  const API = window.API_BASE || '';
  let _initialized = false;
  let _devices = [], _zones = [], _violations = [], _vehicles = [], _overview = {};

  // ── Helpers ──
  function toast(msg, type) { if (window.SL?.toast) SL.toast(msg, type); }
  function escH(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function timeAgo(ts) {
    if (!ts) return '—';
    const m = Math.floor((Date.now() - new Date(ts).getTime()) / 60000);
    if (m < 1) return 'now'; if (m < 60) return m+'m'; 
    const h = Math.floor(m/60); if (h < 24) return h+'h'; return Math.floor(h/24)+'d';
  }
  async function _fetch(url, opts) {
    const r = await fetch(API + url, opts);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }
  async function _post(url, body) {
    return _fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
  }

  // ══════════════════════════════════════════════════════════════════════════
  // INIT
  // ══════════════════════════════════════════════════════════════════════════
  async function init() {
    if (_initialized) { await refreshAll(); return; }
    _initialized = true;
    await refreshAll();
  }

  async function refreshAll() {
    await Promise.allSettled([
      loadOverview(), loadDevices(), loadZones(), loadViolations(), loadVehicles(), loadHealth()
    ]);
  }

  // ══════════════════════════════════════════════════════════════════════════
  // OVERVIEW KPIs
  // ══════════════════════════════════════════════════════════════════════════
  async function loadOverview() {
    try {
      _overview = await _fetch('/api/geo-intel/overview');
      _setKpi('geoKpiDevices', _overview.total_devices || 0);
      _setKpi('geoKpiZones', _overview.total_zones || 0);
      _setKpi('geoKpiViolations', _overview.recent_violations_24h || 0,
        (_overview.recent_violations_24h || 0) > 0 ? 'var(--danger)' : null);
      _setKpi('geoKpiVehicles', _overview.total_vehicle_watches || 0);
      _setKpi('geoKpiStale', _overview.stale_devices || 0,
        (_overview.stale_devices || 0) > 0 ? 'var(--gold)' : null);
      _setKpi('geoKpiUnacked', _overview.unacknowledged_violations || 0,
        (_overview.unacknowledged_violations || 0) > 0 ? 'var(--danger)' : null);
    } catch (e) { console.warn('[GeoIntel] overview error:', e); }
  }

  function _setKpi(id, val, color) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = val ?? '—';
    if (color) el.style.color = color;
  }

  // ══════════════════════════════════════════════════════════════════════════
  // TRACCAR HEALTH
  // ══════════════════════════════════════════════════════════════════════════
  async function loadHealth() {
    const el = document.getElementById('geoTraccarHealth');
    if (!el) return;
    try {
      const h = await _fetch('/api/geo-intel/health');
      const isOnline = h.status === 'online';
      el.innerHTML = `
        <div class="stat-card" style="padding:12px;border-left:3px solid ${isOnline?'var(--success)':'var(--danger)'}">
          <div style="display:flex;align-items:center;gap:8px">
            <span style="width:8px;height:8px;border-radius:50%;background:${isOnline?'var(--success)':'var(--danger)'};display:inline-block"></span>
            <strong>Traccar GPS Engine</strong>
            <span style="color:var(--muted);font-size:12px">${escH(h.status)}</span>
          </div>
          ${isOnline ? `<div style="font-size:11px;color:var(--muted);margin-top:4px">User: ${escH(h.user)} • Admin: ${h.admin?'Yes':'No'}</div>` : ''}
        </div>`;
    } catch (e) {
      el.innerHTML = '<div class="stat-card" style="padding:12px;border-left:3px solid var(--danger)"><span style="color:var(--danger)">⚠ Traccar unreachable</span></div>';
    }
  }

  // ══════════════════════════════════════════════════════════════════════════
  // DEVICE MANAGEMENT
  // ══════════════════════════════════════════════════════════════════════════
  async function loadDevices() {
    try {
      const res = await _fetch('/api/geo-intel/devices');
      _devices = res.devices || [];
      _renderDevices();
    } catch (e) { console.warn('[GeoIntel] devices error:', e); }
  }

  function _renderDevices() {
    const el = document.getElementById('geoDeviceList');
    if (!el) return;
    if (!_devices.length) {
      el.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">No tracking devices registered yet</div>';
      return;
    }
    el.innerHTML = _devices.map(d => {
      const typeIcon = {phone_app:'📱',vehicle_tracker:'🚗',personal_tracker:'📍',ankle_monitor:'⌚'}[d.device_type]||'📡';
      const stale = d.last_seen && (Date.now() - new Date(d.last_seen).getTime()) > 4*3600000;
      return `<div class="stat-card" style="padding:12px;display:flex;align-items:center;gap:12px;border-left:3px solid ${d.status==='active'?(stale?'var(--gold)':'var(--success)'):'var(--muted)'}">
        <span style="font-size:20px">${typeIcon}</span>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;font-size:13px">${escH(d.label||d.device_type)}</div>
          <div style="font-size:11px;color:var(--muted)">${escH(d.booking_number)} • ${escH(d.county||'')}</div>
          ${d.last_seen?`<div style="font-size:11px;color:${stale?'var(--gold)':'var(--muted)'}">Last seen: ${timeAgo(d.last_seen)}</div>`:'<div style="font-size:11px;color:var(--gold)">No signal yet</div>'}
        </div>
        <div style="display:flex;gap:6px">
          ${d.status==='active'?`<button onclick="SLGeoIntel.deactivateDevice('${d.device_id}')" class="btn-sm" style="font-size:11px;padding:4px 8px;background:var(--danger);color:#fff;border:none;border-radius:4px;cursor:pointer" title="Deactivate">✕</button>`:''}
        </div>
      </div>`;
    }).join('');
  }

  async function registerDevice() {
    const booking = document.getElementById('geoNewDeviceBooking')?.value?.trim();
    const county = document.getElementById('geoNewDeviceCounty')?.value?.trim();
    const type = document.getElementById('geoNewDeviceType')?.value || 'phone_app';
    const uid = document.getElementById('geoNewDeviceUid')?.value?.trim();
    const label = document.getElementById('geoNewDeviceLabel')?.value?.trim();
    if (!booking || !uid) { toast('Booking # and Unique ID required', 'error'); return; }
    try {
      await _post('/api/geo-intel/devices', { booking_number:booking, county, device_type:type, unique_id:uid, label });
      toast('✅ Device registered', 'success');
      _clearForm('geoNewDevice');
      await loadDevices();
      await loadOverview();
    } catch (e) { toast('Device registration failed: '+e.message, 'error'); }
  }

  async function deactivateDevice(deviceId) {
    if (!confirm('Deactivate this tracking device?')) return;
    try {
      await _post(`/api/geo-intel/devices/${deviceId}/deactivate`, { reason:'manual' });
      toast('Device deactivated', 'success');
      await loadDevices();
      await loadOverview();
    } catch (e) { toast('Deactivation failed: '+e.message, 'error'); }
  }

  // ══════════════════════════════════════════════════════════════════════════
  // GEOFENCE ZONES
  // ══════════════════════════════════════════════════════════════════════════
  async function loadZones() {
    try {
      const res = await _fetch('/api/geo-intel/zones');
      _zones = res.zones || [];
      _renderZones();
    } catch (e) { console.warn('[GeoIntel] zones error:', e); }
  }

  function _renderZones() {
    const el = document.getElementById('geoZoneList');
    if (!el) return;
    if (!_zones.length) {
      el.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">No geofence zones configured</div>';
      return;
    }
    el.innerHTML = _zones.map(z => {
      const isInclusion = z.zone_type === 'inclusion';
      const color = isInclusion ? 'var(--success)' : 'var(--danger)';
      const icon = isInclusion ? '🟢' : '🔴';
      return `<div class="stat-card" style="padding:12px;border-left:3px solid ${color}">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div>
            <span>${icon}</span>
            <strong style="font-size:13px">${escH(z.name)}</strong>
            <span style="font-size:11px;color:var(--muted);margin-left:8px">${z.zone_type.toUpperCase()}</span>
          </div>
          <button onclick="SLGeoIntel.deleteZone('${z.zone_id}')" class="btn-sm" style="font-size:11px;padding:4px 8px;background:transparent;color:var(--danger);border:1px solid var(--danger);border-radius:4px;cursor:pointer">Delete</button>
        </div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px">
          ${escH(z.booking_number)} • ${z.radius_miles}mi radius • ${z.violation_count||0} violations
          ${z.address?` • ${escH(z.address)}`:''}
        </div>
      </div>`;
    }).join('');
  }

  async function createZone() {
    const booking = document.getElementById('geoNewZoneBooking')?.value?.trim();
    const name = document.getElementById('geoNewZoneName')?.value?.trim();
    const type = document.getElementById('geoNewZoneType')?.value || 'inclusion';
    const lat = parseFloat(document.getElementById('geoNewZoneLat')?.value);
    const lng = parseFloat(document.getElementById('geoNewZoneLng')?.value);
    const radius = parseFloat(document.getElementById('geoNewZoneRadius')?.value);
    const address = document.getElementById('geoNewZoneAddress')?.value?.trim() || '';
    if (!booking || !name || isNaN(lat) || isNaN(lng) || isNaN(radius)) {
      toast('All zone fields required', 'error'); return;
    }
    try {
      await _post('/api/geo-intel/zones', { booking_number:booking, zone_type:type, name, center_lat:lat, center_lng:lng, radius_miles:radius, address });
      toast('✅ Geofence created', 'success');
      _clearForm('geoNewZone');
      await loadZones();
      await loadOverview();
    } catch (e) { toast('Zone creation failed: '+e.message, 'error'); }
  }

  async function deleteZone(zoneId) {
    if (!confirm('Delete this geofence zone?')) return;
    try {
      await _fetch(`/api/geo-intel/zones/${zoneId}`, {method:'DELETE'});
      toast('Zone deleted', 'success');
      await loadZones();
      await loadOverview();
    } catch (e) { toast('Delete failed: '+e.message, 'error'); }
  }

  // ══════════════════════════════════════════════════════════════════════════
  // VIOLATION FEED
  // ══════════════════════════════════════════════════════════════════════════
  async function loadViolations() {
    try {
      const res = await _fetch('/api/geo-intel/violations?limit=30');
      _violations = res.violations || [];
      _renderViolations();
    } catch (e) { console.warn('[GeoIntel] violations error:', e); }
  }

  function _renderViolations() {
    const el = document.getElementById('geoViolationFeed');
    if (!el) return;
    if (!_violations.length) {
      el.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">🎉 No geofence violations</div>';
      return;
    }
    el.innerHTML = _violations.map(v => {
      const acked = v.acknowledged;
      return `<div class="stat-card" style="padding:10px;border-left:3px solid ${acked?'var(--muted)':'var(--danger)'}; ${acked?'opacity:0.6':''}">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div>
            <span style="font-size:14px">${acked?'✓':'🚨'}</span>
            <strong style="font-size:12px">${escH(v.booking_number)}</strong>
            <span style="font-size:11px;color:var(--muted);margin-left:6px">${escH(v.zone_name)} (${v.zone_type})</span>
          </div>
          <span style="font-size:11px;color:var(--muted)">${timeAgo(v.created_at)}</span>
        </div>
        <div style="font-size:11px;color:var(--muted);margin-top:2px">
          ${v.distance_miles}mi from center • Device: ${escH(v.device_type||'')}
          ${!acked?` <button onclick="SLGeoIntel.ackViolation('${v.event_id}')" style="margin-left:8px;font-size:10px;padding:2px 8px;background:var(--surface);border:1px solid var(--border);border-radius:3px;cursor:pointer;color:var(--text)">Acknowledge</button>`:''}
        </div>
      </div>`;
    }).join('');
  }

  async function ackViolation(eventId) {
    try {
      await _post(`/api/geo-intel/violations/${eventId}/acknowledge`, { agent:'dashboard' });
      toast('Violation acknowledged', 'success');
      await loadViolations();
      await loadOverview();
    } catch (e) { toast('Ack failed: '+e.message, 'error'); }
  }

  // ══════════════════════════════════════════════════════════════════════════
  // VEHICLE WATCH
  // ══════════════════════════════════════════════════════════════════════════
  async function loadVehicles() {
    try {
      const res = await _fetch('/api/geo-intel/vehicle-watch');
      _vehicles = res.vehicles || [];
      _renderVehicles();
    } catch (e) { console.warn('[GeoIntel] vehicles error:', e); }
  }

  function _renderVehicles() {
    const el = document.getElementById('geoVehicleList');
    if (!el) return;
    if (!_vehicles.length) {
      el.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)">No vehicles on watch list</div>';
      return;
    }
    el.innerHTML = _vehicles.map(v => {
      const vi = v.vehicle_info || {};
      const desc = [vi.year,vi.make,vi.model,vi.color].filter(Boolean).join(' ');
      return `<div class="stat-card" style="padding:12px;border-left:3px solid var(--gold)">
        <div style="display:flex;align-items:center;gap:10px">
          <span style="font-size:20px">🚗</span>
          <div style="flex:1">
            <div style="font-weight:600;font-size:13px">${escH(desc||'Unknown Vehicle')}</div>
            <div style="font-size:11px;color:var(--muted)">${vi.plate?'Plate: '+escH(vi.plate)+' • ':''}${escH(v.booking_number)}</div>
            ${v.last_seen_at?`<div style="font-size:11px;color:var(--success)">Last seen: ${timeAgo(v.last_seen_at)} • ${v.sighting_count} sighting(s)</div>`:'<div style="font-size:11px;color:var(--gold)">No sightings yet</div>'}
          </div>
        </div>
      </div>`;
    }).join('');
  }

  async function addVehicle() {
    const booking = document.getElementById('geoNewVehicleBooking')?.value?.trim();
    const make = document.getElementById('geoNewVehicleMake')?.value?.trim();
    const model = document.getElementById('geoNewVehicleModel')?.value?.trim();
    const year = document.getElementById('geoNewVehicleYear')?.value?.trim();
    const color = document.getElementById('geoNewVehicleColor')?.value?.trim();
    const plate = document.getElementById('geoNewVehiclePlate')?.value?.trim();
    const reason = document.getElementById('geoNewVehicleReason')?.value?.trim() || '';
    if (!booking) { toast('Booking # required', 'error'); return; }
    try {
      await _post('/api/geo-intel/vehicle-watch', {
        booking_number: booking,
        vehicle_info: { make, model, year, color, plate },
        reason,
      });
      toast('✅ Vehicle added to watch list', 'success');
      _clearForm('geoNewVehicle');
      await loadVehicles();
      await loadOverview();
    } catch (e) { toast('Failed: '+e.message, 'error'); }
  }

  // ── Utilities ──
  function _clearForm(prefix) {
    document.querySelectorAll(`[id^="${prefix}"]`).forEach(el => {
      if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') el.value = '';
    });
  }

  // ── Public ──
  return {
    init, refreshAll, loadOverview, loadDevices, loadZones,
    loadViolations, loadVehicles, loadHealth,
    registerDevice, deactivateDevice,
    createZone, deleteZone,
    ackViolation,
    addVehicle,
  };
})();
