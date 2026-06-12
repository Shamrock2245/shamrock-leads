/* ═══════════════════════════════════════════════════════
   ShamrockLeads — Defendant Profiles Tab
   Full booking sheet detail + Write Bond export
   ═══════════════════════════════════════════════════════ */

let defPage=1, defSearchTimeout, selectedDefendant=null;

function debounceDefSearch(){clearTimeout(defSearchTimeout);defSearchTimeout=setTimeout(()=>loadDefendants(1),300)}

// ── Parse charges string into structured rows ──
// Handles:
//   1. Embedded dollar amounts in charge text: "CHARGE DESC - $5,000" or "CHARGE DESC $5000"
//   2. Pipe / semicolon / newline delimiters
//   3. Falls back to even distribution when no individual amounts found
function parseCharges(chargesStr, bondAmount, bondType, caseNumber){
  if(!chargesStr) return [{charge:'No charges listed',bond:'—',type:'—',case:'—'}];

  // Try pipe-delimited first, then semicolons, then newlines
  let parts=chargesStr.split(/\s*\|\s*/);
  if(parts.length<=1) parts=chargesStr.split(/\s*;\s*/);
  if(parts.length<=1 && chargesStr.length>80) parts=chargesStr.split(/\n/);
  parts=parts.filter(p=>p.trim());

  if(parts.length===1){
    // Single charge — try to extract embedded amount
    const embedded=_extractAmount(parts[0]);
    const displayBond=embedded>0?embedded:(bondAmount||0);
    return [{charge:_stripAmount(parts[0]).trim(),bond:money(displayBond),type:val(bondType),case:val(caseNumber)}];
  }

  // Multi-charge: try to extract individual amounts from each charge string
  const rows=parts.map(p=>({
    raw:p.trim(),
    amount:_extractAmount(p),
    clean:_stripAmount(p).trim(),
  }));

  const embeddedTotal=rows.reduce((s,r)=>s+r.amount,0);
  const hasEmbedded=rows.some(r=>r.amount>0);

  if(hasEmbedded){
    // Use embedded amounts; for charges without an amount, show '—'
    // Warn if embedded total differs significantly from bondAmount
    return rows.map(r=>({
      charge:r.clean,
      bond:r.amount>0?money(r.amount):'—',
      type:val(bondType),
      case:val(caseNumber),
    }));
  }

  // No embedded amounts — distribute bondAmount evenly
  const perCharge=bondAmount>0?bondAmount/rows.length:0;
  return rows.map(r=>({
    charge:r.clean,
    bond:money(perCharge),
    type:val(bondType),
    case:val(caseNumber),
  }));
}

// Extract a dollar amount embedded in a charge string
// Matches patterns like: "- $5,000", "$5000", "BOND: $10,000", "10000.00"
function _extractAmount(str){
  // Pattern: optional dash/colon, optional $, digits with optional commas/dots
  const m=str.match(/(?:[-:]\s*)?\$([\d,]+(?:\.\d{1,2})?)/);
  if(m) return parseFloat(m[1].replace(/,/g,''))||0;
  // Pattern: bare number at end of string like "5000" or "5,000"
  const m2=str.match(/\b([\d]{3,}(?:,\d{3})*(?:\.\d{1,2})?)\s*$/);
  if(m2) return parseFloat(m2[1].replace(/,/g,''))||0;
  return 0;
}

// Strip the embedded dollar amount from a charge string for clean display
function _stripAmount(str){
  return str
    .replace(/\s*[-:]\s*\$[\d,]+(?:\.\d{1,2})?/g,'')
    .replace(/\s+\$[\d,]+(?:\.\d{1,2})?/g,'')
    .replace(/\s+[\d]{3,}(?:,\d{3})*(?:\.\d{1,2})?\s*$/g,'')
    .trim();
}

