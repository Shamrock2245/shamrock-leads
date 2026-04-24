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

    const grid = document.getElementById('defendantGrid');
    grid.innerHTML = leads.map(l => {
      const bond = l.bond_amount||0;
      const bc = bond>=10000?'high':bond>=2500?'mid':'low';
      const stBadge = (l.status||'').toLowerCase().includes('custody')?'custody':(l.status||'').toLowerCase().includes('release')?'released':'other';
      const sc = (l.lead_status||'').toLowerCase();
      const scoreCls = sc==='hot'?'score-hot':sc==='warm'?'score-warm':'score-cold';
      return `<div class="def-card">
        <div class="def-card-header"><div><div class="def-name">${l.full_name||'Unknown'}</div><div class="def-booking">${l.booking_number||'—'}</div></div><div class="def-bond-pill ${bc}">$${bond.toLocaleString()}</div></div>
        <div class="def-body">
          <div class="def-section"><div class="def-section-title">📋 Details</div><div class="def-row"><div class="def-field"><span class="def-label">County</span><span class="def-value">${l.county||'—'}</span></div><div class="def-field"><span class="def-label">DOB</span><span class="def-value">${l.dob||'—'}</span></div><div class="def-field"><span class="def-label">Status</span><span class="def-status-badge ${stBadge}">${l.status||'—'}</span></div><div class="def-field"><span class="def-label">Score</span><span class="score-pill ${scoreCls}">${l.lead_score||0} ${l.lead_status||''}</span></div></div></div>
          <div class="def-section"><div class="def-section-title">⚖️ Charges</div><div class="def-row wide"><div class="def-value" style="font-size:12px;white-space:normal">${l.charges||'—'}</div></div></div>
        </div>
        <div class="def-card-footer">
          <button class="btn-detail" onclick="window.open('${l.detail_url||'#'}')">🔗 Source</button>
          <button class="btn-write-bond" onclick="openBondModal('${(l.full_name||'').replace(/'/g,"\\'")}',${bond},'${l.county||''}','${l.booking_number||''}')">✍️ Write Bond</button>
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
function openBondModal(name, bond, county, booking) {
  document.getElementById('bondModal').classList.add('show');
  const premium = Math.max(100, bond * 0.1);
  const transferFee = (bond > 25000 || ['Lee','Charlotte'].includes(county)) ? 0 : 125;

  document.getElementById('bondModalBody').innerHTML = `
    <div class="wb-section">
      <div class="wb-section-label">Defendant Summary</div>
      <div class="wb-defendant-summary">
        <div class="wb-name">${name}</div>
        <div class="wb-meta-grid">
          <div><span class="wb-meta-label">County</span>${county}</div>
          <div><span class="wb-meta-label">Booking #</span>${booking}</div>
          <div><span class="wb-meta-label">Bond Amount</span>$${bond.toLocaleString()}</div>
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
      <div class="wb-section-label">Appearance Bond PDF</div>
      <div id="pdfPreviewArea" style="background:var(--panel);border-radius:8px;padding:16px;text-align:center">
        <p style="color:var(--muted);margin:0 0 12px">Select a surety above, then download the blank Appearance Bond to fill out.</p>
        <div style="display:flex;gap:12px;justify-content:center">
          <a href="/osi-appearance-bond.pdf" target="_blank" class="btn-export" id="pdfLinkOSI">📄 OSI Appearance Bond</a>
          <a href="/palmetto-appearance-bond.pdf" target="_blank" class="btn-export" id="pdfLinkPalmetto" style="display:none">📄 Palmetto Appearance Bond</a>
        </div>
      </div>
    </div>
    <div class="wb-poa-notice"><span class="wb-poa-icon">📋</span><div><div class="wb-poa-title">Power of Attorney Required</div><div class="wb-poa-text">A POA will be assigned from your available inventory for this surety company.</div></div></div>`;

  // Store modal state
  window._bondModalData = { name, bond, county, booking, surety: 'osi' };
}

function selectSurety(s) {
  window._bondModalData.surety = s;
  document.getElementById('suretyOSI').classList.toggle('active', s === 'osi');
  document.getElementById('suretyPalmetto').classList.toggle('active', s === 'palmetto');
  document.getElementById('pdfLinkOSI').style.display = s === 'osi' ? '' : 'none';
  document.getElementById('pdfLinkPalmetto').style.display = s === 'palmetto' ? '' : 'none';
}

function closeModal() { document.getElementById('bondModal').classList.remove('show'); }

function submitBond() {
  const data = window._bondModalData;
  if (!data) { toast('No bond data', 'error'); return; }
  // Format for Slack copy
  const msg = `☘️ *BOND WRITTEN*\n• Defendant: ${data.name}\n• County: ${data.county}\n• Booking: ${data.booking}\n• Bond: $${data.bond.toLocaleString()}\n• Premium: $${Math.max(100, data.bond * 0.1).toLocaleString()}\n• Surety: ${data.surety === 'osi' ? 'Ohio Security Insurance' : 'Palmetto Surety'}`;
  navigator.clipboard.writeText(msg).then(() => {
    toast('Bond info copied to clipboard — paste in Slack #new-cases!', 'success');
  }).catch(() => toast('Bond info ready', 'info'));
  closeModal();
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
  clearAll, refresh, toast, loadDefendants };

// ── Init ──
loadDashboard();
