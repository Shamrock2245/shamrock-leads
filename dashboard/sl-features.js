/* ShamrockLeads — Defendants, Health, Bond Modal, Export, Init */

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

  try {
    const r = await fetch(`${API}/api/leads?${p}`); const d = await r.json();
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
      return `<div class="def-card">
        <div class="def-card-header"><div><div class="def-name">${l.full_name||'Unknown'}</div><div class="def-booking">${l.booking_number||'\u2014'}</div></div><div class="def-bond-pill ${bc}">$${bond.toLocaleString()}</div></div>
        <div class="def-body">
          <div class="def-section"><div class="def-section-title">📋 Details</div><div class="def-row"><div class="def-field"><span class="def-label">County</span><span class="def-value">${l.county||'\u2014'}</span></div><div class="def-field"><span class="def-label">DOB</span><span class="def-value">${l.dob||'\u2014'}</span></div><div class="def-field"><span class="def-label">Status</span>${custDrop}</div><div class="def-field"><span class="def-label">Score</span><span class="score-pill ${scoreCls}">${l.lead_score||0} ${l.lead_status||''}</span></div></div></div>
          <div class="def-section"><div class="def-section-title">⚖️ Charges</div><div class="def-row wide"><div class="def-value" style="font-size:12px;white-space:normal">${l.charges||'\u2014'}</div></div></div>
        </div>
        <div class="def-card-footer">
          <button class="btn-detail" onclick="window.open('${l.detail_url||'#'}')">\ud83d\udd17 Source</button>
          <button class="btn-write-bond" onclick="openBondModal(window._leadMap['${bkSafe}'] || {full_name:'${(l.full_name||'').replace(/'/g,"\\'")}'}, ${bond}, '${l.county||''}', '${bkSafe}')">\u270d\ufe0f Write Bond</button>
        </div>
      </div>`;
    }).join('') || '<div class="loading">No defendants found</div>';

    // Defendant pagination
    document.getElementById('defPagination').innerHTML = `<button ${SL_STATE.defPage<=1?'disabled':''} onclick="goDefPage(${SL_STATE.defPage-1})">← Prev</button><span>Page ${SL_STATE.defPage} of ${pages}</span><button ${SL_STATE.defPage>=pages?'disabled':''} onclick="goDefPage(${SL_STATE.defPage+1})">Next →</button>`;
  } catch(e) { console.error('loadDefendants error:', e); }
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

// ── Build SL namespace ──
window.SL = { toggleTheme, switchTab, toggleCountyDropdown, filterCountyOptions, toggleCounty,
  applyPreset, setDays, setBond, setDefBond, sortBy, debounceSearch, debounceDefSearch, applyFilters,
  goPage, goDefPage, openBondModal, selectSurety, closeModal, submitBond, exportCSV, copyToSlack,
  clearAll, refresh, toast, loadDefendants, downloadBond, downloadAllBonds, registerActiveBond,
  sendOutreach, loadOutreachHistory, checkBBStatus, updateCustody };

// ── Init ──
loadDashboard();
