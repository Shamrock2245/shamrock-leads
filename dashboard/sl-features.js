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
  const sort = document.getElementById('defSort')?.value || SL_STATE.defSort;
  const custody = document.getElementById('defCustody')?.value || '';
  const county = document.getElementById('defCountyFilter')?.value || '';
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
      const bkSafe = (l.booking_number||'').replace(/'/g,"\\'");
      const bkEscD = (l.booking_number||'').replace(/"/g,'&quot;');
      const custDrop = `<select class="def-status-badge ${stBadge}" style="cursor:pointer;border:1px solid var(--border);background:transparent;padding:2px 6px;font-size:11px;border-radius:6px" onchange="updateCustody('${bkEscD}',this.value,this)"><option value="" ${!stVal?'selected':''}>${stVal||'\u2014'}</option><option value="In Custody" ${'In Custody'===stVal?'selected':''}>In Custody</option><option value="Not In Custody" ${'Not In Custody'===stVal?'selected':''}>Not In Custody</option><option value="Released" ${'Released'===stVal?'selected':''}>Released</option><option value="Bonded Out" ${'Bonded Out'===stVal?'selected':''}>Bonded Out</option></select>`;
      return `<div class="def-card" data-booking="${bkEscD}">
        <div class="def-card-header"><div><div class="def-name">${l.full_name||'Unknown'}</div><div class="def-booking">${l.booking_number||'\u2014'}</div></div><div class="def-bond-pill ${bc}">$${bond.toLocaleString()}</div></div>
        <div class="def-body">
          <div class="def-section"><div class="def-section-title">📋 Details</div><div class="def-row"><div class="def-field"><span class="def-label">County</span><span class="def-value">${l.county||'\u2014'}</span></div><div class="def-field"><span class="def-label">DOB</span><span class="def-value">${l.dob||'\u2014'}</span></div><div class="def-field"><span class="def-label">Status</span>${custDrop}</div><div class="def-field"><span class="def-label">Score</span><span class="score-pill ${scoreCls}">${l.lead_score||0} ${l.lead_status||''}</span></div><div class="def-field"><span class="def-label">FTA Risk</span>${_ftaBadgeDef(l)||'<span style="font-size:11px;color:var(--text-muted)">—</span>'}</div></div></div>
          <div class="def-section"><div class="def-section-title">⚖️ Charges</div><div class="def-row wide"><div class="def-value" style="font-size:12px;white-space:normal">${l.charges||'\u2014'}</div></div></div>
        </div>
        <div class="def-card-footer">
          <button class="btn-detail" onclick="window.open('${l.detail_url||'#'}')">🔗 Source</button>
          <button class="slc-notes-btn" onclick="openShamrockNotes('${bkEscD}')" title="Shamrock Notes">📝 Notes</button>
          <button class="btn-imessage-send" onclick="SLiMessage&&SLiMessage.openCompose('${bkEscD}','${(l.full_name||'').replace(/'/g,"\'")}')" title="Send iMessage">💬 iMsg</button>
          <button class="btn-contact-indem" onclick="SLContact.openModal('${bkSafe}','${(l.full_name||'').replace(/'/g,"\\\\'")}',' ${l.county||''}',${bond},'${(l.booking_number||'')}')">📞 Contact</button>
          <button class="btn-track-lead" id="trackBtn_${bkEscD}" onclick="SLProspective.trackLead('${bkSafe}','${(l.full_name||'').replace(/'/g,"\\\\'")}','${l.county||''}',${bond},'${(l.charges||'').replace(/'/g,"\\\\'")}',${l.lead_score||0},'${l.lead_status||''}')">☘️ Track</button>
            <button class="btn-write-bond" onclick="openBondModal(window._leadMap['${bkSafe}'] || {full_name:'${(l.full_name||'').replace(/'/g,"\\'")}'}, ${bond}, '${l.county||''}', '${bkSafe}')">✍️ Bond</button>
          <button class="btn-lifecycle" onclick="SLLifecycle&&SLLifecycle.open('${bkSafe}',{defendantName:'${(l.full_name||'').replace(/'/g,"\\'")}'})" title="Full bond lifecycle timeline">☘️ Life</button>
        </div>    </div>
      </div>`;
    }).join('') || '<div class="loading">No defendants found</div>';

    // Defendant pagination
    document.getElementById('defPagination').innerHTML = `<button ${SL_STATE.defPage<=1?'disabled':''} onclick="goDefPage(${SL_STATE.defPage-1})">← Prev</button><span>Page ${SL_STATE.defPage} of ${pages}</span><button ${SL_STATE.defPage>=pages?'disabled':''} onclick="goDefPage(${SL_STATE.defPage+1})">Next →</button>`;
  } catch(e) { console.error('loadDefendants error:', e); }
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

