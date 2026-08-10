/* ShamrockLeads — Defendants, Health, Bond Modal, Export, Init */

/** Shared safe-fetch: returns parsed JSON or null if response is invalid */
async function _safeFetch(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) return null;
  const ct = r.headers.get('content-type') || '';
  if (!ct.includes('application/json')) return null;
  return r.json();
}

// ── Defendants ──
async function loadDefendants() {
  const search = document.getElementById('defSearch')?.value || '';
  const sort = document.getElementById('defSort')?.value || SL_STATE.defSort || 'scraped_at';
  if (window.SL_STATE) SL_STATE.defSort = sort;
  const custody = document.getElementById('defCustody')?.value || '';
  // Multi-select: prefer state array; fall back to hidden field (comma-joined)
  const selected = (window.SL_STATE && Array.isArray(SL_STATE.defSelectedCounties))
    ? SL_STATE.defSelectedCounties
    : [];
  const county = selected.length
    ? selected.join(',')
    : (document.getElementById('defCountyFilter')?.value || '');
  const limit = parseInt(document.getElementById('defLimit')?.value || SL_STATE.defLimit);
  const minBond = SL_STATE.defBond || 0;
  const order = (sort === 'full_name' || sort === 'county') ? 'asc' : 'desc';

  const p = new URLSearchParams({ limit: limit, sort: sort, order: order, page: SL_STATE.defPage });
  if (search) p.set('search', search);
  if (custody) p.set('custody', custody);
  if (county) p.set('county', county);
  if (minBond) p.set('min_bond', minBond);
  const hasIndemnitor = document.getElementById('defHasIndemnitor')?.checked;
  if (hasIndemnitor) p.set('has_indemnitor', 'true');

  try {
    const d = await _safeFetch(`${API}/api/leads?${p}`);
    if (!d) { console.warn('[Defendants] fetch failed or non-JSON'); return; }
    const leads = d.leads || [];
    const total = d.total || 0;
    const pages = d.pages || 1;
    SL_STATE.defPage = d.page || 1;

    document.getElementById('defResultsMeta').textContent = `${total.toLocaleString()} defendants · Page ${SL_STATE.defPage}/${pages}`;

    // Store leads in a map for lookup by booking number
    window._leadMap = window._leadMap || {};
    leads.forEach(l => { if (l.booking_number) window._leadMap[l.booking_number] = l; });

    const grid = document.getElementById('defendantGrid');
    grid.innerHTML = leads.map(l => {
      const bond = l.bond_amount||0;
      const bc = bond>=10000?'high':bond>=2500?'mid':'low';
      const stVal = (l.status||'').trim();
      const stLower = stVal.toLowerCase();
      const stBadge = stLower.includes('custody')?'custody':stLower.includes('release')||stLower.includes('bonded')?'released':stLower.includes('not in')?'released':'other';
      const sc = (l.lead_status||'').toLowerCase();
      const scoreCls = sc==='hot'?'score-hot':sc==='warm'?'score-warm':'score-cold';
      const bkSafe = String(l.booking_number||'').replace(/'/g,"\\'");
      const bkEscD = String(l.booking_number||'').replace(/"/g,'&quot;');
      const custDrop = `<select class="def-status-badge ${stBadge}" style="cursor:pointer;border:1px solid var(--border);background:transparent;padding:2px 6px;font-size:11px;border-radius:6px" onchange="updateCustody('${bkEscD}',this.value,this)"><option value="" ${!stVal?'selected':''}>${stVal||'\u2014'}</option><option value="In Custody" ${'In Custody'===stVal?'selected':''}>In Custody</option><option value="Not In Custody" ${'Not In Custody'===stVal?'selected':''}>Not In Custody</option><option value="Released" ${'Released'===stVal?'selected':''}>Released</option><option value="Bonded Out" ${'Bonded Out'===stVal?'selected':''}>Bonded Out</option></select>`;
      const slotId = 'defIdSlots_' + String(l.booking_number || '').replace(/[^a-zA-Z0-9_-]/g, '_');
      const bondEditId = 'defBondAmt_' + String(l.booking_number || '').replace(/[^a-zA-Z0-9_-]/g, '_');
      const bondPillLabel = bond > 0 ? ('$' + bond.toLocaleString()) : '$0 — set bond';
      const isLeeDef = (l.county || '').toLowerCase().includes('lee');
      const isNoBondDef = bond === 0 || (l.bond_type || '').toUpperCase().includes('NO BOND') || (l.bond_type || '').toUpperCase().includes('HOLD');
      const leeBadgeDef = (isLeeDef && isNoBondDef)
        ? `<div style="margin-top:4px"><span style="background:rgba(239,68,68,0.2);color:#fca5a5;border:1px solid rgba(239,68,68,0.5);border-radius:4px;padding:2px 6px;font-size:10px;font-weight:700;display:inline-block;cursor:pointer" title="Lee County First Appearance: 10:00 AM Weekdays / 8:30 AM Weekends & Holidays (Re-check court & leeclerk.org)" onclick="event.stopPropagation();refreshDefendantFromSource('${bkEscD}',this)">⏰ 1st App Watch</span></div>`
        : '';
      const leeClerkBtn = isLeeDef
        ? `<button class="btn-detail" style="background:rgba(59,130,246,0.2);color:#93c5fd;border:1px solid rgba(59,130,246,0.4)" onclick="event.stopPropagation();window.open('https://matrix.leeclerk.org/Home/Search?query=${encodeURIComponent(l.case_number || l.booking_number || '')}','_blank')" title="Search Lee County Clerk of Court (leeclerk.org)">🏛️ LeeClerk</button>`
        : '';

      return `<div class="def-card" data-booking="${bkEscD}">
        <div class="def-card-header"><div><div class="def-name">${l.full_name||'Unknown'}</div><div class="def-booking">${l.booking_number||'\u2014'} ${leeBadgeDef}</div></div>
          <div class="def-bond-edit-wrap" onclick="event.stopPropagation()">
            <div class="def-bond-pill ${bc} ${bond<=0?'bond-zero':''}" title="Click amount to edit — scrapers often leave $0 until first appearance">${bondPillLabel}</div>
            <div class="def-bond-edit-row">
              <span class="def-bond-edit-prefix">$</span>
              <input type="number" class="def-bond-input" id="${bondEditId}" min="0" step="1" inputmode="decimal"
                value="${bond > 0 ? bond : ''}" placeholder="0"
                onclick="event.stopPropagation()"
                onkeydown="if(event.key==='Enter'){event.preventDefault();updateBondAmount('${bkEscD}',this.value,this);}">
              <button type="button" class="def-bond-save-btn" onclick="event.stopPropagation();updateBondAmount('${bkEscD}',document.getElementById('${bondEditId}').value,document.getElementById('${bondEditId}'))" title="Save bond amount">Save</button>
            </div>
          </div>
        </div>
        <div class="def-body">
          <div class="def-section"><div class="def-section-title">📋 Details</div><div class="def-row"><div class="def-field"><span class="def-label">County</span><span class="def-value">${l.county||'\u2014'}</span></div><div class="def-field"><span class="def-label">DOB</span><span class="def-value">${l.dob||'\u2014'}</span></div><div class="def-field"><span class="def-label">Status</span>${custDrop}</div><div class="def-field"><span class="def-label">Score</span><span class="score-pill ${scoreCls}" id="defScore_${bondEditId}">${l.lead_score||0} ${l.lead_status||''}</span></div><div class="def-field"><span class="def-label">FTA Risk</span>${_ftaBadgeDef(l)||'<span style="font-size:11px;color:var(--text-muted)">—</span>'}</div></div></div>
          <div class="def-section"><div class="def-section-title" style="display:flex;justify-content:space-between;align-items:center"><span>⚖️ Charges</span><button type="button" class="btn-detail" style="font-size:10px;padding:2px 8px;background:rgba(168,85,247,0.2);color:#c084fc;border:1px solid rgba(168,85,247,0.4);border-radius:4px" onclick="event.stopPropagation();openChargeBondsModal('${bkEscD}')">⚖️ Per-Charge Bonds</button></div><div class="def-row wide"><div class="def-value" style="font-size:12px;white-space:normal">${l.charges||'\u2014'}</div></div></div>
          <div class="def-section" onclick="event.stopPropagation()">
            <div class="def-section-title">🪪 Driver License / ID &amp; Selfie</div>
            <div class="id-photo-slots" id="${slotId}" data-booking="${bkEscD}">
              <div style="color:var(--muted);font-size:12px;padding:6px;grid-column:1/-1">Loading ID photos…</div>
            </div>
          </div>
        </div>
        <div class="def-card-footer">
          <button class="btn-detail" onclick="event.stopPropagation(); if('${(l.detail_url||'').replace(/'/g,"\\'")}') window.open('${(l.detail_url||'').replace(/'/g,"\\'")}'); else toast('No source booking URL on this record','error')">🔗 Source</button>
          <button class="btn-detail btn-refresh-source" id="btnRefresh_${bondEditId}"
            style="${isNoBondDef ? 'background:rgba(234,179,8,0.25);color:#fde047;border:1px solid rgba(234,179,8,0.5);font-weight:700' : ''}"
            onclick="event.stopPropagation(); refreshDefendantFromSource('${bkEscD}', this)"
            title="Re-fetch updated bond info from county booking sheet">⚡ Fetch Bond</button>
          ${leeClerkBtn}
          <button class="slc-notes-btn" onclick="openShamrockNotes('${bkEscD}')" title="Shamrock Notes">📝 Notes</button>
          <button class="btn-imessage-send" onclick="SLiMessage&&SLiMessage.openCompose('${bkEscD}','${(l.full_name||'').replace(/'/g,"\'")}')" title="Send iMessage">💬 iMsg</button>
          <button class="btn-contact-indem" onclick="SLContact.openModal('${bkSafe}','${(l.full_name||'').replace(/'/g,"\\\\'")}',' ${l.county||''}',${bond},'${String(l.booking_number||'')}')">📞 Contact</button>
          <button class="btn-track-lead" id="trackBtn_${bkEscD}" onclick="SLProspective.trackLead('${bkSafe}','${(l.full_name||'').replace(/'/g,"\\\\'")}','${l.county||''}',${bond},'${(l.charges||'').replace(/'/g,"\\\\'")}',${l.lead_score||0},'${l.lead_status||''}')">☘️ Track</button>
            <button class="btn-write-bond" onclick="openBondModal(window._leadMap['${bkSafe}'] || {full_name:'${(l.full_name||'').replace(/'/g,"\\'")}'}, ${bond}, '${l.county||''}', '${bkSafe}')">✍️ Bond</button>
          <button class="btn-lifecycle" onclick="SLLifecycle&&SLLifecycle.open('${bkSafe}',{defendantName:'${(l.full_name||'').replace(/'/g,"\\'")}'})" title="Full bond lifecycle timeline">☘️ Life</button>
          <button class="btn-detail" style="background:rgba(239,68,68,.2);color:#fca5a5;border:1px solid rgba(239,68,68,.45)"
            onclick="event.stopPropagation();SLAdminHygiene&&SLAdminHygiene.deleteFromCard('${bkEscD}','${(l.full_name||'').replace(/'/g,"\\'")}','${(l.county||'').replace(/'/g,"\\'")}','${(l.state||'').replace(/'/g,"\\'")}')"
            title="Superadmin: permanently delete this lead and related records">🗑️ Delete</button>
        </div>    </div>
      </div>`;
    }).join('') || '<div class="loading">No defendants found</div>';

    // Defendant pagination
    document.getElementById('defPagination').innerHTML = `<button ${SL_STATE.defPage<=1?'disabled':''} onclick="goDefPage(${SL_STATE.defPage-1})">← Prev</button><span>Page ${SL_STATE.defPage} of ${pages}</span><button ${SL_STATE.defPage>=pages?'disabled':''} onclick="goDefPage(${SL_STATE.defPage+1})">Next →</button>`;

    // Hydrate ID photo slots after paint
    leads.forEach(l => {
      if (l.booking_number && typeof window.loadDefendantIdPhotos === 'function') {
        window.loadDefendantIdPhotos(l.booking_number);
      } else if (l.booking_number) {
        _loadDefIdPhotosInline(l.booking_number);
      }
    });
  } catch(e) { console.error('loadDefendants error:', e); }
}

// Inline fallback if defendants.js helpers are not on the page
// Inline fallback if defendants.js helpers are not on the page
async function _loadDefIdPhotosInline(bookingNumber) {
  const safe = String(bookingNumber || '').replace(/[^a-zA-Z0-9_-]/g, '_');
  const el = document.getElementById('defIdSlots_' + safe);
  if (!el) return;
  try {
    const r = await fetch((window.API || '') + '/api/defendants/by_booking/' + encodeURIComponent(bookingNumber) + '/uploads');
    const d = await r.json();
    const uploads = (r.ok && d.success !== false) ? (d.uploads || []) : [];
    const lead = (window._leadMap || {})[bookingNumber] || {};
    const mugshotUrl = d.mugshot_url || lead.Mugshot_URL || lead.mugshot_url || lead.photo_url || lead.image_url || lead.image || lead.mugshot || '';
    el.innerHTML = _renderDefIdSlotsInline(bookingNumber, uploads, mugshotUrl);
  } catch (e) {
    el.innerHTML = '<div style="color:var(--muted);font-size:12px;grid-column:1/-1">Could not load ID photos</div>';
  }
}

function _renderDefIdSlotsInline(bookingNumber, uploads, mugshotUrl = '') {
  const slots = [
    { key: 'govt_id_front', label: 'ID / DL Front', icon: '🪪' },
    { key: 'govt_id_back', label: 'ID / DL Back', icon: '🔄' },
    { key: 'selfie', label: 'Selfie', icon: '🤳' },
  ];
  const imgExts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'heic'];
  const bkSafe = String(bookingNumber || '').replace(/'/g, "\\'");
  const api = window.API || '';
  return slots.map(s => {
    const matches = (uploads || []).filter(u => u.doc_type === s.key);
    const u = matches.sort((a, b) => String(b.uploaded_at || '').localeCompare(String(a.uploaded_at || '')))[0];
    const src = u ? (u.url || ('/uploads/' + encodeURIComponent(u.entity_key || ('def-' + bookingNumber)) + '/' + encodeURIComponent(u.saved_as || ''))) : '';
    const isImg = u && imgExts.includes((u.extension || '').toLowerCase());
    const inputId = 'defSlot_' + s.key + '_' + String(bookingNumber || '').replace(/[^a-zA-Z0-9_-]/g, '_');

    // Auto-populate current mugshot in Selfie slot if defendant has not uploaded a custom selfie yet
    const isMugshotFallback = (s.key === 'selfie') && !u && Boolean(mugshotUrl);
    const displaySrc = u && isImg ? src : (isMugshotFallback ? mugshotUrl : '');

    return `<div class="id-photo-slot ${u || isMugshotFallback ? 'has-file' : ''}" style="position:relative" onclick="event.stopPropagation()">
      <div class="id-photo-slot-label">${s.icon} ${s.label}</div>
      <div class="id-photo-slot-preview" style="position:relative;overflow:hidden">
        ${displaySrc ? `<img src="${displaySrc}" alt="${s.label}" loading="lazy" style="width:100%;height:100%;object-fit:cover;border-radius:4px">`
          : u ? `<div class="ind-upload-pdf-icon">📄</div>`
          : `<div class="id-photo-empty">Tap to upload</div>`}
        ${isMugshotFallback ? `<div style="position:absolute;bottom:0;left:0;right:0;background:rgba(15,23,42,0.85);color:#38bdf8;font-size:9px;font-weight:700;padding:2px 4px;text-align:center;border-top:1px solid rgba(56,189,248,0.4)">📸 MUGSHOT (AUTO)</div>` : ''}
      </div>
      <div class="id-photo-slot-actions">
        <label class="id-photo-upload-btn" for="${inputId}">${u ? 'Replace' : (isMugshotFallback ? 'Upload Selfie' : 'Upload')}</label>
        <input id="${inputId}" type="file" accept="image/*,.pdf,.heic" hidden
          onchange="_uploadDefIdSlotInline('${bkSafe}','${s.key}', this.files && this.files[0])">
        ${u ? `<button type="button" class="id-photo-del-btn" onclick="event.stopPropagation();_deleteDefIdUploadInline('${bkSafe}','${u.file_id}')">Delete</button>` : ''}
        ${displaySrc ? `<a href="${displaySrc}" target="_blank" class="id-photo-view-btn" onclick="event.stopPropagation()">View</a>` : ''}
      </div>
    </div>`;
  }).join('');
}

async function _uploadDefIdSlotInline(bookingNumber, docType, file) {
  if (!file || !bookingNumber) return;
  try {
    if (window.SL && SL.toast) SL.toast('⏳ Uploading…', 'info');
    const formData = new FormData();
    formData.append('file', file);
    formData.append('doc_type', docType);
    const r = await fetch((window.API || '') + '/api/defendants/by_booking/' + encodeURIComponent(bookingNumber) + '/uploads', {
      method: 'POST', body: formData,
    });
    const d = await r.json();
    if (!r.ok || d.success === false) {
      if (window.SL && SL.toast) SL.toast('❌ ' + (d.error || 'Upload failed'), 'error');
      return;
    }
    if (window.SL && SL.toast) SL.toast('✅ ' + (d.doc_type_label || docType) + ' uploaded', 'success');
    await _loadDefIdPhotosInline(bookingNumber);
  } catch (e) {
    if (window.SL && SL.toast) SL.toast('❌ ' + e.message, 'error');
  }
}

async function _deleteDefIdUploadInline(bookingNumber, fileId) {
  if (!bookingNumber || !fileId || !confirm('Delete this ID photo?')) return;
  try {
    const r = await fetch(
      (window.API || '') + '/api/defendants/by_booking/' + encodeURIComponent(bookingNumber) +
      '/uploads/' + encodeURIComponent(fileId),
      { method: 'DELETE' }
    );
    const d = await r.json();
    if (!r.ok || d.success === false) {
      if (window.SL && SL.toast) SL.toast('❌ ' + (d.error || 'Delete failed'), 'error');
      return;
    }
    if (window.SL && SL.toast) SL.toast('🗑️ Deleted', 'success');
    await _loadDefIdPhotosInline(bookingNumber);
  } catch (e) {
    if (window.SL && SL.toast) SL.toast('❌ ' + e.message, 'error');
  }
}
function _ftaBadgeDef(l) {
  const score = l.fta_risk_score;
  if (score == null) return '';
  const lvl = (l.fta_risk_level || (score >= 75 ? 'high' : score >= 45 ? 'medium' : 'low')).toLowerCase();
  const clr = lvl === 'high' ? '#ef4444' : lvl === 'medium' ? '#f59e0b' : '#22c55e';
  const ico = lvl === 'high' ? '🔴' : lvl === 'medium' ? '🟡' : '🟢';
  const conf = l.fta_risk_confidence != null ? ' ' + (l.fta_risk_confidence * 100).toFixed(0) + '%' : '';
  return `<span class="fta-badge" style="background:${clr}22;color:${clr};border:1px solid ${clr}44;border-radius:4px;padding:1px 6px;font-size:10px;font-weight:600;white-space:nowrap;cursor:help" title="FTA Risk: ${lvl} (${score}/100)${conf}">${ico} ${lvl.charAt(0).toUpperCase()+lvl.slice(1)} <span style="opacity:0.7;font-size:9px">${score}</span></span>`;
}
function goDefPage(p) { SL_STATE.defPage = p; loadDefendants(); document.getElementById('tabDefendants').scrollIntoView({behavior:'smooth'}); }

// ── Scraper Health ──
function renderHealth() {
  const sc = SL_STATE.scraperData?.scrapers || {};
  const entries = Object.entries(sc).sort((a,b)=>a[0].localeCompare(b[0]));
  const ok = entries.filter(([,d])=>d.status==='ok').length;
  document.getElementById('healthKpis').innerHTML = `
    <div class="stat-card"><div class="stat-label">Healthy</div><div class="stat-value">${ok}</div></div>
    <div class="stat-card"><div class="stat-label">Total Fleet</div><div class="stat-value">${entries.length}</div></div>
    <div class="stat-card"><div class="stat-label">Errors</div><div class="stat-value">${entries.length - ok}</div></div>`;
  document.getElementById('healthBody').innerHTML = entries.map(([c,d]) => {
    const cls = d.status==='ok'?'status-healthy':'status-offline';
    const lbl = d.status==='ok'?'Healthy':'Error';
    return `<tr class="health-row"><td><strong>${c}</strong></td><td><span class="status-badge ${cls}">${lbl}</span></td><td>${d.records||0}</td><td>${d.hot_leads||0}</td><td>${d.last_run?timeAgo(d.last_run):'—'}</td><td>${d.avg_time?d.avg_time+'s':'—'}</td></tr>`;
  }).join('');
}

// ── Write Bond Modal helpers ──
function _isPlaceholderCharge(text) {
  const s = String(text || '').trim().toLowerCase();
  return !s || s === 'unspecified charge' || s === 'no charge specified' || s === 'unknown' || s === 'n/a' || s === 'none';
}

function _isBookingAsCase(caseNum, booking) {
  const c = String(caseNum || '').trim();
  const b = String(booking || '').trim();
  if (!c) return true;
  if (!b) return false;
  if (c === b) return true;
  const cd = c.replace(/\D/g, '');
  const bd = b.replace(/\D/g, '');
  // Pure-digit "case" that matches booking = booking misused as case #
  return !!(cd && bd && cd === bd && !/[A-Za-z]/.test(c));
}

/** Pull court case # from lead (never use booking/arrest #). */
function _leadCaseNumber(lead, booking) {
  if (!lead) return '';
  const candidates = [
    lead.case_number, lead.Case_Number, lead.caseNumber,
    lead.appearance_bond_number,
  ];
  // Prefer first charge_details case when top-level empty
  const details = lead.charge_details || lead.Charge_Details || [];
  if (Array.isArray(details)) {
    for (const d of details) {
      if (d && typeof d === 'object' && d.case_number) candidates.push(d.case_number);
    }
  }
  for (const c of candidates) {
    const s = String(c || '').trim();
    if (s && !_isBookingAsCase(s, booking)) return s;
  }
  return '';
}

/** Court date + time from lead (supports combined "9/8/2026, 8:30:00 AM"). */
function _leadCourtDateTime(lead) {
  if (!lead) return { date: 'TBN', time: '' };
  let date = String(lead.court_date || lead.Court_Date || '').trim();
  let time = String(lead.court_time || lead.Court_Time || '').trim();
  // From charge_details hearing if top-level missing
  if (!date || date.toUpperCase() === 'TBN' || date.toUpperCase() === 'TBD') {
    const details = lead.charge_details || lead.Charge_Details || [];
    if (Array.isArray(details)) {
      for (const d of details) {
        if (d && d.court_date && String(d.court_date).toUpperCase() !== 'TBN') {
          date = String(d.court_date).trim();
          if (!time && d.court_time) time = String(d.court_time).trim();
          break;
        }
      }
    }
  }
  // Combined date+time in one field
  const m = date.match(/^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})[,\s]+(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)\s*$/);
  if (m) {
    date = m[1];
    time = time || m[2];
  }
  if (!date) date = 'TBN';
  return { date, time };
}

