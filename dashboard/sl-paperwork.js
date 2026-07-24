/**
 * sl-paperwork.js — Twenty CRM Style Document Operations & E-Signature Hub
 * Includes Interactive Drag & Drop Packet Builder & Post-Release Remedy Hub
 */
const SLPaperwork = {
  _currentSubTab: 'live',
  _allPackets: [],
  _draggedDocKey: null,
  _activePacketId: null,

  _docCatalog: [
    { key: "master_bail_application", label: "Master Bail Application", icon: "📄", badge: "Core", desc: "Defendant & Indemnitor personal data, employment, references" },
    { key: "indemnity_agreement", label: "Indemnity Agreement", icon: "✍️", badge: "Legal", desc: "Financial liability & indemnification contract" },
    { key: "promissory_note", label: "Promissory Note", icon: "💵", badge: "Financial", desc: "Master debt promise and collateral backing" },
    { key: "disclosure_statement", label: "Disclosure Statement", icon: "📋", badge: "Compliance", desc: "FL Dept of Financial Services mandatory disclosure" },
    { key: "premium_receipt", label: "Premium Receipt", icon: "🧾", badge: "Receipt", desc: "Itemized premium payment and fee receipt" },
    { key: "payment_plan_agreement", label: "Payment Plan Agreement", icon: "💳", badge: "Financing", desc: "Monthly/weekly premium payment schedule & rules" },
    { key: "credit_card_authorization", label: "Credit Card Auth Form", icon: "💳", badge: "Financing", desc: "Recurring autopay and card on file consent" },
    { key: "promissory_note_schedule", label: "Payment Installment Schedule", icon: "📅", badge: "Financing", desc: "Itemized installment dates, interest & late fee terms" },
    { key: "wage_assignment", label: "Wage Assignment Form", icon: "💼", badge: "Financing", desc: "Voluntary payroll deduction authorization" },
    { key: "osi_appearance_bond", label: "OSI Appearance Bond", icon: "🛡️", badge: "Surety OSI", desc: "O'Shaughnahill Surety official court bond form" },
    { key: "osi_premium_receipt", label: "OSI Surety Receipt", icon: "🧾", badge: "Surety OSI", desc: "OSI official insurer premium split receipt" },
    { key: "palmetto_power_certificate", label: "Palmetto Power Certificate", icon: "🌴", badge: "Surety Palmetto", desc: "Palmetto Surety official power of attorney cert" },
    { key: "palmetto_appearance_bond", label: "Palmetto Appearance Bond", icon: "🌴", badge: "Surety Palmetto", desc: "Palmetto Surety official court bond form" },
    { key: "cosigner_addendum", label: "Co-Signer Addendum", icon: "👥", badge: "Add-On", desc: "Additional indemnitor liability & guarantee form" },
    { key: "additional_cosigner_addendum", label: "Multi Co-Signer Guaranty", icon: "👥", badge: "Add-On", desc: "Joint & several liability for 2nd, 3rd, or 4th co-signers" },
    { key: "recovery_expense_addendum", label: "Fugitive Recovery Reimbursement", icon: "🎯", badge: "Recovery", desc: "Max legal recovery fees & itemized actual expenses contract" },
    { key: "cash_premium_receipt", label: "Cash Premium Receipt", icon: "💵", badge: "Receipt", desc: "Official receipt form for cash premium transactions" },
    { key: "out_of_state_waiver", label: "Out-of-State Waiver", icon: "✈️", badge: "Add-On", desc: "Extradition and travel consent waiver" },
    { key: "gps_checkin_consent", label: "GPS / Check-In Consent", icon: "📍", badge: "Add-On", desc: "Automated check-in & location monitoring agreement" },
  ],

  _categories: {
    universal: ["master_bail_application", "indemnity_agreement", "promissory_note", "disclosure_statement", "premium_receipt"],
    payment_plan: ["payment_plan_agreement", "credit_card_authorization", "promissory_note_schedule", "wage_assignment"],
    osi_surety: ["osi_appearance_bond", "osi_premium_receipt"],
    palmetto_surety: ["palmetto_power_certificate", "palmetto_appearance_bond"],
    conditional: ["cosigner_addendum", "additional_cosigner_addendum", "recovery_expense_addendum", "out_of_state_waiver", "gps_checkin_consent"]
  },

  async load() {
    await this.loadLivePackets();
    await this.loadDocRulesConfig();
    await this.loadConfig();
  },

  switchSubTab(tabName) {
    this._currentSubTab = tabName;
    ['live', 'builder', 'templates', 'rules', 'post_release'].forEach(t => {
      const btn = document.getElementById(`pwSubTab_${t}`);
      const pane = document.getElementById(`pwPane_${t}`);
      if (btn) btn.classList.toggle('active', t === tabName);
      if (pane) pane.style.display = t === tabName ? 'block' : 'none';
    });
    if (tabName === 'builder') {
      this.renderBuilderWorkspace();
    }
  },

  async loadLivePackets() {
    const tbody = document.querySelector('#tableLivePaperworkPackets tbody');
    if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="loading">Loading live document packets…</td></tr>`;

    try {
      const res = await fetch('/api/paperwork/all', { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.success === false) throw new Error(data.error || 'Failed to load packets');

      this._allPackets = data.packets || [];
      this.renderLiveSummary(data.summary);
      this.renderLivePacketsTable(this._allPackets);
    } catch (err) {
      console.error(err);
      if (tbody) tbody.innerHTML = `<tr><td colspan="7" style="color:var(--danger);text-align:center;padding:20px">Failed to load packets: ${this._esc(err.message)}</td></tr>`;
    }
  },

  renderLiveSummary(summary) {
    const bar = document.getElementById('paperworkConfigSummary');
    if (!bar || !summary) return;
    const chip = (label, val, color) =>
      `<div style="background:var(--panel,#1e293b);border:1px solid var(--border,#334155);border-radius:10px;padding:12px 16px;min-width:130px">
        <div style="font-size:11px;color:var(--muted,#94a3b8);text-transform:uppercase;letter-spacing:.04em">${label}</div>
        <div style="font-size:22px;font-weight:700;color:${color}">${val ?? 0}</div>
      </div>`;
    bar.innerHTML =
      chip('Total Packets', summary.total_packets, '#38bdf8') +
      chip('Awaiting Signature', summary.pending_signature, '#f59e0b') +
      chip('Signed & Completed', summary.signed_completed, '#10b981') +
      chip('Filed to Drive', summary.filed_to_drive, '#c084fc');
  },

  renderLivePacketsTable(packets) {
    const tbody = document.querySelector('#tableLivePaperworkPackets tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!packets || packets.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="color:var(--muted);text-align:center;padding:24px">No document packets found</td></tr>`;
      return;
    }

    packets.forEach(p => {
      const pid = p.packet_id || '—';
      const defName = p.defendant_name || p.booking_number || '—';
      const indName = p.indemnitor_name || '—';
      const surety = (p.surety_id || 'osi').toUpperCase();
      const status = p.status || p.signnow_status || 'draft';
      const dt = p.created_at ? p.created_at.slice(0, 10) : '—';
      const amt = p.premium_amount || p.bond_amount ? (p.premium_amount || (p.bond_amount * 0.1)) : 500.0;

      const suretyChipCls = surety === 'OSI' ? 'inv-chip-osi' : 'inv-chip-palm';
      const suretyIcon = surety === 'OSI' ? '🛡️ OSI' : '🌴 PSC';

      let statusBadge = `<span class="badge bg-blue">${this._esc(status)}</span>`;
      if (['signed', 'completed'].includes(status)) {
        statusBadge = `<span class="badge bg-green">✅ Signed</span>`;
      } else if (['sent', 'signnow_pending'].includes(status)) {
        statusBadge = `<span class="badge bg-orange">📱 Sent (Pending)</span>`;
      } else if (status === 'voided') {
        statusBadge = `<span class="badge bg-red">❌ Voided</span>`;
      }

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong style="font-family:monospace;font-size:11px">${this._esc(pid)}</strong></td>
        <td><strong>${this._esc(defName)}</strong></td>
        <td>${this._esc(indName)}</td>
        <td><span class="inv-surety-chip ${suretyChipCls}" style="font-size:10px;padding:2px 6px">${suretyIcon}</span></td>
        <td>${statusBadge}</td>
        <td>${this._esc(dt)}</td>
        <td style="text-align:right">
          <div style="display:inline-flex;gap:4px;flex-wrap:wrap;justify-content:flex-end">
            <button type="button" class="inv-btn" onclick="SLPaperwork.showHydrationAudit('${this._esc(pid)}')" style="font-size:10px;padding:2px 6px" title="Audit field hydration completeness">🔍 Audit</button>
            <button type="button" class="inv-btn" onclick="SLPaperwork.openSwipeSimpleModal('${this._esc(pid)}', ${amt})" style="font-size:10px;padding:2px 6px;color:#38bdf8" title="SwipeSimple credit card link">💳 Card</button>
            <button type="button" class="inv-btn" onclick="SLPaperwork.openCashModal('${this._esc(pid)}', ${amt})" style="font-size:10px;padding:2px 6px;color:#4ade80" title="Log cash payment">💵 Cash</button>
            ${p.drive_url ? `<a href="${this._esc(p.drive_url)}" target="_blank" class="inv-btn" style="font-size:10px;padding:2px 6px;color:#c084fc" title="View signed PDF folder in Drive">☁️ Drive</a>` : ''}
            ${status !== 'voided' ? `<button type="button" class="inv-btn" onclick="SLPaperwork.deliverPacket('${this._esc(pid)}')" style="font-size:10px;padding:2px 6px;color:#34d399" title="Deliver via BlueBubbles iMessage / SMS">📱 Deliver</button>` : ''}
          </div>
        </td>
      `;
      tbody.appendChild(tr);
    });
  },

  filterPackets() {
    const q = (document.getElementById('pwSearchInput')?.value || '').toLowerCase();
    const st = document.getElementById('pwStatusSelect')?.value || 'all';
    const sur = document.getElementById('pwSuretySelect')?.value || 'all';

    const filtered = this._allPackets.filter(p => {
      const matchQ = !q || (
        (p.defendant_name || '').toLowerCase().includes(q) ||
        (p.indemnitor_name || '').toLowerCase().includes(q) ||
        (p.packet_id || '').toLowerCase().includes(q) ||
        (p.case_number || '').toLowerCase().includes(q) ||
        (p.booking_number || '').toLowerCase().includes(q)
      );

      const pStatus = (p.status || p.signnow_status || 'draft').toLowerCase();
      let matchSt = true;
      if (st === 'sent') matchSt = ['sent', 'signnow_pending', 'partially_signed'].includes(pStatus);
      else if (st === 'signed') matchSt = ['signed', 'completed'].includes(pStatus);
      else if (st === 'draft') matchSt = ['draft', 'created'].includes(pStatus);
      else if (st === 'voided') matchSt = pStatus === 'voided';

      const pSurety = (p.surety_id || 'osi').toLowerCase();
      const matchSur = sur === 'all' || pSurety === sur.toLowerCase();

      return matchQ && matchSt && matchSur;
    });

    this.renderLivePacketsTable(filtered);
  },

  /* ─────────────────────────────────────────────────────────────────────────────
   * SwipeSimple & Cash Payment Handlers
   * ───────────────────────────────────────────────────────────────────────────── */
  openSwipeSimpleModal(packetId = 'GENERAL', amount = 500.0) {
    this._activePacketId = packetId;
    const modal = document.getElementById('pwSwipeSimpleModal');
    const amtEl = document.getElementById('pwSwipeSimpleAmount');
    if (amtEl) amtEl.value = amount;
    if (modal) { modal.style.display = 'flex'; modal.classList.add('active'); }
  },

  closeSwipeSimpleModal() {
    const modal = document.getElementById('pwSwipeSimpleModal');
    if (modal) { modal.style.display = 'none'; modal.classList.remove('active'); }
  },

  async sendSwipeSimpleLink() {
    const amount = parseFloat(document.getElementById('pwSwipeSimpleAmount')?.value || '0');
    const phone = document.getElementById('pwSwipeSimplePhone')?.value || '';

    try {
      const res = await fetch('/api/paperwork/payment/swipesimple-link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ packet_id: this._activePacketId, amount, phone, deliver: true }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || 'Failed to dispatch SwipeSimple link');

      alert(`💳 SwipeSimple payment link dispatched successfully!${data.delivered ? ` (Delivered to ${data.recipient})` : ''}`);
      this.closeSwipeSimpleModal();
    } catch (err) {
      alert(`❌ Error: ${err.message}`);
    }
  },

  openCashModal(packetId = 'GENERAL', amount = 500.0) {
    this._activePacketId = packetId;
    const modal = document.getElementById('pwCashModal');
    const amtEl = document.getElementById('pwCashAmount');
    if (amtEl) amtEl.value = amount;
    if (modal) { modal.style.display = 'flex'; modal.classList.add('active'); }
  },

  closeCashModal() {
    const modal = document.getElementById('pwCashModal');
    if (modal) { modal.style.display = 'none'; modal.classList.remove('active'); }
  },

  async submitCashPayment() {
    const amount = parseFloat(document.getElementById('pwCashAmount')?.value || '0');
    const receivedFrom = document.getElementById('pwCashPayer')?.value || 'Indemnitor';
    const notes = document.getElementById('pwCashNotes')?.value || '';

    try {
      const res = await fetch('/api/paperwork/payment/cash-log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ packet_id: this._activePacketId, amount, received_from: receivedFrom, notes }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || 'Failed to log cash payment');

      alert(`💵 Cash payment of $${amount.toFixed(2)} recorded! Receipt ID: ${data.receipt_id}`);
      this.closeCashModal();
    } catch (err) {
      alert(`❌ Cash payment error: ${err.message}`);
    }
  },

  /* ─────────────────────────────────────────────────────────────────────────────
   * Post-Release & Forfeiture Remedy Document Generation
   * ───────────────────────────────────────────────────────────────────────────── */
  async generatePostReleaseRemedyDoc(docType) {
    const packetId = prompt("Enter Packet ID or Case Number for this remedy document:", "LEE-2026-ACTIVE");
    if (!packetId) return;

    try {
      const res = await fetch('/api/paperwork/post-release/remedy-doc', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ doc_type: docType, packet_id: packetId }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || 'Remedy doc generation failed');

      alert(`🛡️ ${data.message} generated successfully! Doc ID: ${data.doc_id}`);
    } catch (err) {
      alert(`❌ Error generating remedy document: ${err.message}`);
    }
  },

  /* ─────────────────────────────────────────────────────────────────────────────
   * Drag & Drop Packet Builder & Category Engine
   * ───────────────────────────────────────────────────────────────────────────── */
  async loadDocRulesConfig() {
    try {
      const res = await fetch('/api/paperwork/config/rules', { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.success && data.categories) {
        this._categories = data.categories;
        if (this._currentSubTab === 'builder') {
          this.renderBuilderWorkspace();
        }
      }
    } catch (err) {
      console.warn("loadDocRulesConfig warning:", err);
    }
  },

  renderBuilderWorkspace() {
    const paletteEl = document.getElementById('pwDocPalette');
    const paletteCount = document.getElementById('pwDocPaletteCount');
    if (!paletteEl) return;

    if (paletteCount) paletteCount.textContent = `${this._docCatalog.length} docs`;
    paletteEl.innerHTML = '';

    // Render palette catalog items
    this._docCatalog.forEach(doc => {
      const card = document.createElement('div');
      card.className = 'pw-palette-card';
      card.draggable = true;
      card.setAttribute('ondragstart', `SLPaperwork.handleDragStart(event, '${doc.key}')`);
      card.style.cssText = `
        background: rgba(30, 41, 59, 0.8);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        padding: 10px 12px;
        cursor: grab;
        transition: all 0.15s ease;
        user-select: none;
      `;

      card.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
          <strong style="font-size:12px;color:var(--text);display:flex;align-items:center;gap:6px">
            <span>${doc.icon}</span> ${this._esc(doc.label)}
          </strong>
          <span style="font-size:10px;background:rgba(255,255,255,0.08);padding:1px 6px;border-radius:4px;color:var(--muted)">${doc.badge}</span>
        </div>
        <div style="font-size:10px;color:var(--muted);line-height:1.3">${this._esc(doc.desc)}</div>
        <div style="margin-top:6px;display:flex;justify-content:flex-end">
          <select onchange="SLPaperwork.moveDocToCategory('${doc.key}', this.value); this.value='';" style="font-size:10px;background:rgba(15,23,42,0.8);border:1px solid #334155;color:#94a3b8;border-radius:4px;padding:2px 4px;">
            <option value="">Move to…</option>
            <option value="universal">📌 Universal</option>
            <option value="payment_plan">💳 Payment Plan</option>
            <option value="osi_surety">🏢 OSI Surety</option>
            <option value="palmetto_surety">🌴 Palmetto Surety</option>
            <option value="conditional">⚖️ Conditional</option>
          </select>
        </div>
      `;
      paletteEl.appendChild(card);
    });

    // Render category boxes
    const catKeys = ['universal', 'payment_plan', 'osi_surety', 'palmetto_surety', 'conditional'];
    catKeys.forEach(catId => {
      const container = document.getElementById(`pwItems_${catId}`);
      const countBadge = document.getElementById(`pwCount_${catId}`);
      if (!container) return;

      const docKeys = this._categories[catId] || [];
      if (countBadge) countBadge.textContent = docKeys.length;

      container.innerHTML = '';
      if (docKeys.length === 0) {
        container.innerHTML = `<div style="font-size:11px;color:rgba(148,163,184,0.5);text-align:center;padding:20px 10px;border:1px dashed rgba(255,255,255,0.05);border-radius:6px">Drag documents here</div>`;
        return;
      }

      docKeys.forEach(dKey => {
        const catalogDoc = this._docCatalog.find(c => c.key === dKey) || { label: dKey, icon: "📄" };
        const itemCard = document.createElement('div');
        itemCard.style.cssText = `
          background: rgba(15, 23, 42, 0.8);
          border: 1px solid rgba(255, 255, 255, 0.12);
          border-radius: 6px;
          padding: 8px 10px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          font-size: 12px;
        `;
        itemCard.innerHTML = `
          <div style="display:flex;align-items:center;gap:6px">
            <span>${catalogDoc.icon}</span>
            <strong style="color:#e2e8f0;font-size:12px">${this._esc(catalogDoc.label)}</strong>
          </div>
          <button type="button" onclick="SLPaperwork.removeDocFromCategory('${dKey}', '${catId}')" style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:14px;padding:0 4px;" title="Remove document">✕</button>
        `;
        container.appendChild(itemCard);
      });
    });
  },

  handleDragStart(evt, docKey) {
    this._draggedDocKey = docKey;
    evt.dataTransfer.setData('text/plain', docKey);
    evt.dataTransfer.effectAllowed = 'copyMove';
  },

  handleDragOver(evt) {
    evt.preventDefault();
    evt.dataTransfer.dropEffect = 'copy';
    const box = evt.currentTarget;
    if (box) box.style.borderColor = '#38bdf8';
  },

  handleDragLeave(evt) {
    const box = evt.currentTarget;
    if (box) box.style.borderColor = '';
  },

  handleDrop(evt, targetCatId) {
    evt.preventDefault();
    const box = evt.currentTarget;
    if (box) box.style.borderColor = '';

    const docKey = evt.dataTransfer.getData('text/plain') || this._draggedDocKey;
    if (!docKey) return;

    this.moveDocToCategory(docKey, targetCatId);
  },

  moveDocToCategory(docKey, targetCatId) {
    if (!targetCatId || !this._categories[targetCatId]) return;

    // Add to target category if not already present
    if (!this._categories[targetCatId].includes(docKey)) {
      this._categories[targetCatId].push(docKey);
    }

    this.renderBuilderWorkspace();
    this.showBuilderToast(`Added ${docKey.replace(/_/g, ' ')} to ${targetCatId.replace(/_/g, ' ')}`, 'info');
  },

  removeDocFromCategory(docKey, catId) {
    if (!this._categories[catId]) return;
    this._categories[catId] = this._categories[catId].filter(k => k !== docKey);
    this.renderBuilderWorkspace();
    this.showBuilderToast(`Removed from ${catId.replace(/_/g, ' ')}`, 'warning');
  },

  async saveDocRulesConfig() {
    try {
      const res = await fetch('/api/paperwork/config/rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ categories: this._categories }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || 'Failed to save configuration');

      this.showBuilderToast('💾 Document rules configuration saved to MongoDB!', 'success');
    } catch (err) {
      this.showBuilderToast(`❌ Error: ${err.message}`, 'error');
    }
  },

  resetDocRulesDefaults() {
    if (!confirm('Reset drag-and-drop document rules to standard Shamrock defaults?')) return;
    this._categories = {
      universal: ["master_bail_application", "indemnity_agreement", "promissory_note", "disclosure_statement", "premium_receipt"],
      payment_plan: ["payment_plan_agreement", "credit_card_authorization", "promissory_note_schedule", "wage_assignment"],
      osi_surety: ["osi_appearance_bond", "osi_premium_receipt"],
      palmetto_surety: ["palmetto_power_certificate", "palmetto_appearance_bond"],
      conditional: ["cosigner_addendum", "additional_cosigner_addendum", "recovery_expense_addendum", "out_of_state_waiver", "gps_checkin_consent"]
    };
    this.renderBuilderWorkspace();
    this.showBuilderToast('🔄 Document rules reset to defaults', 'warning');
  },

  showBuilderToast(msg, type = 'info') {
    const el = document.getElementById('pwBuilderToast');
    if (!el) return;
    const bg = type === 'success' ? '#166534' : type === 'error' ? '#991b1b' : type === 'warning' ? '#854d0e' : '#1e3a8a';
    el.style.background = bg;
    el.style.color = '#ffffff';
    el.style.display = 'block';
    el.textContent = msg;
    setTimeout(() => {
      if (el) el.style.display = 'none';
    }, 4000);
  },

  /* ─────────────────────────────────────────────────────────────────────────────
   * Standard Hydration Audit & Legacy Config
   * ───────────────────────────────────────────────────────────────────────────── */
  async showHydrationAudit(packetId) {
    const modal = document.getElementById('pwHydrationModal');
    const body = document.getElementById('pwHydrationModalBody');
    if (modal) { modal.style.display = 'flex'; modal.classList.add('active'); }
    if (body) body.innerHTML = '<p>Loading field hydration audit…</p>';

    try {
      const res = await fetch(`/api/paperwork/${packetId}/hydration-audit`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.success) throw new Error(data.error || 'Audit failed');

      const scoreColor = data.hydration_score >= 100 ? '#10b981' : data.hydration_score >= 70 ? '#f59e0b' : '#ef4444';

      let rows = (data.fields || []).map(f => `
        <tr style="border-bottom:1px solid rgba(255,255,255,0.06)">
          <td style="font-weight:600;padding:6px 8px">${this._esc(f.label)}</td>
          <td style="font-family:monospace;font-size:11px;color:${f.hydrated ? '#38bdf8' : 'var(--muted)'}">${this._esc(f.val || '— missing —')}</td>
          <td style="text-align:right;padding:6px 8px">${f.hydrated ? '<span style="color:#10b981;font-weight:700">✓ Complete</span>' : '<span style="color:#ef4444">⚠️ Missing</span>'}</td>
        </tr>
      `).join('');

      body.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;background:rgba(15,23,42,0.8);padding:12px 16px;border-radius:8px;margin-bottom:14px;border:1px solid rgba(255,255,255,0.1)">
          <div>
            <div style="font-size:12px;color:var(--muted)">Packet ID: <span class="mono">${this._esc(data.packet_id)}</span></div>
            <div style="font-size:13px;font-weight:700;color:var(--text);margin-top:2px">Hydration Status: ${this._esc(data.status || 'Draft')}</div>
          </div>
          <div style="text-align:right">
            <div style="font-size:24px;font-weight:800;color:${scoreColor}">${data.hydration_score}%</div>
            <div style="font-size:10px;color:var(--muted)">${data.hydrated_count} of ${data.total_required} fields ready</div>
          </div>
        </div>
        <table style="width:100%;font-size:12px;border-collapse:collapse">
          <thead>
            <tr style="border-bottom:2px solid rgba(255,255,255,0.1);text-align:left">
              <th style="padding:6px 8px">Field Name</th>
              <th>Hydrated Value</th>
              <th style="text-align:right;padding:6px 8px">Audit Status</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    } catch (err) {
      if (body) body.innerHTML = `<p style="color:var(--danger)">Failed to audit packet: ${this._esc(err.message)}</p>`;
    }
  },

  closeHydrationModal() {
    const modal = document.getElementById('pwHydrationModal');
    if (modal) { modal.style.display = 'none'; modal.classList.remove('active'); }
  },

  async deliverPacket(packetId) {
    if (!confirm(`Deliver paperwork packet ${packetId} via BlueBubbles iMessage / SMS?`)) return;
    try {
      const res = await fetch(`/api/paperwork/${packetId}/deliver`, { method: 'POST', credentials: 'same-origin' });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || 'Delivery failed');
      alert(`📱 Paperwork delivered successfully to ${data.recipient || 'client'}`);
      this.loadLivePackets();
    } catch (err) {
      alert(`❌ Delivery error: ${err.message}`);
    }
  },

  async loadConfig() {
    const rulesEl = document.getElementById('paperworkDocRules');
    if (rulesEl) rulesEl.textContent = 'Loading…';
    ['tablePaperworkOsi', 'tablePaperworkPalmetto'].forEach(id => {
      const tb = document.querySelector(`#${id} tbody`);
      if (tb) tb.innerHTML = `<tr><td colspan="4" class="loading">Loading…</td></tr>`;
    });

    try {
      const res = await fetch('/api/paperwork/config', { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.success === false) throw new Error(data.error || 'Config error');

      this.renderDocRules(data.doc_rules);
      this.renderTable('tablePaperworkOsi', data.template_map?.osi);
      this.renderTable('tablePaperworkPalmetto', data.template_map?.palmetto);
    } catch (err) {
      console.error(err);
      if (rulesEl) {
        rulesEl.textContent = 'Failed to load: ' + err.message;
        rulesEl.style.color = 'var(--danger)';
      }
    }
  },

  renderDocRules(rules) {
    const el = document.getElementById('paperworkDocRules');
    if (!el) return;
    if (!rules || !Object.keys(rules).length) {
      el.innerHTML = '<span style="color:var(--muted)">No document rules defined.</span>';
      return;
    }
    const rows = Object.entries(rules).map(([key, meta]) => {
      const rule = (meta && meta.rule) || 'static';
      const label = (meta && meta.label) || key;
      return `<tr>
        <td style="font-family:monospace;font-size:12px">${this._esc(key)}</td>
        <td>${this._esc(label)}</td>
        <td><span class="badge ${this.getBadgeClass(rule)}">${this._esc(rule)}</span></td>
      </tr>`;
    }).join('');

    el.innerHTML = `<table class="data-table" style="width:100%">
      <thead><tr><th>Key</th><th>Label</th><th>Rule</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p style="font-size:11px;color:var(--muted);margin-top:10px">
      <strong>Rules:</strong> static = once per packet · shared = one copy · per-indemnitor / per-person / per-charge = multiply · print-only = never e-sign
    </p>`;
  },

  renderTable(tableId, templates) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!templates || !Object.keys(templates).length) {
      tbody.innerHTML = `<tr><td colspan="4" style="color:var(--muted);text-align:center;padding:20px">No templates found</td></tr>`;
      return;
    }

    const entries = Object.entries(templates)
      .map(([key, tpl]) => ({ key, ...(typeof tpl === 'object' ? tpl : { template_id: tpl }) }))
      .sort((a, b) => a.key.localeCompare(b.key));

    entries.forEach(t => {
      const tid = t.template_id || '';
      const configured = t.configured !== false && tid && tid !== '(uses shared)';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong style="font-family:monospace;font-size:12px">${this._esc(t.key)}</strong></td>
        <td>${this._esc(t.label || t.name || 'N/A')}</td>
        <td style="font-family:monospace;font-size:11px;word-break:break-all;color:${configured ? 'var(--text)' : 'var(--muted)'}">${this._esc(tid || '— not set —')}</td>
        <td><span class="badge ${this.getBadgeClass(t.rule)}">${this._esc(t.rule || 'static')}</span>
          ${configured ? '' : '<span style="margin-left:6px;font-size:10px;color:var(--warning)">needs ID</span>'}
        </td>`;
      tbody.appendChild(tr);
    });
  },

  getBadgeClass(rule) {
    switch (rule) {
      case 'per-indemnitor': return 'bg-blue';
      case 'per-charge': return 'bg-orange';
      case 'per-person': return 'bg-purple';
      case 'shared': return 'bg-green';
      case 'print-only': return 'bg-gray';
      default: return '';
    }
  },

  _esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  },
};

window.SLPaperwork = SLPaperwork;
