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

  // ── Open Modal ──
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
    body.innerHTML = '<div style="text-align:center;padding:24px;color:var(--muted)"><div class="btn-spinner" style="width:20px;height:20px;border-width:3px;margin:0 auto 8px"></div>Loading inventory...</div>';
    try {
      const r = await fetch(`${API}/api/poa/inventory`);
      _data = await r.json();
      renderSummary();
      renderMiniKpis();
    } catch (e) {
      body.innerHTML = `<div style="text-align:center;padding:24px;color:var(--red)">❌ Failed to load inventory: ${e.message}</div>`;
    }
  }

  // ── Mini KPI Bar ──
  function renderMiniKpis() {
    const el = document.getElementById('invKpis');
    if (!el) return;
    const osiTotal = _data.totals?.osi || 0;
    const palmTotal = _data.totals?.palmetto || 0;
    const total = osiTotal + palmTotal;
    const lowThreshold = 5;
    const osiLow = (_data.tiers || []).filter(t => t.surety_id === 'osi' && t.available <= lowThreshold).length;
    const palmLow = (_data.tiers || []).filter(t => t.surety_id === 'palmetto' && t.available <= lowThreshold).length;
    const totalLow = osiLow + palmLow;

    el.innerHTML = `
      <div class="inv-kpi"><span class="inv-kpi-value">${total}</span><span class="inv-kpi-label">Total Available</span></div>
      <div class="inv-kpi"><span class="inv-kpi-value" style="color:#60a5fa">${osiTotal}</span><span class="inv-kpi-label">🛡️ OSI</span></div>
      <div class="inv-kpi"><span class="inv-kpi-value" style="color:#34d399">${palmTotal}</span><span class="inv-kpi-label">🌴 Palmetto</span></div>
      <div class="inv-kpi ${totalLow > 0 ? 'inv-kpi-alert' : ''}"><span class="inv-kpi-value" style="color:${totalLow > 0 ? 'var(--red)' : 'var(--muted)'}">${totalLow}</span><span class="inv-kpi-label">⚠️ Low Tiers</span></div>
    `;
  }

  // ── Render Summary View (Tier Cards) ──
  function renderSummary() {
    const body = document.getElementById('invSummaryBody');
    const tiers = _data.tiers || [];
    const LOW = 5;

    // Group by surety
    const osi = tiers.filter(t => t.surety_id === 'osi');
    const palm = tiers.filter(t => t.surety_id === 'palmetto');

    const renderTierCards = (items, surety, icon) => {
      if (items.length === 0) return `<div style="color:var(--muted);font-size:13px;padding:16px">No inventory data for ${surety}</div>`;
      return items.map(t => {
        const pct = Math.min(100, (t.available / Math.max(1, t.available + 5)) * 100);
        const isLow = t.available <= LOW;
        const isCritical = t.available <= 2;
        const barColor = isCritical ? 'var(--red)' : isLow ? 'var(--gold)' : 'var(--accent)';
        const maxBondFmt = t.max_bond_value >= 1000 ? `$${(t.max_bond_value / 1000).toFixed(0)}K` : `$${t.max_bond_value}`;
        return `
          <div class="inv-tier-card ${isLow ? 'inv-tier-low' : ''} ${isCritical ? 'inv-tier-critical' : ''}">
            <div class="inv-tier-header">
              <span class="inv-tier-prefix">${t.poa_prefix}</span>
              <span class="inv-tier-cap">≤ ${maxBondFmt}</span>
            </div>
            <div class="inv-tier-count">${t.available}</div>
            <div class="inv-tier-bar"><div class="inv-tier-fill" style="width:${pct}%;background:${barColor}"></div></div>
            <div class="inv-tier-meta">
              ${isLow ? `<span class="inv-low-badge">${isCritical ? '🔴 CRITICAL' : '🟡 LOW'}</span>` : '<span style="color:var(--muted);font-size:10px">✅ OK</span>'}
              <span style="font-size:10px;color:var(--muted)">Next: ${t.next_serial || '—'}</span>
            </div>
          </div>`;
      }).join('');
    };

    body.innerHTML = `
      <div class="inv-surety-section">
        <div class="inv-surety-header">
          <span>🛡️ Old Southern Indemnity (OSI)</span>
          <span class="inv-surety-total">${_data.totals?.osi || 0} available</span>
        </div>
        <div class="inv-tier-grid">${renderTierCards(osi, 'OSI', '🛡️')}</div>
      </div>
      <div class="inv-surety-section">
        <div class="inv-surety-header">
          <span>🌴 Palmetto Surety Corporation</span>
          <span class="inv-surety-total">${_data.totals?.palmetto || 0} available</span>
        </div>
        <div class="inv-tier-grid">${renderTierCards(palm, 'Palmetto', '🌴')}</div>
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
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--muted)"><div class="btn-spinner" style="width:18px;height:18px;border-width:2px;display:inline-block;vertical-align:middle;margin-right:6px"></div>Loading powers...</td></tr>';
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
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--red)">Error: ${e.message}</td></tr>`;
    }
  }

  function renderDetailTable(d) {
    const tbody = document.getElementById('invDetailBody');
    const powers = d.powers || [];
    const total = d.total || 0;
    const pages = d.pages || 1;

    if (powers.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--muted)">No powers found matching filters</td></tr>';
    } else {
      tbody.innerHTML = powers.map(p => {
        const statusCls = p.status === 'available' ? 'inv-st-available' : p.status === 'assigned' ? 'inv-st-assigned' : p.status === 'voided' ? 'inv-st-voided' : 'inv-st-other';
        const maxBondFmt = p.max_bond_value >= 1000 ? `$${(p.max_bond_value / 1000).toFixed(0)}K` : `$${p.max_bond_value || 0}`;
        const actions = [];
        if (p.status === 'available') {
          actions.push(`<button class="btn-xs inv-action-btn" onclick="SLInventory.openAssignDialog('${p.poa_number}','${p.surety_id}','${p.poa_prefix}')" title="Assign to defendant">📌 Assign</button>`);
          actions.push(`<button class="btn-xs inv-action-btn inv-void-btn" onclick="SLInventory.voidPower('${p.poa_number}','${p.surety_id}')" title="Void this power">🗑️ Void</button>`);
        } else if (p.status === 'assigned') {
          actions.push(`<button class="btn-xs inv-action-btn" onclick="SLInventory.reassignPower('${p.poa_number}','${p.surety_id}')" title="Reassign or release">🔄 Reassign</button>`);
          actions.push(`<button class="btn-xs inv-action-btn inv-void-btn" onclick="SLInventory.voidPower('${p.poa_number}','${p.surety_id}')" title="Void this power">🗑️ Void</button>`);
        } else if (p.status === 'voided') {
          actions.push(`<button class="btn-xs inv-action-btn" onclick="SLInventory.restorePower('${p.poa_number}','${p.surety_id}')" title="Restore to available">♻️ Restore</button>`);
        }
        return `<tr class="inv-row">
          <td><span class="mono">${p.poa_full || p.poa_number}</span></td>
          <td>${p.surety_id === 'osi' ? '🛡️ OSI' : '🌴 Palm'}</td>
          <td>${p.poa_prefix}</td>
          <td>${maxBondFmt}</td>
          <td><span class="inv-status-pill ${statusCls}">${p.status}</span></td>
          <td style="font-size:11px;color:var(--muted)">${p.bond_case_id || '—'}</td>
          <td style="white-space:nowrap">${actions.join(' ')}</td>
        </tr>`;
      }).join('');
    }

    // Pagination
    document.getElementById('invDetailPagination').innerHTML = `
      <button class="pagination-btn" ${_detailPage <= 1 ? 'disabled' : ''} onclick="SLInventory.detailPage(${_detailPage - 1})">← Prev</button>
      <span style="color:var(--muted);font-size:12px">${total.toLocaleString()} powers · Page ${_detailPage}/${pages}</span>
      <button class="pagination-btn" ${_detailPage >= pages ? 'disabled' : ''} onclick="SLInventory.detailPage(${_detailPage + 1})">Next →</button>
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
      statusEl.innerHTML = '<span style="color:var(--red)">❌ Fill in all required fields</span>';
      return;
    }

    statusEl.innerHTML = '<div class="btn-spinner" style="width:14px;height:14px;display:inline-block;vertical-align:middle;margin-right:6px"></div>Adding powers...';
    try {
      const r = await fetch(`${API}/api/poa/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ surety_id: surety, poa_prefix: prefix, start: startNum, end: endNum, max_bond_value: maxBond, expiration: exp }),
      });
      const d = await r.json();
      if (d.error) {
        statusEl.innerHTML = `<span style="color:var(--red)">❌ ${d.error}</span>`;
      } else {
        statusEl.innerHTML = `<span style="color:var(--accent)">✅ Added ${d.count || 1} power(s)</span>`;
        toast('success', `Added ${d.count || 1} POA(s) to ${surety.toUpperCase()}`);
        loadSummary();
        setTimeout(() => { document.getElementById('invAddFormArea').style.display = 'none'; statusEl.innerHTML = ''; }, 2000);
      }
    } catch (e) {
      statusEl.innerHTML = `<span style="color:var(--red)">❌ ${e.message}</span>`;
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
      if (d.error) {
        toast('error', d.error);
      } else {
        toast('success', `POA ${d.poa_full} assigned to ${bookingNumber}`);
        loadDetailView();
        loadSummary();
      }
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

  // ── Reassign (Release back to available or assign to different case) ──
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

  // ── Toast helper (uses existing SL toast system) ──
  function toast(type, msg) {
    if (window.SL && SL.showToast) { SL.showToast(type, msg); return; }
    const t = document.getElementById('toast');
    if (!t) return;
    t.className = `toast-notification toast-${type} show`;
    t.querySelector('.toast-icon').textContent = type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️';
    t.querySelector('.toast-message').textContent = msg;
    setTimeout(() => t.classList.remove('show'), 4000);
  }

  // ── Update prefix options when surety changes in Add form ──
  function updatePrefixOptions() {
    const surety = document.getElementById('addSurety').value;
    const select = document.getElementById('addPrefix');
    const prefixes = surety === 'osi'
      ? [{ v: 'OSI3', l: 'OSI3 (≤$3K)' }, { v: 'OSI6', l: 'OSI6 (≤$6K)' }, { v: 'OSI16', l: 'OSI16 (≤$16K)' }, { v: 'OSI51', l: 'OSI51 (≤$51K)' }, { v: 'OSI101', l: 'OSI101 (≤$101K)' }, { v: 'OSI251', l: 'OSI251 (≤$251K)' }]
      : [{ v: 'PSC5', l: 'PSC5 (≤$5K)' }, { v: 'PSC15', l: 'PSC15 (≤$15K)' }, { v: 'PSC25', l: 'PSC25 (≤$25K)' }, { v: 'PSC50', l: 'PSC50 (≤$50K)' }, { v: 'PSC75', l: 'PSC75 (≤$75K)' }, { v: 'PSC105', l: 'PSC105 (≤$105K)' }];
    select.innerHTML = prefixes.map(p => `<option value="${p.v}">${p.l}</option>`).join('');
    // Auto-fill max bond
    autoFillMaxBond();
  }

  function autoFillMaxBond() {
    const prefix = document.getElementById('addPrefix').value;
    const bondMap = { OSI3: 3000, OSI6: 6000, OSI16: 16000, OSI51: 51000, OSI101: 101000, OSI251: 251000, PSC5: 5000, PSC15: 15000, PSC25: 25000, PSC50: 50000, PSC75: 75000, PSC105: 105000 };
    document.getElementById('addMaxBond').value = bondMap[prefix] || 0;
  }

  return {
    open, close, switchTab, loadSummary, loadDetailView,
    applyFilter, searchFilter, detailPage,
    showAddForm, submitAdd, updatePrefixOptions, autoFillMaxBond,
    openAssignDialog, voidPower, reassignPower, restorePower,
  };
})();