// ── Status badge helper ──
function statusBadge(status){
  if(!status) return '<span class="def-status-badge other">Unknown</span>';
  const s=status.toLowerCase();
  if(s.includes('custody')||s.includes('confined')||s.includes('held')||s.includes('active'))
    return `<span class="def-status-badge custody">● ${status}</span>`;
  if(s.includes('released')||s.includes('bonded')||s.includes('rts'))
    return `<span class="def-status-badge released">● ${status}</span>`;
  return `<span class="def-status-badge other">● ${status}</span>`;
}

// ── Render a single defendant card ──
function renderDefCard(d){
  const charges=parseCharges(d.charges, d.bond_amount, d.bond_type, d.case_number);
  const hasMugshot=d.mugshot_url&&d.mugshot_url.startsWith('http');
  const fullAddr=[d.address,d.city,d.state,d.zip].filter(v=>v&&v!=='—').join(', ');

  const isNoBond = d.bond_amount === 0 && (d.bond_type || '').toUpperCase() === 'NO BOND';
  const bondText = isNoBond ? 'No Bond' : money(d.bond_amount);
  const pillClass = isNoBond ? 'no-bond' : bondPill(d.bond_amount);

  return `
  <div class="def-card" data-booking="${d.booking_number||''}" onclick='openSplitView(this, ${JSON.stringify(d).replace(/'/g,"&#39;")})'>
    <!-- Header -->
    <div class="def-card-header">
      <div style="display:flex;gap:14px;align-items:center;flex:1;min-width:0">
        ${hasMugshot?`<img src="${d.mugshot_url}" class="mugshot" alt="" onerror="this.outerHTML='<div class=mugshot-placeholder>👤</div>'">`:'<div class="mugshot-placeholder">👤</div>'}
        <div style="min-width:0">
          <div class="def-name">${val(d.full_name)}</div>
          <div class="def-booking">#${val(d.booking_number)} · ${val(d.county)} County</div>
        </div>
      </div>
      <div class="def-bond-pill ${pillClass}">${bondText}</div>
    </div>

    <div class="def-body">
      <!-- Demographics -->
      <div class="def-section">
        <div class="def-section-title">👤 Demographics</div>
        <div class="def-row">
          <div class="def-field"><div class="def-label">Date of Birth</div><div class="def-value">${val(d.dob)}</div></div>
          <div class="def-field"><div class="def-label">Sex / Race</div><div class="def-value">${val(d.sex)} / ${val(d.race)}</div></div>
          <div class="def-field"><div class="def-label">Height</div><div class="def-value">${val(d.height)}</div></div>
          <div class="def-field"><div class="def-label">Weight</div><div class="def-value">${val(d.weight)}</div></div>
        </div>
        <div class="def-row wide" style="margin-top:4px">
          <div class="def-field"><div class="def-label">Address</div><div class="def-value">${fullAddr||'—'}</div></div>
        </div>
      </div>

      <!-- Booking Info -->
      <div class="def-section">
        <div class="def-section-title">🏛️ Booking Information</div>
        <div class="def-row">
          <div class="def-field"><div class="def-label">Arrest Date</div><div class="def-value">${val(d.arrest_date)}${d.arrest_time?' '+d.arrest_time:''}</div></div>
          <div class="def-field"><div class="def-label">Booking Date</div><div class="def-value">${val(d.booking_date)}${d.booking_time?' '+d.booking_time:''}</div></div>
          <div class="def-field"><div class="def-label">Facility</div><div class="def-value">${val(d.facility)}</div></div>
          <div class="def-field"><div class="def-label">Arresting Agency</div><div class="def-value">${val(d.agency)}</div></div>
          <div class="def-field"><div class="def-label">Status</div><div class="def-value">${statusBadge(d.status)}</div></div>
          <div class="def-field"><div class="def-label">Bond Paid</div><div class="def-value">${val(d.bond_paid)}</div></div>
          ${isNoBond ? `
          <div class="def-field" style="grid-column: 1 / -1; background: rgba(245, 158, 11, 0.08); padding: 8px 12px; border-radius: 8px; border: 1px solid rgba(245, 158, 11, 0.15); display: flex; flex-direction: row; align-items: center; gap: 8px; margin-top: 6px;">
            <span style="font-size: 14px; line-height: 1;">⏳</span>
            <span style="color: #fbbf24; font-size: 11px; font-weight: 600; line-height: 1.3;">No bond until morning first appearance. Rescan scheduled then.</span>
          </div>
          ` : ''}
        </div>
      </div>

      <!-- Charges -->
      <div class="def-section">
        <div class="def-section-title">⚖️ Charges & Bond Detail</div>
        <table class="def-charges-table">
          <thead><tr><th>Charge Description</th><th>Bond</th><th>Type</th><th>Case #</th></tr></thead>
          <tbody>${charges.map(c=>`<tr><td style="white-space:normal">${c.charge}</td><td style="font-weight:600">${c.bond}</td><td>${c.type}</td><td class="mono" style="font-size:11px">${c.case}</td></tr>`).join('')}</tbody>
        </table>
      </div>

      <!-- Court Info -->
      <div class="def-section">
        <div class="def-section-title">📅 Court Information</div>
        <div class="def-row">
          <div class="def-field"><div class="def-label">Court Date</div><div class="def-value">${val(d.court_date)}${d.court_time?' at '+d.court_time:''}</div></div>
          <div class="def-field"><div class="def-label">Court Type</div><div class="def-value">${val(d.court_type)}</div></div>
          <div class="def-field"><div class="def-label">Court Location</div><div class="def-value">${val(d.court_location)}</div></div>
          <div class="def-field"><div class="def-label">Case Number</div><div class="def-value mono">${val(d.case_number)}</div></div>
        </div>
      </div>
    </div>

    <!-- Footer Actions -->
    <div class="def-card-footer">
      ${d.detail_url?`<a href="${d.detail_url}" target="_blank" class="btn-detail" onclick="event.stopPropagation()">🔗 Source</a>`:''}
      <button class="btn-detail" onclick="event.stopPropagation(); if(window.openShamrockNotes) openShamrockNotes('${d.booking_number}')">📜 Timeline & Notes</button>
      <button class="btn-contact-indem" onclick='event.stopPropagation(); SLContact.openModal("${d.booking_number||""}",${JSON.stringify(d.full_name||"")},"${(d.county||"").replace(/"/g,"&quot;")}",${d.bond_amount||0},"${d.booking_number||""}")'>📞 Contact Indem</button>
      <button onclick='event.stopPropagation(); trackAsInProgress(${JSON.stringify(d).replace(/'/g,"&#39;")})' style="background:#1a4a2e;border:1px solid #16a34a;color:#86efac;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600">🟢 Track In Progress</button>
      <button class="btn-write-bond" onclick='event.stopPropagation(); openWriteBond(${JSON.stringify(d).replace(/'/g,"&#39;")})'> Write Bond</button>
      <button class="btn-record-bond" onclick='event.stopPropagation(); window.openRecordBondModal&&openRecordBondModal(${JSON.stringify(d).replace(/'/g,"&#39;")})'>☘️ Record Bond</button>
    </div>
  </div>`;
}