/** Build charge description list from lead.charges / charge_details / Charges. */
function _extractChargeListFromLead(lead) {
  if (!lead) return [];
  const details = lead.charge_details || lead.Charge_Details;
  if (Array.isArray(details) && details.length) {
    const fromDetails = details.map((d) => {
      if (typeof d === 'string') return d.trim();
      return String(d.charge || d.description || d.charge_desc || d.offenseDescription || '').trim();
    }).filter((c) => !_isPlaceholderCharge(c));
    if (fromDetails.length) return fromDetails;
  }
  const raw = lead.charges || lead.Charges || lead.charge || '';
  if (!raw) return [];
  if (Array.isArray(raw)) {
    return raw.map((c) => (typeof c === 'string' ? c : (c.charge || c.description || '')).trim())
      .filter((c) => !_isPlaceholderCharge(c));
  }
  const s = String(raw).trim();
  if (!s) return [];
  // Prefer pipe / newline / semicolon; avoid splitting statute commas
  let parts;
  if (s.includes('|')) parts = s.split('|');
  else if (s.includes('\n')) parts = s.split('\n');
  else if (s.includes(';')) parts = s.split(';');
  else parts = [s];
  return parts.map((c) => c.trim()).filter((c) => !_isPlaceholderCharge(c));
}

// ── Write Bond Modal ──
// openBondModal accepts a full lead object OR individual fields for backwards compat
function openBondModal(nameOrLead, bond, county, booking) {
  let lead = {};
  if (typeof nameOrLead === 'object' && nameOrLead !== null) {
    lead = { ...nameOrLead };
  } else {
    // Legacy call: openBondModal(name, bond, county, booking)
    // Prefer full lead from _leadMap when available so charges/case/court are present
    const bk = booking || '';
    const fromMap = (typeof window._leadMap === 'object' && window._leadMap && bk)
      ? window._leadMap[bk]
      : null;
    lead = fromMap
      ? { ...fromMap }
      : { full_name: nameOrLead, bond_amount: bond, county: county, booking_number: booking };
    if (!lead.full_name && nameOrLead) lead.full_name = nameOrLead;
    if (bond != null && lead.bond_amount == null) lead.bond_amount = bond;
    if (county && !lead.county) lead.county = county;
    if (booking && !lead.booking_number) lead.booking_number = booking;
  }

  const name = lead.full_name || 'Unknown';
  const bondAmt = parseFloat(lead.bond_amount || bond || 0);
  const cnty = lead.county || county || '';
  const bkNum = lead.booking_number || booking || '';
  const premium = Math.max(100, bondAmt * 0.1);
  const transferFee = (bondAmt > 25000 || ['Lee','Charlotte'].includes(cnty)) ? 0 : 125;

  // Parse charges into individual bonds (one per charge).
  // Prefer structured charge_details (Lee/API) over pipe-delimited charges string.
  const chargeList = _extractChargeListFromLead(lead);
  const chargesRaw = chargeList.join(' | ');

  document.getElementById('bondModal').classList.add('show');

  document.getElementById('bondModalBody').innerHTML = `
    <div class="wb-section">
      <div class="wb-section-label">Defendant Summary</div>
      <div class="wb-defendant-summary">
        <div class="wb-name">${name}</div>
        <div class="wb-meta-grid">
          <div><span class="wb-meta-label">County</span>${cnty}</div>
          <div><span class="wb-meta-label">Booking #</span>${bkNum}</div>
          <div style="grid-column:1/-1">
            <span class="wb-meta-label">Bond Amount (editable)</span>
            <div style="display:flex;align-items:center;gap:8px;margin-top:4px;flex-wrap:wrap">
              <span style="font-weight:700">$</span>
              <input type="number" id="wbBondAmountInput" min="0" step="1" inputmode="decimal"
                value="${bondAmt > 0 ? bondAmt : ''}" placeholder="Enter bond amount"
                style="width:160px;padding:8px 10px;border-radius:8px;border:1px solid ${bondAmt > 0 ? 'var(--border)' : 'rgba(245,158,11,0.6)'};background:var(--bg);color:var(--text);font-size:15px;font-weight:700"
                oninput="onWriteBondAmountChange(this.value)"
                onchange="onWriteBondAmountChange(this.value, true)">
              <button type="button" class="btn-export" style="font-size:11px;padding:6px 12px" onclick="saveWriteBondAmount()">💾 Save to Record</button>
              ${bkNum ? `<button type="button" class="btn-export" style="font-size:11px;padding:6px 12px;background:rgba(59,130,246,0.15);color:#93c5fd" onclick="refreshDefendantFromSource('${bkNum.replace(/'/g,"\\'")}', this)">🔄 Update from Source</button>` : ''}
              ${bondAmt <= 0 ? '<span style="font-size:11px;color:#fbbf24">⚠️ Scraper left $0 — set the real bond from the jail/court before billing</span>' : ''}
            </div>
          </div>
          <div><span class="wb-meta-label">Est. Premium (10%)</span><strong id="wbPremiumDisplay" style="color:var(--success)">$${premium.toLocaleString()}</strong></div>
          <div><span class="wb-meta-label">Transfer Fee</span><span id="wbTransferDisplay">${transferFee ? '$'+transferFee : '<span style="color:var(--success)">Waived</span>'}</span></div>
          <div><span class="wb-meta-label">Total Due</span><strong id="wbTotalDueDisplay">$${(premium + transferFee).toLocaleString()}</strong></div>
        </div>
      </div>
    </div>
    <div class="wb-section">
      <div class="wb-section-label">Select Surety Company</div>
      <div class="insurer-selector">
        <button class="insurer-pill active" id="suretyOSI" onclick="selectSurety('osi')">
          <span class="insurer-pill-icon">🛡️</span><span class="insurer-pill-name">OSI</span><span class="insurer-pill-full">O'Shaughnahill S&I</span>
        </button>
        <button class="insurer-pill" id="suretyPalmetto" onclick="selectSurety('palmetto')">
          <span class="insurer-pill-icon">🌴</span><span class="insurer-pill-name">Palmetto</span><span class="insurer-pill-full">Palmetto Surety Corp.</span>
        </button>
      </div>
    </div>
    <div class="wb-section">
      <div class="wb-section-label">Appearance Bonds — Print / Wet-Ink (one form per charge)</div>
      <div id="pdfPreviewArea" style="background:var(--panel);border-radius:8px;padding:16px">
        <p style="color:var(--muted);margin:0 0 12px;font-size:12px;line-height:1.45">
          <strong style="color:var(--text)">Not e-signed.</strong>
          Fills OSI/Palmetto appearance bond PDFs from this modal (POA, case #, amounts, TBN court date).
          Package is <strong>uncollated · 2 copies per charge</strong> (office file + jail).
          Assign POAs below first for best results.
        </p>
        <div id="chargeBondList" style="display:flex;flex-direction:column;gap:8px">
          ${chargeList.map((ch, i) => `
            <div class="charge-bond-row" style="display:flex;align-items:center;gap:10px;padding:8px;background:var(--bg);border-radius:6px">
              <span class="charge-bond-num" style="font-size:11px;color:var(--muted);min-width:20px">#${i+1}</span>
              <span class="charge-bond-desc" style="flex:1;font-size:12px">${ch}</span>
              <button class="btn-export" style="font-size:11px;padding:4px 10px;margin-right:4px" onclick="editBond('${encodeURIComponent(ch)}', ${i+1})">✏️ Edit</button>
              <button class="btn-export" style="font-size:11px;padding:4px 10px" onclick="downloadBond('${encodeURIComponent(ch)}', ${i+1})" title="Single charge · 2 copies">📄 1×</button>
            </div>`).join('')}
        </div>
        <div style="margin-top:14px;display:flex;flex-direction:column;gap:8px;align-items:stretch">
          <button type="button" id="btnPrintAppearancePackage" class="btn-export"
            style="font-size:14px;padding:12px 16px;font-weight:700;background:linear-gradient(135deg,rgba(16,185,129,0.25),rgba(59,130,246,0.2));color:#6ee7b7;border:1px solid rgba(16,185,129,0.45);border-radius:8px;cursor:pointer"
            onclick="printAppearanceBondPackage()"
            title="Merged uncollated PDF · 2 copies per charge · wet-ink ready">
            🖨️ Print Appearance Bonds — ${chargeList.length} charge${chargeList.length === 1 ? '' : 's'} × 2 copies
          </button>
          <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
            <button type="button" class="btn-export" style="font-size:11px;padding:4px 10px" onclick="printAppearanceBondPackage({dryRun:true})">📋 Preview plan</button>
            <button type="button" class="btn-export" style="font-size:11px;padding:4px 10px" onclick="downloadAllBonds(1)" title="1 copy per charge only">1× only</button>
          </div>
          <p id="printPackageStatus" style="margin:0;font-size:11px;color:var(--muted);text-align:center"></p>
        </div>
      </div>
    </div>
    <div class="wb-section" id="poaSection">
      <div class="wb-section-label">Power of Attorney (POA) Numbers</div>
      <div id="poaLoadingMsg" style="color:var(--muted);font-size:12px;padding:8px 0">⏳ Looking up available powers from inventory...</div>
      <div id="poaAssignmentArea" style="display:none">
        <div id="poaChargeList" style="display:flex;flex-direction:column;gap:8px"></div>
        <div style="margin-top:10px;padding:8px;background:var(--bg);border-radius:6px;font-size:11px;color:var(--muted)">
          <strong>Auto-assigned from your inventory.</strong> You can override any number by typing in the field.
          <span id="poaInventoryBadge" style="margin-left:8px"></span>
        </div>
      </div>
      <div id="poaErrorMsg" style="display:none;color:var(--danger);font-size:12px;padding:8px 0"></div>
    </div>
    <div id="bondSubmitStatus" style="display:none;margin-top:12px;padding:10px;border-radius:6px;text-align:center"></div>

    <div class="wb-section" id="docusealSection" style="border:1px solid rgba(34,197,94,0.25);border-radius:12px;padding:14px;background:linear-gradient(145deg,rgba(6,78,59,0.15),rgba(15,23,42,0.6))">
      <div class="wb-section-label" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
        ☘️ E-Sign Packet — DocuSeal
        <span id="sn-phase-badge" style="font-size:11px;padding:2px 8px;border-radius:10px;background:var(--panel);color:var(--muted)">Not Sent</span>
        <span id="sn-surety-badge" style="font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(34,197,94,0.18);color:#4ade80;margin-left:auto;font-weight:600">self-hosted · OSI/Palmetto</span>
      </div>
      <p style="margin:0 0 12px;font-size:12px;color:var(--muted);line-height:1.45">
        Prefills the combined bond packet from this bond (charges, POAs, premium, parties) and creates
        secure signing links on <strong style="color:#86efac">sign.shamrockbailbonds.biz</strong>.
        Appearance bonds stay print/wet-ink (button above). <em>SignNow is no longer used for new packets.</em>
      </p>

      <div style="font-size:12px;display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:12px;padding:10px;background:rgba(15,23,42,0.5);border-radius:8px;border:1px solid rgba(51,65,85,0.6)">
        <div style="color:#94a3b8">✓ Header · FAQs · Indemnity · App</div>
        <div style="color:#94a3b8">✓ Disclosure · Note · Waiver · SSA</div>
        <div style="color:#94a3b8">✓ Surety terms · Collateral · Payment plan</div>
        <div style="color:#64748b">🖨️ Appearance bonds = print package</div>
      </div>

      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
        <button type="button" class="btn-export" id="btnSendDocuSeal" onclick="triggerDocuSealPacket()"
          style="font-size:14px;padding:12px 18px;font-weight:700;background:linear-gradient(135deg,rgba(34,197,94,0.35),rgba(16,185,129,0.25));color:#bbf7d0;border:1px solid rgba(74,222,128,0.5);border-radius:10px;cursor:pointer;box-shadow:0 0 20px rgba(34,197,94,0.12)">
          🚀 Send DocuSeal Packet
        </button>
        <button type="button" class="btn-export" onclick="printAppearanceBondPackage()" style="font-size:12px;padding:10px 14px">
          🖨️ Print appearance bonds
        </button>
      </div>
      <div id="sn-status" style="margin-top:10px;font-size:12px;color:var(--muted);min-height:1.2em"></div>
    </div>

    <div class="wb-section" id="outreachSection">
      <div class="wb-section-label" style="display:flex;align-items:center;gap:8px">
        📱 Text Outreach <span id="bbStatusDot" class="outreach-status-dot offline"></span><span id="bbStatusText" style="font-size:10px;color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0">Checking...</span>
      </div>
      <div class="outreach-card">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">
          <div>
            <label class="outreach-label">Agent Name</label>
            <input type="text" id="outreachAgent" class="outreach-select" placeholder="Your name" value="Brendan" style="padding:8px 12px" />
          </div>
          <div>
            <label class="outreach-label">Send From</label>
            <select id="outreachFromNumber" class="outreach-select" onchange="checkBBStatus()">
              <option value="2399550178">📱 (239) 955-0178 · shamrockbailoffice</option>
              <option value="2399550314">📱 (239) 955-0314 · brendanoneal99</option>
            </select>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">
          <div>
            <label class="outreach-label">Recipient Phone</label>
            <div class="outreach-phone-wrap">
              <span class="outreach-phone-prefix">+1</span>
              <input type="tel" id="outreachPhone" class="outreach-phone" placeholder="(239) 555-0123" maxlength="14" oninput="formatPhoneInput(this)" />
            </div>
          </div>
          <div>
            <label class="outreach-label">Relationship</label>
            <select id="outreachRelation" class="outreach-select">
              <option value="Mother">Mother</option>
              <option value="Father">Father</option>
              <option value="Spouse">Spouse</option>
              <option value="Sibling">Sibling</option>
              <option value="Friend">Friend</option>
              <option value="Attorney">Attorney</option>
              <option value="Other">Other</option>
            </select>
          </div>
        </div>
        <div style="margin-bottom:10px">
          <label class="outreach-label">Template</label>
          <select id="outreachTemplate" class="outreach-select" onchange="applyOutreachTemplate()">
            <option value="standard">Standard Outreach</option>
            <option value="urgent">Urgent / High Bond</option>
            <option value="followup">Follow-Up</option>
            <option value="custom">Custom Message</option>
          </select>
        </div>
        <div style="margin-bottom:12px">
          <label class="outreach-label">Message</label>
          <textarea id="outreachMessage" class="outreach-textarea" rows="4"></textarea>
        </div>
        <div style="display:flex;align-items:center;gap:10px">
          <button class="outreach-send-btn" id="btnSendOutreach" onclick="sendOutreach()">📱 Send Text</button>
          <span id="outreachSendStatus" style="font-size:12px;color:var(--muted)"></span>
        </div>
        <div id="outreachHistory" style="margin-top:14px;display:none">
          <div class="outreach-label" style="margin-bottom:6px">📋 Sent Messages</div>
          <div id="outreachHistoryList"></div>
        </div>
      </div>
    </div>`;


  // Store full lead data for submit
  const leadCase = _leadCaseNumber(lead, bkNum);
  const leadCourt = _leadCourtDateTime(lead);
  // Enrich lead object so POA rows + collectors see correct case/court (not booking)
  lead.case_number = lead.case_number || lead.Case_Number || leadCase;
  lead.court_date = lead.court_date || lead.Court_Date || leadCourt.date;
  lead.court_time = lead.court_time || lead.Court_Time || leadCourt.time;
  lead.court_type = lead.court_type || lead.Court_Type || '';
  lead.charges = lead.charges || lead.Charges || chargesRaw;
  if (!lead.charge_details && !lead.Charge_Details && chargeList.length) {
    lead.charge_details = chargeList.map((ch) => ({
      charge: ch,
      case_number: leadCase,
      court_date: leadCourt.date,
      court_time: leadCourt.time,
    }));
  }

  window._bondModalData = {
    lead,
    name, bond: bondAmt, county: cnty, booking: bkNum,
    charges: chargesRaw, chargeList,
    case_number: leadCase,
    court_date: leadCourt.date,
    court_time: leadCourt.time,
    surety: 'osi',
    date: new Date().toLocaleDateString('en-US'),
    poaNumbers: [],  // will be populated by fetchPoaNumbers()
  };

  // Auto-fetch POA numbers for the default surety (osi) and charge count
  fetchPoaNumbers('osi', bondAmt, chargeList);

  // Check BlueBubbles status + load outreach template + history
  checkBBStatus();
  applyOutreachTemplate();
  loadOutreachHistory(bkNum);
}

// ── Power Capacity & Predictive Auto-Fill Helpers ──
// Real inventory prefixes from dashboard/extensions.py POA_SEED_TIERS
const POA_CAPACITY_MAP = {
  OSI3: 3000, OSI6: 6000, OSI16: 16000, OSI51: 51000, OSI101: 101000, OSI251: 251000,
  PSC2: 2000, PSC5: 5000, PSC15: 15000, PSC25: 25000, PSC50: 50000, PSC75: 75000, PSC105: 105000,
};

function getPowerCapacity(poaStr) {
  if (!poaStr) return 0;
  const upper = String(poaStr).toUpperCase().trim();
  // Prefer space/hyphen separated form: "OSI51 20127651" or "OSI51-20127651"
  const token = (upper.split(/[\s\-_]+/)[0] || upper).replace(/[^A-Z0-9]/g, '');
  if (POA_CAPACITY_MAP[token]) return POA_CAPACITY_MAP[token];

  // Longest-prefix match so OSI51 wins over OSI5 / OSI
  const known = Object.keys(POA_CAPACITY_MAP).sort((a, b) => b.length - a.length);
  for (const pfx of known) {
    if (token.startsWith(pfx) || upper.startsWith(pfx)) return POA_CAPACITY_MAP[pfx];
  }

  // Fallback: OSI6 → 6000, PSC25 → 25000 (digits after letters, ×1000 when small)
  const m = token.match(/^(?:OSI|PSC|PAL)(\d+)$/);
  if (m) {
    const n = parseInt(m[1], 10);
    return n <= 300 ? n * 1000 : n;
  }
  return 0;
}

function recommendPowerTier(surety, chargeAmount) {
  const amt = Number(chargeAmount) || 0;
  const osi = [
    { cap: 3000, pfx: 'OSI3' }, { cap: 6000, pfx: 'OSI6' }, { cap: 16000, pfx: 'OSI16' },
    { cap: 51000, pfx: 'OSI51' }, { cap: 101000, pfx: 'OSI101' }, { cap: 251000, pfx: 'OSI251' },
  ];
  const psc = [
    { cap: 2000, pfx: 'PSC2' }, { cap: 5000, pfx: 'PSC5' }, { cap: 15000, pfx: 'PSC15' },
    { cap: 25000, pfx: 'PSC25' }, { cap: 50000, pfx: 'PSC50' }, { cap: 75000, pfx: 'PSC75' },
    { cap: 105000, pfx: 'PSC105' },
  ];
  const tiers = (surety === 'palmetto') ? psc : osi;
  for (const t of tiers) {
    if (t.cap >= amt) return t.pfx;
  }
  return tiers[tiers.length - 1].pfx;
}

function predictNextPoaNumber(poaStr, incrementBy = 1) {
  if (!poaStr) return '';
  const trimmed = poaStr.trim();
  const match = trimmed.match(/^(.*?)(\d+)$/);
  if (!match) return trimmed;
  const prefix = match[1];
  const numStr = match[2];
  const nextNum = parseInt(numStr, 10) + incrementBy;
  const paddedNum = String(nextNum).padStart(numStr.length, '0');
  return prefix + paddedNum;
}

function fillDownCaseNumbers() {
  const firstInput = document.getElementById('caseNumInput_0');
  if (!firstInput) return;
  const val = firstInput.value.trim();
  const inputs = document.querySelectorAll('.charge-case-input');
  inputs.forEach(inp => {
    inp.value = val;
  });
  if (typeof toast === 'function') toast(`Case number "${val}" applied to all charges`, 'info');
}

function unlockPoaOverride(idx) {
  const input = document.getElementById(`poaInput_${idx}`);
  if (input) {
    delete input.dataset.locked;
    delete input.dataset.autoFilled;
    input.value = '';
    const prevIdx = idx > 0 ? idx - 1 : 0;
    const prevInput = document.getElementById(`poaInput_${prevIdx}`);
    if (prevInput) onPoaInputChange(prevInput, prevIdx);
    if (typeof toast === 'function') toast(`Charge #${idx + 1} POA unlocked — re-synced to sequence`, 'info');
  }
}