// ── Write Bond Modal ──
// openBondModal accepts a full lead object OR individual fields for backwards compat
function openBondModal(nameOrLead, bond, county, booking) {
  let lead = {};
  if (typeof nameOrLead === 'object' && nameOrLead !== null) {
    lead = nameOrLead;
  } else {
    // Legacy call: openBondModal(name, bond, county, booking)
    lead = { full_name: nameOrLead, bond_amount: bond, county: county, booking_number: booking };
  }

  const name = lead.full_name || 'Unknown';
  const bondAmt = parseFloat(lead.bond_amount || bond || 0);
  const cnty = lead.county || county || '';
  const bkNum = lead.booking_number || booking || '';
  const premium = Math.max(100, bondAmt * 0.1);
  const transferFee = (bondAmt > 25000 || ['Lee','Charlotte'].includes(cnty)) ? 0 : 125;

  // Parse charges into individual bonds (one per charge)
  const chargesRaw = lead.charges || '';
  const chargeList = chargesRaw ? chargesRaw.split('|').map(c => c.trim()).filter(Boolean) : ['Unspecified Charge'];

  document.getElementById('bondModal').classList.add('show');

  document.getElementById('bondModalBody').innerHTML = `
    <div class="wb-section">
      <div class="wb-section-label">Defendant Summary</div>
      <div class="wb-defendant-summary">
        <div class="wb-name">${name}</div>
        <div class="wb-meta-grid">
          <div><span class="wb-meta-label">County</span>${cnty}</div>
          <div><span class="wb-meta-label">Booking #</span>${bkNum}</div>
          <div><span class="wb-meta-label">Bond Amount</span>$${bondAmt.toLocaleString()}</div>
          <div><span class="wb-meta-label">Est. Premium</span><strong style="color:var(--success)">$${premium.toLocaleString()}</strong></div>
          <div><span class="wb-meta-label">Transfer Fee</span>${transferFee ? '$'+transferFee : '<span style="color:var(--success)">Waived</span>'}</div>
          <div><span class="wb-meta-label">Total Due</span><strong>$${(premium + transferFee).toLocaleString()}</strong></div>
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
      <div class="wb-section-label">Appearance Bonds — One Per Charge (${chargeList.length})</div>
      <div id="pdfPreviewArea" style="background:var(--panel);border-radius:8px;padding:16px">
        <p style="color:var(--muted);margin:0 0 12px;font-size:12px">One blank Appearance Bond will be generated per charge, pre-populated with defendant info.</p>
        <div id="chargeBondList" style="display:flex;flex-direction:column;gap:8px">
          ${chargeList.map((ch, i) => `
            <div class="charge-bond-row" style="display:flex;align-items:center;gap:10px;padding:8px;background:var(--bg);border-radius:6px">
              <span class="charge-bond-num" style="font-size:11px;color:var(--muted);min-width:20px">#${i+1}</span>
              <span class="charge-bond-desc" style="flex:1;font-size:12px">${ch}</span>
              <button class="btn-export" style="font-size:11px;padding:4px 10px" onclick="downloadBond('${encodeURIComponent(ch)}', ${i+1})">📄 Bond</button>
            </div>`).join('')}
        </div>
        <div style="margin-top:12px;text-align:center">
          <button class="btn-export" onclick="downloadAllBonds()">Download All (${chargeList.length}) Bonds</button>
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

    <div class="wb-section" id="signnowSection">
      <div class="wb-section-label" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        📝 SignNow Packet
        <span id="sn-phase-badge" style="font-size:11px;padding:2px 8px;border-radius:10px;background:var(--panel);color:var(--muted)">Not Sent</span>
        <span id="sn-surety-badge" style="font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(59,130,246,0.12);color:#60a5fa;margin-left:auto">🛡️ OSI Templates</span>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
        <button class="btn-export" id="btnPhase1" onclick="triggerSignNowPhase1()" style="background:rgba(59,130,246,0.15);color:#60a5fa">📨 Send Phase 1 (Indemnitor)</button>
        <button class="btn-export" id="btnPhase2" onclick="triggerSignNowPhase2()" style="background:rgba(34,197,94,0.15);color:var(--success)" disabled>📨 Send Phase 2 (Post-Approval)</button>
      </div>
      <div id="sn-status" style="margin-top:8px;font-size:12px;color:var(--muted)"></div>
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
  window._bondModalData = {
    lead,
    name, bond: bondAmt, county: cnty, booking: bkNum,
    charges: chargesRaw, chargeList,
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

    // Build per-charge POA input rows
    // If we have fewer suggestions than charges, fill remaining with empty inputs
    const poaRows = chargeList.map((ch, i) => {
      const sug = suggested[i];
      const poaFull = sug ? sug.poa_full : '';
      const poaNum  = sug ? sug.poa_number : '';
      const poaPfx  = sug ? sug.poa_prefix : prefix;
      return `
        <div class="poa-charge-row" style="display:flex;align-items:center;gap:10px;padding:8px;background:var(--bg);border-radius:6px">
          <span style="font-size:11px;color:var(--muted);min-width:20px">#${i+1}</span>
          <span style="flex:1;font-size:11px;color:var(--text)">${ch.length > 50 ? ch.slice(0,50)+'…' : ch}</span>
          <div style="display:flex;flex-direction:column;gap:2px;min-width:160px">
            <label style="font-size:10px;color:var(--muted)">POA Number</label>
            <input
              class="poa-input"
              id="poaInput_${i}"
              data-charge-idx="${i}"
              data-poa-prefix="${poaPfx}"
              data-poa-number="${poaNum}"
              value="${poaFull}"
              placeholder="${prefix} ______"
              style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:12px;font-family:monospace;width:140px"
              oninput="onPoaInputChange(this, ${i})"
            />
          </div>
        </div>`;
    });

    listEl.innerHTML = poaRows.join('');

    // Store in modal data
    window._bondModalData.poaNumbers = chargeList.map((_, i) => {
      const sug = suggested[i];
      return sug ? { poa_full: sug.poa_full, poa_number: sug.poa_number, poa_prefix: sug.poa_prefix } : { poa_full: '', poa_number: '', poa_prefix: prefix };
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
    // Show empty manual inputs as fallback
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

function onPoaInputChange(input, idx) {
  // Update the stored poa number when user manually edits
  const val = input.value.trim();
  if (window._bondModalData && window._bondModalData.poaNumbers) {
    window._bondModalData.poaNumbers[idx] = {
      poa_full: val,
      poa_number: val.includes(' ') ? val.split(' ').pop() : val,
      poa_prefix: val.includes(' ') ? val.split(' ')[0] : (input.dataset.poaPrefix || ''),
    };
  }
}

function downloadBond(chargeEncoded, idx) {
  const data = window._bondModalData;
  if (!data) return;
  const charge = decodeURIComponent(chargeEncoded);
  const surety = data.surety;
  const poaEntry = (data.poaNumbers && data.poaNumbers[idx - 1]) || {};
  // Read live value from input in case user overrode it
  const inputEl = document.getElementById(`poaInput_${idx - 1}`);
  const poaFull = (inputEl ? inputEl.value.trim() : '') || poaEntry.poa_full || '';
  const params = new URLSearchParams({
    name: data.name, booking: data.booking, county: data.county,
    bond: data.bond, charge, surety, date: data.date,
    dob: data.lead.dob || '', address: data.lead.address || '',
    poa_number: poaFull,
  });
  window.open(`${API}/api/appearance-bond-pdf?${params}`, '_blank');
}

function downloadAllBonds() {
  const data = window._bondModalData;
  if (!data) return;
  data.chargeList.forEach((ch, i) => {
    setTimeout(() => downloadBond(encodeURIComponent(ch), i+1), i * 400);
  });
}

function selectSurety(s) {
  window._bondModalData.surety = s;
  document.getElementById('suretyOSI').classList.toggle('active', s === 'osi');
  document.getElementById('suretyPalmetto').classList.toggle('active', s === 'palmetto');
  // Update the SignNow surety badge to reflect which template set will be used
  const snBadge = document.getElementById('sn-surety-badge');
  if (snBadge) {
    if (s === 'palmetto') {
      snBadge.textContent = '\uD83C\uDF34 Palmetto Templates';
      snBadge.style.background = 'rgba(34,197,94,0.12)';
      snBadge.style.color = '#22c55e';
    } else {
      snBadge.textContent = '\uD83D\uDEE1\uFE0F OSI Templates';
      snBadge.style.background = 'rgba(59,130,246,0.12)';
      snBadge.style.color = '#60a5fa';
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
      signer_email: signerEmail,
      signer_name: signerName,
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
      if (snStatus) snStatus.textContent = `✅ Phase 1 sent to ${signerEmail} (${result.manifest_size} docs)`;
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
      if (snStatus) snStatus.textContent = `✅ Phase 2 sent — POA ${poaNumber} (${result.manifest_size} docs)`;
      if (phaseBadge) { phaseBadge.textContent = 'Phase 2 Sent'; phaseBadge.style.background = 'rgba(34,197,94,0.2)'; phaseBadge.style.color = 'var(--success)'; }
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
  const lines = SL_STATE.leads.slice(0,20).map(l => `• *${l.full_name}* — ${l.county} — $${(l.bond_amount||0).toLocaleString()} — Score: ${l.lead_score||0} (${l.lead_status||''})`);
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
    document.querySelector('.multi-select-trigger')?.classList.remove('open');
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
  const county = document.getElementById('defCountyFilter')?.value || '';
  if (!county) {
    SL.toast('Select a county first to verify custody', 'error');
    return;
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
window.SL = { toggleTheme, switchTab, toggleCountyDropdown, filterCountyOptions, toggleCounty,
  applyPreset, setDays, setBond, setDefBond, sortBy, debounceSearch, debounceDefSearch, applyFilters,
  goPage, goDefPage, openBondModal, openWriteBond, selectSurety, closeModal, submitBond, exportCSV, copyToSlack,
  clearAll, refresh, toast, loadDefendants, downloadBond, downloadAllBonds, registerActiveBond,
  sendOutreach, loadOutreachHistory, checkBBStatus, updateCustody,
  triggerSignNowPhase1, triggerSignNowPhase2,
  triggerCustodyRecheck, closeRecheckBanner,
  saveCurrentView, loadSavedView, populateSavedViews };

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
    // Scroll to the SignNow section
    const snSection = document.getElementById('signnowSection');
    if (snSection) snSection.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, 150);
}
window.openBondFromActiveBond = openBondFromActiveBond;

// ── Init ──
loadDashboard();
populateSavedViews();