// ── Load Defendants ──
async function loadDefendants(page){
  if(page)defPage=page;
  const sortVal=$('defSort').value;
  const sortDir=sortVal==='full_name'?1:-1;
  let url=`/api/defendants?page=${defPage}&limit=12&sort=${sortVal}&dir=${sortDir}`;
  const county=$('defFilterCounty').value,search=$('defSearch').value,minBond=$('defMinBond').value;
  if(county)url+=`&county=${encodeURIComponent(county)}`;
  if(search)url+=`&search=${encodeURIComponent(search)}`;
  if(minBond)url+=`&min_bond=${minBond}`;

  const data=await fetchJSON(url);
  if(!data)return;
  $('defResultCount').textContent=`${data.total.toLocaleString()} defendants`;
  $('defendantGrid').innerHTML=data.defendants.length?data.defendants.map(renderDefCard).join(''):'<div class="loading" style="grid-column:1/-1">No defendants match your filters</div>';
  $('defPagination').innerHTML=`<button ${data.page<=1?'disabled':''} onclick="loadDefendants(${data.page-1})">← Prev</button><span>Page ${data.page} of ${data.pages}</span><button ${data.page>=data.pages?'disabled':''} onclick="loadDefendants(${data.page+1})">Next →</button>`;
}


/* ═══════════════════════════════════════════════════════
   WRITE BOND MODAL — Insurance Carrier + Premium Calc
   ═══════════════════════════════════════════════════════ */

