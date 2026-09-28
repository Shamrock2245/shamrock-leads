/* ShamrockLeads — Accounts Receivable. One row per bond. Balance is computed. */
(function (w) {
  'use strict';

  const API = () => (w.API || '');
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  const money = (dollars) => (dollars == null || dollars === '' ? '—' : ('$' + dollars));
  const toast = (msg, type) => {
    if (w.SL && w.SL.toast) w.SL.toast(msg, type);
    else if (w.showToast) w.showToast(msg, type);
  };

  let _filter = 'all';
  let _sort = 'overdue';
  let _q = '';
  let _rows = [];
  let _openBooking = '';
  let _detail = null;
  let _draft = null;

  function paintBoard(model) {
    const root = $('arRoot');
    if (!root) return;
    const rows = model.rows || [];
    root.innerHTML = `
      <div class="ar-head">
        <div>
          <p class="ar-kicker">Fort Myers · Premium ledger</p>
          <h2>Accounts Receivable</h2>
          <p>One row per bond. Balance is premium minus the down payment from Write Bond minus later payments. It is not a number anyone types.</p>
        </div>
      </div>
      <div class="ar-kpis">
        <div class="ar-kpi due"><span>Open balance</span><strong>${esc(money(model.open_balance_dollars || '0.00'))}</strong></div>
        <div class="ar-kpi late"><span>Overdue</span><strong>${esc(model.overdue_count || 0)}</strong></div>
        <div class="ar-kpi paid"><span>Paid in full</span><strong>${esc(model.paid_in_full_count || 0)}</strong></div>
      </div>
      <div class="ar-tools">
        <button type="button" class="ar-chip ${_filter === 'all' ? 'active' : ''}" data-ar-filter="all">All</button>
        <button type="button" class="ar-chip ${_filter === 'overdue' ? 'active' : ''}" data-ar-filter="overdue">Overdue</button>
        <button type="button" class="ar-chip ${_filter === 'open' ? 'active' : ''}" data-ar-filter="open">Balance due</button>
        <button type="button" class="ar-chip ${_filter === 'paid' ? 'active' : ''}" data-ar-filter="paid">Paid in full</button>
        <input id="arSearch" type="search" placeholder="Name, case, or power" value="${esc(_q)}" aria-label="Search bonds">
        <select id="arSort" aria-label="Sort">
          <option value="overdue" ${_sort === 'overdue' ? 'selected' : ''}>Sort: overdue</option>
          <option value="balance" ${_sort === 'balance' ? 'selected' : ''}>Sort: balance</option>
          <option value="next_due" ${_sort === 'next_due' ? 'selected' : ''}>Sort: next due</option>
          <option value="name" ${_sort === 'name' ? 'selected' : ''}>Sort: name</option>
          <option value="last_payment" ${_sort === 'last_payment' ? 'selected' : ''}>Sort: last payment</option>
        </select>
      </div>
      <div class="ar-table-wrap">
        <table class="ar-table">
          <thead>
            <tr>
              <th>Defendant / indemnitor</th>
              <th>Case / power</th>
              <th>Premium</th>
              <th>Down payment</th>
              <th>Balance</th>
              <th>Next due</th>
              <th>Overdue</th>
              <th>Last payment</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${rows.length ? rows.map(rowHtml).join('') : '<tr><td colspan="9" class="ar-empty">No bonds match this view.</td></tr>'}
          </tbody>
        </table>
      </div>
      <div id="arDetail"></div>`;
    root.querySelectorAll('[data-ar-filter]').forEach((btn) => {
      btn.addEventListener('click', () => { _filter = btn.getAttribute('data-ar-filter'); load(); });
    });
    const search = $('arSearch');
    if (search) search.addEventListener('change', () => { _q = search.value; load(); });
    const sort = $('arSort');
    if (sort) sort.addEventListener('change', () => { _sort = sort.value; load(); });
    root.querySelectorAll('[data-ar-open]').forEach((tr) => {
      tr.addEventListener('click', () => openBond(tr.getAttribute('data-ar-open')));
    });
    root.querySelectorAll('[data-ar-remind]').forEach((btn) => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        openBond(btn.getAttribute('data-ar-remind'), true);
      });
    });
    if (_detail) paintDetail();
  }

  function statusPill(row) {
    if (row.ar_status === 'paid_in_full') return '<span class="ar-pill paid">Paid</span>';
    if (row.ar_status === 'overdue') return `<span class="ar-pill overdue">${esc(row.days_overdue)}d overdue</span>`;
    if (row.ar_status === 'premium_not_entered') return '<span class="ar-pill missing">Premium not entered</span>';
    if ((row.balance_due_cents || 0) > 0) return '<span class="ar-pill open">Open</span>';
    return '';
  }

  function rowHtml(row) {
    const balClass = (row.balance_due_cents || 0) > 0 ? 'due' : 'zero';
    const last = row.last_payment_at ? String(row.last_payment_at).slice(0, 10) : '—';
    return `<tr class="ar-row" data-ar-open="${esc(row.booking_number)}">
      <td><div class="ar-name">${esc(row.defendant_name || '—')}</div><div class="ar-sub">${esc(row.indemnitor_name || 'No indemnitor on file')}</div></td>
      <td><div>${esc(row.case_number || '—')}</div><div class="ar-sub">${esc(row.poa_number || 'No power')}</div></td>
      <td class="ar-money">${esc(money(row.premium_dollars))}</td>
      <td class="ar-money">${row.down_payment_entered ? esc(money(row.down_payment_dollars)) : '<span class="ar-pill missing">Not entered</span>'}</td>
      <td class="ar-money ${balClass}">${esc(money(row.balance_due_dollars))}</td>
      <td>${esc(row.next_payment_due || '—')}</td>
      <td>${statusPill(row)}</td>
      <td>${esc(last)}</td>
      <td><button type="button" class="ar-remind" data-ar-remind="${esc(row.booking_number)}">Remind</button></td>
    </tr>`;
  }

  function paintDetail() {
    const host = $('arDetail');
    if (!host || !_detail) return;
    const bond = _detail;
    const payments = bond.payments || [];
    const downWhen = String(bond.down_payment_entered_at || '').slice(0, 10) || '—';
    const downRow = bond.down_payment_entered ? `<tr>
      <td>${esc(downWhen)}</td>
      <td>Down payment</td>
      <td class="ar-money">${esc(money(bond.down_payment_dollars))}</td>
      <td>${esc(bond.down_payment_method || (bond.down_payment_source === 'field' ? 'Write Bond' : 'Recorded with the bond'))}</td>
      <td>${esc(bond.down_payment_reference || '—')}</td>
      <td>${esc(bond.down_payment_entered_by || '—')}</td>
    </tr>` : '';
    const payRows = payments.map((p) => `<tr>
      <td>${esc((p.timestamp || '').slice(0, 10) || '—')}</td>
      <td>${esc(p.kind || 'payment')}${p.swipesimple ? ' · SwipeSimple' : ''}</td>
      <td class="ar-money">${esc(money(centsToDollars(p.amount_cents)))}</td>
      <td>${esc(p.method || '—')}</td>
      <td>${esc(p.reference || '—')}</td>
      <td>${esc(p.entered_by || '—')}</td>
    </tr>`).join('');
    const history = (bond.history || []).filter((h) => h.event && h.event.indexOf('reminder') === 0 || h.event === 'call_prepared').map((h) =>
      `<li>${esc((h.at || h.sent_at || '').slice(0, 16))} · ${esc(h.status || h.event)} · ${esc(h.channel || '')} · ${esc(h.approved_by || h.drafted_by || '')}</li>`
    ).join('');
    host.innerHTML = `
      <section class="ar-detail" id="arDetailCard">
        <h3>${esc(bond.defendant_name || 'Bond')} <span class="ar-sub">${esc(bond.indemnitor_name ? '· ' + bond.indemnitor_name : '')}</span></h3>
        <p class="ar-note">${esc(bond.case_number || 'No case #')} · Power ${esc(bond.poa_number || '—')} · Booking ${esc(bond.booking_number)}</p>
        <div class="ar-split">
          <div>
            <h4 style="margin:12px 0 6px;font-size:13px">Payment history</h4>
            <table class="ar-ledger">
              <thead><tr><th>Date</th><th>Kind</th><th>Amount</th><th>Method</th><th>Reference</th><th>Entered by</th></tr></thead>
              <tbody>
                ${downRow}
                ${payRows || (!bond.down_payment_entered ? '<tr><td colspan="6" class="ar-empty">No payments on the ledger yet.</td></tr>' : '')}
              </tbody>
            </table>
            <p class="ar-note" style="margin-top:8px">Balance ${esc(money(bond.balance_due_dollars))} = premium ${esc(money(bond.premium_dollars))} − down payment − later payments. SwipeSimple rows already attributed to this booking are included. Unmatched SwipeSimple imports stay in Accounting until a booking number is on the row.</p>
            ${history ? `<h4 style="margin:14px 0 6px;font-size:13px">Reminder history</h4><ul class="ar-note">${history}</ul>` : ''}
          </div>
          <div>
            <form class="ar-form" id="arPayForm">
              <strong>Log a later payment</strong>
              <label for="arPayAmount">Amount</label>
              <input id="arPayAmount" type="number" min="0.01" step="0.01" required>
              <label for="arPayMethod">Method</label>
              <select id="arPayMethod"><option value="cash">Cash</option><option value="check">Check</option><option value="swipesimple">SwipeSimple</option></select>
              <label for="arPayRef">Reference</label>
              <input id="arPayRef" type="text" placeholder="Check # or SwipeSimple transaction id">
              <label for="arPayDate">Date</label>
              <input id="arPayDate" type="date">
              <div class="ar-actions"><button class="ar-btn primary" type="submit">Save payment</button></div>
            </form>
            <form class="ar-form" id="arRemindForm" style="margin-top:16px">
              <strong>Remind</strong>
              <p class="ar-note">A draft stays here until a staff member clicks Send. Nothing is scheduled. Texts use BlueBubbles on (239) 955-0178. Calls are a Shannon brief — Twilio is voice only, and this screen does not text through Twilio.</p>
              <label for="arChannel">Channel</label>
              <select id="arChannel"><option value="text">Text</option><option value="call">Shannon call</option></select>
              <label for="arRole">Who</label>
              <select id="arRole"><option value="indemnitor">Indemnitor</option><option value="defendant">Defendant</option></select>
              <div class="ar-actions"><button class="ar-btn" type="button" id="arDraftBtn">Draft reminder</button></div>
              <div id="arDraftBox"></div>
            </form>
          </div>
        </div>
      </section>`;
    $('arPayForm').addEventListener('submit', savePayment);
    $('arDraftBtn').addEventListener('click', draftReminder);
    if (_draft) paintDraft();
    host.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function centsToDollars(cents) {
    if (cents == null || cents === '') return null;
    const n = Number(cents);
    const sign = n < 0 ? '-' : '';
    const abs = Math.abs(n);
    return sign + Math.floor(abs / 100) + '.' + String(abs % 100).padStart(2, '0');
  }

  function paintDraft() {
    const box = $('arDraftBox');
    if (!box || !_draft) return;
    const d = _draft;
    box.innerHTML = `
      <p class="ar-tone">Tone: ${esc(d.tone || '')} · ${esc(d.provider || '')} · ${esc(d.recipient_phone_masked || '')}</p>
      <label for="arFinalText">Preview — edit before sending</label>
      <textarea id="arFinalText">${esc(d.draft_text || '')}</textarea>
      ${(d.prohibited_hits || []).length ? `<p class="ar-banner">Blocked wording: ${esc(d.prohibited_hits.join(', '))}. Edit it out before Send.</p>` : ''}
      <div class="ar-actions">
        <button type="button" class="ar-btn primary" id="arSendBtn">${d.channel === 'call' ? 'Approve call brief' : 'Send text'}</button>
      </div>
      <p class="ar-note">Send is staff approval. Quiet hours are 8:00 AM–9:00 PM Eastern. Opt-outs and third parties are blocked in code.</p>`;
    $('arSendBtn').addEventListener('click', sendReminder);
  }

  async function load() {
    const root = $('arRoot');
    if (root && !root.dataset.ready) {
      root.innerHTML = '<p class="ar-note">Loading accounts receivable…</p>';
    }
    try {
      const params = new URLSearchParams({ filter: _filter, sort: _sort, q: _q });
      const res = await fetch(`${API()}/api/ar/bonds?${params}`, { credentials: 'same-origin' });
      if (res.status === 401) {
        if (root) root.innerHTML = '<p class="ar-banner">Sign in to view accounts receivable. This screen is staff only.</p>';
        return;
      }
      const data = await res.json();
      _rows = data.rows || [];
      paintBoard(data);
      if (root) root.dataset.ready = '1';
    } catch (err) {
      if (root) root.innerHTML = `<p class="ar-banner">Could not load accounts receivable.</p>`;
    }
  }

  async function openBond(booking, remind) {
    _openBooking = booking;
    const res = await fetch(`${API()}/api/ar/bonds/${encodeURIComponent(booking)}`, { credentials: 'same-origin' });
    const data = await res.json();
    if (!res.ok) { toast(data.error || 'Bond not found', 'error'); return; }
    _detail = data.bond;
    if (!remind) _draft = null;
    paintDetail();
    if (remind) {
      const btn = $('arDraftBtn');
      if (btn) btn.focus();
    }
  }

  async function savePayment(ev) {
    ev.preventDefault();
    const body = {
      amount: $('arPayAmount').value,
      method: $('arPayMethod').value,
      reference: $('arPayRef').value,
      paid_at: $('arPayDate').value,
    };
    const res = await fetch(`${API()}/api/ar/bonds/${encodeURIComponent(_openBooking)}/payments`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) { toast(data.error || 'Payment not saved', 'error'); return; }
    toast('Payment logged', 'success');
    _detail = data.bond;
    paintDetail();
    load();
  }

  async function draftReminder() {
    const res = await fetch(`${API()}/api/ar/bonds/${encodeURIComponent(_openBooking)}/reminders/draft`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel: $('arChannel').value, recipient_role: $('arRole').value }),
    });
    const data = await res.json();
    if (!res.ok) { toast(data.error || 'No draft', 'error'); return; }
    _draft = data.draft;
    paintDraft();
  }

  async function sendReminder() {
    if (!_draft) return;
    const finalText = $('arFinalText').value;
    const res = await fetch(`${API()}/api/ar/bonds/${encodeURIComponent(_openBooking)}/reminders/send`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ draft_id: _draft.draft_id, final_text: finalText }),
    });
    const data = await res.json();
    if (!res.ok) {
      const why = (data.reasons || [data.error]).join(', ');
      toast(why, 'error');
      return;
    }
    toast(_draft.channel === 'call' ? 'Call brief saved for Shannon' : 'Reminder sent', 'success');
    _draft = null;
    await openBond(_openBooking, false);
  }

  /** Paint a board from an in-memory payload. Used by the staff preview screenshot only. */
  function paintPreview(model) {
    _filter = model.filter || 'all';
    _sort = model.sort || 'overdue';
    _rows = model.rows || [];
    _detail = model.detail || null;
    _draft = model.draft || null;
    paintBoard(model);
  }

  w.SLAr = { load, paintPreview, openBond };
})(window);