function autoFillConsecutivePoas(sourceIdx) {
  const data = window._bondModalData;
  if (!data || !data.chargeList) return;
  const sourceInput = document.getElementById(`poaInput_${sourceIdx}`);
  if (!sourceInput) return;

  const basePoa = sourceInput.value.trim();
  if (!basePoa) return;

  for (let i = sourceIdx + 1; i < data.chargeList.length; i++) {
    const targetInput = document.getElementById(`poaInput_${i}`);
    // Only auto-fill if the target input is NOT manually locked by a custom user override
    if (targetInput && targetInput.dataset.locked !== 'true') {
      const predicted = predictNextPoaNumber(basePoa, i - sourceIdx);
      targetInput.value = predicted;
      targetInput.dataset.autoFilled = 'true';
      // Update stored POA + capacity badge without re-entering autoFill (avoids O(n²) cascade)
      onPoaInputChange(targetInput, i, true, true);
    }
  }
}

// ── POA Auto-Population ──
async function fetchPoaNumbers(surety, bondAmt, chargeList) {
  const loadEl = document.getElementById('poaLoadingMsg');
  const areaEl = document.getElementById('poaAssignmentArea');
  const errEl  = document.getElementById('poaErrorMsg');
  const badgeEl = document.getElementById('poaInventoryBadge');
  const listEl = document.getElementById('poaChargeList');
  if (!loadEl) return;

  loadEl.style.display = 'block';
  if (areaEl) areaEl.style.display = 'none';
  if (errEl) errEl.style.display = 'none';

  try {
    const count = chargeList.length;
    const res = await fetch(`${API}/api/poa/next?surety=${surety}&bond_amount=${bondAmt}&count=${count}`);
    const data = await res.json();

    if (data.error) throw new Error(data.error);

    const suggested = data.suggested || [];
    const prefix = data.prefix || '';
    const availInTier = data.available_in_tier || 0;
    const availTotal = data.available_total || 0;
    const modal = window._bondModalData || {};
    const lead = modal.lead || {};
    // Case # is court case (e.g. 26CF016741) — NEVER booking/arrest number
    const defaultCaseNum = modal.case_number
      || _leadCaseNumber(lead, modal.booking)
      || '';
    const defaultCounty = modal.county || lead.county || 'Lee';
    const courtPair = _leadCourtDateTime(lead);
    const defaultCourtDate = modal.court_date || courtPair.date || 'TBN';
    const defaultCourtTime = modal.court_time || courtPair.time || '';
    const perChargeBond = count > 0 ? (bondAmt / count) : bondAmt;

    // Build per-charge POA & Case Number input rows
    const poaRows = chargeList.map((ch, i) => {
      const sug = suggested[i];
      const poaFull = sug ? sug.poa_full : '';
      const poaNum  = sug ? sug.poa_number : '';
      const poaPfx  = sug ? sug.poa_prefix : prefix;
      const fillDownBtn = (i === 0 && count > 1) ? `
        <button type="button" class="btn-export" style="font-size:10px;padding:2px 6px;margin-left:4px" onclick="fillDownCaseNumbers()" title="Copy Case #1 to all remaining charges">🔽 Fill All</button>
      ` : '';

      return `
        <div class="poa-charge-row" style="display:flex;flex-direction:column;gap:6px;padding:10px;background:var(--bg);border-radius:6px;border:1px solid var(--border)">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
            <span style="font-size:12px;font-weight:700;color:var(--text)">Charge #${i+1}: ${ch}</span>
            ${fillDownBtn}
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr 1fr 1fr;gap:8px;align-items:center">
            <div>
              <label style="font-size:10px;color:var(--muted);display:block;margin-bottom:2px">County (Appearance Bond)</label>
              <input type="text" id="countyInput_${i}" class="charge-county-input" value="${defaultCounty}" placeholder="e.g. Pinellas" style="padding:4px 6px;border-radius:4px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:11px;width:100%;box-sizing:border-box" />
            </div>
            <div>
              <label style="font-size:10px;color:var(--muted);display:block;margin-bottom:2px">Court Date</label>
              <input type="text" id="courtDateInput_${i}" class="charge-court-input" value="${(lead.charge_details && lead.charge_details[i] && lead.charge_details[i].court_date) ? lead.charge_details[i].court_date : defaultCourtDate}" placeholder="TBN or 9/8/2026" style="padding:4px 6px;border-radius:4px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:11px;width:100%;box-sizing:border-box" />
            </div>
            <div>
              <label style="font-size:10px;color:var(--muted);display:block;margin-bottom:2px">Court Time</label>
              <input type="text" id="courtTimeInput_${i}" class="charge-court-time-input" value="${(lead.charge_details && lead.charge_details[i] && lead.charge_details[i].court_time) ? lead.charge_details[i].court_time : defaultCourtTime}" placeholder="8:30:00 AM" style="padding:4px 6px;border-radius:4px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:11px;width:100%;box-sizing:border-box" />
            </div>
            <div>
              <label style="font-size:10px;color:var(--muted);display:block;margin-bottom:2px">Case # (court — not booking)</label>
              <input type="text" id="caseNumInput_${i}" class="charge-case-input" value="${(lead.charge_details && lead.charge_details[i] && lead.charge_details[i].case_number && !_isBookingAsCase(lead.charge_details[i].case_number, modal.booking)) ? lead.charge_details[i].case_number : defaultCaseNum}" placeholder="e.g. 26CF016741" style="padding:4px 6px;border-radius:4px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:11px;width:100%;box-sizing:border-box" />
            </div>
            <div>
              <label style="font-size:10px;color:var(--muted);display:block;margin-bottom:2px">Charge Bond ($)</label>
              <input type="number" id="chargeAmtInput_${i}" class="charge-amt-input" value="${perChargeBond > 0 ? perChargeBond : ''}" placeholder="Amount" style="padding:4px 6px;border-radius:4px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:12px;font-weight:600;width:100%;box-sizing:border-box" oninput="onPoaInputChange(document.getElementById('poaInput_${i}'), ${i})" />
            </div>
            <div>
              <label style="font-size:10px;color:var(--muted);display:block;margin-bottom:2px">POA Serial #</label>
              <input
                class="poa-input"
                id="poaInput_${i}"
                data-charge-idx="${i}"
                data-poa-prefix="${poaPfx}"
                data-poa-number="${poaNum}"
                value="${poaFull}"
                placeholder="${prefix} ______"
                style="padding:4px 6px;border-radius:4px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:11px;font-family:monospace;width:100%;font-weight:700;box-sizing:border-box"
                oninput="onPoaInputChange(this, ${i})"
              />
              <div id="poaCapBadge_${i}" style="margin-top:2px"></div>
            </div>
          </div>
        </div>`;
    });

    listEl.innerHTML = poaRows.join('');

    // Store in modal data
    window._bondModalData.poaNumbers = chargeList.map((_, i) => {
      const sug = suggested[i];
      return sug ? { poa_full: sug.poa_full, poa_number: sug.poa_number, poa_prefix: sug.poa_prefix } : { poa_full: '', poa_number: '', poa_prefix: prefix };
    });

    // Validate capacity on initial load
    chargeList.forEach((_, i) => {
      const inp = document.getElementById(`poaInput_${i}`);
      if (inp) onPoaInputChange(inp, i, true);
    });

    // Inventory badge
    const warnColor = availInTier <= 3 ? 'var(--danger)' : availInTier <= 10 ? 'var(--warning, #f59e0b)' : 'var(--success)';
    if (badgeEl) badgeEl.innerHTML = `<span style="color:${warnColor};font-weight:600">${availInTier} remaining in ${prefix} tier · ${availTotal} total ${surety.toUpperCase()}</span>`;
    if (data.warning) {
      if (badgeEl) badgeEl.innerHTML += ` <span style="color:var(--danger)">⚠️ ${data.warning}</span>`;
    }

    loadEl.style.display = 'none';
    if (areaEl) areaEl.style.display = 'block';

  } catch(e) {
    loadEl.style.display = 'none';
    if (errEl) {
      errEl.style.display = 'block';
      errEl.innerHTML = `⚠️ Could not load inventory: ${e.message}. <a href="#" onclick="fetchPoaNumbers('${surety}',${bondAmt},window._bondModalData.chargeList);return false">Retry</a> or enter POA numbers manually below.`;
    }
    if (listEl) {
      listEl.innerHTML = chargeList.map((ch, i) => `
        <div class="poa-charge-row" style="display:flex;align-items:center;gap:10px;padding:8px;background:var(--bg);border-radius:6px">
          <span style="font-size:11px;color:var(--muted);min-width:20px">#${i+1}</span>
          <span style="flex:1;font-size:11px">${ch.length>50?ch.slice(0,50)+'…':ch}</span>
          <input id="poaInput_${i}" class="poa-input" data-charge-idx="${i}" placeholder="Enter POA #" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:12px;font-family:monospace;width:140px" oninput="onPoaInputChange(this,${i})" />
        </div>`).join('');
    }
    if (areaEl) areaEl.style.display = 'block';
    window._bondModalData.poaNumbers = chargeList.map(() => ({ poa_full: '', poa_number: '', poa_prefix: '' }));
  }
}

function onPoaInputChange(input, idx, isAutoFill = false, skipCascade = false) {
  if (!input) return;
  const val = input.value.trim();

  // Lock field if user typed manually (preventing auto-overwrites from upstream edits)
  if (!isAutoFill && val) {
    input.dataset.locked = 'true';
  } else if (!val) {
    delete input.dataset.locked;
  }

  // Update stored poa number — prefix is the first token (OSI51), serial is the rest
  if (window._bondModalData && window._bondModalData.poaNumbers) {
    const parts = val.split(/[\s\-]+/).filter(Boolean);
    window._bondModalData.poaNumbers[idx] = {
      poa_full: val,
      poa_number: parts.length > 1 ? parts[parts.length - 1] : val,
      poa_prefix: parts.length > 1 ? parts[0] : (input.dataset.poaPrefix || parts[0] || ''),
    };
  }

  // Validate liability capacity for this charge
  const amtInput = document.getElementById(`chargeAmtInput_${idx}`);
  const chargeAmt = amtInput ? (parseFloat(amtInput.value) || 0) : (window._bondModalData ? window._bondModalData.bond : 0);
  const cap = getPowerCapacity(val);
  const badgeEl = document.getElementById(`poaCapBadge_${idx}`);
  const surety = window._bondModalData ? window._bondModalData.surety : 'osi';

  if (badgeEl) {
    let html = '';
    if (input.dataset.locked === 'true') {
      html += `<span onclick="unlockPoaOverride(${idx})" style="cursor:pointer;color:#93c5fd;font-size:10px;font-weight:700;margin-right:6px;background:rgba(59,130,246,0.2);padding:1px 5px;border-radius:4px" title="Manually overridden — click to unlock and re-sync to sequence">🔒 Custom (Unlock)</span>`;
    }
    if (val && cap > 0 && chargeAmt > cap) {
      const recPfx = recommendPowerTier(surety, chargeAmt);
      html += `<span style="color:#f87171;font-size:10px;font-weight:700">⚠️ $${chargeAmt.toLocaleString()} > $${cap.toLocaleString()} capacity · Recommend ${recPfx}</span>`;
    } else if (val && cap > 0) {
      html += `<span style="color:#4ade80;font-size:10px">✅ Covers up to $${cap.toLocaleString()}</span>`;
    } else if (val && !cap) {
      html += `<span style="color:#fbbf24;font-size:10px">⚠️ Unknown power tier</span>`;
    }
    badgeEl.innerHTML = html;
  }

  // Auto-increment consecutive POAs for subsequent unlocked inputs (source edits only)
  if (!skipCascade) {
    autoFillConsecutivePoas(idx);
  }
}

function editBond(chargeEncoded, idx) {
  const data = window._bondModalData;
  if (!data) return;
  const charge = decodeURIComponent(chargeEncoded);
  
  const newAddress = prompt('Edit Defendant Address (Leave as is if correct):', data.lead.address || '');
  if (newAddress === null) return;
  
  const newCourtDate = prompt('Edit Court Date & Time:', data.lead.court_date || '');
  if (newCourtDate === null) return;

  const newCaseNumber = prompt('Edit Case Number:', data.lead.case_number || '');
  if (newCaseNumber === null) return;
  
  const newCollateral = prompt('Edit Collateral Description:', 'Indemnity Agreement, Promissory Note');
  if (newCollateral === null) return;

  const surety = data.surety;
  const poaEntry = (data.poaNumbers && data.poaNumbers[idx - 1]) || {};
  const inputEl = document.getElementById(`poaInput_${idx - 1}`);
  const poaFull = (inputEl ? inputEl.value.trim() : '') || poaEntry.poa_full || '';
  
  const params = new URLSearchParams({
    name: data.name, booking: data.booking, county: data.county,
    bond: data.bond, charge, surety, date: data.date,
    dob: data.lead.dob || '', address: newAddress,
    court_date: newCourtDate, case_number: newCaseNumber,
    collateral: newCollateral,
    poa_number: poaFull,
  });
  window.open(`${API}/api/appearance-bond-pdf?${params}`, '_blank');
}

function downloadBond(chargeEncoded, idx) {
  const data = window._bondModalData;
  if (!data) return;
  const charge = decodeURIComponent(chargeEncoded);
  const surety = data.surety;
  const poaEntry = (data.poaNumbers && data.poaNumbers[idx - 1]) || {};
  const inputEl = document.getElementById(`poaInput_${idx - 1}`);
  const poaFull = (inputEl ? inputEl.value.trim() : '') || poaEntry.poa_full || '';
  const caseInp = document.getElementById(`caseNumInput_${idx - 1}`);
  let caseNum = (caseInp ? caseInp.value.trim() : '')
    || data.case_number
    || _leadCaseNumber(data.lead, data.booking)
    || '';
  if (_isBookingAsCase(caseNum, data.booking)) caseNum = '';
  const amtInp = document.getElementById(`chargeAmtInput_${idx - 1}`);
  const chargeAmt = amtInp ? (parseFloat(amtInp.value) || 0) : data.bond;
  const countyInp = document.getElementById(`countyInput_${idx - 1}`);
  const countyVal = (countyInp ? countyInp.value.trim() : '') || data.county || 'Lee';
  const courtDateInp = document.getElementById(`courtDateInput_${idx - 1}`);
  const courtTimeInp = document.getElementById(`courtTimeInput_${idx - 1}`);
  const courtPair = _leadCourtDateTime(data.lead);
  let courtDateVal = (courtDateInp ? courtDateInp.value.trim() : '')
    || data.court_date || courtPair.date || 'TBN';
  let courtTimeVal = (courtTimeInp ? courtTimeInp.value.trim() : '')
    || data.court_time || courtPair.time || '';

  const params = new URLSearchParams({
    name: data.name, booking: data.booking, county: countyVal,
    bond: chargeAmt, charge, surety, date: data.date,
    dob: data.lead.dob || '', address: data.lead.address || '',
    court_date: courtDateVal,
    court_time: courtTimeVal,
    case_number: caseNum,
    poa_number: poaFull,
    copies: '2',
    uncollated: 'true'
  });
  window.open(`${API}/api/appearance-bond-pdf?${params}`, '_blank');
}

/**
 * Build charge_details payload from Write Bond modal (POA, case, amounts, court).
 */
function _collectAppearanceBondChargesFromModal() {
  const data = window._bondModalData;
  if (!data || !data.chargeList) return [];
  const booking = data.booking || '';
  const fallbackCase = data.case_number || _leadCaseNumber(data.lead, booking) || '';
  const courtPair = _leadCourtDateTime(data.lead);
  return data.chargeList.map((ch, i) => {
    const poaInp = document.getElementById(`poaInput_${i}`);
    const caseInp = document.getElementById(`caseNumInput_${i}`);
    const amtInp = document.getElementById(`chargeAmtInput_${i}`);
    const countyInp = document.getElementById(`countyInput_${i}`);
    const courtDateInp = document.getElementById(`courtDateInput_${i}`);
    const courtTimeInp = document.getElementById(`courtTimeInput_${i}`);
    let desc = typeof ch === 'string' ? ch : (ch.charge || ch.description || '');
    if (_isPlaceholderCharge(desc)) desc = '';
    let caseNum = (caseInp ? caseInp.value.trim() : '') || fallbackCase;
    if (_isBookingAsCase(caseNum, booking)) caseNum = fallbackCase;
    if (_isBookingAsCase(caseNum, booking)) caseNum = '';
    let courtDate = (courtDateInp ? courtDateInp.value.trim() : '')
      || data.court_date || courtPair.date || 'TBN';
    let courtTime = (courtTimeInp ? courtTimeInp.value.trim() : '')
      || data.court_time || courtPair.time || '';
    // Split combined "9/8/2026, 8:30:00 AM" if agent typed it into date field
    const m = courtDate.match(/^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})[,\s]+(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)\s*$/);
    if (m) {
      courtDate = m[1];
      courtTime = courtTime || m[2];
    }
    return {
      charge: desc,
      poa_number: (poaInp ? poaInp.value.trim() : '')
        || (data.poaNumbers && data.poaNumbers[i] ? (data.poaNumbers[i].poa_full || data.poaNumbers[i].poa_number) : '')
        || '',
      case_number: caseNum,
      bond_amount: amtInp ? (parseFloat(amtInp.value) || 0) : (data.bond || 0),
      county: (countyInp ? countyInp.value.trim() : '') || data.county || 'Lee',
      court_date: courtDate,
      court_time: courtTime,
      bond_type: 'Surety',
    };
  });
}

/**
 * Primary CTA: merged uncollated print package (N charges × 2 copies).
 * Wet-ink only — never DocuSeal / SignNow.
 *
 * @param {{ dryRun?: boolean, copies?: number }} opts
 */
