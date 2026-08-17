/**
 * sl-paperwork.js — Twenty CRM Style Document Operations & E-Signature Hub
 * Includes Interactive Drag & Drop Packet Builder & Post-Release Remedy Hub
 */
const SLPaperwork = {
  _currentSubTab: 'live',
  _allPackets: [],
  _draggedDocKey: null,
  _activePacketId: null,
  _casePacketKeys: [],
  _extraUploads: [],
  _adaptiveContext: null,
  _adaptiveFields: null,

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
    { key: "osi_appearance_bond", label: "OSI Appearance Bond", icon: "🖨️", badge: "Print · Wet-Ink", desc: "Unsigned print file — live signature on paper, then take to jail (never e-sign)" },
    { key: "osi_premium_receipt", label: "OSI Surety Receipt", icon: "🧾", badge: "Surety OSI", desc: "OSI official insurer premium split receipt" },
    { key: "palmetto_power_certificate", label: "Palmetto Power Certificate", icon: "🌴", badge: "Surety Palmetto", desc: "Palmetto Surety official power of attorney cert" },
    { key: "palmetto_appearance_bond", label: "Palmetto Appearance Bond", icon: "🖨️", badge: "Print · Wet-Ink", desc: "Unsigned print file — live signature on paper, then take to jail (never e-sign)" },
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
    ['live', 'builder', 'templates', 'rules', 'post_release', 'adobe_tools'].forEach(t => {
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
            <button type="button" class="inv-btn" onclick="SLPaperwork.openAdaptivePacketModal({packet_id:'${this._esc(pid)}'})" style="font-size:10px;padding:2px 6px;color:#a78bfa" title="Open adaptive packet builder for this case">🎯 Packet</button>
            <button type="button" class="inv-btn" onclick="SLPaperwork.showHydrationAudit('${this._esc(pid)}')" style="font-size:10px;padding:2px 6px" title="Audit field hydration completeness">🔍 Audit</button>
            <button type="button" class="inv-btn" onclick="SLPaperwork.openSwipeSimpleModal('${this._esc(pid)}', ${amt}, '${this._esc(p.indemnitor_phone || p.phone || '')}', '${this._esc(p.indemnitor_email || p.email || '')}')" style="font-size:10px;padding:2px 6px;color:#38bdf8" title="SwipeSimple credit card link">💳 Card</button>
            <button type="button" class="inv-btn" onclick="SLPaperwork.openCashModal('${this._esc(pid)}', ${amt})" style="font-size:10px;padding:2px 6px;color:#4ade80" title="Log cash payment">💵 Cash</button>
            ${p.drive_url ? `<a href="${this._esc(p.drive_url)}" target="_blank" class="inv-btn" style="font-size:10px;padding:2px 6px;color:#c084fc" title="View signed PDF folder in Drive">☁️ Drive</a>` : ''}
            ${this._partyActionButtons(p)}
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
  openSwipeSimpleModal(packetId = 'GENERAL', amount = 500.0, phone = '', email = '') {
    this._activePacketId = packetId;
    const modal = document.getElementById('pwSwipeSimpleModal');
    const amtEl = document.getElementById('pwSwipeSimpleAmount');
    const phoneEl = document.getElementById('pwSwipeSimplePhone');
    const emailEl = document.getElementById('pwSwipeSimpleEmail');
    if (amtEl) amtEl.value = amount;
    if (phoneEl && phone) phoneEl.value = phone;
    if (emailEl && email) emailEl.value = email;
    if (modal) { modal.style.display = 'flex'; modal.classList.add('active'); }
  },

  closeSwipeSimpleModal() {
    const modal = document.getElementById('pwSwipeSimpleModal');
    if (modal) { modal.style.display = 'none'; modal.classList.remove('active'); }
  },

  async sendSwipeSimpleLink() {
    const amount = parseFloat(document.getElementById('pwSwipeSimpleAmount')?.value || '0');
    const phone = document.getElementById('pwSwipeSimplePhone')?.value || '';
    const email = document.getElementById('pwSwipeSimpleEmail')?.value || '';
    const deliver_text = document.getElementById('pwSwipeSimpleCheckText')?.checked ?? true;
    const deliver_email = document.getElementById('pwSwipeSimpleCheckEmail')?.checked ?? true;

    try {
      const res = await fetch('/api/paperwork/payment/swipesimple-link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          packet_id: this._activePacketId,
          amount,
          phone,
          email,
          deliver: true,
          deliver_text,
          deliver_email,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || 'Failed to dispatch SwipeSimple payment request');

      const statusMsgs = [];
      if (data.text_delivered) statusMsgs.push(`Text delivered to ${data.recipient_phone}`);
      else if (deliver_text && phone) statusMsgs.push(`Text failed (${data.text_error || 'unreachable'})`);

      if (data.email_delivered) statusMsgs.push(`Email delivered to ${data.recipient_email}`);
      else if (deliver_email && email) statusMsgs.push(`Email failed (${data.email_error || 'unreachable'})`);

      const summary = statusMsgs.length > 0 ? statusMsgs.join(' · ') : 'Payment link generated';
      alert(`💳 SwipeSimple payment request processed!\nAmount: $${data.amount?.toLocaleString('en-US', { minimumFractionDigits: 2 }) || amount}\n${summary}`);
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

  _partyActionButtons(p) {
    const pid = p.packet_id || '';
    const parties = Array.isArray(p.parties) ? p.parties : [];
    if (!parties.length) {
      if ((p.status || '') === 'voided') return '';
      return `<button type="button" class="inv-btn" onclick="SLPaperwork.deliverPacket('${this._esc(pid)}')" style="font-size:10px;padding:2px 6px;color:#34d399" title="Deliver via iMessage / SMS">📱 Send</button>`;
    }
    return parties.map(party => {
      const role = party.role || 'indemnitor';
      const label = role === 'defendant' ? 'Def' : (role === 'coindemnitor' ? 'Co' : 'Ind');
      const short = this._esc(pid);
      const r = this._esc(role);
      return `<button type="button" class="inv-btn" onclick="SLPaperwork.copyPartyLink('${short}','${r}')" style="font-size:10px;padding:2px 6px;color:#93c5fd" title="Copy ${this._esc(party.label || role)} sign link">📋 ${label}</button>`
        + `<button type="button" class="inv-btn" onclick="SLPaperwork.deliverPacket('${short}','${r}')" style="font-size:10px;padding:2px 6px;color:#34d399" title="iMessage ${this._esc(party.label || role)}">📱 ${label}</button>`;
    }).join('');
  },

  _partyFromCache(packetId, role) {
    const pkt = (this._allPackets || []).find(p => p.packet_id === packetId) || {};
    const parties = pkt.parties || [];
    return parties.find(p => (p.role || '') === role) || parties[0] || null;
  },

  async copyPartyLink(packetId, role) {
    const party = this._partyFromCache(packetId, role);
    const url = party?.share_url || party?.sign_url || '';
    if (!url) {
      alert('No sign link for that party yet — finalize the packet first.');
      return;
    }
    try {
      await navigator.clipboard.writeText(url);
      this._setApStatus(`Copied ${party.label || role} link.`, 'success');
    } catch (err) {
      prompt('Copy this signing link:', url);
    }
  },

  async deliverPacket(packetId, role) {
    const party = this._partyFromCache(packetId, role);
    const who = party?.label || role || 'client';
    if (!confirm(`Send the ${who} signing link via iMessage / SMS?`)) return;
    try {
      const res = await fetch(`/api/paperwork/${packetId}/deliver`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: role || undefined, phone: party?.phone || '' }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || 'Delivery failed');
      alert(`📱 ${who} link sent to ${data.recipient || party?.phone || 'client'}`);
      this.loadLivePackets();
    } catch (err) {
      alert(`❌ Delivery error: ${err.message}`);
    }
  },

  renderPartyCards(parties, packetId) {
    const el = document.getElementById('pwApParties');
    if (!el) return;
    const rows = Array.isArray(parties) ? parties : [];
    if (!rows.length) {
      el.style.display = 'none';
      el.innerHTML = '';
      return;
    }
    el.style.display = 'grid';
    el.innerHTML = rows.map(p => {
      const role = this._esc(p.role || '');
      const pid = this._esc(packetId || '');
      const url = this._esc(p.share_url || p.sign_url || '');
      return `<div style="background:#0f172a;border:1px solid #334155;border-radius:10px;padding:12px">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em">${this._esc(p.label || p.role)}</div>
        <div style="font-weight:700;margin:4px 0 8px">${this._esc(p.name || '—')}</div>
        <div style="font-size:11px;color:#64748b;word-break:break-all;margin-bottom:8px">${url}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button type="button" class="inv-btn" onclick="SLPaperwork.copyPartyLink('${pid}','${role}')">📋 Copy link</button>
          <button type="button" class="inv-btn" style="color:#34d399" onclick="SLPaperwork.deliverPacket('${pid}','${role}')">📱 Send iMessage</button>
          <a class="inv-btn" href="${url}" target="_blank" rel="noopener">✍️ Open</a>
        </div>
      </div>`;
    }).join('');
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

  /* ─────────────────────────────────────────────────────────────────────────────
   * Adaptive Case Packet Builder (match → auto-fill → drag extras → flatten → send)
   * ───────────────────────────────────────────────────────────────────────────── */
  _setAdaptiveField(id, value) {
    if (value == null || value === '') return;
    const el = document.getElementById(id);
    if (el) el.value = value;
  },

  _applyBondSeed(seed = {}) {
    this._setAdaptiveField('pwApBooking', seed.booking_number);
    this._setAdaptiveField('pwApCounty', seed.county);
    this._setAdaptiveField('pwApLookupId', seed.packet_id || seed.intake_id);
    this._setAdaptiveField('pwApPoa', seed.poa_number);
    this._setAdaptiveField('pwApCaseNumber', seed.case_number);
    this._setAdaptiveField('pwApBondAmount', seed.bond_amount);
    this._setAdaptiveField('pwApDefName', seed.defendant_name);
    this._setAdaptiveField('pwApDefDob', seed.defendant_dob);
    this._setAdaptiveField('pwApDefPhone', seed.defendant_phone);
    this._setAdaptiveField('pwApDefAddress', seed.defendant_address);
    this._setAdaptiveField('pwApIndName', seed.indemnitor_name);
    this._setAdaptiveField('pwApIndPhone', seed.indemnitor_phone);
    this._setAdaptiveField('pwApIndEmail', seed.indemnitor_email);
    this._setAdaptiveField('pwApIndAddress', seed.indemnitor_address);
    if (seed.surety_id) {
      const sur = document.getElementById('pwApSurety');
      if (sur) {
        const raw = String(seed.surety_id).toLowerCase();
        sur.value = (raw.includes('palm') || raw.includes('psc')) ? 'palmetto' : 'osi';
      }
    }
  },

  /**
   * Open OSI/Palmetto DocuSeal packet builder from a recorded bond or new indemnitor.
   * Switches to Paperwork so the modal is not trapped in a hidden tab.
   */
  startFromBond(seed = {}) {
    if (window.SL && typeof SL.switchTab === 'function') {
      SL.switchTab('tabPaperwork');
    }
    if (typeof this.loadLivePackets === 'function') {
      this.loadLivePackets().catch(() => {});
    }
    this.openAdaptivePacketModal(seed);
  },

  openAdaptivePacketModal(seed = {}) {
    const modal = document.getElementById('pwAdaptivePacketModal');
    if (!modal) return;
    this._casePacketKeys = [];
    this._extraUploads = [];
    this._adaptiveContext = null;
    this._adaptiveFields = null;
    this._draggedDocKey = null;
    this._pendingBondSeed = seed;
    this._adaptiveReadiness = null;
    this._setAdaptiveAuthoritativeFieldsLocked(false);
    const approval = document.getElementById('pwApStaffApproval');
    if (approval) approval.checked = false;

    this._applyBondSeed(seed);
    this._renderDocuSealReadiness({});

    this.renderAdaptivePalette();
    this.renderCasePacketDrop();
    this.renderExtraList();
    this._setApStatus(
      seed.booking_number
        ? `Loading OSI packet for booking ${seed.booking_number}…`
        : '',
      seed.booking_number ? 'info' : null,
    );
    modal.style.display = 'flex';
    modal.classList.add('active');

    if (seed.packet_id || seed.intake_id || seed.booking_number || seed.match_id || seed.bond_case_id) {
      this.loadAdaptiveContext(seed);
    }
  },

  closeAdaptivePacketModal() {
    const modal = document.getElementById('pwAdaptivePacketModal');
    if (modal) {
      modal.style.display = 'none';
      modal.classList.remove('active');
    }
  },

  _setAdaptiveAuthoritativeFieldsLocked(locked) {
    const ids = [
      'pwApBooking', 'pwApCounty', 'pwApDefName', 'pwApDefDob', 'pwApDefPhone',
      'pwApDefAddress', 'pwApIndName', 'pwApIndPhone', 'pwApIndEmail',
      'pwApIndAddress', 'pwApCaseNumber', 'pwApPoa', 'pwApBondAmount'
    ];
    ids.forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.readOnly = !!locked;
        el.setAttribute('aria-readonly', locked ? 'true' : 'false');
        el.style.opacity = locked ? '.82' : '';
      }
    });
    const surety = document.getElementById('pwApSurety');
    if (surety) surety.disabled = !!locked;
  },

  _deriveDocuSealReadiness(data = {}) {
    const ctx = data.context || this._adaptiveContext || {};
    const hydration = data.hydration || {};
    const score = Number(hydration.hydration_score ?? 0);
    const surety = String(ctx.surety_id || '').toLowerCase();
    const matchStatus = String(ctx.match_status || '').toLowerCase();
    const steps = [
      { key: 'match', label: 'Validated Match', detail: matchStatus || 'not resolved', ready: matchStatus === 'validated' },
      { key: 'case', label: 'BondCase', detail: ctx.bond_case_id || 'not linked', ready: !!ctx.bond_case_id },
      { key: 'surety', label: 'Surety Confirmed', detail: surety ? surety.toUpperCase() : 'not assigned', ready: surety === 'osi' || surety === 'palmetto' },
      { key: 'poa', label: 'POA Assigned', detail: ctx.poa_number || 'not assigned', ready: !!ctx.poa_number },
      { key: 'template', label: 'DocuSeal Ready', detail: data.providers?.docuseal ? `hydration ${score}%` : 'template/API unavailable', ready: !!data.providers?.docuseal && score >= 100 },
    ];
    return { steps, hydration_score: score, chainComplete: steps.every(step => step.ready) };
  },

  _renderDocuSealReadiness(data = {}) {
    this._adaptiveReadiness = this._deriveDocuSealReadiness(data);
    const el = document.getElementById('pwApReadinessRail');
    if (el) {
      el.innerHTML = this._adaptiveReadiness.steps.map(step => {
        const color = step.ready ? '#4ade80' : '#fbbf24';
        const mark = step.ready ? '✓' : '○';
        return `<div style="min-width:132px;flex:1;background:rgba(15,23,42,.72);border:1px solid ${step.ready ? 'rgba(74,222,128,.45)' : 'rgba(251,191,36,.45)'};border-radius:8px;padding:8px 9px">
          <div style="font-size:11px;font-weight:700;color:${color}">${mark} ${this._esc(step.label)}</div>
          <div style="font-size:10px;color:#94a3b8;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${this._esc(step.detail)}">${this._esc(step.detail)}</div>
        </div>`;
      }).join('');
    }
    const summary = document.getElementById('pwApReadinessSummary');
    if (summary) {
      summary.textContent = this._adaptiveReadiness.chainComplete
        ? 'Case chain is verified. Staff approval is required before sending.'
        : 'Resolve a validated case; incomplete or ambiguous records remain blocked.';
      summary.style.color = this._adaptiveReadiness.chainComplete ? '#86efac' : '#fbbf24';
    }
    this._setAdaptiveFinalizeEnabled();
  },

  _setAdaptiveFinalizeEnabled() {
    const button = document.getElementById('pwApFinalizeBtn');
    if (!button) return;
    const approved = !!document.getElementById('pwApStaffApproval')?.checked;
    const ready = !!this._adaptiveReadiness?.chainComplete;
    button.disabled = !(ready && approved);
    button.style.opacity = button.disabled ? '.48' : '';
    button.style.cursor = button.disabled ? 'not-allowed' : '';
    button.title = button.disabled
      ? 'Resolve the case chain and record staff approval before sending.'
      : 'Create the verified DocuSeal packet and release it for signature.';
  },

  toggleSelfIndemnitorUI() {
    const on = document.getElementById('pwApSelfIndemnitor')?.checked;
    const pin = document.getElementById('pwApSelfPin');
    if (pin) pin.style.display = on ? 'inline-block' : 'none';
    if (on) {
      // Pre-copy defendant → indemnitor fields in UI (still requires PIN on finalize)
      const map = [
        ['pwApDefName', 'pwApIndName'],
        ['pwApDefPhone', 'pwApIndPhone'],
        ['pwApDefAddress', 'pwApIndAddress'],
      ];
      map.forEach(([a, b]) => {
        const src = document.getElementById(a);
        const dst = document.getElementById(b);
        if (src && dst && src.value && !dst.value) dst.value = src.value;
      });
    }
  },

  renderAdaptivePalette() {
    const el = document.getElementById('pwApPalette');
    if (!el) return;
    el.innerHTML = '';
    this._docCatalog.forEach(doc => {
      const card = document.createElement('div');
      card.draggable = true;
      card.dataset.docKey = doc.key;
      card.style.cssText = 'background:rgba(30,41,59,.9);border:1px solid rgba(255,255,255,.1);border-radius:6px;padding:8px 10px;cursor:grab;font-size:11px';
      card.ondragstart = (e) => this.handleDragStart(e, doc.key);
      card.innerHTML = `<strong>${doc.icon} ${this._esc(doc.label)}</strong>
        <div style="color:#64748b;margin-top:2px">${this._esc(doc.desc)}</div>`;
      card.ondblclick = () => {
        if (!this._casePacketKeys.includes(doc.key)) {
          this._casePacketKeys.push(doc.key);
          this.renderCasePacketDrop();
        }
      };
      el.appendChild(card);
    });
  },

  renderCasePacketDrop() {
    const box = document.getElementById('pwApPacketDrop');
    const count = document.getElementById('pwApPacketCount');
    if (count) count.textContent = `(${this._casePacketKeys.length})`;
    if (!box) return;
    if (!this._casePacketKeys.length) {
      box.innerHTML = `<div style="font-size:11px;color:#64748b;text-align:center;padding:24px">Drag document cards here · rules auto-seed after resolve</div>`;
      return;
    }
    box.innerHTML = '';
    this._casePacketKeys.forEach((key, idx) => {
      const cat = this._docCatalog.find(d => d.key === key) || { label: key, icon: '📄' };
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;justify-content:space-between;background:rgba(15,23,42,.85);border:1px solid rgba(56,189,248,.25);border-radius:6px;padding:6px 8px;margin-bottom:6px;font-size:12px';
      row.innerHTML = `<span>${idx + 1}. ${cat.icon || '📄'} ${this._esc(cat.label || key)}</span>
        <button type="button" style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:14px" title="Remove">✕</button>`;
      row.querySelector('button').onclick = () => {
        this._casePacketKeys = this._casePacketKeys.filter(k => k !== key);
        this.renderCasePacketDrop();
      };
      box.appendChild(row);
    });
  },

  handleCasePacketDragOver(evt) {
    evt.preventDefault();
    evt.currentTarget.style.borderColor = '#38bdf8';
  },

  handleCasePacketDragLeave(evt) {
    evt.currentTarget.style.borderColor = '';
  },

  handleCasePacketDrop(evt) {
    evt.preventDefault();
    evt.currentTarget.style.borderColor = '';
    const key = evt.dataTransfer.getData('text/plain') || this._draggedDocKey;
    if (!key) return;
    if (!this._casePacketKeys.includes(key)) {
      this._casePacketKeys.push(key);
      this.renderCasePacketDrop();
    }
  },

  renderExtraList() {
    const el = document.getElementById('pwApExtraList');
    if (!el) return;
    if (!this._extraUploads.length) {
      el.innerHTML = '';
      return;
    }
    el.innerHTML = this._extraUploads.map((f, i) =>
      `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,.06)">
        <span>📎 ${this._esc(f.filename)} <span style="color:#64748b">(${Math.round((f.size || 0) / 1024)} KB)</span></span>
        <button type="button" data-i="${i}" style="background:none;border:none;color:#ef4444;cursor:pointer">remove</button>
      </div>`
    ).join('');
    el.querySelectorAll('button[data-i]').forEach(btn => {
      btn.onclick = () => {
        this._extraUploads.splice(Number(btn.dataset.i), 1);
        this.renderExtraList();
      };
    });
  },

  async _readFileAsUpload(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        resolve({
          filename: file.name,
          content_type: file.type || 'application/pdf',
          data_b64: String(reader.result || ''),
          size: file.size,
        });
      };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  },

  async handleExtraFileDrop(evt) {
    evt.preventDefault();
    evt.currentTarget.style.borderColor = '';
    const files = Array.from(evt.dataTransfer?.files || []);
    for (const f of files) {
      if (!/\.pdf$/i.test(f.name) && f.type !== 'application/pdf') continue;
      const up = await this._readFileAsUpload(f);
      this._extraUploads.push(up);
    }
    this.renderExtraList();
  },

  async handleExtraFilePick(evt) {
    const files = Array.from(evt.target.files || []);
    for (const f of files) {
      const up = await this._readFileAsUpload(f);
      this._extraUploads.push(up);
    }
    this.renderExtraList();
    evt.target.value = '';
  },

  _setApStatus(msg, type) {
    const el = document.getElementById('pwApStatus');
    if (!el) return;
    if (!msg) {
      el.style.display = 'none';
      el.textContent = '';
      return;
    }
    el.style.display = 'block';
    el.textContent = msg;
    el.style.background = type === 'error' ? 'rgba(153,27,27,.35)'
      : type === 'success' ? 'rgba(22,101,52,.35)'
      : 'rgba(30,58,138,.35)';
    el.style.color = '#e2e8f0';
    el.style.border = '1px solid rgba(255,255,255,.08)';
  },

  _lookupBody(seed = {}) {
    const lookup = (document.getElementById('pwApLookupId')?.value || '').trim();
    const booking = (document.getElementById('pwApBooking')?.value || seed.booking_number || '').trim();
    const county = (document.getElementById('pwApCounty')?.value || seed.county || '').trim();
    const body = {
      booking_number: booking || undefined,
      county: county || undefined,
      self_indemnitor: !!document.getElementById('pwApSelfIndemnitor')?.checked,
      authorization_pin: document.getElementById('pwApSelfPin')?.value || '',
      include_payment_plan: !!document.getElementById('pwApPaymentPlan')?.checked,
      extra_doc_keys: this._casePacketKeys.slice(),
    };
    if (seed.packet_id) body.packet_id = seed.packet_id;
    if (seed.intake_id) body.intake_id = seed.intake_id;
    if (seed.match_id) body.match_id = seed.match_id;
    if (seed.bond_case_id) body.bond_case_id = seed.bond_case_id;
    if (seed.defendant_id) body.defendant_id = seed.defendant_id;
    if (lookup) {
      if (lookup.startsWith('PKT-') || lookup.toLowerCase().startsWith('pkt')) body.packet_id = lookup;
      else if (lookup.startsWith('INT-') || lookup.toLowerCase().includes('intake')) body.intake_id = lookup;
      else if (/match/i.test(lookup) || lookup.length > 20) body.match_id = lookup;
      else {
        // Free-form identifiers are treated as Match IDs only. Never fan a
        // single ambiguous value out to packet, intake, and match lookups.
        body.match_id = lookup;
      }
    }
    return body;
  },

  _fillFormFromContext(ctx, fields) {
    const def = ctx.defendant || {};
    const ind = ctx.indemnitor || {};
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el && (v != null && v !== '')) el.value = v;
    };
    set('pwApDefName', def.name);
    set('pwApDefDob', def.dob);
    set('pwApDefPhone', def.phone);
    set('pwApDefAddress', def.address);
    set('pwApIndName', ind.name);
    set('pwApIndPhone', ind.phone);
    set('pwApIndEmail', ind.email);
    set('pwApIndAddress', ind.address);
    set('pwApCaseNumber', ctx.case_number);
    set('pwApPoa', ctx.poa_number);
    set('pwApBondAmount', ctx.bond_amount || '');
    set('pwApBooking', ctx.booking_number);
    set('pwApCounty', ctx.county);
    const sur = document.getElementById('pwApSurety');
    if (sur && ctx.surety_id) sur.value = ctx.surety_id;
    if (ctx.self_indemnitor) {
      const cb = document.getElementById('pwApSelfIndemnitor');
      if (cb) cb.checked = true;
      this.toggleSelfIndemnitorUI();
    }
  },

  async loadAdaptiveContext(seed = {}) {
    this._setApStatus('Resolving defendant / indemnitor match…', 'info');
    try {
      const res = await fetch('/api/paperwork/packet/context', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(this._lookupBody(seed)),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || `HTTP ${res.status}`);

      this._adaptiveContext = data.context;
      this._adaptiveFields = data.fields;
      this._fillFormFromContext(data.context, data.fields);
      this._setAdaptiveAuthoritativeFieldsLocked(true);
      this._renderDocuSealReadiness(data);

      // Seed packet from rules manifest when empty
      if (!this._casePacketKeys.length && Array.isArray(data.manifest)) {
        this._casePacketKeys = data.manifest
          .map(m => m.catalog_key)
          .filter(Boolean);
        this.renderCasePacketDrop();
      }

      const h = data.hydration || {};
      const badge = document.getElementById('pwApHydrationBadge');
      if (badge) {
        const sc = h.hydration_score ?? 0;
        const color = sc >= 100 ? '#10b981' : sc >= 70 ? '#f59e0b' : '#ef4444';
        badge.style.color = color;
        badge.textContent = `Hydration: ${sc}% (${h.hydrated_count || 0}/${h.total_required || 0})`;
      }
      const src = document.getElementById('pwApSources');
      if (src) {
        const sources = (data.context?.sources || []).join(', ') || 'none';
        const small = data.context?.is_small_bond ? ' · small-bond eligible' : '';
        src.textContent = `Sources: ${sources}${small}`;
      }

      // E-sign is DocuSeal-only for new packets
      const prov = document.getElementById('pwApProvider');
      if (prov) {
        const preferred = data.esign_provider || data.context?.esign_provider || 'docuseal';
        prov.value = (preferred === 'none') ? 'none' : 'docuseal';
      }

      const pdfBadge = document.getElementById('pwApAdobePdfBadge');
      if (pdfBadge) {
        const dsOk = data.providers?.docuseal;
        pdfBadge.textContent = dsOk
          ? 'DocuSeal: ready · e-sign only'
          : 'DocuSeal: check API key / health';
        pdfBadge.style.color = dsOk ? '#4ade80' : '#fbbf24';
      }

      this._setApStatus(
        this._adaptiveReadiness?.chainComplete
          ? 'Case chain verified — review the DocuSeal prefill, then record staff approval before sending.'
          : 'Context loaded, but the case chain is incomplete. Resolve the flagged gate before any DocuSeal packet can be sent.',
        this._adaptiveReadiness?.chainComplete ? 'success' : 'error'
      );
    } catch (err) {
      this._adaptiveContext = null;
      this._adaptiveReadiness = null;
      this._setAdaptiveAuthoritativeFieldsLocked(false);
      this._renderDocuSealReadiness({});
      this._setApStatus(`Resolve failed: ${err.message}`, 'error');
    }
  },

  async previewAdaptiveHydration() {
    await this.loadAdaptiveContext();
    const h = this._adaptiveFields ? null : null;
    // re-fetch light audit from last context fields via finalize none is heavy; show modal audit from context response
    try {
      const res = await fetch('/api/paperwork/packet/context', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(this._lookupBody()),
      });
      const data = await res.json();
      if (!data.success) throw new Error(data.error || 'preview failed');
      const rows = (data.hydration?.fields || []).map(f =>
        `${f.hydrated ? '✓' : '✗'} ${f.label}: ${f.val || '—'}`
      ).join('\n');
      alert(`Hydration ${data.hydration?.hydration_score}%\n\n${rows}`);
    } catch (err) {
      alert(`Preview failed: ${err.message}`);
    }
  },

  /**
   * Dry-run DocuSeal prefill from current adaptive packet context.
   * Does NOT create a submission — use before Flatten & Send for OSI alignment.
   */
  async previewDocuSealPrefill() {
    this._setApStatus('Building DocuSeal prefill preview…', 'info');
    const selfInd = !!document.getElementById('pwApSelfIndemnitor')?.checked;
    const pin = document.getElementById('pwApSelfPin')?.value || '';
    const body = {
      ...this._lookupBody(),
      self_indemnitor: selfInd,
      authorization_pin: pin,
      surety_id: this._adaptiveContext?.surety_id || '',
      field_overrides: this._collectFieldOverrides(),
      poa_number: document.getElementById('pwApPoa')?.value || '',
      include_defendant: true,
    };
    try {
      const res = await fetch('/api/paperwork/docuseal/prefill-preview', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || `HTTP ${res.status}`);

      const pf = data.prefill || {};
      const keyLines = Object.keys(pf).sort().map(k => `${k}: ${pf[k]}`).join('\n');
      const msg = [
        `DocuSeal prefill · ${data.surety_id?.toUpperCase() || 'OSI'}`,
        `Template ID: ${data.template_id || 'NOT SET'} · keys: ${data.prefill_key_count || 0}`,
        `Can send: ${data.can_send ? 'YES' : 'NO'} · ${data.hint || ''}`,
        `Roles: ${(data.submitter_roles || []).join(' → ') || '—'}`,
        `Premium: ${data.premium_preview || '—'} · Charges: ${data.charges_summary || '—'}`,
        '',
        keyLines.slice(0, 3500) + (keyLines.length > 3500 ? '\n…' : ''),
      ].join('\n');

      this._setApStatus(
        data.can_send
          ? `DocuSeal prefill ready (${data.prefill_key_count} fields) · template ${data.template_id}`
          : `DocuSeal not ready: ${data.hint || 'check env / names'}`,
        data.can_send ? 'success' : 'error'
      );
      alert(msg);
    } catch (err) {
      this._setApStatus(`DocuSeal prefill preview failed: ${err.message}`, 'error');
      alert(`DocuSeal prefill preview failed: ${err.message}`);
    }
  },

  _collectFieldOverrides() {
    // Once a case resolves, all signature-bound data comes from the
    // authoritative server context. Packet-time UI edits must never override
    // identity, recipient, case, POA, or money values.
    if (this._adaptiveContext) return {};
    return {
      defendant_name: document.getElementById('pwApDefName')?.value || '',
      defendant_dob: document.getElementById('pwApDefDob')?.value || '',
      defendant_phone: document.getElementById('pwApDefPhone')?.value || '',
      defendant_address: document.getElementById('pwApDefAddress')?.value || '',
      indemnitor_name: document.getElementById('pwApIndName')?.value || '',
      indemnitor_phone: document.getElementById('pwApIndPhone')?.value || '',
      indemnitor_email: document.getElementById('pwApIndEmail')?.value || '',
      indemnitor_address: document.getElementById('pwApIndAddress')?.value || '',
      case_number: document.getElementById('pwApCaseNumber')?.value || '',
      booking_number: document.getElementById('pwApBooking')?.value || '',
      poa_number: document.getElementById('pwApPoa')?.value || '',
      bond_amount: document.getElementById('pwApBondAmount')?.value || '',
    };
  },

  async finalizeAdaptivePacket() {
    if (!this._adaptiveReadiness?.chainComplete || !this._adaptiveContext) {
      this._setApStatus('DocuSeal send is blocked until the server resolves a validated Match, BondCase, surety, POA, and complete hydration.', 'error');
      return;
    }
    if (!document.getElementById('pwApStaffApproval')?.checked) {
      this._setApStatus('Record staff approval after reviewing the authoritative case and DocuSeal prefill before sending.', 'error');
      return;
    }
    const selfInd = !!document.getElementById('pwApSelfIndemnitor')?.checked;
    const pin = document.getElementById('pwApSelfPin')?.value || '';
    if (selfInd && !pin) {
      this._setApStatus('Self-indemnitor requires Brendan authorization PIN.', 'error');
      return;
    }
    if (!this._casePacketKeys.length && !this._extraUploads.length) {
      this._setApStatus('Add at least one catalog document or extra PDF to the packet.', 'error');
      return;
    }

    let provider = document.getElementById('pwApProvider')?.value || 'docuseal';
    // SignNow/Adobe retired for new packets — force DocuSeal
    if (provider === 'signnow' || provider === 'adobe' || provider === 'both') {
      provider = 'docuseal';
    }
    this._setApStatus(
      provider === 'none' ? 'Finalizing (no e-sign send)…' : 'Hydrating DocuSeal packet…',
      'info'
    );

    const body = {
      ...this._lookupBody(),
      self_indemnitor: selfInd,
      authorization_pin: pin,
      surety_id: this._adaptiveContext?.surety_id || '',
      include_payment_plan: !!document.getElementById('pwApPaymentPlan')?.checked,
      packet_doc_keys: this._casePacketKeys.slice(),
      extra_doc_keys: this._casePacketKeys.slice(),
      extra_uploads: this._extraUploads.map(u => ({
        filename: u.filename,
        content_type: u.content_type,
        data_b64: u.data_b64,
      })),
      field_overrides: this._collectFieldOverrides(),
      provider,
      staff_approved_for_send: true,
      include_defendant: document.getElementById('pwApIncludeDefendant')
        ? !!document.getElementById('pwApIncludeDefendant').checked
        : true,
      signer_email: document.getElementById('pwApIndEmail')?.value || '',
      poa_number: document.getElementById('pwApPoa')?.value || '',
      routing_scenario: document.getElementById('pwApPoa')?.value ? 'all-in-one' : 'phase_1',
    };

    try {
      const res = await fetch('/api/paperwork/packet/finalize', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || `HTTP ${res.status}`);

      const ds = data.send_results?.docuseal;
      let providerMsg = '';
      if (ds) providerMsg += ds.success ? ' DocuSeal ✓' : ` DocuSeal ✗ (${ds.error || 'fail'})`;
      const parties = data.parties || ds?.parties || [];
      if (parties.length) {
        const idx = (this._allPackets || []).findIndex(p => p.packet_id === data.packet_id);
        const merged = { ...(idx >= 0 ? this._allPackets[idx] : {}), packet_id: data.packet_id, parties };
        if (idx >= 0) this._allPackets[idx] = merged;
        else this._allPackets.unshift(merged);
      }
      this._setApStatus(
        `Packet ${data.packet_id} created from the verified case · ${parties.length || 0} signer link(s) · hydration ${data.hydration?.hydration_score ?? '—'}%${providerMsg}`,
        'success'
      );
      this.renderPartyCards(parties, data.packet_id);
      this.loadLivePackets();
    } catch (err) {
      this._setApStatus(`Finalize failed: ${err.message}`, 'error');
    }
  },

  // ═══════════════════════════════════════════════════════════════════
  // Adobe PDF Tools — standalone document intelligence panel
  // ═══════════════════════════════════════════════════════════════════

  _adobeToolsPdfBytes: null,
  _adobeToolsFileName: null,
  _adobeToolsLastResult: null,
  _adobeToolsLastMode: null,

  async initAdobeTools() {
    const badge = document.getElementById('adobeToolsStatusBadge');
    if (badge) {
      badge.textContent = 'Checking credentials…';
      badge.style.color = '#94a3b8';
      badge.style.borderColor = '#334155';
    }
    try {
      const res = await fetch('/api/paperwork/providers', { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const pdfOk = data.providers?.adobe_pdf_services || data.adobe?.pdf_services?.configured;
      const sdkOk = data.adobe?.pdf_services?.sdk_available;
      if (badge) {
        if (pdfOk) {
          badge.textContent = `Adobe PDF Services ✓${sdkOk === false ? ' (SDK not installed)' : ''}`;
          badge.style.color = '#4ade80';
          badge.style.borderColor = 'rgba(74,222,128,0.4)';
        } else {
          badge.textContent = '⚡ Native PDF Engine Active (PyMuPDF / ReportLab · Free)';
          badge.style.color = '#34d399';
          badge.style.borderColor = 'rgba(52,211,153,0.4)';
        }
      }
    } catch (e) {
      if (badge) { badge.textContent = 'Status check failed'; badge.style.color = '#f87171'; }
    }
  },

  handleAdobeToolsDrop(evt) {
    evt.preventDefault();
    const dz = document.getElementById('adobeToolsDropZone');
    if (dz) dz.classList.remove('adobe-dz-active');
    const file = evt.dataTransfer?.files?.[0];
    if (file) this._loadAdobeToolsFile(file);
  },

  handleAdobeToolsPick(evt) {
    const file = evt.target?.files?.[0];
    if (file) this._loadAdobeToolsFile(file);
    // reset input so same file can be re-picked
    if (evt.target) evt.target.value = '';
  },

  _loadAdobeToolsFile(file) {
    if (!file || file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
      alert('Please drop a PDF file.');
      return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      this._adobeToolsPdfBytes = e.target.result; // ArrayBuffer
      this._adobeToolsFileName = file.name;
      this._adobeToolsLastResult = null;
      const info = document.getElementById('adobeToolsFileInfo');
      const nameEl = document.getElementById('adobeToolsFileName');
      const sizeEl = document.getElementById('adobeToolsFileSize');
      if (info) info.style.display = 'block';
      if (nameEl) nameEl.textContent = file.name;
      if (sizeEl) sizeEl.textContent = `${(file.size / 1024).toFixed(1)} KB`;
      this._clearAdobeToolsResults();
    };
    reader.readAsArrayBuffer(file);
  },

  _arrayBufferToBase64(buffer) {
    let binary = '';
    const bytes = new Uint8Array(buffer);
    for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  },

  _setAdobeProgress(msg) {
    const el = document.getElementById('adobeToolsProgress');
    const msgEl = document.getElementById('adobeToolsProgressMsg');
    if (el) el.style.display = msg ? 'block' : 'none';
    if (msgEl) msgEl.textContent = msg || '';
  },

  _clearAdobeToolsResults() {
    const el = document.getElementById('adobeToolsResults');
    if (el) el.style.display = 'none';
    const pre = document.getElementById('adobeToolsResultPre');
    if (pre) pre.textContent = '';
    this._setAdobeProgress('');
  },

  _showAdobeResult(title, text, mode) {
    this._adobeToolsLastResult = text;
    this._adobeToolsLastMode = mode;
    const el = document.getElementById('adobeToolsResults');
    const titleEl = document.getElementById('adobeToolsResultTitle');
    const pre = document.getElementById('adobeToolsResultPre');
    if (el) el.style.display = 'block';
    if (titleEl) titleEl.textContent = title;
    if (pre) pre.textContent = text;
    this._setAdobeProgress('');
  },

  _setAdobeBtnState(running) {
    ['adobeMarkdownBtn', 'adobeExtractBtn', 'adobeAutotagBtn'].forEach(id => {
      const btn = document.getElementById(id);
      if (btn) btn.disabled = running;
    });
  },

  async runAdobePdfToMarkdown() {
    if (!this._adobeToolsPdfBytes) { alert('Drop a PDF first.'); return; }
    this._setAdobeBtnState(true);
    this._setAdobeProgress('Uploading to Adobe PDF Services → converting to Markdown…');
    try {
      const b64 = this._arrayBufferToBase64(this._adobeToolsPdfBytes);
      const res = await fetch('/api/paperwork/pdf-to-markdown', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pdf_b64: b64, bake_forms: true }),
      });
      const data = await res.json();
      if (!data.success) throw new Error(data.error || 'Conversion failed');
      this._showAdobeResult(
        `Markdown — ${this._adobeToolsFileName} (${(data.size / 1024).toFixed(1)} KB via ${data.engine})`,
        data.markdown,
        'md'
      );
    } catch (err) {
      this._setAdobeProgress('');
      alert(`PDF → Markdown failed: ${err.message}`);
    } finally {
      this._setAdobeBtnState(false);
    }
  },

  async runAdobeExtract() {
    if (!this._adobeToolsPdfBytes) { alert('Drop a PDF first.'); return; }
    this._setAdobeBtnState(true);
    this._setAdobeProgress('Uploading to Adobe PDF Extract API… (may take 30–60s)');
    try {
      const b64 = this._arrayBufferToBase64(this._adobeToolsPdfBytes);
      const res = await fetch('/api/paperwork/pdf-extract', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pdf_b64: b64, extract_text: true, extract_tables: true, table_xlsx: true }),
      });
      const data = await res.json();
      if (!data.success) throw new Error(data.error || 'Extract failed');
      const preview = data.text_preview || JSON.stringify(data.structured_data, null, 2);
      this._showAdobeResult(
        `Extract — ${this._adobeToolsFileName} (${data.zip_names?.length ?? 0} resources via ${data.engine})`,
        preview,
        'json'
      );
    } catch (err) {
      this._setAdobeProgress('');
      alert(`PDF Extract failed: ${err.message}`);
    } finally {
      this._setAdobeBtnState(false);
    }
  },

  async runAdobeAutotag() {
    if (!this._adobeToolsPdfBytes) { alert('Drop a PDF first.'); return; }
    this._setAdobeBtnState(true);
    this._setAdobeProgress('Running PDF Accessibility Auto-Tag… baking forms + tagging structure…');
    try {
      const b64 = this._arrayBufferToBase64(this._adobeToolsPdfBytes);
      // Autotag uses the packet finalize pipeline — we call build_flattened_packet via a convenience endpoint
      // For now surface the preflight result via pdf-to-markdown bake_forms probe
      const res = await fetch('/api/paperwork/pdf-to-markdown', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pdf_b64: b64, bake_forms: true }),
      });
      const data = await res.json();
      const pf = data.preflight || {};
      const msg = [
        `File: ${this._adobeToolsFileName}`,
        `Size: ${(pf.size_bytes / 1024).toFixed(1)} KB`,
        `Pages: ${pf.pages ?? 'unknown'}`,
        `Had form widgets: ${pf.had_widgets ? 'Yes' : 'No'}`,
        `Baked forms: ${pf.baked_forms ? 'Yes' : 'No'}`,
        `Disqualified: ${pf.disqualified ? 'Yes' : 'No'}`,
        pf.warnings?.length ? `Warnings: ${pf.warnings.join('; ')}` : '',
        '',
        data.success ? '✅ PDF is compatible — Auto-Tag can be run via the Adaptive Packet Builder (set ADOBE_PDF_AUTOTAG=true in .env).' :
          `⚠️ Note: ${data.error || 'Could not verify'}`,
      ].filter(s => s !== null).join('\n');
      this._showAdobeResult(`Auto-Tag Preflight — ${this._adobeToolsFileName}`, msg, 'txt');
    } catch (err) {
      this._setAdobeProgress('');
      alert(`Auto-Tag check failed: ${err.message}`);
    } finally {
      this._setAdobeBtnState(false);
    }
  },

  copyAdobeResult() {
    if (!this._adobeToolsLastResult) return;
    navigator.clipboard.writeText(this._adobeToolsLastResult)
      .then(() => {
        const btn = document.getElementById('adobeToolsCopyBtn');
        if (btn) { btn.textContent = '✓ Copied!'; setTimeout(() => { btn.textContent = '📋 Copy'; }, 2000); }
      })
      .catch(() => alert('Copy failed — select the text manually.'));
  },

  downloadAdobeResult() {
    if (!this._adobeToolsLastResult) return;
    const ext = this._adobeToolsLastMode === 'md' ? 'md' : this._adobeToolsLastMode === 'json' ? 'json' : 'txt';
    const base = (this._adobeToolsFileName || 'output').replace(/\.pdf$/i, '');
    const blob = new Blob([this._adobeToolsLastResult], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${base}.${ext}`;
    a.click();
    URL.revokeObjectURL(a.href);
  },

  clearAdobeToolsFile() {
    this._adobeToolsPdfBytes = null;
    this._adobeToolsFileName = null;
    this._adobeToolsLastResult = null;
    const info = document.getElementById('adobeToolsFileInfo');
    if (info) info.style.display = 'none';
    this._clearAdobeToolsResults();
  },

};

window.SLPaperwork = SLPaperwork;
