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
      const isStandby = h.status === 'standby';
      const statusColor = isOnline ? 'var(--success)' : (isStandby ? 'var(--gold)' : 'var(--danger)');
      const label = isOnline ? 'ONLINE' : (isStandby ? 'STANDBY' : escH(h.status).toUpperCase());

      el.innerHTML = `
        <div class="stat-card" style="padding:12px;border-left:3px solid ${statusColor}">
          <div style="display:flex;align-items:center;justify-content:space-between">
            <div style="display:flex;align-items:center;gap:8px">
              <span style="width:8px;height:8px;border-radius:50%;background:${statusColor};display:inline-block"></span>
              <strong>Traccar GPS Engine</strong>
              <span style="color:${statusColor};font-size:11px;font-weight:700;padding:2px 6px;border-radius:4px;background:rgba(255,255,255,0.06)">${label}</span>
            </div>
            <button onclick="SLGeoIntel.testPhonePing()" class="btn-sm" style="font-size:11px;padding:4px 10px;background:rgba(16,185,129,0.15);color:#10b981;border:1px solid rgba(16,185,129,0.4);border-radius:6px;cursor:pointer">📡 Test GPS Ping</button>
          </div>
          <div style="font-size:11px;color:var(--muted);margin-top:4px">
            User: ${escH(h.user || 'admin@shamrockbailbonds.biz')} • Admin: ${h.admin !== false ? 'Yes' : 'No'} ${h.message ? `• ${escH(h.message)}` : ''}
          </div>
        </div>`;
    } catch (e) {
      el.innerHTML = `
        <div class="stat-card" style="padding:12px;border-left:3px solid var(--gold)">
          <div style="display:flex;align-items:center;justify-content:space-between">
            <div>
              <strong style="color:var(--gold)">Traccar Engine Standby</strong>
              <div style="font-size:11px;color:var(--muted);margin-top:2px">Run docker compose up -d traccar to initialize service</div>
            </div>
            <button onclick="SLGeoIntel.testPhonePing()" class="btn-sm" style="font-size:11px;padding:4px 10px;background:rgba(16,185,129,0.15);color:#10b981;border:1px solid rgba(16,185,129,0.4);border-radius:6px;cursor:pointer">📡 Test GPS Ping</button>
          </div>
        </div>`;
    }
  }

  async function testPhonePing(phone) {
    const target = (phone || window.prompt('Phone number for GPS test ping (10 digits):') || '').trim();
    if (!target) {
      toast('GPS test ping cancelled — phone required', 'info');
      return;
    }
    toast(`📡 Sending test GPS ping for ${target}...`, 'info');
    try {
      const res = await _post('/api/geo-intel/test-phone-ping', { phone: target, booking_number: 'TEST-PING' });
      toast(`✅ Test location registered for ${target}! Lat: ${res.lat}, Lng: ${res.lng}`, 'success');
      await loadDevices();
      await loadOverview();
    } catch (e) {
      toast(`Ping failed: ${e.message}`, 'error');
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
      const lp = d.last_position || {};
      const attrs = lp.attributes || {};
      const batt = attrs.batt ?? attrs.batteryLevel ?? lp.battery;
      const battColor = (batt !== undefined && batt !== null) ? (batt > 50 ? '#10b981' : (batt > 20 ? '#f59e0b' : '#ef4444')) : '#94a3b8';
      
      const nowMs = Date.now();
      const lastSeenMs = d.last_seen ? new Date(d.last_seen).getTime() : 0;
      const diffMin = lastSeenMs ? Math.floor((nowMs - lastSeenMs) / 60000) : 999999;
      
      let statusColor = 'var(--muted)';
      let statusText = 'OFFLINE / NO FIX';
      if (d.status === 'active') {
        if (diffMin < 15) {
          statusColor = 'var(--success)';
          statusText = 'ONLINE · LIVE';
        } else if (diffMin < 240) {
          statusColor = 'var(--gold)';
          statusText = 'STANDBY';
        } else {
          statusColor = '#64748b';
          statusText = 'STALE';
        }
      } else {
        statusText = 'INACTIVE';
      }

      const hasCoords = lp.lat != null && lp.lng != null;
      const coordsStr = hasCoords ? `${parseFloat(lp.lat).toFixed(5)}, ${parseFloat(lp.lng).toFixed(5)}` : null;
      const mapsUrl = hasCoords ? `https://maps.google.com/?q=${lp.lat},${lp.lng}` : null;
      const setupUrl = d.setup_url || `https://leads.shamrockbailbonds.biz/traccar/setup/${encodeURIComponent(d.unique_id || d.booking_number)}`;
      const safeLabel = esc(d.label || d.booking_number || 'Device');

      return `
        <div class="stat-card" style="padding:16px;border-left:4px solid ${statusColor};margin-bottom:12px;background:var(--card-bg,#151c2c);border-radius:var(--radius-sm,8px)">
          <!-- Top Row: Device Info & Status -->
          <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
            <div style="display:flex;align-items:center;gap:12px">
              <span style="font-size:24px;padding:8px;background:rgba(255,255,255,0.04);border-radius:8px">${typeIcon}</span>
              <div>
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                  <span style="font-weight:700;font-size:15px;color:var(--text)">${escH(d.label || d.device_type)}</span>
                  <span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:12px;background:${statusColor}20;color:${statusColor};border:1px solid ${statusColor}40">
                    ● ${statusText}
                  </span>
                  ${batt !== undefined && batt !== null ? `
                    <span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:12px;background:${battColor}18;color:${battColor};border:1px solid ${battColor}35">
                      🔋 ${batt}%
                    </span>` : ''}
                </div>
                <div style="font-size:12px;color:var(--muted);margin-top:4px;display:flex;gap:12px;flex-wrap:wrap">
                  <span>ID: <strong style="color:#38bdf8">${escH(d.unique_id || '—')}</strong></span>
                  <span>Booking: <strong>${escH(d.booking_number || '—')}</strong></span>
                  <span>County: <strong>${escH(d.county || 'Lee')}</strong></span>
                  ${d.phone ? `<span>Phone: <strong style="color:var(--text)">${escH(d.phone)}</strong></span>` : ''}
                  ${d.traccar_device_id ? `<span>Traccar ID: <strong style="color:var(--muted)">#${escH(String(d.traccar_device_id))}</strong></span>` : ''}
                </div>
              </div>
            </div>

            <!-- Action buttons -->
            <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
              ${hasCoords ? `
                <button onclick="SLGeoIntel.showOnMap(${lp.lat}, ${lp.lng}, '${safeLabel}')" class="btn-sm" style="background:#0ea5e9;color:#fff;border:none;border-radius:6px;padding:5px 10px;font-size:11px;font-weight:600;cursor:pointer">
                  🗺️ Map
                </button>` : ''}
              <button onclick="SLGeoIntel.copySetupLink('${esc(setupUrl)}')" class="btn-sm" style="background:#1e293b;color:#cbd5e1;border:1px solid var(--border);border-radius:6px;padding:5px 10px;font-size:11px;font-weight:600;cursor:pointer" title="Copy 1-Click Setup Link">
                🔗 Copy Link
              </button>
              ${d.phone ? `
                <button onclick="SLGeoIntel.sendSetupSms('${esc(d.phone)}', '${esc(setupUrl)}')" class="btn-sm" style="background:rgba(16,185,129,0.15);color:#10b981;border:1px solid rgba(16,185,129,0.4);border-radius:6px;padding:5px 10px;font-size:11px;font-weight:600;cursor:pointer">
                  💬 Send Link
                </button>` : ''}
              <button onclick="SLGeoIntel.toggleTelemetry('${d.device_id}')" class="btn-sm" style="background:#1e293b;color:#94a3b8;border:1px solid var(--border);border-radius:6px;padding:5px 8px;font-size:11px;cursor:pointer" title="Telemetry Inspector">
                🔍 Raw
              </button>
              ${d.status === 'active' ? `
                <button onclick="SLGeoIntel.deactivateDevice('${d.device_id}')" class="btn-sm" style="background:rgba(239,68,68,0.15);color:#ef4444;border:1px solid rgba(239,68,68,0.4);border-radius:6px;padding:5px 8px;font-size:11px;cursor:pointer" title="Deactivate">✕</button>` : ''}
            </div>
          </div>

          <!-- Middle Row: Telemetry Chips -->
          ${hasCoords ? `
            <div style="display:flex;align-items:center;gap:12px;margin-top:12px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.06);font-size:12px;color:var(--text);flex-wrap:wrap">
              <a href="${mapsUrl}" target="_blank" style="color:var(--accent);text-decoration:none;font-weight:600;display:inline-flex;align-items:center;gap:4px">
                📍 ${coordsStr} ↗
              </a>
              ${lp.accuracy ? `<span style="color:var(--muted)">Accuracy: <strong style="color:var(--text)">±${Math.round(lp.accuracy)}m</strong></span>` : ''}
              ${lp.speed != null ? `<span style="color:var(--muted)">Speed: <strong style="color:var(--text)">${Math.round(lp.speed)} mph</strong></span>` : ''}
              ${lp.altitude != null ? `<span style="color:var(--muted)">Altitude: <strong style="color:var(--text)">${Math.round(lp.altitude)}m</strong></span>` : ''}
              <span style="color:var(--muted);margin-left:auto">Last fix: <strong>${timeAgo(d.last_seen || lp.timestamp)}</strong> (${d.last_seen ? new Date(d.last_seen).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '—'})</span>
            </div>` : `
            <div style="margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.06);font-size:12px;color:var(--gold)">
              ⚠️ No position fix received yet. Send the setup link to the defendant to activate tracking.
            </div>`}

          <!-- Collapsible Raw Telemetry Drawer -->
          <div id="raw-telemetry-${d.device_id}" style="display:none;margin-top:12px;padding:12px;background:#0b0f19;border-radius:8px;border:1px solid var(--border);font-family:monospace;font-size:11px;color:#94a3b8;max-height:220px;overflow:auto">
            <div style="display:flex;justify-content:space-between;margin-bottom:6px;color:var(--text);font-weight:700">
              <span>Fix Telemetry & Attributes</span>
              <span style="color:var(--accent)">Traccar Device ID: ${d.traccar_device_id || '—'}</span>
            </div>
            <pre style="margin:0;white-space:pre-wrap">${escH(JSON.stringify({ last_position: lp, last_seen: d.last_seen, attributes: attrs }, null, 2))}</pre>
          </div>
        </div>
      `;
    }).join('');
  }

  function copySetupLink(url) {
    navigator.clipboard.writeText(url).then(() => {
      toast('✅ 1-Click Setup Link copied to clipboard!', 'success');
    }).catch(() => {
      window.prompt('Copy setup URL:', url);
    });
  }

  async function sendSetupSms(phone, url) {
    if (!phone) { toast('No phone number attached to device', 'error'); return; }
    if (!confirm(`Send 1-click GPS setup link to ${phone} via BlueBubbles iMessage?`)) return;
    toast(`📡 Sending setup message to ${phone}...`, 'info');
    try {
      const msg = `Shamrock Bail Bonds - Please tap this link to activate your required GPS monitoring: ${url}`;
      await _post('/api/bb/send', { recipient: phone, message: msg });
      toast(`✅ Setup link sent via BlueBubbles to ${phone}!`, 'success');
    } catch (e) {
      toast('Failed to send message: ' + e.message, 'error');
    }
  }

  function showOnMap(lat, lng, label) {
    if (window.SLTracking?.focusDevice) {
      SLTracking.focusDevice(lat, lng, label);
    } else {
      window.open(`https://maps.google.com/?q=${lat},${lng}`, '_blank');
    }
  }

  function toggleTelemetry(deviceId) {
    const el = document.getElementById(`raw-telemetry-${deviceId}`);
    if (el) {
      el.style.display = el.style.display === 'none' ? 'block' : 'none';
    }
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
    registerDevice, deactivateDevice, testPhonePing,
    createZone, deleteZone,
    ackViolation,
    addVehicle,
    copySetupLink,
    sendSetupSms,
    showOnMap,
    toggleTelemetry,
  };
})();
