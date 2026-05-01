/* ═══════════════════════════════════════════════════════════════════════
   ShamrockLeads — Active Bonds Module  (sl-active-bonds.js)
   Full-featured: editable records, Add Bond, location history, exoneration
   ═══════════════════════════════════════════════════════════════════════ */

const API = '';

/* ── Tiny helpers ─────────────────────────────────────────────────── */
function toast(msg, type = 'info', duration = 3500) {
  const t = document.createElement('div');
  t.className = `sl-toast sl-toast-${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  if (duration > 0) setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, duration);
}
function fmtCurrency(n) { return n ? '$' + Number(n).toLocaleString() : '—'; }
function fmtDate(s) { if (!s) return '—'; try { return new Date(s).toLocaleDateString(); } catch { return s; } }
function escHtml(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
function timeAgo(ts) {
  if (!ts) return '—';
  const diff = Date.now() - new Date(ts).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/* ── State ────────────────────────────────────────────────────────── */
let _abBonds = [];
let _abFilter = 'all';
let _abCheckinBooking = '';
let _abCheckinName = '';
let _abEditingBooking = null;

/* ══════════════════════════════════════════════════════════════════
   LOAD & RENDER
   ══════════════════════════════════════════════════════════════════ */
async function loadActiveBonds() {
  try {
    const r = await fetch(`${API}/api/active-bonds`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    _abBonds = data.bonds || [];

    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('abKpiTotal',    (data.total || _abBonds.length).toLocaleString());
    set('abKpiAlerts',   (data.alerts || _abBonds.filter(b => (b.alert_count || (b.alerts||[]).length) > 0).length).toLocaleString());
    set('abKpiHighRisk', (data.high_risk || _abBonds.filter(b => (b.risk_score||0) >= 70).length).toLocaleString());
    set('activeBondsBadge', data.total || _abBonds.length);

    const today = new Date().toISOString().slice(0, 10);
    const checkinsToday = _abBonds.reduce((sum, b) => {
      return sum + (b.location_history || []).filter(h => (h.timestamp || '').startsWith(today)).length;
    }, 0);
    set('abKpiCheckins', checkinsToday.toLocaleString());
    set('abMeta', `${_abBonds.length} bonds · Updated ${new Date(data.updated_at || Date.now()).toLocaleTimeString()}`);

    renderActiveBondsTable();
  } catch (e) {
    console.error('loadActiveBonds error:', e);
    const tbody = document.getElementById('abTableBody');
    if (tbody) tbody.innerHTML = `<tr><td colspan="12" style="color:var(--danger);text-align:center;padding:24px">Error loading active bonds: ${e.message}</td></tr>`;
  }
}

/* ── Feature B: CSV Export ─────────────────────────────── */
function exportActiveBondsCSV() {
  if (!_abBonds.length) { toast('No bonds to export', 'error'); return; }
  const headers = ['Defendant','Booking #','County','Bond Amount','Premium','Surety','POA #','Court Date','Days Until Court','Indemnitor','Indemnitor Phone','Status','Risk Score','Last Check-In','Charges'];
  const rows = _abBonds.map(b => {
    const cd = b.court_date ? new Date(b.court_date) : null;
    const daysUntil = cd ? Math.ceil((cd - new Date()) / 86400000) : '';
    return [
      b.defendant_name || '', b.booking_number || '', b.county || '',
      b.bond_amount || 0, b.premium || '', (b.insurance_company || b.surety || ''),
      b.poa_number || '', b.court_date ? cd.toLocaleDateString() : '', daysUntil,
      b.indemnitor?.name || b.indemnitor_name || '', b.indemnitor?.phone || b.indemnitor_phone || '',
      b.status || 'active', b.risk_score || 0,
      b.last_check_in ? new Date(b.last_check_in).toLocaleString() : 'Never',
      (typeof b.charges_raw === 'string' ? b.charges_raw : (b.charges || '')).replace(/,/g, ';')
    ].map(v => `"${String(v).replace(/"/g, '""')}"`);
  });
  const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `active-bonds-${new Date().toISOString().slice(0,10)}.csv`;
  a.click(); URL.revokeObjectURL(url);
  toast(`Exported ${_abBonds.length} bonds to CSV`, 'success');
}

/* ── Feature E: Duplicate Indemnitor Phone Detection ──── */
function _detectDuplicatePhones() {
  const phoneMap = {}; // normalized phone → [{booking, defendant, indemnitor}]
  _abBonds.forEach(b => {
    const phone = (b.indemnitor?.phone || b.indemnitor_phone || '').replace(/\D/g, '');
    if (phone.length >= 10) {
      const norm = phone.slice(-10); // last 10 digits
      if (!phoneMap[norm]) phoneMap[norm] = [];
      phoneMap[norm].push({
        booking: b.booking_number,
        defendant: b.defendant_name,
        indemnitor: b.indemnitor?.name || b.indemnitor_name || 'Unknown',
        bond: b.bond_amount || 0
      });
    }
  });
  const dupes = Object.entries(phoneMap).filter(([, entries]) => entries.length > 1);
  const banner = document.getElementById('abDupePhoneBanner');
  if (!banner) return;
  if (dupes.length === 0) { banner.style.display = 'none'; return; }
  banner.style.display = 'block';
  const dupeHtml = dupes.map(([phone, entries]) => {
    const formatted = phone.replace(/(\d{3})(\d{3})(\d{4})/, '($1) $2-$3');
    const bonds = entries.map(e => `<span style="font-weight:600">${escHtml(e.defendant)}</span> ($${e.bond.toLocaleString()})`).join(', ');
    return `<div style="padding:6px 0;border-bottom:1px solid rgba(239,68,68,0.15)">📱 <strong>${formatted}</strong> — ${bonds}</div>`;
  }).join('');
  banner.innerHTML = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px"><span style="font-size:16px">⚠️</span><strong>Duplicate Indemnitor Phone${dupes.length > 1 ? 's' : ''} Detected (${dupes.length})</strong><button onclick="this.closest('.ab-alert-banner').style.display='none'" style="margin-left:auto;background:none;border:none;color:var(--danger);cursor:pointer;font-size:14px">✕</button></div>${dupeHtml}`;
}

