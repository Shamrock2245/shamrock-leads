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
    tbody.innerHTML = '<tr><td colspan="7"><div class="inv-loading"><div class="btn-spinner"></div><span>Loading powers…</span></div></td></tr>';
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
      tbody.innerHTML = `<tr><td colspan="7"><div class="inv-error">Error: ${e.message}</div></td></tr>`;
    }
  }

  function renderDetailTable(d) {
    const tbody = document.getElementById('invDetailBody');
    const powers = d.powers || [];
    const total = d.total || 0;
    const pages = d.pages || 1;

    if (powers.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="inv-empty-state">No powers found matching filters</td></tr>';
    } else {
      tbody.innerHTML = powers.map(p => {
        const statusCls = p.status === 'available' ? 'inv-st-available' : p.status === 'assigned' ? 'inv-st-assigned' : p.status === 'voided' ? 'inv-st-voided' : 'inv-st-other';
        const maxBondFmt = p.max_bond_value >= 1000 ? `$${(p.max_bond_value / 1000).toFixed(0)}K` : `$${p.max_bond_value || 0}`;
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
        return `<tr class="inv-row">
          <td><span class="inv-poa-mono">${p.poa_full || p.poa_number}</span></td>
          <td><span class="inv-surety-chip ${p.surety_id === 'osi' ? 'inv-chip-osi' : 'inv-chip-palm'}">${p.surety_id === 'osi' ? '🛡️ OSI' : '🌴 PSC'}</span></td>
          <td class="inv-cell-tier">${p.poa_prefix}</td>
          <td class="inv-cell-bond">${maxBondFmt}</td>
          <td><span class="inv-status-pill ${statusCls}">${p.status}</span></td>
          <td class="inv-cell-case">${p.bond_case_id || '—'}</td>
          <td class="inv-cell-actions">${actions.join('')}</td>
        </tr>`;
      }).join('');
    }

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

  // ── Assign Power to Defendant ──
  function openAssignDialog(poaNum, surety, prefix) {
    const booking = prompt(`Assign POA ${prefix} ${poaNum} to which Booking Number?`);
    if (!booking || !booking.trim()) return;
    assignPower(poaNum, surety, prefix, booking.trim());
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

  return {
    open, close, switchTab, loadSummary, loadDetailView,
    applyFilter, searchFilter, detailPage,
    showAddForm, submitAdd, updatePrefixOptions, autoFillMaxBond,
    openAssignDialog, voidPower, reassignPower, restorePower,
    handleUpload, handleDrop, confirmUploadedPOAs,
    checkLowStockBanner: _checkLowStockBanner,
  };
})();
