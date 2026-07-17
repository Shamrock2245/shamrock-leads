/* ═══════════════════════════════════════════════════════════════════════
   ShamrockLeads — Active Bonds Module  (sl-active-bonds.js)
   Full-featured: editable records, Add Bond, location history, exoneration
   NOTE: `API` is declared in sl-core.js (loaded first) — do NOT redeclare here.
   ═══════════════════════════════════════════════════════════════════════ */



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

/* ── FTA Risk Badge (mirrors leads-engine.js ftaBadge) ──────── */
function _abFtaBadge(b) {
  const lvl = (b.fta_risk_level || '').toLowerCase();
  const score = b.fta_risk_score;
  if (!lvl || score == null) return '—';
  const colors = { critical: '#ff4444', high: '#ff8800', moderate: '#ffcc00', low: '#44bb44', disqualified: '#888' };
  const icons  = { critical: '🔴', high: '🟠', moderate: '🟡', low: '🟢', disqualified: '⛔' };
  const clr = colors[lvl] || '#888';
  const ico = icons[lvl] || '⚪';
  return `<span class="fta-badge" style="background:${clr}22;color:${clr};border:1px solid ${clr}44;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:600;white-space:nowrap;cursor:help" title="FTA Risk: ${lvl} (${score}/100) | Confidence: ${b.fta_risk_confidence != null ? (b.fta_risk_confidence * 100).toFixed(0) + '%' : 'N/A'}">${ico} ${lvl.charAt(0).toUpperCase()+lvl.slice(1)} <span style="opacity:0.7;font-size:9px">${score}</span></span>`;
}

/* ── State ────────────────────────────────────────────────────────── */
let _abBonds = [];
window._abBonds = _abBonds;
let _abFilter = 'all';
window._abFilter = _abFilter;
let _abCheckinBooking = '';
let _abCheckinName = '';
let _abEditingBooking = null;
window._abEditingBooking = _abEditingBooking;

/* ══════════════════════════════════════════════════════════════════
   LOAD & RENDER
   ══════════════════════════════════════════════════════════════════ */
