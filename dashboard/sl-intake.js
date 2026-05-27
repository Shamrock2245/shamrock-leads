/**
 * sl-intake.js — ShamrockLeads Intake Queue Module
 *
 * Handles the Intake Queue tab: loading, rendering, processing, and
 * manual entry of indemnitor intake records from all sources.
 *
 * Sources handled:
 *   🌐 wix_portal   — Wix/Velo indemnitor portal
 *   📱 telegram     — Telegram Mini App
 *   🚶 walk_in      — Walk-in client
 *   📞 phone_call   — Phone intake
 *   ✏️ manual_entry — Staff manual entry
 *   🔖 bookmarklet  — LCSO bookmarklet scrape
 *
 * API endpoints consumed:
 *   GET  /api/intake/queue
 *   GET  /api/intake/stats
 *   POST /api/intake/submit
 *   POST /api/intake/<id>/process
 *   POST /api/intake/<id>/archive
 */

const SLIntake = (() => {
  'use strict';

  // ── State ──────────────────────────────────────────────────────────────────
  let _currentIntakeId = null;
  let _currentHydration = null;
  let _queue = [];

  const SOURCE_ICONS = {
    wix_portal:    '🌐',
    telegram:      '📱',
    telegram_mini_app: '📱',
    walk_in:       '🚶',
    phone_call:    '📞',
    manual_entry:  '✏️',
    bookmarklet:   '🔖',
    'shamrock-leads-dashboard': '☘️',
  };

  const STATUS_BADGES = {
    pending:     '<span style="background:#f59e0b;color:#000;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">PENDING</span>',
    in_progress: '<span style="background:#3b82f6;color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">IN PROGRESS</span>',
    archived:    '<span style="background:#6b7280;color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">ARCHIVED</span>',
    promoted:    '<span style="background:#22c55e;color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">☘️ PROMOTED</span>',
  };

  const RISK_COLORS = {
    low:    '#22c55e',
    medium: '#f59e0b',
    high:   '#ef4444',
    '':     '#6b7280',
  };

  // ── Load queue from API ────────────────────────────────────────────────────
  async function load() {
    const source = document.getElementById('intakeSourceFilter')?.value || '';
    const status = document.getElementById('intakeStatusFilter')?.value || 'pending';
    const tbody = document.getElementById('intakeQueueBody');
    if (tbody) tbody.innerHTML = '<tr><td colspan="10" class="loading">Loading…</td></tr>';

    try {
      const params = new URLSearchParams({ limit: 100 });
      if (source) params.set('source', source);
      if (status && status !== 'all') params.set('status', status);

      const [queueRes, statsRes] = await Promise.all([
        fetch(`/api/intake/queue?${params}`),
        fetch('/api/intake/stats'),
      ]);

      const queueData = await queueRes.json();
      const statsData = statsRes.ok ? await statsRes.json() : {};

      _queue = queueData.intakes || [];
      _renderStats(statsData);
      _renderQueue(_queue);

      // Update badge
      const badge = document.getElementById('intakeBadge');
      if (badge) badge.textContent = statsData.pending ?? _queue.length;

      const meta = document.getElementById('intakeQueueMeta');
      if (meta) meta.textContent = `${_queue.length} record${_queue.length !== 1 ? 's' : ''}`;
    } catch (err) {
      console.error('[SLIntake] load error:', err);
      if (tbody) tbody.innerHTML = `<tr><td colspan="10" style="color:var(--danger);text-align:center">Error loading queue: ${err.message}</td></tr>`;
    }
  }

  // ── Render stats row ───────────────────────────────────────────────────────
  function _renderStats(stats) {
    const container = document.getElementById('intakeStatsRow');
    if (!container) return;

    const bySource = stats.by_source || {};
    const byStatus = stats.by_status || {};

    const cards = [
      { label: 'Total', value: stats.total ?? 0, color: '#6366f1' },
      { label: '⏳ Pending', value: byStatus.pending ?? 0, color: '#f59e0b' },
      { label: '🔄 In Progress', value: byStatus.in_progress ?? 0, color: '#3b82f6' },
      { label: '✅ Archived', value: byStatus.archived ?? 0, color: '#22c55e' },
      { label: '🌐 Wix Portal', value: bySource.wix_portal ?? 0, color: '#8b5cf6' },
      { label: '📱 Telegram', value: (bySource.telegram ?? 0) + (bySource.telegram_mini_app ?? 0), color: '#06b6d4' },
      { label: '🚶 Walk-In', value: bySource.walk_in ?? 0, color: '#10b981' },
      { label: '📞 Phone', value: bySource.phone_call ?? 0, color: '#f97316' },
    ];

    container.innerHTML = cards.map(c => `
      <div style="background:var(--card,var(--panel));border:1px solid var(--border);border-top:3px solid ${c.color};border-radius:var(--r-md,10px);padding:14px 16px;text-align:center;transition:transform .2s,box-shadow .2s" onmouseenter="this.style.transform='translateY(-2px)';this.style.boxShadow='0 4px 16px rgba(0,0,0,.2)'" onmouseleave="this.style.transform='';this.style.boxShadow=''">
        <div class="sl-kpi-val" data-target="${c.value}" style="font-size:24px;font-weight:800;color:${c.color};line-height:1;margin-bottom:4px">0</div>
        <div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)">${c.label}</div>
      </div>
    `).join('');
    // Animate KPI counters
    container.querySelectorAll('.sl-kpi-val').forEach(function(el) {
      var target = parseInt(el.dataset.target, 10) || 0;
      if (target === 0) { el.textContent = '0'; return; }
      var duration = 600, startTime = null;
      function step(ts) {
        if (!startTime) startTime = ts;
        var p = Math.min((ts - startTime) / duration, 1);
        el.textContent = Math.round(target * p).toLocaleString();
        if (p < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    });
  }

  // ── Render queue table ─────────────────────────────────────────────────────
  function _renderQueue(items) {
    const tbody = document.getElementById('intakeQueueBody');
    if (!tbody) return;

    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:24px">No intakes found</td></tr>';
      return;
    }

    tbody.innerHTML = items.map(item => {
      const icon = SOURCE_ICONS[item.Source] || '📋';
      const ts = item.Timestamp ? new Date(item.Timestamp).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
      const riskColor = RISK_COLORS[item.AI_Risk?.toLowerCase()] || RISK_COLORS[''];
      const riskBadge = item.AI_Risk
        ? `<span style="background:${riskColor};color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">${item.AI_Risk.toUpperCase()}</span>`
        : '<span style="color:var(--muted);font-size:11px">—</span>';
      const statusBadge = STATUS_BADGES[item.Status] || item.Status;

      return `
        <tr>
          <td><span title="${item.SourceLabel || item.Source}">${icon} ${item.SourceLabel || item.Source}</span></td>
          <td style="white-space:nowrap;font-size:12px">${ts}</td>
          <td>
            <strong>${_esc(item.FullName)}</strong>
            ${item.Email ? `<br><span style="font-size:11px;color:var(--muted)">${_esc(item.Email)}</span>` : ''}
          </td>
          <td style="white-space:nowrap">${_esc(item.Phone) || '—'}</td>
          <td>${_esc(item.DefendantName) || '—'}</td>
          <td>${_esc(item.County) || '—'}</td>
          <td style="font-family:monospace;font-size:12px">${_esc(item.BookingNumber) || '—'}</td>
          <td>${riskBadge}</td>
          <td>${statusBadge}</td>
          <td style="white-space:nowrap">
            <button class="btn-sm btn-primary" onclick="SLIntake.openProcess('${_esc(item.IntakeID)}')" style="font-size:11px;padding:4px 10px">Process</button>
            <button class="btn-sm" onclick="SLIntake.archive('${_esc(item.IntakeID)}')" style="font-size:11px;padding:4px 10px;background:var(--muted);color:#fff;border:none;border-radius:var(--radius-sm);cursor:pointer;margin-left:4px">Archive</button>
          </td>
        </tr>
      `;
    }).join('');
  }

  // ── Open process modal ─────────────────────────────────────────────────────
  async function openProcess(intakeId) {
    _currentIntakeId = intakeId;
    const modal = document.getElementById('intakeModal');
    const body = document.getElementById('intakeModalBody');
    if (!modal || !body) return;

    body.innerHTML = '<p style="text-align:center;padding:24px;color:var(--muted)">Loading…</p>';
    modal.classList.add('active');
    modal.style.display = 'flex';

    try {
      const res = await fetch(`/api/intake/${encodeURIComponent(intakeId)}/process`, { method: 'POST' });
      const data = await res.json();
      if (!data.success) throw new Error(data.error || 'Failed to process intake');

      _currentHydration = data.hydration;
      body.innerHTML = _renderProcessBody(data);
    } catch (err) {
      body.innerHTML = `<p style="color:var(--danger);padding:16px">Error: ${err.message}</p>`;
    }
  }

  function _renderProcessBody(data) {
    const h = data.hydration || {};
    const ind = h.indemnitor || {};
    const def = h.defendant || {};

    const field = (label, value) => `
      <div>
        <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px">${label}</div>
        <div style="font-size:13px;font-weight:500">${_esc(value) || '<span style="color:var(--muted)">—</span>'}</div>
      </div>
    `;

    return `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div style="grid-column:1/-1">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
            <span style="font-size:20px">${SOURCE_ICONS[h.source] || '📋'}</span>
            <div>
              <div style="font-weight:600">${_esc(h.source_label || h.source)}</div>
              <div style="font-size:12px;color:var(--muted)">Intake ID: ${_esc(data.intake_id)}</div>
            </div>
            ${h.consent_given ? '<span style="background:#22c55e;color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;margin-left:auto">✓ CONSENT</span>' : ''}
          </div>
          <hr style="border-color:var(--border);margin:0 0 12px">
        </div>

        <div style="grid-column:1/-1">
          <h4 style="margin:0 0 10px;font-size:12px;color:var(--accent);text-transform:uppercase;letter-spacing:.5px">Defendant</h4>
        </div>
        ${field('Name', def.name)}
        ${field('Booking #', def.bookingNumber)}
        ${field('County', def.county)}
        ${field('Facility', def.facility)}
        ${field('Bond Amount', def.bondAmount ? '$' + Number(def.bondAmount).toLocaleString() : '')}
        ${field('Charges', def.charges)}

        <div style="grid-column:1/-1;margin-top:8px">
          <h4 style="margin:0 0 10px;font-size:12px;color:var(--accent);text-transform:uppercase;letter-spacing:.5px">Indemnitor</h4>
        </div>
        ${field('Name', [ind.firstName, ind.middleName, ind.lastName].filter(Boolean).join(' '))}
        ${field('Relationship', ind.relationship)}
        ${field('DOB', ind.dob)}
        ${field('Phone', ind.phone)}
        ${field('Email', ind.email)}
        ${field('Address', [ind.address, ind.city, ind.state, ind.zip].filter(Boolean).join(', '))}
        ${field('Employer', ind.employer)}
        ${field('Employer Phone', ind.employerPhone)}

        <div style="grid-column:1/-1;margin-top:8px">
          <h4 style="margin:0 0 10px;font-size:12px;color:var(--accent);text-transform:uppercase;letter-spacing:.5px">References</h4>
        </div>
        ${field('Ref 1', [ind.ref1Name, ind.ref1Relation, ind.ref1Phone].filter(Boolean).join(' · '))}
        ${field('Ref 1 Address', ind.ref1Address)}
        ${field('Ref 2', [ind.ref2Name, ind.ref2Relation, ind.ref2Phone].filter(Boolean).join(' · '))}
        ${field('Ref 2 Address', ind.ref2Address)}

        ${h.gps_latitude ? `
          <div style="grid-column:1/-1;margin-top:8px">
            <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">GPS Location</div>
            <div style="font-size:13px">${h.gps_latitude}, ${h.gps_longitude}</div>
          </div>
        ` : ''}
      </div>
    `;
  }

  // ── Write bond from current intake ────────────────────────────────────────
  function writeBondFromIntake() {
    if (!_currentHydration) {
      if (typeof SL !== 'undefined') SL.toast('⚠️ No intake data loaded — re-open the intake first', 'warn');
      return;
    }

    // Capture data BEFORE closeModal() clears _currentHydration / _currentIntakeId
    const h = _currentHydration;
    const ind = h.indemnitor || {};
    const def = h.defendant || {};
    const intakeId = _currentIntakeId;

    // Close intake modal (this nulls _currentHydration + _currentIntakeId)
    closeModal();

    // Build the bond options payload
    const bondOpts = {
      defendant: {
        full_name:  def.name,
        first_name: def.firstName,
        last_name:  def.lastName,
        county:     def.county,
        facility:   def.facility,
      },
      booking: {
        booking_number: def.bookingNumber,
        county:         def.county,
        facility:       def.facility,
      },
      bond: {
        amount:  def.bondAmount || 0,
      },
      charges: def.charges,
      indemnitors: [ind],
      intake_id: intakeId,
      intake_source: h.source,
    };

    // Pre-populate the Write Bond modal
    if (typeof SL !== 'undefined' && typeof SL.openWriteBond === 'function') {
      SL.openWriteBond(bondOpts);
    } else if (typeof SL !== 'undefined' && typeof SL.openBondModal === 'function') {
      // Fallback: use openBondModal directly with a synthetic lead
      const syntheticLead = {
        full_name:      bondOpts.defendant.full_name || 'Unknown',
        bond_amount:    bondOpts.bond.amount || 0,
        county:         bondOpts.booking.county || '',
        booking_number: bondOpts.booking.booking_number || '',
        charges:        bondOpts.charges || '',
        _intake_indemnitors: bondOpts.indemnitors,
        _intake_id:     bondOpts.intake_id || '',
        _intake_source: bondOpts.intake_source || '',
      };
      SL.openBondModal(syntheticLead);

      // Pre-fill indemnitor fields after modal renders
      if (bondOpts.indemnitors.length > 0) {
        setTimeout(() => {
          const i0 = bondOpts.indemnitors[0];
          const fm = [
            ['indemnitorFirstName', i0.firstName],
            ['indemnitorLastName',  i0.lastName],
            ['indemnitorPhone',     i0.phone],
            ['indemnitorEmail',     i0.email],
            ['indemnitorRelation',  i0.relationship],
            ['indemnitorDOB',       i0.dob],
            ['indemnitorAddress',   i0.address],
            ['indemnitorCity',      i0.city],
            ['indemnitorZip',       i0.zip],
            ['indemnitorEmployer',  i0.employer],
            ['indemnitorEmployerPhone', i0.employerPhone],
          ];
          fm.forEach(([id, val]) => {
            const el = document.getElementById(id);
            if (el && val) el.value = val;
          });
        }, 200);
      }

      SL.toast('✍️ Bond form pre-populated from intake', 'success');
    } else {
      SL.toast('⚠️ Bond modal not available', 'warn');
    }
  }

  // ── Archive intake ─────────────────────────────────────────────────────────
  async function archive(intakeId) {
    if (!confirm(`Archive intake ${intakeId}?`)) return;
    try {
      const res = await fetch(`/api/intake/${encodeURIComponent(intakeId)}/archive`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        if (typeof SL !== 'undefined') SL.toast('✅ Intake archived', 'success');
        load();
      } else {
        throw new Error(data.error);
      }
    } catch (err) {
      if (typeof SL !== 'undefined') SL.toast(`Error: ${err.message}`, 'error');
    }
  }

  async function archiveCurrent() {
    if (_currentIntakeId) {
      closeModal();
      await archive(_currentIntakeId);
    }
  }

  // ── Manual Entry Modal ─────────────────────────────────────────────────────
  function openManualEntry() {
    const modal = document.getElementById('manualIntakeModal');
    if (modal) { modal.classList.add('active'); modal.style.display = 'flex'; }
  }

  function closeManualModal() {
    const modal = document.getElementById('manualIntakeModal');
    if (modal) { modal.classList.remove('active'); modal.style.display = 'none'; }
  }

  async function submitManualEntry() {
    const g = id => document.getElementById(id)?.value?.trim() || '';
    const payload = {
      source: g('miSource') || 'manual_entry',
      // Defendant
      defendantName:        g('miDefName'),
      bookingNumber:        g('miBookingNum'),
      county:               g('miCounty'),
      jailFacility:         g('miFacility'),
      bondAmount:           g('miBondAmount'),
      charges:              g('miCharges'),
      // Indemnitor
      indemnitorFirstName:  g('miIndFirst'),
      indemnitorLastName:   g('miIndLast'),
      indemnitorPhone:      g('miIndPhone'),
      indemnitorEmail:      g('miIndEmail'),
      indemnitorRelation:   g('miIndRelation'),
      indemnitorDOB:        g('miIndDOB'),
      indemnitorStreetAddress: g('miIndAddress'),
      indemnitorCity:       g('miIndCity'),
      indemnitorZipCode:    g('miIndZip'),
      indemnitorEmployerName: g('miIndEmployer'),
      indemnitorEmployerPhone: g('miIndEmployerPhone'),
      // References
      reference1Name:       g('miRef1Name'),
      reference1Phone:      g('miRef1Phone'),
      reference1Relation:   g('miRef1Relation'),
      reference1Address:    g('miRef1Address'),
      reference2Name:       g('miRef2Name'),
      reference2Phone:      g('miRef2Phone'),
      reference2Relation:   g('miRef2Relation'),
      reference2Address:    g('miRef2Address'),
    };

    if (!payload.indemnitorFirstName && !payload.indemnitorLastName) {
      if (typeof SL !== 'undefined') SL.toast('⚠️ Indemnitor name required', 'warn');
      return;
    }

    try {
      const res = await fetch('/api/intake/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.success) {
        closeManualModal();
        if (typeof SL !== 'undefined') SL.toast(`✅ Intake submitted: ${data.intake_id}`, 'success');
        load();
      } else {
        throw new Error(data.error);
      }
    } catch (err) {
      if (typeof SL !== 'undefined') SL.toast(`Error: ${err.message}`, 'error');
    }
  }

  // ── Mark intake as In Progress (promote to prospective bonds pipeline) ───────
  async function markAsInProgress() {
    if (!_currentIntakeId) {
      if (typeof SL !== 'undefined') SL.toast('⚠️ No intake loaded — re-open the intake first', 'warn');
      return;
    }
    const h = _currentHydration || {};
    const def = h.defendant || {};
    const ind = h.indemnitor || {};

    // Ask which pipeline stage to start at
    const stage = prompt(
      'Which pipeline stage should this bond start at?\n\n' +
      '  contacted   — Initial contact made\n' +
      '  negotiating — Actively negotiating terms\n' +
      '  paperwork   — Paperwork in progress\n' +
      '  ready       — Ready to post\n\n' +
      'Enter stage name (default: contacted):',
      'contacted'
    );
    if (stage === null) return; // user cancelled
    const validStages = ['contacted', 'negotiating', 'paperwork', 'ready'];
    const finalStage = validStages.includes((stage || '').trim().toLowerCase())
      ? (stage || '').trim().toLowerCase()
      : 'contacted';

    const intakeId = _currentIntakeId;
    closeModal();

    try {
      const res = await fetch('/api/prospective-bonds/from-intake', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          intake_id: intakeId,
          booking_number: def.bookingNumber || '',
          defendant_name: def.name || '',
          county: def.county || '',
          bond_amount: def.bondAmount || 0,
          charges: def.charges || '',
          indemnitor_name: [ind.firstName, ind.lastName].filter(Boolean).join(' '),
          indemnitor_phone: ind.phone || '',
          indemnitor_email: ind.email || '',
          indemnitor_relationship: ind.relationship || '',
          stage: finalStage,
          agent: 'Dashboard',
        }),
      });
      const data = await res.json();
      if (data.success) {
        if (typeof SL !== 'undefined') {
          SL.toast('🟢 Bond moved to In Progress (' + finalStage + ')', 'success');
          if (typeof SLProspective !== 'undefined') SLProspective.load();
        }
        load(); // refresh intake queue
      } else if (res.status === 409) {
        if (typeof SL !== 'undefined') SL.toast('Already in In Progress pipeline (stage: ' + (data.stage || 'unknown') + ')', 'warn');
      } else {
        throw new Error(data.error || 'Failed to mark as In Progress');
      }
    } catch (err) {
      if (typeof SL !== 'undefined') SL.toast('Error: ' + err.message, 'error');
    }
  }

  // ── Promote intake to Active Bond (Phase 5 — atomic transition) ────────────
  async function promoteToBond() {
    if (!_currentIntakeId) {
      if (typeof SL !== 'undefined') SL.toast('⚠️ No intake loaded — re-open the intake first', 'warn');
      return;
    }
    const h = _currentHydration || {};
    const def = h.defendant || {};

    // Check for a matched booking number
    const bookingNum = def.bookingNumber || h.matched_booking_number || '';
    if (!bookingNum) {
      if (typeof SL !== 'undefined') SL.toast('⚠️ No matched booking number — run Match first', 'warn');
      return;
    }

    // Ask for surety selection
    const surety = prompt(
      'Select surety company:\n\n' +
      '  osi      — O\'Shaughnahill Surety & Insurance\n' +
      '  palmetto — Palmetto Surety Corporation\n\n' +
      'Enter surety (default: osi):',
      'osi'
    );
    if (surety === null) return; // cancelled
    const finalSurety = ['osi', 'palmetto'].includes((surety || '').trim().toLowerCase())
      ? (surety || '').trim().toLowerCase()
      : 'osi';

    // Optional: case number
    const caseNumber = prompt('Court case number (optional, press Enter to skip):', '') || '';

    const intakeId = _currentIntakeId;
    closeModal();

    try {
      const res = await fetch(`/api/intake/${encodeURIComponent(intakeId)}/promote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          surety: finalSurety,
          case_number: caseNumber,
        }),
      });
      const data = await res.json();
      if (data.success) {
        if (typeof SL !== 'undefined') {
          SL.toast(
            `☘️ Bond created! ${data.defendant_name} — ${data.surety} — POA: ${data.poa_number} — $${Number(data.bond_amount).toLocaleString()}`,
            'success',
            6000
          );
          // Refresh both tabs
          if (typeof SLActiveBonds !== 'undefined') SLActiveBonds.load();
        }
        load(); // refresh intake queue
      } else {
        throw new Error(data.error || 'Promotion failed');
      }
    } catch (err) {
      if (typeof SL !== 'undefined') SL.toast('Error: ' + err.message, 'error');
    }
  }

  // ── Modal close ──────────────────────────────────────────────────────────────────────────────────
  function closeModal() {
    const modal = document.getElementById('intakeModal');
    if (modal) { modal.classList.remove('active'); modal.style.display = 'none'; }
    _currentIntakeId = null;
    _currentHydration = null;
  }

  // ── Utility ────────────────────────────────────────────────────────────────
  function _esc(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  return {
    load,
    openProcess,
    writeBondFromIntake,
    markAsInProgress,
    promoteToBond,
    archive,
    archiveCurrent,
    openManualEntry,
    closeManualModal,
    submitManualEntry,
    closeModal,
  };
})();
