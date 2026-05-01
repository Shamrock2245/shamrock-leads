/* ShamrockLeads — Active Bonds: Geolocation & Risk Mitigation Module */

// ── State ──
let _abBonds = [];
let _abFilter = 'all';
let _abCheckinBooking = '';
let _abCheckinName = '';

// ── Risk Score Helpers ──
function riskClass(score) {
  if (score >= 75) return 'score-hot';
  if (score >= 50) return 'score-warm';
  return 'score-cold';
}

function riskLabel(score) {
  if (score >= 75) return '🔴 HIGH';
  if (score >= 50) return '🟡 MED';
  return '🟢 LOW';
}

function statusBadgeClass(status) {
  switch ((status || '').toLowerCase()) {
    case 'alert':      return 'status-offline';
    case 'active':     return 'status-healthy';
    case 'monitoring': return 'status-stale';
    case 'exonerated': return 'status-healthy';
    case 'forfeited':  return 'status-offline';
    case 'surrendered': return 'status-stale';
    default:           return 'status-stale';
  }
}

// ── Load Active Bonds from API ──
async function loadActiveBonds() {
  try {
    const statusParam = (_abFilter && _abFilter !== 'all') ? `?status=${_abFilter}` : '';
    const r = await fetch(`${API}/api/active-bonds${statusParam}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    _abBonds = data.bonds || [];

    // Update KPIs
    document.getElementById('abKpiTotal').textContent = (data.total || 0).toLocaleString();
    document.getElementById('abKpiAlerts').textContent = (data.alerts || 0).toLocaleString();
    document.getElementById('abKpiHighRisk').textContent = (data.high_risk || 0).toLocaleString();
    document.getElementById('activeBondsBadge').textContent = data.total || 0;

    // Count check-ins today
    const today = new Date().toISOString().slice(0, 10);
    const checkinsToday = _abBonds.reduce((sum, b) => {
      const hist = b.location_history || [];
      return sum + hist.filter(h => (h.timestamp || '').startsWith(today)).length;
    }, 0);
    document.getElementById('abKpiCheckins').textContent = checkinsToday.toLocaleString();

    document.getElementById('abMeta').textContent = `${_abBonds.length} bonds · Updated ${new Date(data.updated_at || Date.now()).toLocaleTimeString()}`;

    renderActiveBondsTable();
  } catch (e) {
    console.error('loadActiveBonds error:', e);
    document.getElementById('abTableBody').innerHTML =
      `<tr><td colspan="11" style="color:var(--danger);text-align:center">Error loading active bonds: ${e.message}</td></tr>`;
  }
}

// ── Render Table ──
function renderActiveBondsTable() {
  const tbody = document.getElementById('abTableBody');
  if (!_abBonds.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="loading">No active bonds found. Write a bond to get started.</td></tr>';
    return;
  }

  tbody.innerHTML = _abBonds.map(b => {
    const risk = b.risk_score || 0;
    const rCls = riskClass(risk);
    const sCls = statusBadgeClass(b.status);
    const overdue = b.check_in_overdue;
    const hoursOver = b.hours_overdue || 0;
    const lastCI = b.last_check_in ? timeAgo(b.last_check_in) : '<span style="color:var(--muted)">Never</span>';
    const nextDue = b.next_check_in_due ? new Date(b.next_check_in_due).toLocaleDateString('en-US', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
    const nextDueStyle = overdue ? 'color:var(--danger);font-weight:700' : '';
    const overdueLabel = overdue ? `<br><span style="color:var(--danger);font-size:10px">⚠️ ${hoursOver}h overdue</span>` : '';
    const chargesRaw = b.charges_raw || b.charges || '';
    const charges = typeof chargesRaw === 'string'
      ? (chargesRaw.length > 60 ? chargesRaw.slice(0, 57) + '…' : (chargesRaw || '—'))
      : Array.isArray(chargesRaw) ? (chargesRaw.slice(0, 2).join(', ') + (chargesRaw.length > 2 ? ` +${chargesRaw.length - 2} more` : '')) : '—';
    const indemnitorName = (b.indemnitor?.name || b.indemnitor_name || [b.indemnitor?.firstName, b.indemnitor?.lastName].filter(Boolean).join(' ') || '—');
    const alerts = (b.alerts || []).length;
    const alertBadge = alerts > 0 ? `<span style="background:var(--danger);color:#fff;border-radius:10px;padding:1px 6px;font-size:10px;margin-left:4px">${alerts}</span>` : '';
    const bkSafe = (b.booking_number || '').replace(/'/g, "\\'");
    const nameSafe = (b.defendant_name || '').replace(/'/g, "\\'");

    return `<tr class="${overdue ? 'row-alert' : ''}" style="${overdue ? 'background:rgba(239,68,68,0.05)' : ''}">
      <td>
        <div style="font-weight:600">${b.defendant_name || '—'}${alertBadge}</div>
        <div style="font-size:11px;color:var(--muted)">${b.booking_number || '—'}</div>
      </td>
      <td>${b.county || '—'}</td>
      <td>$${(b.bond_amount || 0).toLocaleString()}</td>
      <td><span style="font-size:11px;background:var(--panel);padding:2px 6px;border-radius:4px">${b.surety || '—'}</span></td>
      <td style="font-size:11px;max-width:120px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${indemnitorName}">${indemnitorName}</td>
      <td style="font-size:11px;max-width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${(b.charges_raw||typeof chargesRaw==='string'?chargesRaw:'').replace(/"/g,"'")}">        
        ${charges || '—'}
      </td>
      <td>
        <span class="score-pill ${rCls}" style="cursor:pointer" onclick="showRiskBreakdown('${bkSafe}')">
          ${risk} ${riskLabel(risk)}
        </span>
      </td>
      <td>${lastCI}</td>
      <td style="${nextDueStyle}">${nextDue}${overdueLabel}</td>
      <td><span class="status-badge ${sCls}">${b.status || 'active'}</span></td>
      <td>
        <div style="display:flex;gap:4px;flex-wrap:wrap">
          <button class="btn-export" style="font-size:10px;padding:3px 8px" onclick="openCheckinModal('${bkSafe}','${nameSafe}')">📍 Check-In</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px" onclick="showLocationHistory('${bkSafe}')">🗺️ History</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#3b82f6;color:#fff" onclick="openInTracking('${bkSafe}')">🗺️ Track</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:var(--danger)" onclick="addManualAlert('${bkSafe}','${nameSafe}')">🚨 Alert</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#22c55e;color:#fff" onclick="exonerateFromActiveBonds('${bkSafe}','${nameSafe}')">✅ Exonerate</button>
          <select style="font-size:10px;padding:3px;background:var(--panel);border:1px solid var(--border);border-radius:4px;color:var(--text)" onchange="updateBondStatus('${bkSafe}',this.value);this.value=''">
            <option value="">Status…</option>
            <option value="active">Active</option>
            <option value="monitoring">Monitoring</option>
            <option value="alert">Alert</option>
            <option value="exonerated">Exonerated</option>
            <option value="surrendered">Surrendered</option>
            <option value="forfeited">Forfeited</option>
          </select>
        </div>
      </td>
    </tr>`;
  }).join('');
}

// ── Filter ──
function filterActiveBonds(status) {
  _abFilter = status;
  // Update button states
  document.querySelectorAll('#abStatusFilter button').forEach(btn => {
    btn.classList.toggle('active', btn.textContent.toLowerCase().includes(status === 'all' ? 'all' : status));
  });
  loadActiveBonds();
}

// ── Check-In Modal ──
function openCheckinModal(booking, name) {
  _abCheckinBooking = booking;
  _abCheckinName = name;
  document.getElementById('abCheckinDefName').textContent = `📍 Check-In: ${name}`;
  document.getElementById('abCheckinLat').value = '';
  document.getElementById('abCheckinLng').value = '';
  document.getElementById('abCheckinCounty').value = '';
  document.getElementById('abCheckinSource').value = 'manual';
  document.getElementById('abCheckinModal').classList.add('show');

  // Try to get browser geolocation
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(pos => {
      document.getElementById('abCheckinLat').value = pos.coords.latitude.toFixed(6);
      document.getElementById('abCheckinLng').value = pos.coords.longitude.toFixed(6);
      document.getElementById('abCheckinSource').value = 'gps';
    }, () => {});
  }
}

function closeCheckinModal() {
  document.getElementById('abCheckinModal').classList.remove('show');
  _abCheckinBooking = '';
  _abCheckinName = '';
}

async function submitCheckin() {
  if (!_abCheckinBooking) { toast('No booking selected', 'error'); return; }
  const lat = parseFloat(document.getElementById('abCheckinLat').value) || null;
  const lng = parseFloat(document.getElementById('abCheckinLng').value) || null;
  const county = document.getElementById('abCheckinCounty').value.trim();
  const source = document.getElementById('abCheckinSource').value;

  try {
    const r = await fetch(`${API}/api/active-bonds/${encodeURIComponent(_abCheckinBooking)}/check-in`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat, lng, county, source, accuracy: 0 }),
    });
    const result = await r.json();
    if (result.success) {
      toast(`✅ Check-in recorded for ${_abCheckinName}${result.out_of_area ? ' ⚠️ OUT OF AREA' : ''}`, result.out_of_area ? 'error' : 'success');
      closeCheckinModal();
      loadActiveBonds();
    } else {
      toast(result.error || 'Check-in failed', 'error');
    }
  } catch (e) {
    toast('Network error during check-in', 'error');
  }
}

// ── Location History ──
function showLocationHistory(booking) {
  const bond = _abBonds.find(b => b.booking_number === booking);
  if (!bond) return;
  const panel = document.getElementById('abLocationPanel');
  const body = document.getElementById('abLocationBody');
  document.getElementById('abLocationTitle').textContent = `📍 Location History — ${bond.defendant_name || booking}`;

  const history = (bond.location_history || []).slice().reverse();
  if (!history.length) {
    body.innerHTML = '<p style="color:var(--muted);padding:12px">No location history recorded yet.</p>';
  } else {
    body.innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>Timestamp</th><th>Lat</th><th>Lng</th><th>County</th><th>Source</th><th>Accuracy</th></tr></thead>
      <tbody>${history.map(h => `<tr>
        <td>${h.timestamp ? new Date(h.timestamp).toLocaleString() : '—'}</td>
        <td>${h.lat != null ? h.lat.toFixed(5) : '—'}</td>
        <td>${h.lng != null ? h.lng.toFixed(5) : '—'}</td>
        <td>${h.county || '—'}</td>
        <td>${h.source || '—'}</td>
        <td>${h.accuracy ? h.accuracy + 'm' : '—'}</td>
      </tr>`).join('')}</tbody>
    </table></div>`;
  }

  // Show alerts too
  const alerts = (bond.alerts || []).slice().reverse();
  if (alerts.length) {
    body.innerHTML += `<div style="margin-top:16px"><div class="panel-title">🚨 Alerts (${alerts.length})</div>
      ${alerts.map(a => `<div style="padding:8px;margin:4px 0;background:var(--panel);border-radius:6px;border-left:3px solid ${a.severity==='high'||a.type==='missed_check_in'?'var(--danger)':'var(--warning)'}">
        <div style="font-size:12px;font-weight:600">${a.type?.replace(/_/g,' ').toUpperCase() || 'ALERT'} <span style="font-size:10px;color:var(--muted)">${a.timestamp ? new Date(a.timestamp).toLocaleString() : ''}</span></div>
        <div style="font-size:11px;color:var(--muted)">${a.message || ''}</div>
      </div>`).join('')}
    </div>`;
  }

  panel.style.display = 'block';
  panel.scrollIntoView({ behavior: 'smooth' });
}

