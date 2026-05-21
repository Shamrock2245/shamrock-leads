/**
 * ShamrockLeads — Defendant Lifecycle UI Module
 * sl-defendant-lifecycle.js
 *
 * Provides:
 *  • Card border color system (contact status → bold outline)
 *  • "Shamrock Notes" modal (status, notes, follow-up, next action, pref comm, DNB/DNC)
 *  • Contact log (log a call/text with summary + auto-status bump)
 *  • DNB / DNC badge rendering + filter
 *  • Two-step bond finalization modal (Review → Confirm)
 *  • Bulk notes loader (batch-fetches notes for all visible cards)
 *  • Lifecycle-to-Pipeline bridge (auto-sync to Outreach/Prospective Bonds)
 *
 * Depends on: defendants.js (renderDefCard, loadDefendants, $, val, money, showToast)
 */

/* ═══════════════════════════════════════════════════════════════════════════
   1. CONSTANTS — Status → Color Mapping
   ═══════════════════════════════════════════════════════════════════════════ */
const SL_LIFECYCLE_COLORS = {
  new:         { border: 'transparent',       label: 'New',         icon: '⚪' },
  contacted:   { border: '#3b82f6',           label: 'Contacted',   icon: '📞' },  // blue
  negotiating: { border: '#f59e0b',           label: 'Negotiating', icon: '🤝' },  // amber
  paperwork:   { border: '#a855f7',           label: 'Paperwork',   icon: '📄' },  // purple
  ready:       { border: '#10b981',           label: 'Ready',       icon: '✅' },  // green
  bonded:      { border: '#22c55e',           label: 'Bonded',      icon: '🔒' },  // bright green
  closed:      { border: '#6b7280',           label: 'Closed',      icon: '🗂️' },  // gray
  dnb:         { border: '#ef4444',           label: 'DO NOT BOND', icon: '🚫' },  // red
  dnc:         { border: '#dc2626',           label: 'DO NOT CALL', icon: '🔕' },  // dark red
};

const PREF_COMM_LABELS = {
  call:     '📞 Phone Call',
  text:     '💬 Text / iMessage',
  email:    '📧 Email',
  whatsapp: '💚 WhatsApp',
};

/* ═══════════════════════════════════════════════════════════════════════════
   2. NOTES CACHE — avoid re-fetching on every render
   ═══════════════════════════════════════════════════════════════════════════ */
const _notesCache = {};   // { booking_number: notesDoc }

/** Safe JSON parse — returns null if response isn't valid JSON */
async function _safeJSON(res) {
  if (!res.ok) return null;
  const ct = res.headers.get('content-type') || '';
  if (!ct.includes('application/json')) return null;
  return res.json();
}

async function _fetchNotesDoc(bookingNumber) {
  if (_notesCache[bookingNumber]) return _notesCache[bookingNumber];
  try {
    const res = await fetch(`/api/defendant-notes/${bookingNumber}`);
    const doc = await _safeJSON(res);
    if (!doc) return {};
    _notesCache[bookingNumber] = doc;
    return doc;
  } catch { return {}; }
}

/**
 * Bulk-load notes for all currently visible cards.
 * Called after loadDefendants() renders the grid.
 */