/* ── Feature G: POA Low-Stock Alert ────────────────────── */
async function _checkPoaStock() {
  const banner = document.getElementById('abPoaStockBanner');
  if (!banner) return;
  try {
    const r = await fetch(`${API}/api/poa/inventory-summary`);
    if (!r.ok) return;
    const d = await r.json();
    const lowTiers = (d.tiers || []).filter(t => t.available <= 5 && t.available > 0);
    const emptyTiers = (d.tiers || []).filter(t => t.available === 0);
    if (lowTiers.length === 0 && emptyTiers.length === 0) { banner.style.display = 'none'; return; }
    banner.style.display = 'block';
    let html = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px"><span style="font-size:16px">${emptyTiers.length > 0 ? '🚨' : '⚠️'}</span><strong>POA Inventory ${emptyTiers.length > 0 ? 'CRITICAL' : 'Low Stock'}</strong><button onclick="this.closest('.ab-alert-banner').style.display='none'" style="margin-left:auto;background:none;border:none;color:var(--warning);cursor:pointer;font-size:14px">✕</button></div>`;
    if (emptyTiers.length > 0) {
      html += emptyTiers.map(t => `<div style="color:var(--danger)">🚫 <strong>${t.prefix}</strong> — EMPTY (${t.surety})</div>`).join('');
    }
    if (lowTiers.length > 0) {
      html += lowTiers.map(t => `<div>⚠️ <strong>${t.prefix}</strong> — ${t.available} remaining (${t.surety})</div>`).join('');
    }
    banner.innerHTML = html;
  } catch(e) { /* non-fatal */ }
}

