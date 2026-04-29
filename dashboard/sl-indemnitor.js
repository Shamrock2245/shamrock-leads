/* ═══════════════════════════════════════════════════════════
   ShamrockLeads — Indemnitor Management Tab
   Full lifecycle: Profile, Payment, Documents
   Sources: Wix, Telegram, TG Mini App, ElevenLabs, Texting Agent, Manual
   ═══════════════════════════════════════════════════════════ */

const SLIndemnitor = (() => {
  const API = window.API || '';
  let _data = [];
  let _currentBk = null;
  let _subTab = 'profile';
  let _searchTimer = null;

  const $ = id => document.getElementById(id);
  const money = n => '$' + (parseFloat(n)||0).toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0});
  const toast = (msg,type) => { if(window.SL?.toast) SL.toast(msg,type); else alert(msg); };
  const timeAgo = ts => {
    if (!ts) return '—';
    const s = Math.floor((Date.now()-new Date(ts).getTime())/1000);
    if (s<60) return 'just now'; if (s<3600) return Math.floor(s/60)+'m ago';
    if (s<86400) return Math.floor(s/3600)+'h ago'; return Math.floor(s/86400)+'d ago';
  };

  const SOURCES = [
    {key:'wix_portal',    icon:'🌐', label:'Wix Portal'},
    {key:'telegram',      icon:'📱', label:'Telegram Bot'},
    {key:'telegram_mini_app', icon:'📲', label:'TG Mini App'},
    {key:'elevenlabs',    icon:'🎙️', label:'Shannon (ElevenLabs)'},
    {key:'texting_agent', icon:'💬', label:'Texting Agent'},
    {key:'intake_queue',  icon:'📥', label:'Intake Queue'},
    {key:'manual_entry',  icon:'✏️', label:'Manual Entry'},
    {key:'walk_in',       icon:'🚶', label:'Walk-In'},
  ];

  const stageBadge = s => ({
    contacted:'📞 Contacted', negotiating:'🤝 Negotiating',
    paperwork:'📄 Paperwork', ready:'✅ Ready', bonded:'🔒 Bonded'
  }[s] || s || '—');

  // ── Load ──
  async function load() {
    const search = $('indSearch')?.value || '';
    const p = new URLSearchParams();
    if (search) p.set('search', search);
    try {
      const r = await fetch(`${API}/api/indemnitors?${p}`);
      const d = await r.json();
      _data = d.indemnitors || [];
      const badge = $('indemnitorBadge');
      if (badge) badge.textContent = d.total || '—';
      renderList();
    } catch(e) { console.error('SLIndemnitor.load:', e); }
  }

  function debounceSearch() { clearTimeout(_searchTimer); _searchTimer = setTimeout(load, 350); }

  // ── Render List ──
  function renderList() {
    const c = $('indemnitorList');
    if (!c) return;
    if (!_data.length) { c.innerHTML = '<div class="pipeline-empty">No indemnitors found</div>'; return; }
    c.innerHTML = _data.map(d => {
      const ind = d.indemnitor || {};
      const name = d.indemnitor_name || 'Unknown';
      const phone = d.indemnitor_phone || '';
      const rel = d.indemnitor_relationship || '';
      const docCount = Object.keys(d.documents||{}).length;
      const docTotal = 14;
      const stClass = (d.stage==='bonded')?'ind-badge-bonded':'ind-badge-prospect';
      return `<div class="ind-card" onclick="SLIndemnitor.openDetail('${d.booking_number}')">
        <div class="ind-card-top">
          <div class="ind-card-name">${name}</div>
          <span class="ind-stage-pill ${stClass}">${stageBadge(d.stage)}</span>
        </div>
        <div class="ind-card-meta">
          ${rel ? `<span>👤 ${rel}</span>` : ''}
          ${phone ? `<span>📱 ${phone}</span>` : ''}
        </div>
        <div class="ind-card-bond">
          <span>⚖️ ${d.defendant_name||'—'}</span>
          <span class="ind-card-amount">${money(d.bond_amount)}</span>
        </div>
        <div class="ind-card-bottom">
          <span>${d.county||'—'} County</span>
          <span>📄 ${docCount}/${docTotal} docs</span>
          <span>${timeAgo(d.updated_at)}</span>
        </div>
      </div>`;
    }).join('');
  }

  // ── Open Detail ──
  async function openDetail(bk) {
    _currentBk = bk;
    _subTab = 'profile';
    const panel = $('indDetailPanel');
    if (panel) panel.style.display = 'block';
    try {
      const r = await fetch(`${API}/api/indemnitors/${encodeURIComponent(bk)}`);
      const d = await r.json();
      if (!d.success) { toast(d.error||'Not found','error'); return; }
      renderDetail(d);
    } catch(e) { toast('Error: '+e.message,'error'); }
  }

  function closeDetail() {
    const panel = $('indDetailPanel');
    if (panel) panel.style.display = 'none';
    _currentBk = null;
  }

  function renderDetail(d) {
    const ind = d.indemnitor || {};
    const name = ind.name || [ind.firstName,ind.lastName].filter(Boolean).join(' ') || 'New Indemnitor';
    $('indDetailTitle').textContent = `🤝 ${name} — ${d.defendant_name||'Unknown'} (${d.county||'—'} County)`;
    renderSubTabs();
    switchSubTab(_subTab, d);
  }

  function renderSubTabs() {
    $('indSubTabs').innerHTML = ['profile','payment','documents'].map(t => {
      const icons = {profile:'📋',payment:'💳',documents:'📄'};
      const labels = {profile:'Profile',payment:'Payment',documents:'Documents'};
      return `<button class="ind-sub-btn ${_subTab===t?'active':''}" onclick="SLIndemnitor.switchSubTab('${t}')">${icons[t]} ${labels[t]}</button>`;
    }).join('');
  }

  async function switchSubTab(tab, dataOverride) {
    _subTab = tab;
    renderSubTabs();
    if (tab === 'profile') await renderProfileTab(dataOverride);
    else if (tab === 'payment') await renderPaymentTab();
    else if (tab === 'documents') await renderDocumentsTab();
  }

  // ── PROFILE TAB ──
  async function renderProfileTab(dataOverride) {
    let d = dataOverride;
    if (!d) {
      const r = await fetch(`${API}/api/indemnitors/${encodeURIComponent(_currentBk)}`);
      d = await r.json();
    }
    const ind = d.indemnitor || {};
    const f = (key, ph, type) => {
      type = type || 'text';
      return `<input id="ind_${key}" class="ind-input" type="${type}" value="${(ind[key]||'').replace(/"/g,'&quot;')}" placeholder="${ph}">`;
    };
    const sel = (key, opts) => {
      return `<select id="ind_${key}" class="ind-input">${opts.map(o =>
        `<option value="${o}" ${ind[key]===o?'selected':''}>${o||'—'}</option>`
      ).join('')}</select>`;
    };

    $('indSubContent').innerHTML = `
      <!-- Hydration Sources -->
      <div class="ind-hydrate-bar">
        <span class="ind-hydrate-label">📥 Hydrate from:</span>
        ${SOURCES.map(s => `<button class="ind-hydrate-btn" onclick="SLIndemnitor.hydrateFrom('${s.key}')" title="${s.label}">${s.icon} ${s.label}</button>`).join('')}
      </div>

      <!-- Personal Information -->
      <div class="ind-section">
        <div class="ind-section-header" onclick="this.parentElement.classList.toggle('collapsed')">
          <span>👤 Personal Information</span><span class="ind-chevron">▼</span>
        </div>
        <div class="ind-section-body">
          <div class="ind-form-grid">
            <div class="ind-field"><label>First Name</label>${f('firstName','First name')}</div>
            <div class="ind-field"><label>Middle</label>${f('middleName','Middle')}</div>
            <div class="ind-field"><label>Last Name</label>${f('lastName','Last name')}</div>
            <div class="ind-field"><label>Relationship</label>${f('relationship','e.g. Mother, Spouse')}</div>
            <div class="ind-field"><label>Date of Birth</label>${f('dob','MM/DD/YYYY','date')}</div>
            <div class="ind-field"><label>SSN</label>${f('ssn','XXX-XX-XXXX')}</div>
            <div class="ind-field"><label>DL #</label>${f('dl','Driver License')}</div>
            <div class="ind-field"><label>DL State</label>${sel('dlState',['FL','GA','AL','SC','NC','TX','NY','CA','OH','PA','','Other'])}</div>
            <div class="ind-field"><label>Phone</label>${f('phone','(239) 555-0000','tel')}</div>
            <div class="ind-field"><label>Email</label>${f('email','email@example.com','email')}</div>
            <div class="ind-field"><label>Callback Phone</label>${f('callback_phone','Alt phone','tel')}</div>
          </div>
        </div>
      </div>

      <!-- Address -->
      <div class="ind-section">
        <div class="ind-section-header" onclick="this.parentElement.classList.toggle('collapsed')">
          <span>📍 Address</span><span class="ind-chevron">▼</span>
        </div>
        <div class="ind-section-body">
          <div class="ind-form-grid">
            <div class="ind-field full"><label>Street Address</label>${f('address','123 Main St')}</div>
            <div class="ind-field"><label>City</label>${f('city','Fort Myers')}</div>
            <div class="ind-field"><label>State</label>${sel('state',['FL','GA','AL','SC','NC','TX','NY','CA','OH','PA','','Other'])}</div>
            <div class="ind-field"><label>ZIP</label>${f('zip','33901')}</div>
          </div>
        </div>
      </div>

      <!-- Employment -->
      <div class="ind-section">
        <div class="ind-section-header" onclick="this.parentElement.classList.toggle('collapsed')">
          <span>💼 Employment</span><span class="ind-chevron">▼</span>
        </div>
        <div class="ind-section-body">
          <div class="ind-form-grid">
            <div class="ind-field"><label>Employer</label>${f('employer','Company name')}</div>
            <div class="ind-field"><label>Occupation</label>${f('occupation','Job title')}</div>
            <div class="ind-field"><label>Employer Phone</label>${f('employerPhone','(239) 555-0000','tel')}</div>
            <div class="ind-field"><label>Employer City</label>${f('employerCity','City')}</div>
            <div class="ind-field"><label>Employer State</label>${sel('employerState',['FL','GA','AL','SC','NC','TX','','Other'])}</div>
            <div class="ind-field"><label>Supervisor</label>${f('supervisor','Supervisor name')}</div>
            <div class="ind-field"><label>Supervisor Phone</label>${f('supervisorPhone','Phone','tel')}</div>
            <div class="ind-field"><label>Monthly Income</label>${f('monthlyIncome','$0.00')}</div>
          </div>
        </div>
      </div>

      <!-- Spouse -->
      <div class="ind-section">
        <div class="ind-section-header" onclick="this.parentElement.classList.toggle('collapsed')">
          <span>💍 Spouse Information</span><span class="ind-chevron">▼</span>
        </div>
        <div class="ind-section-body">
          <p class="ind-hint">Spouse may also sign as a separate co-indemnitor.</p>
          <div class="ind-form-grid">
            <div class="ind-field"><label>Spouse Name</label>${f('spouseName','Full name')}</div>
            <div class="ind-field"><label>Spouse Phone</label>${f('spousePhone','Phone','tel')}</div>
            <div class="ind-field"><label>Spouse DOB</label>${f('spouseDob','MM/DD/YYYY','date')}</div>
            <div class="ind-field"><label>Spouse Employer</label>${f('spouseEmployer','Company')}</div>
            <div class="ind-field"><label>Spouse Employer Phone</label>${f('spouseEmployerPhone','Phone','tel')}</div>
            <div class="ind-field full"><label>Spouse Address</label>${f('spouseAddress','If different from above')}</div>
          </div>
        </div>
      </div>

      <!-- References -->
      <div class="ind-section">
        <div class="ind-section-header" onclick="this.parentElement.classList.toggle('collapsed')">
          <span>📞 References</span><span class="ind-chevron">▼</span>
        </div>
        <div class="ind-section-body">
          <p class="ind-hint">"People who could get a message to you, if we could not reach you by telephone, text, or our usual method of communication."</p>
          <div class="ind-ref-grid">
            <div class="ind-ref-card">
              <h5>Reference 1</h5>
              <div class="ind-form-grid">
                <div class="ind-field"><label>Name</label>${f('ref1Name','Full name')}</div>
                <div class="ind-field"><label>Relationship</label>${f('ref1Relationship','e.g. Friend')}</div>
                <div class="ind-field"><label>Phone</label>${f('ref1Phone','Phone','tel')}</div>
                <div class="ind-field full"><label>Address</label>${f('ref1Address','Full address')}</div>
              </div>
            </div>
            <div class="ind-ref-card">
              <h5>Reference 2</h5>
              <div class="ind-form-grid">
                <div class="ind-field"><label>Name</label>${f('ref2Name','Full name')}</div>
                <div class="ind-field"><label>Relationship</label>${f('ref2Relationship','e.g. Coworker')}</div>
                <div class="ind-field"><label>Phone</label>${f('ref2Phone','Phone','tel')}</div>
                <div class="ind-field full"><label>Address</label>${f('ref2Address','Full address')}</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Save Bar -->
      <div class="ind-save-bar">
        <button class="ind-btn-save" onclick="SLIndemnitor.saveProfile()">💾 Save All Changes</button>
        <span class="ind-save-hint">All fields auto-persist on save</span>
      </div>
    `;
  }

  // ── PAYMENT TAB ──
  async function renderPaymentTab() {
    const bond = _data.find(b => b.booking_number === _currentBk);
    const ba = bond?.bond_amount || 0;
    const premium = Math.round(ba * 0.10);
    const ind = bond?.indemnitor || {};
    const name = ind.name || [ind.firstName,ind.lastName].filter(Boolean).join(' ') || 'Indemnitor';

    $('indSubContent').innerHTML = `
      <div class="ind-payment-section">
        <div class="ind-payment-card">
          <div class="ind-payment-header">💳 Premium Payment</div>
          <div class="ind-payment-grid">
            <div class="ind-payment-row"><span>Bond Amount</span><span class="ind-payment-val">${money(ba)}</span></div>
            <div class="ind-payment-row"><span>Premium (10%)</span><span class="ind-payment-val highlight">${money(premium)}</span></div>
            <div class="ind-payment-row"><span>Indemnitor</span><span>${name}</span></div>
            <div class="ind-payment-row"><span>Booking #</span><span class="mono">${_currentBk}</span></div>
          </div>
          <div class="ind-payment-actions">
            <button class="ind-btn-payment" onclick="SLIndemnitor.generatePaymentLink()">🔗 Generate Payment Link</button>
            <button class="ind-btn-payment secondary" onclick="SLIndemnitor.copyPaymentLink()">📋 Copy Link</button>
            <button class="ind-btn-payment secondary" onclick="SLIndemnitor.sendPaymentLink()">📱 Send via Text</button>
          </div>
          <div class="ind-payment-link" id="indPaymentLink"></div>
        </div>
        <div class="ind-payment-card">
          <div class="ind-payment-header">📊 Payment History</div>
          <div class="pipeline-empty" style="padding:20px">No payments recorded yet. Payment tracking will sync with SwipeSimple.</div>
        </div>
      </div>
    `;
  }

  // ── DOCUMENTS TAB ──
  async function renderDocumentsTab() {
    try {
      const r = await fetch(`${API}/api/indemnitors/${encodeURIComponent(_currentBk)}/documents`);
      const d = await r.json();
      if (!d.success) { $('indSubContent').innerHTML = '<div class="pipeline-empty">Error loading documents</div>'; return; }

      const surety = d.surety || 'osi';
      const cl = d.checklist || {};
      const sectionLabels = {
        shamrock: '☘️ Shamrock Bail Bonds (Every Bond)',
        osi: '🏛️ O\'Shaughnahill Surety & Insurance (OSI)',
        palmetto: '🌴 Palmetto Surety Corporation',
      };
      // Determine which surety-specific section to show
      const suretySection = surety === 'palmetto' ? 'palmetto' : 'osi';
      const sections = ['shamrock', suretySection];

      $('indSubContent').innerHTML = `
        <div class="ind-docs-section">
          <div class="ind-docs-surety-select">
            <label>Surety:</label>
            <select id="indSuretySelect" onchange="SLIndemnitor.switchSubTab('documents')">
              <option value="osi" ${surety==='osi'?'selected':''}>OSI — O'Shaughnahill</option>
              <option value="palmetto" ${surety==='palmetto'?'selected':''}>Palmetto</option>
            </select>
          </div>
          ${sections.map(sec => {
            const items = cl[sec] || [];
            const signed = items.filter(i => i.signed).length;
            return `<div class="ind-doc-group">
              <div class="ind-doc-group-header">
                <span>${sectionLabels[sec]}</span>
                <span class="ind-doc-counter">${signed}/${items.length}</span>
              </div>
              <div class="ind-doc-list">
                ${items.map(item => `<div class="ind-doc-item ${item.signed?'signed':''}" onclick="SLIndemnitor.toggleDoc('${item.key}',${!item.signed})">
                  <span class="ind-doc-check">${item.signed?'✅':'⬜'}</span>
                  <span class="ind-doc-label">${item.label}</span>
                  <span class="ind-doc-time">${item.signed_at ? timeAgo(item.signed_at) : ''}</span>
                </div>`).join('')}
              </div>
            </div>`;
          }).join('')}
        </div>
      `;
    } catch(e) { $('indSubContent').innerHTML = '<div class="pipeline-empty">Error: '+e.message+'</div>'; }
  }

  // ── Actions ──
  async function saveProfile() {
    const fields = [
      'firstName','middleName','lastName','relationship','dob','ssn','dl','dlState',
      'phone','email','callback_phone','address','city','state','zip',
      'employer','occupation','employerPhone','employerCity','employerState','supervisor','supervisorPhone','monthlyIncome',
      'spouseName','spousePhone','spouseDob','spouseEmployer','spouseEmployerPhone','spouseAddress',
      'ref1Name','ref1Relationship','ref1Phone','ref1Address',
      'ref2Name','ref2Relationship','ref2Phone','ref2Address',
    ];
    const payload = {};
    fields.forEach(k => {
      const el = $('ind_'+k);
      if (el) payload[k] = el.value.trim();
    });
    // Build display name
    payload.name = [payload.firstName, payload.lastName].filter(Boolean).join(' ');

    try {
      const r = await fetch(`${API}/api/indemnitors/${encodeURIComponent(_currentBk)}`, {
        method: 'PATCH',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      const d = await r.json();
      if (d.success) { toast('✅ Indemnitor profile saved', 'success'); load(); }
      else toast(d.error||'Save failed', 'error');
    } catch(e) { toast('Error: '+e.message, 'error'); }
  }

  async function toggleDoc(docKey, signed) {
    try {
      await fetch(`${API}/api/indemnitors/${encodeURIComponent(_currentBk)}/documents`, {
        method: 'PATCH',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ doc_key: docKey, signed })
      });
      toast(signed ? '✅ Document signed' : '⬜ Document unsigned', 'success');
      renderDocumentsTab();
    } catch(e) { toast('Error: '+e.message, 'error'); }
  }

  async function generatePaymentLink() {
    try {
      const r = await fetch(`${API}/api/indemnitors/${encodeURIComponent(_currentBk)}/payment-link`, {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}'
      });
      const d = await r.json();
      if (d.success) {
        const el = $('indPaymentLink');
        if (el) el.innerHTML = `<a href="${d.payment_link}" target="_blank" class="ind-link">${d.payment_link}</a>`;
        toast(`💳 Payment link generated — ${money(d.premium)} premium`, 'success');
      } else toast(d.error||'Failed', 'error');
    } catch(e) { toast('Error: '+e.message, 'error'); }
  }

  function copyPaymentLink() {
    const link = $('indPaymentLink')?.querySelector('a')?.href;
    if (link) { navigator.clipboard.writeText(link); toast('📋 Link copied!', 'success'); }
    else toast('Generate a link first', 'warn');
  }

  async function sendPaymentLink() {
    const bond = _data.find(b => b.booking_number === _currentBk);
    const phone = bond?.indemnitor?.phone || '';
    const link = $('indPaymentLink')?.querySelector('a')?.href;
    if (!phone) { toast('No phone number', 'error'); return; }
    if (!link) { toast('Generate link first', 'warn'); return; }
    try {
      await fetch(`${API}/api/imessage/send`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          phone, message: `Hi! Here's your secure payment link for the bail bond:\n${link}\n\n— Shamrock Bail Bonds`,
          from_number: '2399550178', booking_number: _currentBk,
        })
      });
      toast('📱 Payment link sent!', 'success');
    } catch(e) { toast('Send failed: '+e.message, 'error'); }
  }

  // ── Hydration from external sources ──
  async function hydrateFrom(sourceKey) {
    if (sourceKey === 'intake_queue') {
      await hydrateFromIntake();
      return;
    }
    // For other sources, show a paste modal
    const sourceLabel = SOURCES.find(s => s.key === sourceKey)?.label || sourceKey;
    const raw = prompt(`Paste ${sourceLabel} JSON data (or leave blank to cancel):`);
    if (!raw) return;
    try {
      const data = JSON.parse(raw);
      fillFieldsFromData(data);
      toast(`📥 Hydrated from ${sourceLabel}`, 'success');
    } catch(e) { toast('Invalid JSON: '+e.message, 'error'); }
  }

  async function hydrateFromIntake() {
    try {
      const r = await fetch(`${API}/api/intake/queue?status=all&limit=20`);
      const d = await r.json();
      const items = d.intakes || d.items || [];
      if (!items.length) { toast('No intakes found', 'warn'); return; }
      // Show picker
      const choices = items.map((it,i) =>
        `${i+1}. ${it.FullName||it.indemnitor_name||'?'} → ${it.DefendantName||it.defendant_name||'?'} (${it.Source||it.source||'?'})`
      ).join('\n');
      const pick = prompt(`Select intake to hydrate from:\n\n${choices}\n\nEnter number:`);
      if (!pick) return;
      const idx = parseInt(pick) - 1;
      if (idx < 0 || idx >= items.length) { toast('Invalid selection', 'error'); return; }
      const item = items[idx];
      // Map intake fields to our indemnitor fields
      const ind = item.indemnitor || item._raw?.indemnitor || {};
      const mapped = {
        firstName: ind.firstName || item.FirstName || '',
        lastName: ind.lastName || item.LastName || '',
        middleName: ind.middleName || '',
        phone: ind.phone || item.Phone || '',
        email: ind.email || item.Email || '',
        relationship: ind.relationship || item.Role || '',
        dob: ind.dob || '', ssn: ind.ssn || '', dl: ind.dl || '', dlState: ind.dlState || 'FL',
        address: ind.address || '', city: ind.city || '', state: ind.state || 'FL', zip: ind.zip || '',
        employer: ind.employer || '', employerPhone: ind.employerPhone || '',
        employerCity: ind.employerCity || '', employerState: ind.employerState || '',
        supervisor: ind.supervisor || '', supervisorPhone: ind.supervisorPhone || '',
        ref1Name: ind.ref1Name || '', ref1Phone: ind.ref1Phone || '',
        ref1Address: ind.ref1Address || '', ref1Relationship: ind.ref1Relation || '',
        ref2Name: ind.ref2Name || '', ref2Phone: ind.ref2Phone || '',
        ref2Address: ind.ref2Address || '', ref2Relationship: ind.ref2Relation || '',
      };
      fillFieldsFromData(mapped);
      toast(`📥 Hydrated from intake: ${item.FullName||item.indemnitor_name}`, 'success');
    } catch(e) { toast('Hydration error: '+e.message, 'error'); }
  }

  function fillFieldsFromData(data) {
    // Map common field name variants
    const aliases = {
      first_name:'firstName', last_name:'lastName', middle_name:'middleName',
      IndFirstName:'firstName', IndLastName:'lastName', IndPhone:'phone', IndEmail:'email',
      indemnitorFirstName:'firstName', indemnitorLastName:'lastName',
      indemnitorPhone:'phone', indemnitorEmail:'email',
      indemnitorDOB:'dob', indemnitorSSN:'ssn', indemnitorDL:'dl',
      indemnitorAddress:'address', indemnitorCity:'city', indemnitorZip:'zip',
      indemnitorEmployerName:'employer', indemnitorEmployerPhone:'employerPhone',
    };
    const normalized = {};
    Object.entries(data).forEach(([k,v]) => {
      const mapped = aliases[k] || k;
      if (v && typeof v === 'string') normalized[mapped] = v;
    });
    Object.entries(normalized).forEach(([k,v]) => {
      const el = $('ind_'+k);
      if (el && v) el.value = v;
    });
  }

  return {
    load, debounceSearch, openDetail, closeDetail,
    switchSubTab, saveProfile, toggleDoc,
    generatePaymentLink, copyPaymentLink, sendPaymentLink,
    hydrateFrom,
  };
})();