async function bulkLoadNotes() {
  const cards = document.querySelectorAll('.def-card[data-booking]');
  const bookingNumbers = [...cards].map(c => c.dataset.booking).filter(Boolean);
  if (!bookingNumbers.length) return;

  try {
    const res = await fetch(`/api/defendant-notes/bulk?booking_numbers=${bookingNumbers.join(',')}`);
    const map = await _safeJSON(res);
    if (!map) return;
    Object.assign(_notesCache, map);
    // Apply borders + badges to all visible cards
    bookingNumbers.forEach(bn => {
      const notes = _notesCache[bn] || {};
      _applyCardStyling(bn, notes);
    });
  } catch (e) {
    console.warn('bulkLoadNotes failed:', e);
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   3. CARD STYLING — apply border color + DNB/DNC badges
   ═══════════════════════════════════════════════════════════════════════════ */
function _applyCardStyling(bookingNumber, notes) {
  const card = document.querySelector(`.def-card[data-booking="${bookingNumber}"]`);
  if (!card) return;

  let status = notes.shamrock_status || 'new';
  if (notes.dnb) status = 'dnb';
  else if (notes.dnc) status = 'dnc';

  const color = SL_LIFECYCLE_COLORS[status] || SL_LIFECYCLE_COLORS.new;

  // Bold border
  if (color.border !== 'transparent') {
    card.style.borderColor = color.border;
    card.style.boxShadow = `0 0 0 2px ${color.border}44, 0 4px 16px ${color.border}22`;
  } else {
    card.style.borderColor = '';
    card.style.boxShadow = '';
  }

  // Inject/update the lifecycle bar at the top of the card header
  let bar = card.querySelector('.slc-lifecycle-bar');
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'slc-lifecycle-bar';
    const header = card.querySelector('.def-card-header');
    if (header) header.insertAdjacentElement('afterend', bar);
  }

  const dnbBadge = notes.dnb
    ? `<span class="slc-badge slc-badge-dnb">🚫 DNB</span>` : '';
  const dncBadge = notes.dnc
    ? `<span class="slc-badge slc-badge-dnc">🔕 DNC</span>` : '';
  const prefComm = notes.pref_comm
    ? `<span class="slc-badge slc-badge-comm">${PREF_COMM_LABELS[notes.pref_comm] || notes.pref_comm}</span>` : '';
  const followUp = notes.follow_up_date
    ? `<span class="slc-badge slc-badge-followup">📅 Follow-up: ${notes.follow_up_date}</span>` : '';
  const nextAction = notes.next_action
    ? `<span class="slc-badge slc-badge-action" title="${_esc(notes.next_action)}">⚡ ${_esc(notes.next_action.substring(0, 30))}${notes.next_action.length > 30 ? '…' : ''}</span>` : '';

  bar.innerHTML = `
    <div class="slc-status-row">
      <span class="slc-status-dot" style="background:${color.border || '#6b7280'}"></span>
      <span class="slc-status-label">${color.icon} ${color.label}</span>
      ${dnbBadge}${dncBadge}${prefComm}${followUp}${nextAction}
      <button class="slc-notes-btn" onclick="openShamrockNotes('${bookingNumber}')" title="Open Shamrock Notes">
        📝 Notes${notes.shamrock_notes ? ' ●' : ''}
      </button>
    </div>
    ${notes.shamrock_notes ? `<div class="slc-notes-preview">${_esc(notes.shamrock_notes.substring(0, 120))}${notes.shamrock_notes.length > 120 ? '…' : ''}</div>` : ''}
  `;
}

function _esc(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ═══════════════════════════════════════════════════════════════════════════
   4. SHAMROCK NOTES MODAL
   ═══════════════════════════════════════════════════════════════════════════ */
let _notesModalBooking = null;
let _notesModalDefendant = null;

async function openShamrockNotes(bookingNumber, defendantData) {
  _notesModalBooking = bookingNumber;
  _notesModalDefendant = defendantData || null;

  const notes = await _fetchNotesDoc(bookingNumber);

  // Check pipeline status
  let pipelineStatus = null;
  try {
    const psRes = await fetch(`/api/defendant-notes/${bookingNumber}/pipeline-status`);
    pipelineStatus = await _safeJSON(psRes) || { tracked: false };
  } catch { pipelineStatus = { tracked: false }; }

  // Build or reuse modal
  let modal = document.getElementById('slcNotesModal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'slcNotesModal';
    modal.className = 'slc-modal-overlay';
    modal.addEventListener('click', e => { if (e.target === modal) closeShamrockNotes(); });
    document.body.appendChild(modal);
  }

  const contactLog = notes.contact_log || [];
  const logHtml = contactLog.length
    ? contactLog.slice().reverse().map(e => `
        <div class="slc-log-entry">
          <span class="slc-log-method slc-log-${e.method}">${_methodIcon(e.method)} ${e.method?.toUpperCase()}</span>
          <span class="slc-log-dir">${e.direction === 'inbound' ? '← In' : '→ Out'}</span>
          <span class="slc-log-contact">${_esc(e.contact || 'defendant')}</span>
          <span class="slc-log-agent">${_esc(e.agent || '')}</span>
          <span class="slc-log-ts">${_formatTs(e.ts)}</span>
          ${e.summary ? `<div class="slc-log-summary">${_esc(e.summary)}</div>` : ''}
        </div>`).join('')
    : '<div class="slc-log-empty">No contact logged yet</div>';

  modal.innerHTML = `
    <div class="slc-modal-box">
      <div class="slc-modal-header">
        <div>
        <div class="slc-modal-title">📝 Shamrock Notes</div>
          <div class="slc-modal-subtitle">Booking #${bookingNumber}</div>
          ${pipelineStatus?.tracked
            ? `<div class="slc-pipeline-badge tracked">📋 In Outreach — <strong>${(pipelineStatus.stage||'contacted').charAt(0).toUpperCase()+(pipelineStatus.stage||'contacted').slice(1)}</strong></div>`
            : `<div class="slc-pipeline-badge not-tracked">Not in outreach pipeline</div>`
          }
        </div>
        <button class="slc-modal-close" onclick="closeShamrockNotes()">✕</button>
      </div>
      <div class="slc-modal-body">

        <!-- ── Status & Flags ── -->
        <div class="slc-form-section">
          <div class="slc-form-section-title">Bond Status</div>
          <div class="slc-form-row">
            <div class="slc-form-field">
              <label class="slc-label">Shamrock Status</label>
              <select id="slcStatus" class="slc-select">
                ${Object.entries(SL_LIFECYCLE_COLORS).filter(([k])=>!['dnb','dnc'].includes(k)).map(([k,v])=>
                  `<option value="${k}" ${notes.shamrock_status===k?'selected':''}>${v.icon} ${v.label}</option>`
                ).join('')}
              </select>
            </div>
            <div class="slc-form-field">
              <label class="slc-label">Preferred Communication</label>
              <select id="slcPrefComm" class="slc-select">
                <option value="">— Not set —</option>
                ${Object.entries(PREF_COMM_LABELS).map(([k,v])=>
                  `<option value="${k}" ${notes.pref_comm===k?'selected':''}>${v}</option>`
                ).join('')}
              </select>
            </div>
          </div>
          <div class="slc-form-row slc-flags-row">
            <label class="slc-flag-toggle ${notes.dnb?'active-dnb':''}">
              <input type="checkbox" id="slcDnb" ${notes.dnb?'checked':''} onchange="this.closest('label').classList.toggle('active-dnb',this.checked)">
              🚫 DO NOT BOND
            </label>
            <label class="slc-flag-toggle ${notes.dnc?'active-dnc':''}">
              <input type="checkbox" id="slcDnc" ${notes.dnc?'checked':''} onchange="this.closest('label').classList.toggle('active-dnc',this.checked)">
              🔕 DO NOT CALL
            </label>
          </div>
          <div class="slc-form-row" id="slcDnbReasonRow" style="${notes.dnb?'':'display:none'}">
            <div class="slc-form-field slc-full-width">
              <label class="slc-label">DNB Reason</label>
              <input type="text" id="slcDnbReason" class="slc-input" value="${_esc(notes.dnb_reason||'')}" placeholder="Why should we not bond this person?">
            </div>
          </div>
          <div class="slc-form-row" id="slcDncReasonRow" style="${notes.dnc?'':'display:none'}">
            <div class="slc-form-field slc-full-width">
              <label class="slc-label">DNC Reason</label>
              <input type="text" id="slcDncReason" class="slc-input" value="${_esc(notes.dnc_reason||'')}" placeholder="Why should we not call this person?">
            </div>
          </div>
        </div>

        <!-- ── Notes & Follow-up ── -->
        <div class="slc-form-section">
          <div class="slc-form-section-title">Notes & Follow-up</div>
          <div class="slc-form-row">
            <div class="slc-form-field slc-full-width">
              <label class="slc-label">Shamrock Notes</label>
              <textarea id="slcNotes" class="slc-textarea" rows="4" placeholder="What was said, who you spoke with, what they need, any concerns...">${_esc(notes.shamrock_notes||'')}</textarea>
            </div>
          </div>
          <div class="slc-form-row">
            <div class="slc-form-field">
              <label class="slc-label">Follow-up Date</label>
              <input type="date" id="slcFollowUpDate" class="slc-input" value="${notes.follow_up_date||''}">
            </div>
            <div class="slc-form-field">
              <label class="slc-label">Next Action</label>
              <input type="text" id="slcNextAction" class="slc-input" value="${_esc(notes.next_action||'')}" placeholder="e.g. Call indemnitor back at 3pm">
            </div>
          </div>
          <div class="slc-form-row">
            <div class="slc-form-field">
              <label class="slc-label">Agent</label>
              <input type="text" id="slcAgent" class="slc-input" value="${_esc(notes.agent||'')}" placeholder="Your name">
            </div>
          </div>
        </div>

        <!-- ── Log a Contact ── -->
        <div class="slc-form-section">
          <div class="slc-form-section-title">Log a Contact</div>
          <div class="slc-form-row">
            <div class="slc-form-field">
              <label class="slc-label">Method</label>
              <select id="slcLogMethod" class="slc-select">
                <option value="call">📞 Phone Call</option>
                <option value="text">💬 Text / iMessage</option>
                <option value="email">📧 Email</option>
                <option value="in_person">🤝 In Person</option>
              </select>
            </div>
            <div class="slc-form-field">
              <label class="slc-label">Direction</label>
              <select id="slcLogDir" class="slc-select">
                <option value="outbound">→ Outbound</option>
                <option value="inbound">← Inbound</option>
              </select>
            </div>
            <div class="slc-form-field">
              <label class="slc-label">Who Was Contacted</label>
              <input type="text" id="slcLogContact" class="slc-input" value="defendant" placeholder="defendant / cosigner name">
            </div>
          </div>
          <div class="slc-form-row">
            <div class="slc-form-field slc-full-width">
              <label class="slc-label">Summary / What Was Said</label>
              <textarea id="slcLogSummary" class="slc-textarea" rows="2" placeholder="Brief summary of the conversation..."></textarea>
            </div>
          </div>
          <button class="slc-btn slc-btn-log" onclick="logContact('${bookingNumber}')">
            📋 Log Contact
          </button>
        </div>

        <!-- ── Lifecycle Timeline ── -->
        <div class="slc-form-section">
          <div class="slc-form-section-title">📅 Unified Lifecycle Timeline <span class="slc-count" id="slcTimelineCount">—</span></div>
          <div class="slc-timeline" id="slcTimelineDisplay">
            <div class="slc-log-empty">Loading timeline…</div>
          </div>
        </div>
        <!-- ── Financial Ledger ── -->
        <div class="slc-form-section">
          <div class="slc-form-section-title">💰 Financial Ledger <span class="slc-count" id="slcLedgerBalance">—</span></div>
          <div class="slc-ledger" id="slcLedgerDisplay">
            <div class="slc-log-empty">Loading ledger…</div>
          </div>
        </div>
        <!-- ── Contact History ── -->
        <div class="slc-form-section">
          <div class="slc-form-section-title">Contact History <span class="slc-count">${contactLog.length}</span></div>
          <div class="slc-contact-log" id="slcContactLogDisplay">
            ${logHtml}
          </div>
        </div>

      </div>
      <div class="slc-modal-footer">
        <button class="slc-btn slc-btn-secondary" onclick="closeShamrockNotes()">Cancel</button>
        ${pipelineStatus?.tracked
          ? `<button class="slc-btn slc-btn-pipeline tracked" disabled title="Already in outreach pipeline">📋 In Outreach ✓</button>`
          : `<button class="slc-btn slc-btn-pipeline" onclick="promoteToPipeline('${bookingNumber}')">📱 Move to Outreach</button>`
        }
        <button class="slc-btn slc-btn-finalize" onclick="openFinalizeBond('${bookingNumber}')">
          🔒 Finalize Bond
        </button>
        <button class="btn-record-bond" onclick="closeShamrockNotes(); window.openRecordBondModal && openRecordBondModal({booking_number:'${bookingNumber}', ..._notesCache['${bookingNumber}']||{}})">
          ☘️ Record Bond
        </button>
        <button class="slc-btn slc-btn-primary" onclick="saveShamrockNotes('${bookingNumber}')">
          💾 Save Notes
        </button>
      </div>
    </div>
  `;

  // Wire up DNB/DNC reason row toggles
  document.getElementById('slcDnb').addEventListener('change', function() {
    document.getElementById('slcDnbReasonRow').style.display = this.checked ? '' : 'none';
  });
  document.getElementById('slcDnc').addEventListener('change', function() {
    document.getElementById('slcDncReasonRow').style.display = this.checked ? '' : 'none';
  });
  // Load lifecycle timeline asynchronously
  _loadLifecycleTimeline(bookingNumber);

  // iOS Safari touch fix: force repaint before adding .active
  modal.style.display = 'flex';
  modal.style.opacity = '0';
  requestAnimationFrame(function () {
    setTimeout(function () {
      modal.classList.add('active');
      modal.style.display = '';
      modal.style.opacity = '';
      document.body.style.overflow = 'hidden';
    }, 0);
  });
}

function closeShamrockNotes() {
  const modal = document.getElementById('slcNotesModal');
  if (modal) modal.classList.remove('active');
  document.body.style.overflow = '';
}

/* ── Lifecycle Timeline & Ledger Loader ──────────────────────────────────────── */
async function _loadLifecycleTimeline(bookingNumber) {
  const tlEl = document.getElementById('slcTimelineDisplay');
  const countEl = document.getElementById('slcTimelineCount');
  const ledgerEl = document.getElementById('slcLedgerDisplay');
  const balanceEl = document.getElementById('slcLedgerBalance');

  try {
    // 1. Fetch unified timeline
    const tlRes = await fetch(`/api/defendants/by_booking/${encodeURIComponent(bookingNumber)}/timeline`);
    if (tlRes.ok) {
      const data = await tlRes.json();
      const events = data.events || [];
      if (countEl) countEl.textContent = events.length;
      
      if (!events.length) {
        if (tlEl) tlEl.innerHTML = '<div class="slc-log-empty">No lifecycle events recorded yet</div>';
      } else {
        // Group events by year for sticky year headers
        let tlHtml = '';
        let lastYear = null;
        events.forEach(e => {
          const d = e.timestamp ? new Date(e.timestamp) : null;
          const year = d && !isNaN(d) ? d.getFullYear() : null;
          if (year && year !== lastYear) {
            tlHtml += `<div class="slc-timeline-year-group">${year}</div>`;
            lastYear = year;
          }
          const timeStr = d && !isNaN(d)
            ? d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
            : '—';
          const badge = e.badge ? `<span class="lcp-event-badge ${e.badge.class||\'\'}">${_esc(e.badge.text||\'\')}</span>` : '';
          const bookingTag = e.booking_number && e.booking_number !== bookingNumber ? `<span class="slc-timeline-booking">#${e.booking_number}</span>` : '';
          tlHtml += `<div class="slc-timeline-entry">
            <div class="slc-timeline-icon">${e.icon || '📋'}</div>
            <div class="slc-timeline-body">
              <div class="slc-timeline-label">${bookingTag} ${_esc(e.title || e.label || '')} ${badge}</div>
              ${e.detail ? `<div class="slc-timeline-detail">${_esc(e.detail)}</div>` : ''}
              <div class="slc-timeline-time">${timeStr}</div>
            </div>
          </div>`;
        });
        // Show capped notice if the API returned a capped flag
        if (data.capped) {
          tlHtml += `<div class="slc-timeline-capped-notice">Showing most recent ${events.length} of ${data.total_events} events</div>`;
        }
        if (tlEl) tlEl.innerHTML = tlHtml;
      }
    } else {
      if (tlEl) tlEl.innerHTML = `<div class="slc-log-empty" style="color:var(--muted)">Timeline unavailable</div>`;
    }
    
    // 2. Fetch financial ledger
    const ledRes = await fetch(`/api/ledger/${encodeURIComponent(bookingNumber)}`);
    if (ledRes.ok) {
      const ledData = await ledRes.json();
      if (ledData.success) {
        if (balanceEl) balanceEl.textContent = money(ledData.balance || 0);
        
        const history = ledData.history || [];
        if (!history.length) {
          if (ledgerEl) ledgerEl.innerHTML = '<div class="slc-log-empty">No ledger entries yet</div>';
        } else {
          if (ledgerEl) ledgerEl.innerHTML = `
            <table class="slc-ledger-table">
              <thead><tr><th>Date</th><th>Type</th><th>Amount</th><th>Category</th></tr></thead>
              <tbody>
                ${history.map(h => {
                  const amtColor = h.type === 'credit' ? '#10b981' : (h.type === 'debit' ? '#ef4444' : 'inherit');
                  const amtPrefix = h.type === 'credit' ? '-' : (h.type === 'debit' ? '+' : '');
                  const d = new Date(h.timestamp);
                  return `<tr>
                    <td>${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</td>
                    <td style="text-transform:capitalize">${_esc(h.type)}</td>
                    <td style="color:${amtColor};font-weight:600">${amtPrefix}${money(h.amount)}</td>
                    <td>${_esc(h.category)}</td>
                  </tr>`;
                }).join('')}
              </tbody>
            </table>
          `;
        }
      } else {
        if (ledgerEl) ledgerEl.innerHTML = `<div class="slc-log-empty" style="color:var(--muted)">Ledger error: ${ledData.error}</div>`;
      }
    }
  } catch (err) {
    if (tlEl) tlEl.innerHTML = `<div class="slc-log-empty" style="color:var(--muted)">Error loading data: ${err.message}</div>`;
    if (ledgerEl) ledgerEl.innerHTML = '';
  }
}

async function saveShamrockNotes(bookingNumber) {
  const payload = {
    shamrock_status:  document.getElementById('slcStatus')?.value || 'new',
    shamrock_notes:   document.getElementById('slcNotes')?.value || '',
    follow_up_date:   document.getElementById('slcFollowUpDate')?.value || '',
    next_action:      document.getElementById('slcNextAction')?.value || '',
    pref_comm:        document.getElementById('slcPrefComm')?.value || '',
    dnb:              document.getElementById('slcDnb')?.checked || false,
    dnc:              document.getElementById('slcDnc')?.checked || false,
    dnb_reason:       document.getElementById('slcDnbReason')?.value || '',
    dnc_reason:       document.getElementById('slcDncReason')?.value || '',
    agent:            document.getElementById('slcAgent')?.value || '',
  };

  try {
    const res = await fetch(`/api/defendant-notes/${bookingNumber}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await _safeJSON(res);
    if (!data) { if (typeof showToast === 'function') showToast('Save failed: bad response', 'error'); return; }
    if (data.success) {
      _notesCache[bookingNumber] = data.notes;
      _applyCardStyling(bookingNumber, data.notes);
      closeShamrockNotes();
      if (typeof showToast === 'function') showToast('Notes saved ✓', 'success');
    } else {
      if (typeof showToast === 'function') showToast('Save failed: ' + (data.error || 'unknown'), 'error');
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast('Network error: ' + e.message, 'error');
  }
}

async function logContact(bookingNumber) {
  const payload = {
    method:    document.getElementById('slcLogMethod')?.value || 'call',
    direction: document.getElementById('slcLogDir')?.value || 'outbound',
    contact:   document.getElementById('slcLogContact')?.value || 'defendant',
    summary:   document.getElementById('slcLogSummary')?.value || '',
    agent:     document.getElementById('slcAgent')?.value || '',
  };

  try {
    const res = await fetch(`/api/defendant-contact-log/${bookingNumber}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await _safeJSON(res);
    if (!data) { if (typeof showToast === 'function') showToast('Log failed: bad response', 'error'); return; }
    if (data.success) {
      _notesCache[bookingNumber] = data.notes;
      _applyCardStyling(bookingNumber, data.notes);
      // Clear the log form
      if (document.getElementById('slcLogSummary')) document.getElementById('slcLogSummary').value = '';
      // Refresh the contact history display
      const logDiv = document.getElementById('slcContactLogDisplay');
      if (logDiv) {
        const log = (data.notes.contact_log || []).slice().reverse();
        logDiv.innerHTML = log.length
          ? log.map(e => `
              <div class="slc-log-entry">
                <span class="slc-log-method slc-log-${e.method}">${_methodIcon(e.method)} ${e.method?.toUpperCase()}</span>
                <span class="slc-log-dir">${e.direction === 'inbound' ? '← In' : '→ Out'}</span>
                <span class="slc-log-contact">${_esc(e.contact || 'defendant')}</span>
                <span class="slc-log-agent">${_esc(e.agent || '')}</span>
                <span class="slc-log-ts">${_formatTs(e.ts)}</span>
                ${e.summary ? `<div class="slc-log-summary">${_esc(e.summary)}</div>` : ''}
              </div>`).join('')
          : '<div class="slc-log-empty">No contact logged yet</div>';
      }
      if (typeof showToast === 'function') {
        if (data.pipeline_synced) {
          showToast('Contact logged & synced to Outreach ✓', 'success');
        } else {
          showToast('Contact logged ✓', 'success');
        }
      }
    } else {
      if (typeof showToast === 'function') showToast('Log failed: ' + (data.error || 'unknown'), 'error');
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast('Network error: ' + e.message, 'error');
  }
}

/**
 * Promote a defendant to the Outreach (prospective bonds) pipeline
 */
async function promoteToPipeline(bookingNumber) {
  const agent = document.getElementById('slcAgent')?.value || 'Dashboard';
  const notes = document.getElementById('slcNotes')?.value || '';

  try {
    const res = await fetch(`/api/defendant-notes/${bookingNumber}/promote-to-pipeline`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        agent: agent,
        note: notes ? `Notes at promotion: ${notes.substring(0, 200)}` : '',
      }),
    });
    const data = await _safeJSON(res);
    if (!data) { if (typeof showToast === 'function') showToast('Promote failed: bad response', 'error'); return; }
    if (data.success) {
      if (typeof showToast === 'function') showToast(`📱 Moved to Outreach (${data.stage}) ✓`, 'success');
      // Update the button in the modal footer
      const footer = document.querySelector('.slc-modal-footer');
      if (footer) {
        const pipeBtn = footer.querySelector('.slc-btn-pipeline');
        if (pipeBtn) {
          pipeBtn.textContent = '📋 In Outreach ✓';
          pipeBtn.disabled = true;
          pipeBtn.classList.add('tracked');
        }
      }
      // Update pipeline badge
      const badge = document.querySelector('.slc-pipeline-badge');
      if (badge) {
        badge.className = 'slc-pipeline-badge tracked';
        badge.innerHTML = `📋 In Outreach — <strong>${(data.stage||'contacted').charAt(0).toUpperCase()+(data.stage||'contacted').slice(1)}</strong>`;
      }
      // Update prospective badge count if visible
      const countBadge = document.getElementById('prospectiveBadge');
      if (countBadge) { const c = parseInt(countBadge.textContent)||0; countBadge.textContent = c+1; }
    } else {
      if (res.status === 409) {
        if (typeof showToast === 'function') showToast(`Already in Outreach (${data.stage||'pipeline'})`, 'info');
      } else {
        if (typeof showToast === 'function') showToast('Promote failed: ' + (data.error || 'unknown'), 'error');
      }
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast('Network error: ' + e.message, 'error');
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   5. TWO-STEP BOND FINALIZATION MODAL
   ═══════════════════════════════════════════════════════════════════════════ */
let _finalizeStep1Data = null;

async function openFinalizeBond(bookingNumber) {
  // Close notes modal first
  closeShamrockNotes();

  const notes = _notesCache[bookingNumber] || {};

  let modal = document.getElementById('slcFinalizeModal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'slcFinalizeModal';
    modal.className = 'slc-modal-overlay';
    modal.addEventListener('click', e => { if (e.target === modal) _closeFinalizeModal(); });
    document.body.appendChild(modal);
  }

  modal.innerHTML = `
    <div class="slc-modal-box slc-finalize-box">
      <div class="slc-modal-header">
        <div>
          <div class="slc-modal-title">🔒 Finalize Bond — Step 1: Review</div>
          <div class="slc-modal-subtitle">Booking #${bookingNumber}</div>
        </div>
        <button class="slc-modal-close" onclick="_closeFinalizeModal()">✕</button>
      </div>
      <div class="slc-modal-body" id="slcFinalizeBody">
        <div class="slc-form-section">
          <div class="slc-form-section-title">Bond Details</div>
          <div class="slc-form-row">
            <div class="slc-form-field">
              <label class="slc-label">Insurance Company</label>
              <select id="slcFinalInsurer" class="slc-select">
                <option value="osi">OSI — Old Surety Indemnity</option>
                <option value="accredited">Accredited Surety</option>
                <option value="allegheny">Allegheny Casualty</option>
                <option value="bankers">Bankers Insurance</option>
                <option value="other">Other</option>
              </select>
            </div>
            <div class="slc-form-field">
              <label class="slc-label">POA Number</label>
              <input type="text" id="slcFinalPoa" class="slc-input" value="${_esc(notes.poa_number||'')}" placeholder="Power of Attorney #">
            </div>
          </div>
          <div class="slc-form-row">
            <div class="slc-form-field">
              <label class="slc-label">Indemnitor Name</label>
              <input type="text" id="slcFinalIndemName" class="slc-input" value="${_esc(notes.indemnitor_name||'')}" placeholder="Full name of cosigner">
            </div>
            <div class="slc-form-field">
              <label class="slc-label">Indemnitor Phone</label>
              <input type="text" id="slcFinalIndemPhone" class="slc-input" value="${_esc(notes.indemnitor_phone||'')}" placeholder="(239) 555-0100">
            </div>
          </div>
          <div class="slc-form-row">
            <div class="slc-form-field">
              <label class="slc-label">Agent Name</label>
              <input type="text" id="slcFinalAgent" class="slc-input" value="${_esc(notes.agent||'')}" placeholder="Your name">
            </div>
          </div>
          <div class="slc-form-row">
            <div class="slc-form-field slc-full-width">
              <label class="slc-label">Final Notes</label>
              <textarea id="slcFinalNotes" class="slc-textarea" rows="2" placeholder="Any final notes for the file...">${_esc(notes.shamrock_notes||'')}</textarea>
            </div>
          </div>
        </div>
      </div>
      <div class="slc-modal-footer">
        <button class="slc-btn slc-btn-secondary" onclick="_closeFinalizeModal()">Cancel</button>
        <button class="slc-btn slc-btn-primary" onclick="_finalizeBondStep1('${bookingNumber}')">
          Next: Review Summary →
        </button>
      </div>
    </div>
  `;

  modal.classList.add('active');
  document.body.style.overflow = 'hidden';
}

async function _finalizeBondStep1(bookingNumber) {
  const payload = {
    insurance_company: document.getElementById('slcFinalInsurer')?.value || '',
    poa_number:        document.getElementById('slcFinalPoa')?.value || '',
    indemnitor_name:   document.getElementById('slcFinalIndemName')?.value || '',
    indemnitor_phone:  document.getElementById('slcFinalIndemPhone')?.value || '',
    agent:             document.getElementById('slcFinalAgent')?.value || '',
    notes:             document.getElementById('slcFinalNotes')?.value || '',
  };

  try {
    const res = await fetch(`/api/finalize-bond/step1/${bookingNumber}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await _safeJSON(res);
    if (!data) { if (typeof showToast === 'function') showToast('Error: bad response from server', 'error'); return; }
    if (!data.success) {
      if (typeof showToast === 'function') showToast('Error: ' + (data.error || 'unknown'), 'error');
      return;
    }
    _finalizeStep1Data = data.review;
    _showFinalizeStep2(bookingNumber, data.review);
  } catch (e) {
    if (typeof showToast === 'function') showToast('Network error: ' + e.message, 'error');
  }
}

function _showFinalizeStep2(bookingNumber, review) {
  const modal = document.getElementById('slcFinalizeModal');
  if (!modal) return;

  const r = review;
  modal.querySelector('.slc-modal-title').textContent = '🔒 Finalize Bond — Step 2: Confirm';

  modal.querySelector('#slcFinalizeBody').innerHTML = `
    <div class="slc-finalize-review">
      <div class="slc-review-banner">
        ⚠️ Please review all details carefully before confirming. This action will post the bond to Active Bonds.
      </div>
      <table class="slc-review-table">
        <tr><td class="slc-rt-label">Defendant</td><td class="slc-rt-value">${_esc(r.defendant_name)}</td></tr>
        <tr><td class="slc-rt-label">Booking #</td><td class="slc-rt-value mono">${_esc(r.booking_number)}</td></tr>
        <tr><td class="slc-rt-label">County</td><td class="slc-rt-value">${_esc(r.county)}</td></tr>
        <tr><td class="slc-rt-label">Bond Amount</td><td class="slc-rt-value" style="color:#f87171;font-weight:700">${typeof money === 'function' ? money(r.bond_amount) : '$'+r.bond_amount}</td></tr>
        <tr><td class="slc-rt-label">Premium (10%)</td><td class="slc-rt-value" style="color:#34d399;font-weight:700">${typeof money === 'function' ? money(r.premium) : '$'+r.premium}</td></tr>
        <tr><td class="slc-rt-label">Insurance Co.</td><td class="slc-rt-value">${_esc(r.insurance_company)}</td></tr>
        <tr><td class="slc-rt-label">POA Number</td><td class="slc-rt-value mono">${_esc(r.poa_number || '—')}</td></tr>
        <tr><td class="slc-rt-label">Indemnitor</td><td class="slc-rt-value">${_esc(r.indemnitor_name || '—')}</td></tr>
        <tr><td class="slc-rt-label">Indem. Phone</td><td class="slc-rt-value">${_esc(r.indemnitor_phone || '—')}</td></tr>
        <tr><td class="slc-rt-label">Court Date</td><td class="slc-rt-value">${_esc(r.court_date || '—')}</td></tr>
        <tr><td class="slc-rt-label">Case Number</td><td class="slc-rt-value mono">${_esc(r.case_number || '—')}</td></tr>
        <tr><td class="slc-rt-label">Charges</td><td class="slc-rt-value" style="font-size:12px">${_esc((r.charges||'').substring(0,200))}</td></tr>
        <tr><td class="slc-rt-label">Agent</td><td class="slc-rt-value">${_esc(r.agent || '—')}</td></tr>
        <tr><td class="slc-rt-label">Notes</td><td class="slc-rt-value">${_esc(r.notes || '—')}</td></tr>
      </table>
      <div class="slc-confirm-checklist">
        <label class="slc-check-item">
          <input type="checkbox" id="slcChk1"> I have verified the defendant's identity and booking number
        </label>
        <label class="slc-check-item">
          <input type="checkbox" id="slcChk2"> The bond amount and premium are correct
        </label>
        <label class="slc-check-item">
          <input type="checkbox" id="slcChk3"> The indemnitor has been identified and contacted
        </label>
        <label class="slc-check-item">
          <input type="checkbox" id="slcChk4"> All paperwork has been completed or is in progress
        </label>
      </div>
    </div>
  `;

  modal.querySelector('.slc-modal-footer').innerHTML = `
    <button class="slc-btn slc-btn-secondary" onclick="_closeFinalizeModal()">Cancel</button>
    <button class="slc-btn slc-btn-secondary" onclick="openFinalizeBond('${bookingNumber}')">← Back</button>
    <button class="slc-btn slc-btn-confirm" onclick="_finalizeBondStep2('${bookingNumber}','${r.review_token}')">
      ✅ Confirm & Post Bond
    </button>
  `;
}

async function _finalizeBondStep2(bookingNumber, reviewToken) {
  // Validate checklist
  const checks = ['slcChk1','slcChk2','slcChk3','slcChk4'];
  const allChecked = checks.every(id => document.getElementById(id)?.checked);
  if (!allChecked) {
    if (typeof showToast === 'function') showToast('Please check all confirmation boxes before finalizing.', 'warn');
    return;
  }

  const step1 = _finalizeStep1Data || {};
  const payload = {
    review_token:  reviewToken,
    confirmed_by:  step1.agent || '',
    poa_number:    step1.poa_number || '',
    notes:         step1.notes || '',
  };

  try {
    const res = await fetch(`/api/finalize-bond/step2/${bookingNumber}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await _safeJSON(res);
    if (!data) { if (typeof showToast === 'function') showToast('Error: bad response from server', 'error'); return; }
    if (data.success) {
      // Update cache + card
      if (_notesCache[bookingNumber]) {
        _notesCache[bookingNumber].shamrock_status = 'bonded';
        _notesCache[bookingNumber].bond_finalized = true;
      }
      _applyCardStyling(bookingNumber, _notesCache[bookingNumber] || { shamrock_status: 'bonded' });
      _closeFinalizeModal();
      if (typeof showToast === 'function') showToast('🔒 Bond finalized and posted to Active Bonds!', 'success', 6000);
      // Refresh active bonds tab if visible
      if (typeof SLActiveBonds !== 'undefined' && typeof SLActiveBonds.load === 'function') {
        SLActiveBonds.load();
      }
    } else {
      if (typeof showToast === 'function') showToast('Error: ' + (data.error || 'unknown'), 'error');
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast('Network error: ' + e.message, 'error');
  }
}

function _closeFinalizeModal() {
  const modal = document.getElementById('slcFinalizeModal');
  if (modal) modal.classList.remove('active');
  document.body.style.overflow = '';
  _finalizeStep1Data = null;
}

/* ═══════════════════════════════════════════════════════════════════════════
   6. HELPERS
   ═══════════════════════════════════════════════════════════════════════════ */
function _methodIcon(method) {
  const icons = { call: '📞', text: '💬', email: '📧', in_person: '🤝' };
  return icons[method] || '📋';
}

function _formatTs(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  } catch { return ts; }
}

/* ═══════════════════════════════════════════════════════════════════════════
   7. DEFENDANTS TAB FILTER — DNB/DNC filter button injection
   ═══════════════════════════════════════════════════════════════════════════ */
function _injectDnbFilter() {
  const filterRow = document.getElementById('defBondRange')?.parentElement;
  if (!filterRow || document.getElementById('slcDnbFilterBtn')) return;

  const btn = document.createElement('button');
  btn.id = 'slcDnbFilterBtn';
  btn.className = 'slc-dnb-filter-btn';
  btn.textContent = '🚫 DNB/DNC List';
  btn.onclick = openDnbList;
  filterRow.insertBefore(btn, filterRow.firstChild);
}

async function openDnbList() {
  try {
    const res = await fetch('/api/dnb-list');
    const data = await _safeJSON(res);
    if (!data) return;
    const records = data.records || [];

    let modal = document.getElementById('slcDnbListModal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'slcDnbListModal';
      modal.className = 'slc-modal-overlay';
      modal.addEventListener('click', e => { if (e.target === modal) modal.classList.remove('active'); });
      document.body.appendChild(modal);
    }

    const rows = records.length
      ? records.map(r => `
          <tr>
            <td class="mono">${_esc(r.booking_number)}</td>
            <td>${r.dnb ? '<span class="slc-badge slc-badge-dnb">🚫 DNB</span>' : ''}${r.dnc ? '<span class="slc-badge slc-badge-dnc">🔕 DNC</span>' : ''}</td>
            <td>${_esc(r.dnb_reason || r.dnc_reason || '—')}</td>
            <td>${_esc(r.agent || '—')}</td>
            <td style="font-size:11px">${_formatTs(r.updated_at)}</td>
            <td><button class="slc-btn slc-btn-sm" onclick="openShamrockNotes('${r.booking_number}')">Edit</button></td>
          </tr>`).join('')
      : '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:20px">No DNB/DNC records</td></tr>';

    modal.innerHTML = `
      <div class="slc-modal-box">
        <div class="slc-modal-header">
          <div class="slc-modal-title">🚫 Do Not Bond / Do Not Call List</div>
          <button class="slc-modal-close" onclick="document.getElementById('slcDnbListModal').classList.remove('active')">✕</button>
        </div>
        <div class="slc-modal-body">
          <table class="slc-dnb-table">
            <thead><tr><th>Booking #</th><th>Flag</th><th>Reason</th><th>Agent</th><th>Updated</th><th></th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>
    `;
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
  } catch (e) {
    if (typeof showToast === 'function') showToast('Failed to load DNB list: ' + e.message, 'error');
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
   8. INIT — hook into the existing loadDefendants flow
   ═══════════════════════════════════════════════════════════════════════════ */
(function _initLifecycle() {
  // Wait for DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _setup);
  } else {
    _setup();
  }

  function _setup() {
    // Inject DNB filter button when defendants tab is visible
    const defTab = document.getElementById('tabDefendants');
    if (defTab) {
      const observer = new MutationObserver(() => {
        if (defTab.classList.contains('active') || defTab.style.display !== 'none') {
          _injectDnbFilter();
        }
      });
      observer.observe(defTab, { attributes: true, attributeFilter: ['class', 'style'] });
    }

    // Monkey-patch loadDefendants to call bulkLoadNotes after render
    const _origLoadDef = window.loadDefendants;
    if (typeof _origLoadDef === 'function') {
      window.loadDefendants = async function(...args) {
        await _origLoadDef.apply(this, args);
        // Small delay to let the DOM render
        setTimeout(bulkLoadNotes, 100);
      };
    }

    // Also patch SL.loadDefendants if it exists
    if (window.SL && typeof window.SL.loadDefendants === 'function') {
      const _origSLLoad = window.SL.loadDefendants.bind(window.SL);
      window.SL.loadDefendants = async function(...args) {
        await _origSLLoad(...args);
        setTimeout(bulkLoadNotes, 100);
      };
    }
  }
})();

// Expose public API
window.SLLifecycle = {
  openShamrockNotes,
  closeShamrockNotes,
  saveShamrockNotes,
  logContact,
  openFinalizeBond,
  openDnbList,
  bulkLoadNotes,
  applyCardStyling: _applyCardStyling,
};

// Also expose top-level for onclick= attributes
window.openShamrockNotes = openShamrockNotes;
window.closeShamrockNotes = closeShamrockNotes;
window.saveShamrockNotes = saveShamrockNotes;
window.logContact = logContact;
window.openFinalizeBond = openFinalizeBond;
window.openDnbList = openDnbList;
