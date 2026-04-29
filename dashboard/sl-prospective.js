/* ═══════════════════════════════════════════════════════════
   ShamrockLeads — Prospective Bonds Pipeline
   Kanban board for tracking leads from contact to bond
   ═══════════════════════════════════════════════════════════ */

const SLProspective = (() => {
  const API = window.API || '';
  let _data = [];
  let _stage = 'all';
  let _searchTimer = null;
  let _currentBk = null; // currently open detail booking_number
  let _autoReplyConfig = {}; // cached auto-reply config

  // ── Helpers ──
  const $ = id => document.getElementById(id);
  const money = n => '$' + (parseFloat(n)||0).toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0});
  const timeAgo = ts => {
    if (!ts) return '—';
    const d = new Date(ts);
    const s = Math.floor((Date.now()-d.getTime())/1000);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s/60)+'m ago';
    if (s < 86400) return Math.floor(s/3600)+'h ago';
    return Math.floor(s/86400)+'d ago';
  };
  const stageLabel = s => ({contacted:'📞 Contacted',negotiating:'🤝 Negotiating',paperwork:'📄 Paperwork',ready:'✅ Ready'}[s]||s);
  const stageColor = s => ({contacted:'#3b82f6',negotiating:'#f59e0b',paperwork:'#8b5cf6',ready:'#10b981'}[s]||'#6b7280');
  const channelIcon = ch => ({imessage:'💬',sms:'📱',phone:'📞',left_vm:'📞',sent_text_to:'📱',walk_in:'🚶',whatsapp:'💚',note:'📝'}[ch]||'💬');
  const toast = (msg,type) => { if(window.SL?.toast) SL.toast(msg,type); else if(window.showToast) showToast(msg,type); else alert(msg); };

  // ── Load Pipeline Data ──
  async function load() {
    const status = $('prospStatusFilter')?.value || 'active';
    const search = $('prospSearch')?.value || '';
    const p = new URLSearchParams({ status });
    if (_stage !== 'all') p.set('stage', _stage);
    if (search) p.set('search', search);

    try {
      const r = await fetch(`${API}/api/prospective-bonds?${p}`);
      const d = await r.json();
      _data = d.bonds || [];

      // Update KPIs
      const sc = d.stage_counts || {};
      $('prospKpiTotal').textContent = d.total_active || 0;
      $('prospKpiContacted').textContent = sc.contacted || 0;
      $('prospKpiNegotiating').textContent = sc.negotiating || 0;
      $('prospKpiPaperwork').textContent = sc.paperwork || 0;
      $('prospKpiReady').textContent = sc.ready || 0;

      // Update badge
      const badge = $('prospectiveBadge');
      if (badge) badge.textContent = d.total_active || '—';

      renderBoard();
    } catch(e) { console.error('SLProspective.load:', e); }
  }

  // ── Render Kanban Board ──
  function renderBoard() {
    const stages = ['contacted','negotiating','paperwork','ready'];
    stages.forEach(stage => {
      const cards = _data.filter(b => b.stage === stage);
      const col = $('col' + stage.charAt(0).toUpperCase() + stage.slice(1));
      const countEl = $('colCount' + stage.charAt(0).toUpperCase() + stage.slice(1));
      if (countEl) countEl.textContent = cards.length;
      if (!col) return;

      col.innerHTML = cards.length ? cards.map(b => {
        const sc = (b.lead_status||'').toLowerCase();
        const scoreCls = sc==='hot'?'score-hot':sc==='warm'?'score-warm':'score-cold';
        const comms = b.communication_log || [];
        const lastComm = comms.length ? comms[comms.length-1] : null;
        const lastMsg = lastComm ? `${channelIcon(lastComm.channel)} ${timeAgo(lastComm.timestamp)}` : 'No messages';
        const indName = b.indemnitor?.name || '';
        const indRelation = b.indemnitor?.relationship || '';
        const indCallback = b.indemnitor?.callback_phone || '';
        // Count conversation turns
        const turnCount = comms.filter(c => c.channel === 'imessage').length;
        const turnBadge = turnCount > 0 ? `<span class="turn-badge" title="${turnCount} messages">${turnCount}💬</span>` : '';
        // Inbound reply badge
        const hasInbound = comms.some(c => c.direction === 'inbound');
        const hasNewReply = comms.length && comms[comms.length-1].direction === 'inbound';
        const replyBadge = hasNewReply ? '<span class="reply-badge new">🔔 New Reply</span>' :
                           hasInbound ? '<span class="reply-badge">⬅️ Replied</span>' : '';
        return `<div class="pipeline-card ${hasNewReply?'has-new-reply':''}" onclick="SLProspective.openDetail('${b.booking_number}')">
          <div class="pipeline-card-header">
            <span class="pipeline-card-name">${b.defendant_name||'Unknown'}</span>
            <span class="pipeline-card-bond">${money(b.bond_amount)}</span>
          </div>
          <div class="pipeline-card-meta">
            <span>${b.county||'—'} County</span>
            <span class="score-pill ${scoreCls}">${b.lead_score||0}</span>
            ${turnBadge}
          </div>
          ${indName ? `<div class="pipeline-card-ind">👤 ${indName}${indRelation ? ' ('+indRelation+')' : ''}</div>` : ''}
          ${indCallback ? `<div class="pipeline-card-ind" style="font-size:11px">📞 ${indCallback}</div>` : ''}
          <div class="pipeline-card-comm">${replyBadge} ${lastMsg}</div>
          ${lastComm?.message ? `<div class="pipeline-card-preview">${(lastComm.message||'').substring(0,50)}${(lastComm.message||'').length>50?'...':''}</div>` : ''}
          <div class="pipeline-card-notes-row" onclick="event.stopPropagation()">
            <span class="pipeline-notes-label">📝</span>
            <input class="pipeline-notes-input" type="text" placeholder="Quick note..." value="${(window._notesCache&&window._notesCache[b.booking_number]&&window._notesCache[b.booking_number].shamrock_notes)||''}" onchange="SLLifecycle&&SLLifecycle.quickNote('${b.booking_number}',this.value)" />
          </div>
          ${stage==='ready'?'<button class="btn-officialize-sm" onclick="event.stopPropagation();SLProspective.officialize(''+b.booking_number+'')">☘️ Officialize</button>':''}
        </div>`;
      }).join('') : '<div class="pipeline-empty">No leads</div>';
    });
  }

  // ── Track Lead (from Defendant card) ──
  async function trackLead(bk, name, county, bond, charges, score, status) {
    try {
      const r = await fetch(`${API}/api/prospective-bonds`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          booking_number: bk,
          defendant_name: name,
          county: county,
          bond_amount: bond,
          charges: charges,
          lead_score: score,
          lead_status: status,
        })
      });
      const d = await r.json();
      if (d.success) {
        toast(`☘️ ${name} added to pipeline`, 'success');
        const btn = $('trackBtn_' + bk);
        if (btn) { btn.textContent = '📋 In Progress'; btn.disabled = true; btn.classList.add('tracked'); }
        // Update badge
        const badge = $('prospectiveBadge');
        if (badge) { const c = parseInt(badge.textContent)||0; badge.textContent = c+1; }
      } else {
        if (r.status === 409) toast(`Already tracked: ${d.stage||'in progress'}`, 'info');
        else toast(d.error || 'Failed to track', 'error');
      }
    } catch(e) { toast('Network error: '+e.message, 'error'); }
  }

  // ── Stage Filter ──
  function setStage(stage, btn) {
    _stage = stage;
    document.querySelectorAll('#prospStageFilter button').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    load();
  }

  function debounceSearch() { clearTimeout(_searchTimer); _searchTimer = setTimeout(load, 350); }

  // ── Open Detail Panel ──
  function openDetail(bk) {
    _currentBk = bk;
    const bond = _data.find(b => b.booking_number === bk);
    if (!bond) return;

    $('prospDetailPanel').style.display = 'block';
    $('prospDetailTitle').textContent = `📋 ${bond.defendant_name} · ${bond.county} County`;

    const ind = bond.indemnitor || {};
    const comms = bond.communication_log || [];
    const timeline = bond.timeline || [];

    $('prospDetailBody').innerHTML = `
      <!-- Stage Progress -->
      <div class="prosp-stage-bar">
        ${['contacted','negotiating','paperwork','ready'].map(s =>
          `<div class="prosp-stage-dot ${bond.stage===s?'active':''}" style="--dot-color:${stageColor(s)}" onclick="SLProspective.promptStage('${bk}','${s}')">${stageLabel(s)}</div>`
        ).join('<div class="prosp-stage-line"></div>')}
      </div>

      <!-- Two columns: Defendant + Indemnitor -->
      <div class="prosp-detail-grid">
        <div class="prosp-info-card">
          <h4>👤 Defendant</h4>
          <div class="prosp-field"><span>Name</span><span>${bond.defendant_name||'—'}</span></div>
          <div class="prosp-field"><span>Booking #</span><span class="mono">${bond.booking_number||'—'}</span></div>
          <div class="prosp-field"><span>County</span><span>${bond.county||'—'}</span></div>
          <div class="prosp-field"><span>Bond</span><span style="font-weight:700;color:var(--success)">${money(bond.bond_amount)}</span></div>
          <div class="prosp-field"><span>Charges</span><span style="font-size:11px">${bond.charges||'—'}</span></div>
          <div class="prosp-field"><span>Score</span><span>${bond.lead_score||0} ${bond.lead_status||''}</span></div>
        </div>
        <div class="prosp-info-card">
          <h4>🤝 Indemnitor / Cosigner</h4>
          <div class="prosp-field"><span>Name</span><input id="indName" value="${ind.name||''}" placeholder="Full name"></div>
          <div class="prosp-field"><span>Phone</span><input id="indPhone" value="${ind.phone||''}" placeholder="(239) 555-0000"></div>
          <div class="prosp-field"><span>Email</span><input id="indEmail" value="${ind.email||''}" placeholder="email@example.com"></div>
          <div class="prosp-field"><span>Relationship</span><input id="indRelation" value="${ind.relationship||''}" placeholder="e.g. Mother"></div>
          <button class="btn-save-ind" onclick="SLProspective.saveIndemnitor('${bk}')">💾 Save Indemnitor</button>
        </div>
      </div>

      <!-- Communication / Messaging -->
      <div class="prosp-comm-section">
        <h4>📱 Messages & Notes</h4>
        <div class="prosp-comm-timeline" id="commTimeline">
          ${comms.length ? comms.map((c,ci) => {
            const isOut = c.direction === 'outbound';
            const isAuto = c.agent === 'auto_reply';
            const intentBadge = c.intent ? `<span class="intent-badge intent-${c.intent}">${c.intent}</span>` : '';
            const agentLabel = isAuto ? '🤖 Auto-reply' : (c.agent || '—');
            // Message action buttons for outbound iMessages
            const msgActions = (isOut && c.channel === 'imessage') ? `
              <div class="comm-actions">
                <button class="comm-action-btn" title="Unsend" onclick="event.stopPropagation();SLProspective.unsendMsg('${bk}',${ci})">🚫</button>
                <button class="comm-action-btn" title="Edit" onclick="event.stopPropagation();SLProspective.editMsg('${bk}',${ci})">✏️</button>
                <button class="comm-action-btn" title="React" onclick="event.stopPropagation();SLProspective.reactMsg('${bk}',${ci})">❤️</button>
              </div>` : '';
            return `
            <div class="comm-bubble ${isOut?'outbound':'inbound'} ${isAuto?'auto-reply':''}">
              <div class="comm-bubble-header">
                <span>${channelIcon(c.channel)} ${c.channel} · ${agentLabel} ${intentBadge}</span>
                <span>${timeAgo(c.timestamp)}</span>
              </div>
              <div class="comm-bubble-body">${c.message||''}</div>
              ${c.to_number?`<div class="comm-bubble-meta">→ ${c.to_number}</div>`:''}
              ${msgActions}
            </div>`;
          }).join('') : '<div class="pipeline-empty" style="padding:16px">No messages yet</div>'}
        </div>

        <!-- Send Message -->
        <div class="prosp-send-bar">
          <select id="commChannel">
            <option value="imessage">💬 iMessage</option>
            <option value="sms">📱 SMS</option>
            <option value="phone">📞 Phone Call</option>
            <option value="left_vm">📞 Left VM</option>
            <option value="sent_text_to">📱 Sent Text To...</option>
            <option value="walk_in">🚶 Walk-In</option>
            <option value="note">📝 Note</option>
          </select>
          <select id="commTemplate">
            <option value="">Custom message...</option>
            <option value="intro">Intro: Hi, this is Shamrock Bail...</option>
            <option value="followup">Follow-up: Just checking in...</option>
            <option value="urgent">Urgent: We can help get them out today...</option>
          </select>
          <select id="commEffect">
            <option value="">No effect</option>
            <option value="slam">💥 Slam</option>
            <option value="loud">📢 Loud</option>
            <option value="gentle">🤫 Gentle</option>
            <option value="invisible_ink">👻 Invisible Ink</option>
            <option value="confetti">🎊 Confetti</option>
            <option value="fireworks">🎆 Fireworks</option>
          </select>
          <textarea id="commMessage" placeholder="Type a message or note..." rows="2"></textarea>
          <div style="display:flex;gap:8px">
            <button class="btn-send-msg" onclick="SLProspective.sendMessage('${bk}')">📱 Send</button>
            <button class="btn-add-note" onclick="SLProspective.addNote('${bk}')">📝 Add Note</button>
          </div>
        </div>
      </div>

      <!-- Timeline -->
      <div class="prosp-timeline-section">
        <h4>📅 Timeline</h4>
        <div class="prosp-timeline">
          ${timeline.slice().reverse().map(t => `
            <div class="timeline-entry">
              <div class="timeline-dot" style="background:${stageColor(t.new_stage||'contacted')}"></div>
              <div class="timeline-content">
                <span class="timeline-event">${t.event}</span>
                <span class="timeline-detail">${t.detail||''}</span>
                <span class="timeline-time">${timeAgo(t.timestamp)} · ${t.agent||''}</span>
              </div>
            </div>
          `).join('')}
        </div>
      </div>

      <!-- Action Buttons -->
      <div class="prosp-actions">
        <button class="btn-close-lead" onclick="SLProspective.promptClose('${bk}')">❌ Close Lead</button>
        <div class="prosp-stage-select">
          <label>Stage:</label>
          <select onchange="SLProspective.promptStage('${bk}',this.value)">
            ${['contacted','negotiating','paperwork','ready'].map(s =>
              `<option value="${s}" ${bond.stage===s?'selected':''}>${stageLabel(s)}</option>`
            ).join('')}
          </select>
        </div>
        <button class="btn-officialize" onclick="SLProspective.officialize('${bk}')">☘️ Officialize Bond</button>
      </div>
    `;

    // Scroll into view
    $('prospDetailPanel').scrollIntoView({behavior:'smooth', block:'start'});
  }

  function closeDetail() {
    $('prospDetailPanel').style.display = 'none';
    _currentBk = null;
  }

  // ── Prompt for stage change with note ──
  function promptStage(bk, newStage) {
    const bond = _data.find(b => b.booking_number === bk);
    if (!bond || bond.stage === newStage) return;

    const note = prompt(`Moving to "${stageLabel(newStage)}".\n\nAdd a note about this case:`);
    if (note === null) return; // cancelled

    updateStage(bk, newStage, note);
  }

  async function updateStage(bk, stage, note) {
    try {
      const r = await fetch(`${API}/api/prospective-bonds/${encodeURIComponent(bk)}/stage`, {
        method: 'PATCH',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ stage, note })
      });
      const d = await r.json();
      if (d.success) {
        toast(`Stage → ${stageLabel(stage)}`, 'success');
        load();
        if (_currentBk === bk) setTimeout(() => openDetail(bk), 300);
      } else toast(d.error||'Failed', 'error');
    } catch(e) { toast('Error: '+e.message, 'error'); }
  }

  // ── Save Indemnitor ──
  async function saveIndemnitor(bk) {
    const data = {
      name: $('indName')?.value || '',
      phone: $('indPhone')?.value || '',
      email: $('indEmail')?.value || '',
      relationship: $('indRelation')?.value || '',
    };
    try {
      const r = await fetch(`${API}/api/prospective-bonds/${encodeURIComponent(bk)}/indemnitor`, {
        method: 'PATCH',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(data)
      });
      const d = await r.json();
      if (d.success) { toast('Indemnitor saved', 'success'); load(); }
      else toast(d.error||'Failed', 'error');
    } catch(e) { toast('Error: '+e.message, 'error'); }
  }

  // ── Send Message via BlueBubbles ──
  async function sendMessage(bk) {
    const bond = _data.find(b => b.booking_number === bk);
    const phone = $('indPhone')?.value || bond?.indemnitor?.phone || '';
    const channel = $('commChannel')?.value || 'imessage';
    let message = $('commMessage')?.value || '';
    const template = $('commTemplate')?.value || '';

    // Apply template if selected
    if (template && !message) {
      const name = bond?.defendant_name || 'your loved one';
      const templates = {
        intro: `Hi, this is Brendan with Shamrock Bail Bonds. I'm reaching out about ${name}. We can help get them released quickly. Would you like to discuss the process?`,
        followup: `Hi, just checking in regarding ${name}. We're here to help whenever you're ready. Feel free to call or text us anytime.`,
        urgent: `Hi, we can help get ${name} released today. Our process is fast and easy — we handle everything. Ready to get started?`,
      };
      message = templates[template] || '';
      if ($('commMessage')) $('commMessage').value = message;
    }

    if (!message) { toast('Enter a message', 'error'); return; }

    // If it's an iMessage/SMS channel and we have a phone, send via BlueBubbles
    if (['imessage','sms'].includes(channel) && phone) {
      try {
        const effect = $('commEffect')?.value || '';
        const sendBody = {
          phone: phone,
          message: message,
          from_number: '2399550178',
          booking_number: bk,
          defendant_name: bond?.defendant_name || '',
          county: bond?.county || '',
          recipient_label: bond?.indemnitor?.name || 'Indemnitor',
          agent_name: 'Brendan',
        };

        // Use effect endpoint if an effect is selected
        const endpoint = effect ? `${API}/api/imessage/send-effect` : `${API}/api/imessage/send`;
        if (effect) sendBody.effect = effect;

        const r = await fetch(endpoint, {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify(sendBody)
        });
        const d = await r.json();

        // Handle dedup 409
        if (r.status === 409 && d.error === 'duplicate') {
          const forceSend = confirm(
            `⚠️ Already messaged this lead within 24h.\n\nLast sent: ${d.last_sent || 'recently'}\nPreview: "${d.message_preview || ''}"\n\nSend anyway?`
          );
          if (forceSend) {
            sendBody.force_send = true;
            const r2 = await fetch(`${API}/api/imessage/send`, {
              method: 'POST',
              headers: {'Content-Type':'application/json'},
              body: JSON.stringify(sendBody)
            });
            const d2 = await r2.json();
            if (!d2.success) { toast('Force send failed: '+(d2.error||''), 'error'); return; }
          } else return;
        } else if (!d.success) {
          toast('BB send failed: '+(d.error||''), 'error'); return;
        }
      } catch(e) { toast('BB error: '+e.message, 'error'); return; }
    }

    // Log the communication
    try {
      await fetch(`${API}/api/prospective-bonds/${encodeURIComponent(bk)}/note`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          message: message,
          channel: channel,
          direction: 'outbound',
          to_number: phone,
          from_number: '2399550178',
        })
      });
      toast('Message sent & logged', 'success');
      if ($('commMessage')) $('commMessage').value = '';
      if ($('commTemplate')) $('commTemplate').value = '';
      load();
      if (_currentBk === bk) setTimeout(() => openDetail(bk), 300);
    } catch(e) { toast('Log error: '+e.message, 'error'); }
  }

  // ── Add Note ──
  async function addNote(bk) {
    const note = $('commMessage')?.value || '';
    if (!note) { toast('Enter a note', 'error'); return; }
    try {
      await fetch(`${API}/api/prospective-bonds/${encodeURIComponent(bk)}/note`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ note, channel: 'note' })
      });
      toast('Note added', 'success');
      if ($('commMessage')) $('commMessage').value = '';
      load();
      if (_currentBk === bk) setTimeout(() => openDetail(bk), 300);
    } catch(e) { toast('Error: '+e.message, 'error'); }
  }

  // ── Close Lead ──
  function promptClose(bk) {
    const bond = _data.find(b => b.booking_number === bk);
    const outcomes = ['lost_to_competitor','released_ror','no_contact','declined','left_vm','sent_text_to','other'];
    const choice = prompt(
      `Close "${bond?.defendant_name}"?\n\nSelect outcome:\n` +
      outcomes.map((o,i) => `${i+1}. ${o.replace(/_/g,' ')}`).join('\n') +
      '\n\nEnter number (1-7):'
    );
    if (!choice) return;
    const idx = parseInt(choice) - 1;
    if (idx < 0 || idx >= outcomes.length) { toast('Invalid choice', 'error'); return; }

    const note = prompt('Add a closing note (optional):') || '';
    closeLead(bk, outcomes[idx], note);
  }

  async function closeLead(bk, outcome, note) {
    try {
      const r = await fetch(`${API}/api/prospective-bonds/${encodeURIComponent(bk)}/close`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ outcome, note })
      });
      const d = await r.json();
      if (d.success) { toast(`Lead closed: ${outcome.replace(/_/g,' ')}`, 'success'); closeDetail(); load(); }
      else toast(d.error||'Failed', 'error');
    } catch(e) { toast('Error: '+e.message, 'error'); }
  }

  // ── Officialize Bond ──
  async function officialize(bk) {
    const bond = _data.find(b => b.booking_number === bk);
    if (!bond) return;
    if (!confirm(`Officialize bond for ${bond.defendant_name}?\n\nThis will promote to Active Bond and open the Write Bond modal.`)) return;

    try {
      const r = await fetch(`${API}/api/prospective-bonds/${encodeURIComponent(bk)}/officialize`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
      });
      const d = await r.json();
      if (d.success) {
        toast(`🎉 Bond officialized for ${bond.defendant_name}`, 'success');
        closeDetail();
        load();
        // Open Write Bond modal pre-filled
        if (window.openBondModal && d.defendant) {
          setTimeout(() => openBondModal(d.defendant, d.defendant.bond_amount, d.defendant.county, bk), 500);
        }
      } else toast(d.error||'Failed', 'error');
    } catch(e) { toast('Error: '+e.message, 'error'); }
  }

  // ── Auto-Reply Settings ──
  async function loadAutoReplyConfig() {
    try {
      const r = await fetch(`${API}/api/imessage/auto-reply/config`);
      _autoReplyConfig = await r.json();
      renderAutoReplyPanel();
    } catch(e) { console.warn('Auto-reply config load failed:', e); }
  }

  function renderAutoReplyPanel() {
    const panel = $('autoReplyPanel');
    if (!panel) return;
    const cfg = _autoReplyConfig;
    const isLive = cfg.enabled;
    // Auto-expand when agent is live
    if (isLive && !panel.classList.contains('expanded')) panel.classList.add('expanded');

    panel.innerHTML = `
      <div class="auto-reply-header ${isLive ? 'agent-live' : ''}" onclick="this.parentElement.classList.toggle('expanded')">
        <span style="display:flex;align-items:center;gap:8px">
          🤖 AI Outreach Agent
          ${isLive
            ? '<span class="ar-status on"><span class="ar-pulse"></span>LIVE</span>'
            : '<span class="ar-status off">OFF</span>'}
          ${isLive && cfg.ai_enabled ? '<span class="ar-mode-badge">GPT-4o</span>' : ''}
          ${isLive && cfg.conversational_mode !== false ? '<span class="ar-mode-badge">Multi-Turn</span>' : ''}
          ${cfg.last_poll_at ? '<span style="font-size:10px;color:var(--text-muted);font-weight:400">Polled ' + timeAgo(cfg.last_poll_at) + '</span>' : ''}
        </span>
        <span style="display:flex;align-items:center;gap:8px">
          <label class="ar-switch" onclick="event.stopPropagation()">
            <input type="checkbox" ${cfg.enabled?'checked':''} onchange="SLProspective.updateAutoReply({enabled:this.checked})">
            <span class="ar-slider"></span>
          </label>
          <span class="ar-toggle-arrow">▼</span>
        </span>
      </div>
      <div class="auto-reply-body">
        <div class="ar-grid">
          <div class="ar-row">
            <label>AI-Powered (GPT-4o)</label>
            <input type="checkbox" id="arAI" ${cfg.ai_enabled?'checked':''} onchange="SLProspective.updateAutoReply({ai_enabled:this.checked})">
          </div>
          <div class="ar-row">
            <label>Conversational Mode</label>
            <div style="display:flex;align-items:center;gap:6px">
              <input type="checkbox" id="arConvo" ${cfg.conversational_mode!==false?'checked':''} onchange="SLProspective.updateAutoReply({conversational_mode:this.checked})">
              <small style="color:var(--text-muted);font-size:10px">Keeps talking & gathering info</small>
            </div>
          </div>
          <div class="ar-row">
            <label>Simulate Typing</label>
            <input type="checkbox" id="arTyping" ${cfg.simulate_typing?'checked':''} onchange="SLProspective.updateAutoReply({simulate_typing:this.checked})">
          </div>
          <div class="ar-row">
            <label>Auto Mark Read</label>
            <input type="checkbox" id="arMarkRead" ${cfg.auto_mark_read?'checked':''} onchange="SLProspective.updateAutoReply({auto_mark_read:this.checked})">
          </div>
          <div class="ar-row">
            <label>Auto ❤️ Interested</label>
            <input type="checkbox" id="arReact" ${cfg.auto_react_interested?'checked':''} onchange="SLProspective.updateAutoReply({auto_react_interested:this.checked})">
          </div>
          <div class="ar-row">
            <label>Reply Cooldown</label>
            <div style="display:flex;align-items:center;gap:6px">
              <input type="number" id="arCooldown" value="${cfg.cooldown_minutes||5}" min="1" max="120" style="width:60px" onchange="SLProspective.updateAutoReply({cooldown_minutes:parseInt(this.value)})">
              <span style="font-size:11px;color:var(--text-muted)">min</span>
            </div>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:12px;margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
          <button class="btn-poll" onclick="SLProspective.manualPoll()">🔄 Poll Inbox Now</button>
          <span style="font-size:11px;color:var(--text-muted)">
            Last: ${cfg.last_poll_at ? timeAgo(cfg.last_poll_at) : 'never'} · Every ${cfg.poll_interval_seconds||30}s
          </span>
        </div>
      </div>
    `;
  }

  async function updateAutoReply(updates) {
    try {
      const r = await fetch(`${API}/api/imessage/auto-reply/config`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(updates)
      });
      const d = await r.json();
      if (d.success) {
        _autoReplyConfig = d.config;
        renderAutoReplyPanel();
        toast('Auto-reply updated', 'success');
      }
    } catch(e) { toast('Update failed: '+e.message, 'error'); }
  }

  async function manualPoll() {
    try {
      toast('Polling inbox...', 'info');
      const r = await fetch(`${API}/api/imessage/inbox/poll`, { method: 'POST' });
      const d = await r.json();
      toast(`Poll: ${d.matched||0} matched, ${d.replied||0} replied`, 'success');
      loadAutoReplyConfig();
      load();
    } catch(e) { toast('Poll failed: '+e.message, 'error'); }
  }

  // ── Message Actions (Unsend, Edit, React) ──
  async function unsendMsg(bk, commIdx) {
    const bond = _data.find(b => b.booking_number === bk);
    const comm = bond?.communication_log?.[commIdx];
    if (!comm) return;
    if (!confirm('Unsend this message? It will be retracted from the recipient.')) return;

    // We need the BB message GUID — look it up from outreach log
    try {
      const r = await fetch(`${API}/api/imessage/unsend`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ message_guid: comm.bb_message_guid || '' })
      });
      const d = await r.json();
      if (d.success) { toast('Message unsent', 'success'); setTimeout(() => openDetail(bk), 300); }
      else toast('Unsend failed: '+(d.error||d.message||''), 'error');
    } catch(e) { toast('Error: '+e.message, 'error'); }
  }

  async function editMsg(bk, commIdx) {
    const bond = _data.find(b => b.booking_number === bk);
    const comm = bond?.communication_log?.[commIdx];
    if (!comm) return;
    const newText = prompt('Edit message:', comm.message || '');
    if (!newText || newText === comm.message) return;

    try {
      const r = await fetch(`${API}/api/imessage/edit`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ message_guid: comm.bb_message_guid || '', new_text: newText })
      });
      const d = await r.json();
      if (d.success) { toast('Message edited', 'success'); setTimeout(() => openDetail(bk), 300); }
      else toast('Edit failed: '+(d.error||d.message||''), 'error');
    } catch(e) { toast('Error: '+e.message, 'error'); }
  }

  async function reactMsg(bk, commIdx) {
    const bond = _data.find(b => b.booking_number === bk);
    const comm = bond?.communication_log?.[commIdx];
    if (!comm) return;
    const phone = bond?.indemnitor?.phone || '';
    if (!phone) { toast('No phone number for chat', 'error'); return; }

    const reactions = ['love','like','dislike','laugh','emphasize','question'];
    const emojis = ['❤️','👍','👎','😂','❗','❓'];
    const choice = prompt(
      'React with:\n' + reactions.map((r,i) => `${i+1}. ${emojis[i]} ${r}`).join('\n') + '\n\nEnter number (1-6):'
    );
    if (!choice) return;
    const idx = parseInt(choice) - 1;
    if (idx < 0 || idx >= reactions.length) return;

    try {
      const chatGuid = `iMessage;-;${phone.startsWith('+') ? phone : '+1' + phone.replace(/\D/g,'')}`;
      const r = await fetch(`${API}/api/imessage/react`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          chat_guid: chatGuid,
          message_guid: comm.bb_message_guid || '',
          reaction: reactions[idx]
        })
      });
      const d = await r.json();
      if (d.success) toast(`${emojis[idx]} Reaction sent`, 'success');
      else toast('React failed: '+(d.error||''), 'error');
    } catch(e) { toast('Error: '+e.message, 'error'); }
  }

  // Load auto-reply config on init
  setTimeout(loadAutoReplyConfig, 1000);

  // Public API
  return {
    load, trackLead, setStage, debounceSearch, openDetail, closeDetail,
    promptStage, saveIndemnitor, sendMessage, addNote, promptClose, officialize,
    // New: auto-reply + message actions
    loadAutoReplyConfig, updateAutoReply, manualPoll,
    unsendMsg, editMsg, reactMsg,
  };
})();
