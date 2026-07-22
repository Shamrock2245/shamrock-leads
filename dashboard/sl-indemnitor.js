/* ═══════════════════════════════════════════════════════════
   ShamrockLeads — Indemnitor Management Tab
   Full lifecycle: Profile, Payment, Documents
   Sources: Wix, Telegram, TG Mini App, ElevenLabs, Texting Agent, Manual
   ═══════════════════════════════════════════════════════════ */

const SLIndemnitor = (() => {
  const API = window.API || '';
  let _data = [];
  let _personData = [];
  let _viewMode = 'bonds';  // 'bonds' | 'persons'
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
    {key:'imessage',      icon:'💚', label:'iMessage'},
    {key:'kiosk',         icon:'📋', label:'Kiosk (iPad)'},
    {key:'intake_queue',  icon:'📥', label:'Intake Queue'},
    {key:'manual_entry',  icon:'✏️', label:'Manual Entry'},
    {key:'walk_in',       icon:'🚶', label:'Walk-In'},
  ];

  const stageBadge = s => ({
    contacted:'📞 Contacted', negotiating:'🤝 Negotiating',
    paperwork:'📄 Paperwork', ready:'✅ Ready', bonded:'🔒 Bonded',
    unlinked:'🔓 Unlinked'
  }[s] || s || '—');

  // ── Load ──
  async function load() {
    if (_viewMode === 'persons') { await loadPersonView(); return; }
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

  async function loadPersonView() {
    const search = $('indSearch')?.value || '';
    const p = new URLSearchParams();
    if (search) p.set('search', search);
    const c = $('indemnitorList');
    if (c) c.innerHTML = '<div class="pipeline-empty" style="padding:40px">⏳ Loading…</div>';
    try {
      const r = await fetch(`${API}/api/indemnitors/by-person?${p}`);
      const d = await r.json();
      _personData = d.persons || [];
      const badge = $('indemnitorBadge');
      if (badge) badge.textContent = d.total || '—';
      renderPersonList();
    } catch(e) { console.error('SLIndemnitor.loadPersonView:', e); }
  }

  function renderPersonList() {
    const c = $('indemnitorList');
    if (!c) return;
    if (!_personData.length) { c.innerHTML = '<div class="pipeline-empty">No indemnitors found</div>'; return; }
    c.innerHTML = _personData.map(p => {
      const bondCount = p.bonds.length;
      const activeBonds = p.active_bonds || 0;
      const totalVal = '$' + (p.total_bond_value||0).toLocaleString();
      const bondsHtml = p.bonds.map(b => {
        const stageColor = b.bond_type === 'active' ? '#22c55e' : b.stage === 'ready' ? '#6366f1' : '#64748b';
        const bkSafe = (b.booking_number||'').replace(/'/g,"\\'");
        return `<div class="ind-person-bond-row" onclick="event.stopPropagation();SLIndemnitor.openDetail('${bkSafe}')">
          <span class="ind-person-bond-def">${b.defendant_name || '—'}</span>
          <span style="color:var(--muted);font-size:11px">${b.county || ''}</span>
          <span style="color:var(--accent);font-size:11px">$${(b.bond_amount||0).toLocaleString()}</span>
          <span style="color:${stageColor};font-size:10px;text-transform:capitalize">${b.stage || b.bond_type || '—'}</span>
        </div>`;
      }).join('');
      return `<div class="ind-card ind-person-card">
        <div class="ind-card-top">
          <div class="ind-card-name">👤 ${p.name || '—'}</div>
          <span class="ind-stage-pill" style="background:rgba(99,102,241,0.15);color:#818cf8">${bondCount} bond${bondCount!==1?'s':''}</span>
        </div>
        <div class="ind-card-meta">
          ${p.phone ? `<span>📱 ${p.phone}</span>` : ''}
          ${p.email ? `<span>✉️ ${p.email}</span>` : ''}
          ${p.relationship ? `<span>👥 ${p.relationship}</span>` : ''}
        </div>
        <div style="display:flex;gap:12px;margin:6px 0;font-size:12px">
          <span style="color:#22c55e">🔒 ${activeBonds} active</span>
          <span style="color:var(--accent)">💰 ${totalVal} total</span>
          <span style="color:var(--muted);font-size:11px">${timeAgo(p.latest_activity)}</span>
        </div>
        <div style="border-top:1px solid var(--border);margin-top:6px;padding-top:6px">${bondsHtml}</div>
      </div>`;
    }).join('');
  }

  function toggleViewMode(mode) {
    _viewMode = mode;
    const btnBonds = $('indViewBonds');
    const btnPersons = $('indViewPersons');
    if (btnBonds) btnBonds.classList.toggle('active', mode === 'bonds');
    if (btnPersons) btnPersons.classList.toggle('active', mode === 'persons');
    load();
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
      const isUnlinked = d.stage === 'unlinked' || d.bond_type === 'unlinked';
      const stClass = isUnlinked ? 'ind-badge-unlinked' : (d.stage==='bonded')?'ind-badge-bonded':'ind-badge-prospect';
      const bkSafe = String(d.booking_number || '').replace(/'/g, "\\'");
      return `<div class="ind-card" onclick="SLIndemnitor.openDetail('${bkSafe}')">
        <div class="ind-card-top">
          <div class="ind-card-name">${name}</div>
          <span class="ind-stage-pill ${stClass}">${stageBadge(d.stage)}</span>
        </div>
        <div class="ind-card-meta">
          ${rel ? `<span>👤 ${rel}</span>` : ''}
          ${phone ? `<span>📱 ${phone}</span>` : ''}
        </div>
        <div class="ind-card-bond">
          <span>⚖️ ${isUnlinked ? 'No defendant linked yet' : (d.defendant_name||'—')}</span>
          <span class="ind-card-amount">${isUnlinked ? '—' : money(d.bond_amount)}</span>
        </div>
        <div class="ind-card-bottom">
          <span>${isUnlinked ? 'Link booking # from detail' : ((d.county||'—') + ' County')}</span>
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

    const isUnlinked = d.bond_type === 'unlinked' || d.stage === 'unlinked';
    const indId = d.indemnitor_id || (String(_currentBk || '').startsWith('UNLINKED-') ? String(_currentBk).slice('UNLINKED-'.length) : '');
    const linkBar = isUnlinked ? `
      <div class="ind-link-bar" style="margin-bottom:14px;padding:12px;border:1px solid var(--border);border-radius:8px;background:rgba(99,102,241,0.08)">
        <div style="font-size:12px;font-weight:600;margin-bottom:8px;color:#818cf8">🔓 Unlinked indemnitor — attach to a bond</div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <input id="indLinkBooking" class="ind-input" type="text" placeholder="Booking # to link" style="flex:1;min-width:160px">
          <button class="btn btn-primary" type="button" onclick="SLIndemnitor.linkToBond('${indId.replace(/'/g, "\\'")}')">Link to Bond</button>
        </div>
        <div style="font-size:11px;color:var(--muted);margin-top:6px">Creates/uses a pipeline record for that booking # and attaches this person as indemnitor.</div>
      </div>` : '';

    $('indSubContent').innerHTML = `
      ${linkBar}
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

      // Fetch uploaded KYC files
      let uploads = [];
      try {
        const ur = await fetch(`${API}/api/indemnitors/${encodeURIComponent(_currentBk)}/uploads`);
        const ud = await ur.json();
        if (ud.success) uploads = ud.uploads || [];
      } catch(e) { /* ignore */ }

      const imgExts = ['png','jpg','jpeg','gif','webp','heic'];

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

          <!-- Identity photos: DL/ID front, back, selfie -->
          <div class="ind-doc-group" style="margin-top:20px">
            <div class="ind-doc-group-header">
              <span>🪪 Driver License / ID &amp; Selfie</span>
              <span class="ind-doc-counter">${[
                uploads.find(u => u.doc_type === 'govt_id_front'),
                uploads.find(u => u.doc_type === 'govt_id_back'),
                uploads.find(u => u.doc_type === 'selfie'),
              ].filter(Boolean).length}/3</span>
            </div>
            <div class="id-photo-slots" id="indIdPhotoSlots">
              ${renderIdPhotoSlots(uploads, 'indemnitor')}
            </div>
          </div>

          <!-- Other KYC Upload Section -->
          <div class="ind-doc-group" style="margin-top:20px">
            <div class="ind-doc-group-header">
              <span>📎 Other KYC Documents</span>
              <span class="ind-doc-counter">${uploads.filter(u => !['govt_id_front','govt_id_back','selfie'].includes(u.doc_type)).length} files</span>
            </div>

            <div class="ind-upload-zone" id="indUploadZone"
              ondragover="event.preventDefault();this.classList.add('dragover')"
              ondragleave="this.classList.remove('dragover')"
              ondrop="SLIndemnitor.handleDrop(event)">
              <div class="ind-upload-inner">
                <span class="ind-upload-icon">📄</span>
                <span>Drag & drop files here, or</span>
                <label class="ind-upload-btn">
                  Browse Files
                  <input type="file" id="indFileInput" accept=".pdf,.png,.jpg,.jpeg,.gif,.webp,.heic" multiple hidden onchange="SLIndemnitor.handleFileSelect(this.files)">
                </label>
              </div>
              <div class="ind-upload-type-row">
                <select id="indUploadType" style="padding:6px 10px;background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:12px">
                  <option value="pay_stub">Pay Stub / Income Proof</option>
                  <option value="utility_bill">Utility Bill / Address Proof</option>
                  <option value="other">Other Document</option>
                </select>
              </div>
            </div>

            ${(() => {
              const other = uploads.filter(u => !['govt_id_front','govt_id_back','selfie'].includes(u.doc_type));
              if (!other.length) {
                return '<div style="padding:12px 16px;color:var(--muted);font-size:13px">No extra documents yet. Use the slots above for ID front/back and selfie.</div>';
              }
              return `<div class="ind-upload-gallery">${other.map(u => renderUploadCard(u)).join('')}</div>`;
            })()}
          </div>
        </div>
      `;
    } catch(e) { $('indSubContent').innerHTML = '<div class="pipeline-empty">Error: '+e.message+'</div>'; }
  }

  function _uploadUrl(u) {
    if (u.url) return u.url;
    const key = u.entity_key || _currentBk;
    return `/uploads/${encodeURIComponent(key)}/${encodeURIComponent(u.saved_as)}`;
  }

  function renderUploadCard(u) {
    const imgExts = ['png','jpg','jpeg','gif','webp','heic'];
    const isImg = imgExts.includes((u.extension || '').toLowerCase());
    const sizeKb = ((u.size_bytes || 0) / 1024).toFixed(1);
    const src = _uploadUrl(u);
    return `<div class="ind-upload-card">
      <div class="ind-upload-preview">
        ${isImg ? `<img src="${src}" alt="${u.doc_type_label || ''}" loading="lazy">` : `<div class="ind-upload-pdf-icon">📄</div>`}
      </div>
      <div class="ind-upload-info">
        <span class="ind-upload-type-badge">${u.doc_type_label || u.doc_type || 'File'}</span>
        <span class="ind-upload-meta">${sizeKb}KB · ${(u.extension || '').toUpperCase()}</span>
        <span class="ind-upload-date">${timeAgo(u.uploaded_at)}</span>
      </div>
      <div class="ind-upload-actions">
        ${isImg ? `<a href="${src}" target="_blank" class="ind-upload-action" title="View full size">🔍</a>` : ''}
        <button class="ind-upload-action del" onclick="SLIndemnitor.deleteUpload('${u.file_id}')" title="Delete">🗑️</button>
      </div>
    </div>`;
  }

  function renderIdPhotoSlots(uploads, kind) {
    const slots = [
      { key: 'govt_id_front', label: 'ID / DL Front', icon: '🪪' },
      { key: 'govt_id_back', label: 'ID / DL Back', icon: '🔄' },
      { key: 'selfie', label: 'Selfie', icon: '🤳' },
    ];
    const imgExts = ['png','jpg','jpeg','gif','webp','heic'];
    return slots.map(s => {
      // latest of this type
      const matches = (uploads || []).filter(u => u.doc_type === s.key);
      const u = matches.sort((a, b) => String(b.uploaded_at || '').localeCompare(String(a.uploaded_at || '')))[0];
      const src = u ? _uploadUrl(u) : '';
      const isImg = u && imgExts.includes((u.extension || '').toLowerCase());
      const inputId = `idSlot_${kind}_${s.key}`;
      return `<div class="id-photo-slot ${u ? 'has-file' : ''}">
        <div class="id-photo-slot-label">${s.icon} ${s.label}</div>
        <div class="id-photo-slot-preview">
          ${u && isImg ? `<img src="${src}" alt="${s.label}" loading="lazy">`
            : u ? `<div class="ind-upload-pdf-icon">📄</div>`
            : `<div class="id-photo-empty">Tap to upload</div>`}
        </div>
        <div class="id-photo-slot-actions">
          <label class="id-photo-upload-btn" for="${inputId}">${u ? 'Replace' : 'Upload'}</label>
          <input id="${inputId}" type="file" accept="image/*,.pdf,.heic" hidden
            onchange="SLIndemnitor.uploadIdSlot('${s.key}', this.files && this.files[0])">
          ${u ? `<button type="button" class="id-photo-del-btn" onclick="SLIndemnitor.deleteUpload('${u.file_id}')">Delete</button>` : ''}
          ${u && isImg ? `<a href="${src}" target="_blank" class="id-photo-view-btn">View</a>` : ''}
        </div>
      </div>`;
    }).join('');
  }

  async function uploadIdSlot(docType, file) {
    if (!file || !_currentBk) return;
    try {
      toast('⏳ Uploading ' + (file.name || docType) + '…', 'info');
      const formData = new FormData();
      formData.append('file', file);
      formData.append('doc_type', docType);
      const r = await fetch(`${API}/api/indemnitors/${encodeURIComponent(_currentBk)}/uploads`, {
        method: 'POST',
        body: formData,
      });
      const d = await r.json();
      if (!r.ok || d.success === false) {
        toast(`❌ ${d.error || 'Upload failed'}`, 'error');
        return;
      }
      toast(`✅ ${d.doc_type_label || docType} uploaded`, 'success');
      renderDocumentsTab();
    } catch (e) {
      toast('❌ ' + e.message, 'error');
    }
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

  // ── Add Indemnitor Modal ──
  let _smartSearchTimer = null;
  let _selectedPrior = null;

  function openAddModal() {
    const modal = $('addIndemnitorModal');
    if (modal) {
      modal.classList.add('active');
      modal.style.display = 'flex';
    }
    const step1 = $('indAddStep1');
    const step2 = $('indAddStep2');
    if (step1) step1.style.display = '';
    if (step2) step2.style.display = 'none';
    const search = $('indSmartSearch');
    if (search) search.value = '';
    const results = $('indSearchResults');
    if (results) results.innerHTML = '';
    _selectedPrior = null;
  }

  function closeAddModal() {
    const modal = $('addIndemnitorModal');
    if (modal) {
      modal.classList.remove('active');
      modal.style.display = 'none';
    }
    _selectedPrior = null;
  }

  function smartSearch(q) {
    clearTimeout(_smartSearchTimer);
    if (q.length < 2) { $('indSearchResults').innerHTML = ''; return; }
    _smartSearchTimer = setTimeout(async () => {
      try {
        const r = await fetch(`${API}/api/indemnitors/search-existing?q=${encodeURIComponent(q)}`);
        const d = await r.json();
        const results = d.results || [];
        if (!results.length) {
          $('indSearchResults').innerHTML = `<div style="padding:16px;text-align:center;color:var(--muted);font-size:13px">No existing records found. <a href="#" onclick="SLIndemnitor.showNewForm();return false" style="color:var(--accent)">Create new →</a></div>`;
          return;
        }
        $('indSearchResults').innerHTML = results.map((r,i) => {
          const roleIcon = r.prior_role === 'defendant' ? '⚖️' : '🤝';
          const roleLabel = r.prior_role === 'defendant' ? 'Prior Defendant' : 'Prior Indemnitor';
          const sourceBg = r.source === 'arrest' ? 'rgba(239,68,68,.1)' : r.source === 'active_bond' ? 'rgba(16,185,129,.1)' : 'rgba(59,130,246,.1)';
          return `<div class="ind-search-result" onclick="SLIndemnitor.selectSearchResult(${i})" style="padding:10px 14px;background:var(--surface);border:1px solid var(--border);border-radius:8px;margin-bottom:6px;cursor:pointer;transition:var(--transition)" onmouseover="this.style.borderColor='var(--accent)'" onmouseout="this.style.borderColor='var(--border)'">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
              <span style="font-weight:700;font-size:14px">${r.name || '—'}</span>
              <span style="font-size:10px;padding:2px 8px;border-radius:8px;background:${sourceBg};font-weight:600">${roleIcon} ${roleLabel}</span>
            </div>
            <div style="display:flex;gap:12px;font-size:12px;color:var(--text-secondary)">
              ${r.phone ? `<span>📱 ${r.phone}</span>` : ''}
              ${r.county ? `<span>📍 ${r.county}</span>` : ''}
              ${r.booking_number ? `<span>🔖 ${r.booking_number}</span>` : ''}
            </div>
          </div>`;
        }).join('');
        // Stash results for selection
        $('indSearchResults')._results = results;
      } catch(e) { console.error('smartSearch:', e); }
    }, 300);
  }

  function selectSearchResult(idx) {
    const results = $('indSearchResults')._results || [];
    const r = results[idx];
    if (!r) return;
    _selectedPrior = r;
    populateFormFromResult(r);
    $('indAddStep1').style.display = 'none';
    $('indAddStep2').style.display = '';
    $('indFormTitle').textContent = `Linking: ${r.name || '—'} (${r.prior_role})`;
    $('indPriorMatch').style.display = '';
    $('indPriorDetail').textContent = `Previously ${r.prior_role === 'defendant' ? 'a defendant' : 'an indemnitor'} — ${r.county || ''} ${r.booking_number ? '(#' + r.booking_number + ')' : ''}`;
  }

  function populateFormFromResult(r) {
    const nameParts = (r.name || '').split(' ');
    $('indFormFirst').value = r.first_name || nameParts[0] || '';
    $('indFormLast').value = r.last_name || nameParts.slice(1).join(' ') || '';
    $('indFormPhone').value = r.phone || '';
    $('indFormEmail').value = r.email || '';
    $('indFormAddress').value = r.address || '';
    $('indFormDOB').value = r.dob || '';
    $('indFormRelationship').value = r.relationship || '';
    $('indFormBooking').value = '';  // User must specify which bond to link to
  }

  function showNewForm() {
    _selectedPrior = null;
    $('indAddStep1').style.display = 'none';
    $('indAddStep2').style.display = '';
    $('indFormTitle').textContent = 'New Indemnitor';
    $('indPriorMatch').style.display = 'none';
    // Clear form
    ['indFormFirst','indFormLast','indFormPhone','indFormEmail','indFormAddress',
     'indFormDOB','indFormRelationship','indFormEmployer','indFormDL','indFormDLState',
     'indFormSSN4','indFormBooking',
     'indFormRef1Name','indFormRef1Phone','indFormRef1Rel',
     'indFormRef2Name','indFormRef2Phone','indFormRef2Rel',
     'indFormRef3Name','indFormRef3Phone','indFormRef3Rel',
    ].forEach(id => { const el = $(id); if (el) el.value = id === 'indFormDLState' ? 'FL' : ''; });
  }

  function backToSearch() {
    $('indAddStep1').style.display = '';
    $('indAddStep2').style.display = 'none';
  }

  async function submitAddForm() {
    const booking = ($('indFormBooking')?.value || '').trim();
    const firstName = ($('indFormFirst')?.value || '').trim();
    const lastName = ($('indFormLast')?.value || '').trim();
    const phone = ($('indFormPhone')?.value || '').trim();

    // Minimal validation: only a name is required to save.
    // Everything else (booking#, phone, address, etc.) can be filled in later.
    if (!firstName && !lastName) { toast('⚠️ At least a first or last name is required', 'error'); return; }

    const body = {
      booking_number: booking,
      firstName, lastName, phone,
      email: $('indFormEmail')?.value || '',
      address: $('indFormAddress')?.value || '',
      dob: $('indFormDOB')?.value || '',
      relationship: $('indFormRelationship')?.value || '',
      employer: $('indFormEmployer')?.value || '',
      dl_number: $('indFormDL')?.value || '',
      dl_state: $('indFormDLState')?.value || 'FL',
      ssn_last4: $('indFormSSN4')?.value || '',
      reference1_name: $('indFormRef1Name')?.value || '',
      reference1_phone: $('indFormRef1Phone')?.value || '',
      reference1_relationship: $('indFormRef1Rel')?.value || '',
      reference2_name: $('indFormRef2Name')?.value || '',
      reference2_phone: $('indFormRef2Phone')?.value || '',
      reference2_relationship: $('indFormRef2Rel')?.value || '',
      reference3_name: $('indFormRef3Name')?.value || '',
      reference3_phone: $('indFormRef3Phone')?.value || '',
      reference3_relationship: $('indFormRef3Rel')?.value || '',
    };

    if (_selectedPrior) {
      body.prior_defendant_id = _selectedPrior.prior_id || '';
      body.prior_role = _selectedPrior.prior_role || '';
    }

    try {
      const r = await fetch(`${API}/api/indemnitors/create`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!r.ok) { toast(`❌ ${d.error || 'Failed'}`, 'error'); return; }
      const verb = d.action === 'updated_existing' ? 'updated' : 'saved';
      const suffix = d.linked === false ? ' (unlinked — link to a bond later)' : '';
      toast(`✅ Indemnitor ${verb}: ${firstName} ${lastName}${suffix}`, 'success');
      closeAddModal();
      load();  // Refresh list
    } catch(e) { toast('❌ Network error: ' + e.message, 'error'); }
  }

  // ── KYC Upload Handlers ──
  async function handleFileSelect(files) {
    if (!files || !files.length) return;
    const docType = $('indUploadType')?.value || 'other';
    for (const file of files) {
      await uploadFile(file, docType);
    }
    // Refresh the documents tab to show new uploads
    renderDocumentsTab();
  }

  function handleDrop(event) {
    event.preventDefault();
    const zone = $('indUploadZone');
    if (zone) zone.classList.remove('dragover');
    const files = event.dataTransfer?.files;
    if (files && files.length) handleFileSelect(files);
  }

  async function uploadFile(file, docType) {
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('doc_type', docType || 'other');

      toast('⏳ Uploading ' + file.name + '...', 'info');
      const r = await fetch(`${API}/api/indemnitors/${encodeURIComponent(_currentBk)}/uploads`, {
        method: 'POST',
        body: formData,
      });
      const d = await r.json();
      if (r.ok && d.success) {
        toast(`✅ Uploaded: ${d.doc_type_label}`, 'success');
      } else {
        toast(`❌ Upload failed: ${d.error || 'Unknown error'}`, 'error');
      }
    } catch(e) {
      toast('❌ Upload error: ' + e.message, 'error');
    }
  }

  async function deleteUpload(fileId) {
    if (!confirm('Delete this document?')) return;
    try {
      const r = await fetch(`${API}/api/indemnitors/${encodeURIComponent(_currentBk)}/uploads/${fileId}`, {
        method: 'DELETE',
      });
      const d = await r.json();
      if (d.success) {
        toast('🗑️ Document deleted', 'success');
        renderDocumentsTab();
      } else {
        toast(`❌ ${d.error || 'Delete failed'}`, 'error');
      }
    } catch(e) { toast('❌ ' + e.message, 'error'); }
  }

  async function linkToBond(indemnitorId) {
    const booking = ($('indLinkBooking')?.value || '').trim();
    if (!booking) { toast('⚠️ Enter a booking number to link', 'error'); return; }
    if (!indemnitorId) { toast('⚠️ Missing indemnitor id', 'error'); return; }
    try {
      const r = await fetch(`${API}/api/indemnitors/link`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ indemnitor_id: indemnitorId, booking_number: booking, agent: 'Dashboard' }),
      });
      const d = await r.json();
      if (!r.ok || d.success === false) {
        toast(`❌ ${d.error || 'Failed to link'}`, 'error');
        return;
      }
      toast(`✅ Linked to booking ${d.booking_number || booking}`, 'success');
      closeDetail();
      load();
      // Open the newly linked bond detail if we have a booking#
      if (d.booking_number && !String(d.booking_number).startsWith('UNLINKED-')) {
        setTimeout(() => openDetail(d.booking_number), 400);
      }
    } catch (e) {
      toast('❌ Network error: ' + e.message, 'error');
    }
  }

  return {
    load, debounceSearch, openDetail, closeDetail,
    switchSubTab, saveProfile, toggleDoc,
    generatePaymentLink, copyPaymentLink, sendPaymentLink,
    hydrateFrom,
    // Add Indemnitor Modal
    openAddModal, closeAddModal, smartSearch, selectSearchResult,
    showNewForm, backToSearch, submitAddForm,
    // KYC Uploads + ID slots
    handleFileSelect, handleDrop, deleteUpload, uploadIdSlot,
    // View mode toggle
    toggleViewMode,
    // Link unlinked indemnitor → bond
    linkToBond,
  };
})();
