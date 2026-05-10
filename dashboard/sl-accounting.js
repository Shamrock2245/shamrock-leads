/* ═══════════════════════════════════════════════════════════════
   ShamrockLeads — Accounting & Revenue Intelligence
   SwipeSimple import, cash ledger, case attribution, exports
   ═══════════════════════════════════════════════════════════════ */

const SLAccounting = (() => {
  const API = window.API || '';
  const $ = id => document.getElementById(id);
  const money = n => '$' + (parseFloat(n)||0).toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0});
  const toast = (msg,type) => { if(window.SL?.toast) SL.toast(msg,type); else if(window.showToast) showToast(msg,type); else alert(msg); };

  let _transactions = [];
  let _page = 0;
  let _total = 0;
  let _filters = {};

  // ── Load Dashboard KPIs ──────────────────────────────────────────────────
  async function loadDashboard() {
    try {
      const r = await fetch(`${API}/api/accounting/dashboard`);
      const d = await r.json();

      setKpi('acctKpiMTD', money(d.mtd?.total || 0));
      setKpi('acctKpiYTD', money(d.ytd?.total || 0));
      setKpi('acctKpiOutstanding', money(d.outstanding?.total || 0));
      setKpi('acctKpiTxnCount', d.ytd?.count || 0);

      // Method breakdown
      const methEl = $('acctMethodBreakdown');
      if (methEl && d.by_method) {
        const methods = Object.entries(d.by_method);
        methEl.innerHTML = methods.map(([m, v]) =>
          `<div class="acct-method-row">
            <span class="acct-method-label">${methodIcon(m)} ${m}</span>
            <span class="acct-method-value">${money(v.total)} <small>(${v.count})</small></span>
          </div>`
        ).join('') || '<div style="color:var(--muted);font-size:12px">No transactions yet</div>';
      }

      // Surety breakdown
      const surEl = $('acctSuretyBreakdown');
      if (surEl && d.by_surety) {
        const sureties = Object.entries(d.by_surety);
        surEl.innerHTML = sureties.map(([s, v]) =>
          `<div class="acct-method-row">
            <span class="acct-method-label">${s || 'Unassigned'}</span>
            <span class="acct-method-value">${money(v.total)} <small>(${v.count})</small></span>
          </div>`
        ).join('') || '<div style="color:var(--muted);font-size:12px">—</div>';
      }

      // Load chart
      loadRevenueChart();

    } catch (e) {
      console.error('Accounting dashboard error:', e);
    }
  }

  function setKpi(id, val) {
    const el = $(id);
    if (el) el.textContent = val;
  }

  function methodIcon(m) {
    const icons = { card: '💳', cash: '💵', check: '🧾', wire: '🏦', swipesimple: '💳', other: '📝' };
    return icons[m?.toLowerCase()] || '💰';
  }

  // ── Revenue Chart (simple bar chart) ─────────────────────────────────────
  async function loadRevenueChart() {
    const canvas = $('acctRevenueChart');
    if (!canvas) return;

    try {
      const r = await fetch(`${API}/api/accounting/revenue/monthly?months=12`);
      const d = await r.json();
      const data = d.monthly || [];
      if (!data.length) return;

      const ctx = canvas.getContext('2d');
      const W = canvas.width = canvas.parentElement.offsetWidth;
      const H = canvas.height = 200;
      const pad = { t: 20, r: 20, b: 40, l: 60 };
      const plotW = W - pad.l - pad.r;
      const plotH = H - pad.t - pad.b;
      const maxVal = Math.max(...data.map(d => d.total), 1);
      const barW = Math.max(plotW / data.length - 4, 8);

      ctx.clearRect(0, 0, W, H);

      // Grid lines
      ctx.strokeStyle = 'rgba(255,255,255,0.05)';
      ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) {
        const y = pad.t + (plotH / 4) * i;
        ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
        ctx.fillStyle = 'rgba(255,255,255,0.3)';
        ctx.font = '10px Inter, sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(money(maxVal - (maxVal / 4) * i), pad.l - 8, y + 4);
      }

      // Bars
      data.forEach((item, i) => {
        const x = pad.l + (plotW / data.length) * i + 2;
        const barH = (item.total / maxVal) * plotH;
        const y = pad.t + plotH - barH;

        const gradient = ctx.createLinearGradient(x, y, x, y + barH);
        gradient.addColorStop(0, 'rgba(16,185,129,0.8)');
        gradient.addColorStop(1, 'rgba(16,185,129,0.3)');
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.roundRect(x, y, barW, barH, [4, 4, 0, 0]);
        ctx.fill();

        // Month label
        ctx.fillStyle = 'rgba(255,255,255,0.4)';
        ctx.font = '10px Inter, sans-serif';
        ctx.textAlign = 'center';
        const label = item.month.substring(5); // "MM"
        ctx.fillText(label, x + barW / 2, H - pad.b + 16);
      });
    } catch (e) {
      console.warn('Revenue chart error:', e);
    }
  }

  // ── Load Transactions Table ──────────────────────────────────────────────
  async function loadTransactions(page = 0) {
    _page = page;
    const params = new URLSearchParams({ page, limit: 50 });
    if (_filters.method) params.set('method', _filters.method);
    if (_filters.status) params.set('status', _filters.status);
    if (_filters.search) params.set('search', _filters.search);
    if (_filters.date_from) params.set('date_from', _filters.date_from);
    if (_filters.date_to) params.set('date_to', _filters.date_to);
    if (_filters.unattributed) params.set('unattributed', 'true');

    try {
      const r = await fetch(`${API}/api/accounting/transactions?${params}`);
      const d = await r.json();
      _transactions = d.transactions || [];
      _total = d.total || 0;
      renderTransactions();
    } catch (e) {
      console.error('Load transactions error:', e);
    }
  }

  function renderTransactions() {
    const tbody = $('acctTxnBody');
    if (!tbody) return;

    if (!_transactions.length) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:40px;color:var(--muted)">No transactions found. Import from SwipeSimple or record a cash payment.</td></tr>';
      return;
    }

    tbody.innerHTML = _transactions.map(t => {
      const statusClass = t.status === 'completed' || t.status === 'settled' ? 'acct-status-ok' : t.status === 'voided' ? 'acct-status-void' : 'acct-status-pending';
      const attributed = t.booking_number ? true : false;
      return `<tr class="${t.status === 'voided' ? 'acct-voided' : ''}">
        <td style="font-size:12px;color:var(--muted)">${(t.timestamp || '').substring(0,10)}</td>
        <td><span class="acct-txn-id">${t.transaction_id || '—'}</span></td>
        <td style="font-weight:600;color:var(--accent)">${money(t.amount)}</td>
        <td>${methodIcon(t.method)} ${t.method || '—'}</td>
        <td><span class="acct-status ${statusClass}">${t.status || '—'}</span></td>
        <td>${t.defendant_name || '<span style="color:var(--muted);font-style:italic">—</span>'}</td>
        <td>${attributed
          ? '<span style="color:var(--accent)">✓ ' + t.booking_number + '</span>'
          : '<button class="acct-attr-btn" onclick="SLAccounting.openAttribution(\'' + t.transaction_id + '\')">Link to Case</button>'
        }</td>
        <td>
          ${t.status !== 'voided' ? '<button class="acct-void-btn" onclick="SLAccounting.voidTransaction(\'' + t.transaction_id + '\')" title="Void">✕</button>' : ''}
        </td>
      </tr>`;
    }).join('');

    // Pagination
    const pagEl = $('acctPagination');
    if (pagEl) {
      const totalPages = Math.ceil(_total / 50);
      pagEl.innerHTML = `
        <span style="color:var(--muted);font-size:12px">${_total} transaction${_total !== 1 ? 's' : ''}</span>
        <div style="display:flex;gap:6px">
          ${_page > 0 ? '<button class="acct-page-btn" onclick="SLAccounting.loadTransactions(' + (_page - 1) + ')">← Prev</button>' : ''}
          <span style="color:var(--text-secondary);font-size:12px;padding:4px 8px">Page ${_page + 1} of ${totalPages || 1}</span>
          ${_page < totalPages - 1 ? '<button class="acct-page-btn" onclick="SLAccounting.loadTransactions(' + (_page + 1) + ')">Next →</button>' : ''}
        </div>
      `;
    }
  }

  // ── Filters ──────────────────────────────────────────────────────────────
  function applyFilters() {
    _filters.method = $('acctFilterMethod')?.value || '';
    _filters.status = $('acctFilterStatus')?.value || '';
    _filters.search = $('acctSearch')?.value || '';
    _filters.date_from = $('acctDateFrom')?.value || '';
    _filters.date_to = $('acctDateTo')?.value || '';
    _filters.unattributed = $('acctFilterUnattributed')?.checked || false;
    loadTransactions(0);
  }

  function clearFilters() {
    ['acctFilterMethod', 'acctFilterStatus', 'acctSearch', 'acctDateFrom', 'acctDateTo'].forEach(id => { const el = $(id); if (el) el.value = ''; });
    const ua = $('acctFilterUnattributed'); if (ua) ua.checked = false;
    _filters = {};
    loadTransactions(0);
  }

  // ── Record Cash / Manual Payment ─────────────────────────────────────────
  function openRecordModal() {
    const modal = $('acctRecordModal');
    if (!modal) return;
    // Reset fields
    ['acctRecAmount', 'acctRecDefendant', 'acctRecBooking', 'acctRecPOA', 'acctRecCase', 'acctRecIndemnitor', 'acctRecDesc', 'acctRecRef'].forEach(id => {
      const el = $(id); if (el) el.value = '';
    });
    if ($('acctRecMethod')) $('acctRecMethod').value = 'cash';
    if ($('acctRecType')) $('acctRecType').value = 'premium';
    if ($('acctRecSurety')) $('acctRecSurety').value = 'osi';
    modal.style.display = 'flex';
  }

  function closeRecordModal() {
    const modal = $('acctRecordModal'); if (modal) modal.style.display = 'none';
  }

  async function submitRecord() {
    const amount = parseFloat($('acctRecAmount')?.value || 0);
    if (amount <= 0) { toast('Amount must be > 0', 'error'); return; }

    const payload = {
      amount,
      method: $('acctRecMethod')?.value || 'cash',
      type: $('acctRecType')?.value || 'premium',
      surety: $('acctRecSurety')?.value || '',
      booking_number: $('acctRecBooking')?.value || '',
      defendant_name: $('acctRecDefendant')?.value || '',
      poa_number: $('acctRecPOA')?.value || '',
      case_number: $('acctRecCase')?.value || '',
      indemnitor_name: $('acctRecIndemnitor')?.value || '',
      description: $('acctRecDesc')?.value || '',
      reference_id: $('acctRecRef')?.value || '',
    };

    try {
      const r = await fetch(`${API}/api/accounting/transactions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const d = await r.json();
      if (d.success) {
        toast('💵 Transaction recorded: ' + money(amount), 'success');
        closeRecordModal();
        load();
      } else {
        toast(d.error || 'Failed', 'error');
      }
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  // ── SwipeSimple CSV Import ───────────────────────────────────────────────
  function openImportModal() {
    const modal = $('acctImportModal');
    if (!modal) return;
    $('acctImportStatus').innerHTML = '';
    $('acctImportFile').value = '';
    modal.style.display = 'flex';
  }

  function closeImportModal() {
    const modal = $('acctImportModal'); if (modal) modal.style.display = 'none';
  }

  async function submitImport() {
    const fileInput = $('acctImportFile');
    if (!fileInput?.files?.length) { toast('Select a CSV file', 'error'); return; }

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);

    const statusEl = $('acctImportStatus');
    if (statusEl) statusEl.innerHTML = '<span style="color:var(--accent)">⏳ Importing...</span>';

    try {
      const r = await fetch(`${API}/api/accounting/import/swipesimple`, { method: 'POST', body: formData });
      const d = await r.json();
      if (d.success) {
        if (statusEl) statusEl.innerHTML = `<span style="color:var(--accent)">✅ Imported ${d.imported} transactions (${d.skipped} skipped, ${d.errors} errors)</span>`;
        toast(`💳 SwipeSimple: ${d.imported} imported`, 'success');
        setTimeout(() => { closeImportModal(); load(); }, 2000);
      } else {
        if (statusEl) statusEl.innerHTML = `<span style="color:var(--red)">❌ ${d.error}</span>`;
      }
    } catch (e) {
      if (statusEl) statusEl.innerHTML = `<span style="color:var(--red)">❌ ${e.message}</span>`;
    }
  }

  // ── Attribution Modal ────────────────────────────────────────────────────
  let _attributeTxnId = null;

  function openAttribution(txnId) {
    _attributeTxnId = txnId;
    const modal = $('acctAttributeModal');
    if (!modal) return;
    ['acctAttrBooking', 'acctAttrDefendant', 'acctAttrPOA', 'acctAttrCase', 'acctAttrSurety', 'acctAttrIndemnitor'].forEach(id => {
      const el = $(id); if (el) el.value = '';
    });
    modal.style.display = 'flex';
  }

  function closeAttribution() {
    const modal = $('acctAttributeModal'); if (modal) modal.style.display = 'none';
    _attributeTxnId = null;
  }

  async function submitAttribution() {
    if (!_attributeTxnId) return;
    const payload = {
      booking_number: $('acctAttrBooking')?.value || '',
      defendant_name: $('acctAttrDefendant')?.value || '',
      poa_number: $('acctAttrPOA')?.value || '',
      case_number: $('acctAttrCase')?.value || '',
      surety: $('acctAttrSurety')?.value || '',
      indemnitor_name: $('acctAttrIndemnitor')?.value || '',
    };

    try {
      const r = await fetch(`${API}/api/accounting/transactions/${encodeURIComponent(_attributeTxnId)}/attribute`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const d = await r.json();
      if (d.success) {
        toast('✅ Transaction linked to case', 'success');
        closeAttribution();
        loadTransactions(_page);
      } else { toast(d.error || 'Failed', 'error'); }
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  // ── Void Transaction ─────────────────────────────────────────────────────
  async function voidTransaction(txnId) {
    if (!confirm('Void this transaction? This cannot be undone.')) return;
    try {
      const r = await fetch(`${API}/api/accounting/transactions/${encodeURIComponent(txnId)}`, { method: 'DELETE' });
      const d = await r.json();
      if (d.success) { toast('Transaction voided', 'success'); loadTransactions(_page); }
      else toast(d.error || 'Failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  // ── Export ───────────────────────────────────────────────────────────────
  function exportQuickBooks() {
    const from = $('acctDateFrom')?.value || '';
    const to = $('acctDateTo')?.value || '';
    const params = new URLSearchParams();
    if (from) params.set('date_from', from);
    if (to) params.set('date_to', to);
    window.open(`${API}/api/accounting/export/quickbooks?${params}`, '_blank');
    toast('📥 QuickBooks export downloading...', 'success');
  }

  function exportCSV() {
    const from = $('acctDateFrom')?.value || '';
    const to = $('acctDateTo')?.value || '';
    const params = new URLSearchParams();
    if (from) params.set('date_from', from);
    if (to) params.set('date_to', to);
    window.open(`${API}/api/accounting/export/csv?${params}`, '_blank');
    toast('📥 CSV export downloading...', 'success');
  }

  // ── Premium Split Calculator ─────────────────────────────────────────────
  async function calcSplit() {
    const bondAmt = parseFloat($('acctSplitBond')?.value || 0);
    const surety = $('acctSplitSurety')?.value || 'osi';
    if (bondAmt <= 0) return;

    try {
      const r = await fetch(`${API}/api/accounting/premium-split?bond_amount=${bondAmt}&surety=${surety}`);
      const d = await r.json();
      const el = $('acctSplitResult');
      if (el) {
        el.innerHTML = `
          <div class="acct-split-grid">
            <div class="acct-split-item"><span>Premium (10%)</span><strong>${money(d.premium)}</strong></div>
            <div class="acct-split-item"><span>Surety Owed (${(d.surety_rate*100).toFixed(1)}%)</span><strong style="color:var(--red)">${money(d.surety_owed)}</strong></div>
            <div class="acct-split-item"><span>BUF (${(d.buf_rate*100).toFixed(1)}%)</span><strong style="color:var(--gold)">${money(d.buf_owed)}</strong></div>
            <div class="acct-split-item acct-split-highlight"><span>Agent Retains</span><strong style="color:var(--accent)">${money(d.agent_retains)}</strong></div>
          </div>`;
      }
    } catch (e) { console.warn('Split calc error:', e); }
  }

  // ── Master Load ──────────────────────────────────────────────────────────
  async function load() {
    await Promise.all([loadDashboard(), loadTransactions(0)]);
  }

  // ── Keyboard ─────────────────────────────────────────────────────────────
  document.addEventListener('keydown', e => {
    const tab = $('tabAccounting');
    if (!tab || tab.style.display === 'none') return;
    if (e.key === 'Escape') { closeRecordModal(); closeImportModal(); closeAttribution(); }
  });

  return {
    load, loadDashboard, loadTransactions, applyFilters, clearFilters,
    openRecordModal, closeRecordModal, submitRecord,
    openImportModal, closeImportModal, submitImport,
    openAttribution, closeAttribution, submitAttribution,
    voidTransaction, exportQuickBooks, exportCSV, calcSplit,
  };
})();
