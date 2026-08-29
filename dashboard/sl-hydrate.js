/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ShamrockLeads — Universal Booking Ingestion & Form Hydration Suite (SLHydrate)
 * ═══════════════════════════════════════════════════════════════════════════
 * Allows 1-click clipboard/bookmarklet hydration for:
 *   1. Write Bond / Appearance Bond Modal (#bondModal) -> Appearance Bond PDFs & DocuSeal
 *   2. Add Lead to Pipeline (#addLeadModal)
 *   3. Manual Intake Queue (#manualIntakeModal)
 *   4. Add Active Bond Modal (#abAddBondModal)
 *   5. Global Ingest Modal (#slHydrateModal) & Omnibar commands
 */
(function() {
  'use strict';

  try {
    const h = String(location.hash || '');
    const m = h.match(/(?:^|#|&)booking-extract=([^&]*)/);
    if (m && m[1]) {
      sessionStorage.setItem('sl_booking_extract', decodeURIComponent(m[1]));
      history.replaceState(null, '', location.pathname + location.search);
    }
  } catch (e) { /* ignore */ }

  const SLHydrate = {
    modalId: 'slHydrateModal',
    parsedCache: null,

    /**
     * Parse raw string / JSON / object into a standardized Shamrock Booking record.
     */
    normalizeBookingData: function(raw) {
      if (!raw) return null;
      let data = raw;
      if (typeof raw === 'string') {
        raw = raw.trim();
        if (raw.startsWith('{') || raw.startsWith('[')) {
          try {
            data = JSON.parse(raw);
          } catch(e) {
            data = this.parsePlainTextRoster(raw);
          }
        } else {
          data = this.parsePlainTextRoster(raw);
        }
      }

      if (!data || typeof data !== 'object') return null;

      // Handle single item array
      if (Array.isArray(data)) {
        data = data[0] || {};
      }

      const g = function(...keys) {
        for (let k of keys) {
          if (data[k] !== undefined && data[k] !== null && String(data[k]).trim() !== '') {
            return String(data[k]).trim();
          }
        }
        return '';
      };

      // 1. Defendant Name
      let name = g('defendantFullName', 'full_name', 'defendant_name', 'name', 'DefName', 'defendantName', 'Defendant_Name');
      if (!name) {
        const fn = g('firstName', 'first_name', 'DefFirstName');
        const ln = g('lastName', 'last_name', 'DefLastName');
        if (fn || ln) name = [ln, fn].filter(Boolean).join(', ');
      }

      // 2. Booking / Arrest #
      const booking = g('defendantArrestNumber', 'bookingNumber', 'booking_number', 'arrest_number', 'arrestNumber', 'booking', 'Booking_Number', 'DefBookingNumber');

      // 3. County
      let county = g('county', 'County', 'defCounty');
      if (!county) {
        const facility = g('facility', 'DefFacility', 'jailFacility');
        const fac = (facility || '').toLowerCase();
        const otherState = /\b(ga|georgia|sc|nc|al|tn|tx|ms|la)\b/.test(fac);
        if (/\blee county\b/.test(fac) && !otherState) county = 'Lee';
        else if (fac.includes('collier')) county = 'Collier';
        else if (fac.includes('charlotte')) county = 'Charlotte';
        else if (fac.includes('sarasota')) county = 'Sarasota';
        else if (fac.includes('manatee')) county = 'Manatee';
        else if (fac.includes('hendry')) county = 'Hendry';
        else if (fac.includes('desoto') || fac.includes('de soto')) county = 'DeSoto';
      }

      // 4. DOB (normalize YYYY-MM-DD or MM/DD/YYYY)
      let dob = g('defendantDOB', 'dob', 'DOB', 'defDOB', 'defendant_dob');
      if (dob && dob.includes('/')) {
        const parts = dob.split('/');
        if (parts.length === 3) {
          const mm = parts[0].padStart(2, '0');
          const dd = parts[1].padStart(2, '0');
          let yy = parts[2].replace(/\s.*$/, '');
          if (yy.length === 2) yy = (parseInt(yy) > 30 ? '19' : '20') + yy;
          dob = `${yy}-${mm}-${dd}`;
        }
      }

      // 5. Demographics
      const race = g('defendantRace', 'race', 'Race');
      const sex = g('defendantSex', 'sex', 'Sex', 'gender', 'Gender');
      const height = g('defendantHeight', 'height', 'Height');
      const weight = g('defendantWeight', 'weight', 'Weight').replace(/lbs?/i, '').trim();

      // 6. Address
      const street = g('defendantStreetAddress', 'street', 'street_address', 'address', 'Address', 'IndAddress');
      const city = g('defendantCity', 'city', 'City');
      const state = g('defendantState', 'state', 'State') || 'FL';
      const zip = g('defendantZip', 'zip', 'zip_code', 'Zip');
      const fullAddress = [street, city, state, zip].filter(Boolean).join(', ');

      // 7. Charges & Bond Breakdown
      let rawCharges = data.charges || data.charge_details || data.Charges || [];
      let chargeDetails = [];
      let totalBond = 0;
      let primaryCaseNumber = '';
      let primaryCourtDate = '';
      let primaryCourtTime = '';
      let primaryCourtLoc = '';

      if (typeof rawCharges === 'string') {
        const split = rawCharges.split(/\||\n/).map(c => c.trim()).filter(Boolean);
        chargeDetails = split.map(ch => ({
          charge: ch,
          description: ch,
          bond_amount: 0,
          bondAmount: 0,
          case_number: '',
          court_date: 'TBN',
          court_time: ''
        }));
      } else if (Array.isArray(rawCharges)) {
        chargeDetails = rawCharges.map((c, i) => {
          if (typeof c === 'string') {
            return {
              charge: c,
              description: c,
              bond_amount: 0,
              bondAmount: 0,
              case_number: '',
              court_date: 'TBN',
              court_time: ''
            };
          }
          const desc = c.description || c.charge || c.desc || `Charge #${i+1}`;
          const amtNum = parseFloat(String(c.bondAmount || c.bond_amount || c.amount || c.bond || '0').replace(/[^0-9.]/g, '')) || 0;
          totalBond += amtNum;
          const caseNum = String(c.caseNumber || c.case_number || c.caseNum || '').replace('#', '').trim();
          if (!primaryCaseNumber && caseNum && caseNum !== booking) {
            primaryCaseNumber = caseNum;
          }
          const hearing = c.hearing || c.court_date || c.courtDate || '';
          let cDate = 'TBN';
          let cTime = '';
          if (hearing) {
            const hMatch = hearing.match(/(\d{1,2}\/\d{1,2}\/\d{2,4})(?:[,\s]+(.*))?/);
            if (hMatch) {
              cDate = hMatch[1];
              cTime = (hMatch[2] || '').trim();
            } else {
              cDate = hearing;
            }
          }
          if (!primaryCourtDate && cDate && cDate !== 'TBN') {
            primaryCourtDate = cDate;
            primaryCourtTime = cTime;
          }
          const courtLoc = c.courtLocation || c.court_location || c.courtLoc || '';
          if (!primaryCourtLoc && courtLoc) primaryCourtLoc = courtLoc;

          return {
            charge: desc,
            description: desc,
            bond_amount: amtNum,
            bondAmount: amtNum,
            bond_type: c.bondType || c.bond_type || 'CASH / SURETY',
            bondType: c.bondType || c.bond_type || 'CASH / SURETY',
            case_number: caseNum,
            caseNumber: caseNum,
            court_date: cDate,
            courtDate: cDate,
            court_time: cTime,
            courtTime: cTime,
            court_location: courtLoc,
            courtLocation: courtLoc,
            hearing: hearing
          };
        });
      }

      // Explicit bond amount fallback if charges had $0 or missing amounts
      const explicitBond = parseFloat(String(g('bondAmount', 'bond_amount', 'bond', 'DefBondAmount', 'totalBond')).replace(/[^0-9.]/g, '')) || 0;
      if (explicitBond > 0 && (totalBond === 0 || explicitBond > totalBond)) {
        totalBond = explicitBond;
      }

      const chargesRaw = chargeDetails.map(c => c.charge || c.description).join(' | ');

      return {
        name,
        defendantFullName: name,
        full_name: name,
        bookingNumber: booking,
        booking_number: booking,
        defendantArrestNumber: booking,
        county,
        facility: `${county} County Jail`,
        dob,
        race,
        sex,
        height,
        weight,
        street,
        city,
        state,
        zip,
        fullAddress,
        address: fullAddress,
        totalBond,
        bond_amount: totalBond,
        charges: chargeDetails,
        charge_details: chargeDetails,
        chargesRaw,
        caseNumber: primaryCaseNumber || g('caseNumber', 'case_number', 'Case_Number'),
        case_number: primaryCaseNumber || g('caseNumber', 'case_number', 'Case_Number'),
        courtDate: primaryCourtDate || g('courtDate', 'court_date', 'Court_Date') || 'TBN',
        court_date: primaryCourtDate || g('courtDate', 'court_date', 'Court_Date') || 'TBN',
        courtTime: primaryCourtTime || g('courtTime', 'court_time', 'Court_Time') || '',
        court_time: primaryCourtTime || g('courtTime', 'court_time', 'Court_Time') || '',
        courtLocation: primaryCourtLoc || g('courtLocation', 'court_location') || '',
        court_location: primaryCourtLoc || g('courtLocation', 'court_location') || '',
        source: 'bookmarklet'
      };
    },

    /**
     * Fallback parser for raw plain text copied from county booking portals.
     */
    parsePlainTextRoster: function(text) {
      if (!text || typeof text !== 'string') return {};
      const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
      function getAfter(label) {
        for (let i = 0; i < lines.length - 1; i++) {
          if (lines[i].replace(/:$/, '').toLowerCase() === label.toLowerCase()) {
            return lines[i + 1];
          }
        }
        return '';
      }

      const name = getAfter('Name');
      const arrestNum = getAfter('Number') || getAfter('Booking Number') || getAfter('Booking #');
      const dob = getAfter('DOB') || getAfter('Date of Birth');
      const race = getAfter('Race');
      const sex = getAfter('Sex') || getAfter('Gender');
      const height = getAfter('Height');
      const weight = getAfter('Weight');
      const addr = getAfter('Address');

      return {
        defendantFullName: name,
        defendantArrestNumber: arrestNum,
        defendantDOB: dob,
        defendantRace: race,
        defendantSex: sex,
        defendantHeight: height,
        defendantWeight: weight,
        defendantStreetAddress: addr,
        charges: []
      };
    },

    /**
     * Smart hydration entry point:
     * Dispatches to whichever modal is currently open, or opens Write Bond / Hydrate dialog.
     */
    hydrateActiveModal: async function(overrideData) {
      let data = overrideData;
      if (!data) {
        data = await this.readClipboardData();
      }
      if (!data) {
        this.openModal();
        return;
      }

      const norm = this.normalizeBookingData(data);
      if (!norm || (!norm.name && !norm.bookingNumber)) {
        this.openModal(typeof data === 'string' ? data : JSON.stringify(data, null, 2));
        return;
      }

      // 1. If Write Bond modal is open
      const bondModal = document.getElementById('bondModal');
      if (bondModal && bondModal.classList.contains('show')) {
        this.hydrateBondModal(norm);
        this.showToast(`☘️ Hydrated Write Bond for ${norm.name || 'Defendant'}`);
        return;
      }

      // 2. If Add Lead modal is open
      const addLeadModal = document.getElementById('addLeadModal');
      if (addLeadModal && (addLeadModal.classList.contains('show') || addLeadModal.style.display !== 'none')) {
        this.hydrateAddLeadModal(norm);
        this.showToast(`☘️ Hydrated Pipeline Lead for ${norm.name || 'Defendant'}`);
        return;
      }

      // 3. If Manual Intake modal is open
      const manualIntakeModal = document.getElementById('manualIntakeModal');
      if (manualIntakeModal && (manualIntakeModal.classList.contains('show') || manualIntakeModal.style.display !== 'none')) {
        this.hydrateManualIntakeModal(norm);
        this.showToast(`☘️ Hydrated Intake for ${norm.name || 'Defendant'}`);
        return;
      }

      // 4. If Add Active Bond modal is open
      const abAddBondModal = document.getElementById('abAddBondModal');
      if (abAddBondModal && abAddBondModal.style.display !== 'none') {
        this.hydrateActiveBondModal(norm);
        this.showToast(`☘️ Hydrated Active Bond for ${norm.name || 'Defendant'}`);
        return;
      }

      // 5. Default: Open Write Bond / Appearance Bond modal prefilled
      this.hydrateBondModal(norm);
      this.showToast(`☘️ Opened Appearance Bond & Write Bond for ${norm.name || 'Defendant'}`);
    },

    /**
     * Hydrate Write Bond / Appearance Bond Modal (#bondModal)
     */
    hydrateBondModal: function(norm) {
      if (!norm) return;
      const lead = {
        full_name: norm.name,
        booking_number: norm.bookingNumber,
        county: norm.county || 'Lee',
        facility: norm.facility || `${norm.county || 'Lee'} County Jail`,
        dob: norm.dob || '',
        race: norm.race || '',
        sex: norm.sex || '',
        height: norm.height || '',
        weight: norm.weight || '',
        address: norm.fullAddress || norm.street || '',
        street_address: norm.street || '',
        city: norm.city || '',
        state: norm.state || 'FL',
        zip: norm.zip || '',
        bond_amount: norm.totalBond || 0,
        charges: norm.chargesRaw || '',
        charge_details: (norm.charges && norm.charges.length > 0) ? norm.charges : [{
          charge: norm.chargesRaw || 'BOND APPEARANCE',
          bond_amount: norm.totalBond || 0,
          case_number: norm.caseNumber || '',
          court_date: norm.courtDate || 'TBN',
          court_time: norm.courtTime || ''
        }],
        case_number: norm.caseNumber || '',
        court_date: norm.courtDate || 'TBN',
        court_time: norm.courtTime || '',
        court_location: norm.courtLocation || '',
        source: 'bookmarklet'
      };

      if (typeof openBondModal === 'function') {
        openBondModal(lead);
      } else if (typeof SL !== 'undefined' && typeof SL.openBondModal === 'function') {
        SL.openBondModal(lead);
      }

      // Ensure form inputs in the modal are populated
      setTimeout(() => {
        const amtInp = document.getElementById('wbBondAmountInput');
        if (amtInp && norm.totalBond > 0) {
          amtInp.value = norm.totalBond;
          if (typeof onWriteBondAmountChange === 'function') {
            onWriteBondAmountChange(norm.totalBond);
          }
        }
      }, 50);
    },

    /**
     * Hydrate Add Lead Modal (#addLeadModal)
     */
    hydrateAddLeadModal: function(norm) {
      if (!norm) return;
      if (typeof SLProspective !== 'undefined' && typeof SLProspective.openAddModal === 'function') {
        const modal = document.getElementById('addLeadModal');
        if (!modal || !modal.classList.contains('show')) {
          SLProspective.openAddModal();
        }
      }

      const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el && val !== undefined && val !== null) el.value = val;
      };

      setVal('alDefName', norm.name);
      setVal('alBooking', norm.bookingNumber);
      setVal('alCounty', norm.county);
      setVal('alBondAmount', norm.totalBond > 0 ? norm.totalBond : '');
      setVal('alCharges', norm.chargesRaw);
      
      const noteEl = document.getElementById('alNote');
      if (noteEl && !noteEl.value) {
        const notes = [];
        if (norm.dob) notes.push(`DOB: ${norm.dob}`);
        if (norm.fullAddress) notes.push(`Address: ${norm.fullAddress}`);
        if (norm.caseNumber) notes.push(`Case: ${norm.caseNumber}`);
        if (norm.courtDate && norm.courtDate !== 'TBN') notes.push(`Court: ${norm.courtDate} ${norm.courtTime}`.trim());
        if (notes.length) noteEl.value = `[Bookmarklet Ingest] ${notes.join(' · ')}`;
      }
    },

    /**
     * Hydrate Manual Intake Modal (#manualIntakeModal)
     */
    hydrateManualIntakeModal: function(norm) {
      if (!norm) return;
      if (typeof SLIntake !== 'undefined' && typeof SLIntake.openManualModal === 'function') {
        const modal = document.getElementById('manualIntakeModal');
        if (!modal || (!modal.classList.contains('show') && modal.style.display === 'none')) {
          SLIntake.openManualModal();
        }
      }

      const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el && val !== undefined && val !== null) el.value = val;
      };

      setVal('miDefName', norm.name);
      setVal('miBookingNum', norm.bookingNumber);
      setVal('miCounty', norm.county);
      setVal('miFacility', norm.facility || `${norm.county} County Jail`);
      setVal('miBondAmount', norm.totalBond > 0 ? norm.totalBond : '');
      setVal('miCharges', norm.chargesRaw);

      const srcEl = document.getElementById('miSource');
      if (srcEl) srcEl.value = 'manual_entry';
    },

    /**
     * Hydrate Active Bonds Modal (#abAddBondModal)
     */
    hydrateActiveBondModal: function(norm) {
      if (!norm) return;
      if (typeof SLActiveBonds !== 'undefined' && typeof SLActiveBonds.openAddModal === 'function') {
        const modal = document.getElementById('abAddBondModal');
        if (!modal || modal.style.display === 'none') {
          SLActiveBonds.openAddModal();
        }
      }

      const setVal = (ids, val) => {
        if (!Array.isArray(ids)) ids = [ids];
        for (const id of ids) {
          const el = document.getElementById(id);
          if (el && val !== undefined && val !== null) {
            el.value = val;
            break;
          }
        }
      };

      setVal(['abAddDefName', 'abDefName'], norm.name);
      setVal(['abAddBooking', 'abBooking'], norm.bookingNumber);
      setVal(['abAddCounty', 'abCounty'], norm.county);
      setVal(['abAddBondAmt', 'abBondAmt'], norm.totalBond > 0 ? norm.totalBond : '');
      setVal(['abAddCharges', 'abCharges'], norm.chargesRaw);
      
      // Normalize court date for <input type="date"> (YYYY-MM-DD)
      let cDate = norm.courtDate;
      if (cDate && cDate !== 'TBN') {
        if (cDate.includes('/')) {
          const p = cDate.split('/');
          if (p.length === 3) {
            const mm = p[0].padStart(2, '0');
            const dd = p[1].padStart(2, '0');
            let yy = p[2].replace(/\s.*$/, '');
            if (yy.length === 2) yy = '20' + yy;
            cDate = `${yy}-${mm}-${dd}`;
          }
        }
        setVal(['abAddCourtDate', 'abCourtDate'], cDate);
      }

      const notes = [];
      if (norm.dob) notes.push(`DOB: ${norm.dob}`);
      if (norm.fullAddress) notes.push(`Address: ${norm.fullAddress}`);
      if (norm.caseNumber) notes.push(`Case #: ${norm.caseNumber}`);
      if (notes.length) {
        setVal(['abAddNotes', 'abNotes'], `[Bookmarklet Ingest] ${notes.join(' · ')}`);
      }
    },

    /**
     * Read text from clipboard with fallback handling.
     */
    readClipboardData: async function() {
      try {
        if (navigator.clipboard && navigator.clipboard.readText) {
          const text = await navigator.clipboard.readText();
          if (text && text.trim()) return text.trim();
        }
      } catch (err) {
        console.log('[SLHydrate] Clipboard direct read permission denied or unavailable:', err);
      }
      return null;
    },

    /**
     * 1-Click Action from Bookmarklet / Hotkey.
     */
    pasteAndHydrate: async function() {
      const clipText = await this.readClipboardData();
      if (clipText) {
        const norm = this.normalizeBookingData(clipText);
        if (norm && (norm.name || norm.bookingNumber)) {
          await this.ingestExtract(clipText);
          return;
        }
      }
      this.openModal(clipText || '');
    },

    captureExtractFromUrl: function() {
      try {
        const h = String(location.hash || '');
        const m = h.match(/(?:^|#|&)booking-extract=([^&]*)/);
        if (m && m[1]) {
          sessionStorage.setItem('sl_booking_extract', decodeURIComponent(m[1]));
          history.replaceState(null, '', location.pathname + location.search);
        }
      } catch (e) { /* ignore */ }
    },

    listenForExtractMessages: function() {
      if (this._msgBound) return;
      this._msgBound = true;
      window.addEventListener('message', (e) => {
        const d = e.data;
        if (!d || d.type !== 'sl-booking-extract' || !d.payload) return;
        const host = String(e.origin || '').replace(/^https?:\/\//, '');
        const allowed = /sheriffleefl\.org$/i.test(host) || e.origin === location.origin;
        if (!allowed) return;
        this.ingestExtract(d.payload);
      });
    },

    consumePendingExtract: async function() {
      let raw = null;
      try { raw = sessionStorage.getItem('sl_booking_extract'); } catch (e) { raw = null; }
      if (!raw) return;
      try { sessionStorage.removeItem('sl_booking_extract'); } catch (e) { /* ignore */ }
      let data = raw;
      try { data = JSON.parse(raw); } catch (e) { /* keep string */ }
      await this.ingestExtract(data);
    },

    ingestExtract: async function(raw) {
      if (!raw) return;
      if (this._ingestLock) return;
      this._ingestLock = true;
      try {
        const norm = this.normalizeBookingData(raw);
        if (!norm || (!norm.name && !norm.bookingNumber)) {
          this.openModal(typeof raw === 'string' ? raw : JSON.stringify(raw, null, 2));
          return;
        }
        const key = String(norm.bookingNumber || norm.name || '');
        if (key && this._ingestedKey === key) return;
        this._ingestedKey = key;

        let payload = raw;
        if (typeof raw === 'string') {
          try { payload = JSON.parse(raw); } catch (e) { payload = norm; }
        }

        const res = await fetch((typeof API === 'string' ? API : '') + '/api/leads/merge-booking-extract', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const d = await res.json().catch(() => ({}));
        if (res.status === 401) {
          this.showToast('Session expired — log in, then run the Lee bookmarklet again');
          return;
        }
        if (!res.ok || d.success === false) {
          this.showToast('⚠️ ' + (d.error || 'Merge failed') + ' — opening Write / Print from extract only');
          this.hydrateBondModal(norm);
          return;
        }

        const lead = d.lead || {};
        const booking = String(lead.booking_number || norm.bookingNumber || '');
        window._leadMap = window._leadMap || {};
        if (booking) {
          window._leadMap[booking] = Object.assign({}, window._leadMap[booking] || {}, lead, {
            charge_details: lead.charge_details || norm.charge_details || norm.charges || [],
            charges: lead.charges || norm.chargesRaw || '',
          });
        }
        const created = d.created ? 'Created arrest · ' : 'Merged ';
        this.showToast('☘️ ' + created + (d.charge_count || 0) + ' charge(s) · $' + Number(d.total_bond || 0).toLocaleString());
        const searchEl = document.getElementById('defSearch');
        if (searchEl && booking) searchEl.value = booking;
        if (typeof switchTab === 'function') switchTab('tabDefendants');
        if (typeof openDefendantWritePrint === 'function' && booking) {
          await openDefendantWritePrint(booking);
        } else {
          this.hydrateBondModal(norm);
        }
      } catch (e) {
        const norm = this.normalizeBookingData(raw);
        if (norm && (norm.name || norm.bookingNumber)) this.hydrateBondModal(norm);
        this.showToast('Network error merging extract — opened Write Bond from page data');
      } finally {
        this._ingestLock = false;
      }
    },

    /**
     * Open Ingestion & Bookmarklet Modal (#slHydrateModal)
     */
    openModal: function(initialText = '') {
      let modal = document.getElementById(this.modalId);
      if (!modal) {
        this.injectModalHtml();
        modal = document.getElementById(this.modalId);
      }

      modal.style.display = 'flex';
      modal.classList.add('show');

      const textarea = document.getElementById('slHydrateTextarea');
      if (textarea) {
        if (initialText) textarea.value = initialText;
        this.updatePreviewFromInput();
        setTimeout(() => textarea.focus(), 80);
      }
    },

    closeModal: function() {
      const modal = document.getElementById(this.modalId);
      if (modal) {
        modal.style.display = 'none';
        modal.classList.remove('show');
      }
    },

    updatePreviewFromInput: function() {
      const textarea = document.getElementById('slHydrateTextarea');
      const previewArea = document.getElementById('slHydratePreviewArea');
      if (!textarea || !previewArea) return;

      const raw = textarea.value.trim();
      if (!raw) {
        previewArea.innerHTML = '<div style="color:var(--muted);font-size:12px;text-align:center;padding:16px">Paste JSON or booking text above to preview extracted fields.</div>';
        this.parsedCache = null;
        return;
      }

      const norm = this.normalizeBookingData(raw);
      this.parsedCache = norm;

      if (!norm || (!norm.name && !norm.bookingNumber)) {
        previewArea.innerHTML = `
          <div style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);border-radius:8px;padding:12px;color:#fca5a5;font-size:12px">
            ⚠️ Could not find defendant name or booking number. Please ensure the booking JSON or text is formatted properly.
          </div>`;
        return;
      }

      const chargeBadges = (norm.charges && norm.charges.length > 0)
        ? norm.charges.map((c, i) => `
            <div style="padding:6px 8px;background:var(--bg);border-radius:6px;border:1px solid var(--border);font-size:11px;margin-top:4px">
              <span style="font-weight:700;color:var(--text)">#${i+1} ${c.charge || c.description}</span>
              <div style="display:flex;gap:8px;color:var(--muted);font-size:10px;margin-top:2px;flex-wrap:wrap">
                <span style="color:#34d399;font-weight:700">$${Number(c.bond_amount || c.bondAmount || 0).toLocaleString()}</span>
                ${c.case_number ? `<span style="color:#60a5fa">Case: ${c.case_number}</span>` : ''}
                ${c.court_date ? `<span>Court: ${c.court_date} ${c.court_time || ''}</span>` : ''}
              </div>
            </div>`).join('')
        : `<div style="font-size:11px;color:var(--muted)">${norm.chargesRaw || 'No charges parsed'}</div>`;

      previewArea.innerHTML = `
        <div style="background:var(--panel);border:1px solid rgba(16,185,129,0.3);border-radius:8px;padding:12px;display:flex;flex-direction:column;gap:8px">
          <div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border);padding-bottom:8px">
            <div>
              <div style="font-size:15px;font-weight:800;color:#10b981">👤 ${norm.name || '(Unknown Name)'}</div>
              <div style="font-size:11px;color:var(--muted)">County: <strong>${norm.county}</strong> · Booking: <strong>${norm.bookingNumber || 'N/A'}</strong></div>
            </div>
            <div style="text-align:right">
              <div style="font-size:15px;font-weight:800;color:#34d399">$${Number(norm.totalBond).toLocaleString()}</div>
              <div style="font-size:10px;color:var(--muted)">Total Bond</div>
            </div>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px;color:var(--text)">
            <div><strong>DOB:</strong> ${norm.dob || '—'}</div>
            <div><strong>Demographics:</strong> ${[norm.race, norm.sex, norm.height, norm.weight ? norm.weight+' lbs' : ''].filter(Boolean).join(' / ') || '—'}</div>
            <div style="grid-column:1/-1"><strong>Address:</strong> ${norm.fullAddress || '—'}</div>
            ${norm.caseNumber ? `<div><strong>Case #:</strong> <span style="color:#60a5fa">${norm.caseNumber}</span></div>` : ''}
            ${norm.courtDate ? `<div><strong>Court Date:</strong> ${norm.courtDate} ${norm.courtTime || ''}</div>` : ''}
          </div>

          <div style="margin-top:4px">
            <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);margin-bottom:2px">Charges (${norm.charges ? norm.charges.length : 0})</div>
            ${chargeBadges}
          </div>
        </div>`;
    },

    executeHydration: function(targetType) {
      if (!this.parsedCache) {
        const raw = (document.getElementById('slHydrateTextarea')?.value || '').trim();
        if (raw) this.parsedCache = this.normalizeBookingData(raw);
      }

      if (!this.parsedCache) {
        alert('Please paste booking data first.');
        return;
      }

      const norm = this.parsedCache;
      const raw = (document.getElementById('slHydrateTextarea')?.value || '').trim();
      this.closeModal();

      if (targetType === 'write_bond' || targetType === 'appearance_bond') {
        this.ingestExtract(raw || norm);
        return;
      } else if (targetType === 'pipeline') {
        this.hydrateAddLeadModal(norm);
        this.showToast(`☘️ Hydrated Pipeline Lead for ${norm.name}`);
      } else if (targetType === 'intake') {
        this.hydrateManualIntakeModal(norm);
        this.showToast(`☘️ Hydrated Intake for ${norm.name}`);
      } else if (targetType === 'active_bond') {
        this.hydrateActiveBondModal(norm);
        this.showToast(`☘️ Hydrated Active Bond for ${norm.name}`);
      }
    },

    /**
     * Lee County bookmarklet: extract booking page → open dashboard with
     * #booking-extract= JSON (and postMessage). Super CRM merges onto the arrest.
     */
    buildLeeBookmarklet: function(origin) {
      const dash = JSON.stringify(origin || 'https://leads.shamrockbailbonds.biz');
      return 'javascript:(function(){var DASH=' + dash + ';try{if(!location.href.includes("sheriffleefl.org")){if(!confirm("Run Lee County booking scraper?"))return;}var text=(document.body&&document.body.innerText||"").trim();if(!text){alert("Couldn\'t read page. Try scrolling once.");return;}var lines=text.split("\\n").map(function(l){return l.trim();}).filter(Boolean);function getAfter(label){for(var i=0;i<lines.length-1;i++){if(lines[i].replace(/:$/,"").toLowerCase()===label.toLowerCase()){return lines[i+1];}}return "";}var name=getAfter("Name");var arrestNum=getAfter("Number")||getAfter("Booking Number");var dob=getAfter("DOB");var race=getAfter("Race");var sex=getAfter("Sex");var height=getAfter("Height");var weight=(getAfter("Weight")||"").replace(/lbs?/i,"").trim();var addr=getAfter("Address");function parseAddress(s){var out={street:"",city:"",state:"FL",zip:""};if(!s)return out;var m=s.match(/\\s([A-Z]{2})\\s(\\d{5}(?:-\\d{4})?)$/);if(!m){out.street=s;return out;}out.state=m[1];out.zip=m[2];var left=s.slice(0,m.index).trim();var parts=left.split(/\\s+/);if(!/^\\d+/.test(left)){out.street=left;return out;}var stTypes=["ST","STREET","AVE","AVENUE","RD","ROAD","DR","DRIVE","LN","LANE","WAY","BLVD","BOULEVARD","CT","COURT","CIR","CIRCLE","TER","TERRACE","PKWY","PARKWAY","HWY","HIGHWAY","PL","PLACE","TRL","TRAIL","RUN","LOOP"];var endIdx=-1;for(var i=2;i<=parts.length;i++){var last=parts[i-1].toUpperCase().replace(/\\.$/,"");if(stTypes.indexOf(last)>-1){endIdx=i;}}if(endIdx===-1){out.street=left;return out;}out.street=parts.slice(0,endIdx).join(" ");out.city=parts.slice(endIdx).join(" ");return out;}var ap=parseAddress(addr);if(dob){var p=dob.split("/");if(p.length===3){var mm=(""+p[0]).padStart(2,"0");var dd=(""+p[1]).padStart(2,"0");var yy=(""+p[2]).replace(/\\s.*$/,"");if(yy.length===2)yy=(parseInt(yy)>30?"19":"20")+yy;dob=yy+"-"+mm+"-"+dd;}}function looksLikeChargeHeader(s){if(!s||s.length<5)return false;if(/^#/.test(s))return false;if(/\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/.test(s))return false;if(/^(CASH|SURETY|CASH \\/ SURETY|NO BOND|ROR)$/i.test(s))return false;var letters=s.replace(/[^A-Za-z]/g,"");if(!letters)return false;return s===s.toUpperCase();}var charges=[];var i=lines.indexOf("Charges");if(i>-1){for(i=i+1;i<lines.length;i++){var line=lines[i];if(looksLikeChargeHeader(line)){var desc=line,bondAmt="",bondType="",caseNum="",hearing="",courtLoc="";for(var j=i+1;j<Math.min(i+40,lines.length);j++){var lj=lines[j];if(looksLikeChargeHeader(lj)&&lj!==desc){break;}if(lj==="Type"&&j<lines.length-1){bondType=lines[j+1];}else if(lj==="Amount"&&j<lines.length-1){bondAmt=(lines[j+1]||"").replace(/[^0-9.]/g,"");}else if(lj==="Case#"&&j<lines.length-1){caseNum=(lines[j+1]||"").replace("#","").trim();}else if(lj==="Hearing"&&j<lines.length-1){hearing=lines[j+1];}else if(lj==="Location"&&j<lines.length-1){courtLoc=lines[j+1];}}if(desc&&(caseNum||bondAmt||bondType||courtLoc||hearing)){charges.push({description:desc,bondAmount:bondAmt,bondType:bondType,caseNumber:caseNum,hearing:hearing,courtLocation:courtLoc});}i=j-1;}}}var data={county:"Lee",facility:"Lee County Jail",defendantFullName:name,defendantArrestNumber:arrestNum,bookingNumber:arrestNum,defendantDOB:dob,defendantRace:race,defendantSex:sex,defendantHeight:height,defendantWeight:weight,defendantStreetAddress:ap.street,defendantCity:ap.city,defendantState:ap.state,defendantZip:ap.zip,sourceUrl:location.href,charges:charges};var json=JSON.stringify(data);try{navigator.clipboard.writeText(json);}catch(err){}var w=window.open(DASH+"/?tab=defendants&write=1#booking-extract="+encodeURIComponent(json),"shamrockleads");var n=0;var t=setInterval(function(){n++;try{if(w)w.postMessage({type:"sl-booking-extract",payload:data},DASH);}catch(err){}if(n>16)clearInterval(t);},500);alert("☘️ Sent to ShamrockLeads\\n\\n"+(name||"(missing)")+"\\nArrest # "+(arrestNum||"(missing)")+"\\n"+charges.length+" charge(s)\\n\\nKeep this jail tab open until Write / Print opens.");}catch(e){alert("Error: "+(e&&e.message?e.message:e));}})();';
    },

    /**
     * Copy bookmarklet code directly to clipboard.
     */
    copyBookmarklet: function(codeType) {
      let code = '';
      if (codeType === 'lee') {
        code = this.buildLeeBookmarklet((location && location.origin) || 'https://leads.shamrockbailbonds.biz');
      } else if (codeType === 'universal') {
        code = `javascript:(function(){try{var text=(document.body&&document.body.innerText||"").trim();if(!text){alert("No text found.");return;}var lines=text.split("\\n").map(function(l){return l.trim();}).filter(Boolean);function findVal(regexes){for(var i=0;i<lines.length-1;i++){for(var r=0;r<regexes.length;r++){if(regexes[r].test(lines[i])){return lines[i+1];}}}return "";}var name=findVal([/^name:?$/i,/^inmate name:?$/i,/^defendant:?$/i]);var booking=findVal([/^booking\\s*(#|no|number):?$/i,/^arrest\\s*(#|no|number):?$/i,/^number:?$/i]);var dob=findVal([/^dob:?$/i,/^date of birth:?$/i]);var race=findVal([/^race:?$/i]);var sex=findVal([/^sex:?$/i,/^gender:?$/i]);var addr=findVal([/^address:?$/i,/^street:?$/i]);var host=location.hostname.toLowerCase();var cnty="Lee";if(host.includes("collier"))cnty="Collier";else if(host.includes("charlotte"))cnty="Charlotte";else if(host.includes("sarasota"))cnty="Sarasota";else if(host.includes("manatee"))cnty="Manatee";else if(host.includes("orange"))cnty="Orange";else if(host.includes("hillsborough"))cnty="Hillsborough";else if(host.includes("broward"))cnty="Broward";else if(host.includes("miamidade")||host.includes("miami-dade"))cnty="Miami-Dade";var data={county:cnty,defendantFullName:name,defendantArrestNumber:booking,bookingNumber:booking,defendantDOB:dob,defendantRace:race,defendantSex:sex,defendantStreetAddress:addr,charges:[]};var json=JSON.stringify(data);navigator.clipboard.writeText(json).then(function(){alert("☘️ [Universal Jail Roster] Data Extracted!\\n\\nDefendant: "+(name||"(missing)")+"\\nBooking: "+(booking||"(missing)")+"\\nCounty: "+cnty+"\\n\\nCopied to clipboard. Go to ShamrockLeads and click 'Paste Booking'.");}).catch(function(){prompt("Copy JSON:",json);});}catch(e){alert("Error: "+e);}})();`;
      } else if (codeType === 'dashboard_fill') {
        code = `javascript:(function(){if(window.SLHydrate&&typeof window.SLHydrate.pasteAndHydrate==='function'){window.SLHydrate.pasteAndHydrate();}else{alert("Run this on ShamrockLeads Dashboard.");}})();`;
      }

      if (code) {
        navigator.clipboard.writeText(code).then(() => {
          this.showToast('📋 Bookmarklet code copied to clipboard! Create a browser bookmark and paste as the URL.');
        }).catch(() => {
          prompt('Copy bookmarklet JavaScript URL:', code);
        });
      }
    },

    /**
     * Show sleek toast message
     */
    showToast: function(msg) {
      let toast = document.getElementById('slHydrateToast');
      if (!toast) {
        toast = document.createElement('div');
        toast.id = 'slHydrateToast';
        toast.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:99999;padding:12px 18px;background:rgba(15,23,42,0.95);border:1px solid #10b981;border-radius:10px;color:#f1f5f9;font-size:13px;font-weight:600;box-shadow:0 10px 30px rgba(0,0,0,0.5);display:flex;align-items:center;gap:10px;animation:slideInUp 0.25s ease;backdrop-filter:blur(10px);pointer-events:none;';
        document.body.appendChild(toast);
      }
      toast.innerHTML = msg;
      toast.style.display = 'flex';
      toast.style.opacity = '1';
      clearTimeout(this._toastTimeout);
      this._toastTimeout = setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.style.display = 'none', 300);
      }, 3500);
    },

    /**
     * Inject Modal HTML into DOM
     */
    injectModalHtml: function() {
      if (document.getElementById(this.modalId)) return;

      const div = document.createElement('div');
      div.id = this.modalId;
      div.className = 'modal-overlay';
      div.style.cssText = 'display:none;align-items:center;justify-content:center;z-index:9999;background:rgba(0,0,0,0.75);backdrop-filter:blur(6px);position:fixed;inset:0;padding:20px;box-sizing:border-box;';
      
      div.innerHTML = `
        <div class="modal modal-lg" style="max-width:760px;width:100%;max-height:92vh;overflow-y:auto;background:var(--bg-main,#0f172a);border:1px solid rgba(16,185,129,0.3);border-radius:14px;box-shadow:0 20px 50px rgba(0,0,0,0.8);color:var(--text,#f1f5f9);display:flex;flex-direction:column">
          
          <!-- Header -->
          <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--border,rgba(255,255,255,0.1))">
            <div style="display:flex;align-items:center;gap:10px">
              <span style="font-size:1.5rem">🔖</span>
              <div>
                <h2 style="margin:0;font-size:18px;font-weight:800;color:var(--text,#f1f5f9)">Fast Ingest & Form Hydration</h2>
                <div style="font-size:11px;color:var(--muted,#64748b)">Lee bookmarklet merges onto the arrest · Write / Print fills appearance bonds</div>
              </div>
            </div>
            <button type="button" class="modal-close" onclick="SLHydrate.closeModal()" style="background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer">✕</button>
          </div>

          <!-- Body -->
          <div class="modal-body" style="padding:20px;display:flex;flex-direction:column;gap:16px">
            
            <!-- Paste & Parse Section -->
            <div>
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
                <label style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)">Paste Booking Data / JSON</label>
                <button type="button" class="btn-export" style="font-size:11px;padding:4px 10px;background:rgba(16,185,129,0.15);color:#34d399;border:1px solid rgba(16,185,129,0.3)" onclick="SLHydrate.pasteFromClipboardToInput()">📋 Paste from Clipboard</button>
              </div>
              <textarea id="slHydrateTextarea" rows="5" placeholder='Paste booking JSON (from bookmarklet) or raw jail booking text here...' style="width:100%;box-sizing:border-box;padding:12px;border-radius:8px;border:1px solid var(--border);background:var(--panel,#1e293b);color:var(--text);font-family:monospace;font-size:12px;resize:vertical" oninput="SLHydrate.updatePreviewFromInput()"></textarea>
            </div>

            <!-- Real-Time Extracted Preview -->
            <div>
              <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);margin-bottom:6px">Parsed Fields Preview</div>
              <div id="slHydratePreviewArea" style="min-height:70px">
                <div style="color:var(--muted);font-size:12px;text-align:center;padding:16px">Paste JSON or booking text above to preview extracted fields.</div>
              </div>
            </div>

            <!-- Target Selection Action Buttons -->
            <div>
              <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);margin-bottom:8px">Select Target Form to Hydrate</div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
                <button type="button" class="btn-primary" style="padding:14px;display:flex;align-items:center;gap:10px;justify-content:center;background:linear-gradient(135deg,#059669,#10b981);font-weight:700;font-size:14px;border-radius:10px" onclick="SLHydrate.executeHydration('write_bond')">
                  <span>✍️</span>
                  <span>Write Bond / Appearance Bond</span>
                </button>
                <button type="button" class="btn-export" style="padding:14px;display:flex;align-items:center;gap:10px;justify-content:center;background:rgba(59,130,246,0.15);color:#93c5fd;border:1px solid rgba(59,130,246,0.3);font-weight:700;font-size:14px;border-radius:10px" onclick="SLHydrate.executeHydration('pipeline')">
                  <span>➕</span>
                  <span>Add to Pipeline / Leads</span>
                </button>
                <button type="button" class="btn-export" style="padding:12px;display:flex;align-items:center;gap:10px;justify-content:center;background:rgba(139,92,246,0.15);color:#c4b5fd;border:1px solid rgba(139,92,246,0.3);font-weight:600;font-size:13px;border-radius:10px" onclick="SLHydrate.executeHydration('intake')">
                  <span>📥</span>
                  <span>Manual Intake Queue</span>
                </button>
                <button type="button" class="btn-export" style="padding:12px;display:flex;align-items:center;gap:10px;justify-content:center;background:rgba(245,158,11,0.15);color:#fcd34d;border:1px solid rgba(245,158,11,0.3);font-weight:600;font-size:13px;border-radius:10px" onclick="SLHydrate.executeHydration('active_bond')">
                  <span>📋</span>
                  <span>Add Active Bond</span>
                </button>
              </div>
            </div>

            <!-- Bookmarklet Suite Drawer / Section -->
            <div style="margin-top:8px;padding-top:14px;border-top:1px solid var(--border)">
              <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
                <div style="font-size:12px;font-weight:800;color:var(--text)">🔖 Bookmarklet Tools (Browser Shortcuts)</div>
                <span style="font-size:11px;color:var(--muted)">Drag to bookmarks bar or copy code</span>
              </div>
              
              <div style="display:flex;flex-direction:column;gap:8px">
                <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 12px;background:var(--panel);border-radius:8px;border:1px solid var(--border)">
                  <div>
                    <div style="font-weight:700;font-size:12px;color:#34d399">📌 Lee County Sheriff Bookmarklet</div>
                    <div style="font-size:11px;color:var(--muted)">On sheriffleefl.org/booking — merges charges into Super CRM and opens Write / Print. Replace the old clipboard bookmarklet with this one.</div>
                  </div>
                  <button type="button" class="btn-export" style="font-size:11px;padding:5px 10px" onclick="SLHydrate.copyBookmarklet('lee')">📋 Copy Code</button>
                </div>

                <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 12px;background:var(--panel);border-radius:8px;border:1px solid var(--border)">
                  <div>
                    <div style="font-weight:700;font-size:12px;color:#60a5fa">🌐 Universal Jail Roster Extractor</div>
                    <div style="font-size:11px;color:var(--muted)">Scrapes booking rosters across FL counties (Collier, Charlotte, Sarasota, Orange, etc.)</div>
                  </div>
                  <button type="button" class="btn-export" style="font-size:11px;padding:5px 10px" onclick="SLHydrate.copyBookmarklet('universal')">📋 Copy Code</button>
                </div>

                <div style="display:flex;align-items:center;justify-content:space-between;padding:10px 12px;background:var(--panel);border-radius:8px;border:1px solid var(--border)">
                  <div>
                    <div style="font-weight:700;font-size:12px;color:#a78bfa">⚡ Dashboard paste fallback</div>
                    <div style="font-size:11px;color:var(--muted)">If the jail extract is already on the clipboard, click this on ShamrockLeads to merge and open Write / Print</div>
                  </div>
                  <button type="button" class="btn-export" style="font-size:11px;padding:5px 10px" onclick="SLHydrate.copyBookmarklet('dashboard_fill')">📋 Copy Code</button>
                </div>
              </div>
            </div>

          </div>

          <!-- Footer -->
          <div class="modal-footer" style="display:flex;justify-content:space-between;align-items:center;padding:14px 20px;border-top:1px solid var(--border);background:rgba(0,0,0,0.2)">
            <span style="font-size:11px;color:var(--muted)">Keyboard shortcut: <kbd style="background:var(--panel);padding:2px 6px;border-radius:4px;border:1px solid var(--border);color:var(--text)">⌘ Shift V</kbd> to fast hydrate</span>
            <button type="button" class="btn-cancel" onclick="SLHydrate.closeModal()">Close</button>
          </div>

        </div>
      `;

      div.addEventListener('click', (e) => {
        if (e.target === div) this.closeModal();
      });

      document.body.appendChild(div);
    },

    pasteFromClipboardToInput: async function() {
      const text = await this.readClipboardData();
      if (text) {
        const textarea = document.getElementById('slHydrateTextarea');
        if (textarea) {
          textarea.value = text;
          this.updatePreviewFromInput();
        }
      } else {
        alert('Clipboard empty or permission not granted. Please paste manually into the box with ⌘V.');
      }
    },

    /**
     * Initialize Global Listeners & Keyboard Shortcuts
     */
    applyDeepLink: function() {
      try {
        const q = new URLSearchParams(location.search || '');
        const tab = (q.get('tab') || '').trim();
        if (tab && typeof switchTab === 'function') {
          const id = tab.indexOf('tab') === 0
            ? tab
            : 'tab' + tab.charAt(0).toUpperCase() + tab.slice(1);
          switchTab(id);
        }
      } catch (e) { /* ignore */ }
    },

    init: function() {
      this.injectModalHtml();
      this.captureExtractFromUrl();
      this.listenForExtractMessages();
      this.applyDeepLink();

      // Keyboard Shortcut: Cmd+Shift+V or Ctrl+Shift+V
      document.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === 'V' || e.key === 'v')) {
          e.preventDefault();
          this.pasteAndHydrate();
        }
      });

      setTimeout(() => { this.consumePendingExtract(); }, 250);
    }
  };

  window.SLHydrate = SLHydrate;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => SLHydrate.init());
  } else {
    SLHydrate.init();
  }
})();
