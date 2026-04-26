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
      const stBadge = (l.status||'').toLowerCase().includes('custody')?'custody':(l.status||'').toLowerCase().includes('release')?'released':'other';
      const sc = (l.lead_status||'').toLowerCase();
      const scoreCls = sc==='hot'?'score-hot':sc==='warm'?'score-warm':'score-cold';
      const bkSafe = (l.booking_number||'').replace(/'/g,"\\'");
      return `<div class="def-card">
        <div class="def-card-header"><div><div class="def-name">${l.full_name||'Unknown'}</div><div class="def-booking">${l.booking_number||'\u2014'}</div></div><div class="def-bond-pill ${bc}">$${bond.toLocaleString()}</div></div>
        <div class="def-body">
          <div class="def-section"><div class="def-section-title">📋 Details</div><div class="def-row"><div class="def-field"><span class="def-label">County</span><span class="def-value">${l.county||'\u2014'}</span></div><div class="def-field"><span class="def-label">DOB</span><span class="def-value">${l.dob||'\u2014'}</span></div><div class="def-field"><span class="def-label">Status</span><span class="def-status-badge ${stBadge}">${l.status||'\u2014'}</span></div><div class="def-field"><span class="def-label">Score</span><span class="score-pill ${scoreCls}">${l.lead_score||0} ${l.lead_status||''}</span></div></div></div>
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
          <span class="insurer-pill-icon">🛡️</span><span class="insurer-pill-name">OSI</span><span class="insurer-pill-full">Ohio Security Insurance</span>
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
    <div class="wb-poa-notice"><span class="wb-poa-icon">📋</span><div><div class="wb-poa-title">Power of Attorney Required</div><div class="wb-poa-text">A POA will be assigned from your available inventory for the selected surety company.</div></div></div>
    <div id="bondSubmitStatus" style="display:none;margin-top:12px;padding:10px;border-radius:6px;text-align:center"></div>`;

  // Store full lead data for submit
  window._bondModalData = {
    lead,
    name, bond: bondAmt, county: cnty, booking: bkNum,
    charges: chargesRaw, chargeList,
    surety: 'osi',
    date: new Date().toLocaleDateString('en-US')
  };
}

function downloadBond(chargeEncoded, idx) {
  const data = window._bondModalData;
  if (!data) return;
  const charge = decodeURIComponent(chargeEncoded);
  const surety = data.surety;
  const params = new URLSearchParams({
    name: data.name, booking: data.booking, county: data.county,
    bond: data.bond, charge, surety, date: data.date,
    dob: data.lead.dob || '', address: data.lead.address || '',
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
}

function closeModal() { document.getElementById('bondModal').classList.remove('show'); }

async function submitBond() {
  const data = window._bondModalData;
  if (!data) { toast('No bond data', 'error'); return; }

  const statusEl = document.getElementById('bondSubmitStatus');
  if (statusEl) { statusEl.style.display = 'block'; statusEl.style.background = 'var(--panel)'; statusEl.textContent = 'Writing bond...'; }

  const lead = data.lead;
  const payload = {
    insurance_company: data.surety,
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

async function registerActiveBond(data, bondResult) {
  try {
    const activeBondPayload = {
      defendant_name: data.name,
      booking_number: data.booking,
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

// ── Build SL namespace ──
window.SL = { toggleTheme, switchTab, toggleCountyDropdown, filterCountyOptions, toggleCounty,
  applyPreset, setDays, setBond, setDefBond, sortBy, debounceSearch, debounceDefSearch, applyFilters,
  goPage, goDefPage, openBondModal, selectSurety, closeModal, submitBond, exportCSV, copyToSlack,
  clearAll, refresh, toast, loadDefendants, downloadBond, downloadAllBonds, registerActiveBond };

// ── Init ──
loadDashboard();