async function loadActiveBonds() {
  try {
    const r = await fetch(`${API}/api/active-bonds`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    _abBonds = data.bonds || [];
    window._abBonds = _abBonds;   // expose for sl-active-bonds-ext.js override

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
    // Fire-and-forget: POA stock check (non-blocking)
    _checkPoaStock();
  } catch (e) {
    console.error('loadActiveBonds error:', e);
    const tbody = document.getElementById('abTableBody');
    if (tbody) tbody.innerHTML = `<tr><td colspan="15" style="color:var(--danger);text-align:center;padding:24px">Error loading active bonds: ${e.message}</td></tr>`;
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
    // Support both tiers[] format and {osi:{}, palmetto:{}} format
    let sureties = [];
    if (Array.isArray(d.tiers)) {
      sureties = d.tiers.map(t => ({ name: t.surety || t.prefix, available: t.available || 0 }));
    } else {
      // Flat surety-keyed format: { osi: { available, assigned, total }, palmetto: {...} }
      sureties = Object.entries(d)
        .filter(([k]) => !['updated_at','success'].includes(k))
        .map(([k, v]) => ({ name: k.toUpperCase(), available: (v && v.available) || 0 }));
    }
    const lowStock  = sureties.filter(s => s.available > 0 && s.available <= 5);
    const outOfStock = sureties.filter(s => s.available === 0);
    if (lowStock.length === 0 && outOfStock.length === 0) { banner.style.display = 'none'; return; }
    banner.style.display = 'block';
    const isCritical = outOfStock.length > 0;
    const manageLink = `<a href="#" onclick="event.preventDefault();if(window.SLInventory)SLInventory.open();else{var t=document.querySelector('.inv-tab-trigger');if(t)t.click();}" style="color:var(--warning);font-weight:600;text-decoration:underline;font-size:12px">Manage Inventory →</a>`;
    let html = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">`;
    html += `<span style="font-size:16px">${isCritical ? '🚨' : '⚠️'}</span>`;
    html += `<strong style="color:${isCritical ? 'var(--danger)' : 'var(--warning)'}">POA Inventory ${isCritical ? 'CRITICAL' : 'Low Stock'}</strong>`;
    html += `<span style="margin-left:8px">${manageLink}</span>`;
    html += `<button onclick="this.closest('.ab-alert-banner').style.display='none'" style="margin-left:auto;background:none;border:none;color:var(--muted);cursor:pointer;font-size:16px;line-height:1;padding:0 4px" title="Dismiss">✕</button></div>`;
    if (outOfStock.length > 0) {
      html += outOfStock.map(s => `<div style="color:var(--danger);font-size:12px">🚫 <strong>${escHtml(s.name)}</strong> — OUT OF STOCK</div>`).join('');
    }
    if (lowStock.length > 0) {
      html += lowStock.map(s => `<div style="color:var(--warning);font-size:12px">⚠️ <strong>${escHtml(s.name)}</strong> — only <strong>${s.available}</strong> POA${s.available !== 1 ? 's' : ''} remaining</div>`).join('');
    }
    banner.innerHTML = html;
  } catch(e) { /* non-fatal — POA stock check should never break the main view */ }
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
    tbody.innerHTML = `<tr><td colspan="15" style="text-align:center;padding:32px;color:var(--muted)">No bonds match this filter.<br><button class="btn-export" style="margin-top:12px" onclick="openAddBondModal()">➕ Add First Bond</button></td></tr>`;
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
    const bookingUrl = b.booking_page_url || b.detail_url || '';
    const bookingLink = bookingUrl ? `<br><a href="${bookingUrl}" target="_blank" style="font-size:9px;color:var(--accent);text-decoration:none">🔗 Booking Page</a>` : '';

    /* Feature A: Court Date Countdown — polished with full-date tooltip + location */
    let courtCountdown = '—';
    if (b.court_date) {
      const cd = new Date(b.court_date);
      const diff = Math.ceil((cd - new Date()) / 86400000);
      const fullDate = cd.toLocaleDateString('en-US', { weekday:'short', month:'short', day:'numeric', year:'numeric' });
      const locationTip = b.court_location ? ` @ ${b.court_location}` : '';
      const tooltip = escHtml(`${fullDate}${locationTip}`);
      let countLabel, countStyle;
      if (diff < 0) {
        countLabel = `${Math.abs(diff)}d ago ⚠️`;
        countStyle = 'color:var(--danger);font-weight:700';
      } else if (diff === 0) {
        countLabel = 'TODAY 🔴';
        countStyle = 'color:var(--danger);font-weight:800;letter-spacing:.02em';
      } else if (diff === 1) {
        countLabel = 'TOMORROW ⚠️';
        countStyle = 'color:var(--danger);font-weight:700';
      } else if (diff <= 3) {
        countLabel = `${diff}d`;
        countStyle = 'color:var(--danger);font-weight:600';
      } else if (diff <= 7) {
        countLabel = `${diff}d`;
        countStyle = 'color:#f59e0b;font-weight:600';
      } else if (diff <= 14) {
        countLabel = `${diff}d`;
        countStyle = 'color:#3b82f6';
      } else {
        countLabel = `${diff}d`;
        countStyle = 'color:var(--muted)';
      }
      const dateLabel = cd.toLocaleDateString('en-US', { month:'short', day:'numeric' });
      courtCountdown = `<span title="${tooltip}" style="cursor:help;${countStyle}">${countLabel}</span>`
        + `<div style="font-size:10px;color:var(--muted)" title="${tooltip}">${dateLabel}${b.court_location ? `<br><span style="font-size:9px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:80px;display:block" title="${escHtml(b.court_location)}">${escHtml(b.court_location.slice(0,18))}${b.court_location.length>18?'…':''}</span>` : ''}</div>`;
    }

    return `<tr class="${overdue ? 'row-alert' : ''}" style="${overdue ? 'background:rgba(239,68,68,0.05)' : ''}">
      <td>
        <div style="font-weight:600">${escHtml(b.defendant_name || '—')}${alertBadge}</div>
        <div style="font-size:11px;color:var(--muted)">${escHtml(b.booking_number || '—')}${bookingLink}</div>
      </td>
      <td>${b.county&&b.county!=='—'?`<span class="county-badge" data-county="${escHtml(b.county)}">${escHtml(b.county)}</span>`:'—'}</td>
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
      <td style="text-align:center">${_abFtaBadge(b)}</td>
      <td style="text-align:center;min-width:70px">${courtCountdown}</td>
      <td>${lastCI}</td>
      <td style="${nextDueStyle}">${nextDue}${overdueLabel}</td>
      <td><span class="status-badge ${sCls}">${b.status || 'active'}</span></td>
      <td>
        <div class="poa-inline-cell" style="display:flex;align-items:center;gap:4px;min-width:90px">
          <span class="poa-inline-value" style="font-size:11px;font-weight:600;color:var(--text)" title="${escHtml(b.poa_number||'—')}">${escHtml((b.poa_number||'—').slice(0,12))}</span>
          <button class="poa-inline-swap-btn" title="Swap POA" style="font-size:10px;padding:1px 5px;background:var(--panel);border:1px solid var(--border);border-radius:3px;cursor:pointer;color:var(--accent)" onclick="SLKanban&&SLKanban.openPoaSwap(${JSON.stringify(b).replace(/"/g,'&quot;')})">⇄</button>
        </div>
      </td>
      <td>
        <div style="display:flex;gap:4px;flex-wrap:wrap;min-width:280px">
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#7c3aed;color:#fff" onclick="window.openEditDrawer('${bkSafe}')">✏️ Edit</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#f59e0b;color:#000;font-weight:600" onclick="openBondFromActiveBond(${JSON.stringify(b).replace(/"/g,'&quot;')})" title="Send SignNow packet (${ins.includes('PALM')||ins.includes('PSC')?'Palmetto':'OSI'} templates)">📝 Docs</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px" onclick="openCheckinModal('${bkSafe}','${nameSafe}')">📍 Check-In</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px" onclick="showLocationHistory('${bkSafe}','${nameSafe}')">🗺️ History</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#3b82f6;color:#fff" onclick="openInTracking('${bkSafe}')">📡 Track</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:var(--danger)" onclick="addManualAlert('${bkSafe}','${nameSafe}')">🚨 Alert</button>
          ${b.status !== 'exonerated' ? `<button class="btn-export" style="font-size:10px;padding:3px 8px;background:#22c55e;color:#fff" onclick="exonerateFromActiveBonds('${bkSafe}','${nameSafe}')">✅ Exonerate</button>` : ''}
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#10b981;color:#fff" onclick="fileBondToDrive('${bkSafe}')">📁 File to Drive</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#0ea5e9;color:#fff" onclick="sendPaymentLink('${bkSafe}','${nameSafe}','${escHtml(b.indemnitor?.phone||b.indemnitor_phone||'')}')">💳 Pay Link</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#8b5cf6;color:#fff" onclick="sendBondImessage('${bkSafe}','${nameSafe}','${escHtml(b.indemnitor?.phone||b.indemnitor_phone||'')}')">💬 iMessage</button>
          <select style="font-size:10px;padding:3px;background:var(--panel);border:1px solid var(--border);border-radius:4px;color:var(--text)" onchange="updateBondStatus('${bkSafe}',this.value);this.value=''">
            <option value="">Status…</option>
            <option value="active">Active</option>
            <option value="monitoring">Monitoring</option>
            <option value="alert">Alert</option>
            <option value="exonerated">Exonerated</option>
            <option value="surrendered">Surrendered</option>
            <option value="forfeited">Forfeited</option>
            <option value="reinstated">Reinstated</option>
          </select>
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#f59e0b;color:#fff" onclick="openRenewBondModal('${bkSafe}','${nameSafe}')">🔄 Renew</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#6b7280;color:#fff" onclick="SLKanban&&SLKanban.loadStatusHistory('${bkSafe}')">📋 History</button>
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
  window._abFilter = status;   // keep ext in sync
  document.querySelectorAll('#abStatusFilter button[data-filter]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === status);
  });
  renderActiveBondsTable();
}

/* ══════════════════════════════════════════════════════════════════
   EDIT DRAWER
   ══════════════════════════════════════════════════════════════════ */
window.openEditDrawer = function(bookingNumber) {
  const bond = _abBonds.find(b => b.booking_number === bookingNumber);
  if (!bond) { toast('Bond not found', 'error'); return; }
  _abEditingBooking = bookingNumber;
  window._abEditingBooking = bookingNumber;
  if (!document.getElementById('abEditDrawer')) window._buildEditDrawer();

  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
  set('abEditDefName',       bond.defendant_name);
  set('abEditDefPhone',      bond.defendant_phone);
  set('abEditDefAddress',    bond.defendant_address);
  set('abEditDefDob',        bond.defendant_dob);
  set('abEditDefEmail',      bond.defendant_email);
  set('abEditBookingUrl',    bond.booking_page_url);
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
  set('abEditIndemRel',      bond.indemnitor?.relationship || bond.indemnitor_relationship || '');
  set('abEditRef1Name',      bond.ref1_name);
  set('abEditRef1Phone',     bond.ref1_phone);
  set('abEditRef2Name',      bond.ref2_name);
  set('abEditRef2Phone',     bond.ref2_phone);
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

  window._abEditBookingNumber = bookingNumber;
  window._abEditingBooking = bookingNumber;
  const drawer = document.getElementById('abEditDrawer');
  const overlay = document.getElementById('abEditOverlay');
  if (drawer) drawer.classList.add('open');
  if (overlay) overlay.classList.add('show');
  // Load compliance data for this defendant
  _loadCompliancePanel(bookingNumber);
  // Load renewal history for this bond
  _loadRenewalHistory(bookingNumber);
  // Load related cases (same defendant / indemnitor)
  if (window.SLRelationships) SLRelationships.loadRelatedIntoPanel(bond);
}

window.closeEditDrawer = function() {
  document.getElementById('abEditDrawer')?.classList.remove('open');
  document.getElementById('abEditOverlay')?.classList.remove('show');
  _abEditingBooking = null;
}

window.copyBondToClipboard = function() {
  const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  const text = `
DEFENDANT: ${get('abEditDefName')}
PHONE: ${get('abEditDefPhone')}
DOB: ${get('abEditDefDob')}
ADDRESS: ${get('abEditDefAddress')}
BOOKING #: ${window._abEditingBooking}
COUNTY: ${get('abEditCounty')}
CHARGES: ${get('abEditCharges')}

BOND AMOUNT: $${get('abEditBondAmount')}
PREMIUM: $${get('abEditPremium')}
POA: ${get('abEditPOA')}
CASE #: ${get('abEditCaseNum')}
COURT: ${get('abEditCourtDate')} @ ${get('abEditCourtLocation')}

INDEMNITOR: ${get('abEditIndemName')}
INDEMNITOR PHONE: ${get('abEditIndemPhone')}
  `.trim();

  navigator.clipboard.writeText(text).then(() => {
    toast('📋 Case info copied to clipboard', 'success');
  }).catch(err => {
    console.error('Clipboard error:', err);
    toast('Failed to copy', 'error');
  });
};

window.saveEditDrawer = async function() {
  const booking = window._abEditingBooking;
  if (!booking) return;
  const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  const getNum = id => { const v = get(id); return v !== '' ? parseFloat(v) : undefined; };

  const payload = {};
  const fields = {
    defendant_name: get('abEditDefName'),
    defendant_phone: get('abEditDefPhone'),
    defendant_address: get('abEditDefAddress'),
    defendant_dob: get('abEditDefDob'),
    defendant_email: get('abEditDefEmail'),
    booking_page_url: get('abEditBookingUrl'),
    county: get('abEditCounty'),
    facility: get('abEditFacility'),
    bond_amount: getNum('abEditBondAmount'),
    premium: getNum('abEditPremium'),
    insurance_company: get('abEditInsurance'),
    poa_number: get('abEditPOA'),
    case_number: get('abEditCaseNum'),
    court_date: get('abEditCourtDate'),
    court_location: get('abEditCourtLocation'),
    charges: get('abEditCharges'),
    indemnitor_name: get('abEditIndemName'),
    indemnitor_phone: get('abEditIndemPhone'),
    indemnitor_email: get('abEditIndemEmail'),
    indemnitor_relationship: get('abEditIndemRel'),
    ref1_name: get('abEditRef1Name'),
    ref1_phone: get('abEditRef1Phone'),
    ref2_name: get('abEditRef2Name'),
    ref2_phone: get('abEditRef2Phone'),
    agent_name: get('abEditAgentName'),
    notes: get('abEditNotes'),
    check_in_required: document.getElementById('abEditCIRequired')?.checked,
    check_in_frequency_days: getNum('abEditCIFreq'),
    agent: 'Dashboard',
  };
  Object.entries(fields).forEach(([k, v]) => { if (v !== undefined && v !== '') payload[k] = v; });

  const btn = document.getElementById('abEditSaveBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

  try {
    const r = await fetch(`${API}/api/active-bonds/${encodeURIComponent(booking)}/edit`, {
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

window._buildEditDrawer = function() {
  const overlay = document.createElement('div');
  overlay.id = 'abEditOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:1000;display:none;cursor:pointer';
  overlay.onclick = window.closeEditDrawer;
  document.body.appendChild(overlay);

  const drawer = document.createElement('div');
  drawer.id = 'abEditDrawer';
  drawer.style.cssText = 'position:fixed;top:0;right:-520px;width:500px;height:100vh;background:var(--bg);border-left:1px solid var(--border);z-index:1001;transition:right .3s ease;overflow-y:auto;display:flex;flex-direction:column';
  drawer.innerHTML = `
    <div style="padding:20px 24px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;background:var(--panel)">
      <div style="display:flex;align-items:center;gap:12px">
        <h3 id="abEditDrawerTitle" style="margin:0;font-size:16px">✏️ Edit Bond</h3>
        <button onclick="window.copyBondToClipboard()" title="Copy Case Info to Clipboard" style="background:rgba(59,130,246,0.1);border:1px solid rgba(59,130,246,0.3);color:#3b82f6;border-radius:6px;padding:4px 8px;font-size:11px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:4px">📋 Copy Info</button>
      </div>
      <button onclick="window.closeEditDrawer()" style="background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer">✕</button>
    </div>
    <div style="flex:1;overflow-y:auto;padding:20px 24px">
      ${_es('Defendant', `
        ${_ef('abEditDefName','text','Full Name','Last, First Middle')}
        ${_ef('abEditDefPhone','tel','Phone','+12395550000')}
        ${_ef('abEditDefDob','text','Date of Birth','MM/DD/YYYY')}
        ${_ef('abEditDefEmail','email','Email Address','defendant@example.com')}
        ${_ef('abEditDefAddress','text','Address','Street, City, State, Zip')}
        ${_ef('abEditBookingUrl','text','Sheriff Booking Link','https://...')}
        ${_ef('abEditCounty','text','County','e.g. Lee')}
        ${_ef('abEditFacility','text','Facility','e.g. Lee County Jail')}
        <label style="grid-column:1/-1;display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)">Charges<textarea id="abEditCharges" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;min-height:60px;resize:vertical"></textarea></label>
      `)}
      ${_es('Bond Details', `
        ${_ef('abEditBondAmount','number','Bond Amount ($)','')}
        ${_ef('abEditPremium','number','Premium ($)','')}
        <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)">Insurance<select id="abEditInsurance" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px"><option value="OSI">🛡️ OSI</option><option value="PALMETTO">🌴 Palmetto</option></select></label>
      `)}
      ${_es('Indemnitor (linked to this case)', `
        ${_ef('abEditIndemName','text','Name','Full name')}
        ${_ef('abEditIndemPhone','tel','Phone','+12395550000')}
        ${_ef('abEditIndemEmail','email','Email','email@example.com')}
        ${_ef('abEditIndemRel','text','Relationship','e.g. Spouse, Mother, Friend')}
        <div style="grid-column:1/-1;display:flex;gap:8px;flex-wrap:wrap;margin-top:4px">
          <button type="button" class="btn-export" style="font-size:11px;padding:5px 10px;background:#8b5cf6;color:#fff" onclick="SLRelationships&&SLRelationships.findFromEdit('indemnitor')">🔗 All bonds for this indemnitor</button>
          <button type="button" class="btn-export" style="font-size:11px;padding:5px 10px;background:#6366f1;color:#fff" onclick="SLRelationships&&SLRelationships.openGraph(window._abEditBookingNumber)">🕸️ Case relationship map</button>
        </div>
      `)}
      ${_es('References', `
        ${_ef('abEditRef1Name','text','Ref 1 Name','Full name')}
        ${_ef('abEditRef1Phone','tel','Ref 1 Phone','+12395550000')}
        ${_ef('abEditRef2Name','text','Ref 2 Name','Full name')}
        ${_ef('abEditRef2Phone','tel','Ref 2 Phone','+12395550000')}
      `)}
      ${_es('System Details', `
        ${_ef('abEditPOA','text','POA Number','e.g. PSC2 2644680')}
        ${_ef('abEditCaseNum','text','Case Number','e.g. 24-CF-001234')}
        ${_ef('abEditCourtDate','date','Court Date','')}
        ${_ef('abEditCourtLocation','text','Court Location','e.g. Lee County Courthouse')}
        ${_ef('abEditAgentName','text','Agent Name','e.g. Brendan')}
      `)}
      ${_es('Notes', `
        <label style="grid-column:1/-1;display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)">Internal Notes<textarea id="abEditNotes" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;min-height:80px;resize:vertical" placeholder="Internal notes…"></textarea></label>
      `)}
      ${_es('Check-In Settings', `
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px"><input id="abEditCIRequired" type="checkbox" style="width:16px;height:16px"> Check-In Required</label>
        ${_ef('abEditCIFreq','number','Frequency (days)','30')}
      `)}
      <div style="margin-bottom:20px">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:10px;padding-bottom:4px;border-bottom:1px solid var(--border)">Related Cases (same people)</div>
        <div id="abRelatedCasesPanel" style="background:var(--surface,var(--panel));border:1px solid var(--border);border-radius:8px;padding:12px;min-height:40px;font-size:12px;color:var(--muted)">Open drawer to load related cases…</div>
        <div style="margin-top:8px">
          <button type="button" class="btn-export" style="font-size:11px;padding:5px 10px" onclick="SLRelationships&&SLRelationships.findFromEdit('defendant')">🔗 All bonds for this defendant</button>
        </div>
      </div>

      <div style="margin-bottom:20px">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:10px;padding-bottom:4px;border-bottom:1px solid var(--border)">Compliance Status</div>
        <div id="abCompliancePanel" style="background:var(--surface,var(--panel));border:1px solid var(--border);border-radius:8px;padding:14px;min-height:60px">
          <div style="color:var(--muted);font-size:12px;text-align:center">Loading compliance data…</div>
        </div>
      </div>
      <div style="margin-bottom:20px">
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:10px;padding-bottom:4px;border-bottom:1px solid var(--border)">Bond Renewal History</div>
        <div id="abRenewalHistoryPanel" style="background:var(--surface,var(--panel));border:1px solid var(--border);border-radius:8px;padding:14px;min-height:40px">
          <div style="color:var(--muted);font-size:12px;text-align:center">Loading renewal history…</div>
        </div>
      </div>
    </div>
    <div style="padding:16px 24px;border-top:1px solid var(--border);display:flex;gap:8px;justify-content:flex-end;background:var(--panel)">
      <button onclick="window.closeEditDrawer()" style="padding:8px 16px;background:var(--panel);border:1px solid var(--border);border-radius:6px;color:var(--text);cursor:pointer">Cancel</button>
      <button onclick="openRenewBondModal(window._abEditBookingNumber||'','')" style="padding:8px 16px;background:#f59e0b;border:none;border-radius:6px;color:#fff;cursor:pointer;font-weight:600">🔄 Renew Bond</button>
      <button id="abEditSaveBtn" onclick="window.saveEditDrawer()" style="padding:8px 20px;background:var(--accent);border:none;border-radius:6px;color:#fff;cursor:pointer;font-weight:600">💾 Save Changes</button>
    </div>`;
  document.body.appendChild(drawer);

  const style = document.createElement('style');
  style.textContent = '#abEditDrawer.open{right:0!important} #abEditOverlay.show{display:block!important}';
  document.head.appendChild(style);
}

/* ══════════════════════════════════════════════════════════════════
   ADD BOND MODAL
   ══════════════════════════════════════════════════════════════════ */
window.openAddBondModal = function() {
  if (window.SLRecordBond && typeof window.SLRecordBond.open === 'function') {
    window.SLRecordBond.open();
  } else {
    // Fallback to legacy modal if Record Bond isn't available
    if (!document.getElementById('abAddBondModal')) window._buildAddBondModal();
    const modal = document.getElementById('abAddBondModal');
    if (modal) {
      modal.querySelectorAll('input,select,textarea').forEach(el => {
        if (el.type === 'checkbox') el.checked = false;
        else if (el.tagName === 'SELECT') el.selectedIndex = 0;
        else el.value = '';
      });
      const agentEl = document.getElementById('abAddAgent');
      if (agentEl) agentEl.value = 'Brendan';
      const freqEl = document.getElementById('abAddCIFreq');
      if (freqEl) freqEl.value = '30';
      modal.classList.add('active');
      modal.style.display = 'flex';
    }
  }
}

window.closeAddBondModal = function() {
  const m = document.getElementById('abAddBondModal');
  if (m) { m.classList.remove('active'); m.style.display = 'none'; }
}

window.submitAddBond = async function() {
  const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
  const booking = get('abAddBooking');
  const defName = get('abAddDefName');
  if (!booking) { toast('Booking number is required', 'error'); return; }
  if (!defName)  { toast('Defendant name is required', 'error'); return; }

  const payload = {
    booking_number: booking,
    defendant_name: defName,
    defendant_phone: get('abAddDefPhone'),
    defendant_dob: get('abAddDefDob'),
    defendant_address: get('abAddDefAddress'),
    booking_page_url: get('abAddBookingUrl'),
    county: get('abAddCounty'),
    facility: get('abAddFacility'),
    bond_amount: parseFloat(get('abAddBondAmount')) || 0,
    premium: parseFloat(get('abAddPremium')) || 0,
    insurance_company: get('abAddInsurance') || 'OSI',
    poa_number: get('abAddPOA'),
    case_number: get('abAddCaseNum'),
    court_date: get('abAddCourtDate'),
    court_location: get('abAddCourtLocation'),
    charges: get('abAddCharges'),
    indemnitor_name: get('abAddIndemName'),
    indemnitor_phone: get('abAddIndemPhone'),
    indemnitor_email: get('abAddIndemEmail'),
    ref1_name: get('abAddRef1Name'),
    ref1_phone: get('abAddRef1Phone'),
    ref2_name: get('abAddRef2Name'),
    ref2_phone: get('abAddRef2Phone'),
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

window._buildAddBondModal = function() {
  const modal = document.createElement('div');
  modal.id = 'abAddBondModal';
  modal.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1002;align-items:center;justify-content:center';
  modal.innerHTML = `
    <div style="background:var(--bg);border:1px solid var(--border);border-radius:12px;width:min(680px,95vw);max-height:90vh;overflow-y:auto;display:flex;flex-direction:column">
      <div style="padding:20px 24px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;background:var(--panel);border-radius:12px 12px 0 0">
        <h3 style="margin:0;font-size:16px">➕ Add Active Bond</h3>
        <button onclick="window.closeAddBondModal()" style="background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer">✕</button>
      </div>
      <div style="padding:20px 24px;overflow-y:auto">
        ${_es('Defendant', `
          <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)">Booking # <span style="color:var(--danger)">*</span><input id="abAddBooking" type="text" placeholder="e.g. 2024-00012345" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px"></label>
          <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)">Full Name <span style="color:var(--danger)">*</span><input id="abAddDefName" type="text" placeholder="Last, First Middle" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px"></label>
          ${_ef('abAddDefPhone','tel','Phone','+12395550000')}
          ${_ef('abAddDefDob','text','DOB','MM/DD/YYYY')}
          ${_ef('abAddDefAddress','text','Address','Street, City, State, Zip')}
          ${_ef('abAddBookingUrl','text','Sheriff Booking Link','https://...')}
          ${_ef('abAddRef1Name','text','Ref 1 Name','Full name')}
          ${_ef('abAddRef1Phone','tel','Ref 1 Phone','+12395550000')}
          ${_ef('abAddRef2Name','text','Ref 2 Name','Full name')}
          ${_ef('abAddRef2Phone','tel','Ref 2 Phone','+12395550000')}
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
        ${_es('References', `
          ${_ef('abAddRef1Name','text','Ref 1 Name','Full name')}
          ${_ef('abAddRef1Phone','tel','Ref 1 Phone','+12395550000')}
          ${_ef('abAddRef2Name','text','Ref 2 Name','Full name')}
          ${_ef('abAddRef2Phone','tel','Ref 2 Phone','+12395550000')}
        `)}
        ${_es('Check-In', `
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px"><input id="abAddCIRequired" type="checkbox" style="width:16px;height:16px"> Check-In Required</label>
          ${_ef('abAddCIFreq','number','Frequency (days)','30')}
        `)}
      </div>
      <div style="padding:16px 24px;border-top:1px solid var(--border);display:flex;gap:8px;justify-content:flex-end;background:var(--panel);border-radius:0 0 12px 12px">
        <button onclick="window.closeAddBondModal()" style="padding:8px 16px;background:var(--panel);border:1px solid var(--border);border-radius:6px;color:var(--text);cursor:pointer">Cancel</button>
        <button id="abAddSubmitBtn" onclick="window.submitAddBond()" style="padding:8px 20px;background:var(--accent);border:none;border-radius:6px;color:#fff;cursor:pointer;font-weight:600">✅ Add Bond</button>
      </div>
    </div>`;
  modal.addEventListener('click', e => { if (e.target === modal) window.closeAddBondModal(); });
  document.body.appendChild(modal);
}

/* ══════════════════════════════════════════════════════════════════
   LOCATION HISTORY MODAL
   ══════════════════════════════════════════════════════════════════ */
window.showLocationHistory = async function(bookingNumber, defName) {
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
let _abCheckinPortalUrl = '';

window.openCheckinModal = function(booking, name) {
  _abCheckinBooking = booking;
  _abCheckinName = name;
  _abCheckinPortalUrl = '';
  const nameEl = document.getElementById('abCheckinDefName');
  if (nameEl) nameEl.textContent = `📍 Check-In: ${name}`;
  ['abCheckinLat','abCheckinLng','abCheckinCounty','abCheckinPhone'].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = '';
  });
  const urlEl = document.getElementById('abCheckinPortalUrl');
  if (urlEl) urlEl.textContent = '';
  const srcEl = document.getElementById('abCheckinSource');
  if (srcEl) srcEl.value = 'manual';
  // Prefill defendant phone from bond if available (staff can correct)
  try {
    const bond = (_abBonds || []).find(b => b.booking_number === booking) || {};
    const phone = bond.defendant_phone || bond.phone || '';
    const phoneEl = document.getElementById('abCheckinPhone');
    if (phoneEl && phone) phoneEl.value = phone;
    if (bond.checkin_portal_url) {
      _abCheckinPortalUrl = bond.checkin_portal_url;
      if (urlEl) urlEl.textContent = bond.checkin_portal_url;
    }
  } catch (_) {}
  document.getElementById('abCheckinModal')?.classList.add('show');
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(pos => {
      const lat = document.getElementById('abCheckinLat');
      const lng = document.getElementById('abCheckinLng');
      if (lat) lat.value = pos.coords.latitude.toFixed(6);
      if (lng) lng.value = pos.coords.longitude.toFixed(6);
      const src = document.getElementById('abCheckinSource');
      if (src) src.value = 'gps';
      toast('📍 Staff GPS captured', 'success', 2000);
    }, () => {});
  }
}

function closeCheckinModal() {
  document.getElementById('abCheckinModal')?.classList.remove('show');
  _abCheckinBooking = '';
  _abCheckinName = '';
  _abCheckinPortalUrl = '';
}

async function enableBondCheckin() {
  if (!_abCheckinBooking) { toast('No booking selected', 'error'); return; }
  try {
    const agent = (typeof SL !== 'undefined' && SL.currentAgent) || localStorage.getItem('slcAgent') || 'staff';
    const r = await fetch(`${API}/api/active-bonds/${encodeURIComponent(_abCheckinBooking)}/enable-checkin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frequency_days: 7, actor: agent, provision_traccar: true }),
    });
    const result = await r.json();
    if (result.success) {
      _abCheckinPortalUrl = result.portal_url || '';
      const urlEl = document.getElementById('abCheckinPortalUrl');
      if (urlEl) urlEl.textContent = _abCheckinPortalUrl || '(no URL — check bond exists)';
      const setupEl = document.getElementById('abTraccarSetup');
      const tr = result.traccar || {};
      if (setupEl) {
        if (tr.success && tr.setup) {
          setupEl.textContent = `Traccar: ${tr.unique_id || ''}\n${tr.setup.instructions || ''}`;
        } else if (tr.error) {
          setupEl.textContent = `Traccar: not ready (${tr.error}) — portal check-ins still work`;
        } else {
          setupEl.textContent = '';
        }
      }
      toast('✅ Monitoring + Traccar device ready — send portal link to defendant', 'success');
    } else {
      toast(result.error || 'Enable failed', 'error');
    }
  } catch (e) {
    toast('Network error enabling check-in', 'error');
  }
}

async function provisionTraccarContinuous() {
  if (!_abCheckinBooking) { toast('No booking selected', 'error'); return; }
  if (!confirm('Enable continuous GPS via Traccar Client app? Defendant must install the app knowingly (not covert).')) {
    return;
  }
  try {
    const agent = (typeof SL !== 'undefined' && SL.currentAgent) || localStorage.getItem('slcAgent') || 'staff';
    const phone = document.getElementById('abCheckinPhone')?.value?.trim() || '';
    const r = await fetch(`${API}/api/active-bonds/${encodeURIComponent(_abCheckinBooking)}/provision-traccar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ continuous_gps: true, actor: agent, phone }),
    });
    const result = await r.json();
    const setupEl = document.getElementById('abTraccarSetup');
    if (result.success) {
      if (setupEl && result.setup) {
        setupEl.textContent = result.setup.instructions || JSON.stringify(result.setup);
      }
      toast('📡 Traccar continuous GPS provisioned — install task created', 'success');
    } else {
      if (setupEl) setupEl.textContent = result.error || 'Traccar provision failed';
      toast(result.error || 'Traccar provision failed', 'error');
    }
  } catch (e) {
    toast('Network error provisioning Traccar', 'error');
  }
}

