/**
 * sl-intake.js — Bond Desk (write the bond)
 *
 * Primary ops surface for ShamrockLeads:
 *   Match defendant → verify indemnitor → generate packet → promote to Active Bonds.
 *
 * Lead Pipeline (tabProspective) is optional pre-desk contact only — not the bond path.
 *
 * Sources: Wix · Telegram · Walk-In · Phone · Manual · Bookmarklet
 *
 * APIs:
 *   GET  /api/intake/queue | /stats
 *   POST /api/intake/submit | /process | /match | /promote | /archive
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
    pending:     '<span style="background:#f59e0b;color:#000;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">NEW</span>',
    in_progress: '<span style="background:#3b82f6;color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">WRITING</span>',
    archived:    '<span style="background:#6b7280;color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">ARCHIVED</span>',
    promoted:    '<span style="background:#22c55e;color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">→ ACTIVE BOND</span>',
  };

  // ── Packet readiness (mirrors signnow_packet_service autofill groups) ───────
  function _val(v) {
    if (v == null) return '';
    return String(v).trim();
  }

  function _computeReadiness(h) {
    const ind = (h && h.indemnitor) || {};
    const def = (h && h.defendant) || {};
    const indName = [ind.firstName, ind.lastName].filter(Boolean).join(' ') || _val(h && h.indemnitor_name);
    const defName = _val(def.name) || _val(h && h.defendant_name);
    const booking = _val(def.bookingNumber) || _val(h && h.matched_booking_number);
    const matched = !!(h && (h.matched_booking_number || booking));
    const address = [ind.address, ind.city, ind.state, ind.zip].filter(Boolean).join(', ');

    const checks = [
      { id: 'defendant', label: 'Defendant name', ok: !!defName, required: true },
      { id: 'booking', label: 'Booking # / match', ok: !!booking, required: true },
      { id: 'county', label: 'County', ok: !!_val(def.county), required: true },
      { id: 'bond', label: 'Bond amount', ok: !!_val(def.bondAmount) && Number(String(def.bondAmount).replace(/[$,]/g, '')) > 0, required: true },
      { id: 'charges', label: 'Charges', ok: !!_val(def.charges), required: false },
      { id: 'indemnitor', label: 'Indemnitor name', ok: !!indName, required: true },
      { id: 'phone', label: 'Indemnitor phone', ok: !!_val(ind.phone), required: true },
      { id: 'address', label: 'Indemnitor address', ok: !!_val(ind.address) || address.length > 4, required: true },
      { id: 'relation', label: 'Relationship', ok: !!_val(ind.relationship), required: false },
      { id: 'dob', label: 'Indemnitor DOB', ok: !!_val(ind.dob), required: false },
      { id: 'email', label: 'Indemnitor email (SignNow)', ok: !!_val(ind.email), required: false },
      { id: 'refs', label: 'At least 1 reference', ok: !!(_val(ind.ref1Name) || _val(ind.ref1Phone)), required: false },
      { id: 'match', label: 'Defendant matched', ok: matched, required: true },
    ];

    const required = checks.filter(c => c.required);
    const requiredOk = required.filter(c => c.ok).length;
    const optionalOk = checks.filter(c => !c.required && c.ok).length;
    const allRequired = required.every(c => c.ok);
    const packetReady = allRequired; // hard gate for promote / soft warn for packet

    // Stage index for stepper (0–4)
    let stage = 0; // new
    if (defName && indName) stage = 1; // people present
    if (matched) stage = 2; // matched
    if (packetReady && _val(ind.email)) stage = 3; // ready for packet
    if (h && h.paperwork_status === 'signed') stage = 4;
    if (h && (h.status === 'promoted' || h.promoted_to_booking)) stage = 5;

    return { checks, requiredOk, requiredTotal: required.length, optionalOk, allRequired, packetReady, stage, booking, indName, defName };
  }

  function _renderStepper(readiness) {
    const steps = [
      { n: 0, label: 'Open' },
      { n: 1, label: 'People' },
      { n: 2, label: 'Match' },
      { n: 3, label: 'Packet' },
      { n: 4, label: 'Active Bond' },
    ];
    // Map internal stage 0–5 onto 5 UI steps (packet+signed collapse to step 3)
    const uiStage = Math.min(readiness.stage, 4);
    return `
      <div class="bd-stepper" style="display:flex;gap:4px;margin-bottom:16px;flex-wrap:wrap">
        ${steps.map((s, i) => {
          const done = uiStage > s.n || (uiStage === 4 && s.n === 4);
          const active = uiStage === s.n || (uiStage >= 4 && s.n === 4);
          const bg = done || active ? 'rgba(16,185,129,0.15)' : 'var(--panel,var(--bg-card,#1e293b))';
          const border = done || active ? '1px solid rgba(16,185,129,0.45)' : '1px solid var(--border)';
          const color = done || active ? '#6ee7b7' : 'var(--muted)';
          return `<div style="flex:1;min-width:72px;text-align:center;padding:8px 6px;border-radius:8px;background:${bg};border:${border}">
            <div style="font-size:10px;font-weight:700;color:${color};letter-spacing:.04em">${i + 1}. ${s.label}</div>
          </div>`;
        }).join('<div style="width:8px;align-self:center;height:2px;background:var(--border);flex-shrink:0"></div>')}
      </div>
      <p style="margin:0 0 14px;font-size:12px;color:var(--muted);line-height:1.45">
        <strong style="color:var(--text)">Bond Desk path:</strong>
        Match defendant → complete cosigner fields → generate paperwork →
        <strong style="color:#6ee7b7">Promote to Active Bonds</strong> when ready.
        Lead Pipeline is optional contact only — not a second bond queue.
      </p>
    `;
  }

  function _renderChecklist(readiness) {
    const pct = readiness.requiredTotal
      ? Math.round((readiness.requiredOk / readiness.requiredTotal) * 100)
      : 0;
    const barColor = readiness.allRequired ? '#10b981' : pct >= 50 ? '#f59e0b' : '#ef4444';
    return `
      <div style="margin-bottom:16px;padding:12px 14px;border-radius:10px;border:1px solid var(--border);background:rgba(0,0,0,.15)">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;flex-wrap:wrap">
          <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--accent)">Packet readiness</div>
          <div style="font-size:12px;font-weight:600;color:${barColor}">${readiness.requiredOk}/${readiness.requiredTotal} required · ${pct}%</div>
        </div>
        <div style="height:6px;border-radius:99px;background:rgba(255,255,255,.06);overflow:hidden;margin-bottom:10px">
          <div style="height:100%;width:${pct}%;background:${barColor};border-radius:99px;transition:width .3s"></div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 12px">
          ${readiness.checks.map(c => {
            const icon = c.ok ? '✅' : (c.required ? '⬜' : '○');
            const col = c.ok ? '#86efac' : (c.required ? 'var(--text)' : 'var(--muted)');
            return `<div style="font-size:12px;color:${col}">${icon} ${c.label}${c.required && !c.ok ? ' *' : ''}</div>`;
          }).join('')}
        </div>
        ${!readiness.allRequired
          ? '<div style="margin-top:10px;font-size:11px;color:#fbbf24">Complete required items (*) before promoting to Active Bonds. You can still open Write Packet to fill gaps.</div>'
          : '<div style="margin-top:10px;font-size:11px;color:#86efac">Required fields look good — generate packet, then Promote to Active Bonds.</div>'}
      </div>
    `;
  }

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
      { label: '🆕 New', value: byStatus.pending ?? 0, color: '#f59e0b' },
      { label: '✍️ Writing', value: byStatus.in_progress ?? 0, color: '#3b82f6' },
      { label: '→ Active', value: byStatus.promoted ?? 0, color: '#10b981' },
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
          <td ondblclick="SLIntake.editCell(this, '${_esc(item.IntakeID)}', 'full_name', '${_esc(item.FullName)}')">
            <strong>${_esc(item.FullName)}</strong>
            ${item.Email ? `<br><span style="font-size:11px;color:var(--muted)">${_esc(item.Email)}</span>` : ''}
          </td>
          <td style="white-space:nowrap" ondblclick="SLIntake.editCell(this, '${_esc(item.IntakeID)}', 'phone', '${_esc(item.Phone)}')">${_esc(item.Phone) || '—'}</td>
          <td ondblclick="SLIntake.editCell(this, '${_esc(item.IntakeID)}', 'defendant_name', '${_esc(item.DefendantName)}')">${_esc(item.DefendantName) || '—'}</td>
          <td>${_esc(item.County) || '—'}</td>
          <td style="font-family:monospace;font-size:12px">${_esc(item.BookingNumber) || '—'}</td>
          <td>${riskBadge}</td>
          <td>${statusBadge}</td>
          <td style="white-space:nowrap">
            <button class="btn-sm btn-primary" onclick="SLIntake.openProcess('${_esc(item.IntakeID)}')" style="font-size:11px;padding:4px 10px" title="Open Bond Desk workflow">Open desk</button>
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
    const readiness = _computeReadiness(h);
    // Stash for promote gate
    h._readiness = readiness;

    const field = (label, value) => `
      <div>
        <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px">${label}</div>
        <div style="font-size:13px;font-weight:500">${_esc(value) || '<span style="color:var(--muted)">—</span>'}</div>
      </div>
    `;

    const confPct = (c) => {
      if (c == null || c === '') return '';
      const n = Number(c);
      if (Number.isNaN(n)) return '';
      return (n <= 1 ? Math.round(n * 100) : Math.round(n)) + '%';
    };
    const matchBadge = h.matched_booking_number
      ? `<span style="background:#065f46;color:#a7f3d0;padding:2px 10px;border-radius:12px;font-size:11px;font-weight:600">MATCHED · ${_esc(h.matched_booking_number)}${h.match_confidence != null ? ' · ' + confPct(h.match_confidence) : ''}</span>`
      : `<span style="background:rgba(245,158,11,.15);color:#fbbf24;padding:2px 10px;border-radius:12px;font-size:11px;font-weight:600">UNMATCHED — run Match</span>`;

    return `
      ${_renderStepper(readiness)}
      ${_renderChecklist(readiness)}

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div style="grid-column:1/-1">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap">
            <span style="font-size:20px">${SOURCE_ICONS[h.source] || '📋'}</span>
            <div>
              <div style="font-weight:600">${_esc(h.source_label || h.source)}</div>
              <div style="font-size:12px;color:var(--muted)">Intake ID: ${_esc(data.intake_id)}</div>
            </div>
            ${matchBadge}
            ${h.consent_given ? '<span style="background:#22c55e;color:#fff;padding:2px 8px;border-radius:12px;font-size:11px">✓ CONSENT</span>' : ''}
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
            <button type="button" class="btn-sm btn-primary" onclick="SLIntake.runMatch()" style="font-size:12px;padding:6px 12px">🔗 Match defendant</button>
            <button type="button" class="btn-sm btn-primary" onclick="SLIntake.writeBondFromIntake()" style="font-size:12px;padding:6px 12px">📄 Write packet</button>
            <button type="button" class="btn-sm" onclick="SLIntake.promoteToBond()" style="font-size:12px;padding:6px 12px;background:linear-gradient(135deg,#065f46,#047857);border:1px solid #10b981;color:#a7f3d0;font-weight:600">☘️ Promote → Active Bonds</button>
          </div>
          <hr style="border-color:var(--border);margin:0 0 12px">
        </div>

        <div style="grid-column:1/-1">
          <h4 style="margin:0 0 10px;font-size:12px;color:var(--accent);text-transform:uppercase;letter-spacing:.5px">Defendant</h4>
        </div>
        ${field('Name', def.name)}
        ${field('Booking #', def.bookingNumber || h.matched_booking_number)}
        ${field('County', def.county)}
        ${field('Facility', def.facility)}
        ${field('Bond Amount', def.bondAmount ? '$' + Number(String(def.bondAmount).replace(/[$,]/g, '')).toLocaleString() : '')}
        ${field('Charges', def.charges)}

        <div style="grid-column:1/-1;margin-top:8px">
          <h4 style="margin:0 0 10px;font-size:12px;color:var(--accent);text-transform:uppercase;letter-spacing:.5px">Indemnitor (cosigner)</h4>
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
          <h4 style="margin:0 0 10px;font-size:12px;color:var(--accent);text-transform:uppercase;letter-spacing:.5px">References (packet autofill)</h4>
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

  // ── Run matching engine from Bond Desk ─────────────────────────────────────
  async function runMatch() {
    if (!_currentIntakeId) {
      if (typeof SL !== 'undefined') SL.toast('⚠️ No intake loaded', 'warn');
      return;
    }
    try {
      if (typeof SL !== 'undefined') SL.toast('🔗 Matching defendant…', 'info');
      const res = await fetch(`/api/intake/${encodeURIComponent(_currentIntakeId)}/match`, { method: 'POST' });
      const data = await res.json();
      if (!data.success) throw new Error(data.error || 'Match failed');

      const best = data.best_match || data.match || null;
      const conf = best && best.confidence != null ? best.confidence : data.confidence;
      const confLabel = (c) => {
        if (c == null || c === '') return '';
        const n = Number(c);
        if (Number.isNaN(n)) return '';
        return ' (' + (n <= 1 ? Math.round(n * 100) : Math.round(n)) + '%)';
      };
      const booking = (best && (best.booking_number || best.Booking_Number)) || data.matched_booking_number || '';
      const name = (best && (best.full_name || best.Full_Name || best.defendant_name)) || '';

      if (booking && _currentHydration) {
        _currentHydration.matched_booking_number = booking;
        _currentHydration.match_confidence = conf;
        if (_currentHydration.defendant && !_currentHydration.defendant.bookingNumber) {
          _currentHydration.defendant.bookingNumber = booking;
        }
      }

      if (typeof SL !== 'undefined') {
        if (data.auto_linked || booking) {
          SL.toast(`✅ Match${data.auto_linked ? ' (auto-linked)' : ''}: ${name || booking}${confLabel(conf)}`, 'success');
        } else if (data.candidates && data.candidates.length) {
          SL.toast(`⚠️ ${data.candidates.length} candidate(s) under threshold — review carefully (human gate)`, 'warn');
        } else {
          SL.toast('No automatic match — set booking # on intake or search Hot Leads', 'warn');
        }
      }

      // Re-open process view with refreshed hydration from API
      await openProcess(_currentIntakeId);
    } catch (err) {
      if (typeof SL !== 'undefined') SL.toast('Match error: ' + err.message, 'error');
    }
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

  // ── Optional: push to Lead Pipeline (legacy contact stages — not Active Bonds) ─
  async function markAsInProgress() {
    if (!_currentIntakeId) {
      if (typeof SL !== 'undefined') SL.toast('⚠️ No intake loaded — re-open the intake first', 'warn');
      return;
    }
    const h = _currentHydration || {};
    const def = h.defendant || {};
    const ind = h.indemnitor || {};

    // Lead Pipeline is pre-desk contact only. Prefer Match → Packet → Promote to Active Bond.
    const stage = prompt(
      'Optional: add to Lead Pipeline (contact stages only).\n' +
      'To write the bond, stay on Bond Desk: Match → Paperwork → Promote to Active Bond.\n\n' +
      '  contacted   — Initial contact made\n' +
      '  negotiating — Actively negotiating terms\n' +
      '  paperwork   — Paperwork in progress\n' +
      '  ready       — Ready for Bond Desk promote\n\n' +
      'Enter stage (default: contacted), or Cancel to stay on Bond Desk:',
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
          SL.toast('🟢 Added to Lead Pipeline (' + finalStage + '). Still use Bond Desk to complete paperwork → Active Bonds.', 'success');
          if (typeof SLProspective !== 'undefined') SLProspective.load();
        }
        load(); // refresh Bond Desk queue
      } else if (res.status === 409) {
        if (typeof SL !== 'undefined') SL.toast('Already on Lead Pipeline (stage: ' + (data.stage || 'unknown') + ')', 'warn');
      } else {
        throw new Error(data.error || 'Failed to add to Lead Pipeline');
      }
    } catch (err) {
      if (typeof SL !== 'undefined') SL.toast('Error: ' + err.message, 'error');
    }
  }

  // ── Promote intake to Active Bond (only after match + people ready) ────────
  async function promoteToBond() {
    if (!_currentIntakeId) {
      if (typeof SL !== 'undefined') SL.toast('⚠️ No intake loaded — re-open the intake first', 'warn');
      return;
    }
    const h = _currentHydration || {};
    const def = h.defendant || {};
    const readiness = h._readiness || _computeReadiness(h);

    // Check for a matched booking number
    const bookingNum = def.bookingNumber || h.matched_booking_number || readiness.booking || '';
    if (!bookingNum) {
      if (typeof SL !== 'undefined') SL.toast('⚠️ No matched booking number — click Match defendant first', 'warn');
      return;
    }

    if (!readiness.allRequired) {
      const missing = readiness.checks.filter(c => c.required && !c.ok).map(c => c.label).join(', ');
      const ok = confirm(
        'Packet readiness is incomplete:\n\n' + missing + '\n\n' +
        'Best practice: complete cosigner/defendant fields and generate paperwork first.\n\n' +
        'Promote to Active Bonds anyway? (creates bond + assigns POA)'
      );
      if (!ok) return;
    } else {
      const ok = confirm(
        'Promote to Active Bonds?\n\n' +
        'Defendant: ' + (readiness.defName || def.name || '—') + '\n' +
        'Indemnitor: ' + (readiness.indName || '—') + '\n' +
        'Booking: ' + bookingNum + '\n\n' +
        'This assigns a POA and moves the case out of Bond Desk.'
      );
      if (!ok) return;
    }

    // Ask for surety selection
    const surety = prompt(
      'Select surety company:\n\n' +
      '  osi      — O\'Shaughnahill Surety & Insurance (preferred FL)\n' +
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
            `☘️ Active bond created — ${data.defendant_name || readiness.defName} · ${String(data.surety || finalSurety).toUpperCase()} · POA ${data.poa_number} · $${Number(data.bond_amount || 0).toLocaleString()}`,
            'success'
          );
          // Jump agent to Active Bonds
          const abBtn = document.querySelector('.sidebar-btn[data-tab="tabActiveBonds"]');
          if (abBtn && typeof SL.switchTab === 'function') {
            setTimeout(() => {
              SL.switchTab(abBtn);
              if (typeof loadActiveBonds === 'function') loadActiveBonds();
              else if (typeof SLActiveBonds !== 'undefined' && SLActiveBonds.load) SLActiveBonds.load();
            }, 400);
          } else if (typeof SLActiveBonds !== 'undefined' && SLActiveBonds.load) {
            SLActiveBonds.load();
          }
        }
        load(); // refresh Bond Desk queue
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
    runMatch,
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