// ── Risk Breakdown ──
function showRiskBreakdown(booking) {
  const bond = _abBonds.find(b => b.booking_number === booking);
  if (!bond) return;
  const risk = bond.risk_score || 0;
  const missed = bond.missed_check_ins || 0;
  const outOfArea = bond.out_of_area_count || 0;
  const bondAmt = bond.bond_amount || 0;
  const charges = (bond.charges_raw || '').toUpperCase();
  const highRiskKw = ['MURDER','HOMICIDE','ROBBERY','TRAFFICKING','ASSAULT','WEAPON','FIREARM','FLEE','ESCAPE','FUGITIVE'];
  const hasHighRisk = highRiskKw.some(kw => charges.includes(kw));

  const breakdown = [
    { label: 'Baseline', value: 50 },
    { label: `Missed check-ins (${missed})`, value: Math.min(missed * 10, 30) },
    { label: `Out-of-area pings (${outOfArea})`, value: Math.min(outOfArea * 8, 24) },
    { label: `Bond amount ($${bondAmt.toLocaleString()})`, value: bondAmt >= 50000 ? 10 : bondAmt >= 25000 ? 5 : 0 },
    { label: 'High-risk charge keywords', value: hasHighRisk ? 5 : 0 },
    { label: `Location history (${(bond.location_history||[]).length} pings)`, value: (bond.location_history||[]).length >= 3 ? -5 : 0 },
  ].filter(r => r.value !== 0);

  // Show in a toast/panel instead of alert()
  const lines = breakdown.map(r => `<div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border);font-size:12px"><span>${r.label}</span><span style="font-weight:700;color:${r.value > 0 ? 'var(--danger)' : 'var(--accent)'}">${r.value > 0 ? '+' : ''}${r.value}</span></div>`).join('');
  const modal = document.createElement('div');
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center';
  modal.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:24px;max-width:420px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,.5)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <h3 style="margin:0;font-size:16px">🧠 Risk Score Breakdown</h3>
      <button onclick="this.closest('[style*=fixed]').remove()" style="background:none;border:none;color:var(--text);font-size:20px;cursor:pointer">✕</button>
    </div>
    <div style="font-size:28px;font-weight:700;text-align:center;margin-bottom:16px;color:${risk >= 75 ? 'var(--danger)' : risk >= 50 ? 'var(--gold)' : 'var(--accent)'}">${risk}<span style="font-size:14px;color:var(--muted)">/100</span></div>
    ${lines}
    <div style="margin-top:14px;font-size:11px;color:var(--muted);text-align:center">Score updates automatically as check-ins and alerts are recorded.</div>
  </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

