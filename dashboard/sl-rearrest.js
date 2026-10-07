/* ═══════════════════════════════════════════════════════
   ShamrockLeads — Book Watch review (sl-rearrest.js)
   ═══════════════════════════════════════════════════════
   Polls /api/rearrest/pending for pending_review and unconfirmed_triage.
   Staff triage calls PATCH /api/rearrest/{id}/action.
   The session cookie is the actor. This file never sends actor, reviewed_by,
   or contacted_by.
*/

const SLRearrest = (() => {
  const POLL_INTERVAL = 30_000;
  let _timer = null;
  let _review = [];
  let _identity = [];
  let _loading = false;
  let _identityOpen = false;

  const CONF_LABEL = {
    confirmed: 'Confirmed',
    high: 'High',
    probable: 'Probable',
    low: 'Low',
  };

  function init() {
    load();
    if (_timer) clearInterval(_timer);
    _timer = setInterval(load, POLL_INTERVAL);
  }

  async function load() {
    if (_loading) return;
    _loading = true;
    try {
      const r = await fetch(`${API}/api/rearrest/pending?limit=25&include=pending_review,unconfirmed_triage`);
      const d = await r.json();
      if (d.success) {
        const lanes = d.lanes || {};
        _review = lanes.pending_review || (d.alerts || []).filter(a => !isIdentity(a));
        _identity = lanes.needs_identity_check || (d.alerts || []).filter(isIdentity);
        render();
      }
    } catch (e) {
      console.debug('Book Watch poll error:', e);
    } finally {
      _loading = false;
    }
  }

  function isIdentity(alert) {
    return alert.status === 'unconfirmed_triage'
      || alert.lane === 'needs_identity_check'
      || String(alert.confidence || '').toLowerCase() === 'low';
  }

  function render() {
    const panel = document.getElementById('rearrestAlertPanel');
    const body = document.getElementById('rearrestAlertBody');
    const countEl = document.getElementById('rearrestCount');
    if (!panel || !body) return;

    const existingLane = document.getElementById('rearrestIdentityLane');
    if (existingLane) _identityOpen = existingLane.open;

    const total = _review.length + _identity.length;
    if (countEl) countEl.textContent = String(total);
    panel.classList.toggle('has-alerts', total > 0);

    const reviewHtml = _review.length
      ? _review.map(cardHtml).join('')
      : `<div class="ra-empty"><span class="ra-empty-icon">✅</span><span>Review queue is clear</span></div>`;

    const identityHtml = _identity.length
      ? _identity.map(a => cardHtml(a, true)).join('')
      : `<div class="ra-empty"><span>No low-confidence matches</span></div>`;

    body.innerHTML = `
      <div class="ra-lane" id="rearrestReviewLane">
        <div class="ra-lane-label">Review queue <span>${_review.length}</span></div>
        ${reviewHtml}
      </div>
      <details class="ra-identity-lane" id="rearrestIdentityLane" ${_identityOpen ? 'open' : ''}>
        <summary>
          Needs identity check
          <span class="ra-identity-count" id="rearrestIdentityCount">${_identity.length}</span>
        </summary>
        <div class="ra-identity-body">${identityHtml}</div>
      </details>`;

    body.querySelectorAll('.ra-card[data-id]').forEach(card => {
      card.addEventListener('click', () => openLead(card.getAttribute('data-id')));
    });
    body.querySelectorAll('[data-act]').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const card = btn.closest('.ra-card');
        if (!card || btn.disabled) return;
        act(card.getAttribute('data-id'), btn.getAttribute('data-act'));
      });
    });
  }

  function cardHtml(alert, identity) {
    const id = esc(alert._id);
    const confidence = String(alert.confidence || '').toLowerCase();
    const evidence = alert.evidence || {};
    const arrest = evidence.arrest || {};
    const bond = evidence.bond || {};
    const defendant = alert.defendant_name || arrest.name || 'Unknown';
    const timeStr = alert.created_at ? timeAgo(alert.created_at) : '';
    const bondAmount = Number(alert.bond_amount || arrest.bond_amount || 0);
    const bondClass = bondAmount >= 10000 ? 'ra-bond-high' : bondAmount >= 2500 ? 'ra-bond-mid' : 'ra-bond-low';
    const phone = alert.indemnitor_phone || '';
    const phoneMask = phone ? `(***) ***-${String(phone).replace(/\D/g, '').slice(-4)}` : 'No phone';
    const canText = confidence === 'confirmed' || confidence === 'high';
    const textBtn = canText
      ? `<button type="button" class="ra-btn ra-btn-text" data-act="notify_indemnitor">Text indemnitor</button>`
      : `<button type="button" class="ra-btn ra-btn-text" disabled title="Blocked until confidence is confirmed or high">Text blocked</button>`;

    return `
      <div class="ra-card${identity ? ' ra-card-identity' : ''}" id="ra-${id}" data-id="${id}" data-booking="${esc(alert.booking_number || '')}">
        <div class="ra-pulse-dot"></div>
        <div class="ra-card-top">
          <div class="ra-defendant">
            <span class="ra-icon">${identity ? '🪪' : '🚨'}</span>
            <div>
              <div class="ra-name">${esc(defendant)} ${confidenceBadge(confidence)}</div>
              <div class="ra-county">${esc(alert.county || arrest.county || '')} · ${esc(timeStr)} · ${esc(alert.confidence_reason || 'unscored')}</div>
            </div>
          </div>
          <div class="ra-bond-pill ${bondClass}">${money(bondAmount)}</div>
        </div>
        <div class="ra-evidence">
          <div class="ra-evidence-col">
            <div class="ra-evidence-title">New arrest</div>
            ${evRow('Name', arrest.name || defendant)}
            ${evRow('DOB', arrest.dob || alert.arrest_dob)}
            ${evRow('County', arrest.county || alert.county)}
            ${evRow('Booking', arrest.booking_number || alert.booking_number)}
            ${evRow('Charges', arrest.charges || alert.charges)}
            ${evRow('Bond', money(arrest.bond_amount != null ? arrest.bond_amount : alert.bond_amount))}
            ${evRow('Custody', arrest.custody_status || alert.custody_status)}
          </div>
          <div class="ra-evidence-col">
            <div class="ra-evidence-title">Bond on file</div>
            ${evRow('Name', bond.name || alert.prior_defendant_name)}
            ${evRow('DOB', bond.dob || alert.bond_dob)}
            ${evRow('County', bond.county || alert.prior_county)}
            ${evRow('Booking', bond.booking_number || alert.prior_booking_number)}
            ${evRow('Case', bond.case_number || alert.original_case_number)}
            ${evRow('POA', bond.poa_number || alert.original_poa)}
            ${evRow('Bond', money(bond.bond_amount != null ? bond.bond_amount : alert.prior_bond_amount))}
            ${evRow('Status', bond.status || alert.prior_bond_status)}
          </div>
        </div>
        <div class="ra-indemnitor-row">
          <div class="ra-indem-info">
            <span class="ra-indem-label">Prior indemnitor</span>
            <span class="ra-indem-name">${esc(alert.indemnitor_name || 'Unknown')}</span>
            <span class="ra-indem-phone">${esc(phoneMask)}</span>
          </div>
          <div class="ra-actions">
            <button type="button" class="ra-btn ra-btn-revoke" data-act="revoke">Revoke</button>
            <button type="button" class="ra-btn ra-btn-second" data-act="second_bond">Second bond</button>
            <button type="button" class="ra-btn ra-btn-fp" data-act="false_positive">False positive</button>
            <button type="button" class="ra-btn ra-btn-contact" data-act="contacted">Contacted</button>
            ${textBtn}
            <button type="button" class="ra-btn ra-btn-dismiss" data-act="dismiss">Dismiss</button>
          </div>
        </div>
      </div>`;
  }

  function confidenceBadge(confidence) {
    const key = CONF_LABEL[confidence] ? confidence : 'unscored';
    const label = CONF_LABEL[confidence] || 'Unscored';
    return `<span class="ra-conf ra-conf-${esc(key)}">${esc(label)}</span>`;
  }

  function evRow(label, value) {
    const shown = value === 0 ? '0' : (value || '—');
    return `<div class="ra-ev-row"><span class="ra-ev-k">${esc(label)}</span><span class="ra-ev-v">${esc(shown)}</span></div>`;
  }

  function findAlert(id) {
    return _review.find(a => String(a._id) === String(id))
      || _identity.find(a => String(a._id) === String(id));
  }

  function openLead(id) {
    const alert = findAlert(id);
    if (!alert) return;
    const bk = alert.booking_number || alert.rearrest_booking || '';
    if (!bk) {
      toast('This alert has no booking number', 'error');
      return;
    }
    if (window.SLProspective && SLProspective.openDetail) {
      SLProspective.openDetail(bk, {
        indemnitor_name: alert.indemnitor_name || '',
        indemnitor_phone: alert.indemnitor_phone || '',
        prior_bonds_count: alert.prior_bonds_count || 0,
      });
    }
  }

  async function act(id, action) {
    const alert = findAlert(id);
    if (!alert) return;
    const confidence = String(alert.confidence || '').toLowerCase();
    let notes = '';

    if (action === 'revoke') {
      const warn = confidence === 'low' || alert.lane === 'needs_identity_check'
        ? 'This match still needs an identity check. '
        : '';
      if (!window.confirm(`${warn}Move the bond on file to alert when the state machine allows it?`)) return;
    } else if (action === 'false_positive') {
      notes = window.prompt('False-positive reason (required):') || '';
      if (!notes.trim()) {
        toast('A false-positive reason is required', 'error');
        return;
      }
    } else if (action === 'contacted') {
      notes = window.prompt(`Contact notes for ${alert.indemnitor_name || 'indemnitor'}:`) || '';
    } else if (action === 'notify_indemnitor') {
      if (confidence !== 'confirmed' && confidence !== 'high') {
        toast('Indemnitor text stays blocked below confirmed or high confidence', 'error');
        return;
      }
      if (!window.confirm('Send the indemnitor text for this confirmed match?')) return;
    }

    const card = document.getElementById(`ra-${id}`);
    if (card) card.style.opacity = '0.45';
    try {
      const r = await fetch(`${API}/api/rearrest/${id}/action`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, notes: notes || '' }),
      });
      const d = await r.json();
      if (!d.success) {
        if (card) card.style.opacity = '1';
        toast(d.message || d.error || 'Book Watch action failed', 'error');
        return;
      }
      if (action === 'notify_indemnitor') {
        if (card) card.style.opacity = '1';
        toast('Indemnitor text sent', 'success');
        return;
      }
      if (action === 'second_bond' && d.intake_url) {
        window.open(d.intake_url, '_blank', 'noopener');
      }
      const label = {
        revoke: d.bond_status_unchanged ? 'Revoke noted. Bond status was left unchanged.' : 'Revoke sent through the bond state machine',
        second_bond: 'Second-bond intake opened',
        false_positive: 'Marked false positive',
        contacted: 'Marked contacted',
        dismiss: 'Dismissed',
      }[action] || 'Updated';
      toast(label, action === 'false_positive' ? 'info' : 'success');
      await load();
    } catch (e) {
      if (card) card.style.opacity = '1';
      toast('Book Watch action failed', 'error');
    }
  }

  function dismiss(id) {
    return act(id, 'dismiss');
  }

  function contact(id) {
    return act(id, 'contacted');
  }

  function onSSE(data) {
    load();
    toast(`Book Watch: ${data.defendant_name || 'Unknown'} (${data.county || ''})`, 'info');
    playAlert();
  }

  function timeAgo(iso) {
    const d = (Date.now() - new Date(iso).getTime()) / 1000;
    if (Number.isNaN(d) || d < 0) return '';
    if (d < 60) return Math.round(d) + 's ago';
    if (d < 3600) return Math.round(d / 60) + 'm ago';
    if (d < 86400) return Math.round(d / 3600) + 'h ago';
    return Math.round(d / 86400) + 'd ago';
  }

  function money(n) {
    const v = Number(n);
    if (!Number.isFinite(v)) return '—';
    return '$' + v.toLocaleString();
  }

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  function playAlert() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.setValueAtTime(660, ctx.currentTime);
      osc.frequency.setValueAtTime(880, ctx.currentTime + 0.15);
      osc.frequency.setValueAtTime(660, ctx.currentTime + 0.3);
      gain.gain.setValueAtTime(0.2, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.5);
      osc.start(ctx.currentTime);
      osc.stop(ctx.currentTime + 0.5);
    } catch (e) {}
  }

  return { init, load, render, dismiss, contact, act, openLead, onSSE };
})();

document.addEventListener('DOMContentLoaded', () => SLRearrest.init());
