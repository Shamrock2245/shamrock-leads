/* ═══════════════════════════════════════════════════════════
   ShamrockLeads — Record Bond (Retrospective)
   Modal for recording manually-written bonds into the system
   ═══════════════════════════════════════════════════════════ */

const SLRecordBond = (() => {
  const API = window.API || '';
  const $ = id => document.getElementById(id);
  const money = n => '$' + (parseFloat(n)||0).toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0});
  const toast = (msg,type) => { if(window.SL?.toast) SL.toast(msg,type); else if(window.showToast) showToast(msg,type); else alert(msg); };

  let _prefillData = {};
  let _suggestedPOA = null;

  // ── Open Modal ──────────────────────────────────────────────────────────
  function open(data = {}) {
    _prefillData = data;
    const modal = $('recordBondModal');
    if (!modal) { console.error('Record Bond modal not found'); return; }

    // Pre-fill from defendant/arrest data
    $('rbDefendantName').value = data.defendant_name || data.full_name || '';
    $('rbBookingNumber').value = data.booking_number || '';
    $('rbCounty').value = data.county || '';
    $('rbFacility').value = data.facility || '';
    $('rbCharges').value = data.charges || '';

    // Bond amount — handle string or number
    const bondAmt = parseFloat(data.bond_amount || data.total_bond_amount || 0);
    $('rbBondAmount').value = bondAmt > 0 ? bondAmt : '';

    // Auto-calc premium (10%)
    const premiumAmt = bondAmt > 0 ? Math.round(bondAmt * 0.10) : '';
    $('rbPremium').value = premiumAmt;

    // Defaults
    $('rbSurety').value = (data.insurance_company || data.surety || 'osi').toLowerCase();
    $('rbPOANumber').value = data.poa_number || '';
    $('rbCaseNumber').value = data.case_number || '';
    $('rbCourtDate').value = data.court_date || '';
    $('rbCourtTime').value = data.court_time || '8:30 AM';
    $('rbCourtLocation').value = data.court_location || '';
    $('rbBondDate').value = data.bond_date || new Date().toISOString().split('T')[0];
    $('rbIndemnitorName').value = data.indemnitor_name || '';
    $('rbIndemnitorPhone').value = data.indemnitor_phone || '';
    $('rbIndemnitorEmail').value = data.indemnitor_email || '';
    $('rbIndemnitorRelationship').value = data.indemnitor_relationship || '';
    $('rbPaymentMethod').value = data.payment_method || 'cash';
    $('rbAgentName').value = data.agent_name || "Brendan O'Neal";
    $('rbNotes').value = data.notes || '';

    // Clear status
    $('rbSubmitStatus').innerHTML = '';
    $('rbSubmitBtn').disabled = false;

    // Show modal
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';

    // Auto-suggest POA
    suggestPOA();
  }

  // ── Close Modal ─────────────────────────────────────────────────────────
  function close() {
    const modal = $('recordBondModal');
    if (modal) modal.classList.remove('active');
    document.body.style.overflow = '';
    _prefillData = {};
    _suggestedPOA = null;
  }

  // ── Auto-calculate premium when bond amount changes ─────────────────────
  function calcPremium() {
    const bondAmt = parseFloat($('rbBondAmount')?.value || 0);
    const premiumEl = $('rbPremium');
    if (premiumEl && bondAmt > 0 && !premiumEl.dataset.manualEdit) {
      premiumEl.value = Math.round(bondAmt * 0.10);
    }
  }

  function markPremiumManual() {
    const el = $('rbPremium');
    if (el) el.dataset.manualEdit = 'true';
  }

  // ── Suggest next available POA ──────────────────────────────────────────
  async function suggestPOA() {
    const surety = $('rbSurety')?.value || 'osi';
    const bondAmount = parseFloat($('rbBondAmount')?.value || 0);
    const hintEl = $('rbPOAHint');

    try {
      const params = new URLSearchParams({ surety, bond_amount: bondAmount });
      const r = await fetch(`${API}/api/poa/next?${params}`);
      const d = await r.json();

      if (d.suggested && d.suggested.length > 0) {
        _suggestedPOA = d.suggested[0];
        const poaInput = $('rbPOANumber');
        if (poaInput && !poaInput.value) {
          poaInput.value = _suggestedPOA.poa_number || '';
          poaInput.placeholder = _suggestedPOA.poa_full || '';
        }
        if (hintEl) {
          hintEl.innerHTML = `<span class="rb-poa-hint">
            💡 Suggested: <strong>${_suggestedPOA.poa_full || _suggestedPOA.poa_number}</strong>
            (${d.available_in_tier} available in tier)
            ${d.warning ? `<span class="rb-poa-warn">⚠️ ${d.warning}</span>` : ''}
          </span>`;
        }
      } else {
        if (hintEl) hintEl.innerHTML = '<span class="rb-poa-warn">⚠️ No POAs available for this tier/surety</span>';
      }
    } catch(e) {
      if (hintEl) hintEl.innerHTML = '';
      console.warn('POA suggest failed:', e);
    }
  }

  // ── Submit Record Bond ──────────────────────────────────────────────────
  async function submit() {
    const btn = $('rbSubmitBtn');
    const statusEl = $('rbSubmitStatus');
    if (btn) btn.disabled = true;
    if (statusEl) statusEl.innerHTML = '<span class="rb-loading">☘️ Recording bond...</span>';

    const payload = {
      defendant_name: $('rbDefendantName')?.value || '',
      booking_number: $('rbBookingNumber')?.value || '',
      county: $('rbCounty')?.value || '',
      facility: $('rbFacility')?.value || '',
      charges: $('rbCharges')?.value || '',
      bond_amount: parseFloat($('rbBondAmount')?.value || 0),
      premium: parseFloat($('rbPremium')?.value || 0),
      surety: $('rbSurety')?.value || 'osi',
      poa_number: $('rbPOANumber')?.value || '',
      case_number: $('rbCaseNumber')?.value || '',
      court_date: $('rbCourtDate')?.value || '',
      court_time: $('rbCourtTime')?.value || '',
      court_location: $('rbCourtLocation')?.value || '',
      bond_date: $('rbBondDate')?.value || '',
      indemnitor_name: $('rbIndemnitorName')?.value || '',
      indemnitor_phone: $('rbIndemnitorPhone')?.value || '',
      indemnitor_email: $('rbIndemnitorEmail')?.value || '',
      indemnitor_relationship: $('rbIndemnitorRelationship')?.value || '',
      payment_method: $('rbPaymentMethod')?.value || 'cash',
      agent_name: $('rbAgentName')?.value || "Brendan O'Neal",
      notes: $('rbNotes')?.value || '',
    };

    // Client-side validation
    if (!payload.defendant_name || !payload.booking_number || !payload.poa_number) {
      if (statusEl) statusEl.innerHTML = '<span class="rb-error">❌ Name, Booking #, and POA # are required</span>';
      if (btn) btn.disabled = false;
      return;
    }

    try {
      const r = await fetch(`${API}/api/bonds/record`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const d = await r.json();

      if (d.success) {
        const poaInfo = d.poa?.was === 'created_and_assigned' ? ' (POA created)' : d.poa?.was === 'available' ? ' (POA assigned)' : '';
        if (statusEl) statusEl.innerHTML = `<span class="rb-success">
          ✅ Bond recorded! ${money(d.bond_amount)} bond · ${money(d.premium)} premium · ${d.surety}${poaInfo}
        </span>`;
        toast(`☘️ Bond recorded: ${payload.defendant_name} — ${money(d.bond_amount)}`, 'success');

        // Refresh dependent tabs
        setTimeout(() => {
          if (window.SLProspective?.load) SLProspective.load();
          if (window.SLActiveBonds?.load) SLActiveBonds.load();
          if (window.loadActiveBonds) loadActiveBonds();
          if (window.SLInventory?.load) SLInventory.load();
        }, 500);

        // Close modal after brief delay to show success
        setTimeout(close, 1500);
      } else {
        const errs = d.errors ? d.errors.join(', ') : (d.error || 'Unknown error');
        if (statusEl) statusEl.innerHTML = `<span class="rb-error">❌ ${errs}</span>`;
        if (btn) btn.disabled = false;
      }
    } catch(e) {
      if (statusEl) statusEl.innerHTML = `<span class="rb-error">❌ Network error: ${e.message}</span>`;
      if (btn) btn.disabled = false;
    }
  }

  // ── Keyboard Shortcut: Escape to close ──────────────────────────────────
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && $('recordBondModal')?.classList.contains('active')) close();
  });

  // Public API
  return { open, close, calcPremium, markPremiumManual, suggestPOA, submit };
})();

// Global alias for easy access from other modules
window.openRecordBondModal = SLRecordBond.open;
