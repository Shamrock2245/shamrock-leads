/* ═══════════════════════════════════════════════════════════
   ShamrockLeads — Record Bond (Retrospective)
   Modal for recording manually-written bonds into the system
   Features: defendant search, URL auto-population, NLP risk scoring
   ═══════════════════════════════════════════════════════════ */

window.SLRecordBond = (() => {
  const API = window.API || '';
  const $ = id => document.getElementById(id);
  const money = n => '$' + (parseFloat(n)||0).toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0});
  const toast = (msg,type) => { if(window.SL?.toast) SL.toast(msg,type); else if(window.showToast) showToast(msg,type); else alert(msg); };

  let _prefillData = {};
  let _suggestedPOA = null;
  let _searchTimer = null;
  let _searchResults = [];

  // ── Defendant Search — queries /api/arrests/search ──────────────────────
  function searchDefendants(query) {
    clearTimeout(_searchTimer);
    const resultsEl = $('rbSearchResults');
    const hintEl = $('rbSearchHint');

    if (!query || query.length < 2) {
      if (resultsEl) resultsEl.style.display = 'none';
      if (hintEl) hintEl.textContent = 'Type to search all arrest records. Select to auto-fill form.';
      _searchResults = [];
      return;
    }

    if (hintEl) hintEl.textContent = '⏳ Searching...';

    // Debounce — wait 300ms after last keystroke
    _searchTimer = setTimeout(async () => {
      try {
        const r = await fetch(`${API}/api/arrests/search?q=${encodeURIComponent(query)}&limit=15`);
        const d = await r.json();
        _searchResults = d.arrests || [];

        if (_searchResults.length === 0) {
          if (hintEl) hintEl.textContent = `No results for "${query}". You can still fill the form manually.`;
          if (resultsEl) resultsEl.style.display = 'none';
          return;
        }

        if (hintEl) hintEl.textContent = `${d.total} result${d.total !== 1 ? 's' : ''} found. Click to auto-fill.`;
        renderSearchResults();
      } catch (e) {
        if (hintEl) hintEl.textContent = '❌ Search failed. Fill form manually.';
        console.warn('Defendant search error:', e);
      }
    }, 300);
  }

  function renderSearchResults() {
    const resultsEl = $('rbSearchResults');
    if (!resultsEl || _searchResults.length === 0) return;

    const fmtBond = n => n ? '$' + Number(n).toLocaleString() : '—';
    resultsEl.innerHTML = _searchResults.map((a, i) => `
      <div class="rb-search-item" onclick="SLRecordBond.selectDefendant(${i})"
        style="padding:10px 14px;border-bottom:1px solid var(--border);cursor:pointer;transition:background .15s"
        onmouseenter="this.style.background='rgba(16,185,129,0.08)'"
        onmouseleave="this.style.background='transparent'">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <strong style="color:var(--text);font-size:13px">${a.full_name || 'Unknown'}</strong>
          <span style="font-size:12px;color:var(--accent);font-weight:600">${fmtBond(a.bond_amount || a.total_bond_amount)}</span>
        </div>
        <div style="font-size:11px;color:var(--muted);margin-top:2px;display:flex;gap:8px;flex-wrap:wrap">
          <span>📍 ${a.county || '—'}</span>
          <span>📋 ${a.booking_number || '—'}</span>
          <span>⚖️ ${(a.charges || '—').substring(0, 45)}${(a.charges || '').length > 45 ? '…' : ''}</span>
          ${a.custody_status ? `<span style="color:${a.custody_status.toLowerCase().includes('custody') ? 'var(--warning)' : 'var(--success)'}">● ${a.custody_status}</span>` : ''}
        </div>
      </div>
    `).join('');
    resultsEl.style.display = 'block';
  }

  function showResults() {
    if (_searchResults.length > 0) {
      renderSearchResults();
    }
  }

  function hideResults() {
    // Delay to allow click events on results
    setTimeout(() => {
      const resultsEl = $('rbSearchResults');
      if (resultsEl) resultsEl.style.display = 'none';
    }, 200);
  }

  function selectDefendant(index) {
    const arrest = _searchResults[index];
    if (!arrest) return;

    // Auto-fill ALL form fields from the selected arrest record
    // This works regardless of which county scraped the data
    $('rbDefendantName').value = arrest.full_name || '';
    $('rbDefendantPhone').value = arrest.phone || arrest.defendant_phone || '';
    $('rbDefendantAddress').value = arrest.address || arrest.defendant_address || '';
    $('rbDefendantDob').value = arrest.dob || arrest.date_of_birth || '';
    $('rbBookingUrl').value = arrest.source_url || arrest.detail_url || '';
    $('rbBookingNumber').value = arrest.booking_number || '';
    $('rbCounty').value = arrest.county || '';
    $('rbFacility').value = arrest.facility || arrest.jail_facility || '';
    $('rbCharges').value = arrest.charges || '';
    $('rbCaseNumber').value = arrest.case_number || '';

    // Bond amount — handle various field names across counties
    const bondAmt = parseFloat(arrest.bond_amount || arrest.total_bond_amount || 0);
    $('rbBondAmount').value = bondAmt > 0 ? bondAmt : '';
    const premiumEl = $('rbPremium');
    if (premiumEl && bondAmt > 0) {
      premiumEl.value = Math.round(bondAmt * 0.10);
      delete premiumEl.dataset.manualEdit;
    }

    // Court info if available
    if (arrest.court_date) $('rbCourtDate').value = arrest.court_date;
    if (arrest.court_time) $('rbCourtTime').value = arrest.court_time;
    if (arrest.court_location) $('rbCourtLocation').value = arrest.court_location;

    // Auto-set today as bond date
    $('rbBondDate').value = new Date().toISOString().split('T')[0];

    // Hide search results and show success hint
    const resultsEl = $('rbSearchResults');
    if (resultsEl) resultsEl.style.display = 'none';
    const hintEl = $('rbSearchHint');
    if (hintEl) hintEl.innerHTML = `<span style="color:var(--success)">✅ Auto-filled from <strong>${arrest.county}</strong> arrest data. Assign a POA below.</span>`;

    // Clear search input
    $('rbDefendantSearch').value = '';
    _searchResults = [];

    // Trigger POA suggestion for the selected surety
    suggestPOA();

    // Fetch NLP risk score for the selected defendant
    fetchRiskBadge(arrest.booking_number);

    toast(`☘️ Loaded: ${arrest.full_name} — ${arrest.county} County`, 'success');
  }

  // ── Open Modal ──────────────────────────────────────────────────────────
  function open(data = {}) {
    _prefillData = data;
    const modal = $('recordBondModal');
    if (!modal) { console.error('Record Bond modal not found'); return; }

    // Reset search state
    if ($('rbDefendantSearch')) $('rbDefendantSearch').value = '';
    if ($('rbSearchResults')) $('rbSearchResults').style.display = 'none';
    if ($('rbSearchHint')) $('rbSearchHint').textContent = 'Type to search all arrest records. Select to auto-fill form.';
    _searchResults = [];

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
    $('rbDefendantPhone').value = data.defendant_phone || '';
    $('rbDefendantDob').value = data.defendant_dob || data.dob || '';
    $('rbDefendantEmail').value = data.defendant_email || data.email || '';
    $('rbDefendantAddress').value = data.defendant_address || data.address || '';
    $('rbBookingUrl').value = data.booking_page_url || data.detail_url || '';
    $('rbRef1Name').value = data.ref1_name || (data.indemnitor && data.indemnitor.ref1Name) || '';
    $('rbRef1Phone').value = data.ref1_phone || (data.indemnitor && data.indemnitor.ref1Phone) || '';
    $('rbRef2Name').value = data.ref2_name || (data.indemnitor && data.indemnitor.ref2Name) || '';
    $('rbRef2Phone').value = data.ref2_phone || (data.indemnitor && data.indemnitor.ref2Phone) || '';
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
      defendant_phone: $('rbDefendantPhone')?.value || '',
      defendant_address: $('rbDefendantAddress')?.value || '',
      defendant_dob: $('rbDefendantDob')?.value || '',
      defendant_email: $('rbDefendantEmail')?.value || '',
      booking_page_url: $('rbBookingUrl')?.value || '',
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
      ref1_name: $('rbRef1Name')?.value || '',
      ref1_phone: $('rbRef1Phone')?.value || '',
      ref2_name: $('rbRef2Name')?.value || '',
      ref2_phone: $('rbRef2Phone')?.value || '',
      payment_method: $('rbPaymentMethod')?.value || 'cash',
      agent_name: $('rbAgentName')?.value || "Brendan O'Neal",
      notes: $('rbNotes')?.value || '',
    };

    // Auto-generate booking number for fully manual entries
    if (!payload.booking_number && payload.defendant_name) {
      payload.booking_number = 'MANUAL-' + Date.now().toString(36).toUpperCase();
      const bkEl = $('rbBookingNumber');
      if (bkEl) bkEl.value = payload.booking_number;
    }

    // Client-side validation
    if (!payload.defendant_name || !payload.poa_number) {
      if (statusEl) statusEl.innerHTML = '<span class="rb-error">❌ Defendant Name and POA # are required</span>';
      if (btn) btn.disabled = false;
      return;
    }
    if (!payload.bond_amount || payload.bond_amount <= 0) {
      if (statusEl) statusEl.innerHTML = '<span class="rb-error">❌ Bond amount is required (scrapers often leave $0 until first appearance — enter the real amount)</span>';
      if (btn) btn.disabled = false;
      $('rbBondAmount')?.focus();
      return;
    }

    // Mirror bond amount onto the arrest lead so Defendants / billing stay in sync
    if (payload.booking_number && typeof window.updateBondAmount === 'function') {
      try {
        await window.updateBondAmount(payload.booking_number, payload.bond_amount, $('rbBondAmount'));
      } catch (e) { /* non-fatal */ }
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
          // Re-render Kanban board if it's currently visible
          if (window.SLKanban?.render) SLKanban.render();
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

  // ── Fetch from URL — auto-populate from jail booking URL ─────────────
  async function fetchFromURL() {
    const urlInput = $('rbFetchUrl');
    const statusEl = $('rbUrlStatus');
    const url = urlInput?.value?.trim();

    if (!url) {
      if (statusEl) statusEl.innerHTML = '<span class="rb-error">⚠️ Paste a jail booking URL first</span>';
      return;
    }

    // Validate URL
    try { new URL(url); } catch {
      if (statusEl) statusEl.innerHTML = '<span class="rb-error">❌ Invalid URL format</span>';
      return;
    }

    if (statusEl) statusEl.innerHTML = '<span class="rb-loading">🔍 Fetching arrest data from URL...</span>';

    try {
      const r = await fetch(`${API}/api/bonds/ingest-url`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ url }),
      });
      const d = await r.json();

      if (d.success && d.data) {
        const data = d.data;
        // Auto-fill form fields from the ingested data
        if (data.full_name) $('rbDefendantName').value = data.full_name;
        if (data.date_of_birth) $('rbDefendantDob').value = data.date_of_birth;
        if (data.source_url) $('rbBookingUrl').value = data.source_url;
        if (data.booking_number) $('rbBookingNumber').value = data.booking_number;
        if (data.county) $('rbCounty').value = data.county;
        if (data.facility) $('rbFacility').value = data.facility;
        if (data.charges) $('rbCharges').value = data.charges;
        if (data.case_number) $('rbCaseNumber').value = data.case_number;
        if (data.court_date) $('rbCourtDate').value = data.court_date;
        if (data.court_location) $('rbCourtLocation').value = data.court_location;

        // Bond amount
        const bondAmt = parseFloat(data.bond_amount || 0);
        if (bondAmt > 0) {
          $('rbBondAmount').value = bondAmt;
          const premiumEl = $('rbPremium');
          if (premiumEl) {
            premiumEl.value = Math.round(bondAmt * 0.10);
            delete premiumEl.dataset.manualEdit;
          }
        }

        // Auto-set today as bond date
        $('rbBondDate').value = new Date().toISOString().split('T')[0];

        if (statusEl) statusEl.innerHTML = `<span class="rb-success">✅ Auto-filled from <strong>${data.county || 'booking'}</strong> URL (${data.parser || 'auto'})</span>`;
        toast(`☘️ Loaded from URL: ${data.full_name || 'Defendant'}`, 'success');

        // Clear URL input
        urlInput.value = '';

        // Trigger POA suggestion
        suggestPOA();

        // Fetch NLP risk badge
        if (data.booking_number) fetchRiskBadge(data.booking_number);

      } else {
        if (statusEl) statusEl.innerHTML = `<span class="rb-error">❌ ${d.error || 'Could not parse this URL'}</span>`;
      }
    } catch (e) {
      if (statusEl) statusEl.innerHTML = `<span class="rb-error">❌ Network error: ${e.message}</span>`;
    }
  }

  // ── NLP Risk Badge — visual risk tier indicator ────────────────────────
  async function fetchRiskBadge(bookingNumber) {
    if (!bookingNumber) return;
    const badgeEl = $('rbRiskBadge');
    if (!badgeEl) return;

    badgeEl.innerHTML = '<span style="color:var(--muted);font-size:11px">⏳ Scoring...</span>';

    try {
      const r = await fetch(`${API}/api/legal-nlp/risk-score/${encodeURIComponent(bookingNumber)}`);
      const d = await r.json();

      if (d.success) {
        const tierColors = {
          critical: '#ef4444',
          high: '#f59e0b',
          medium: '#3b82f6',
          low: '#10b981',
        };
        const tier = d.risk_tier || 'low';
        const color = tierColors[tier] || '#6b7280';
        const icon = tier === 'critical' ? '🔴' : tier === 'high' ? '🟡' : tier === 'medium' ? '🔵' : '🟢';

        badgeEl.innerHTML = `
          <div style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:6px;
            background:${color}15;border:1px solid ${color}40;font-size:12px;font-weight:600;color:${color}">
            ${icon} ${tier.toUpperCase()} RISK
            <span style="font-weight:400;opacity:0.8">
              R:${d.recidivism_score || 0} · FTA:${d.fta_score || 0}
              ${d.prior_count > 0 ? ` · ${d.prior_count} priors` : ''}
            </span>
          </div>
        `;
      } else {
        badgeEl.innerHTML = '';
      }
    } catch {
      badgeEl.innerHTML = '';
    }
  }

  // Public API
  return { open, close, calcPremium, markPremiumManual, suggestPOA, submit,
           searchDefendants, showResults, hideResults, selectDefendant,
           fetchFromURL, fetchRiskBadge };
})();

// Global alias for easy access from other modules
window.openRecordBondModal = SLRecordBond.open;