function copyCheckinPortalUrl() {
  if (!_abCheckinPortalUrl) {
    toast('Enable monitoring first to generate a portal URL', 'error');
    return;
  }
  navigator.clipboard?.writeText(_abCheckinPortalUrl).then(
    () => toast('Portal URL copied', 'success'),
    () => toast(_abCheckinPortalUrl, 'info', 8000),
  );
}

async function sendBondCheckinLink() {
  if (!_abCheckinBooking) { toast('No booking selected', 'error'); return; }
  const phone = document.getElementById('abCheckinPhone')?.value?.trim() || '';
  if (!phone) {
    toast('Enter the validated defendant phone before sending', 'error');
    return;
  }
  if (!confirm(`Send transparent check-in link to this number for ${_abCheckinName || _abCheckinBooking}?`)) {
    return;
  }
  try {
    const agent = (typeof SL !== 'undefined' && SL.currentAgent) || localStorage.getItem('slcAgent') || 'staff';
    const r = await fetch(`${API}/api/active-bonds/${encodeURIComponent(_abCheckinBooking)}/send-checkin-link`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, actor: agent }),
    });
    const result = await r.json();
    if (result.success || result.message_sent) {
      _abCheckinPortalUrl = result.portal_url || _abCheckinPortalUrl;
      const urlEl = document.getElementById('abCheckinPortalUrl');
      if (urlEl && _abCheckinPortalUrl) urlEl.textContent = _abCheckinPortalUrl;
      toast(`📲 Check-in link sent via ${result.channel || 'message'}`, 'success');
    } else {
      toast(result.error || 'Send failed', 'error');
    }
  } catch (e) {
    toast('Network error sending check-in link', 'error');
  }
}