async function printAppearanceBondPackage(opts = {}) {
  const data = window._bondModalData;
  if (!data || !data.chargeList || !data.chargeList.length) {
    if (typeof toast === 'function') toast('No charges to print', 'error');
    return;
  }
  const dryRun = !!opts.dryRun;
  const copies = opts.copies != null ? opts.copies : 2;
  const statusEl = document.getElementById('printPackageStatus');
  const charges = _collectAppearanceBondChargesFromModal();
  const missingPoa = charges.filter(c => !c.poa_number).length;
  const missingCase = charges.filter(c => !c.case_number).length;

  const courtPair = _leadCourtDateTime(data.lead);
  const payload = {
    name: data.name,
    defendant_name: data.name,
    booking: data.booking,
    booking_number: data.booking,
    county: data.county,
    surety: data.surety || 'osi',
    date: data.date || new Date().toLocaleDateString('en-US'),
    dob: (data.lead && (data.lead.dob || data.lead.date_of_birth)) || '',
    address: (data.lead && (data.lead.address || data.lead.home_address || data.lead.Address)) || '',
    court_date: data.court_date || (data.lead && data.lead.court_date) || courtPair.date || 'TBN',
    court_time: data.court_time || (data.lead && data.lead.court_time) || courtPair.time || '',
    court_type: (data.lead && (data.lead.court_type || data.lead.Court_Type)) || '',
    case_number: data.case_number || _leadCaseNumber(data.lead, data.booking) || '',
    indemnitor_name: (data.indemnitors && data.indemnitors[0] && data.indemnitors[0].name) || data.indemnitor_name || '',
    bond_amount: data.bond,
    charge_details: charges,
    copies,
    dry_run: dryRun,
  };

  if (statusEl) {
    statusEl.textContent = dryRun
      ? 'Building plan…'
      : `Generating ${charges.length} charge(s) × ${copies} copies (uncollated)…`;
    statusEl.style.color = 'var(--muted)';
  }
  if (typeof toast === 'function') {
    toast(
      dryRun
        ? `Preview: ${charges.length} appearance bond(s)…`
        : `🖨️ Building print package (${charges.length}×${copies})…`,
      'info'
    );
  }

  try {
    const res = await fetch(`${API}/api/appearance-bonds/print-package`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (dryRun) {
      const plan = await res.json();
      if (!res.ok || !plan.success) throw new Error(plan.error || `HTTP ${res.status}`);
      const warn = [];
      if (plan.warnings?.missing_poa_indices?.length) {
        warn.push(`Missing POA on charge(s): ${plan.warnings.missing_poa_indices.map(i => i + 1).join(', ')}`);
      }
      if (plan.warnings?.missing_case_indices?.length) {
        warn.push(`Missing case # on charge(s): ${plan.warnings.missing_case_indices.map(i => i + 1).join(', ')}`);
      }
      const lines = (plan.plan || []).map(p =>
        `#${(p.charge_index || 0) + 1} ${p.charge || '?'} · $${Number(p.bond_amount || 0).toLocaleString()} · case ${p.case_number || '—'} · POA ${p.poa_number || 'MISSING'}`
      ).join('\n');
      alert(
        `Appearance bond plan (${plan.surety || 'osi'})\n` +
        `${plan.message || ''}\n` +
        `Ready: ${plan.ready ? 'YES' : 'NO — fill POA/case first'}\n\n` +
        lines +
        (warn.length ? `\n\n⚠️ ${warn.join('\n')}` : '')
      );
      if (statusEl) {
        statusEl.textContent = plan.ready
          ? `Plan OK · ${plan.page_estimate} pages · wet-ink print`
          : `Plan has gaps · ${warn.join(' · ') || 'check POA/case'}`;
        statusEl.style.color = plan.ready ? '#6ee7b7' : '#fbbf24';
      }
      return;
    }

    if (!res.ok) {
      let errMsg = `Print package failed (${res.status})`;
      try {
        const errJ = await res.json();
        errMsg = errJ.error || errMsg;
      } catch (_) { /* blob error body */ }
      throw new Error(errMsg);
    }

    const driveUrl = res.headers.get('x-drive-url') || '';
    const chargeCount = res.headers.get('x-charge-count') || String(charges.length);
    const missPoa = res.headers.get('x-missing-poa') || '';
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const safeName = (data.name || 'defendant').replace(/[^A-Za-z0-9_-]/g, '_');
    const a = document.createElement('a');
    a.href = url;
    a.download = `AppearanceBonds_${(data.surety || 'osi').toUpperCase()}_${safeName}_${chargeCount}ch_x${copies}_UNSIGNED_PRINT.pdf`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    // Open in new tab for quick print (popup may be blocked)
    try { window.open(url, '_blank'); } catch (_) { /* ignore */ }
    setTimeout(() => URL.revokeObjectURL(url), 60_000);

    let msg = `🖨️ Print package ready · ${chargeCount} form(s) × ${copies} copies (file + jail) · unsigned / wet-ink`;
    if (missPoa) msg += ` · ⚠️ missing POA on charge index ${missPoa}`;
    if (missingPoa) msg += ` · ${missingPoa} charge(s) had empty POA in form`;
    if (driveUrl) msg += ` · Drive filed`;
    if (typeof toast === 'function') toast(msg, missPoa || missingPoa ? 'warning' : 'success');
    if (statusEl) {
      statusEl.innerHTML = msg + (driveUrl
        ? ` · <a href="${driveUrl}" target="_blank" style="color:#c084fc">Drive</a>`
        : '');
      statusEl.style.color = '#6ee7b7';
    }
  } catch (err) {
    if (typeof toast === 'function') toast(`Error: ${err.message}`, 'error');
    if (statusEl) {
      statusEl.textContent = `Error: ${err.message}`;
      statusEl.style.color = '#f87171';
    }
  }
}

/** @deprecated use printAppearanceBondPackage — kept for older onclick handlers */
async function downloadAllBonds(copiesPerCharge = 2) {
  return printAppearanceBondPackage({ copies: copiesPerCharge });
}

function selectSurety(s) {
  window._bondModalData.surety = s;
  document.getElementById('suretyOSI').classList.toggle('active', s === 'osi');
  document.getElementById('suretyPalmetto').classList.toggle('active', s === 'palmetto');
  // DocuSeal surety badge (template set for selected surety)
  const snBadge = document.getElementById('sn-surety-badge');
  if (snBadge) {
    if (s === 'palmetto') {
      snBadge.textContent = '🌴 Palmetto · DocuSeal';
      snBadge.style.background = 'rgba(34,197,94,0.18)';
      snBadge.style.color = '#4ade80';
    } else {
      snBadge.textContent = '🛡️ OSI · DocuSeal';
      snBadge.style.background = 'rgba(34,197,94,0.18)';
      snBadge.style.color = '#4ade80';
    }
  }
  // Re-fetch POA numbers for the newly selected surety
  const data = window._bondModalData;
  if (data) fetchPoaNumbers(s, data.bond, data.chargeList);
}

function closeModal() { document.getElementById('bondModal').classList.remove('show'); }

// ── BlueBubbles Outreach ──
async function checkBBStatus() {
  const dot = document.getElementById('bbStatusDot');
  const txt = document.getElementById('bbStatusText');
  if (!dot) return;
  try {
    const r = await fetch(`${API}/api/imessage/status`);
    const d = await r.json();
    if (d.connected) {
      // Check how many servers are connected
      const onlineCount = (d.servers || []).filter(s => s.connected).length;
      const totalCount = d.server_count || 0;
      dot.className = 'outreach-status-dot online';
      let label = `${onlineCount}/${totalCount} servers`;
      if (d.private_api) label += ' · Private API';
      txt.textContent = label;
      txt.style.color = 'var(--accent)';

      // Highlight the selected server's status
      const selectedNum = document.getElementById('outreachFromNumber')?.value || '';
      const selSrv = (d.servers || []).find(s => s.phone === selectedNum);
      if (selSrv && !selSrv.connected) {
        dot.className = 'outreach-status-dot offline';
        txt.textContent = `Selected line offline (${onlineCount}/${totalCount} up)`;
        txt.style.color = 'var(--muted)';
      }
    } else {
      dot.className = 'outreach-status-dot offline';
      txt.textContent = d.reason || 'Not connected';
      txt.style.color = 'var(--muted)';
    }
  } catch(e) {
    dot.className = 'outreach-status-dot offline';
    txt.textContent = 'Server unreachable';
    txt.style.color = 'var(--muted)';
  }
}

function formatPhoneInput(el) {
  let v = el.value.replace(/\D/g, '').slice(0, 10);
  if (v.length > 6) v = `(${v.slice(0,3)}) ${v.slice(3,6)}-${v.slice(6)}`;
  else if (v.length > 3) v = `(${v.slice(0,3)}) ${v.slice(3)}`;
  else if (v.length > 0) v = `(${v}`;
  el.value = v;
}

function applyOutreachTemplate() {
  const sel = document.getElementById('outreachTemplate');
  const area = document.getElementById('outreachMessage');
  const agentEl = document.getElementById('outreachAgent');
  const data = window._bondModalData;
  if (!sel || !area || !data) return;
  const agent = agentEl?.value?.trim() || 'Brendan';
  const templates = {
    standard: `Hi, this is ${agent}, with Shamrock Bail Bonds. I see that ${data.name} is currently in custody in the ${data.county} County Jail. We were wondering if you'd like some help bonding them out of jail.`,
    urgent: `Hi, this is ${agent} with Shamrock Bail Bonds. I see that ${data.name} is currently being held in ${data.county} County on a significant bond. We specialize in getting people home fast with flexible payment plans. Would you like some help?`,
    followup: `Hi, this is ${agent} with Shamrock Bail Bonds, just following up about ${data.name} in ${data.county} County. We're still available to help if you'd like to get them out. No obligation to chat.`,
    custom: '',
  };
  area.value = templates[sel.value] || '';
}

async function sendOutreach() {
  const data = window._bondModalData;
  const phoneEl = document.getElementById('outreachPhone');
  const msgEl = document.getElementById('outreachMessage');
  const relEl = document.getElementById('outreachRelation');
  const btn = document.getElementById('btnSendOutreach');
  const statusEl = document.getElementById('outreachSendStatus');
  if (!data || !phoneEl || !msgEl) return;

  const rawPhone = phoneEl.value.replace(/\D/g, '');
  const message = msgEl.value.trim();
  if (!rawPhone || rawPhone.length < 10) { toast('Enter a valid phone number', 'error'); return; }
  if (!message) { toast('Message cannot be empty', 'error'); return; }

  btn.disabled = true;
  btn.innerHTML = '<span class="btn-spinner"></span> Sending...';
  statusEl.textContent = '';

  try {
    const r = await fetch(`${API}/api/imessage/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        phone: rawPhone,
        message,
        booking_number: data.booking,
        defendant_name: data.name,
        county: data.county,
        recipient_label: relEl?.value || 'Unknown',
        agent_name: document.getElementById('outreachAgent')?.value?.trim() || 'Brendan',
        from_number: document.getElementById('outreachFromNumber')?.value || '2399550178',
      }),
    });
    const result = await r.json();
    if (result.success) {
      statusEl.innerHTML = '<span style="color:var(--accent)">\u2713 Sent successfully</span>';
      toast(`Text sent to ${relEl?.value || 'recipient'}`, 'success');
      phoneEl.value = '';
      loadOutreachHistory(data.booking);
    } else {
      statusEl.innerHTML = `<span style="color:var(--red)">\u26a0 ${result.error || 'Send failed'}</span>`;
      toast(result.error || 'Send failed', 'error');
    }
  } catch(e) {
    statusEl.innerHTML = `<span style="color:var(--red)">\u26a0 Network error</span>`;
    toast('Network error sending text', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '\ud83d\udcf1 Send Text';
  }
}

async function loadOutreachHistory(bookingNumber) {
  const container = document.getElementById('outreachHistory');
  const list = document.getElementById('outreachHistoryList');
  if (!container || !list || !bookingNumber) return;

  try {
    const r = await fetch(`${API}/api/imessage/history/${encodeURIComponent(bookingNumber)}`);
    const d = await r.json();
    if (d.count > 0) {
      container.style.display = 'block';
      list.innerHTML = d.messages.map(m => {
        const t = new Date(m.sent_at).toLocaleString();
        const icon = m.status === 'sent' ? '\u2713' : '\u2717';
        const color = m.status === 'sent' ? 'var(--accent)' : 'var(--red)';
        return `<div class="outreach-history-row">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
            <span style="font-size:12px;font-weight:600">${m.recipient_label} · ${m.recipient_phone}</span>
            <span style="font-size:10px;color:${color};font-weight:600">${icon} ${m.status}</span>
          </div>
          <div style="font-size:11px;color:var(--text-secondary);line-height:1.4">${m.message.slice(0,120)}${m.message.length > 120 ? '…' : ''}</div>
          <div style="font-size:10px;color:var(--muted);margin-top:3px">${t} · via ${m.agent_name || 'Unknown'}${m.from_number ? ' · ' + m.from_number.replace(/(\d{3})(\d{3})(\d{4})/, '($1) $2-$3') : ''}</div>
        </div>`;
      }).join('');
    } else {
      container.style.display = 'none';
    }
  } catch(e) {
    container.style.display = 'none';
  }
}

async function submitBond() {
  const data = window._bondModalData;
  if (!data) { toast('No bond data', 'error'); return; }

  const statusEl = document.getElementById('bondSubmitStatus');
  if (statusEl) { statusEl.style.display = 'block'; statusEl.style.background = 'var(--panel)'; statusEl.textContent = 'Writing bond...'; }

  // Prefer live value from the editable bond field (fixes $0 scraper gaps)
  const wbAmtEl = document.getElementById('wbBondAmountInput');
  if (wbAmtEl) {
    const liveAmt = _parseBondInput(wbAmtEl.value);
    if (Number.isFinite(liveAmt) && liveAmt >= 0) {
      data.bond = liveAmt;
      if (data.lead) data.lead.bond_amount = liveAmt;
    }
  }
  if (!data.bond || data.bond <= 0) {
    if (statusEl) {
      statusEl.style.background = 'rgba(245,158,11,0.15)';
      statusEl.style.color = '#fbbf24';
      statusEl.textContent = '⚠️ Bond amount is $0. Enter the real bond amount above and click Save to Record before writing.';
    }
    toast('Set a bond amount greater than $0 before writing the bond', 'error');
    if (wbAmtEl) wbAmtEl.focus();
    return;
  }

  // Persist override so the Defendants list / future opens stay correct
  if (data.booking) {
    await updateBondAmount(data.booking, data.bond, wbAmtEl);
  }

  const lead = data.lead;
  // Collect final POA values from inputs (user may have overridden)
  const finalPoaNumbers = (data.chargeList || []).map((_, i) => {
    const inputEl = document.getElementById(`poaInput_${i}`);
    const val = inputEl ? inputEl.value.trim() : '';
    const stored = (data.poaNumbers && data.poaNumbers[i]) || {};
    return {
      poa_full: val || stored.poa_full || '',
      poa_number: val ? (val.includes(' ') ? val.split(' ').pop() : val) : stored.poa_number || '',
      poa_prefix: val ? (val.includes(' ') ? val.split(' ')[0] : stored.poa_prefix) : stored.poa_prefix || '',
    };
  });
  data.poaNumbers = finalPoaNumbers;

  const payload = {
    insurance_company: data.surety,
    surety_id: data.surety,
    poa_numbers: finalPoaNumbers,
    defendant: {
      full_name: data.name,
      first_name: lead.first_name || '',
      last_name: lead.last_name || '',
      middle_name: lead.middle_name || '',
      dob: lead.dob || '',
      address: lead.address || '',
      sex: lead.sex || '',
      race: lead.race || '',
      height: lead.height || '',
      weight: lead.weight || '',
    },
    booking: {
      booking_number: data.booking,
      county: data.county,
      facility: lead.facility || '',
      arrest_date: lead.arrest_date || lead.booking_date || '',
      booking_date: lead.booking_date || '',
    },
    bond: {
      amount: data.bond,
      premium: Math.max(100, data.bond * 0.1),
      type: lead.bond_type || 'Surety',
      paid: 'NO',
    },
    charges: data.charges,
    charge_list: data.chargeList,
    court: {
      date: lead.court_date || '',
      location: lead.court_location || '',
      case_number: lead.case_number || '',
    },
    // Indemnitor(s) — from intake pre-population or manually entered in modal
    indemnitors: data.indemnitors || [],
    intake_id: data.intake_id || '',
    intake_source: data.intake_source || 'shamrock-leads-dashboard',
  };

  try {
    const r = await fetch(`${API}/api/write-bond`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await r.json();

    if (result.success) {
      // Mark each POA as assigned in inventory
      await assignPoaNumbers(finalPoaNumbers, data.surety, data.booking);
      // Register in Active Bonds tracking
      await registerActiveBond(data, result);

      if (statusEl) { statusEl.style.background = 'rgba(34,197,94,0.15)'; statusEl.style.color = 'var(--success)'; statusEl.textContent = `✅ Bond written for ${data.name} via ${data.surety.toUpperCase()}. Registered in Active Bonds.`; }
      toast(`Bond written for ${data.name}`, 'success');
      setTimeout(() => { closeModal(); if (typeof loadActiveBonds === 'function') loadActiveBonds(); }, 2000);
    } else {
      if (statusEl) { statusEl.style.background = 'rgba(239,68,68,0.15)'; statusEl.style.color = 'var(--danger)'; statusEl.textContent = `❌ ${result.error || 'Bond write failed'}`; }
      toast(result.error || 'Bond write failed', 'error');
    }
  } catch(e) {
    if (statusEl) { statusEl.style.background = 'rgba(239,68,68,0.15)'; statusEl.style.color = 'var(--danger)'; statusEl.textContent = `❌ Network error: ${e.message}`; }
    toast('Network error writing bond', 'error');
  }
}

async function assignPoaNumbers(poaNumbers, surety, bookingNumber) {
  // Fire-and-forget: mark each used POA as assigned in MongoDB inventory
  for (const poa of (poaNumbers || [])) {
    if (!poa.poa_number) continue;
    try {
      await fetch(`${API}/api/poa/assign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          poa_number: poa.poa_number,
          poa_prefix: poa.poa_prefix,
          surety_id: surety,
          booking_number: bookingNumber,
        }),
      });
    } catch(e) {
      console.warn('POA assign failed (non-fatal):', poa.poa_number, e);
    }
  }
}

async function registerActiveBond(data, bondResult) {
  try {
    const activeBondPayload = {
      defendant_name: data.name,
      booking_number: data.booking,
      poa_numbers: data.poaNumbers || [],
      county: data.county,
      bond_amount: data.bond,
      premium: Math.max(100, data.bond * 0.1),
      surety: data.surety,
      charges: data.chargeList,
      charges_raw: data.charges,
      bond_date: new Date().toISOString(),
      status: 'active',
      risk_score: 50,  // default; updated by risk engine
      check_in_required: true,
      check_in_interval_hours: 24,
      last_check_in: null,
      next_check_in_due: new Date(Date.now() + 24*3600*1000).toISOString(),
      geolocation_enabled: true,
      location_history: [],
      alerts: [],
      defendant_info: data.lead,
    };
    await fetch(`${API}/api/active-bonds`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(activeBondPayload),
    });
  } catch(e) {
    console.warn('Active bond registration failed (non-fatal):', e);
  }
}

