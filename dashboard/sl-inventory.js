/* ═══════════════════════════════════════════════════════
   ShamrockLeads — POA Inventory Management Module
   Full lifecycle: View, Add, Assign, Void, Reassign
   ═══════════════════════════════════════════════════════ */

const SLInventory = (() => {
  const API = window.API || '';
  let _data = { tiers: [], totals: { osi: 0, palmetto: 0 } };
  let _allPowers = [];
  let _filter = { surety: 'all', status: 'all', search: '' };
  let _detailPage = 1;
  const PAGE_SIZE = 50;
  const _selected = new Set(); // track selected POA keys: "poaNum|suretyId"
  let _searchDebounce = null;
  let _defendantCharges = []; // parsed from selected defendant's record

  // ── Open / Close Modal ──
  function open() {
    document.getElementById('inventoryModal').classList.add('show');
    loadSummary();
  }
  function close() {
    document.getElementById('inventoryModal').classList.remove('show');
  }

  // ── Load Summary (Tier Aggregation) ──
  async function loadSummary() {
    const body = document.getElementById('invSummaryBody');
    body.innerHTML = '<div class="inv-loading"><div class="btn-spinner"></div><span>Loading inventory…</span></div>';
    try {
      const r = await fetch(`${API}/api/poa/inventory`);
      _data = await r.json();
      renderSummary();
      renderMiniKpis();
      _checkLowStockBanner();
    } catch (e) {
      body.innerHTML = `<div class="inv-error">❌ Failed to load inventory: ${e.message}</div>`;
    }
  }

  // ── Mini KPI Bar ──
  function renderMiniKpis() {
    const el = document.getElementById('invKpis');
    if (!el) return;
    const osiTotal = _data.totals?.osi || 0;
    const palmTotal = _data.totals?.palmetto || 0;
    const total = osiTotal + palmTotal;
    const LOW = 5;
    const osiLow = (_data.tiers || []).filter(t => t.surety_id === 'osi' && t.available <= LOW).length;
    const palmLow = (_data.tiers || []).filter(t => t.surety_id === 'palmetto' && t.available <= LOW).length;
    const totalLow = osiLow + palmLow;

    el.innerHTML = `
      <div class="inv-kpi">
        <div class="inv-kpi-value">${total}</div>
        <div class="inv-kpi-label">Total Available</div>
      </div>
      <div class="inv-kpi inv-kpi-osi">
        <div class="inv-kpi-value">${osiTotal}</div>
        <div class="inv-kpi-label">🛡️ OSI</div>
      </div>
      <div class="inv-kpi inv-kpi-palm">
        <div class="inv-kpi-value">${palmTotal}</div>
        <div class="inv-kpi-label">🌴 Palmetto</div>
      </div>
      <div class="inv-kpi ${totalLow > 0 ? 'inv-kpi-alert' : ''}">
        <div class="inv-kpi-value">${totalLow}</div>
        <div class="inv-kpi-label">⚠️ Low Tiers</div>
      </div>
    `;
  }

  // ── Render Summary View (Tier Cards) ──
  function renderSummary() {
    const body = document.getElementById('invSummaryBody');
    const tiers = _data.tiers || [];
    const LOW = 5;

    const osi = tiers.filter(t => t.surety_id === 'osi');
    const palm = tiers.filter(t => t.surety_id === 'palmetto');

    const renderTierCards = (items, suretyLabel) => {
      if (items.length === 0) return '<div class="inv-empty-tier">No inventory data</div>';
      return items.map(t => {
        const maxCapacity = t.available + 10; // estimated capacity for bar
        const pct = Math.min(100, (t.available / maxCapacity) * 100);
        const isLow = t.available <= LOW;
        const isCritical = t.available <= 2;
        const maxBondFmt = t.max_bond_value >= 1000 ? `$${(t.max_bond_value / 1000).toFixed(0)}K` : `$${t.max_bond_value}`;
        const healthClass = isCritical ? 'critical' : isLow ? 'low' : 'ok';
        const statusLabel = isCritical ? 'CRITICAL' : isLow ? 'LOW' : 'OK';
        const statusIcon = isCritical ? '🔴' : isLow ? '🟡' : '✅';
        return `
          <div class="inv-tier-card inv-tier-${healthClass}">
            <div class="inv-tier-header">
              <span class="inv-tier-prefix">${t.poa_prefix}</span>
              <span class="inv-tier-cap">≤ ${maxBondFmt}</span>
            </div>
            <div class="inv-tier-count">${t.available}</div>
            <div class="inv-tier-bar"><div class="inv-tier-fill inv-fill-${healthClass}" style="width:${pct}%"></div></div>
            <div class="inv-tier-meta">
              <span class="inv-health-badge inv-badge-${healthClass}">${statusIcon} ${statusLabel}</span>
              <span class="inv-next-serial">Next: ${t.next_serial || '—'}</span>
            </div>
          </div>`;
      }).join('');
    };

    body.innerHTML = `
      <div class="inv-surety-section">
        <div class="inv-surety-header inv-header-osi">
          <div class="inv-surety-name">
            <span class="inv-surety-icon">🛡️</span>
            <div>
              <div class="inv-surety-title">O'Shaughnahill Surety & Ins.</div>
              <div class="inv-surety-subtitle">West Palm Beach, FL · OSI Prefix</div>
            </div>
          </div>
          <div class="inv-surety-badge">${_data.totals?.osi || 0} available</div>
        </div>
        <div class="inv-tier-grid">${renderTierCards(osi, 'OSI')}</div>
      </div>
      <div class="inv-surety-section">
        <div class="inv-surety-header inv-header-palm">
          <div class="inv-surety-name">
            <span class="inv-surety-icon">🌴</span>
            <div>
              <div class="inv-surety-title">Palmetto Surety Corporation</div>
              <div class="inv-surety-subtitle">Multi-State · PSC Prefix</div>
            </div>
          </div>
          <div class="inv-surety-badge inv-badge-palm">${_data.totals?.palmetto || 0} available</div>
        </div>
        <div class="inv-tier-grid">${renderTierCards(palm, 'Palmetto')}</div>
      </div>
    `;
  }

  // ── Switch Tab ──
  function switchTab(tab) {
    document.querySelectorAll('.inv-tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelector(`.inv-tab-btn[data-tab="${tab}"]`)?.classList.add('active');
    document.querySelectorAll('.inv-tab-pane').forEach(p => p.style.display = 'none');
    document.getElementById(`invPane_${tab}`).style.display = 'block';
    if (tab === 'detail') loadDetailView();
    if (tab === 'summary') loadSummary();
  }

  // ── Detail View (All Powers Table) ──
  async function loadDetailView() {
    const tbody = document.getElementById('invDetailBody');
    tbody.innerHTML = '<tr><td colspan="8"><div class="inv-loading"><div class="btn-spinner"></div><span>Loading powers…</span></div></td></tr>';
    try {
      const params = new URLSearchParams({ page: _detailPage, limit: PAGE_SIZE });
      if (_filter.surety !== 'all') params.set('surety', _filter.surety);
      if (_filter.status !== 'all') params.set('status', _filter.status);
      if (_filter.search) params.set('search', _filter.search);
      const r = await fetch(`${API}/api/poa/list?${params}`);
      const d = await r.json();
      _allPowers = d.powers || [];
      renderDetailTable(d);
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="8"><div class="inv-error">Error: ${e.message}</div></td></tr>`;
    }
  }

  function renderDetailTable(d) {
    const tbody = document.getElementById('invDetailBody');
    const powers = d.powers || [];
    const total = d.total || 0;
    const pages = d.pages || 1;

    if (powers.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" class="inv-empty-state">No powers found matching filters</td></tr>';
    } else {
      tbody.innerHTML = powers.map(p => {
        const key = `${p.poa_number}|${p.surety_id}`;
        const checked = _selected.has(key) ? 'checked' : '';
        const statusCls = p.status === 'available' ? 'inv-st-available' : p.status === 'assigned' ? 'inv-st-assigned' : p.status === 'voided' ? 'inv-st-voided' : 'inv-st-other';
        const maxBondFmt = p.max_bond_value >= 1000 ? `$${(p.max_bond_value / 1000).toFixed(0)}K` : `$${p.max_bond_value || 0}`;
        let expHtml = '—';
        if (p.expiration) {
          const expDate = new Date(p.expiration);
          const now = new Date();
          const daysLeft = Math.ceil((expDate - now) / 86400000);
          const expStr = expDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
          if (daysLeft < 0) {
            expHtml = `<span style="background:rgba(239,68,68,.15);color:#f87171;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700;white-space:nowrap" title="Expired ${Math.abs(daysLeft)}d ago">⛔ EXPIRED ${expStr}</span>`;
          } else if (daysLeft <= 7) {
            expHtml = `<span style="background:rgba(239,68,68,.12);color:#fca5a5;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700;white-space:nowrap" title="${daysLeft}d remaining — CRITICAL">🚨 ${daysLeft}d — ${expStr}</span>`;
          } else if (daysLeft <= 30) {
            expHtml = `<span style="background:rgba(245,158,11,.12);color:#fcd34d;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600;white-space:nowrap" title="${daysLeft}d remaining">⚠️ ${daysLeft}d — ${expStr}</span>`;
          } else {
            expHtml = `<span style="color:var(--text-secondary,var(--muted))" title="${daysLeft}d remaining">${expStr}</span>`;
          }
        }
        const actions = [];
        if (p.status === 'available') {
          actions.push(`<button class="inv-btn inv-btn-assign" onclick="SLInventory.openAssignDialog('${p.poa_number}','${p.surety_id}','${p.poa_prefix}')" title="Assign to defendant">📌 Assign</button>`);
          actions.push(`<button class="inv-btn inv-btn-void" onclick="SLInventory.voidPower('${p.poa_number}','${p.surety_id}')" title="Void">🗑️ Void</button>`);
        } else if (p.status === 'assigned') {
          actions.push(`<button class="inv-btn inv-btn-reassign" onclick="SLInventory.reassignPower('${p.poa_number}','${p.surety_id}')" title="Reassign">🔄 Move</button>`);
          actions.push(`<button class="inv-btn inv-btn-void" onclick="SLInventory.voidPower('${p.poa_number}','${p.surety_id}')" title="Void">🗑️ Void</button>`);
        } else if (p.status === 'voided') {
          actions.push(`<button class="inv-btn inv-btn-restore" onclick="SLInventory.restorePower('${p.poa_number}','${p.surety_id}')" title="Restore">♻️ Restore</button>`);
        }
        return `<tr class="inv-row ${checked ? 'inv-row-selected' : ''}" onclick="SLInventory.toggleRowSelect(event,'${p.poa_number}','${p.surety_id}')">
          <td style="text-align:center"><input type="checkbox" class="inv-cb" data-key="${key}" ${checked} onclick="event.stopPropagation();SLInventory.toggleSelect('${p.poa_number}','${p.surety_id}')"></td>
          <td><span class="inv-poa-mono">${p.poa_full || p.poa_number}</span></td>
          <td><span class="inv-surety-chip ${p.surety_id === 'osi' ? 'inv-chip-osi' : 'inv-chip-palm'}">${p.surety_id === 'osi' ? '🛡️ OSI' : '🌴 PSC'}</span></td>
          <td class="inv-cell-tier">${p.poa_prefix}</td>
          <td class="inv-cell-bond">${maxBondFmt}</td>
          <td class="inv-cell-exp">${expHtml}</td>
          <td><span class="inv-status-pill ${statusCls}">${p.status}</span></td>
          <td class="inv-cell-case">${p.bond_case_id
            ? `<div style="font-weight:600">${p.bond_case_id}${p.defendant_name ? ` <span style="color:var(--muted);font-weight:400">· ${p.defendant_name}</span>` : ''}</div>${p.charge ? `<div style="font-size:11px;color:#94a3b8;margin-top:2px">⚖️ ${p.charge}</div>` : ''}${p.appearance_bond_number ? `<div style="font-size:11px;color:#6ee7b7;margin-top:1px">📄 Bond #${p.appearance_bond_number}</div>` : ''}`
            : '—'}</td>
          <td class="inv-cell-actions">${actions.join('')}</td>
        </tr>`;
      }).join('');
    }
    _updateBulkBar();

    // Pagination
    document.getElementById('invDetailPagination').innerHTML = `
      <button class="inv-page-btn" ${_detailPage <= 1 ? 'disabled' : ''} onclick="SLInventory.detailPage(${_detailPage - 1})">‹ Prev</button>
      <span class="inv-page-info">${total.toLocaleString()} powers · Page ${_detailPage} of ${pages}</span>
      <button class="inv-page-btn" ${_detailPage >= pages ? 'disabled' : ''} onclick="SLInventory.detailPage(${_detailPage + 1})">Next ›</button>
    `;
  }

  function detailPage(p) { _detailPage = p; loadDetailView(); }

  function applyFilter(key, value) {
    _filter[key] = value;
    _detailPage = 1;
    loadDetailView();
  }
  function searchFilter() {
    _filter.search = document.getElementById('invSearchInput')?.value || '';
    _detailPage = 1;
    loadDetailView();
  }

  // ── Add POA (Manual Entry) ──
  function showAddForm() {
    const area = document.getElementById('invAddFormArea');
    area.style.display = area.style.display === 'none' ? 'block' : 'none';
  }

  async function submitAdd() {
    const surety = document.getElementById('addSurety').value;
    const prefix = document.getElementById('addPrefix').value;
    const startNum = document.getElementById('addStart').value.trim();
    const endNum = document.getElementById('addEnd').value.trim() || startNum;
    const maxBond = parseInt(document.getElementById('addMaxBond').value || '0');
    const exp = document.getElementById('addExpiration').value || null;
    const statusEl = document.getElementById('addStatus');

    if (!startNum || !prefix || !surety) {
      statusEl.innerHTML = '<span class="inv-form-error">❌ Fill in all required fields</span>';
      return;
    }

    statusEl.innerHTML = '<div class="inv-loading inv-loading-sm"><div class="btn-spinner"></div><span>Adding powers…</span></div>';
    try {
      const r = await fetch(`${API}/api/poa/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ surety_id: surety, poa_prefix: prefix, start: startNum, end: endNum, max_bond_value: maxBond, expiration: exp }),
      });
      const d = await r.json();
      if (d.error) {
        statusEl.innerHTML = `<span class="inv-form-error">❌ ${d.error}</span>`;
      } else {
        statusEl.innerHTML = `<span class="inv-form-success">✅ Added ${d.count || 1} power(s)</span>`;
        toast('success', `Added ${d.count || 1} POA(s) to ${surety.toUpperCase()}`);
        loadSummary();
        setTimeout(() => { statusEl.innerHTML = ''; }, 3000);
      }
    } catch (e) {
      statusEl.innerHTML = `<span class="inv-form-error">❌ ${e.message}</span>`;
    }
  }

  // ── Selection Management ──
  function toggleSelect(poaNum, surety) {
    const key = `${poaNum}|${surety}`;
    if (_selected.has(key)) _selected.delete(key); else _selected.add(key);
    // Update row highlight
    const cb = document.querySelector(`.inv-cb[data-key="${key}"]`);
    if (cb) { cb.checked = _selected.has(key); cb.closest('tr')?.classList.toggle('inv-row-selected', _selected.has(key)); }
    _updateBulkBar();
  }
  function toggleRowSelect(event, poaNum, surety) {
    if (event.target.tagName === 'INPUT' || event.target.tagName === 'BUTTON') return;
    toggleSelect(poaNum, surety);
  }
  function toggleSelectAll(checked) {
    _allPowers.forEach(p => {
      const key = `${p.poa_number}|${p.surety_id}`;
      if (checked) _selected.add(key); else _selected.delete(key);
    });
    document.querySelectorAll('.inv-cb').forEach(cb => { cb.checked = checked; cb.closest('tr')?.classList.toggle('inv-row-selected', checked); });
    _updateBulkBar();
  }
  function clearSelection() {
    _selected.clear();
    document.querySelectorAll('.inv-cb').forEach(cb => { cb.checked = false; cb.closest('tr')?.classList.remove('inv-row-selected'); });
    const sa = document.getElementById('invSelectAll'); if (sa) sa.checked = false;
    _updateBulkBar();
  }
  function _updateBulkBar() {
    const bar = document.getElementById('invBulkBar');
    const countEl = document.getElementById('invBulkCount');
    if (!bar) return;
    const n = _selected.size;
    if (n === 0) { bar.style.display = 'none'; return; }
    bar.style.display = 'flex';
    if (countEl) countEl.textContent = n;
  }

  // ── Assign Power to Defendant (single — now opens modal with 1 selected) ──
  function openAssignDialog(poaNum, surety, prefix) {
    _selected.clear();
    _selected.add(`${poaNum}|${surety}`);
    _updateBulkBar();
    openBulkAssignModal();
  }

  async function assignPower(poaNum, surety, prefix, bookingNumber) {
    try {
      const r = await fetch(`${API}/api/poa/assign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ poa_number: poaNum, surety_id: surety, poa_prefix: prefix, booking_number: bookingNumber }),
      });
      const d = await r.json();
      if (d.error) { toast('error', d.error); }
      else { toast('success', `POA ${d.poa_full} assigned to ${bookingNumber}`); loadDetailView(); loadSummary(); }
    } catch (e) { toast('error', e.message); }
  }

  // ── Bulk Assign Modal ──
  function openBulkAssignModal() {
    const modal = document.getElementById('bulkAssignModal');
    if (!modal) return;
    modal.style.display = 'flex';
    // Must add .show class for opacity transition (CSS: .inv-overlay starts at opacity:0)
    requestAnimationFrame(() => modal.classList.add('show'));
    // Update subtitle and chip strip
    const n = _selected.size;
    const sub = document.getElementById('bulkAssignSubtitle');
    if (sub) sub.textContent = `${n} power${n !== 1 ? 's' : ''} selected`;
    const submitCount = document.getElementById('baSubmitCount');
    if (submitCount) submitCount.textContent = n;
    // Render selected POAs as chips
    const strip = document.getElementById('baSelectedStrip');
    if (strip) {
      const chips = [..._selected].map(k => {
        const [num] = k.split('|');
        const pw = _allPowers.find(p => p.poa_number === num);
        const label = pw ? (pw.poa_full || `${pw.poa_prefix} ${num}`) : num;
        return `<span class="ba-chip">${label}<button onclick="SLInventory.removeFromSelection('${k}')" class="ba-chip-x">✕</button></span>`;
      });
      strip.innerHTML = chips.join('');
    }
    // Reset form fields
    const s = document.getElementById('baDefendantSearch'); if (s) s.value = '';
    const b = document.getElementById('baBookingNumber'); if (b) b.value = '';
    const d = document.getElementById('baDefendantName'); if (d) d.value = '';
    const r = document.getElementById('baSearchResults'); if (r) { r.innerHTML = ''; r.style.display = 'none'; }
    const st = document.getElementById('baBulkStatus'); if (st) st.innerHTML = '';
    // Reset charge mapping
    _defendantCharges = [];
    const cms = document.getElementById('baChargeMappingSection'); if (cms) cms.style.display = 'none';
    const cmb = document.getElementById('baChargeMappingBody'); if (cmb) cmb.innerHTML = '';
    // Reset indemnitor section
    const indSec = document.getElementById('baIndemnitorSection'); if (indSec) indSec.style.display = 'none';
    ['baIndemName','baIndemPhone','baIndemEmail','baIndemRelationship','baBondAmount','baPremium','baCourtDate','baCourtLocation','baCaseNumber','baPaymentMethod'].forEach(id => {
      const el = document.getElementById(id); if (el) { if (el.tagName === 'SELECT') el.selectedIndex = 0; else el.value = ''; }
    });
    const toggleEl = document.getElementById('baRecordBondToggle'); if (toggleEl) toggleEl.checked = true;
    const extraFields = document.getElementById('baExtraBondFields'); if (extraFields) extraFields.style.display = '';
    // Wire toggle for extra fields visibility
    if (toggleEl && !toggleEl._wired) {
      toggleEl._wired = true;
      toggleEl.addEventListener('change', () => {
        const ef = document.getElementById('baExtraBondFields');
        if (ef) ef.style.display = toggleEl.checked ? '' : 'none';
        // Update submit button text
        const btn = document.getElementById('baSubmitBtn');
        if (btn) btn.innerHTML = toggleEl.checked ? `☘️ Assign & Record Bond` : `📌 Assign <span id="baSubmitCount">${_selected.size}</span> POA(s)`;
      });
    }
    // Store selected search result data for bond recording
    _selectedArrestData = null;
  }
  function closeBulkAssignModal() {
    const modal = document.getElementById('bulkAssignModal');
    if (modal) { modal.classList.remove('show'); modal.style.display = 'none'; }
  }
  function removeFromSelection(key) {
    _selected.delete(key);
    if (_selected.size === 0) { closeBulkAssignModal(); clearSelection(); return; }
    openBulkAssignModal(); // re-render
    // Also update table checkboxes
    const cb = document.querySelector(`.inv-cb[data-key="${key}"]`);
    if (cb) { cb.checked = false; cb.closest('tr')?.classList.remove('inv-row-selected'); }
    _updateBulkBar();
  }

  // ── Defendant Search in Bulk Assign Modal ──
  function searchDefendantsForAssign(query) {
    clearTimeout(_searchDebounce);
    if (!query || query.length < 2) {
      const r = document.getElementById('baSearchResults'); if (r) r.style.display = 'none';
      return;
    }
    _searchDebounce = setTimeout(async () => {
      try {
        const r = await fetch(`${API}/api/leads?search=${encodeURIComponent(query)}&days=0&limit=8`);
        const d = await r.json();
        const results = d.leads || d.records || [];
        const el = document.getElementById('baSearchResults');
        if (!el) return;
        if (results.length === 0) {
          el.innerHTML = '<div class="ba-no-results">No matching defendants found</div>';
          el.style.display = 'block';
          return;
        }
        el.innerHTML = results.map(rec => {
          const name = rec.full_name || rec.Name || `${rec.First_Name || ''} ${rec.Last_Name || ''}`.trim() || 'Unknown';
          const booking = rec.booking_number || rec.Booking_Number || '';
          const county = rec.county || rec.County || '';
          const charges = rec.charges || rec.Charges || '';
          const bond = rec.bond_amount || rec.Bond_Amount || 0;
          const bondFmt = bond >= 1000 ? `$${(bond/1000).toFixed(0)}K` : `$${bond}`;
          // Escape for inline onclick — store charges in data attribute
          const escapedCharges = charges.replace(/'/g, "\\'").replace(/"/g, '&quot;');
          const escapedCounty = county.replace(/'/g, "\\'");
          return `<div class="ba-result-row" onclick="SLInventory.selectDefendantForAssign('${booking.replace(/'/g,'\\\'')}','${name.replace(/'/g,'\\\'')}','${escapedCharges}',${bond},'${escapedCounty}')">
            <div class="ba-result-name">${name}</div>
            <div class="ba-result-meta">${county ? county + ' · ' : ''}${booking} · ${bondFmt}${charges ? ' · ' + charges.substring(0,60) : ''}</div>
          </div>`;
        }).join('');
        el.style.display = 'block';
      } catch (_) {}
    }, 300);
  }
  function selectDefendantForAssign(booking, name, chargesStr, bondAmount, county) {
    const b = document.getElementById('baBookingNumber'); if (b) b.value = booking;
    const d = document.getElementById('baDefendantName'); if (d) d.value = name;
    const r = document.getElementById('baSearchResults'); if (r) r.style.display = 'none';
    const s = document.getElementById('baDefendantSearch'); if (s) s.value = `${name} — ${booking}`;
    // Parse charges (semicolon-delimited) and show charge mapping
    _defendantCharges = (chargesStr || '')
      .split(/[;|]/)
      .map(c => c.trim())
      .filter(c => c.length > 0);
    _renderChargeMapping();
    // Show indemnitor section (Step 3)
    const indSec = document.getElementById('baIndemnitorSection'); if (indSec) indSec.style.display = '';
    // Auto-fill bond amount if available from arrest record
    if (bondAmount && parseFloat(bondAmount) > 0) {
      const baEl = document.getElementById('baBondAmount'); if (baEl) baEl.value = parseFloat(bondAmount);
      const premEl = document.getElementById('baPremium'); if (premEl) premEl.value = Math.round(parseFloat(bondAmount) * 0.10);
    }
    // Store arrest data for bond recording
    _selectedArrestData = { booking_number: booking, defendant_name: name, charges: chargesStr || '', county: county || '', bond_amount: parseFloat(bondAmount || 0) };
    // Update submit button to reflect Record Bond mode
    const toggle = document.getElementById('baRecordBondToggle');
    const btn = document.getElementById('baSubmitBtn');
    if (toggle && toggle.checked && btn) btn.innerHTML = '☘️ Assign & Record Bond';
  }
  function _renderChargeMapping() {
    const section = document.getElementById('baChargeMappingSection');
    const body = document.getElementById('baChargeMappingBody');
    if (!section || !body) return;
    const poaKeys = [..._selected];
    if (poaKeys.length === 0) { section.style.display = 'none'; return; }
    section.style.display = 'block';
    // Build charge options
    const chargeOpts = _defendantCharges.length > 0
      ? _defendantCharges.map((c, i) => `<option value="${c.replace(/"/g,'&quot;')}">${c}</option>`).join('')
      : '';
    const hasCharges = _defendantCharges.length > 0;
    body.innerHTML = poaKeys.map((key, idx) => {
      const [num] = key.split('|');
      const pw = _allPowers.find(p => p.poa_number === num);
      const label = pw ? (pw.poa_full || `${pw.poa_prefix} ${num}`) : num;
      // Auto-map 1:1 if counts match
      const autoCharge = (_defendantCharges.length === poaKeys.length) ? _defendantCharges[idx] : '';
      return `
        <div class="ba-map-row" data-poa-key="${key}">
          <div class="ba-map-poa">
            <span class="ba-chip" style="margin:0">${label}</span>
          </div>
          <div class="ba-map-fields">
            <div class="ba-field" style="flex:2">
              <label class="ba-label">Charge</label>
              ${hasCharges
                ? `<select class="ba-input ba-charge-select" data-idx="${idx}">
                    <option value="">— Select charge —</option>
                    ${chargeOpts}
                    <option value="__custom">✏️ Enter manually…</option>
                  </select>
                  <input type="text" class="ba-input ba-charge-custom" data-idx="${idx}" placeholder="Type charge description…" style="display:none;margin-top:6px">`
                : `<input type="text" class="ba-input ba-charge-custom-only" data-idx="${idx}" placeholder="e.g. BATTERY - DOMESTIC VIOLENCE">`
              }
            </div>
            <div class="ba-field" style="flex:1">
              <label class="ba-label">Appearance Bond #</label>
              <input type="text" class="ba-input ba-bond-input" data-idx="${idx}" placeholder="e.g. 26-CF-001234">
            </div>
          </div>
        </div>`;
    }).join('');
    // Auto-select if 1:1 match
    if (_defendantCharges.length === poaKeys.length && hasCharges) {
      body.querySelectorAll('.ba-charge-select').forEach((sel, i) => {
        sel.value = _defendantCharges[i] || '';
      });
    }
    // Wire up "Enter manually" toggle for dropdowns
    body.querySelectorAll('.ba-charge-select').forEach(sel => {
      sel.addEventListener('change', (e) => {
        const idx = e.target.dataset.idx;
        const customInput = body.querySelector(`.ba-charge-custom[data-idx="${idx}"]`);
        if (customInput) customInput.style.display = e.target.value === '__custom' ? 'block' : 'none';
      });
    });
  }

  // ── Auto-calc premium helper ──
  function _autoCalcPremium() {
    const amtEl = document.getElementById('baBondAmount');
    const premEl = document.getElementById('baPremium');
    if (amtEl && premEl) {
      const amt = parseFloat(amtEl.value || 0);
      if (amt > 0) premEl.value = Math.round(amt * 0.10);
    }
  }

  // ── Submit Bulk Assign ──
  async function submitBulkAssign() {
    const bookingNum = (document.getElementById('baBookingNumber')?.value || '').trim();
    const defName = (document.getElementById('baDefendantName')?.value || '').trim();
    const statusEl = document.getElementById('baBulkStatus');
    if (!bookingNum) { if (statusEl) statusEl.innerHTML = '<span style="color:#f87171;font-size:13px">❌ Booking number is required</span>'; return; }
    if (_selected.size === 0) { if (statusEl) statusEl.innerHTML = '<span style="color:#f87171;font-size:13px">❌ No POAs selected</span>'; return; }
    const suretyIds = [...new Set([..._selected].map(k => k.split('|')[1]))];
    // Build per-POA assignments with charge + appearance bond data
    const poaKeys = [..._selected];
    const mappingBody = document.getElementById('baChargeMappingBody');
    const assignments = poaKeys.map((key, idx) => {
      const [poaNum] = key.split('|');
      let charge = '';
      let bondNum = '';
      if (mappingBody) {
        // Check dropdown first, then custom input
        const sel = mappingBody.querySelector(`.ba-charge-select[data-idx="${idx}"]`);
        if (sel && sel.value && sel.value !== '__custom') {
          charge = sel.value;
        } else {
          const custom = mappingBody.querySelector(`.ba-charge-custom[data-idx="${idx}"]`);
          const customOnly = mappingBody.querySelector(`.ba-charge-custom-only[data-idx="${idx}"]`);
          charge = (custom?.value || customOnly?.value || '').trim();
        }
        const bondInput = mappingBody.querySelector(`.ba-bond-input[data-idx="${idx}"]`);
        bondNum = (bondInput?.value || '').trim();
      }
      return { poa_number: poaNum, charge, appearance_bond_number: bondNum };
    });

    // Check if we should also record an active bond
    const recordBondToggle = document.getElementById('baRecordBondToggle');
    const shouldRecordBond = recordBondToggle && recordBondToggle.checked;

    if (statusEl) statusEl.innerHTML = '<div class="inv-loading inv-loading-sm"><div class="btn-spinner"></div><span>' + (shouldRecordBond ? 'Assigning & recording bond…' : 'Assigning…') + '</span></div>';
    try {
      // 1. POA assignment
      const r = await fetch(`${API}/api/poa/bulk-assign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ assignments, surety_id: suretyIds[0] || '', bond_case_id: bookingNum, defendant_name: defName }),
      });
      const d = await r.json();
      if (d.error) { if (statusEl) statusEl.innerHTML = `<span style="color:#f87171;font-size:13px">❌ ${d.error}</span>`; return; }
      let msg = `✅ ${d.assigned_count} POA(s) assigned to ${defName || bookingNum}` + (d.skipped_count ? ` (${d.skipped_count} skipped)` : '');

      // 2. Optionally record as active bond via /api/bonds/record
      if (shouldRecordBond) {
        const firstPOA = assignments[0]?.poa_number || '';
        const allCharges = assignments.map(a => a.charge).filter(Boolean).join('; ');
        const indemName = (document.getElementById('baIndemName')?.value || '').trim();
        const indemPhone = (document.getElementById('baIndemPhone')?.value || '').trim();
        const indemEmail = (document.getElementById('baIndemEmail')?.value || '').trim();
        const indemRel = (document.getElementById('baIndemRelationship')?.value || '').trim();
        const bondAmount = parseFloat(document.getElementById('baBondAmount')?.value || 0);
        const premium = parseFloat(document.getElementById('baPremium')?.value || 0);
        const courtDate = (document.getElementById('baCourtDate')?.value || '').trim();
        const courtLocation = (document.getElementById('baCourtLocation')?.value || '').trim();
        const caseNumber = (document.getElementById('baCaseNumber')?.value || '').trim();
        const paymentMethod = (document.getElementById('baPaymentMethod')?.value || 'cash').trim();
        const county = _selectedArrestData?.county || '';

        const bondPayload = {
          defendant_name: defName,
          booking_number: bookingNum,
          county: county,
          bond_amount: bondAmount,
          premium: premium,
          surety: suretyIds[0] || 'osi',
          poa_number: firstPOA,
          case_number: caseNumber,
          court_date: courtDate,
          court_location: courtLocation,
          bond_date: new Date().toISOString().split('T')[0],
          charges: allCharges || _selectedArrestData?.charges || '',
          indemnitor_name: indemName,
          indemnitor_phone: indemPhone,
          indemnitor_email: indemEmail,
          indemnitor_relationship: indemRel,
          payment_method: paymentMethod,
          agent_name: "Brendan O'Neal",
          notes: `POA assignment + bond recorded from POA Inventory (${_selected.size} POA(s))`,
        };

        try {
          const br = await fetch(`${API}/api/bonds/record`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(bondPayload),
          });
          const bd = await br.json();
          if (bd.success) {
            msg += ` · ☘️ Bond recorded ($${bondAmount.toLocaleString()})`;
            // Refresh active bonds tab if loaded
            setTimeout(() => {
              if (window.loadActiveBonds) loadActiveBonds();
              if (window.SLKanban?.render) SLKanban.render();
            }, 300);
          } else {
            const errs = bd.errors ? bd.errors.join(', ') : (bd.error || 'Unknown error');
            msg += ` · ⚠️ Bond record failed: ${errs}`;
          }
        } catch (bondErr) {
          msg += ` · ⚠️ Bond record error: ${bondErr.message}`;
        }
      }

      toast('success', msg);
      closeBulkAssignModal();
      clearSelection();
      loadDetailView();
      loadSummary();
    } catch (e) { if (statusEl) statusEl.innerHTML = `<span style="color:#f87171;font-size:13px">❌ ${e.message}</span>`; }
  }

  // ── Bulk Void ──
  async function bulkVoid() {
    const n = _selected.size;
    if (!n || !confirm(`Void ${n} selected POA(s)? This marks them as unusable.`)) return;
    let voided = 0;
    for (const key of _selected) {
      const [num, surety] = key.split('|');
      try {
        const r = await fetch(`${API}/api/poa/void`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ poa_number: num, surety_id: surety }) });
        const d = await r.json();
        if (d.success) voided++;
      } catch (_) {}
    }
    toast('success', `Voided ${voided} of ${n} POA(s)`);
    clearSelection();
    loadDetailView();
    loadSummary();
  }

  // ── Void Power ──
  async function voidPower(poaNum, surety) {
    if (!confirm(`Void POA ${poaNum}? This marks it as unusable.`)) return;
    try {
      const r = await fetch(`${API}/api/poa/void`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ poa_number: poaNum, surety_id: surety }),
      });
      const d = await r.json();
      if (d.error) toast('error', d.error);
      else { toast('success', `POA ${poaNum} voided`); loadDetailView(); loadSummary(); }
    } catch (e) { toast('error', e.message); }
  }

  // ── Reassign ──
  async function reassignPower(poaNum, surety) {
    const action = prompt(`POA ${poaNum} is currently assigned.\n\nType "release" to make available again, or enter a new Booking Number to reassign:`);
    if (!action || !action.trim()) return;
    const endpoint = action.trim().toLowerCase() === 'release' ? '/api/poa/release' : '/api/poa/reassign';
    const body = action.trim().toLowerCase() === 'release'
      ? { poa_number: poaNum, surety_id: surety }
      : { poa_number: poaNum, surety_id: surety, new_booking_number: action.trim() };
    try {
      const r = await fetch(`${API}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (d.error) toast('error', d.error);
      else { toast('success', d.message || 'Done'); loadDetailView(); loadSummary(); }
    } catch (e) { toast('error', e.message); }
  }

  // ── Restore (Un-void) ──
  async function restorePower(poaNum, surety) {
    if (!confirm(`Restore POA ${poaNum} back to available?`)) return;
    try {
      const r = await fetch(`${API}/api/poa/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ poa_number: poaNum, surety_id: surety }),
      });
      const d = await r.json();
      if (d.error) toast('error', d.error);
      else { toast('success', `POA ${poaNum} restored`); loadDetailView(); loadSummary(); }
    } catch (e) { toast('error', e.message); }
  }

  // ── Toast helper ──
  function toast(type, msg) {
    if (window.SL && SL.showToast) { SL.showToast(type, msg); return; }
    const t = document.getElementById('toast');
    if (!t) return;
    t.className = `toast-notification toast-${type} show`;
    t.querySelector('.toast-icon').textContent = type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️';
    t.querySelector('.toast-message').textContent = msg;
    setTimeout(() => t.classList.remove('show'), 4000);
  }

  // ── Update prefix options when surety changes ──
  function updatePrefixOptions() {
    const surety = document.getElementById('addSurety').value;
    const select = document.getElementById('addPrefix');
    const prefixes = surety === 'osi'
      ? [{ v: 'OSI3', l: 'OSI3 — ≤ $3K' }, { v: 'OSI6', l: 'OSI6 — ≤ $6K' }, { v: 'OSI16', l: 'OSI16 — ≤ $16K' }, { v: 'OSI51', l: 'OSI51 — ≤ $51K' }, { v: 'OSI101', l: 'OSI101 — ≤ $101K' }, { v: 'OSI251', l: 'OSI251 — ≤ $251K' }]
      : [{ v: 'PSC2', l: 'PSC2 — ≤ $2K' }, { v: 'PSC5', l: 'PSC5 — ≤ $5K' }, { v: 'PSC15', l: 'PSC15 — ≤ $15K' }, { v: 'PSC25', l: 'PSC25 — ≤ $25K' }, { v: 'PSC50', l: 'PSC50 — ≤ $50K' }, { v: 'PSC75', l: 'PSC75 — ≤ $75K' }, { v: 'PSC105', l: 'PSC105 — ≤ $105K' }];
    select.innerHTML = prefixes.map(p => `<option value="${p.v}">${p.l}</option>`).join('');
    autoFillMaxBond();
  }

  function autoFillMaxBond() {
    const prefix = document.getElementById('addPrefix').value;
    const bondMap = { OSI3: 3000, OSI6: 6000, OSI16: 16000, OSI51: 51000, OSI101: 101000, OSI251: 251000, PSC2: 2000, PSC5: 5000, PSC15: 15000, PSC25: 25000, PSC50: 50000, PSC75: 75000, PSC105: 105000 };
    document.getElementById('addMaxBond').value = bondMap[prefix] || 0;
  }

  // ── Image Upload for OCR-based POA Ingestion ──
  function handleDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    document.getElementById('invUploadZone').classList.remove('dragover');
    const files = e.dataTransfer?.files;
    if (files && files.length > 0) processUpload(files[0]);
  }

  function handleUpload(input) {
    if (input.files && input.files.length > 0) processUpload(input.files[0]);
  }

  async function processUpload(file) {
    const resultEl = document.getElementById('invUploadResult');
    resultEl.innerHTML = `<div class="inv-loading"><div class="btn-spinner"></div><span>Analyzing ${file.name}…</span></div>`;
    try {
      const formData = new FormData();
      formData.append('file', file);
      const surety = document.getElementById('addSurety')?.value || 'osi';
      formData.append('surety_id', surety);

      const r = await fetch(`${API}/api/poa/upload-image`, { method: 'POST', body: formData });
      const d = await r.json();
      if (d.error) {
        resultEl.innerHTML = `<div class="inv-upload-error">❌ ${d.error}</div>`;
        return;
      }
      const extracted = d.extracted || [];
      if (extracted.length === 0) {
        resultEl.innerHTML = `<div class="inv-upload-warn">⚠️ No POA serial numbers could be extracted from this image. Try a clearer image or use manual entry.</div>`;
        return;
      }
      resultEl.innerHTML = `
        <div class="inv-upload-success">
          <div class="inv-upload-count">✅ Found ${extracted.length} POA serial number(s)</div>
          <div class="inv-extracted-list">${extracted.map(e => `<span class="inv-extracted-num">${e}</span>`).join('')}</div>
          <div class="inv-upload-confirm-row">
            <button class="inv-btn-submit" onclick="SLInventory.confirmUploadedPOAs(${JSON.stringify(extracted).replace(/"/g, '&quot;')}, '${surety}')">✅ Add All to Inventory</button>
            <button class="inv-btn-cancel" onclick="document.getElementById('invUploadResult').innerHTML=''">Cancel</button>
          </div>
        </div>`;
    } catch (e) {
      resultEl.innerHTML = `<div class="inv-upload-error">❌ Upload failed: ${e.message}</div>`;
    }
  }

  async function confirmUploadedPOAs(serials, surety) {
    const resultEl = document.getElementById('invUploadResult');
    const prefix = document.getElementById('addPrefix')?.value || 'OSI3';
    const maxBond = parseInt(document.getElementById('addMaxBond')?.value || '3000');
    resultEl.innerHTML = `<div class="inv-loading"><div class="btn-spinner"></div><span>Adding ${serials.length} powers…</span></div>`;
    let added = 0;
    for (const serial of serials) {
      try {
        const r = await fetch(`${API}/api/poa/add`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ surety_id: surety, poa_prefix: prefix, start: serial, end: serial, max_bond_value: maxBond }),
        });
        const d = await r.json();
        if (d.count) added += d.count;
      } catch (_) { /* skip */ }
    }
    resultEl.innerHTML = `<div class="inv-upload-success">✅ Added ${added} of ${serials.length} power(s) to inventory</div>`;
    toast('success', `Added ${added} OCR-extracted POAs`);
    loadSummary();
    setTimeout(() => { resultEl.innerHTML = ''; }, 5000);
  }

  // ── Low-Stock Global Banner ──────────────────────────────────────────────
  function _checkLowStockBanner() {
    const LOW = 5;
    const tiers = _data.tiers || [];
    const criticalTiers = tiers.filter(t => t.available <= 2);
    const lowTiers = tiers.filter(t => t.available > 2 && t.available <= LOW);
    const bannerId = 'poaLowStockBanner';
    let banner = document.getElementById(bannerId);
    if (!criticalTiers.length && !lowTiers.length) {
      if (banner) banner.remove();
      return;
    }
    if (!banner) {
      banner = document.createElement('div');
      banner.id = bannerId;
      banner.style.cssText = 'position:fixed;top:60px;left:50%;transform:translateX(-50%);z-index:8500;max-width:600px;width:calc(100% - 32px);border-radius:10px;padding:10px 16px;display:flex;align-items:center;gap:12px;font-size:13px;font-weight:600;box-shadow:0 4px 20px rgba(0,0,0,.4);cursor:pointer;transition:opacity .3s';
      banner.onclick = function() { SLInventory.open(); };
      document.body.appendChild(banner);
    }
    if (criticalTiers.length > 0) {
      banner.style.background = '#ef4444';
      banner.style.color = '#fff';
      banner.innerHTML = '🔴 CRITICAL: ' + criticalTiers.map(t => t.poa_prefix + ' (' + t.available + ' left)').join(', ') + ' — Click to manage POA inventory';
    } else {
      banner.style.background = '#f59e0b';
      banner.style.color = '#000';
      banner.innerHTML = '⚠️ Low Stock: ' + lowTiers.map(t => t.poa_prefix + ' (' + t.available + ' left)').join(', ') + ' — Click to manage POA inventory';
    }
    setTimeout(function() {
      if (banner && banner.parentNode) {
        banner.style.opacity = '0';
        setTimeout(function() { if (banner && banner.parentNode) banner.remove(); }, 300);
      }
    }, 12000);
  }

  // ── State for arrest data from search ──
  let _selectedArrestData = null;

  return {
    open, close, switchTab, loadSummary, loadDetailView,
    applyFilter, searchFilter, detailPage,
    showAddForm, submitAdd, updatePrefixOptions, autoFillMaxBond,
    openAssignDialog, voidPower, reassignPower, restorePower,
    handleUpload, handleDrop, confirmUploadedPOAs,
    checkLowStockBanner: _checkLowStockBanner,
    _autoCalcPremium,
    // Bulk selection + charge mapping
    toggleSelect, toggleRowSelect, toggleSelectAll, clearSelection,
    openBulkAssignModal, closeBulkAssignModal, removeFromSelection,
    searchDefendantsForAssign, selectDefendantForAssign,
    submitBulkAssign, bulkVoid,
  };
})();