async function submitCheckin() {
  if (!_abCheckinBooking) { toast('No booking selected', 'error'); return; }
  const lat    = parseFloat(document.getElementById('abCheckinLat')?.value);
  const lng    = parseFloat(document.getElementById('abCheckinLng')?.value);
  const county = document.getElementById('abCheckinCounty')?.value?.trim() || '';
  const source = document.getElementById('abCheckinSource')?.value || 'manual';
  try {
    const r = await fetch(`${API}/api/active-bonds/${encodeURIComponent(_abCheckinBooking)}/check-in`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        method: source,
        location: county,
        notes: county ? `County: ${county}` : '',
        gps_lat: Number.isFinite(lat) ? lat : null,
        gps_lon: Number.isFinite(lng) ? lng : null,
      }),
    });
    const result = await r.json();
    if (result.success) {
      toast(`✅ Check-in recorded for ${_abCheckinName}`, 'success');
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

  // Look up FTA data from the bond record
  const bond = (_abBonds || []).find(b => b.booking_number === bookingNumber) || {};
  const ftaScore = bond.fta_risk_score;
  const ftaLevel = (bond.fta_risk_level || '').toLowerCase();
  const ftaConf = bond.fta_risk_confidence;
  const ftaColors = { critical: '#ff4444', high: '#ff8800', moderate: '#ffcc00', low: '#44bb44' };
  const ftaColor = ftaColors[ftaLevel] || '#888';
  const ftaSection = ftaScore != null ? `
    <div style="margin-top:16px;padding:14px;background:${ftaColor}11;border:1px solid ${ftaColor}33;border-radius:8px">
      <div style="font-size:11px;font-weight:600;color:${ftaColor};text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">⚡ FTA Predictive Intelligence</div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
        <span style="font-size:12px;color:var(--muted)">FTA Risk Score</span>
        <span style="font-size:16px;font-weight:700;color:${ftaColor}">${ftaScore}<span style="font-size:11px;color:var(--muted)">/100</span></span>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
        <span style="font-size:12px;color:var(--muted)">Risk Level</span>
        <span style="font-size:12px;font-weight:600;color:${ftaColor}">${ftaLevel.charAt(0).toUpperCase() + ftaLevel.slice(1)}</span>
      </div>
      ${ftaConf != null ? `<div style="display:flex;justify-content:space-between;align-items:center"><span style="font-size:12px;color:var(--muted)">Model Confidence</span><span style="font-size:12px;font-weight:600;color:var(--text)">${(ftaConf * 100).toFixed(1)}%</span></div>` : ''}
    </div>` : '';

  const modal = document.createElement('div');
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1003;display:flex;align-items:center;justify-content:center';
  modal.innerHTML = `
    <div style="background:var(--bg);border:1px solid var(--border);border-radius:12px;width:min(400px,90vw);max-height:80vh;overflow-y:auto">
      <div style="padding:16px 20px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;background:var(--panel);border-radius:12px 12px 0 0">
        <h3 style="margin:0;font-size:15px">⚠️ Risk Profile — ${escHtml(defName || bookingNumber)}</h3>
        <button onclick="this.closest('[style*=fixed]').remove()" style="background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer">✕</button>
      </div>
      <div style="padding:20px">
        <div style="text-align:center;margin-bottom:20px">
          <div style="font-size:52px;font-weight:700;color:${risk >= 70 ? 'var(--danger)' : risk >= 40 ? '#f59e0b' : 'var(--accent)'}">${risk}<span style="font-size:20px;color:var(--muted)">/100</span></div>
          <div style="color:var(--muted);font-size:13px">${risk >= 70 ? '🔴 High Risk' : risk >= 40 ? '🟡 Medium Risk' : '🟢 Low Risk'}</div>
          <div style="font-size:10px;color:var(--muted);margin-top:4px">Forfeiture Risk Score</div>
        </div>
        ${lines}
        ${ftaSection}
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
window.addManualAlert = async function(booking, name) {
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
window.openInTracking = function(bookingNumber) {
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
window.exonerateFromActiveBonds = async function(bookingNumber, defName) {
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

/* ══════════════════════════════════════════════════════════════════
   PAYMENT LINK & IMESSAGE SHORTCUTS
   ══════════════════════════════════════════════════════════════════ */

/**
 * Send SwipeSimple payment link to indemnitor via iMessage.
 * Falls back to copying the link if BB is offline.
 */
window.sendPaymentLink = async function(bookingNumber, defendantName, phone) {
  const paymentLink = window._SWIPESIMPLE_LINK || 'https://swipesimple.com/links/lnk_b6bf996f4c57bb340a150e297e769abd';
  const cleanPhone = (phone || '').replace(/\D/g, '');

  if (!cleanPhone) {
    // No phone — just copy the link
    try { await navigator.clipboard.writeText(paymentLink); } catch(e) { /* ignore */ }
    toast('No phone on file — payment link copied to clipboard', 'warning');
    return;
  }

  const message = `Hi! This is Shamrock Bail Bonds. Here is your secure payment link for ${defendantName}'s bond: ${paymentLink} — Please complete payment at your earliest convenience. Questions? Call us at (239) 224-5454.`;

  try {
    const r = await fetch('/api/imessage/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: '+1' + cleanPhone.slice(-10), message }),
    });
    const d = await r.json();
    if (d.success) {
      toast(`💳 Payment link sent to ${phone} via iMessage`, 'success');
    } else {
      // BB offline — fallback: open SMS
      window.open(`sms:${phone}?body=${encodeURIComponent(message)}`);
      toast('BB offline — opened SMS fallback', 'warning');
    }
  } catch(e) {
    window.open(`sms:${phone}?body=${encodeURIComponent(message)}`);
    toast('Opened SMS fallback', 'warning');
  }
}