// ── SignNow Phase Triggers ──
async function triggerSignNowPhase1() {
  const data = window._bondModalData;
  if (!data) { toast('No bond data', 'error'); return; }
  const snStatus = document.getElementById('sn-status');
  const phaseBadge = document.getElementById('sn-phase-badge');
  if (snStatus) snStatus.textContent = 'Sending Phase 1 packet...';
  try {
    let signerEmail = data.lead.indemnitor_email || '';
    let signerName = data.lead.indemnitor_name || '';
    if (!signerEmail) {
      signerEmail = prompt('Enter indemnitor email for Phase 1 packet:') || '';
      if (!signerEmail) { if (snStatus) snStatus.textContent = 'Cancelled.'; return; }
      signerName = prompt('Enter indemnitor full name:') || 'Indemnitor';
    }
    const payload = {
      intake_id: data.lead._intake_id || '',
      booking_number: data.booking,
      signer_email: signerEmail,
      signer_name: signerName,
      surety_id: data.surety || 'osi',
      form_data: {
        defendant: data.lead,
        booking_number: data.booking,
        bond_amount: data.bond,
        surety: data.surety,
        charges: data.chargeList,
      }
    };
    const r = await fetch(`${API}/api/bond-lifecycle/phase1/trigger`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await r.json();
    if (result.status === 'success') {
      if (snStatus) snStatus.innerHTML = `✅ Phase 1 sent to ${signerEmail} (${result.manifest_size} docs). <a href="${result.signing_link}" target="_blank" style="color:#60a5fa;text-decoration:underline;margin-left:8px">Open Signing Link</a>`;
      if (phaseBadge) { phaseBadge.textContent = 'Phase 1 Sent'; phaseBadge.style.background = 'rgba(59,130,246,0.2)'; phaseBadge.style.color = '#60a5fa'; }
      document.getElementById('btnPhase2').disabled = false;
      toast('Phase 1 packet sent', 'success');
    } else {
      if (snStatus) snStatus.textContent = `❌ ${result.error || 'Phase 1 failed'}`;
      toast(result.error || 'Phase 1 failed', 'error');
    }
  } catch(e) {
    if (snStatus) snStatus.textContent = `❌ Network error: ${e.message}`;
    toast('Network error', 'error');
  }
}

async function triggerSignNowPhase2() {
  const data = window._bondModalData;
  if (!data) { toast('No bond data', 'error'); return; }
  const snStatus = document.getElementById('sn-status');
  const phaseBadge = document.getElementById('sn-phase-badge');
  const poaInput = document.getElementById('poaInput_0');
  const poaNumber = poaInput ? poaInput.value.trim() : '';
  if (!poaNumber) { toast('Enter POA number before sending Phase 2', 'error'); return; }
  if (snStatus) snStatus.textContent = 'Sending Phase 2 packet...';
  try {
    let signerEmail = data.lead.indemnitor_email || '';
    let signerName = data.lead.indemnitor_name || '';
    if (!signerEmail) {
      signerEmail = prompt('Enter indemnitor email for Phase 2 packet:') || '';
      signerName = prompt('Enter indemnitor full name:') || 'Indemnitor';
    }
    const payload = {
      intake_id: data.lead._intake_id || '',
      booking_number: data.booking,
      signer_email: signerEmail,
      signer_name: signerName,
      poa_number: poaNumber,
      agent_name: 'Brendan Doyle',
      agent_license: 'W239955',
      surety_id: data.surety || 'osi',
      form_data: {
        defendant: data.lead,
        booking_number: data.booking,
        bond_amount: data.bond,
        surety: data.surety,
        charges: data.chargeList,
      }
    };
    const r = await fetch(`${API}/api/bond-lifecycle/phase2/trigger`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await r.json();
    if (result.status === 'success') {
      if (snStatus) snStatus.innerHTML = `✅ Phase 2 sent — POA ${poaNumber} (${result.manifest_size} docs). <a href="${result.signing_link}" target="_blank" style="color:#60a5fa;text-decoration:underline;margin-left:8px">Open Signing Link</a>`;
      if (phaseBadge) { phaseBadge.textContent = 'Phase 2 Sent'; phaseBadge.style.background = 'rgba(16,185,129,0.2)'; phaseBadge.style.color = '#10b981'; }
      toast('Phase 2 packet sent', 'success');
    } else {
      if (snStatus) snStatus.textContent = `❌ ${result.error || 'Phase 2 failed'}`;
      toast(result.error || 'Phase 2 failed', 'error');
    }
  } catch(e) {
    if (snStatus) snStatus.textContent = `❌ Network error: ${e.message}`;
    toast('Network error', 'error');
  }
}

// ── Export ──
function exportCSV() {
  const p = new URLSearchParams({sort:SL_STATE.sort,order:SL_STATE.order});
  if (SL_STATE.selectedCounties.length) p.set('county', SL_STATE.selectedCounties.join(','));
  if (SL_STATE.stateCode) p.set('state', SL_STATE.stateCode);
  if (SL_STATE.days) p.set('days', SL_STATE.days);
  if (SL_STATE.custody) p.set('custody', SL_STATE.custody);
  if (SL_STATE.status) p.set('status', SL_STATE.status);
  if (SL_STATE.minBond) p.set('min_bond', SL_STATE.minBond);
  if (SL_STATE.search) p.set('search', SL_STATE.search);
  window.open(`${API}/api/leads/export?${p}`);
  toast('CSV download started','success');
}
function copyToSlack() {
  if (!SL_STATE.leads.length) { toast('No leads to copy','error'); return; }
  const lines = SL_STATE.leads.slice(0,20).map(l => `• *${l.full_name}* — ${l.county}${l.state?' ('+l.state+')':''} — $${(l.bond_amount||0).toLocaleString()} — Score: ${l.lead_score||0} (${l.lead_status||''})`);
  const text = `*☘️ ShamrockLeads Export* (${SL_STATE.total} total)\n${lines.join('\n')}${SL_STATE.total>20?'\n_...and '+(SL_STATE.total-20)+' more_':''}`;
  navigator.clipboard.writeText(text).then(()=>toast('Copied — paste in Slack!','success')).catch(()=>toast('Copy failed','error'));
}

// ── Auto-Refresh ──
let cd = 30;
async function refresh() { cd = 30; await loadDashboard(); if (document.getElementById('tabLeads').classList.contains('active')) applyFilters(); }
setInterval(() => { cd--; document.getElementById('refreshMeta').textContent = `Auto-refresh in ${cd}s`; if (cd <= 0) { cd = 30; refresh(); } }, 1000);

// ── Event Listeners ──
document.addEventListener('click', e => {
  if (!e.target.closest('.multi-select')) {
    document.getElementById('countyDropdown')?.classList.remove('show');
    document.getElementById('defCountyDropdown')?.classList.remove('show');
    document.querySelectorAll('.multi-select-trigger').forEach(t => t.classList.remove('open'));
  }
});
document.addEventListener('keydown', e => {
  if (e.key === '/' && !e.target.matches('input,textarea,select')) { e.preventDefault(); document.getElementById('searchInput')?.focus(); }
  if (e.key === 'Escape') { document.getElementById('searchInput').value=''; SL_STATE.search=''; closeModal(); applyFilters(); }
});

// ── Mobile redirect ──
if (/Mobi|Android/i.test(navigator.userAgent) && !location.pathname.includes('mobile')) {
  const mobilePath = location.pathname.replace('index.html','') + 'mobile.html';
  if (confirm('Switch to mobile view?')) location.href = mobilePath;
}

// ── Custody Status Override ──
async function updateCustody(bookingNumber, newStatus, selectEl) {
  if (!newStatus || !bookingNumber) return;
  try {
    const r = await fetch(`${API}/api/leads/update-custody`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        booking_number: bookingNumber,
        custody_status: newStatus,
        changed_by: document.getElementById('outreachAgent')?.value || 'dashboard_user',
      }),
    });
    const d = await r.json();
    if (d.success) {
      toast(`${bookingNumber}: ${d.old_status} → ${d.new_status}`, 'success');
      // Update dropdown class for color
      const cls = newStatus.toLowerCase().includes('custody') && !newStatus.toLowerCase().includes('not') ? 'custody' : 'released';
      selectEl.className = `def-status-badge ${cls}`;
      selectEl.style.cssText = 'cursor:pointer;border:1px solid var(--border);background:transparent;padding:2px 6px;font-size:11px;border-radius:6px';
    } else {
      toast(d.error || 'Update failed', 'error');
    }
  } catch(e) {
    toast('Network error updating custody', 'error');
  }

  // Task B: Fetch the intake_id for this booking number so Phase 1/2 triggers work 
  // if this bond is opened without a known intake_id
  if (window._bondModalData && !window._bondModalData.lead._intake_id && bookingNumber) {
    fetch(`${API}/api/intake/by-booking/${encodeURIComponent(bookingNumber)}`)
      .then(r => r.json())
      .then(d => {
        if (d.intake_id && window._bondModalData) {
          window._bondModalData.lead._intake_id = d.intake_id;
        }
      })
      .catch(() => {}); // non-fatal
  }
}

// ── Bond Amount Override ──
// Scrapers often leave bond_amount = 0 until first appearance (hours later).
// Staff can set the real amount so Write Bond / premium / billing work.
function _parseBondInput(raw) {
  if (raw == null || raw === '') return NaN;
  const n = parseFloat(String(raw).replace(/[$,\s]/g, ''));
  return Number.isFinite(n) ? n : NaN;
}

async function updateBondAmount(bookingNumber, rawAmount, inputEl) {
  if (!bookingNumber) { toast('Missing booking number', 'error'); return null; }
  const amount = _parseBondInput(rawAmount);
  if (!Number.isFinite(amount) || amount < 0) {
    toast('Enter a valid bond amount (0 or more)', 'error');
    if (inputEl) inputEl.focus();
    return null;
  }
  try {
    const r = await fetch(`${API}/api/leads/update-bond-amount`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        booking_number: bookingNumber,
        bond_amount: amount,
        changed_by: document.getElementById('outreachAgent')?.value || 'dashboard_user',
        note: 'Manual bond amount from Defendants tab',
      }),
    });
    const d = await r.json();
    if (!r.ok || d.success === false) {
      toast(d.error || 'Failed to update bond amount', 'error');
      return null;
    }
    // Keep in-memory lead map fresh for Write Bond
    if (window._leadMap && window._leadMap[bookingNumber]) {
      window._leadMap[bookingNumber].bond_amount = amount;
      if (d.lead_score != null) window._leadMap[bookingNumber].lead_score = d.lead_score;
      if (d.lead_status) window._leadMap[bookingNumber].lead_status = d.lead_status;
    }
    if (window._bondModalData && window._bondModalData.booking === bookingNumber) {
      window._bondModalData.bond = amount;
      if (window._bondModalData.lead) window._bondModalData.lead.bond_amount = amount;
    }
    // Update card pill if present
    const card = document.querySelector(`.def-card[data-booking="${CSS.escape ? CSS.escape(bookingNumber) : bookingNumber}"]`);
    if (card) {
      const pill = card.querySelector('.def-bond-pill');
      if (pill) {
        pill.textContent = amount > 0 ? ('$' + amount.toLocaleString()) : '$0 — set bond';
        pill.classList.toggle('bond-zero', amount <= 0);
        const bc = amount >= 10000 ? 'high' : amount >= 2500 ? 'mid' : 'low';
        pill.className = 'def-bond-pill ' + bc + (amount <= 0 ? ' bond-zero' : '');
      }
      const scoreEl = card.querySelector('[id^="defScore_"]');
      if (scoreEl && d.lead_score != null) {
        scoreEl.textContent = `${d.lead_score} ${d.lead_status || ''}`.trim();
      }
    }
    if (inputEl) inputEl.value = amount > 0 ? amount : '';
    const prem = d.premium_estimate != null ? d.premium_estimate : Math.round(amount * 0.1);
    toast(`Bond set to $${amount.toLocaleString()} (est. premium $${Number(prem).toLocaleString()})`, 'success');
    return d;
  } catch (e) {
    toast('Network error updating bond amount', 'error');
    return null;
  }
}

function onWriteBondAmountChange(raw, persist) {
  const amount = _parseBondInput(raw);
  if (!Number.isFinite(amount) || amount < 0) return;
  const data = window._bondModalData;
  if (!data) return;
  data.bond = amount;
  if (data.lead) data.lead.bond_amount = amount;

  const cnty = data.county || '';
  const premium = Math.max(100, amount * 0.1);
  const transferFee = (amount > 25000 || ['Lee', 'Charlotte'].includes(cnty)) ? 0 : 125;
  const premEl = document.getElementById('wbPremiumDisplay');
  const xferEl = document.getElementById('wbTransferDisplay');
  const totEl = document.getElementById('wbTotalDueDisplay');
  if (premEl) premEl.textContent = '$' + premium.toLocaleString();
  if (xferEl) xferEl.innerHTML = transferFee ? ('$' + transferFee) : '<span style="color:var(--success)">Waived</span>';
  if (totEl) totEl.textContent = '$' + (premium + transferFee).toLocaleString();

  // Re-suggest POA tier when bond changes (debounced lightly via persist path)
  if (persist && data.surety && data.chargeList) {
    fetchPoaNumbers(data.surety, amount, data.chargeList);
  }
}

async function saveWriteBondAmount() {
  const data = window._bondModalData;
  if (!data || !data.booking) {
    toast('No booking number on this bond', 'error');
    return;
  }
  const input = document.getElementById('wbBondAmountInput');
  const raw = input ? input.value : data.bond;
  onWriteBondAmountChange(raw, true);
  await updateBondAmount(data.booking, raw, input);
}

// ── Refresh one defendant from county booking sheet ─────────────────────────
function _applyBondToCard(bookingNumber, amount, score, status) {
  if (window._leadMap && window._leadMap[bookingNumber]) {
    window._leadMap[bookingNumber].bond_amount = amount;
    if (score != null) window._leadMap[bookingNumber].lead_score = score;
    if (status) window._leadMap[bookingNumber].lead_status = status;
  }
  const card = document.querySelector(`.def-card[data-booking="${bookingNumber}"]`);
  if (!card) return;
  const pill = card.querySelector('.def-bond-pill');
  if (pill) {
    const bc = amount >= 10000 ? 'high' : amount >= 2500 ? 'mid' : 'low';
    pill.className = 'def-bond-pill ' + bc + (amount <= 0 ? ' bond-zero' : '');
    pill.textContent = amount > 0 ? ('$' + Number(amount).toLocaleString()) : '$0 — set bond';
  }
  const inp = card.querySelector('.def-bond-input');
  if (inp) inp.value = amount > 0 ? amount : '';
  const scoreEl = card.querySelector('[id^="defScore_"]');
  if (scoreEl && score != null) scoreEl.textContent = `${score} ${status || ''}`.trim();
}

async function refreshDefendantFromSource(bookingNumber, btnEl) {
  if (!bookingNumber) { toast('Missing booking number', 'error'); return; }
  const btn = btnEl;
  const prev = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = '⏳…'; }
  try {
    const r = await fetch(`${API}/api/leads/refresh-from-source`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        booking_number: bookingNumber,
        changed_by: document.getElementById('outreachAgent')?.value || 'dashboard_user',
      }),
    });
    const d = await r.json();
    if (!r.ok || d.success === false) {
      toast(d.error || 'Refresh failed', 'error');
      return;
    }

    // Open source booking sheet so staff can verify visually
    if (d.detail_url) {
      try { window.open(d.detail_url, '_blank', 'noopener'); } catch (e) { /* popup blocked */ }
    }

    const newAmt = d.new_bond_amount != null ? Number(d.new_bond_amount) : null;
    const bondRes = d.bond_result || {};
    if (d.updated_lead) {
      _applyFullLeadToCard(bookingNumber, d.updated_lead);
      toast(`🔄 Updated from source: ${d.updated_lead.full_name || ''} ($${Number(d.updated_lead.bond_amount||0).toLocaleString()})`, 'success');
    } else if (d.immediate && d.immediate.bond_updated && newAmt != null) {
      _applyBondToCard(bookingNumber, newAmt, bondRes.lead_score, bondRes.lead_status);
      toast(`🔄 Updated from source: $${newAmt.toLocaleString()}`, 'success');
    } else if (d.immediate && d.immediate.bond_found > 0) {
      _applyBondToCard(bookingNumber, d.immediate.bond_found, null, null);
      toast(`Source shows $${Number(d.immediate.bond_found).toLocaleString()} (already on file or saved)`, 'success');
    } else if (d.immediate && d.immediate.error) {
      toast(`Source page: ${d.immediate.error}. Full recheck queued.`, 'info');
    } else {
      toast(d.message || 'Refresh requested — waiting for scraper…', 'info');
    }

    // Poll scraper engine for full field refresh (bond type, charges, status)
    if (d.trigger_id) {
      _pollSourceRefresh(d.trigger_id, bookingNumber, btn, prev);
      return; // poll restores button
    }
  } catch (e) {
    toast('Network error refreshing from source', 'error');
  } finally {
    if (btn && !btn.dataset.polling) {
      btn.disabled = false;
      btn.innerHTML = prev || '🔄 Update';
    }
  }
}

async function _pollSourceRefresh(triggerId, bookingNumber, btn, prevLabel) {
  if (btn) {
    btn.dataset.polling = '1';
    btn.disabled = true;
    btn.innerHTML = '⏳ Scraping…';
  }
  const maxAttempts = 24; // ~2 min at 5s
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(res => setTimeout(res, 5000));
    try {
      const r = await fetch(
        `${API}/api/leads/refresh-from-source/status?trigger_id=${encodeURIComponent(triggerId)}&booking_number=${encodeURIComponent(bookingNumber)}`
      );
      const d = await r.json();
      const st = (d.trigger && d.trigger.status) || '';
      if (st === 'done' || st === 'error') {
        const arrest = d.arrest || {};
        if (arrest.bond_amount != null) {
          _applyBondToCard(
            bookingNumber,
            Number(arrest.bond_amount) || 0,
            arrest.lead_score,
            arrest.lead_status
          );
        }
        const changes = (d.rechecks || []).flatMap(x => x.changes || []);
        if (st === 'error') {
          toast(`Scraper recheck error: ${(d.trigger && d.trigger.error) || 'unknown'}`, 'error');
        } else if (changes.length) {
          const bits = changes.map(c => `${c.field}: ${c.old} → ${c.new}`).join('; ');
          toast(`🔄 Source refresh complete: ${bits}`, 'success');
        } else {
          toast('🔄 Source refresh complete — no field changes', 'success');
        }
        break;
      }
      if (btn) btn.innerHTML = `⏳ ${st || 'queued'}…`;
    } catch (e) {
      // keep polling
    }
  }
  if (btn) {
    delete btn.dataset.polling;
    btn.disabled = false;
    btn.innerHTML = prevLabel || '🔄 Update';
  }
}

// ── openWriteBond ──
// Pre-populate the Write Bond modal from an intake record.
// Called by SLIntake.writeBondFromIntake() when staff clicks 'Write Bond' in the Intake Queue.
function openWriteBond(opts) {
  opts = opts || {};
  const def = opts.defendant || {};
  const booking = opts.booking || {};
  const bond = opts.bond || {};
  const indemnitors = opts.indemnitors || [];

  // Build a synthetic lead object that openBondModal() expects
  const syntheticLead = {
    full_name:      def.full_name || def.name || '',
    bond_amount:    bond.amount || 0,
    county:         booking.county || def.county || '',
    booking_number: booking.booking_number || def.bookingNumber || '',
    charges:        opts.charges || '',
    _intake_indemnitors: indemnitors,
    _intake_id:     opts.intake_id || '',
    _intake_source: opts.intake_source || '',
  };

  openBondModal(syntheticLead);

  // After modal renders, pre-fill indemnitor fields
  if (indemnitors.length > 0) {
    setTimeout(() => {
      const ind = indemnitors[0];
      const fieldMap = [
        ['indemnitorFirstName', ind.firstName],
        ['indemnitorLastName',  ind.lastName],
        ['indemnitorPhone',     ind.phone],
        ['indemnitorEmail',     ind.email],
        ['indemnitorRelation',  ind.relationship],
        ['indemnitorDOB',       ind.dob],
        ['indemnitorAddress',   ind.address],
        ['indemnitorCity',      ind.city],
        ['indemnitorZip',       ind.zip],
        ['indemnitorEmployer',  ind.employer],
        ['indemnitorEmployerPhone', ind.employerPhone],
      ];
      fieldMap.forEach(([id, val]) => {
        const el = document.getElementById(id);
        if (el && val) el.value = val;
      });
      if (window._bondModalData) {
        window._bondModalData.indemnitors = indemnitors;
        window._bondModalData.intake_id = opts.intake_id || '';
        window._bondModalData.intake_source = opts.intake_source || '';
      }
    }, 200);
  }
}