function renderActiveBondsTable() {
  const tbody = document.getElementById('abTableBody');
  if (!tbody) return;

  let filtered = _abFilter === 'all'       ? _abBonds
    : _abFilter === 'active'     ? _abBonds.filter(b => b.status === 'active')
    : _abFilter === 'alerts'     ? _abBonds.filter(b => (b.alert_count || (b.alerts||[]).length) > 0)
    : _abFilter === 'monitoring' ? _abBonds.filter(b => b.status === 'monitoring')
    : _abFilter === 'exonerated' ? _abBonds.filter(b => b.status === 'exonerated')
    : _abBonds;

  // Search filter
  const searchEl = document.getElementById('abSearch');
  const q = (searchEl ? searchEl.value : '').trim().toLowerCase();
  if (q) {
    filtered = filtered.filter(b => {
      const indName = b.indemnitor?.name || b.indemnitor_name || '';
      return (b.defendant_name || '').toLowerCase().includes(q)
        || (b.booking_number || '').toLowerCase().includes(q)
        || (b.county || '').toLowerCase().includes(q)
        || indName.toLowerCase().includes(q)
        || (b.charges_raw || b.charges || '').toString().toLowerCase().includes(q)
        || (b.poa_number || '').toLowerCase().includes(q);
    });
  }

  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="12" style="text-align:center;padding:32px;color:var(--muted)">No bonds match this filter.<br><button class="btn-export" style="margin-top:12px" onclick="openAddBondModal()">➕ Add First Bond</button></td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(b => {
    const risk = b.risk_score || 0;
    const rCls = risk >= 75 ? 'score-hot' : risk >= 50 ? 'score-warm' : 'score-cold';
    const sCls = { alert:'status-offline', active:'status-healthy', monitoring:'status-stale', exonerated:'status-healthy', forfeited:'status-offline', surrendered:'status-stale' }[b.status] || 'status-stale';
    const overdue = b.check_in_overdue;
    const hoursOver = b.hours_overdue || 0;
    const lastCI = b.last_check_in ? timeAgo(b.last_check_in) : '<span style="color:var(--danger)">Never</span>';
    const nextDue = b.next_check_in_due ? new Date(b.next_check_in_due).toLocaleDateString('en-US',{month:'short',day:'numeric'}) : '—';
    const nextDueStyle = overdue ? 'color:var(--danger);font-weight:700' : '';
    const overdueLabel = overdue ? `<br><span style="color:var(--danger);font-size:10px">⚠️ ${hoursOver}h overdue</span>` : '';
    const chargesRaw = b.charges_raw || b.charges || '';
    const charges = typeof chargesRaw === 'string'
      ? (chargesRaw.length > 55 ? chargesRaw.slice(0, 52) + '…' : (chargesRaw || '—'))
      : Array.isArray(chargesRaw) ? (chargesRaw.slice(0, 2).join(', ') + (chargesRaw.length > 2 ? ` +${chargesRaw.length - 2}` : '')) : '—';
    const indemnitorName = b.indemnitor?.name || b.indemnitor_name || [b.indemnitor?.firstName, b.indemnitor?.lastName].filter(Boolean).join(' ') || '—';
    const alerts = (b.alerts || []).length + (b.alert_count || 0);
    const alertBadge = alerts > 0 ? `<span style="background:var(--danger);color:#fff;border-radius:10px;padding:1px 6px;font-size:10px;margin-left:4px">${alerts}</span>` : '';
    const ins = (b.insurance_company || b.surety || '').toUpperCase();
    const insBadge = ins.includes('PALM') || ins.includes('PSC')
      ? `<span style="font-size:10px;background:#166534;color:#86efac;padding:2px 6px;border-radius:4px">🌴 PSC</span>`
      : `<span style="font-size:10px;background:#1e3a5f;color:#93c5fd;padding:2px 6px;border-radius:4px">🛡️ OSI</span>`;
    const bkSafe = (b.booking_number || '').replace(/'/g, "\\'");
    const nameSafe = (b.defendant_name || '').replace(/'/g, "\\'");
    const factorsSafe = encodeURIComponent(JSON.stringify(b.risk_factors || {}));

    /* Feature A: Court Date Countdown */
    let courtCountdown = '—';
    if (b.court_date) {
      const cd = new Date(b.court_date);
      const diff = Math.ceil((cd - new Date()) / 86400000);
      if (diff < 0) courtCountdown = `<span style="color:var(--danger);font-weight:700">${Math.abs(diff)}d ago ⚠️</span>`;
      else if (diff === 0) courtCountdown = `<span style="color:var(--danger);font-weight:700">TODAY 🔴</span>`;
      else if (diff <= 3) courtCountdown = `<span style="color:var(--danger);font-weight:600">${diff}d</span>`;
      else if (diff <= 7) courtCountdown = `<span style="color:#f59e0b;font-weight:600">${diff}d</span>`;
      else if (diff <= 14) courtCountdown = `<span style="color:#3b82f6">${diff}d</span>`;
      else courtCountdown = `<span style="color:var(--muted)">${diff}d</span>`;
      courtCountdown += `<div style="font-size:10px;color:var(--muted)">${cd.toLocaleDateString('en-US',{month:'short',day:'numeric'})}</div>`;
    }

    return `<tr class="${overdue ? 'row-alert' : ''}" style="${overdue ? 'background:rgba(239,68,68,0.05)' : ''}">
      <td>
        <div style="font-weight:600">${escHtml(b.defendant_name || '—')}${alertBadge}</div>
        <div style="font-size:11px;color:var(--muted)">${escHtml(b.booking_number || '—')}</div>
      </td>
      <td>${escHtml(b.county || '—')}</td>
      <td><strong>$${(b.bond_amount || 0).toLocaleString()}</strong></td>
      <td>${insBadge}</td>
      <td style="font-size:11px;max-width:120px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${escHtml(indemnitorName)}">
        ${indemnitorName && indemnitorName !== '—'
          ? `<a href="#" style="color:var(--accent);text-decoration:none" onclick="event.preventDefault();crossLinkToDefendants('${escHtml(indemnitorName).replace(/'/g,"\\'")}')">${escHtml(indemnitorName)}</a>`
          : '—'}
      </td>
      <td style="font-size:11px;max-width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escHtml(charges)}</td>
      <td>
        <span class="score-pill ${rCls}" style="cursor:pointer" onclick="showRiskBreakdown('${bkSafe}','${nameSafe}',${risk},'${factorsSafe}')">
          ${risk} ${risk >= 75 ? '🔴' : risk >= 50 ? '🟡' : '🟢'}
        </span>
      </td>
      <td style="text-align:center;min-width:70px">${courtCountdown}</td>
      <td>${lastCI}</td>
      <td style="${nextDueStyle}">${nextDue}${overdueLabel}</td>
      <td><span class="status-badge ${sCls}">${b.status || 'active'}</span></td>
      <td>
        <div style="display:flex;gap:4px;flex-wrap:wrap;min-width:280px">
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#7c3aed;color:#fff" onclick="openEditDrawer('${bkSafe}')">✏️ Edit</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px" onclick="openCheckinModal('${bkSafe}','${nameSafe}')">📍 Check-In</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px" onclick="showLocationHistory('${bkSafe}','${nameSafe}')">🗺️ History</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#3b82f6;color:#fff" onclick="openInTracking('${bkSafe}')">📡 Track</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:var(--danger)" onclick="addManualAlert('${bkSafe}','${nameSafe}')">🚨 Alert</button>
          ${b.status !== 'exonerated' ? `<button class="btn-export" style="font-size:10px;padding:3px 8px;background:#22c55e;color:#fff" onclick="exonerateFromActiveBonds('${bkSafe}','${nameSafe}')">✅ Exonerate</button>` : ''}
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

  // Run post-render checks
  _detectDuplicatePhones();
}

/* ── Filter ─────────────────────────────────────────────────────── */
function filterActiveBonds(status) {
  _abFilter = status;
  document.querySelectorAll('#abStatusFilter button[data-filter]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === status);
  });
  renderActiveBondsTable();
}

/* ══════════════════════════════════════════════════════════════════
   EDIT DRAWER
   ══════════════════════════════════════════════════════════════════ */
function openEditDrawer(bookingNumber) {
  const bond = _abBonds.find(b => b.booking_number === bookingNumber);
  if (!bond) { toast('Bond not found', 'error'); return; }
  _abEditingBooking = bookingNumber;
  if (!document.getElementById('abEditDrawer')) _buildEditDrawer();

  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
  set('abEditDefName',       bond.defendant_name);
  set('abEditCounty',        bond.county);
  set('abEditFacility',      bond.facility);
  set('abEditBondAmount',    bond.bond_amount);
  set('abEditPremium',       bond.premium);
  set('abEditPOA',           bond.poa_number);
  set('abEditCaseNum',       bond.case_number);
  set('abEditCourtDate',     bond.court_date ? String(bond.court_date).substring(0, 10) : '');
  set('abEditCourtLocation', bond.court_location);
  set('abEditCharges',       typeof bond.charges === 'string' ? bond.charges : (bond.charges_raw || ''));
  set('abEditIndemName',     bond.indemnitor?.name || bond.indemnitor_name || '');
  set('abEditIndemPhone',    bond.indemnitor?.phone || bond.indemnitor_phone || '');
  set('abEditIndemEmail',    bond.indemnitor?.email || bond.indemnitor_email || '');
  set('abEditAgentName',     bond.agent_name);
  set('abEditNotes',         bond.notes);
  set('abEditCIFreq',        bond.check_in_frequency_days || 30);

  const ins = document.getElementById('abEditInsurance');
  if (ins) {
    const insVal = (bond.insurance_company || bond.surety || 'OSI').toUpperCase();
    ins.value = (insVal.includes('PALM') || insVal.includes('PSC')) ? 'PALMETTO' : 'OSI';
  }
  const ciReq = document.getElementById('abEditCIRequired');
  if (ciReq) ciReq.checked = !!bond.check_in_required;

  const hdr = document.getElementById('abEditDrawerTitle');
  if (hdr) hdr.textContent = `✏️ Edit Bond — ${bond.defendant_name || bookingNumber}`;

  document.getElementById('abEditDrawer').classList.add('open');
  document.getElementById('abEditOverlay').classList.add('show');
}

function closeEditDrawer() {
  document.getElementById('abEditDrawer')?.classList.remove('open');
  document.getElementById('abEditOverlay')?.classList.remove('show');
  _abEditingBooking = null;
}

async function saveEditDrawer() {
  if (!_abEditingBooking) return;
  const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  const getNum = id => { const v = get(id); return v !== '' ? parseFloat(v) : undefined; };

  const payload = {};
  const fields = {
    defendant_name: get('abEditDefName'), county: get('abEditCounty'),
    facility: get('abEditFacility'), bond_amount: getNum('abEditBondAmount'),
    premium: getNum('abEditPremium'), insurance_company: get('abEditInsurance'),
    poa_number: get('abEditPOA'), case_number: get('abEditCaseNum'),
    court_date: get('abEditCourtDate'), court_location: get('abEditCourtLocation'),
    charges: get('abEditCharges'), indemnitor_name: get('abEditIndemName'),
    indemnitor_phone: get('abEditIndemPhone'), indemnitor_email: get('abEditIndemEmail'),
    agent_name: get('abEditAgentName'), notes: get('abEditNotes'),
    check_in_required: document.getElementById('abEditCIRequired')?.checked,
    check_in_frequency_days: getNum('abEditCIFreq'),
    agent: 'Dashboard',
  };
  Object.entries(fields).forEach(([k, v]) => { if (v !== undefined && v !== '') payload[k] = v; });

  const btn = document.getElementById('abEditSaveBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

  try {
    const r = await fetch(`${API}/api/active-bonds/${encodeURIComponent(_abEditingBooking)}/edit`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (d.success) {
      toast(`✅ Bond updated — ${(d.updated || []).length} fields saved`, 'success');
      closeEditDrawer();
      loadActiveBonds();
      if (window.SLIndemnitor) SLIndemnitor.load();
    } else {
      toast('❌ ' + (d.error || 'Save failed'), 'error');
    }
  } catch (e) {
    toast('Network error: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '💾 Save Changes'; }
  }
}

function _ef(id, type, label, ph) {
  return `<label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)">${label}<input id="${id}" type="${type}" placeholder="${ph}" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px"></label>`;
}
function _es(title, fields) {
  return `<div style="margin-bottom:20px"><div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:10px;padding-bottom:4px;border-bottom:1px solid var(--border)">${title}</div><div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">${fields}</div></div>`;
}

function _buildEditDrawer() {
  const overlay = document.createElement('div');
  overlay.id = 'abEditOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:1000;display:none;cursor:pointer';
  overlay.onclick = closeEditDrawer;
  document.body.appendChild(overlay);

  const drawer = document.createElement('div');
  drawer.id = 'abEditDrawer';
  drawer.style.cssText = 'position:fixed;top:0;right:-520px;width:500px;height:100vh;background:var(--bg);border-left:1px solid var(--border);z-index:1001;transition:right .3s ease;overflow-y:auto;display:flex;flex-direction:column';
  drawer.innerHTML = `
    <div style="padding:20px 24px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;background:var(--panel)">
      <h3 id="abEditDrawerTitle" style="margin:0;font-size:16px">✏️ Edit Bond</h3>
      <button onclick="closeEditDrawer()" style="background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer">✕</button>
    </div>
    <div style="flex:1;overflow-y:auto;padding:20px 24px">
      ${_es('Defendant', `
        ${_ef('abEditDefName','text','Full Name','Last, First Middle')}
        ${_ef('abEditCounty','text','County','e.g. Lee')}
        ${_ef('abEditFacility','text','Facility','e.g. Lee County Jail')}
        <label style="grid-column:1/-1;display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)">Charges<textarea id="abEditCharges" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;min-height:60px;resize:vertical"></textarea></label>
      `)}
      ${_es('Bond Details', `
        ${_ef('abEditBondAmount','number','Bond Amount ($)','')}
        ${_ef('abEditPremium','number','Premium ($)','')}
        <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)">Insurance<select id="abEditInsurance" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px"><option value="OSI">🛡️ OSI</option><option value="PALMETTO">🌴 Palmetto</option></select></label>
        ${_ef('abEditPOA','text','POA Number','e.g. PSC2 2644680')}
        ${_ef('abEditCaseNum','text','Case Number','e.g. 24-CF-001234')}
        ${_ef('abEditCourtDate','date','Court Date','')}
        ${_ef('abEditCourtLocation','text','Court Location','e.g. Lee County Courthouse')}
        ${_ef('abEditAgentName','text','Agent Name','e.g. Brendan')}
      `)}
      ${_es('Indemnitor', `
        ${_ef('abEditIndemName','text','Name','Full name')}
        ${_ef('abEditIndemPhone','tel','Phone','+12395550000')}
        ${_ef('abEditIndemEmail','email','Email','email@example.com')}
      `)}
      ${_es('Check-In', `
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px"><input id="abEditCIRequired" type="checkbox" style="width:16px;height:16px"> Check-In Required</label>
        ${_ef('abEditCIFreq','number','Frequency (days)','30')}
      `)}
      ${_es('Notes', `
        <label style="grid-column:1/-1;display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)">Internal Notes<textarea id="abEditNotes" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;min-height:80px;resize:vertical" placeholder="Internal notes…"></textarea></label>
      `)}
    </div>
    <div style="padding:16px 24px;border-top:1px solid var(--border);display:flex;gap:8px;justify-content:flex-end;background:var(--panel)">
      <button onclick="closeEditDrawer()" style="padding:8px 16px;background:var(--panel);border:1px solid var(--border);border-radius:6px;color:var(--text);cursor:pointer">Cancel</button>
      <button id="abEditSaveBtn" onclick="saveEditDrawer()" style="padding:8px 20px;background:var(--accent);border:none;border-radius:6px;color:#fff;cursor:pointer;font-weight:600">💾 Save Changes</button>
    </div>`;
  document.body.appendChild(drawer);

  const style = document.createElement('style');
  style.textContent = '#abEditDrawer.open{right:0!important} #abEditOverlay.show{display:block!important}';
  document.head.appendChild(style);
}

/* ══════════════════════════════════════════════════════════════════
   ADD BOND MODAL
   ══════════════════════════════════════════════════════════════════ */
function openAddBondModal() {
  if (!document.getElementById('abAddBondModal')) _buildAddBondModal();
  const modal = document.getElementById('abAddBondModal');
  modal.querySelectorAll('input,select,textarea').forEach(el => {
    if (el.type === 'checkbox') el.checked = false;
    else if (el.tagName === 'SELECT') el.selectedIndex = 0;
    else el.value = '';
  });
  const agentEl = document.getElementById('abAddAgent');
  if (agentEl) agentEl.value = 'Brendan';
  const freqEl = document.getElementById('abAddCIFreq');
  if (freqEl) freqEl.value = '30';
  modal.style.display = 'flex';
}

function closeAddBondModal() {
  const m = document.getElementById('abAddBondModal');
  if (m) m.style.display = 'none';
}

async function submitAddBond() {
  const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  const booking = get('abAddBooking');
  const defName = get('abAddDefName');
  if (!booking) { toast('Booking number is required', 'error'); return; }
  if (!defName)  { toast('Defendant name is required', 'error'); return; }

  const payload = {
    booking_number: booking, defendant_name: defName,
    county: get('abAddCounty'), facility: get('abAddFacility'),
    bond_amount: parseFloat(get('abAddBondAmount')) || 0,
    premium: parseFloat(get('abAddPremium')) || 0,
    insurance_company: get('abAddInsurance') || 'OSI',
    poa_number: get('abAddPOA'), case_number: get('abAddCaseNum'),
    court_date: get('abAddCourtDate'), court_location: get('abAddCourtLocation'),
    charges: get('abAddCharges'), indemnitor_name: get('abAddIndemName'),
    indemnitor_phone: get('abAddIndemPhone'), indemnitor_email: get('abAddIndemEmail'),
    agent_name: get('abAddAgent'),
    check_in_required: document.getElementById('abAddCIRequired')?.checked || false,
    check_in_frequency_days: parseInt(get('abAddCIFreq')) || 30,
  };

  const btn = document.getElementById('abAddSubmitBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Adding…'; }

  try {
    const r = await fetch(`${API}/api/active-bonds`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (d.success) {
      toast(`✅ Bond added for ${defName}`, 'success');
      closeAddBondModal();
      loadActiveBonds();
      if (window.SLIndemnitor) SLIndemnitor.load();
    } else {
      toast('❌ ' + (d.error || 'Failed to add bond'), 'error');
    }
  } catch (e) {
    toast('Network error: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✅ Add Bond'; }
  }
}

function _buildAddBondModal() {
  const modal = document.createElement('div');
  modal.id = 'abAddBondModal';
  modal.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1002;align-items:center;justify-content:center';
  modal.innerHTML = `
    <div style="background:var(--bg);border:1px solid var(--border);border-radius:12px;width:min(680px,95vw);max-height:90vh;overflow-y:auto;display:flex;flex-direction:column">
      <div style="padding:20px 24px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;background:var(--panel);border-radius:12px 12px 0 0">
        <h3 style="margin:0;font-size:16px">➕ Add Active Bond</h3>
        <button onclick="closeAddBondModal()" style="background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer">✕</button>
      </div>
      <div style="padding:20px 24px;overflow-y:auto">
        ${_es('Defendant', `
          <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)">Booking # <span style="color:var(--danger)">*</span><input id="abAddBooking" type="text" placeholder="e.g. 2024-00012345" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px"></label>
          <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)">Full Name <span style="color:var(--danger)">*</span><input id="abAddDefName" type="text" placeholder="Last, First Middle" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px"></label>
          ${_ef('abAddCounty','text','County','e.g. Lee')}
          ${_ef('abAddFacility','text','Facility','e.g. Lee County Jail')}
          <label style="grid-column:1/-1;display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)">Charges<textarea id="abAddCharges" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;min-height:60px;resize:vertical" placeholder="Charge descriptions"></textarea></label>
        `)}
        ${_es('Bond Details', `
          ${_ef('abAddBondAmount','number','Bond Amount ($)','')}
          ${_ef('abAddPremium','number','Premium ($)','')}
          <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)">Insurance<select id="abAddInsurance" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px"><option value="OSI">🛡️ OSI</option><option value="PALMETTO">🌴 Palmetto</option></select></label>
          ${_ef('abAddPOA','text','POA Number','e.g. PSC2 2644680')}
          ${_ef('abAddCaseNum','text','Case Number','e.g. 24-CF-001234')}
          ${_ef('abAddCourtDate','date','Court Date','')}
          ${_ef('abAddCourtLocation','text','Court Location','e.g. Lee County Courthouse')}
          ${_ef('abAddAgent','text','Agent Name','e.g. Brendan')}
        `)}
        ${_es('Indemnitor', `
          ${_ef('abAddIndemName','text','Name','Full name')}
          ${_ef('abAddIndemPhone','tel','Phone','+12395550000')}
          ${_ef('abAddIndemEmail','email','Email','email@example.com')}
        `)}
        ${_es('Check-In', `
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px"><input id="abAddCIRequired" type="checkbox" style="width:16px;height:16px"> Check-In Required</label>
          ${_ef('abAddCIFreq','number','Frequency (days)','30')}
        `)}
      </div>
      <div style="padding:16px 24px;border-top:1px solid var(--border);display:flex;gap:8px;justify-content:flex-end;background:var(--panel);border-radius:0 0 12px 12px">
        <button onclick="closeAddBondModal()" style="padding:8px 16px;background:var(--panel);border:1px solid var(--border);border-radius:6px;color:var(--text);cursor:pointer">Cancel</button>
        <button id="abAddSubmitBtn" onclick="submitAddBond()" style="padding:8px 20px;background:var(--accent);border:none;border-radius:6px;color:#fff;cursor:pointer;font-weight:600">✅ Add Bond</button>
      </div>
    </div>`;
  modal.addEventListener('click', e => { if (e.target === modal) closeAddBondModal(); });
  document.body.appendChild(modal);
}

/* ══════════════════════════════════════════════════════════════════
   LOCATION HISTORY MODAL
   ══════════════════════════════════════════════════════════════════ */
async function showLocationHistory(bookingNumber, defName) {
  const bond = _abBonds.find(b => b.booking_number === bookingNumber);
  let pings = bond?.location_history || [];
  try {
    const r = await fetch(`${API}/api/tracking/${encodeURIComponent(bookingNumber)}/history`);
    if (r.ok) {
      const d = await r.json();
      const remote = d.history || d.pings || [];
      if (remote.length > pings.length) pings = remote;
    }
  } catch {}

  const name = defName || bond?.defendant_name || bookingNumber;
  let modal = document.getElementById('abLocationModal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'abLocationModal';
    modal.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1002;align-items:center;justify-content:center';
    modal.addEventListener('click', e => { if (e.target === modal) modal.style.display = 'none'; });
    document.body.appendChild(modal);
  }

  const bkSafe = (bookingNumber || '').replace(/'/g, "\\'");
  const nameSafe = (name || '').replace(/'/g, "\\'");

  modal.innerHTML = `
    <div style="background:var(--bg);border:1px solid var(--border);border-radius:12px;width:min(760px,95vw);max-height:85vh;overflow-y:auto;display:flex;flex-direction:column">
      <div style="padding:16px 20px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;background:var(--panel);border-radius:12px 12px 0 0">
        <h3 style="margin:0;font-size:15px">🗺️ Location History — ${escHtml(name)}</h3>
        <button onclick="document.getElementById('abLocationModal').style.display='none'" style="background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer">✕</button>
      </div>
      <div style="padding:16px 20px;overflow-y:auto">
        ${pings.length === 0
          ? `<div style="text-align:center;padding:32px;color:var(--muted)"><div style="font-size:32px;margin-bottom:8px">📍</div><div>No location pings recorded yet.</div><button class="btn-export" style="margin-top:12px" onclick="document.getElementById('abLocationModal').style.display='none';openCheckinModal('${bkSafe}','${nameSafe}')">📍 Record First Check-In</button></div>`
          : `<div class="table-wrap"><table><thead><tr><th>Date/Time</th><th>Address</th><th>Coordinates</th><th>Source</th><th>Notes</th></tr></thead><tbody>${pings.slice().reverse().map(p => `<tr>
              <td style="white-space:nowrap">${p.timestamp ? new Date(p.timestamp).toLocaleString() : (p.created_at ? new Date(p.created_at).toLocaleString() : '—')}</td>
              <td>${escHtml(p.address || p.location || p.county || '—')}</td>
              <td>${p.lat && p.lng ? `<a href="https://maps.google.com/?q=${p.lat},${p.lng}" target="_blank" style="color:var(--accent)">${Number(p.lat).toFixed(4)}, ${Number(p.lng).toFixed(4)}</a>` : '—'}</td>
              <td><span style="font-size:10px;background:var(--panel);padding:2px 6px;border-radius:4px">${escHtml(p.source || 'manual')}</span></td>
              <td style="font-size:11px;color:var(--muted)">${escHtml(p.notes || p.message || '')}</td>
            </tr>`).join('')}</tbody></table></div>`}
      </div>
      <div style="padding:12px 20px;border-top:1px solid var(--border);display:flex;gap:8px;justify-content:flex-end;background:var(--panel);border-radius:0 0 12px 12px">
        <button class="btn-export" onclick="document.getElementById('abLocationModal').style.display='none';openCheckinModal('${bkSafe}','${nameSafe}')">📍 Add Check-In</button>
        <button class="btn-export" onclick="document.getElementById('abLocationModal').style.display='none';openInTracking('${bkSafe}')">📡 Open in Tracking</button>
        <button onclick="document.getElementById('abLocationModal').style.display='none'" style="padding:8px 16px;background:var(--panel);border:1px solid var(--border);border-radius:6px;color:var(--text);cursor:pointer">Close</button>
      </div>
    </div>`;
  modal.style.display = 'flex';
}

/* ══════════════════════════════════════════════════════════════════
   CHECK-IN MODAL
   ══════════════════════════════════════════════════════════════════ */
function openCheckinModal(booking, name) {
  _abCheckinBooking = booking;
  _abCheckinName = name;
  const nameEl = document.getElementById('abCheckinDefName');
  if (nameEl) nameEl.textContent = `📍 Check-In: ${name}`;
  ['abCheckinLat','abCheckinLng','abCheckinCounty'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  const srcEl = document.getElementById('abCheckinSource');
  if (srcEl) srcEl.value = 'manual';
  document.getElementById('abCheckinModal')?.classList.add('show');
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(pos => {
      const lat = document.getElementById('abCheckinLat');
      const lng = document.getElementById('abCheckinLng');
      if (lat) lat.value = pos.coords.latitude.toFixed(6);
      if (lng) lng.value = pos.coords.longitude.toFixed(6);
      const src = document.getElementById('abCheckinSource');
      if (src) src.value = 'gps';
      toast('📍 GPS captured', 'success', 2000);
    }, () => {});
  }
}

function closeCheckinModal() {
  document.getElementById('abCheckinModal')?.classList.remove('show');
  _abCheckinBooking = '';
  _abCheckinName = '';
}

async function submitCheckin() {
  if (!_abCheckinBooking) { toast('No booking selected', 'error'); return; }
  const lat    = parseFloat(document.getElementById('abCheckinLat')?.value) || null;
  const lng    = parseFloat(document.getElementById('abCheckinLng')?.value) || null;
  const county = document.getElementById('abCheckinCounty')?.value?.trim() || '';
  const source = document.getElementById('abCheckinSource')?.value || 'manual';
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

/* ══════════════════════════════════════════════════════════════════
   RISK BREAKDOWN MODAL
   ══════════════════════════════════════════════════════════════════ */
function showRiskBreakdown(bookingNumber, defName, risk, factorsEncoded) {
  let factors = {};
  try { factors = JSON.parse(decodeURIComponent(factorsEncoded || '{}')); } catch {
    try { factors = JSON.parse(factorsEncoded || '{}'); } catch {}
  }
  const lines = Object.entries(factors).map(([k, v]) =>
    `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border)"><span style="font-size:12px;color:var(--muted)">${k.replace(/_/g,' ')}</span><span style="font-size:12px;font-weight:600;color:${v > 0 ? 'var(--danger)' : 'var(--accent)'}">${v > 0 ? '+' : ''}${v}</span></div>`
  ).join('') || '<p style="color:var(--muted);font-size:12px">No factor breakdown available.</p>';

  const modal = document.createElement('div');
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1003;display:flex;align-items:center;justify-content:center';
  modal.innerHTML = `
    <div style="background:var(--bg);border:1px solid var(--border);border-radius:12px;width:min(400px,90vw);max-height:80vh;overflow-y:auto">
      <div style="padding:16px 20px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;background:var(--panel);border-radius:12px 12px 0 0">
        <h3 style="margin:0;font-size:15px">⚠️ Risk Score — ${escHtml(defName || bookingNumber)}</h3>
        <button onclick="this.closest('[style*=fixed]').remove()" style="background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer">✕</button>
      </div>
      <div style="padding:20px">
        <div style="text-align:center;margin-bottom:20px">
          <div style="font-size:52px;font-weight:700;color:${risk >= 70 ? 'var(--danger)' : risk >= 40 ? '#f59e0b' : 'var(--accent)'}">${risk}<span style="font-size:20px;color:var(--muted)">/100</span></div>
          <div style="color:var(--muted);font-size:13px">${risk >= 70 ? '🔴 High Risk' : risk >= 40 ? '🟡 Medium Risk' : '🟢 Low Risk'}</div>
        </div>
        ${lines}
        <p style="margin-top:14px;font-size:11px;color:var(--muted);text-align:center">Score updates automatically as check-ins and alerts are recorded.</p>
      </div>
      <div style="padding:12px 20px;border-top:1px solid var(--border);text-align:right;background:var(--panel);border-radius:0 0 12px 12px">
        <button onclick="this.closest('[style*=fixed]').remove()" style="padding:8px 16px;background:var(--panel);border:1px solid var(--border);border-radius:6px;color:var(--text);cursor:pointer">Close</button>
      </div>
    </div>`;
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
}

/* ══════════════════════════════════════════════════════════════════
   MANUAL ALERT
   ══════════════════════════════════════════════════════════════════ */
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

/* ══════════════════════════════════════════════════════════════════
   UPDATE STATUS
   ══════════════════════════════════════════════════════════════════ */
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

/* ══════════════════════════════════════════════════════════════════
   CROSS-TAB: OPEN IN TRACKING
   ══════════════════════════════════════════════════════════════════ */
function openInTracking(bookingNumber) {
  const trackTab = document.querySelector('[data-tab="tabTracking"]') ||
                   Array.from(document.querySelectorAll('.tab-btn')).find(b => b.textContent.includes('Tracking'));
  if (trackTab) trackTab.click();
  setTimeout(() => {
    const searchEl = document.getElementById('trkSearch');
    if (searchEl) { searchEl.value = bookingNumber; searchEl.dispatchEvent(new Event('input')); }
    if (window.SLTracking) SLTracking.openDetail(bookingNumber);
  }, 350);
}

/* ══════════════════════════════════════════════════════════════════
   EXONERATE
   ══════════════════════════════════════════════════════════════════ */
async function exonerateFromActiveBonds(bookingNumber, defName) {
  const note = prompt(
    `✅ Exonerate bond for ${defName}?\n\n` +
    'This will:\n  • Stop all location tracking immediately\n  • Cancel all pending GPS capture links\n  • Cancel all pending court reminders\n\n' +
    'Enter a note (e.g. "Discharge email from Lee County Clerk") or leave blank:'
  );
  if (note === null) return;
  const notifyIndem = confirm('Notify indemnitor via iMessage that the bond is officially discharged?');
  try {
    const r = await fetch(`${API}/api/tracking/${encodeURIComponent(bookingNumber)}/exonerate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: 'manual', note: note || 'Manual exoneration from Active Bonds tab', notify_indemnitor: notifyIndem }),
    });
    const data = await r.json();
    if (data.success) {
      toast(`✅ ${defName} exonerated — tracking stopped`, 'success');
      loadActiveBonds();
      if (window.SLTracking) { SLTracking.refresh(); SLTracking.onBondExonerated({ booking_number: bookingNumber, defendant_name: defName }); }
    } else if (data.already_exonerated) {
      toast(`${defName} was already exonerated on ${data.exonerated_at ? new Date(data.exonerated_at).toLocaleDateString() : '—'}`, 'info');
    } else {
      toast('❌ ' + (data.error || 'Exoneration failed'), 'error');
    }
  } catch (e) {
    toast('Network error during exoneration', 'error');
  }
}

/* ══════════════════════════════════════════════════════════════════
   PROCESS MISSED CHECK-INS
   ══════════════════════════════════════════════════════════════════ */
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

/* ══════════════════════════════════════════════════════════════════
   INIT
   ══════════════════════════════════════════════════════════════════ */
function initActiveBonds() {
  document.querySelectorAll('#abStatusFilter button[data-filter]').forEach(btn => {
    btn.addEventListener('click', () => filterActiveBonds(btn.dataset.filter));
  });
  const addBtn = document.getElementById('abAddBondBtn');
  if (addBtn) addBtn.addEventListener('click', openAddBondModal);
  const refBtn = document.getElementById('abRefreshBtn');
  if (refBtn) refBtn.addEventListener('click', loadActiveBonds);
  const missedBtn = document.getElementById('abProcessMissedBtn');
  if (missedBtn) missedBtn.addEventListener('click', processMissedCheckins);
  loadActiveBonds();
  _checkPoaStock();
  loadReminderStatus();
}

/* ── Feature I: Court Reminder Panel Functions ───────── */
async function loadReminderStatus() {
  try {
    const r = await fetch(`${API}/api/court-reminders/status`);
    if (!r.ok) return;
    const d = await r.json();
    const el = id => document.getElementById(id);
    el('crPending').textContent = d.pending || 0;
    el('crSent').textContent = d.sent || 0;
    el('crFailed').textContent = d.failed || 0;
    el('crTotal').textContent = d.total || 0;
    el('crStatus').textContent = `${d.total || 0} total reminders`;
    if (d.next_due) {
      const dt = new Date(d.next_due.send_at);
      el('crNextDue').innerHTML = `⏰ Next: <strong>${escHtml(d.next_due.defendant_name || '—')}</strong> (${d.next_due.touch}) → ${dt.toLocaleDateString('en-US',{month:'short',day:'numeric'})} at ${dt.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'})}`;
    } else {
      el('crNextDue').textContent = 'No pending reminders';
    }
  } catch(e) { console.warn('Reminder status load error:', e); }
}

async function runCourtReminderScan() {
  const btn = event?.target;
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Scanning...'; }
  const resultDiv = document.getElementById('crScanResult');
  try {
    const r = await fetch(`${API}/api/court-reminders/auto-scan`, { method: 'POST' });
    const d = await r.json();
    if (d.success) {
      const scan = d.scan || {};
      const send = d.send || {};
      let html = `<div style="margin-bottom:6px"><strong>✅ Scan Complete</strong></div>`;
      html += `<div>📋 Bonds scanned: <strong>${scan.bonds_scanned || 0}</strong></div>`;
      html += `<div>📲 New reminders scheduled: <strong>${scan.scheduled || 0}</strong></div>`;
      html += `<div>⏭ Already scheduled (skipped): ${scan.skipped || 0}</div>`;
      if (scan.errors > 0) html += `<div style="color:var(--danger)">❌ Errors: ${scan.errors}</div>`;
      html += `<div style="margin-top:6px">📤 Sent this cycle: <strong>${send.sent || 0}</strong> | Failed: ${send.failed || 0}</div>`;
      if (scan.details?.length > 0) {
        html += `<details style="margin-top:6px"><summary style="cursor:pointer;font-size:11px">Details (${scan.details.length})</summary><div style="margin-top:4px;max-height:150px;overflow-y:auto">`;
        scan.details.forEach(d => {
          const icon = d.status === 'scheduled' ? '✅' : d.status === 'no_phone' ? '📵' : '❌';
          html += `<div style="padding:2px 0;font-size:11px">${icon} ${escHtml(d.name || d.booking)} — ${d.status}${d.count ? ` (${d.count} msgs)` : ''}${d.error ? ` — ${d.error}` : ''}</div>`;
        });
        html += `</div></details>`;
      }
      resultDiv.innerHTML = html;
      resultDiv.style.display = 'block';
      toast(`Scanned ${scan.bonds_scanned} bonds, scheduled ${scan.scheduled} new reminders`, 'success');
      loadReminderStatus();
    } else {
      toast(d.error || 'Scan failed', 'error');
    }
  } catch(e) {
    toast('Court scan error: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔄 Scan Now'; }
  }
}

/* ── Feature C: Cross-link indemnitor → Defendants tab ───────── */
function crossLinkToDefendants(name) {
  // Switch to Defendants tab and pre-fill search
  const defBtn = document.querySelector('[data-tab="tabDefendants"]');
  if (defBtn) {
    SL.switchTab(defBtn);
    const searchInput = document.getElementById('defSearch');
    if (searchInput) {
      searchInput.value = name;
      SL.debounceDefSearch();
    }
  }
}

/* ── Feature F: Bulk Exonerate ───────────────────────────────── */
async function bulkExonerate() {
  const input = prompt(
    'Enter booking numbers to exonerate (comma-separated):\n\n' +
    'Example: 2025-001234, 2025-001235, 2025-001236\n\n' +
    'This will:\n• Set status to "exonerated"\n• Release assigned POAs\n• Cancel pending court reminders\n• Create audit trail'
  );
  if (!input) return;

  const bookings = input.split(',').map(s => s.trim()).filter(Boolean);
  if (bookings.length === 0) return;

  const exType = prompt('Exoneration type? (discharge / nolle_prosequi / acquittal / completion)', 'discharge') || 'discharge';

  if (!confirm(`Exonerate ${bookings.length} bond(s) as "${exType}"?\n\n${bookings.join('\n')}`)) return;

  try {
    const r = await fetch(`${API}/api/active-bonds/bulk-exonerate`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        booking_numbers: bookings,
        exoneration_type: exType,
        agent: 'Dashboard',
      }),
    });
    const d = await r.json();
    if (d.success) {
      toast(`✅ ${d.exonerated} bond(s) exonerated, ${d.poa_released} POA(s) released`, 'success');
      loadActiveBonds();
    } else {
      toast(d.error || 'Bulk exonerate failed', 'error');
    }
  } catch(e) {
    toast('Bulk exonerate error: ' + e.message, 'error');
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initActiveBonds);
} else {
  initActiveBonds();
}