let selectedInsurer = 'osi'; // default

function selectInsurer(choice) {
  selectedInsurer = choice;
  document.querySelectorAll('.insurer-pill').forEach(el => el.classList.remove('active'));
  const active = document.querySelector(`.insurer-pill[data-insurer="${choice}"]`);
  if (active) active.classList.add('active');
  // Update premium display
  updatePremiumDisplay();
}

function updatePremiumDisplay() {
  if (!selectedDefendant) return;
  const bond = selectedDefendant.bond_amount || 0;
  const premiumRate = 0.10; // 10% standard
  const premium = bond * premiumRate;
  const premiumEl = document.getElementById('premiumAmount');
  if (premiumEl) premiumEl.textContent = money(premium);
  const bondEl = document.getElementById('modalBondTotal');
  if (bondEl) bondEl.textContent = money(bond);
}

function openWriteBond(defendant) {
  selectedDefendant = defendant;
  selectedInsurer = 'osi'; // reset to default
  const charges = parseCharges(defendant.charges, defendant.bond_amount, defendant.bond_type, defendant.case_number);
  const fullAddr = [defendant.address, defendant.city, defendant.state, defendant.zip].filter(v => v && v !== '—').join(', ');
  const bond = defendant.bond_amount || 0;
  const premium = bond * 0.10;

  $('modalBody').innerHTML = `
    <!-- Defendant Summary -->
    <div class="wb-section">
      <div class="wb-section-label">DEFENDANT</div>
      <div class="wb-defendant-summary">
        <div class="wb-name">${val(defendant.full_name)}</div>
        <div class="wb-meta-grid">
          <div><span class="wb-meta-label">DOB</span> ${val(defendant.dob)}</div>
          <div><span class="wb-meta-label">Sex/Race</span> ${val(defendant.sex)}/${val(defendant.race)}</div>
          <div><span class="wb-meta-label">County</span> ${val(defendant.county)}</div>
          <div><span class="wb-meta-label">Booking #</span> ${val(defendant.booking_number)}</div>
          <div style="grid-column:1/-1"><span class="wb-meta-label">Address</span> ${fullAddr || '—'}</div>
        </div>
      </div>
    </div>

    <!-- Charges & Bond -->
    <div class="wb-section">
      <div class="wb-section-label">CHARGES & BOND</div>
      <div class="wb-charges-box">
        <table class="wb-charges-table">
          ${charges.map(c => `<tr><td>${c.charge}</td><td class="wb-charge-bond">${c.bond}</td></tr>`).join('')}
        </table>
        <div class="wb-bond-total-row">
          <span>Total Bond</span>
          <span class="wb-bond-total" id="modalBondTotal">${money(bond)}</span>
        </div>
      </div>
    </div>

    <!-- Insurance Company Selection -->
    <div class="wb-section">
      <div class="wb-section-label">INSURANCE COMPANY</div>
      <div class="insurer-selector">
        <button class="insurer-pill active" data-insurer="osi" onclick="selectInsurer('osi')">
          <div class="insurer-pill-icon">🏛️</div>
          <div class="insurer-pill-name">OSI</div>
          <div class="insurer-pill-full">Old Surety Insurance</div>
        </button>
        <button class="insurer-pill" data-insurer="palmetto" onclick="selectInsurer('palmetto')">
          <div class="insurer-pill-icon">🌴</div>
          <div class="insurer-pill-name">Palmetto</div>
          <div class="insurer-pill-full">Palmetto Surety Corp</div>
        </button>
      </div>
    </div>

    <!-- Premium Calculation -->
    <div class="wb-section">
      <div class="wb-section-label">PREMIUM CALCULATION</div>
      <div class="wb-premium-box">
        <div class="wb-premium-row">
          <span>Bond Amount</span>
          <span class="wb-premium-value">${money(bond)}</span>
        </div>
        <div class="wb-premium-row">
          <span>Rate</span>
          <span class="wb-premium-value">10%</span>
        </div>
        <div class="wb-premium-row total">
          <span>Premium Due</span>
          <span class="wb-premium-value accent" id="premiumAmount">${money(premium)}</span>
        </div>
      </div>
    </div>

    <!-- POA Notice -->
    <div class="wb-poa-notice">
      <span class="wb-poa-icon">📋</span>
      <div>
        <div class="wb-poa-title">Power of Attorney</div>
        <div class="wb-poa-text">POA number will be assigned manually after packet generation. Auto-assignment coming in a future update.</div>
      </div>
    </div>
  `;
  $('writeBondModal').classList.add('show');
}