// ── Contact Indemnitor Module ──
window.SLContact = (function() {
  const TEMPLATES_EN = {
    standard: (name, county, agent) =>
      `Hi, this is ${agent} with Shamrock Bail Bonds. I see that ${name} is currently in custody in ${county} County. We can help get them home fast with flexible payment plans. Give us a call or reply here.`,
    urgent: (name, county, agent) =>
      `Hi, this is ${agent} with Shamrock Bail Bonds. ${name} is currently being held in ${county} County on a significant bond. We specialize in quick releases and flexible payment options. Would you like help?`,
    followup: (name, county, agent) =>
      `Hi, this is ${agent} with Shamrock Bail Bonds, just following up about ${name} in ${county} County. We're still available to help if you'd like to get them home. No obligation to chat.`,
    payment: (name, county, agent) =>
      `Hi, this is ${agent} with Shamrock Bail Bonds. We can help bond ${name} out of ${county} County Jail today. We offer flexible payment plans and fast service. Reply or call us anytime.`,
  };
  const TEMPLATES_ES = {
    standard: (name, county, agent) =>
      `Hola, soy ${agent} de Shamrock Bail Bonds. Veo que ${name} está detenido/a en la cárcel del condado de ${county}. Podemos ayudarle a salir rápido con planes de pago flexibles. Llámenos o responda aquí.`,
    urgent: (name, county, agent) =>
      `Hola, soy ${agent} de Shamrock Bail Bonds. ${name} está detenido/a en el condado de ${county} con una fianza significativa. Nos especializamos en liberaciones rápidas y opciones de pago flexibles. ¿Le gustaría ayuda?`,
    followup: (name, county, agent) =>
      `Hola, soy ${agent} de Shamrock Bail Bonds, haciendo seguimiento sobre ${name} en el condado de ${county}. Todavía estamos disponibles para ayudar si desea que salga. Sin compromiso de hablar.`,
    payment: (name, county, agent) =>
      `Hola, soy ${agent} de Shamrock Bail Bonds. Podemos sacar a ${name} de la cárcel del condado de ${county} hoy mismo. Ofrecemos planes de pago flexibles y servicio rápido. Responda o llámenos cuando quiera.`,
  };

  let _current = {};

  function openModal(booking, name, county, bond, bookingNum) {
    _current = { booking: booking || bookingNum || '', name: name || '', county: (county||'').trim(), bond: bond || 0 };
    const modal = document.getElementById('contactIndemModal');
    if (!modal) return;
    document.getElementById('ciDefName').textContent = name || '—';
    document.getElementById('ciDefCounty').textContent = (county||'').trim() || '—';
    document.getElementById('ciDefBond').textContent = bond ? '$' + Number(bond).toLocaleString() : '—';
    document.getElementById('ciIndemName').value = '';
    document.getElementById('ciPhone').value = '';
    document.getElementById('ciRelation').value = 'Indemnitor';
    document.getElementById('ciAgent').value = document.getElementById('outreachAgent')?.value || 'Brendan';
    document.getElementById('ciFromNumber').value = '2399550178';
    document.getElementById('ciLang').value = 'en';
    document.getElementById('ciTemplate').value = 'standard';
    document.getElementById('ciSendStatus').textContent = '';
    _fillTemplate();
    modal.classList.add('show');
  }

  function closeModal() {
    document.getElementById('contactIndemModal')?.classList.remove('show');
  }

  function _fillTemplate() {
    const lang = document.getElementById('ciLang')?.value || 'en';
    const tpl = document.getElementById('ciTemplate')?.value || 'standard';
    const agent = document.getElementById('ciAgent')?.value?.trim() || 'Brendan';
    const templates = lang === 'es' ? TEMPLATES_ES : TEMPLATES_EN;
    const fn = templates[tpl] || templates.standard;
    document.getElementById('ciMessage').value = fn(_current.name, _current.county, agent);
  }

  async function sendText() {
    const phone = (document.getElementById('ciPhone')?.value || '').replace(/\D/g, '');
    const message = (document.getElementById('ciMessage')?.value || '').trim();
    const relation = document.getElementById('ciRelation')?.value || 'Indemnitor';
    const agent = document.getElementById('ciAgent')?.value?.trim() || 'Brendan';
    const fromNum = document.getElementById('ciFromNumber')?.value || '2399550178';
    const statusEl = document.getElementById('ciSendStatus');
    const btn = document.getElementById('ciSendBtn');

    if (!phone || phone.length < 10) { SL.toast('Enter a valid phone number', 'error'); return; }
    if (!message) { SL.toast('Message cannot be empty', 'error'); return; }

    btn.disabled = true;
    btn.innerHTML = '<span class="btn-spinner"></span> Sending…';
    if (statusEl) statusEl.textContent = '';

    try {
      const r = await fetch(`${API}/api/imessage/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone,
          message,
          booking_number: _current.booking,
          defendant_name: _current.name,
          county: _current.county,
          recipient_label: relation,
          agent_name: agent,
          from_number: fromNum,
          inject_geo: true,
        }),
      });
      const result = await r.json();
      if (result.success) {
        if (statusEl) statusEl.innerHTML = '<span style="color:var(--accent)">\u2713 Sent</span>';
        SL.toast(`Text sent to ${relation}`, 'success');
        // ── Auto-attach indemnitor to the bond record ──
        if (_current.booking && phone) {
          try {
            const indName = (document.getElementById('ciIndemName')?.value || '').trim();
            const formattedPhone = phone.length === 10 ? '+1' + phone : (phone.startsWith('1') ? '+' + phone : phone);
            await fetch(`${API}/api/indemnitors/create`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                booking_number: _current.booking,
                phone: formattedPhone,
                name: indName || relation,
                relationship: relation,
                agent: agent,
                source: 'contact_indem_button',
              }),
            });
            // Refresh Indemnitors tab if it is the active tab
            if (typeof SLIndemnitor !== 'undefined' && SLIndemnitor.load) {
              SLIndemnitor.load();
            }
          } catch(_e) { /* non-fatal — text was still sent successfully */ }
        }
        document.getElementById('ciPhone').value = '';
        setTimeout(closeModal, 1200);
      } else {
        if (statusEl) statusEl.innerHTML = `<span style="color:var(--red)">⚠ ${result.error || 'Send failed'}</span>`;
        SL.toast(result.error || 'Send failed', 'error');
      }
    } catch(e) {
      if (statusEl) statusEl.innerHTML = '<span style="color:var(--red)">⚠ Network error</span>';
      SL.toast('Network error', 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '\ud83d\udcf1 Send Text';
    }
  }

  return { openModal, closeModal, fillTemplate: _fillTemplate, sendText };
})();

// ═══════════════════════════════════════════════════════
//  CUSTODY RE-CHECK AGENT — Dashboard UI Controller
// ═══════════════════════════════════════════════════════
let _recheckPollTimer = null;
let _recheckDiffs = {};  // booking_number → diff data

async function triggerCustodyRecheck() {
  const selected = (window.SL_STATE && Array.isArray(SL_STATE.defSelectedCounties))
    ? SL_STATE.defSelectedCounties
    : [];
  // Custody recheck is per-county on the backend — use first selection (or bare name from label)
  let county = selected[0] || document.getElementById('defCountyFilter')?.value || '';
  if (county.includes(',')) county = county.split(',')[0].trim();
  // Strip " (FL)" label → bare county for recheck jobs that expect bare names
  const bare = county.replace(/\s*\([A-Za-z]{2}\)$/, '').trim();
  county = bare || county;
  if (!county) {
    SL.toast('Select at least one county first to verify custody', 'error');
    return;
  }
  if (selected.length > 1) {
    SL.toast(`Rechecking ${county} first (${selected.length} selected — run again for others)`, 'info');
  }

  // Update button to checking state
  const btn = document.getElementById('custodyRecheckBtn');
  if (btn) {
    btn.classList.add('checking');
    btn.querySelector('.recheck-label').textContent = 'Checking...';
    document.getElementById('recheckPulse').style.display = 'inline-block';
  }

  // Show banner in pending state
  const banner = document.getElementById('custodyRecheckBanner');
  if (banner) {
    banner.style.display = 'block';
    banner.classList.add('pending');
    banner.classList.remove('done');
    document.getElementById('recheckStatusIcon').textContent = '⏳';
    document.getElementById('recheckBannerTitle').textContent = `Verifying custody for ${county} County...`;
    document.getElementById('recheckBannerStats').innerHTML = '<span class="stat-pill">Queuing scraper agent...</span>';
    document.getElementById('recheckDiffList').innerHTML = '';
  }

  try {
    const r = await fetch(`${API}/api/scraper/custody-recheck`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ county }),
    });
    const data = await r.json();

    if (!data.ok) {
      SL.toast(data.error || 'Failed to trigger recheck', 'error');
      _resetRecheckButton();
      return;
    }

    SL.toast(`Custody recheck queued for ${county}`, 'success');

    // Start polling for results
    _pollRecheckResults(data.trigger_id, county);

  } catch (e) {
    console.error('Custody recheck error:', e);
    SL.toast('Network error triggering recheck', 'error');
    _resetRecheckButton();
  }
}

function _pollRecheckResults(triggerId, county) {
  if (_recheckPollTimer) clearInterval(_recheckPollTimer);

  let attempts = 0;
  const maxAttempts = 36; // 3 minutes @ 5s intervals

  _recheckPollTimer = setInterval(async () => {
    attempts++;
    if (attempts > maxAttempts) {
      clearInterval(_recheckPollTimer);
      _recheckPollTimer = null;
      _updateRecheckBanner('timeout', county, {});
      _resetRecheckButton();
      return;
    }

    try {
      const r = await fetch(`${API}/api/scraper/custody-recheck/results?trigger_id=${triggerId}`);
      const data = await r.json();

      // Update banner with progress
      const titleEl = document.getElementById('recheckBannerTitle');
      if (titleEl && data.status === 'running') {
        titleEl.textContent = `Scanning ${county} County roster...`;
        document.getElementById('recheckStatusIcon').textContent = '🔍';
      }

      if (data.status === 'done' || data.status === 'error') {
        clearInterval(_recheckPollTimer);
        _recheckPollTimer = null;
        _updateRecheckBanner(data.status, county, data);
        _highlightChangedCards(data.diffs || []);
        _resetRecheckButton();

        if (data.status === 'done') {
          const summary = `${data.total_checked} checked · ${data.changes_found} changes · ${data.not_found_count} not found`;
          SL.toast(`Custody verification complete: ${summary}`, 'success');
        } else {
          SL.toast('Custody verification encountered an error', 'error');
        }
      }
    } catch (e) {
      console.debug('Poll error (will retry):', e);
    }
  }, 5000);
}

function _updateRecheckBanner(status, county, data) {
  const banner = document.getElementById('custodyRecheckBanner');
  if (!banner) return;

  banner.classList.remove('pending');
  banner.classList.add('done');

  const iconEl = document.getElementById('recheckStatusIcon');
  const titleEl = document.getElementById('recheckBannerTitle');
  const statsEl = document.getElementById('recheckBannerStats');
  const diffListEl = document.getElementById('recheckDiffList');

  if (status === 'done') {
    const checked = data.total_checked || 0;
    const changes = data.changes_found || 0;
    const notFound = data.not_found_count || 0;
    const verified = checked - changes - notFound;

    iconEl.textContent = changes > 0 || notFound > 0 ? '⚠️' : '✅';
    titleEl.textContent = `${county} County — Custody Verified`;

    statsEl.innerHTML = `
      <span class="stat-pill verified">✓ ${verified} verified</span>
      ${changes > 0 ? `<span class="stat-pill changes">🔄 ${changes} changed</span>` : ''}
      ${notFound > 0 ? `<span class="stat-pill released">🚪 ${notFound} not found</span>` : ''}
    `;

    // Render diff items
    const diffs = (data.diffs || []).filter(d => d.changes && d.changes.length > 0);
    if (diffs.length > 0) {
      diffListEl.innerHTML = diffs.map(d => {
        const chips = d.changes.map(c => {
          const chipClass = c.field === 'status' ? 'status-change'
            : c.field === 'bond_amount' ? 'bond-change'
            : c.field === 'charges' ? 'charge-change'
            : 'status-change';
          const label = c.field.replace('_', ' ');
          const oldStr = typeof c.old === 'number' ? `$${c.old.toLocaleString()}` : (c.old || '—');
          const newStr = typeof c.new === 'number' ? `$${c.new.toLocaleString()}` : (c.new || '—');
          return `<span class="recheck-diff-chip ${chipClass}">
            <span class="old-val">${oldStr}</span>
            <span class="arrow">→</span>
            <span class="new-val">${newStr}</span>
          </span>`;
        }).join('');

        return `<div class="recheck-diff-item">
          <div>
            <div class="recheck-diff-name">${d.full_name || 'Unknown'}</div>
            <div class="recheck-diff-booking">${d.booking_number || ''}</div>
          </div>
          <div class="recheck-diff-changes">${chips}</div>
        </div>`;
      }).join('');
    } else {
      diffListEl.innerHTML = '<div style="padding:12px 16px;text-align:center;color:var(--muted);font-size:12px">All records verified — no changes detected</div>';
    }

  } else if (status === 'timeout') {
    iconEl.textContent = '⏱️';
    titleEl.textContent = `${county} County — Verification timed out`;
    statsEl.innerHTML = '<span class="stat-pill changes">Scraper may still be running. Try again later.</span>';
    diffListEl.innerHTML = '';
  } else {
    iconEl.textContent = '❌';
    titleEl.textContent = `${county} County — Verification error`;
    statsEl.innerHTML = '<span class="stat-pill released">The scraper encountered an error</span>';
    diffListEl.innerHTML = '';
  }
}

function _highlightChangedCards(diffs) {
  // Build a lookup map
  _recheckDiffs = {};
  diffs.forEach(d => {
    if (d.booking_number) _recheckDiffs[d.booking_number] = d;
  });

  // Find all defendant cards and overlay badges
  document.querySelectorAll('.def-card').forEach(card => {
    const bookingEl = card.querySelector('.def-booking');
    if (!bookingEl) return;
    const bk = bookingEl.textContent.trim();
    const diff = _recheckDiffs[bk];
    if (!diff) return;

    // Remove existing badge if any
    card.querySelectorAll('.custody-diff-badge').forEach(b => b.remove());

    if (!diff.source_found) {
      // Not found on roster
      card.insertAdjacentHTML('afterbegin',
        '<div class="custody-diff-badge released">🚪 Not on Roster</div>'
      );
      card.style.position = 'relative';
    } else if (diff.changes && diff.changes.length > 0) {
      // Has changes
      const label = diff.changes.map(c => c.field.replace('_',' ')).join(', ');
      card.insertAdjacentHTML('afterbegin',
        `<div class="custody-diff-badge changed">🔄 ${label}</div>`
      );
      card.style.position = 'relative';
    }
  });
}

function closeRecheckBanner() {
  const banner = document.getElementById('custodyRecheckBanner');
  if (banner) {
    banner.style.display = 'none';
    banner.classList.remove('pending', 'done');
  }
}

function _resetRecheckButton() {
  const btn = document.getElementById('custodyRecheckBtn');
  if (btn) {
    btn.classList.remove('checking');
    btn.querySelector('.recheck-label').textContent = 'Verify Custody';
    document.getElementById('recheckPulse').style.display = 'none';
  }
}


// ── Saved Views ──
function saveCurrentView() {
  const name = prompt("Enter a name for this saved view:");
  if (!name) return;

  const view = {
    id: 'view_' + Date.now(),
    name: name,
    state: {
      selectedCounties: [...SL_STATE.selectedCounties],
      days: SL_STATE.days,
      custody: SL_STATE.custody,
      status: SL_STATE.status,
      minBond: SL_STATE.minBond,
      search: SL_STATE.search
    }
  };

  const views = JSON.parse(localStorage.getItem('sl_saved_views') || '[]');
  views.push(view);
  localStorage.setItem('sl_saved_views', JSON.stringify(views));
  
  populateSavedViews();
  if (window.SL && SL.toast) SL.toast(`Saved view: ${name}`);
}

function loadSavedView(id) {
  if (!id) return;
  if (id === '__clear__') {
    if (confirm("Are you sure you want to delete all saved views?")) {
      localStorage.removeItem('sl_saved_views');
      populateSavedViews();
      if (window.SL && SL.toast) SL.toast("All saved views cleared.");
    }
    document.getElementById('savedViewsSelect').value = '';
    return;
  }

  const views = JSON.parse(localStorage.getItem('sl_saved_views') || '[]');
  const view = views.find(v => v.id === id);
  if (!view) return;

  SL_STATE.selectedCounties = [...(view.state.selectedCounties || [])];
  SL_STATE.days = view.state.days || 0;
  SL_STATE.custody = view.state.custody || '';
  SL_STATE.status = view.state.status || '';
  SL_STATE.minBond = view.state.minBond || 0;
  SL_STATE.search = view.state.search || '';

  // Update DOM elements
  if (document.getElementById('custodyFilter')) document.getElementById('custodyFilter').value = SL_STATE.custody;
  if (document.getElementById('statusFilter')) document.getElementById('statusFilter').value = SL_STATE.status;
  if (document.getElementById('searchInput')) document.getElementById('searchInput').value = SL_STATE.search;
  
  // Update Buttons
  document.querySelectorAll('#dateRange button').forEach(b => {
    b.classList.remove('active');
    const val = parseInt(b.innerText);
    if ((isNaN(val) && SL_STATE.days === 0 && b.innerText === 'All') || val === SL_STATE.days) {
      b.classList.add('active');
    }
  });
  document.querySelectorAll('#bondRange button').forEach(b => {
    b.classList.remove('active');
    if (
      (SL_STATE.minBond === 0 && b.innerText === '$0+') ||
      (SL_STATE.minBond === 1000 && b.innerText === '$1K+') ||
      (SL_STATE.minBond === 2500 && b.innerText === '$2.5K+') ||
      (SL_STATE.minBond === 5000 && b.innerText === '$5K+') ||
      (SL_STATE.minBond === 10000 && b.innerText === '$10K+')
    ) {
      b.classList.add('active');
    }
  });

  if (window.buildCountyOptions) buildCountyOptions(SL_STATE.counties);
  if (window.applyFilters) applyFilters();
  
  document.getElementById('savedViewsSelect').value = '';
}

function populateSavedViews() {
  const select = document.getElementById('savedViewsSelect');
  if (!select) return;
  const views = JSON.parse(localStorage.getItem('sl_saved_views') || '[]');
  
  let html = `<option value="">Saved Views...</option>`;
  views.forEach(v => {
    html += `<option value="${v.id}">${v.name}</option>`;
  });
  if (views.length > 0) {
    html += `<option disabled>──────────</option>`;
    html += `<option value="__clear__">Clear All Views</option>`;
  }
  select.innerHTML = html;
}

// ── Build SL namespace ──
window.SL = { toggleTheme, switchTab, toggleNavGroup, restoreNavGroups, toggleCountyDropdown, filterCountyOptions, toggleCounty,
  toggleDefCountyDropdown, toggleDefCounty, filterDefCountyOptions, applyDefCountyPreset,
  buildDefCountyOptions, updateDefCountyLabel,
  applyPreset, setDays, setBond, setDefBond, sortBy, debounceSearch, debounceDefSearch, applyFilters,
  goPage, goDefPage, openBondModal, openWriteBond, selectSurety, closeModal, submitBond, exportCSV, copyToSlack,
  clearAll, refresh, toast, loadDefendants, downloadBond, downloadAllBonds, printAppearanceBondPackage, registerActiveBond,
  sendOutreach, loadOutreachHistory, checkBBStatus, updateCustody, updateBondAmount,
  onWriteBondAmountChange, saveWriteBondAmount, refreshDefendantFromSource,
  triggerSignNowPhase1, triggerSignNowPhase2,
  triggerCustodyRecheck, closeRecheckBanner,
  saveCurrentView, loadSavedView, populateSavedViews };

// Restore collapsible sidebar groups after shell load
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', restoreNavGroups);
} else {
  try { restoreNavGroups(); } catch (_) {}
}

// Expose for inline handlers on defendant cards
window.updateBondAmount = updateBondAmount;
window.onWriteBondAmountChange = onWriteBondAmountChange;
window.printAppearanceBondPackage = printAppearanceBondPackage;
window.downloadAllBonds = downloadAllBonds;
window.downloadBond = downloadBond;
window.editBond = editBond;
window.saveWriteBondAmount = saveWriteBondAmount;
window.refreshDefendantFromSource = refreshDefendantFromSource;

// ── Apply Updated Lead to Cards ──
function _applyFullLeadToCard(bookingNumber, lead) {
  if (!bookingNumber || !lead) return;

  if (window._leadMap && window._leadMap[bookingNumber]) {
    Object.assign(window._leadMap[bookingNumber], lead);
  }

  const cards = document.querySelectorAll(`.def-card[data-booking="${bookingNumber}"]`);
  cards.forEach(card => {
    if (lead.full_name) {
      const nameEl = card.querySelector('.def-name, h3, .def-card-name');
      if (nameEl) nameEl.textContent = lead.full_name;
    }
    if (lead.bond_amount != null) {
      const amount = Number(lead.bond_amount) || 0;
      const pill = card.querySelector('.def-bond-pill');
      if (pill) {
        const bc = amount >= 10000 ? 'high' : amount >= 2500 ? 'mid' : 'low';
        pill.className = 'def-bond-pill ' + bc + (amount <= 0 ? ' bond-zero' : '');
        pill.textContent = amount > 0 ? ('$' + Number(amount).toLocaleString()) : '$0 — set bond';
      }
      const inp = card.querySelector('.def-bond-input');
      if (inp) inp.value = amount > 0 ? amount : '';
    }
    if (lead.charges) {
      const chargesEl = card.querySelector('.def-charges, .def-card-charges');
      if (chargesEl) chargesEl.textContent = lead.charges;
    }
    if (lead.court_date) {
      const courtEls = card.querySelectorAll('.def-court-date, [data-field="court_date"]');
      courtEls.forEach(el => el.textContent = lead.court_date);
    }
    if (lead.county) {
      const countyEls = card.querySelectorAll('.def-county, [data-field="county"]');
      countyEls.forEach(el => el.textContent = lead.county + ' County');
    }
    const scoreEl = card.querySelector('[id^="defScore_"]');
    if (scoreEl && lead.lead_score != null) {
      scoreEl.textContent = `${lead.lead_score} ${lead.lead_status || ''}`.trim();
    }
  });
}
window._applyFullLeadToCard = _applyFullLeadToCard;

// ── Edit Lead Modal ──
function openEditLeadModal(bookingNumberOrLead) {
  let lead = typeof bookingNumberOrLead === 'object' ? bookingNumberOrLead : (window._leadMap && window._leadMap[bookingNumberOrLead]);
  const bk = typeof bookingNumberOrLead === 'string' ? bookingNumberOrLead : (lead ? lead.booking_number : '');

  if (!lead && bk) {
    lead = { booking_number: bk };
  }
  if (!lead) {
    toast('No lead data found to edit', 'error');
    return;
  }

  let modal = document.getElementById('editLeadModal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'editLeadModal';
    modal.className = 'modal-backdrop';
    modal.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px;box-sizing:border-box;';
    document.body.appendChild(modal);
  }

  const bkEsc = (lead.booking_number || '').replace(/"/g, '&quot;');
  const nameEsc = (lead.full_name || '').replace(/"/g, '&quot;');
  const chargesEsc = (lead.charges || '').replace(/"/g, '&quot;');
  const bondVal = lead.bond_amount != null ? lead.bond_amount : 0;
  const courtVal = (lead.court_date || 'TBN').replace(/"/g, '&quot;');
  const caseVal = (lead.case_number || '').replace(/"/g, '&quot;');
  const countyVal = (lead.county || 'Lee').replace(/"/g, '&quot;');
  const dobVal = (lead.dob || '').replace(/"/g, '&quot;');

  modal.innerHTML = `
    <div style="background:#0f172a;border:1px solid #1e293b;border-radius:12px;width:100%;max-width:540px;padding:24px;color:#f8fafc;box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);box-sizing:border-box;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;border-bottom:1px solid #334155;padding-bottom:12px">
        <h3 style="margin:0;font-size:18px;font-weight:700;color:#f8fafc">✏️ Edit Defendant & Lead Details</h3>
        <button onclick="document.getElementById('editLeadModal').style.display='none'" style="background:none;border:none;color:#94a3b8;font-size:20px;cursor:pointer">&times;</button>
      </div>
      <form onsubmit="saveEditLeadDetails(event, '${bkEsc}'); return false;" style="display:flex;flex-direction:column;gap:12px">
        <div>
          <label style="font-size:12px;color:#94a3b8;display:block;margin-bottom:4px">Booking Number</label>
          <input type="text" value="${bkEsc}" readonly style="width:100%;padding:8px 12px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#94a3b8;box-sizing:border-box" />
        </div>
        <div>
          <label style="font-size:12px;color:#94a3b8;display:block;margin-bottom:4px">Full Name (First, Middle, Last)</label>
          <input type="text" id="elmFullName" value="${nameEsc}" placeholder="e.g. D'Angelo Marquis Jones" style="width:100%;padding:8px 12px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f8fafc;box-sizing:border-box" />
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div>
            <label style="font-size:12px;color:#94a3b8;display:block;margin-bottom:4px">Bond Amount ($)</label>
            <input type="number" step="0.01" id="elmBondAmount" value="${bondVal}" style="width:100%;padding:8px 12px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f8fafc;box-sizing:border-box" />
          </div>
          <div>
            <label style="font-size:12px;color:#94a3b8;display:block;margin-bottom:4px">County</label>
            <input type="text" id="elmCounty" value="${countyVal}" placeholder="Lee, Pinellas, etc." style="width:100%;padding:8px 12px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f8fafc;box-sizing:border-box" />
          </div>
        </div>
        <div>
          <label style="font-size:12px;color:#94a3b8;display:block;margin-bottom:4px">Charges</label>
          <textarea id="elmCharges" rows="2" style="width:100%;padding:8px 12px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f8fafc;box-sizing:border-box">${chargesEsc}</textarea>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
              <label style="font-size:12px;color:#94a3b8">Court Date</label>
              <button type="button" onclick="document.getElementById('elmCourtDate').value='TBN'" style="background:#3b82f6;border:none;color:#fff;border-radius:4px;font-size:10px;padding:2px 6px;cursor:pointer">Set TBN</button>
            </div>
            <input type="text" id="elmCourtDate" value="${courtVal}" placeholder="YYYY-MM-DD or TBN" style="width:100%;padding:8px 12px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f8fafc;box-sizing:border-box" />
          </div>
          <div>
            <label style="font-size:12px;color:#94a3b8;display:block;margin-bottom:4px">Case Number</label>
            <input type="text" id="elmCaseNumber" value="${caseVal}" style="width:100%;padding:8px 12px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f8fafc;box-sizing:border-box" />
          </div>
        </div>
        <div>
          <label style="font-size:12px;color:#94a3b8;display:block;margin-bottom:4px">Date of Birth</label>
          <input type="text" id="elmDob" value="${dobVal}" placeholder="YYYY-MM-DD" style="width:100%;padding:8px 12px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#f8fafc;box-sizing:border-box" />
        </div>
        <div style="display:flex;justify-content:flex-end;gap:12px;margin-top:12px;padding-top:12px;border-top:1px solid #334155">
          <button type="button" onclick="document.getElementById('editLeadModal').style.display='none'" style="padding:8px 16px;background:#334155;border:none;border-radius:6px;color:#f8fafc;cursor:pointer;font-weight:600">Cancel</button>
          <button type="submit" id="saveEditLeadBtn" style="padding:8px 20px;background:#16a34a;border:none;border-radius:6px;color:#fff;cursor:pointer;font-weight:700">Save Changes</button>
        </div>
      </form>
    </div>
  `;
  modal.style.display = 'flex';
}

async function saveEditLeadDetails(evt, bookingNumber) {
  if (evt) evt.preventDefault();
  const btn = document.getElementById('saveEditLeadBtn');
  if (btn) { btn.disabled = true; btn.innerHTML = 'Saving…'; }

  try {
    const payload = {
      booking_number: bookingNumber,
      full_name: (document.getElementById('elmFullName')?.value || '').trim(),
      charges: (document.getElementById('elmCharges')?.value || '').trim(),
      bond_amount: parseFloat(document.getElementById('elmBondAmount')?.value || 0),
      county: (document.getElementById('elmCounty')?.value || '').trim(),
      court_date: (document.getElementById('elmCourtDate')?.value || '').trim() || 'TBN',
      case_number: (document.getElementById('elmCaseNumber')?.value || '').trim(),
      dob: (document.getElementById('elmDob')?.value || '').trim(),
      changed_by: document.getElementById('outreachAgent')?.value || 'dashboard_user'
    };

    const r = await fetch(`${API}/api/leads/update-lead-details`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await r.json();

    if (!r.ok || !d.success) {
      toast(d.error || 'Failed to update details', 'error');
      return;
    }

    toast('✅ Defendant details updated successfully!', 'success');
    _applyFullLeadToCard(bookingNumber, d.data || payload);

    const modal = document.getElementById('editLeadModal');
    if (modal) modal.style.display = 'none';

    if (typeof loadDefendants === 'function') loadDefendants();
  } catch (e) {
    toast('Error updating details: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = 'Save Changes'; }
  }
}

window.openEditLeadModal = openEditLeadModal;
window.saveEditLeadDetails = saveEditLeadDetails;


/**
 * openBondFromActiveBond — Opens the bond modal pre-populated from an existing active bond.
 * Automatically pre-selects the correct surety (OSI vs Palmetto) so the SignNow
 * template set is correct before Phase 1 / Phase 2 is triggered.
 *
 * @param {Object} bond - The active bond document from the active-bonds table
 */
function openBondFromActiveBond(bond) {
  if (!bond) return;
  const syntheticLead = {
    full_name:      bond.defendant_name || '',
    bond_amount:    bond.bond_amount || 0,
    county:         bond.county || '',
    booking_number: bond.booking_number || '',
    charges:        bond.charges || '',
  };
  openBondModal(syntheticLead);
  // Pre-select the surety after the modal renders
  const rawSurety = (bond.insurance_company || bond.surety || 'osi').toLowerCase();
  const surety = (rawSurety.includes('palm') || rawSurety.includes('psc')) ? 'palmetto' : 'osi';
  setTimeout(() => {
    selectSurety(surety);
    // Scroll to DocuSeal e-sign section
    const snSection = document.getElementById('docusealSection') || document.getElementById('signnowSection');
    if (snSection) snSection.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, 150);
}
window.openBondFromActiveBond = openBondFromActiveBond;

async function triggerDocuSealPacket() {
  const data = window._bondModalData;
  if (!data) { toast('No bond data', 'error'); return; }
  const snStatus = document.getElementById('sn-status');
  const phaseBadge = document.getElementById('sn-phase-badge');
  const poaInput = document.getElementById('poaInput_0');
  const poaNumber = poaInput ? poaInput.value.trim() : '';

  if (snStatus) snStatus.textContent = 'Creating DocuSeal submission...';
  try {
    let signerEmail = (data.lead && (data.lead.indemnitor_email || data.lead.email)) || '';
    let signerName = (data.lead && (data.lead.indemnitor_name || data.lead.full_name)) || '';
    if (data.indemnitors && data.indemnitors[0]) {
      signerEmail = signerEmail || data.indemnitors[0].email || '';
      signerName = signerName || data.indemnitors[0].name || '';
    }
    if (!signerEmail) {
      signerEmail = prompt('Enter indemnitor email for DocuSeal signature:') || '';
      if (!signerEmail) { if (snStatus) snStatus.textContent = 'Cancelled.'; return; }
      signerName = prompt('Enter indemnitor full name:') || 'Indemnitor';
    }

    // Prefer live charge grid from modal (POA / case / amounts per charge)
    const chargeDetails = (typeof _collectAppearanceBondChargesFromModal === 'function')
      ? _collectAppearanceBondChargesFromModal()
      : [];

    const payload = {
      booking_number: data.booking,
      county: data.county || (data.lead && data.lead.county) || '',
      surety_id: data.surety || 'osi',
      poa_number: poaNumber,
      provider: 'docuseal',
      signer_email: signerEmail,
      include_payment_plan: true,
      include_defendant: true,
      send_email: false,
      charge_details: chargeDetails,
      field_overrides: {
        defendant_name: data.name || (data.lead && (data.lead.full_name || data.lead.defendant_name)) || '',
        indemnitor_name: signerName,
        indemnitor_email: signerEmail,
        case_number: (document.getElementById('caseNumInput_0')?.value || '').trim()
          || (data.lead && data.lead.case_number) || data.booking || '',
        poa_number: poaNumber,
        bond_amount: data.bond || 0,
        booking_number: data.booking || '',
        defendant_address: (data.lead && (data.lead.address || data.lead.home_address)) || '',
        court_date: (data.lead && data.lead.court_date) || 'TBN',
      },
    };
    if (data.intake_id) payload.intake_id = data.intake_id;
    if (data.lead && data.lead._intake_id) payload.intake_id = data.lead._intake_id;

    const r = await fetch(`${API}/api/paperwork/packet/finalize`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await r.json().catch(() => ({}));
    // finalize returns { success: true, status: 'pending_signature', send_results: { docuseal: { success, signing_link } } }
    const ds = (result.send_results && result.send_results.docuseal) || {};
    const ok = r.ok && (result.success === true || ds.success === true);
    if (ok) {
      const link = result.signing_link || ds.signing_link
        || (ds.sign_links && ds.sign_links[0])
        || (ds.submitters && ds.submitters[0] && ds.submitters[0].sign_url)
        || '';
      const links = ds.sign_links || (ds.submitters || []).map(s => s.sign_url).filter(Boolean);
      const portalBase = 'https://paperwork.shamrockbailbonds.biz';
      const ipadUrl = link
        ? `${portalBase}/?mode=ipad&link=${encodeURIComponent(link)}`
        : `${portalBase}/?mode=ipad`;
      let linkHtml = link
        ? `<a href="${link}" target="_blank" rel="noopener" style="color:#22c55e;font-weight:bold;text-decoration:underline;margin-left:8px">Open indemnitor link</a>`
          + `<a href="${ipadUrl}" target="_blank" rel="noopener" style="color:#93c5fd;font-weight:700;text-decoration:underline;margin-left:10px">✍️ Open on iPad (in-person)</a>`
        : ' <span style="color:#fbbf24">(no link returned — check DOCUSEAL_API_KEY / template)</span>';
      if (links.length > 1) {
        linkHtml += links.slice(1).map((u, i) =>
          ` <a href="${u}" target="_blank" rel="noopener" style="color:#93c5fd;margin-left:6px">Signer ${i + 2}</a>`
          + ` <a href="${portalBase}/?mode=ipad&link=${encodeURIComponent(u)}" target="_blank" rel="noopener" style="color:#86efac;margin-left:4px">iPad</a>`
        ).join('');
      }
      if (snStatus) {
        snStatus.innerHTML = `✅ <strong>DocuSeal packet ready</strong> · ${result.packet_id || ''}${linkHtml}`
          + `<div style="margin-top:8px;font-size:11px;color:var(--muted)">In-person: open the iPad link on the office tablet · Apple Pencil works in the white form area · or send the PIN portal link to the indemnitor phone</div>`;
      }
      if (phaseBadge) {
        phaseBadge.textContent = 'DocuSeal Live';
        phaseBadge.style.background = 'rgba(34,197,94,0.2)';
        phaseBadge.style.color = '#22c55e';
      }
      toast(link ? 'DocuSeal e-sign link generated!' : 'DocuSeal packet created (check template/API)', link ? 'success' : 'warning');
      if (link) {
        try { await navigator.clipboard.writeText(link); toast('Signing link copied', 'info'); } catch (_) { /* ignore */ }
      }
    } else {
      const err = result.error || ds.error || ds.hint || `DocuSeal send failed (HTTP ${r.status})`;
      if (snStatus) snStatus.textContent = `❌ ${err}`;
      toast(err, 'error');
    }
  } catch (e) {
    if (snStatus) snStatus.textContent = `❌ Network error: ${e.message}`;
    toast('Network error', 'error');
  }
}
window.triggerDocuSealPacket = triggerDocuSealPacket;

async function triggerSignNowPacket() {
  const data = window._bondModalData;
  if (!data) { toast('No bond data', 'error'); return; }
  const snStatus = document.getElementById('sn-status');
  const phaseBadge = document.getElementById('sn-phase-badge');
  const poaInput = document.getElementById('poaInput_0');
  const poaNumber = poaInput ? poaInput.value.trim() : '';
  
  const routingScenario = document.getElementById('routingScenarioSelect').value;
  
  if (routingScenario === 'all-in-one' && !poaNumber) {
    toast('Enter POA number before sending All-in-One packet', 'error'); return;
  }
  
  // Get checked documents for custom manifest
  const checkedDocs = Array.from(document.querySelectorAll('.doc-chk:checked')).map(el => el.value);
  
  if (snStatus) snStatus.textContent = 'Preparing SignNow packet...';
  try {
    let signerEmail = data.lead.indemnitor_email || '';
    let signerName = data.lead.indemnitor_name || '';
    if (!signerEmail) {
      signerEmail = prompt('Enter indemnitor email:') || '';
      if (!signerEmail) { if (snStatus) snStatus.textContent = 'Cancelled.'; return; }
      signerName = prompt('Enter indemnitor full name:') || 'Indemnitor';
    }
    
    // For Phase 1_2, we hit the phase1 endpoint for now. For all-in-one, we hit generate-packet directly.
    // Actually, let's just hit the generate-packet endpoint directly for everything, since the backend handles it.
    // Let's create a new unified endpoint or just use generate-packet.
    
    const payload = {
      intake_id: data.lead._intake_id || '',
      booking_number: data.booking,
      signer_email: signerEmail,
      signer_name: signerName,
      agent_name: 'Brendan O\'Shaughnahill',
      agent_license: 'P322089',
      surety_id: data.surety || 'osi',
      poa_number: poaNumber,
      routing_scenario: routingScenario,
      custom_manifest: checkedDocs,
      form_data: {
        defendant: data.lead,
        booking_number: data.booking,
        bond_amount: data.bond,
        surety: data.surety,
        charges: (data.chargeList || []).map((ch, i) => {
          const poaInp = document.getElementById(`poaInput_${i}`);
          const caseInp = document.getElementById(`caseNumInput_${i}`);
          const amtInp = document.getElementById(`chargeAmtInput_${i}`);
          const countyInp = document.getElementById(`countyInput_${i}`);
          const courtDateInp = document.getElementById(`courtDateInput_${i}`);
          return {
            charge: ch,
            poa_number: (poaInp ? poaInp.value.trim() : '') || (data.poaNumbers && data.poaNumbers[i] ? data.poaNumbers[i].poa_full : ''),
            case_number: (caseInp ? caseInp.value.trim() : '') || data.booking || '',
            bond_amount: amtInp ? (parseFloat(amtInp.value) || 0) : data.bond,
            county: (countyInp ? countyInp.value.trim() : '') || data.county || 'Lee',
            court_date: (courtDateInp ? courtDateInp.value.trim() : '') || (data.lead && data.lead.court_date) || 'TBN'
          };
        }),
      }
    };
    
    const r = await fetch(`${API}/api/generate-packet`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await r.json();
    if (result.status === 'success') {
      if (snStatus) snStatus.innerHTML = `✅ Packet sent to ${signerEmail} (${result.manifest_size || checkedDocs.length} docs). <a href="${result.signing_link || '#'}" target="_blank" style="color:#60a5fa;text-decoration:underline;margin-left:8px">Open Signing Link</a>`;
      if (phaseBadge) { phaseBadge.textContent = 'Packet Sent'; phaseBadge.style.background = 'rgba(59,130,246,0.2)'; phaseBadge.style.color = '#60a5fa'; }
      toast('Packet sent', 'success');
    } else {
      if (snStatus) snStatus.textContent = `❌ ${result.error || 'Packet creation failed'}`;
      toast(result.error || 'Packet creation failed', 'error');
    }
  } catch(e) {
    if (snStatus) snStatus.textContent = `❌ Network error: ${e.message}`;
    toast('Network error', 'error');
  }
}

async function promptAdminPinOverride(bookingNumber) {
  const pin = prompt('🔑 Enter Admin PIN to approve posting before all paperwork is complete (logs 24-hour compliance deadline):');
  if (!pin) return;

  const reason = prompt('Reason for override / notes:', 'Admin override for immediate bond posting') || 'Admin override';
  const approvedBy = prompt('Admin Name:', 'Admin User') || 'Admin';

  try {
    const res = await fetch(`${API}/api/bonds/admin-pin-override`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pin: pin.trim(),
        booking_number: bookingNumber,
        reason: reason.trim(),
        approved_by: approvedBy.trim()
      })
    });
    const d = await res.json();
    if (!res.ok || !d.success) {
      toast(d.error || 'Invalid Admin PIN', 'error');
      return;
    }
    toast('✅ Admin PIN verified! Bond approved for posting (24-hour compliance deadline logged).', 'success');
    if (typeof loadDefendants === 'function') loadDefendants();
  } catch (err) {
    toast('Error verifying PIN: ' + err.message, 'error');
  }
}
window.promptAdminPinOverride = promptAdminPinOverride;

// ═══════════════════════════════════════════════════════════════════════════════
// COMPETITIVE SUPER-FEATURES: FRONTEND CONTROLLERS
// ═══════════════════════════════════════════════════════════════════════════════

window.SLFeatures = window.SLFeatures || {};

SLFeatures.openBordereauModal = function() {
  document.getElementById('bordereauModal').style.display = 'flex';
};

SLFeatures.previewBordereauData = async function() {
  const surety = document.getElementById('bordereauSuretySelect').value;
  const year = document.getElementById('bordereauYearInput').value;
  const month = document.getElementById('bordereauMonthSelect').value;
  const box = document.getElementById('bordereauSummaryPreview');

  box.style.display = 'block';
  box.innerHTML = '<i>Loading live Bordereau calculations...</i>';

  try {
    const res = await fetch(`${API}/api/reports/bordereau?surety=${surety}&year=${year}&month=${month}&fmt=json`);
    const d = await res.json();
    if (!res.ok) {
      box.innerHTML = `<span style="color:#ef4444">Error loading summary: ${d.error || 'Unknown error'}</span>`;
      return;
    }
    const s = d.summary || {};
    box.innerHTML = `
      <div style="font-weight:600;margin-bottom:8px;color:#22c55e">📊 ${d.surety_name} — Reporting Period ${d.reporting_period}</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
        <div>Total Bonds Executed: <strong>${s.total_bonds_executed || 0}</strong></div>
        <div>Total Bond Amount Written: <strong>$${(s.total_bond_amount || 0).toLocaleString('en-US', {minimumFractionDigits:2})}</strong></div>
        <div>Gross Premium (10%): <strong>$${(s.total_gross_premium || 0).toLocaleString('en-US', {minimumFractionDigits:2})}</strong></div>
        <div>BUF Escrow Deduction (1%): <strong>$${(s.total_buf_escrow_1pct || 0).toLocaleString('en-US', {minimumFractionDigits:2})}</strong></div>
        <div>Net Owed to Surety: <strong style="color:#f59e0b">$${(s.total_net_surety_owed || 0).toLocaleString('en-US', {minimumFractionDigits:2})}</strong></div>
      </div>
    `;
  } catch (err) {
    box.innerHTML = `<span style="color:#ef4444">Network error: ${err.message}</span>`;
  }
};

SLFeatures.downloadBordereauCSV = function() {
  const surety = document.getElementById('bordereauSuretySelect').value;
  const year = document.getElementById('bordereauYearInput').value;
  const month = document.getElementById('bordereauMonthSelect').value;
  window.open(`${API}/api/reports/bordereau?surety=${surety}&year=${year}&month=${month}&fmt=csv`, '_blank');
};

SLFeatures.openCollateralModal = function(bookingNumber, defName) {
  document.getElementById('collateralModal').style.display = 'flex';
  if (bookingNumber) document.getElementById('colBookingNum').value = bookingNumber;
  if (defName) document.getElementById('colDefName').value = defName;
  SLFeatures.loadCollateralList(bookingNumber);
};

SLFeatures.saveCollateralItem = async function() {
  const data = {
    booking_number: document.getElementById('colBookingNum').value.trim(),
    defendant_name: document.getElementById('colDefName').value.trim(),
    depositor_name: document.getElementById('colDepName').value.trim(),
    item_type: document.getElementById('colType').value,
    estimated_value: parseFloat(document.getElementById('colVal').value || 0),
    storage_location: document.getElementById('colLoc').value.trim(),
    description: document.getElementById('colDesc').value.trim()
  };

  if (!data.booking_number || !data.defendant_name) {
    toast('Please enter Booking Number and Defendant Name', 'error');
    return;
  }

  try {
    const res = await fetch(`${API}/api/collateral/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    const d = await res.json();
    if (!res.ok || !d.success) {
      toast(d.error || 'Failed to record collateral', 'error');
      return;
    }
    toast(`✅ Collateral Tag #${d.item.tag_number} recorded in vault!`, 'success');
    document.getElementById('colDesc').value = '';
    SLFeatures.loadCollateralList(data.booking_number);
  } catch (err) {
    toast('Error recording collateral: ' + err.message, 'error');
  }
};

SLFeatures.loadCollateralList = async function(bookingNumber) {
  const container = document.getElementById('collateralItemsTable');
  container.innerHTML = '<i>Loading items...</i>';

  try {
    const url = bookingNumber ? `${API}/api/collateral?booking_number=${bookingNumber}` : `${API}/api/collateral`;
    const res = await fetch(url);
    const d = await res.json();
    if (!res.ok || !d.items || d.items.length === 0) {
      container.innerHTML = '<span style="color:var(--text-muted)">No collateral items currently recorded.</span>';
      return;
    }
    container.innerHTML = d.items.map(i => `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.1)">
        <div>
          <strong>${i.tag_number}</strong> — ${i.item_type} ($${(i.estimated_value || 0).toLocaleString()})
          <div style="color:var(--text-muted);font-size:11px">${i.defendant_name} | Depositor: ${i.depositor_name || 'N/A'} | ${i.storage_location}</div>
        </div>
        <div>
          ${i.status === 'returned'
            ? `<span style="color:#22c55e;font-size:11px;font-weight:600">✅ Returned</span>`
            : `<button class="btn-secondary" style="font-size:11px;padding:4px 8px" onclick="SLFeatures.returnCollateralItem('${i.collateral_id}')">Return & Print PDF Receipt</button>`
          }
        </div>
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = '<span style="color:#ef4444">Error loading collateral items</span>';
  }
};

SLFeatures.returnCollateralItem = async function(id) {
  if (!confirm('Mark this collateral item as returned to depositor and generate PDF receipt?')) return;
  try {
    const res = await fetch(`${API}/api/collateral/return/${id}`, { method: 'POST' });
    const d = await res.json();
    if (!res.ok || !d.success) {
      toast(d.error || 'Failed to update collateral', 'error');
      return;
    }
    toast('✅ Collateral marked as returned! Opening PDF Return Receipt...', 'success');
    window.open(`${API}/api/collateral/receipt-pdf/${id}`, '_blank');
    SLFeatures.loadCollateralList();
  } catch (err) {
    toast('Error returning collateral: ' + err.message, 'error');
  }
};

SLFeatures.openRemittiturModal = async function() {
  document.getElementById('remittiturModal').style.display = 'flex';
  const container = document.getElementById('remittiturClocksList');
  container.innerHTML = '<i>Calculating F.S. 903.26 remittitur countdown clocks...</i>';

  try {
    const res = await fetch(`${API}/api/forfeitures/remittitur-clock`);
    const d = await res.json();
    if (!res.ok || !d.forfeitures || d.forfeitures.length === 0) {
      container.innerHTML = '<span style="color:var(--text-muted)">No active bond forfeitures currently logged.</span>';
      return;
    }
    container.innerHTML = d.forfeitures.map(f => `
      <div style="background:rgba(15,23,42,0.6);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <strong style="font-size:14px;color:#ef4444">⚠️ ${f.defendant_name} (Booking #${f.booking_number})</strong>
          <span style="background:${f.urgency_level === 'CRITICAL' ? '#ef4444' : '#f59e0b'};color:#000;font-size:10px;font-weight:bold;padding:2px 8px;border-radius:10px">${f.urgency_level} URGENCY</span>
        </div>
        <div style="margin-top:6px;font-size:12px;display:grid;grid-template-columns:1fr 1fr;gap:6px;color:var(--text-muted)">
          <div>Bond Amount: <strong style="color:#fff">$${(f.bond_amount || 0).toLocaleString()}</strong></div>
          <div>POA #: <strong style="color:#fff">${f.poa_number}</strong></div>
          <div>60-Day Remittitur Deadline: <strong style="color:#22c55e">${f.deadline_60d} (${f.days_left_60d} days left)</strong></div>
          <div>Remittitur Return Rate: <strong style="color:#38bdf8">${f.remittitur_percentage}% ($${(f.potential_remittitur_amount || 0).toLocaleString()})</strong></div>
        </div>
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = '<span style="color:#ef4444">Error loading remittitur clocks</span>';
  }
};

SLFeatures.sendMobileCheckinLink = async function(bookingNumber, phone) {
  if (!bookingNumber) {
    toast('No booking number specified', 'error');
    return;
  }
  try {
    const res = await fetch(`${API}/api/checkin/create-request`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ booking_number: bookingNumber, defendant_phone: phone || '' })
    });
    const d = await res.json();
    if (!res.ok || !d.success) {
      toast(d.error || 'Failed to create check-in request', 'error');
      return;
    }
    const checkinUrl = d.checkin_request.checkin_url;
    toast(`✅ Mobile Check-In URL generated: ${checkinUrl}`, 'success');

    if (phone && confirm(`Send mobile check-in link to ${phone} via BlueBubbles iMessage/SMS now?`)) {
      const msgRes = await fetch(`${API}/api/imessage/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone: phone,
          message: `Shamrock Bail Bonds Mandatory Check-In: Please click your single-use link to submit your selfie & location check-in: ${checkinUrl}`
        })
      });
      const msgD = await msgRes.json();
      if (msgD.success) toast('✅ Check-in link dispatched via iMessage/SMS!', 'success');
    }
  } catch (err) {
    toast('Error dispatching check-in link: ' + err.message, 'error');
  }
};

// ── Per-Charge Bond Breakdown Modal ─────────────────────────────────────────
let _currentChargeBondsBooking = null;

async function openChargeBondsModal(bookingNumber) {
  if (!bookingNumber) { toast('Missing booking number', 'error'); return; }
  _currentChargeBondsBooking = bookingNumber;

  const modal = document.getElementById('chargeBondsModal');
  const body = document.getElementById('chargeBondsModalBody');
  if (!modal || !body) return;

  modal.style.display = 'flex';
  modal.classList.add('show');
  body.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:20px;text-align:center">Loading charges data...</div>';

  let lead = window._leadMap ? window._leadMap[bookingNumber] : null;
  if (!lead) {
    try {
      const r = await fetch(`${API}/api/leads/${encodeURIComponent(bookingNumber)}`);
      const d = await r.json();
      if (r.ok && d) lead = d;
    } catch (e) { console.error('Error fetching lead detail:', e); }
  }

  const name = lead?.full_name || 'Defendant';
  const county = lead?.county || '';
  const currentTotal = lead?.bond_amount || 0;
  const chargeDetails = Array.isArray(lead?.charge_details) && lead.charge_details.length ? lead.charge_details : null;
  const chargesRaw = lead?.charges || '';

  let items = [];
  if (chargeDetails) {
    items = chargeDetails;
  } else if (chargesRaw) {
    const split = chargesRaw.split(/\s*\|\s*/);
    items = split.map(c => ({ charge: c, bond_amount: 0, bond_type: 'Surety', case_number: '' }));
  } else {
    items = [{ charge: 'Charge 1', bond_amount: currentTotal, bond_type: 'Surety', case_number: '' }];
  }

  let rowsHtml = items.map((item, idx) => `
    <tr class="charge-bond-row" data-index="${idx}" style="border-bottom:1px solid var(--border,#334155)">
      <td style="padding:8px">
        <input type="text" class="ci-input charge-name-input" value="${(item.charge || '').replace(/"/g, '&quot;')}" style="width:100%;font-size:12px;background:rgba(0,0,0,0.3);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:4px 8px" placeholder="Offense description">
      </td>
      <td style="padding:8px">
        <input type="text" class="ci-input charge-case-input" value="${(item.case_number || '').replace(/"/g, '&quot;')}" style="width:100%;font-size:12px;background:rgba(0,0,0,0.3);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:4px 8px" placeholder="Case #">
      </td>
      <td style="padding:8px">
        <select class="ci-input charge-type-select" style="font-size:12px;background:rgba(0,0,0,0.3);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:4px 6px">
          <option value="Surety" ${item.bond_type === 'Surety' ? 'selected' : ''}>Surety</option>
          <option value="Cash" ${item.bond_type === 'Cash' ? 'selected' : ''}>Cash</option>
          <option value="No Bond" ${item.bond_type === 'No Bond' ? 'selected' : ''}>No Bond</option>
          <option value="ROR" ${item.bond_type === 'ROR' ? 'selected' : ''}>ROR</option>
        </select>
      </td>
      <td style="padding:8px">
        <div style="display:flex;align-items:center;gap:4px">
          <span style="color:var(--muted)">$</span>
          <input type="number" class="ci-input charge-bond-input" value="${item.bond_amount || 0}" min="0" step="100" style="width:110px;font-size:13px;font-weight:700;background:rgba(0,0,0,0.3);color:var(--emerald,#10b981);border:1px solid var(--border);border-radius:4px;padding:4px 8px" oninput="recalcChargeBondsTotal()">
        </div>
      </td>
      <td style="padding:8px;text-align:center">
        <button type="button" class="btn-cancel" style="padding:2px 8px;color:#fca5a5;border:1px solid rgba(239,68,68,0.4);border-radius:4px;background:rgba(239,68,68,0.1);cursor:pointer" onclick="removeChargeBondRow(this)">✕</button>
      </td>
    </tr>
  `).join('');

  body.innerHTML = `
    <div style="margin-bottom:16px;display:flex;justify-content:space-between;align-items:center">
      <div>
        <h3 style="margin:0;font-size:16px;color:var(--text,#f8fafc)">${name}</h3>
        <div style="font-size:12px;color:var(--muted,#94a3b8)">Booking #${bookingNumber} · ${county}</div>
      </div>
      <div style="text-align:right">
        <div style="font-size:11px;color:var(--muted,#94a3b8)">TOTAL BOND</div>
        <div id="cbModalTotal" style="font-size:20px;font-weight:800;color:var(--emerald,#10b981)">$${Number(currentTotal).toLocaleString()}</div>
      </div>
    </div>
    <div style="max-height:360px;overflow-y:auto;border:1px solid var(--border,#334155);border-radius:6px">
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead>
          <tr style="background:var(--bg-subtle,#1e293b);text-align:left;color:var(--muted,#94a3b8)">
            <th style="padding:8px 12px">Charge Description</th>
            <th style="padding:8px 12px;width:120px">Case #</th>
            <th style="padding:8px 12px;width:110px">Type</th>
            <th style="padding:8px 12px;width:130px">Bond Amount</th>
            <th style="padding:8px 12px;width:40px"></th>
          </tr>
        </thead>
        <tbody id="chargeBondsTableBody">
          ${rowsHtml}
        </tbody>
      </table>
    </div>
    <div style="margin-top:14px;display:flex;justify-content:space-between;align-items:center">
      <button type="button" class="btn-detail" style="font-size:11px;padding:4px 10px;background:rgba(59,130,246,0.2);color:#93c5fd;border:1px solid rgba(59,130,246,0.4);border-radius:4px" onclick="addChargeBondRow()">➕ Add Charge</button>
      <span style="font-size:11px;color:var(--muted,#94a3b8)">Total bond auto-sums from charge amounts.</span>
    </div>
  `;

  recalcChargeBondsTotal();
}

function closeChargeBondsModal() {
  const modal = document.getElementById('chargeBondsModal');
  if (modal) {
    modal.style.display = 'none';
    modal.classList.remove('show');
  }
}

function recalcChargeBondsTotal() {
  let total = 0;
  document.querySelectorAll('#chargeBondsTableBody .charge-bond-input').forEach(inp => {
    const val = parseFloat(inp.value) || 0;
    total += Math.max(0, val);
  });
  const el = document.getElementById('cbModalTotal');
  if (el) el.textContent = '$' + total.toLocaleString();
}

function addChargeBondRow() {
  const tbody = document.getElementById('chargeBondsTableBody');
  if (!tbody) return;
  const tr = document.createElement('tr');
  tr.className = 'charge-bond-row';
  tr.style.borderBottom = '1px solid var(--border,#334155)';
  tr.innerHTML = `
    <td style="padding:8px">
      <input type="text" class="ci-input charge-name-input" value="" style="width:100%;font-size:12px;background:rgba(0,0,0,0.3);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:4px 8px" placeholder="Offense description">
    </td>
    <td style="padding:8px">
      <input type="text" class="ci-input charge-case-input" value="" style="width:100%;font-size:12px;background:rgba(0,0,0,0.3);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:4px 8px" placeholder="Case #">
    </td>
    <td style="padding:8px">
      <select class="ci-input charge-type-select" style="font-size:12px;background:rgba(0,0,0,0.3);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:4px 6px">
        <option value="Surety" selected>Surety</option>
        <option value="Cash">Cash</option>
        <option value="No Bond">No Bond</option>
        <option value="ROR">ROR</option>
      </select>
    </td>
    <td style="padding:8px">
      <div style="display:flex;align-items:center;gap:4px">
        <span style="color:var(--muted)">$</span>
        <input type="number" class="ci-input charge-bond-input" value="0" min="0" step="100" style="width:110px;font-size:13px;font-weight:700;background:rgba(0,0,0,0.3);color:var(--emerald,#10b981);border:1px solid var(--border);border-radius:4px;padding:4px 8px" oninput="recalcChargeBondsTotal()">
      </div>
    </td>
    <td style="padding:8px;text-align:center">
      <button type="button" class="btn-cancel" style="padding:2px 8px;color:#fca5a5;border:1px solid rgba(239,68,68,0.4);border-radius:4px;background:rgba(239,68,68,0.1);cursor:pointer" onclick="removeChargeBondRow(this)">✕</button>
    </td>
  `;
  tbody.appendChild(tr);
}

function removeChargeBondRow(btn) {
  const tr = btn.closest('tr');
  if (tr) tr.remove();
  recalcChargeBondsTotal();
}

async function saveChargeBondsFromModal() {
  if (!_currentChargeBondsBooking) return;
  const rows = document.querySelectorAll('#chargeBondsTableBody .charge-bond-row');
  const details = [];
  rows.forEach(tr => {
    const charge = tr.querySelector('.charge-name-input')?.value?.trim();
    const caseNum = tr.querySelector('.charge-case-input')?.value?.trim() || '';
    const bondType = tr.querySelector('.charge-type-select')?.value || 'Surety';
    const bondAmt = parseFloat(tr.querySelector('.charge-bond-input')?.value) || 0;
    if (charge) {
      details.push({
        charge: charge,
        bond_amount: bondAmt,
        bond_type: bondType,
        case_number: caseNum,
      });
    }
  });

  try {
    const r = await fetch(`${API}/api/leads/update-charge-bonds`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        booking_number: _currentChargeBondsBooking,
        charge_details: details,
        changed_by: document.getElementById('outreachAgent')?.value || 'dashboard_user',
      }),
    });
    const d = await r.json();
    if (!r.ok || d.success === false) {
      toast(d.error || 'Failed to save charge bonds', 'error');
      return;
    }

    const total = d.total_bond || 0;
    toast(`Saved per-charge bonds! Total bond: $${total.toLocaleString()}`, 'success');

    if (window._leadMap && window._leadMap[_currentChargeBondsBooking]) {
      window._leadMap[_currentChargeBondsBooking].bond_amount = total;
      window._leadMap[_currentChargeBondsBooking].charge_details = details;
      if (d.lead_score != null) window._leadMap[_currentChargeBondsBooking].lead_score = d.lead_score;
      if (d.lead_status) window._leadMap[_currentChargeBondsBooking].lead_status = d.lead_status;
    }

    const card = document.querySelector(`.def-card[data-booking="${CSS.escape ? CSS.escape(_currentChargeBondsBooking) : _currentChargeBondsBooking}"]`);
    if (card) {
      const pill = card.querySelector('.def-bond-pill');
      if (pill) {
        pill.textContent = total > 0 ? ('$' + total.toLocaleString()) : '$0 — set bond';
        pill.classList.toggle('bond-zero', total <= 0);
        const bc = total >= 10000 ? 'high' : total >= 2500 ? 'mid' : 'low';
        pill.className = 'def-bond-pill ' + bc + (total <= 0 ? ' bond-zero' : '');
      }
      const bondInp = card.querySelector('.def-bond-input');
      if (bondInp) bondInp.value = total > 0 ? total : '';
    }

    closeChargeBondsModal();
  } catch (e) {
    toast('Network error saving per-charge bonds', 'error');
  }
}

window.openChargeBondsModal = openChargeBondsModal;
window.closeChargeBondsModal = closeChargeBondsModal;
window.recalcChargeBondsTotal = recalcChargeBondsTotal;
window.addChargeBondRow = addChargeBondRow;
window.removeChargeBondRow = removeChargeBondRow;
window.saveChargeBondsFromModal = saveChargeBondsFromModal;