// ── Manual Alert ──
async function addManualAlert(booking, name) {
  const message = prompt(`Add alert for ${name}:\nDescribe the issue (e.g. "FTA - warrant issued", "Missed court date"):`);
  if (!message) return;
  const severity = confirm('Is this HIGH severity? (OK = High, Cancel = Medium)') ? 'high' : 'medium';
  try {
    const r = await fetch(`${API}/api/active-bonds/${encodeURIComponent(booking)}/alert`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'manual', message, severity }),
    });
    const result = await r.json();
    if (result.success) {
      toast(`🚨 Alert added for ${name}`, 'error');
      loadActiveBonds();
    } else {
      toast(result.error || 'Alert failed', 'error');
    }
  } catch (e) {
    toast('Network error', 'error');
  }
}

// ── Update Bond Status ──
async function updateBondStatus(booking, newStatus) {
  if (!newStatus) return;
  try {
    const r = await fetch(`${API}/api/active-bonds/${encodeURIComponent(booking)}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus }),
    });
    const result = await r.json();
    if (result.success) {
      toast(`Status updated to ${newStatus}`, 'success');
      loadActiveBonds();
    } else {
      toast(result.error || 'Update failed', 'error');
    }
  } catch (e) {
    toast('Network error', 'error');
  }
}

// ── Open in Tracking Tab ──
// Switches to the Tracking tab and opens the detail panel for this booking number.
function openInTracking(bookingNumber) {
  // Switch to Tracking tab
  const trackTab = document.querySelector('[data-tab="tabTracking"]') ||
                   document.querySelector('button[onclick*="tabTracking"]') ||
                   Array.from(document.querySelectorAll('.tab-btn')).find(b => b.textContent.includes('Tracking'));
  if (trackTab) trackTab.click();
  // Pre-fill the search box and open detail after a short delay
  setTimeout(() => {
    const searchEl = document.getElementById('trkSearch');
    if (searchEl) { searchEl.value = bookingNumber; searchEl.dispatchEvent(new Event('input')); }
    if (window.SLTracking) SLTracking.openDetail(bookingNumber);
  }, 350);
}

// ── Exonerate Bond from Active Bonds Tab ──
// Calls the tracking exoneration endpoint and refreshes both tabs.
async function exonerateFromActiveBonds(bookingNumber, defName) {
  const note = prompt(
    '✅ Exonerate bond for ' + defName + '?\n\n' +
    'This will:\n' +
    '  • Stop all location tracking immediately\n' +
    '  • Cancel all pending GPS capture links\n' +
    '  • Cancel all pending court reminders\n\n' +
    'Enter a note (e.g. "Discharge email from Lee County Clerk") or leave blank:'
  );
  if (note === null) return; // User pressed Cancel
  const notifyIndem = confirm('Notify indemnitor via iMessage that the bond is officially discharged?');
  try {
    const r = await fetch(`${API}/api/tracking/${encodeURIComponent(bookingNumber)}/exonerate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source: 'manual',
        note: note || 'Manual exoneration from Active Bonds tab',
        notify_indemnitor: notifyIndem,
      }),
    });
    const data = await r.json();
    if (data.success) {
      toast('✅ ' + defName + ' exonerated — tracking stopped', 'success');
      loadActiveBonds();
      // Refresh tracking tab data and exoneration log
      if (window.SLTracking) {
        SLTracking.refresh();
        SLTracking.onBondExonerated({ booking_number: bookingNumber, defendant_name: defName });
      }
    } else if (data.already_exonerated) {
      toast(defName + ' was already exonerated on ' + (data.exonerated_at ? new Date(data.exonerated_at).toLocaleDateString() : '—'), 'info');
    } else {
      toast('❌ ' + (data.error || 'Exoneration failed'), 'error');
    }
  } catch (e) {
    toast('Network error during exoneration', 'error');
  }
}

// ── Process Missed Check-Ins ──
async function processMissedCheckins() {
  try {
    const r = await fetch(`${API}/api/active-bonds/missed-checkins`, { method: 'POST' });
    const result = await r.json();
    if (result.success) {
      toast(`Processed ${result.processed} overdue check-ins`, result.processed > 0 ? 'error' : 'success');
      loadActiveBonds();
    } else {
      toast(result.error || 'Processing failed', 'error');
    }
  } catch (e) {
    toast('Network error', 'error');
  }
}