/**
 * Open iMessage compose for a bond's indemnitor.
 * If the iMessage tab is available, switches to it and pre-fills the compose area.
 * Otherwise falls back to sms: link.
 */
window.sendBondImessage = function(bookingNumber, defendantName, phone) {
  const cleanPhone = (phone || '').replace(/\D/g, '');
  if (!cleanPhone || cleanPhone.length < 10) {
    toast('No valid phone number on file for this bond', 'error');
    return;
  }
  const digits10 = cleanPhone.slice(-10);
  const formattedPhone = '+1' + digits10;
  const displayPhone = `(${digits10.slice(0,3)}) ${digits10.slice(3,6)}-${digits10.slice(6)}`;
  const defaultMsg = `Hi, this is Shamrock Bail Bonds regarding ${defendantName}'s bond. `;

  // Prefer in-app iMessage compose (SLiMessage module)
  if (window.SLiMessage && typeof window.SLiMessage.openCompose === 'function') {
    const imTab = document.querySelector('[data-tab="tabImessage"]');
    if (imTab && typeof SL !== 'undefined' && typeof SL.switchTab === 'function') {
      SL.switchTab(imTab);
    }
    // Small delay to let tab transition complete before opening compose
    setTimeout(() => {
      window.SLiMessage.openCompose(formattedPhone, defaultMsg);
    }, 250);
    toast(`💬 iMessage compose opened for ${displayPhone}`, 'info');
    return;
  }

  // Fallback: native SMS deep link (works on macOS + iOS)
  const smsUrl = `sms:${formattedPhone}?body=${encodeURIComponent(defaultMsg)}`;
  const a = document.createElement('a');
  a.href = smsUrl;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 500);
  toast(`💬 SMS app opened for ${displayPhone}`, 'info');
}