function closeModal() {
  $('writeBondModal').classList.remove('show');
  selectedDefendant = null;
}

// ── Toast Notification System ──
function showToast(message, type = 'success', duration = 4000) {
  // Remove existing toasts
  document.querySelectorAll('.toast-notification').forEach(t => t.remove());

  const toast = document.createElement('div');
  toast.className = `toast-notification toast-${type}`;
  toast.innerHTML = `
    <span class="toast-icon">${type === 'success' ? '✅' : type === 'error' ? '❌' : '⏳'}</span>
    <span class="toast-message">${message}</span>
  `;
  document.body.appendChild(toast);

  // Trigger animation
  requestAnimationFrame(() => toast.classList.add('show'));

  if (duration > 0) {
    setTimeout(() => {
      toast.classList.remove('show');
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }
  return toast;
}

// ── Export to SignNow via /api/write-bond ──
async function exportToSignNow() {
  if (!selectedDefendant) return;

  const btn = $('btnExportSignNow');
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="btn-spinner"></span> Generating Packet...';

  const payload = {
    action: 'generate_packet',
    insurance_company: selectedInsurer,
    defendant: {
      full_name: selectedDefendant.full_name,
      first_name: selectedDefendant.first_name,
      last_name: selectedDefendant.last_name,
      middle_name: selectedDefendant.middle_name || '',
      dob: selectedDefendant.dob,
      address: selectedDefendant.address,
      city: selectedDefendant.city,
      state: selectedDefendant.state,
      zip: selectedDefendant.zip,
      sex: selectedDefendant.sex,
      race: selectedDefendant.race,
      height: selectedDefendant.height,
      weight: selectedDefendant.weight,
    },
    booking: {
      booking_number: selectedDefendant.booking_number,
      county: selectedDefendant.county,
      facility: selectedDefendant.facility,
      agency: selectedDefendant.agency,
      arrest_date: selectedDefendant.arrest_date,
      booking_date: selectedDefendant.booking_date,
    },
    bond: {
      amount: selectedDefendant.bond_amount,
      type: selectedDefendant.bond_type,
      paid: selectedDefendant.bond_paid,
      premium: (selectedDefendant.bond_amount || 0) * 0.10,
    },
    charges: selectedDefendant.charges,
    court: {
      date: selectedDefendant.court_date,
      time: selectedDefendant.court_time,
      type: selectedDefendant.court_type,
      location: selectedDefendant.court_location,
      case_number: selectedDefendant.case_number,
    }
  };

  try {
    const res = await fetch('/api/write-bond', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (data.success) {
      showToast(`Bond packet queued for ${val(selectedDefendant.full_name)} via ${selectedInsurer.toUpperCase()}`, 'success');
      closeModal();
    } else {
      showToast(`Error: ${data.error || 'Unknown error'}`, 'error');
    }
  } catch (err) {
    console.error('Write Bond error:', err);
    showToast(`Network error: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

// Close modal on overlay click
$('writeBondModal').addEventListener('click', e => { if (e.target === $('writeBondModal')) closeModal() });
// Close modal on Escape
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal() });

// ── Track defendant as In Progress (prospective bond pipeline) ────────────────
async function trackAsInProgress(defendant) {
  const stages = ['contacted', 'negotiating', 'paperwork', 'ready'];
  const stage = prompt(
    'Track "' + (defendant.full_name || 'Defendant') + '" in the In Progress pipeline.\n\n' +
    'Choose starting stage:\n' +
    '  contacted   — Initial contact made\n' +
    '  negotiating — Actively negotiating terms\n' +
    '  paperwork   — Paperwork in progress\n' +
    '  ready       — Ready to post\n\n' +
    'Enter stage name (default: contacted):',
    'contacted'
  );
  if (stage === null) return; // user cancelled
  const finalStage = stages.includes((stage || '').trim().toLowerCase())
    ? (stage || '').trim().toLowerCase()
    : 'contacted';

  try {
    const res = await fetch('/api/prospective-bonds', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        booking_number: defendant.booking_number || '',
        defendant_name: defendant.full_name || '',
        county: defendant.county || '',
        bond_amount: defendant.bond_amount || 0,
        charges: defendant.charges || '',
        lead_score: defendant.lead_score || 0,
        lead_status: defendant.lead_status || '',
        stage: finalStage,
        note: 'Tracked from Defendants tab',
        agent: 'Dashboard',
      }),
    });
    const data = await res.json();
    if (data.success) {
      showToast('🟢 Added to In Progress pipeline (' + finalStage + ')', 'success');
      if (typeof SLProspective !== 'undefined') SLProspective.load();
    } else if (res.status === 409) {
      showToast('Already in In Progress pipeline (stage: ' + (data.stage || 'unknown') + ')', 'warn');
    } else {
      showToast('Error: ' + (data.error || 'Unknown error'), 'error');
    }
  } catch (err) {
    showToast('Network error: ' + err.message, 'error');
  }
}


/* ═══════════════════════════════════════════════════════
   SPLIT VIEW & KEYBOARD NAVIGATION (Twenty UX)
   ═══════════════════════════════════════════════════════ */
let currentSplitIndex = -1;

function openSplitView(cardEl, d) {
  const wrapper = document.getElementById('defSplitWrapper');
  const detailPane = document.getElementById('defDetailPane');
  
  if (!wrapper || !detailPane) return;
  
  // Update active card
  document.querySelectorAll('.def-card.active-split').forEach(el => el.classList.remove('active-split'));
  if (cardEl) {
    cardEl.classList.add('active-split');
    // Find index for keyboard navigation
    const cards = Array.from(document.querySelectorAll('.def-card'));
    currentSplitIndex = cards.indexOf(cardEl);
  }
  
  wrapper.classList.add('split-active');
  
  // Render detail pane
  const charges = parseCharges(d.charges, d.bond_amount, d.bond_type, d.case_number);
  const hasMugshot = d.mugshot_url && d.mugshot_url.startsWith('http');
  const fullAddr = [d.address, d.city, d.state, d.zip].filter(v => v && v !== '—').join(', ');
  
  detailPane.innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom: 20px;">
      <div style="display:flex; gap:16px; align-items:center;">
        ${hasMugshot ? `<img src="${d.mugshot_url}" style="width:80px;height:80px;border-radius:12px;object-fit:cover;">` : `<div style="width:80px;height:80px;border-radius:12px;background:var(--panel-hover);display:flex;align-items:center;justify-content:center;font-size:32px;">👤</div>`}
        <div>
          <h2 style="margin:0; font-size:20px; font-weight:700;">${val(d.full_name)}</h2>
          <div style="color:var(--muted); font-size:13px; margin-top:4px;">#${val(d.booking_number)} · ${val(d.county)} County</div>
          <div style="margin-top:8px;">${statusBadge(d.status)}</div>
        </div>
      </div>
      <button onclick="closeSplitView()" class="sl-btn sl-btn-ghost" style="padding:4px 8px;">✕</button>
    </div>
    
    <div class="def-section" style="margin-bottom:20px">
      <div class="def-section-title">🏛️ Booking Info</div>
      <div style="display:grid; grid-template-columns: 1fr 1fr; gap:12px; font-size:13px;">
        <div><strong style="color:var(--muted);display:block;font-size:11px;">Arrest Date</strong>${val(d.arrest_date)} ${d.arrest_time||''}</div>
        <div><strong style="color:var(--muted);display:block;font-size:11px;">Agency</strong>${val(d.agency)}</div>
        <div style="grid-column: 1/-1"><strong style="color:var(--muted);display:block;font-size:11px;">Address</strong>${fullAddr || '—'}</div>
      </div>
    </div>
    
    <div class="def-section" style="margin-bottom:20px">
      <div class="def-section-title">⚖️ Charges</div>
      <table class="def-charges-table" style="width:100%">
        <thead><tr><th>Charge</th><th>Bond</th><th>Case</th></tr></thead>
        <tbody>
          ${charges.map(c => `<tr><td>${c.charge}</td><td style="font-weight:600">${c.bond}</td><td class="mono" style="font-size:11px">${c.case}</td></tr>`).join('')}
        </tbody>
      </table>
    </div>
    
    <div style="display:flex; gap:12px; margin-top:24px;">
      <button class="sl-btn sl-btn-primary" style="flex:1" onclick='openWriteBond(${JSON.stringify(d).replace(/'/g,"&#39;")})'>Write Bond</button>
      <button class="sl-btn sl-btn-secondary" style="flex:1" onclick="if(window.openShamrockNotes) openShamrockNotes('${d.booking_number}')">Notes</button>
    </div>
  `;
}

function closeSplitView() {
  const wrapper = document.getElementById('defSplitWrapper');
  if (wrapper) wrapper.classList.remove('split-active');
  document.querySelectorAll('.def-card.active-split').forEach(el => el.classList.remove('active-split'));
  currentSplitIndex = -1;
}

// Keyboard Navigation for Split View
document.addEventListener('keydown', (e) => {
  // Only trigger if we are on the defendants tab, split view is active, and no input is focused
  const wrapper = document.getElementById('defSplitWrapper');
  if (!wrapper || !wrapper.classList.contains('split-active')) return;
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
  
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const cards = Array.from(document.querySelectorAll('.def-card'));
    if (!cards.length) return;
    
    if (e.key === 'ArrowDown') {
      currentSplitIndex = Math.min(currentSplitIndex + 1, cards.length - 1);
    } else {
      currentSplitIndex = Math.max(currentSplitIndex - 1, 0);
    }
    
    const nextCard = cards[currentSplitIndex];
    if (nextCard) {
      nextCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      nextCard.click();
    }
  } else if (e.key === 'Escape') {
    closeSplitView();
  }
});

// ── Saved Views (Twenty UX) ──
function applySavedView(viewId, btnEl) {
  // Update UI active state
  if (btnEl) {
    document.querySelectorAll('#defSavedViews .view-tab').forEach(b => b.classList.remove('active'));
    btnEl.classList.add('active');
  }
  
  // Reset filters
  document.getElementById('defSearch').value = '';
  document.getElementById('defSort').value = 'arrest_date';
  document.getElementById('defCustody').value = '';
  document.querySelectorAll('#defBondRange button').forEach(b => b.classList.remove('active'));
  document.querySelector('#defBondRange button').classList.add('active'); // Default zsh+
  document.getElementById('defMinBond').value = '';
  
  // Apply specific view logic
  if (viewId === 'high_value') {
    document.getElementById('defSort').value = 'bond_amount';
    document.getElementById('defMinBond').value = '5000';
    // Highlight correct bond button
    document.querySelectorAll('#defBondRange button').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('#defBondRange button')[3].classList.add('active'); // Assuming index 3 is K+
  } else if (viewId === 'in_custody') {
    document.getElementById('defCustody').value = 'true';
  } else if (viewId === 'high_risk') {
    document.getElementById('defSort').value = 'lead_score';
  }
  
  // Reload
  if (window.SL && SL.loadDefendants) {
    SL.loadDefendants(1);
  } else if (typeof loadDefendants === 'function') {
    loadDefendants(1);
  }
}

window.applySavedView = applySavedView;