/* ══════════════════════════════════════════════════════════════════
   KANBAN BOARD — Bond Portfolio View
   ══════════════════════════════════════════════════════════════════

   Provides a drag-and-drop Kanban view of all active bonds organised
   by status column.  Works alongside the existing table view — both
   share the same _abBonds data array and the same loadActiveBonds()
   refresh cycle.

   Public API exposed on window.SLKanban:
     .render()          — (re)render the board from current _abBonds
     .toggle(view)      — switch between 'table' | 'kanban'
     .openPoaSwap(b)    — open the POA quick-swap modal for bond b
     .loadStatusHistory(booking) — fetch & render status timeline
   ══════════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ── Constants ──────────────────────────────────────────────────── */
  const COLUMNS = [
    { status: 'active',      label: 'Active',      icon: '🟢' },
    { status: 'monitoring',  label: 'Monitoring',  icon: '🔵' },
    { status: 'alert',       label: 'Alert',       icon: '🔴' },
    { status: 'exonerated',  label: 'Exonerated',  icon: '✅' },
    { status: 'forfeited',   label: 'Forfeited',   icon: '❌' },
    { status: 'surrendered', label: 'Surrendered', icon: '🏳️' },
    { status: 'reinstated',  label: 'Reinstated',  icon: '🔄' },
  ];

  /* ── State ──────────────────────────────────────────────────────── */
  let _currentView = 'table';   // 'table' | 'kanban'
  let _dragCard    = null;      // DOM element being dragged
  let _dragBond    = null;      // bond object being dragged
  let _ghost       = null;      // placeholder DOM element

  /* ── DOM helpers ────────────────────────────────────────────────── */
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  /* ── Days-until helper ──────────────────────────────────────────── */
  function daysUntil(dateStr) {
    if (!dateStr) return null;
    return Math.ceil((new Date(dateStr) - new Date()) / 86400000);
  }

  /* ══════════════════════════════════════════════════════════════════
     RENDER KANBAN BOARD
     ══════════════════════════════════════════════════════════════════ */
  function render() {
    const board = document.getElementById('abKanbanBoard');
    if (!board) return;

    const bonds = window._abBonds || [];
    const filter = window._abFilter || 'all';

    // Apply same filter as table view
    const filtered = filter === 'all' ? bonds : bonds.filter(b => b.status === filter);

    // Group by status
    const byStatus = {};
    COLUMNS.forEach(c => { byStatus[c.status] = []; });
    filtered.forEach(b => {
      const s = (b.status || 'active').toLowerCase();
      if (byStatus[s]) byStatus[s].push(b);
      else byStatus['active'].push(b);  // fallback unknown statuses to active
    });

    board.innerHTML = '';

    COLUMNS.forEach(col => {
      const cards = byStatus[col.status] || [];
      const colEl = document.createElement('div');
      colEl.className = 'kb-col';
      colEl.dataset.status = col.status;

      colEl.innerHTML = `
        <div class="kb-col-header">
          <span class="kb-col-title">${col.icon} ${col.label}</span>
          <span class="kb-col-count">${cards.length}</span>
        </div>
        <div class="kb-col-body" data-status="${col.status}"></div>
      `;

      const body = colEl.querySelector('.kb-col-body');
      cards.forEach(bond => body.appendChild(buildCard(bond)));

      // Drop zone events
      body.addEventListener('dragover', onDragOver);
      body.addEventListener('dragleave', onDragLeave);
      body.addEventListener('drop', onDrop);

      board.appendChild(colEl);
    });
  }

  /* ── Build a single Kanban card ─────────────────────────────────── */
  function buildCard(bond) {
    const card = document.createElement('div');
    card.className = 'kb-card' + (bond.status === 'alert' ? ' kb-card-alert' : '');
    card.draggable = true;
    card.dataset.booking = bond.booking_number;

    const days = daysUntil(bond.court_date);
    const courtClass = days !== null && days <= 7 ? 'kb-tag-court-soon' : 'kb-tag-court-ok';
    const courtLabel = days !== null ? (days < 0 ? `${Math.abs(days)}d overdue` : `${days}d to court`) : '—';
    const amount = bond.bond_amount ? '$' + Number(bond.bond_amount).toLocaleString() : '—';
    const poa = bond.poa_number || bond.poa_full || '—';
    const surety = (bond.insurance_company || bond.surety || '').replace('Old Surety Insurance', 'OSI').replace('Palmetto Surety', 'PSC');

    card.innerHTML = `
      <div class="kb-card-name" title="${escHtml(bond.defendant_name)}">${escHtml(bond.defendant_name || 'Unknown')}</div>
      <div class="kb-card-booking">${escHtml(bond.booking_number || '')} · ${escHtml(bond.county || '')} Co.</div>
      <div class="kb-card-meta">
        <span class="kb-tag kb-tag-amount">${amount}</span>
        <span class="kb-tag ${courtClass}">${courtLabel}</span>
        ${surety ? `<span class="kb-tag">${escHtml(surety)}</span>` : ''}
        ${bond.fta_risk_score != null ? `<span class="kb-tag" style="font-size:9px">${_abFtaBadge(bond)}</span>` : ''}
      </div>
      <div class="kb-card-poa">
        <span class="kb-card-poa-label">POA:</span>
        <span class="kb-card-poa-value" title="${escHtml(poa)}">${escHtml(poa)}</span>
        <button class="kb-card-poa-edit-btn" title="Swap POA" onclick="SLKanban.openPoaSwap(${JSON.stringify(bond).replace(/"/g, '&quot;')})">⇄</button>
      </div>
      <div class="kb-card-actions">
        <button onclick="openEditDrawer('${escHtml(bond.booking_number)}')">Edit</button>
        <button onclick="openInTracking('${escHtml(bond.booking_number)}')">Track</button>
        <button onclick="sendBondImessage('${escHtml(bond.booking_number)}','${escHtml(bond.defendant_name)}','${escHtml(bond.indemnitor_phone||'')}')">💬</button>
        <button onclick="SLKanban.loadStatusHistory('${escHtml(bond.booking_number)}')">History</button>
      </div>
    `;

    // Drag events
    card.addEventListener('dragstart', (e) => onDragStart(e, bond, card));
    card.addEventListener('dragend',   onDragEnd);

    // Touch drag fallback
    addTouchDrag(card, bond);

    return card;
  }

  /* ══════════════════════════════════════════════════════════════════
     DRAG AND DROP (Mouse)
     ══════════════════════════════════════════════════════════════════ */
  function onDragStart(e, bond, card) {
    _dragBond = bond;
    _dragCard = card;
    card.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', bond.booking_number);
  }

  function onDragEnd() {
    if (_dragCard) _dragCard.classList.remove('dragging');
    removeGhost();
    qsa('.kb-col-body.drag-target').forEach(el => el.classList.remove('drag-target'));
    _dragCard = null;
    _dragBond = null;
  }

  function onDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const body = e.currentTarget;
    body.classList.add('drag-target');

    // Show ghost placeholder
    removeGhost();
    _ghost = document.createElement('div');
    _ghost.className = 'kb-drag-ghost';
    const afterEl = getDragAfterElement(body, e.clientY);
    if (afterEl) body.insertBefore(_ghost, afterEl);
    else body.appendChild(_ghost);
  }

  function onDragLeave(e) {
    if (!e.currentTarget.contains(e.relatedTarget)) {
      e.currentTarget.classList.remove('drag-target');
      removeGhost();
    }
  }

  async function onDrop(e) {
    e.preventDefault();
    const body = e.currentTarget;
    body.classList.remove('drag-target');
    removeGhost();

    const newStatus = body.dataset.status;
    if (!_dragBond || !newStatus) return;
    if (_dragBond.status === newStatus) return;

    await doStatusChange(_dragBond.booking_number, _dragBond.defendant_name, newStatus);
  }

  function getDragAfterElement(container, y) {
    const draggables = qsa('.kb-card:not(.dragging)', container);
    return draggables.reduce((closest, child) => {
      const box = child.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) return { offset, element: child };
      return closest;
    }, { offset: Number.NEGATIVE_INFINITY }).element;
  }

  function removeGhost() {
    if (_ghost && _ghost.parentNode) _ghost.parentNode.removeChild(_ghost);
    _ghost = null;
  }

  /* ══════════════════════════════════════════════════════════════════
     TOUCH DRAG FALLBACK
     ══════════════════════════════════════════════════════════════════ */
  function addTouchDrag(card, bond) {
    let startX, startY, clone, moved;

    card.addEventListener('touchstart', (e) => {
      const t = e.touches[0];
      startX = t.clientX; startY = t.clientY;
      moved = false;
      _dragBond = bond;
      _dragCard = card;
    }, { passive: true });

    card.addEventListener('touchmove', (e) => {
      const t = e.touches[0];
      if (!moved && Math.abs(t.clientX - startX) < 5 && Math.abs(t.clientY - startY) < 5) return;
      moved = true;
      e.preventDefault();

      if (!clone) {
        clone = card.cloneNode(true);
        clone.style.cssText = `position:fixed;opacity:.7;pointer-events:none;z-index:9999;width:${card.offsetWidth}px;`;
        document.body.appendChild(clone);
        card.classList.add('dragging');
      }
      clone.style.left = (t.clientX - card.offsetWidth / 2) + 'px';
      clone.style.top  = (t.clientY - 30) + 'px';

      // Highlight target column
      const el = document.elementFromPoint(t.clientX, t.clientY);
      const targetBody = el && el.closest('.kb-col-body');
      qsa('.kb-col-body.drag-target').forEach(b => b.classList.remove('drag-target'));
      if (targetBody) targetBody.classList.add('drag-target');
    }, { passive: false });

    card.addEventListener('touchend', async (e) => {
      if (clone) { clone.remove(); clone = null; }
      card.classList.remove('dragging');
      qsa('.kb-col-body.drag-target').forEach(b => b.classList.remove('drag-target'));

      if (!moved || !_dragBond) return;

      const t = e.changedTouches[0];
      const el = document.elementFromPoint(t.clientX, t.clientY);
      const targetBody = el && el.closest('.kb-col-body');
      if (!targetBody) return;

      const newStatus = targetBody.dataset.status;
      if (newStatus && newStatus !== _dragBond.status) {
        await doStatusChange(_dragBond.booking_number, _dragBond.defendant_name, newStatus);
      }
      _dragBond = null; _dragCard = null;
    });
  }

  /* ══════════════════════════════════════════════════════════════════
     STATUS CHANGE (shared by drag-drop and card buttons)
     ══════════════════════════════════════════════════════════════════ */
  /* ── Confirmation modal for legally significant status changes ─── */
  function _confirmDestructive(defendantName, newStatus) {
    return new Promise(resolve => {
      const existing = document.getElementById('abDestructiveConfirm');
      if (existing) existing.remove();
      const overlay = document.createElement('div');
      overlay.id = 'abDestructiveConfirm';
      overlay.className = 'sl-modal-overlay';
      overlay.style.cssText = 'display:flex;align-items:center;justify-content:center;z-index:10000;';
      const label = newStatus.charAt(0).toUpperCase() + newStatus.slice(1);
      overlay.innerHTML = `
        <div class="sl-modal" style="max-width:420px;width:90%;padding:28px 24px;border:1px solid var(--danger);">
          <div style="font-size:28px;text-align:center;margin-bottom:10px">⚠️</div>
          <h3 style="color:var(--danger);margin:0 0 10px;text-align:center;font-size:16px">Legally Significant Status Change</h3>
          <p style="color:var(--text);margin:0 0 20px;text-align:center;line-height:1.6;font-size:14px">
            Change status of <strong>${escHtml(defendantName)}</strong> to
            <strong style="color:var(--danger)">${label}</strong>?<br>
            <span style="color:var(--muted);font-size:12px">This action is legally significant and will be logged in the audit trail.</span>
          </p>
          <div style="display:flex;gap:10px;justify-content:center">
            <button id="abDestructCancel" style="padding:9px 22px;border-radius:6px;border:1px solid var(--border);background:var(--panel);color:var(--text);cursor:pointer;font-size:13px;font-weight:600;min-width:80px">Cancel</button>
            <button id="abDestructConfirm" style="padding:9px 22px;border-radius:6px;border:none;background:var(--danger);color:#fff;cursor:pointer;font-size:13px;font-weight:700;min-width:130px">Confirm ${label}</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);
      const cleanup = (result) => { overlay.remove(); resolve(result); };
      overlay.querySelector('#abDestructCancel').addEventListener('click', () => cleanup(false));
      overlay.querySelector('#abDestructConfirm').addEventListener('click', () => cleanup(true));
      overlay.addEventListener('click', e => { if (e.target === overlay) cleanup(false); });
      const onKey = e => { if (e.key === 'Escape') { document.removeEventListener('keydown', onKey); cleanup(false); } };
      document.addEventListener('keydown', onKey);
    });
  }

  async function doStatusChange(bookingNumber, defendantName, newStatus) {
    // Guard: require explicit confirmation for legally significant statuses
    const DESTRUCTIVE = ['forfeited', 'surrendered'];
    if (DESTRUCTIVE.includes(newStatus)) {
      const confirmed = await _confirmDestructive(defendantName, newStatus);
      if (!confirmed) { render(); return; }  // revert any optimistic UI
    }
    // Optimistic UI update
    const bond = (window._abBonds || []).find(b => b.booking_number === bookingNumber);
    const prevStatus = bond ? bond.status : null;
    if (bond) bond.status = newStatus;
    render();

    try {
      const r = await fetch(`/api/active-bonds/${encodeURIComponent(bookingNumber)}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus, agent: 'Kanban Board' }),
      });
      const result = await r.json();
      if (result.success) {
        const msg = result.poa_released
          ? `${defendantName} → ${newStatus} · POA ${result.poa_number || ''} released`
          : `${defendantName} → ${newStatus}`;
        toast(msg, newStatus === 'exonerated' ? 'success' : 'info');
        // Full reload to sync KPIs
        if (typeof loadActiveBonds === 'function') loadActiveBonds();
      } else {
        // Revert optimistic update to previous status
        if (bond) bond.status = result.from_status || prevStatus || bond.status;
        render();
        toast(result.error || 'Status update failed', 'error');
      }
    } catch (e) {
      if (bond && prevStatus) bond.status = prevStatus;  // revert to known-good state
      render();
      toast('Network error updating status', 'error');
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     VIEW TOGGLE
     ══════════════════════════════════════════════════════════════════ */
  function toggleView(view) {
    _currentView = view;
    const table  = document.getElementById('abTableWrapper') || qs('.ab-table-wrapper') || qs('#abTable')?.closest('div');
    const board  = document.getElementById('abKanbanBoard');
    const btnTable  = document.getElementById('abViewTable');
    const btnKanban = document.getElementById('abViewKanban');

    if (!board) return;

    if (view === 'kanban') {
      if (table) table.style.display = 'none';
      board.classList.add('visible');
      if (btnTable)  btnTable.classList.remove('active');
      if (btnKanban) btnKanban.classList.add('active');
      render();
    } else {
      if (table) table.style.display = '';
      board.classList.remove('visible');
      if (btnTable)  btnTable.classList.add('active');
      if (btnKanban) btnKanban.classList.remove('active');
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     POA QUICK-SWAP MODAL
     ══════════════════════════════════════════════════════════════════ */
  async function openPoaSwap(bond) {
    // Fetch available POAs for this surety
    const suretyRaw = (bond.insurance_company || bond.surety || 'osi').toLowerCase();
    const suretyId  = (suretyRaw.includes('palm') || suretyRaw.includes('psc')) ? 'palmetto' : 'osi';

    let available = [];
    try {
      const r = await fetch(`/api/poa/list?surety_id=${suretyId}&status=available`);
      const d = await r.json();
      available = d.poas || d.items || [];
    } catch (e) {
      toast('Could not load available POAs', 'error');
      return;
    }

    // Build modal
    let modal = document.getElementById('abPoaSwapModal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'abPoaSwapModal';
      modal.className = 'sl-modal-overlay';
      modal.innerHTML = `
        <div class="sl-modal" style="max-width:480px">
          <div class="sl-modal-header">
            <h3 class="sl-modal-title">Swap POA</h3>
            <button class="sl-modal-close" onclick="document.getElementById('abPoaSwapModal').style.display='none'">✕</button>
          </div>
          <div class="sl-modal-body">
            <p id="abPoaSwapSubtitle" style="font-size:13px;color:var(--muted);margin-bottom:12px"></p>
            <div class="poa-swap-list" id="abPoaSwapList"></div>
          </div>
        </div>
      `;
      document.body.appendChild(modal);
    }

    const subtitle = document.getElementById('abPoaSwapSubtitle');
    const list     = document.getElementById('abPoaSwapList');
    if (subtitle) subtitle.textContent = `${bond.defendant_name} · Current POA: ${bond.poa_number || '(none)'} · Surety: ${suretyId.toUpperCase()}`;

    list.innerHTML = available.length === 0
      ? '<p style="color:var(--muted);text-align:center;padding:16px">No available POAs for this surety</p>'
      : available.map(p => `
          <div class="poa-swap-item" onclick="SLKanban._confirmPoaSwap('${escHtml(bond.booking_number)}','${escHtml(bond.poa_number||'')}','${escHtml(p.poa_number)}','${suretyId}','${escHtml(bond.defendant_name)}')">
            <div>
              <div class="poa-num">${escHtml(p.poa_number)}</div>
              <div class="poa-meta">${escHtml(p.poa_prefix || suretyId.toUpperCase())} · Added ${fmtDate(p.added_at)}</div>
            </div>
            <div class="poa-max">${p.max_bond_amount ? '$' + Number(p.max_bond_amount).toLocaleString() : ''}</div>
          </div>
        `).join('');

    modal.style.display = 'flex';
  }

  async function _confirmPoaSwap(bookingNumber, oldPoa, newPoa, suretyId, defendantName) {
    document.getElementById('abPoaSwapModal').style.display = 'none';
    try {
      const r = await fetch('/api/poa/reassign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          poa_number: newPoa,
          surety_id: suretyId,
          new_booking_number: bookingNumber,
          old_booking_number: bookingNumber,
        }),
      });
      const d = await r.json();
      if (d.success) {
        toast(`POA swapped: ${oldPoa || '(none)'} → ${newPoa} for ${defendantName}`, 'success');
        if (typeof loadActiveBonds === 'function') loadActiveBonds();
      } else {
        toast(d.error || 'POA swap failed', 'error');
      }
    } catch (e) {
      toast('Network error during POA swap', 'error');
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     STATUS HISTORY TIMELINE
     ══════════════════════════════════════════════════════════════════ */
  async function loadStatusHistory(bookingNumber) {
    // Try to find the history section in the Edit Drawer first
    const drawerHistoryEl = document.getElementById('abStatusHistoryTimeline');
    if (drawerHistoryEl) {
      drawerHistoryEl.innerHTML = '<p style="color:var(--muted);font-size:12px">Loading…</p>';
    }

    try {
      const r = await fetch(`/api/active-bonds/${encodeURIComponent(bookingNumber)}/status-history`);
      const d = await r.json();
      if (!d.success) { toast(d.error || 'Could not load history', 'error'); return; }

      const history = d.history || [];
      if (!history.length) {
        if (drawerHistoryEl) drawerHistoryEl.innerHTML = '<p style="color:var(--muted);font-size:12px">No status changes recorded yet.</p>';
        return;
      }

      const html = `<div class="ab-status-timeline">${history.map(h => `
        <div class="ab-timeline-entry">
          <div class="tl-transition">${escHtml(h.from_status || '—')} → ${escHtml(h.to_status || '—')}</div>
          <div class="tl-time">${fmtDate(h.timestamp)} ${h.timestamp ? new Date(h.timestamp).toLocaleTimeString() : ''}</div>
          ${h.agent ? `<div class="tl-agent">by ${escHtml(h.agent)}</div>` : ''}
          ${h.note  ? `<div class="tl-note">"${escHtml(h.note)}"</div>` : ''}
        </div>
      `).join('')}</div>`;

      if (drawerHistoryEl) {
        drawerHistoryEl.innerHTML = html;
      } else {
        // Show in a quick modal if drawer isn't open
        toast(`${d.defendant_name}: ${history.length} status change(s) — open Edit Drawer to view timeline`, 'info', 5000);
      }
    } catch (e) {
      toast('Error loading status history', 'error');
    }
  }

  /* ══════════════════════════════════════════════════════════════════
     INIT — wire up view toggle buttons
     ══════════════════════════════════════════════════════════════════ */
  function init() {
    const btnTable  = document.getElementById('abViewTable');
    const btnKanban = document.getElementById('abViewKanban');
    if (btnTable)  btnTable.addEventListener('click',  () => toggleView('table'));
    if (btnKanban) btnKanban.addEventListener('click', () => toggleView('kanban'));

    // Re-render kanban whenever bonds are refreshed (if kanban is active)
    const origLoad = window.loadActiveBonds;
    if (typeof origLoad === 'function') {
      // Patch: after loadActiveBonds resolves, also re-render kanban if visible
      // (loadActiveBonds already calls renderActiveBondsTable; we add kanban re-render)
      const origRender = window.renderActiveBondsTable;
      if (typeof origRender === 'function') {
        window.renderActiveBondsTable = function () {
          origRender.apply(this, arguments);
          if (_currentView === 'kanban') render();
        };
      }
    }
  }

  /* ── setView: explicit switch between table and kanban ─────────── */
  function setView(view) {
    const tablePanel = document.getElementById('abTablePanel');
    const kanbanBoard = document.getElementById('abKanbanBoard');
    const tableBtn = document.getElementById('abViewTableBtn');
    const kanbanBtn = document.getElementById('abViewKanbanBtn');
    if (!tablePanel || !kanbanBoard) return;
    if (view === 'kanban') {
      tablePanel.style.display = 'none';
      kanbanBoard.style.display = 'flex';
      if (tableBtn) tableBtn.classList.remove('active');
      if (kanbanBtn) kanbanBtn.classList.add('active');
      render(window._abBonds || []);
    } else {
      tablePanel.style.display = '';
      kanbanBoard.style.display = 'none';
      if (tableBtn) tableBtn.classList.add('active');
      if (kanbanBtn) kanbanBtn.classList.remove('active');
    }
  }

  /* ── Public API ─────────────────────────────────────────────────── */
  window.SLKanban = {
    render,
    toggle: toggleView,
    setView,
    openPoaSwap,
    _confirmPoaSwap,
    loadStatusHistory,
    init,
  };

  // Auto-init when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();

/* ══════════════════════════════════════════════════════════════════
   COMPLIANCE PANEL — Per-defendant compliance status
   Captira-style: check-in, court, payment compliance
   ══════════════════════════════════════════════════════════════════ */
async function _loadCompliancePanel(bookingNumber) {
  const panel = document.getElementById('abCompliancePanel');
  if (!panel) return;
  panel.innerHTML = '<div style="color:var(--muted);font-size:12px;text-align:center;padding:8px">Loading compliance data…</div>';
  try {
    const r = await fetch(`${API}/api/active-bonds/${encodeURIComponent(bookingNumber)}/compliance`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    if (!d.success) throw new Error(d.error || 'Unknown error');

    const score = d.overall_score || 0;
    const level = d.compliance_level || 'unknown';
    const scoreColor = level === 'compliant' ? 'var(--accent)' : level === 'warning' ? '#f59e0b' : 'var(--danger)';
    const scoreLabel = level === 'compliant' ? '✅ Compliant' : level === 'warning' ? '⚠️ Warning' : '🚨 Critical';

    // Check-in row
    const ci = d.check_in || {};
    const ciStatus = !ci.required ? 'N/A' : ci.overdue ? `<span style="color:var(--danger);font-weight:700">Overdue ${ci.hours_overdue}h</span>` : `<span style="color:var(--accent)">${ci.compliance_pct}% (${ci.checkins_90d} in 90d)</span>`;
    const ciLast = ci.last_checkin ? new Date(ci.last_checkin).toLocaleDateString('en-US',{month:'short',day:'numeric'}) : '<span style="color:var(--muted)">Never</span>';

    // Court row
    const ct = d.court || {};
    let courtStatus = '—';
    if (ct.court_date) {
      const daysUntil = ct.days_until;
      if (ct.status === 'past') courtStatus = `<span style="color:var(--danger)">Past (${Math.abs(daysUntil)}d ago)</span>`;
      else if (ct.status === 'today') courtStatus = `<span style="color:var(--danger);font-weight:700">TODAY</span>`;
      else if (ct.status === 'imminent') courtStatus = `<span style="color:var(--danger)">${daysUntil}d away</span>`;
      else if (ct.status === 'upcoming') courtStatus = `<span style="color:#f59e0b">${daysUntil}d away</span>`;
      else courtStatus = `<span style="color:var(--muted)">${daysUntil}d away</span>`;
    }

    // Payment row
    const pay = d.payment || {};
    let payStatus = '—';
    if (pay.status === 'paid') payStatus = `<span style="color:var(--accent)">Paid in Full</span>`;
    else if (pay.status === 'current') payStatus = `<span style="color:var(--accent)">Current ($${(pay.balance_remaining||0).toLocaleString()} remaining)</span>`;
    else if (pay.status === 'overdue') payStatus = `<span style="color:var(--danger);font-weight:700">Overdue ${pay.days_overdue}d ($${(pay.balance_remaining||0).toLocaleString()})</span>`;
    else payStatus = `<span style="color:var(--muted)">No plan on file</span>`;

    panel.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <div style="font-size:13px;font-weight:700;color:${scoreColor}">${scoreLabel}</div>
        <div style="font-size:22px;font-weight:800;color:${scoreColor}">${score}<span style="font-size:13px;font-weight:400;color:var(--muted)">/100</span></div>
      </div>
      <div style="background:var(--border);border-radius:4px;height:6px;margin-bottom:14px;overflow:hidden">
        <div style="background:${scoreColor};height:100%;width:${score}%;border-radius:4px;transition:width .4s ease"></div>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <tr style="border-bottom:1px solid var(--border)">
          <td style="padding:6px 0;color:var(--muted);width:40%">📍 Check-In</td>
          <td style="padding:6px 0">${ciStatus}</td>
          <td style="padding:6px 0;color:var(--muted);font-size:11px;text-align:right">Last: ${ciLast}</td>
        </tr>
        <tr style="border-bottom:1px solid var(--border)">
          <td style="padding:6px 0;color:var(--muted)">⚖️ Court Date</td>
          <td style="padding:6px 0">${courtStatus}</td>
          <td style="padding:6px 0;color:var(--muted);font-size:11px;text-align:right">${ct.court_date ? new Date(ct.court_date).toLocaleDateString('en-US',{month:'short',day:'numeric'}) : '—'}</td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:var(--muted)">💳 Payment</td>
          <td style="padding:6px 0" colspan="2">${payStatus}</td>
        </tr>
      </table>
      <div style="margin-top:8px;font-size:10px;color:var(--muted);text-align:right">Updated ${new Date(d.evaluated_at).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'})}</div>
    `;
  } catch (e) {
    panel.innerHTML = `<div style="color:var(--muted);font-size:12px;text-align:center;padding:8px">Compliance data unavailable</div>`;
  }
}


/* ══════════════════════════════════════════════════════════════════
   BOND RENEWAL / RE-WRITE MODAL
   Captira-style: update court date, reason, optional new POA/bond amount
   ══════════════════════════════════════════════════════════════════ */
window.openRenewBondModal = function(bookingNumber, defendantName) {
  const bn = bookingNumber || window._abEditBookingNumber || '';
  if (!bn) { toast('No booking number — open from a bond row', 'error'); return; }

  const bond = _abBonds.find(b => b.booking_number === bn);
  const name = defendantName || (bond && bond.defendant_name) || bn;

  // Remove any stale modal
  const old = document.getElementById('abRenewModal');
  if (old) old.remove();

  const modal = document.createElement('div');
  modal.id = 'abRenewModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center';
  modal.innerHTML = `
    <div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;width:480px;max-width:95vw;padding:28px;box-shadow:0 20px 60px rgba(0,0,0,.4)">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
        <h3 style="margin:0;font-size:17px">🔄 Renew / Re-Write Bond</h3>
        <button onclick="document.getElementById('abRenewModal').remove()" style="background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer">✕</button>
      </div>
      <div style="font-size:13px;color:var(--muted);margin-bottom:18px;padding:10px;background:var(--surface,var(--panel));border:1px solid var(--border);border-radius:6px">
        Defendant: <strong style="color:var(--text)">${escHtml(name)}</strong><br>
        Booking: <code style="font-size:12px">${escHtml(bn)}</code>
      </div>
      <div style="display:grid;gap:14px">
        <label style="display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--muted)">
          Renewal Reason *
          <select id="abRenewReason" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px">
            <option value="">— Select reason —</option>
            <option value="new_court_date">New Court Date Set</option>
            <option value="charge_amendment">Charge Amendment</option>
            <option value="bond_reduction">Bond Reduction</option>
            <option value="bond_increase">Bond Increase</option>
            <option value="poa_replacement">POA Replacement</option>
            <option value="continuance">Continuance</option>
            <option value="other">Other</option>
          </select>
        </label>
        <label style="display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--muted)">
          New Court Date *
          <input id="abRenewCourtDate" type="date" style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px"
            value="${bond && bond.court_date ? String(bond.court_date).substring(0,10) : ''}">
        </label>
        <label style="display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--muted)">
          New Court Location (optional)
          <input id="abRenewCourtLoc" type="text" placeholder="e.g. Lee County Courthouse Rm 4A"
            style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px"
            value="${bond && bond.court_location ? escHtml(bond.court_location) : ''}">
        </label>
        <label style="display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--muted)">
          New Bond Amount (optional — leave blank to keep current)
          <input id="abRenewBondAmount" type="number" min="0" step="500" placeholder="${bond ? (bond.bond_amount || '') : ''}"
            style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px">
        </label>
        <label style="display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--muted)">
          New POA Number (optional)
          <input id="abRenewPOA" type="text" placeholder="e.g. PSC2 2644680"
            style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px">
        </label>
        <label style="display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--muted)">
          Notes (optional)
          <textarea id="abRenewNotes" rows="2" placeholder="Internal notes about this renewal…"
            style="padding:8px;background:var(--input,var(--panel));border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;resize:vertical"></textarea>
        </label>
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:20px">
        <button onclick="document.getElementById('abRenewModal').remove()" style="padding:8px 16px;background:var(--panel);border:1px solid var(--border);border-radius:6px;color:var(--text);cursor:pointer">Cancel</button>
        <button id="abRenewSubmitBtn" onclick="_submitRenewBond('${escHtml(bn)}')" style="padding:8px 20px;background:#f59e0b;border:none;border-radius:6px;color:#fff;cursor:pointer;font-weight:600">🔄 Confirm Renewal</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  // Close on backdrop click
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

async function _submitRenewBond(bookingNumber) {
  const reason    = document.getElementById('abRenewReason')?.value?.trim();
  const courtDate = document.getElementById('abRenewCourtDate')?.value?.trim();
  const courtLoc  = document.getElementById('abRenewCourtLoc')?.value?.trim();
  const bondAmt   = document.getElementById('abRenewBondAmount')?.value?.trim();
  const poa       = document.getElementById('abRenewPOA')?.value?.trim();
  const notes     = document.getElementById('abRenewNotes')?.value?.trim();

  if (!reason) { toast('Please select a renewal reason', 'error'); return; }
  if (!courtDate) { toast('New court date is required', 'error'); return; }

  const btn = document.getElementById('abRenewSubmitBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

  const payload = { renewal_reason: reason, new_court_date: courtDate };
  if (courtLoc) payload.court_location = courtLoc;
  if (bondAmt)  payload.bond_amount = parseFloat(bondAmt);
  if (poa)      payload.poa_number = poa;
  if (notes)    payload.renewal_notes = notes;

  try {
    const r = await fetch(`${API}/api/active-bonds/${encodeURIComponent(bookingNumber)}/renew`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (!r.ok || !d.success) throw new Error(d.error || `HTTP ${r.status}`);

    document.getElementById('abRenewModal')?.remove();
    toast(`✅ Bond renewed — ${d.reminders_scheduled} court reminder(s) scheduled via iMessage`, 'success', 5000);

    // Refresh the table so the new court date shows immediately
    await loadActiveBonds();
  } catch (e) {
    toast(`Renewal failed: ${e.message}`, 'error');
    if (btn) { btn.disabled = false; btn.textContent = '🔄 Confirm Renewal'; }
  }
}

/* ══════════════════════════════════════════════════════════════════
   RENEWAL HISTORY PANEL
   ══════════════════════════════════════════════════════════════════ */
async function _loadRenewalHistory(bookingNumber) {
  const panel = document.getElementById('abRenewalHistoryPanel');
  if (!panel) return;

  try {
    const res = await fetch(`/api/active-bonds/${encodeURIComponent(bookingNumber)}/renewal-history`);
    const data = await res.json();

    if (!data.success) {
      panel.innerHTML = '<div style="color:var(--muted);font-size:12px;text-align:center">No renewal history</div>';
      return;
    }

    const history = data.renewal_history || [];
    if (!history.length) {
      panel.innerHTML = '<div style="color:var(--muted);font-size:12px;text-align:center">No renewals on record</div>';
      return;
    }

    const rows = history.map((r, i) => {
      const date = r.renewed_at ? new Date(r.renewed_at).toLocaleDateString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
      }) : '—';
      const oldCourt = r.old_court_date ? new Date(r.old_court_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
      const newCourt = r.new_court_date ? new Date(r.new_court_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
      const reason = (r.renewal_reason || 'unknown').replace(/_/g, ' ');
      const agent = r.renewed_by || 'staff';
      const poa = r.new_poa_number ? `<span style="font-family:monospace;font-size:10px;color:var(--muted)">POA: ${r.new_poa_number}</span>` : '';
      const notes = r.renewal_notes ? `<div style="font-size:11px;color:var(--muted);margin-top:4px;font-style:italic">${r.renewal_notes}</div>` : '';

      return `
        <div style="padding:10px 12px;${i < history.length - 1 ? 'border-bottom:1px solid var(--border);' : ''}">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;flex-wrap:wrap">
            <div>
              <span style="font-size:12px;font-weight:600;text-transform:capitalize">${reason}</span>
              ${poa}
            </div>
            <span style="font-size:10px;color:var(--muted);white-space:nowrap">${date}</span>
          </div>
          <div style="font-size:11px;color:var(--muted);margin-top:4px">
            Court: <span style="text-decoration:line-through">${oldCourt}</span>
            → <span style="color:var(--accent);font-weight:600">${newCourt}</span>
            &nbsp;·&nbsp; By: ${agent}
          </div>
          ${notes}
        </div>`;
    }).join('');

    panel.innerHTML = rows;

  } catch (e) {
    panel.innerHTML = `<div style="color:var(--muted);font-size:12px;text-align:center">Could not load renewal history</div>`;
  }
}

function openBondFromActiveBond(bond) {
  if (window.SLRecordBond && typeof window.SLRecordBond.open === 'function') {
    // Map fields for Record Bond modal if needed
    const data = { ...bond };
    if (!data.defendant_name && data.Defendant_Name) data.defendant_name = data.Defendant_Name;
    if (!data.booking_number && data.Booking_Number) data.booking_number = data.Booking_Number;
    window.SLRecordBond.open(data);
  } else {
    toast('Record Bond module not loaded', 'error');
  }
}

window.fileBondToDrive = async function(bookingNumber) {
  toast('Fetching document from SignNow and uploading to Drive...', 'info');
  try {
    const r = await fetch(`${API}/api/file-to-drive/${encodeURIComponent(bookingNumber)}`, {
      method: 'POST'
    });
    const result = await r.json();
    if (result.status === 'success') {
      toast('Bond filed to Drive successfully', 'success');
      window.open(result.drive_link, '_blank');
    } else {
      toast(result.error || 'Failed to file to drive', 'error');
    }
  } catch(e) {
    toast('Network error while filing to drive', 'error');
  }
}
