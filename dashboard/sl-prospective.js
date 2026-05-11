/* ═══════════════════════════════════════════════════════════════════════════
   ShamrockLeads — Outreach Pipeline (Fortune 50 CRM)
   sl-prospective.js  ·  Complete rewrite

   Features:
   ─ Manual lead add (manual entry / from arrest record / from intake queue)
   ─ 7-metric KPI row with pipeline value and new-reply counter
   ─ Kanban board with per-column add buttons
   ─ Card selection + bulk actions (advance stage, bulk message, bulk sequence, bulk close)
   ─ Slide-in detail drawer with prev/next navigation
   ─ Full conversation thread (iMessage bubbles, inbound/outbound, reactions)
   ─ AI response suggestions (GPT-4o via /api/agent-brain/suggest)
   ─ Outreach sequence controls (start / stop / view schedule)
   ─ Contact discovery integration (/api/contacts/discover)
   ─ Stage advance with animated flow arrows
   ─ Sort: latest activity, highest score, highest bond, newest, replies-first
   ─ Replies-only filter
   ─ Export to CSV
   ─ SSE live updates (new_reply, sms_received, message_received)
   ─ Keyboard shortcuts: Escape = close drawer, N = new lead
   ═══════════════════════════════════════════════════════════════════════════ */
window.SLProspective = (function () {
  'use strict';

  const API = window.API_BASE || window.API || '';

  // ── State ──────────────────────────────────────────────────────────────────
  let _data = [];
  let _stage = 'all';
  let _searchTimer = null;
  let _currentBk = null;
  let _currentIdx = null;
  let _autoReplyConfig = {};
  let _selectedBks = new Set();
  let _addSource = 'manual';

  // ── Helpers ────────────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);
  const money = n => '$' + (parseFloat(n) || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  const toast = (msg, type) => { if (window.SL && window.SL.toast) SL.toast(msg, type); else if (window.showToast) showToast(msg, type); };

  const timeAgo = ts => {
    if (!ts) return '—';
    const s = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  };

  const stageLabel = s => ({ contacted: '📞 Contacted', negotiating: '🤝 Negotiating', paperwork: '📄 Paperwork', ready: '✅ Ready' }[s] || s);
  const stageColor = s => ({ contacted: '#3b82f6', negotiating: '#f59e0b', paperwork: '#8b5cf6', ready: '#10b981' }[s] || '#6b7280');
  const channelIcon = ch => ({ imessage: '💬', sms: '📱', phone: '📞', left_vm: '📞', sent_text_to: '📱', walk_in: '🚶', whatsapp: '💚', note: '📝', email: '📧' }[ch] || '💬');
  const nextStage = s => ({ contacted: 'negotiating', negotiating: 'paperwork', paperwork: 'ready', ready: null }[s]);

  // ── Load Pipeline Data ─────────────────────────────────────────────────────
  let _showArchived = false;

  async function load() {
    const status = _showArchived ? 'archived' : ($('prospStatusFilter') ? $('prospStatusFilter').value : 'active');
    const search = $('prospSearch') ? $('prospSearch').value : '';
    const sort = $('prospSortFilter') ? $('prospSortFilter').value : 'updated_desc';
    const repliesOnly = $('prospShowReplies') ? $('prospShowReplies').checked : false;
    const p = new URLSearchParams({ status });
    if (_stage !== 'all') p.set('stage', _stage);
    if (search) p.set('search', search);
    if (_showArchived) p.set('show_archived', 'true');

    try {
      const r = await fetch(API + '/api/prospective-bonds?' + p);
      const d = await r.json();
      let bonds = d.bonds || [];
      bonds = sortBonds(bonds, sort);
      if (repliesOnly) {
        bonds = bonds.filter(function(b) {
          return (b.communication_log || []).some(function(c) { return c.direction === 'inbound'; });
        });
      }
      _data = bonds;
      const sc = d.stage_counts || {};
      updateKpis(d, sc, bonds);
      const badge = $('prospectiveBadge');
      if (badge) badge.textContent = d.total_active || '—';
      renderBoard();
      updateBulkBar();
      const clr = $('prospSearchClear');
      if (clr) clr.style.display = search ? 'flex' : 'none';
      // Update archived badge
      var archBadge = $('prospArchivedBadge');
      if (archBadge) {
        var ac = d.archived_count || 0;
        archBadge.textContent = ac;
        archBadge.style.display = ac > 0 ? 'inline-flex' : 'none';
      }
      var archBtn = $('prospArchiveToggle');
      if (archBtn) archBtn.classList.toggle('active', _showArchived);
    } catch (e) {
      console.error('SLProspective.load:', e);
    }
  }

  function sortBonds(bonds, sort) {
    return bonds.slice().sort(function(a, b) {
      if (sort === 'score_desc') return (b.lead_score || 0) - (a.lead_score || 0);
      if (sort === 'bond_desc') return (b.bond_amount || 0) - (a.bond_amount || 0);
      if (sort === 'created_desc') return new Date(b.created_at || 0) - new Date(a.created_at || 0);
      if (sort === 'replies_first') {
        var aNew = (a.communication_log || []).some(function(c) { return c.direction === 'inbound' && !c.read; });
        var bNew = (b.communication_log || []).some(function(c) { return c.direction === 'inbound' && !c.read; });
        if (bNew && !aNew) return 1;
        if (aNew && !bNew) return -1;
        return new Date(b.updated_at || 0) - new Date(a.updated_at || 0);
      }
      return new Date(b.updated_at || 0) - new Date(a.updated_at || 0);
    });
  }

  function updateKpis(d, sc, bonds) {
    var setKpi = function(id, val) {
      var el = $(id);
      if (!el) return;
      el.textContent = val;
    };
    setKpi('prospKpiTotal', d.total_active || 0);
    setKpi('prospKpiContacted', sc.contacted || 0);
    setKpi('prospKpiNegotiating', sc.negotiating || 0);
    setKpi('prospKpiPaperwork', sc.paperwork || 0);
    setKpi('prospKpiReady', sc.ready || 0);
    var replies = bonds.filter(function(b) {
      var comms = b.communication_log || [];
      return comms.length && comms[comms.length - 1].direction === 'inbound';
    }).length;
    var repliesEl = $('prospKpiReplies');
    if (repliesEl) repliesEl.textContent = replies || '0';
    var totalValue = bonds.reduce(function(s, b) { return s + (b.bond_amount || 0); }, 0);
    var valEl = $('prospKpiValue');
    if (valEl) {
      if (totalValue >= 1000000) valEl.textContent = '$' + (totalValue / 1000000).toFixed(1) + 'M';
      else if (totalValue >= 1000) valEl.textContent = '$' + (totalValue / 1000).toFixed(0) + 'K';
      else valEl.textContent = money(totalValue);
    }
  }

  // ── Render Kanban Board ────────────────────────────────────────────────────
  function renderBoard() {
    ['contacted', 'negotiating', 'paperwork', 'ready'].forEach(function(stage) {
      var cards = _data.filter(function(b) { return b.stage === stage; });
      var cap = stage.charAt(0).toUpperCase() + stage.slice(1);
      var col = $('col' + cap);
      var countEl = $('colCount' + cap);
      if (countEl) countEl.textContent = cards.length;
      if (!col) return;
      if (!cards.length) {
        col.innerHTML = '<div class="pipeline-empty">No leads<br><button class="pcol-empty-add" onclick="SLProspective.openAddModal(\'' + stage + '\')">＋ Add one</button></div>';
        return;
      }
      col.innerHTML = cards.map(function(b) { return renderCard(b); }).join('');
    });
  }

  // ── Risk tier inline badge (NLP/COMPAS data + FTA intelligence) ────────────
  function riskBadge(b) {
    var badges = '';
    // NLP/COMPAS risk tier
    var tier = (b.nlp_risk_tier || b.risk_tier || '').toLowerCase();
    if (tier) {
      var colors = { critical: '#ef4444', high: '#f59e0b', medium: '#3b82f6', low: '#10b981' };
      var icons = { critical: '🔴', high: '🟡', medium: '🔵', low: '🟢' };
      var c = colors[tier] || '#6b7280';
      badges += '<span style="font-size:10px;font-weight:600;padding:1px 6px;border-radius:4px;background:' + c + '22;color:' + c + ';margin-left:4px">' + (icons[tier] || '') + ' ' + tier.toUpperCase() + '</span>';
    }
    // FTA risk intelligence
    var ftaLvl = (b.fta_risk_level || '').toLowerCase();
    var ftaScore = b.fta_risk_score;
    if (ftaLvl && ftaScore != null) {
      var fc = { critical: '#ff4444', high: '#ff8800', moderate: '#ffcc00', low: '#44bb44' }[ftaLvl] || '#888';
      var fi = { critical: '🔴', high: '🟠', moderate: '🟡', low: '🟢' }[ftaLvl] || '⚪';
      badges += '<span style="font-size:10px;font-weight:600;padding:1px 6px;border-radius:4px;background:' + fc + '22;color:' + fc + ';margin-left:4px" title="FTA Risk: ' + ftaScore + '/100">' + fi + ' FTA ' + ftaLvl.charAt(0).toUpperCase() + ftaLvl.slice(1) + '</span>';
    }
    return badges;
  }

  function renderCard(b) {
    var bk = b.booking_number;
    var sc = (b.lead_status || '').toLowerCase();
    var scoreCls = sc === 'hot' ? 'score-hot' : sc === 'warm' ? 'score-warm' : 'score-cold';
    var comms = b.communication_log || [];
    var lastComm = comms.length ? comms[comms.length - 1] : null;
    var lastMsg = lastComm ? channelIcon(lastComm.channel) + ' ' + timeAgo(lastComm.timestamp) : 'No messages';
    var indName = (b.indemnitor && b.indemnitor.name) || '';
    var indPhone = (b.indemnitor && (b.indemnitor.phone || b.indemnitor.callback_phone)) || '';
    var turnCount = comms.filter(function(c) { return c.channel === 'imessage' || c.channel === 'sms'; }).length;
    var hasNewReply = comms.length && comms[comms.length - 1].direction === 'inbound';
    var hasInbound = comms.some(function(c) { return c.direction === 'inbound'; });
    var isSelected = _selectedBks.has(bk);
    var ns = nextStage(b.stage);
    var seqStatus = b.sequence_status || '';
    var seqBadge = seqStatus === 'active' ? '<span class="seq-badge seq-active">🤖 Sequence Active</span>' :
                   seqStatus === 'stopped' ? '<span class="seq-badge seq-stopped">⏹ Stopped</span>' : '';
    var advBtn = ns
      ? '<button class="cqa-btn cqa-advance" title="Advance to ' + stageLabel(ns) + '" onclick="event.stopPropagation();SLProspective.quickAdvance(\'' + bk + '\',\'' + ns + '\')">→ ' + ns.charAt(0).toUpperCase() + ns.slice(1) + '</button>'
      : '<button class="cqa-btn cqa-officialize" onclick="event.stopPropagation();SLProspective.officialize(\'' + bk + '\')">☘️ Officialize</button>';

    return '<div class="pipeline-card' + (hasNewReply ? ' has-new-reply' : '') + (isSelected ? ' card-selected' : '') + '" data-bk="' + bk + '" data-stage="' + b.stage + '">' +
      '<div class="card-select-wrap"><input type="checkbox" class="card-checkbox"' + (isSelected ? ' checked' : '') + ' onchange="SLProspective.toggleSelect(\'' + bk + '\',this.checked)" onclick="event.stopPropagation()"></div>' +
      '<div class="card-main" onclick="SLProspective.openDetail(\'' + bk + '\')">' +
        '<div class="pipeline-card-header"><span class="pipeline-card-name">' + (b.defendant_name || 'Unknown') + '</span><span class="pipeline-card-bond">' + money(b.bond_amount) + '</span></div>' +
        '<div class="pipeline-card-meta"><span>' + (b.county || '—') + ' County</span><span class="score-pill ' + scoreCls + '">' + (b.lead_score || 0) + '</span>' + (turnCount > 0 ? '<span class="turn-badge">' + turnCount + '💬</span>' : '') + riskBadge(b) + '</div>' +
        (indName ? '<div class="pipeline-card-ind">👤 ' + indName + '</div>' : '') +
        (indPhone ? '<div class="pipeline-card-ind" style="font-size:11px;color:var(--muted)">📞 ' + indPhone + '</div>' : '') +
        seqBadge +
        '<div class="pipeline-card-comm">' + (hasNewReply ? '<span class="reply-badge new">🔔 New Reply</span>' : hasInbound ? '<span class="reply-badge">⬅️ Replied</span>' : '') + ' ' + lastMsg + '</div>' +
        (lastComm && lastComm.message ? '<div class="pipeline-card-preview">"' + (lastComm.message || '').substring(0, 55) + ((lastComm.message || '').length > 55 ? '…' : '') + '"</div>' : '') +
      '</div>' +
      '<div class="card-quick-actions">' +
        (indPhone ? '<button class="cqa-btn cqa-msg" title="Send iMessage" onclick="event.stopPropagation();SLProspective.quickMessage(\'' + bk + '\')">💬 Msg</button>' : '') +
        advBtn +
        '<button class="cqa-btn cqa-intel" title="AI Intelligence" onclick="event.stopPropagation();SLProspective.showIntel(\'' + bk + '\')">🧠 Intel</button>' +
        '<button class="cqa-btn cqa-view-def" title="View in Defendants tab" onclick="event.stopPropagation();SLProspective.viewInDefendants(\'' + bk + '\')">👤</button>' +
        (b.status === 'archived'
          ? '<button class="cqa-btn cqa-restore" title="Restore from archive" onclick="event.stopPropagation();SLProspective.restoreLead(\'' + bk + '\')">♻️</button>'
          : '<button class="cqa-btn cqa-archive" title="Hide / Archive" onclick="event.stopPropagation();SLProspective.archiveLead(\'' + bk + '\')">🗑️</button>') +
      '</div>' +
    '</div>';
  }

  // ── Card Selection ─────────────────────────────────────────────────────────
  function toggleSelect(bk, checked) {
    if (checked) _selectedBks.add(bk);
    else _selectedBks.delete(bk);
    updateBulkBar();
    var card = document.querySelector('.pipeline-card[data-bk="' + bk + '"]');
    if (card) card.classList.toggle('card-selected', checked);
  }

  function clearSelection() {
    _selectedBks.clear();
    document.querySelectorAll('.pipeline-card.card-selected').forEach(function(c) {
      c.classList.remove('card-selected');
      var cb = c.querySelector('.card-checkbox');
      if (cb) cb.checked = false;
    });
    updateBulkBar();
  }

  function updateBulkBar() {
    var bar = $('bulkActionBar');
    var cnt = $('bulkCount');
    if (!bar) return;
    if (_selectedBks.size > 0) {
      bar.style.display = 'flex';
      if (cnt) cnt.textContent = _selectedBks.size + ' selected';
    } else {
      bar.style.display = 'none';
    }
  }

  // ── Bulk Actions ───────────────────────────────────────────────────────────
  async function bulkAdvanceStage() {
    if (!_selectedBks.size) return;
    var selected = _data.filter(function(b) { return _selectedBks.has(b.booking_number); });
    var stages = [...new Set(selected.map(function(b) { return b.stage; }))];
    if (stages.length > 1) { toast('Select leads from the same stage to advance together', 'error'); return; }
    var ns = nextStage(stages[0]);
    if (!ns) { toast('These leads are already in Ready stage', 'info'); return; }
    if (!confirm('Advance ' + _selectedBks.size + ' leads from ' + stageLabel(stages[0]) + ' → ' + stageLabel(ns) + '?')) return;
    var ok = 0;
    for (var bk of _selectedBks) {
      try {
        var r = await fetch(API + '/api/prospective-bonds/' + encodeURIComponent(bk) + '/stage', {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ stage: ns, note: 'Bulk advanced to ' + ns })
        });
        if ((await r.json()).success) ok++;
      } catch (e) { /* continue */ }
    }
    toast('Advanced ' + ok + '/' + _selectedBks.size + ' leads to ' + stageLabel(ns), 'success');
    clearSelection();
    load();
  }

  async function bulkStartSequence() {
    if (!_selectedBks.size) return;
    if (!confirm('Start outreach sequences for ' + _selectedBks.size + ' leads?')) return;
    var ok = 0;
    for (var bk of _selectedBks) {
      var bond = _data.find(function(b) { return b.booking_number === bk; });
      if (!bond) continue;
      try {
        var r = await fetch(API + '/api/outreach/start/' + encodeURIComponent(bk) + '/' + encodeURIComponent(bond.county || ''), { method: 'POST' });
        if ((await r.json()).success) ok++;
      } catch (e) { /* continue */ }
    }
    toast('Started ' + ok + '/' + _selectedBks.size + ' sequences', 'success');
    clearSelection();
    load();
  }

  async function bulkClose() {
    if (!_selectedBks.size) return;
    var reason = prompt('Close ' + _selectedBks.size + ' leads?\n\nReason (optional):');
    if (reason === null) return;
    var ok = 0;
    for (var bk of _selectedBks) {
      try {
        var r = await fetch(API + '/api/prospective-bonds/' + encodeURIComponent(bk) + '/close', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: reason || 'bulk_close', agent: 'Dashboard' })
        });
        if ((await r.json()).success) ok++;
      } catch (e) { /* continue */ }
    }
    toast('Closed ' + ok + '/' + _selectedBks.size + ' leads', 'success');
    clearSelection();
    load();
  }

  function bulkSendMessage() {
    if (!_selectedBks.size) { toast('Select leads first', 'error'); return; }
    var modal = $('bulkMessageModal');
    if (!modal) return;
    var targets = $('bulkMsgTargets');
    if (targets) {
      var names = _data.filter(function(b) { return _selectedBks.has(b.booking_number); }).map(function(b) { return b.defendant_name || b.booking_number; });
      targets.textContent = 'Sending to: ' + names.slice(0, 5).join(', ') + (names.length > 5 ? ' +' + (names.length - 5) + ' more' : '');
    }
    modal.classList.add('show');
  }

  function applyBulkTemplate() {
    var sel = $('bulkMsgTemplate') ? $('bulkMsgTemplate').value : '';
    var ta = $('bulkMsgText');
    if (!ta || !sel) return;
    var templates = {
      intro: 'Hi {name}, this is Brendan with Shamrock Bail Bonds. We can help get your loved one released quickly. Would you like to discuss the process?',
      followup: 'Hi {name}, just checking in. We\'re here to help whenever you\'re ready. Feel free to call or text us anytime. 239-332-2245',
      urgent: 'Hi {name}, we can help get {defendant} released today. Our process is fast and easy — we handle everything. Ready to get started?',
      paperwork: 'Hi {name}, your paperwork is ready to sign! Click here to complete it digitally — takes less than 5 minutes.',
      court: 'Hi {name}, just a reminder that {defendant}\'s court date is coming up. Please make sure all paperwork is in order. Call us at 239-332-2245.',
    };
    ta.value = templates[sel] || '';
  }

  async function executeBulkMessage() {
    var msg = $('bulkMsgText') ? $('bulkMsgText').value.trim() : '';
    if (!msg) { toast('Enter a message', 'error'); return; }
    var personalize = $('bulkMsgPersonalize') ? $('bulkMsgPersonalize').checked : true;
    var ok = 0, fail = 0;
    for (var bk of _selectedBks) {
      var bond = _data.find(function(b) { return b.booking_number === bk; });
      if (!bond) continue;
      var phone = (bond.indemnitor && (bond.indemnitor.phone || bond.indemnitor.callback_phone)) || '';
      if (!phone) { fail++; continue; }
      var text = msg;
      if (personalize) {
        text = text.replace(/{name}/g, (bond.indemnitor && bond.indemnitor.name) || 'there')
                   .replace(/{defendant}/g, bond.defendant_name || 'your loved one');
      }
      try {
        var r = await fetch(API + '/api/imessage/send', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ phone: phone, message: text, booking_number: bk, defendant_name: bond.defendant_name, county: bond.county, agent_name: 'Brendan' })
        });
        if ((await r.json()).success !== false) ok++;
        else fail++;
      } catch (e) { fail++; }
    }
    toast('Sent ' + ok + ' messages' + (fail ? ', ' + fail + ' failed' : ''), ok > 0 ? 'success' : 'error');
    $('bulkMessageModal').classList.remove('show');
    clearSelection();
    load();
  }

  // ── Quick Actions ──────────────────────────────────────────────────────────
  async function quickMessage(bk) {
    var bond = _data.find(function(b) { return b.booking_number === bk; });
    if (!bond) return;
    var phone = (bond.indemnitor && (bond.indemnitor.phone || bond.indemnitor.callback_phone)) || '';
    if (!phone) { toast('No phone number on file', 'error'); return; }
    var msg = prompt('Quick message to ' + ((bond.indemnitor && bond.indemnitor.name) || 'contact') + ' (' + phone + '):\n\nType your message:');
    if (!msg) return;
    try {
      var r = await fetch(API + '/api/imessage/send', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: phone, message: msg, booking_number: bk, defendant_name: bond.defendant_name, county: bond.county, agent_name: 'Brendan' })
      });
      var d = await r.json();
      if (d.success !== false) { toast('Message sent ✓', 'success'); load(); }
      else toast('Send failed: ' + (d.error || ''), 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  async function quickAdvance(bk, newStage) {
    try {
      var r = await fetch(API + '/api/prospective-bonds/' + encodeURIComponent(bk) + '/stage', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stage: newStage, note: 'Advanced to ' + newStage + ' via quick action' })
      });
      var d = await r.json();
      if (d.success) {
        toast('→ ' + stageLabel(newStage), 'success');
        var card = document.querySelector('.pipeline-card[data-bk="' + bk + '"]');
        if (card) { card.style.opacity = '0.4'; card.style.transform = 'translateX(20px)'; }
        setTimeout(load, 400);
      } else toast(d.error || 'Failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  function showIntel(bk) {
    if (window.SLUtils && window.SLUtils.showLeadIntelligence) SLUtils.showLeadIntelligence(bk);
    else toast('AI Intelligence module not loaded', 'error');
  }

  // ── Detail Drawer ──────────────────────────────────────────────────────────
  function openDetail(bk) {
    _currentBk = bk;
    _currentIdx = _data.findIndex(function(b) { return b.booking_number === bk; });
    renderDetail(bk);
    var drawer = $('prospDetailPanel');
    var overlay = $('prospDrawerOverlay');
    if (drawer) { drawer.classList.add('open'); document.body.classList.add('drawer-open'); }
    if (overlay) overlay.classList.add('show');
    updateDrawerNav();
  }

  function closeDetail() {
    _currentBk = null;
    _currentIdx = null;
    var drawer = $('prospDetailPanel');
    var overlay = $('prospDrawerOverlay');
    if (drawer) { drawer.classList.remove('open'); document.body.classList.remove('drawer-open'); }
    if (overlay) overlay.classList.remove('show');
  }

  function navDetail(dir) {
    if (_currentIdx === null) return;
    var newIdx = _currentIdx + dir;
    if (newIdx < 0 || newIdx >= _data.length) return;
    _currentIdx = newIdx;
    _currentBk = _data[newIdx].booking_number;
    renderDetail(_currentBk);
    updateDrawerNav();
  }

  function updateDrawerNav() {
    var prev = $('prospDrawerPrev');
    var next = $('prospDrawerNext');
    if (prev) prev.disabled = _currentIdx === 0;
    if (next) next.disabled = _currentIdx === _data.length - 1;
  }

  function renderDetail(bk) {
    var bond = _data.find(function(b) { return b.booking_number === bk; });
    if (!bond) return;
    var titleEl = $('prospDetailTitle');
    if (titleEl) titleEl.textContent = '📋 ' + (bond.defendant_name || bk);
    var body = $('prospDetailBody');
    if (!body) return;

    var comms = bond.communication_log || [];
    var timeline = bond.timeline || [];
    var ind = bond.indemnitor || {};
    var seqActive = bond.sequence_status === 'active';
    var stages = ['contacted', 'negotiating', 'paperwork', 'ready'];
    var currentStageIdx = stages.indexOf(bond.stage);

    var stageBarHtml = stages.map(function(s, i) {
      var isActive = s === bond.stage;
      var isDone = stages.indexOf(s) < currentStageIdx;
      var dot = '<div class="prosp-stage-dot' + (isActive ? ' active' : isDone ? ' done' : '') + '" style="--dot-color:' + stageColor(s) + '" onclick="SLProspective.promptStage(\'' + bk + '\',\'' + s + '\')">' + stageLabel(s) + '</div>';
      return (i > 0 ? '<div class="prosp-stage-line' + (isDone ? ' done' : '') + '"></div>' : '') + dot;
    }).join('');

    var timelineHtml = timeline.length ? timeline.slice().reverse().map(function(t) {
      return '<div class="timeline-item"><div class="timeline-dot"></div><div class="timeline-content"><div class="timeline-event">' + (t.event || '—') + '</div><div class="timeline-detail">' + (t.detail || '') + '</div><div class="timeline-meta">' + timeAgo(t.timestamp) + ' · ' + (t.agent || 'System') + '</div></div></div>';
    }).join('') : '<div style="color:var(--muted);font-size:12px;padding:8px 0">No timeline events</div>';

    var commHtml = comms.length ? renderConversation(comms, bk) : '<div class="comm-empty">No messages yet</div>';

    var indRelOptions = ['', 'Spouse', 'Parent', 'Sibling', 'Child', 'Friend', 'Employer', 'Attorney', 'Other'].map(function(r) {
      return '<option' + (ind.relationship === r ? ' selected' : '') + '>' + r + '</option>';
    }).join('');

    body.innerHTML =
      '<div class="prosp-stage-bar">' + stageBarHtml + '</div>' +
      '<div class="prosp-detail-grid">' +
        '<div>' +
          '<div class="prosp-info-card" style="margin-bottom:12px">' +
            '<h4>🧑 Defendant</h4>' +
            '<div class="prosp-field"><span>Name</span><span style="font-weight:700">' + (bond.defendant_name || '—') + '</span></div>' +
            '<div class="prosp-field"><span>Booking #</span><span class="mono">' + (bond.booking_number || '—') + '</span></div>' +
            '<div class="prosp-field"><span>County</span><span>' + (bond.county || '—') + '</span></div>' +
            '<div class="prosp-field"><span>Bond Amount</span><span style="font-weight:800;color:var(--accent)">' + money(bond.bond_amount) + '</span></div>' +
            '<div class="prosp-field"><span>Charges</span><span style="font-size:12px">' + (bond.charges || '—') + '</span></div>' +
            '<div class="prosp-field"><span>Lead Score</span><span class="score-pill ' + ((bond.lead_status || '').toLowerCase() === 'hot' ? 'score-hot' : (bond.lead_status || '').toLowerCase() === 'warm' ? 'score-warm' : 'score-cold') + '">' + (bond.lead_score || 0) + ' · ' + (bond.lead_status || '—') + '</span></div>' +
            '<div class="prosp-field"><span>FTA Risk</span><span>' + (riskBadge(bond) || '—') + '</span></div>' +
            (bond.detail_url ? '<div style="margin-top:8px"><a href="' + bond.detail_url + '" target="_blank" class="prosp-ext-link">🔗 View Arrest Record</a></div>' : '') +
          '</div>' +
          '<div class="prosp-info-card" style="margin-bottom:12px">' +
            '<h4>👤 Indemnitor / Contact</h4>' +
            '<div class="prosp-field"><span>Name</span><input class="prosp-inline-input" id="indName" value="' + (ind.name || '') + '" placeholder="Contact name"></div>' +
            '<div class="prosp-field"><span>Phone</span><div style="display:flex;align-items:center;gap:6px"><input class="prosp-inline-input" id="indPhone" value="' + (ind.phone || '') + '" placeholder="+1 (239) 555-0000" style="flex:1">' + (ind.phone ? '<a href="tel:' + ind.phone + '" class="cqa-btn cqa-msg" style="text-decoration:none">📞</a>' : '') + '</div></div>' +
            '<div class="prosp-field"><span>Email</span><input class="prosp-inline-input" id="indEmail" value="' + (ind.email || '') + '" placeholder="email@example.com"></div>' +
            '<div class="prosp-field"><span>Relationship</span><select class="prosp-inline-input" id="indRelation">' + indRelOptions + '</select></div>' +
            '<div style="display:flex;gap:8px;margin-top:10px"><button class="btn-primary" style="font-size:12px;padding:7px 16px" onclick="SLProspective.saveIndemnitor(\'' + bk + '\')">💾 Save</button><button class="btn-secondary" style="font-size:12px;padding:7px 14px" onclick="SLProspective.discoverContacts(\'' + bk + '\')">🔍 Discover Contacts</button></div>' +
          '</div>' +
          '<div class="prosp-info-card" style="margin-bottom:12px">' +
            '<h4>🤖 Outreach Sequence</h4>' +
            '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px"><span class="seq-badge ' + (seqActive ? 'seq-active' : 'seq-stopped') + '">' + (seqActive ? '🤖 Active' : '⏹ Not Running') + '</span>' +
            '<div style="display:flex;gap:6px">' +
              (!seqActive ? '<button class="btn-primary" style="font-size:11px;padding:5px 12px" onclick="SLProspective.startSequence(\'' + bk + '\')">▶ Start</button>' : '<button class="btn-cancel" style="font-size:11px;padding:5px 12px" onclick="SLProspective.stopSequence(\'' + bk + '\')">⏹ Stop</button>') +
              '<button class="btn-secondary" style="font-size:11px;padding:5px 12px" onclick="SLProspective.viewSequence(\'' + bk + '\')">📋 Schedule</button>' +
            '</div></div>' +
            '<div id="seqSchedule_' + bk + '" style="font-size:12px;color:var(--muted)">Click "Schedule" to load</div>' +
          '</div>' +
        '</div>' +
        '<div>' +
          '<div class="prosp-comm-section">' +
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px"><h4 style="margin:0">💬 Conversation (' + comms.length + ')</h4><button class="btn-secondary" style="font-size:11px;padding:4px 10px" onclick="SLProspective.load().then(function(){SLProspective.openDetail(\'' + bk + '\')})">🔄 Refresh</button></div>' +
            '<div class="prosp-comm-timeline" id="commTimeline_' + bk + '">' + commHtml + '</div>' +
          '</div>' +
          '<div class="prosp-ai-suggest" id="aiSuggest_' + bk + '">' +
            '<div class="ai-suggest-header"><span>🧠 AI Suggested Replies</span><button class="ai-suggest-refresh" onclick="SLProspective.loadAiSuggestions(\'' + bk + '\')">↻ Generate</button></div>' +
            '<div class="ai-suggest-body" id="aiSuggestBody_' + bk + '"><div class="ai-suggest-hint">Click ↻ Generate to get AI-powered reply suggestions</div></div>' +
          '</div>' +
          '<div class="prosp-composer">' +
            '<div class="composer-top-row">' +
              '<select class="composer-select" id="commChannel"><option value="imessage">💬 iMessage</option><option value="sms">📱 SMS</option><option value="phone">📞 Phone</option><option value="left_vm">📞 Left VM</option><option value="note">📝 Note</option></select>' +
              '<select class="composer-select" id="commTemplate" onchange="SLProspective.applyTemplate(\'' + bk + '\')"><option value="">— Template —</option><option value="intro">👋 Introduction</option><option value="followup">🔄 Follow-up</option><option value="urgent">⚡ Urgent</option><option value="paperwork">📄 Paperwork Ready</option><option value="court">⚖️ Court Reminder</option></select>' +
              '<select class="composer-select" id="commEffect" style="width:130px"><option value="">No Effect</option><option value="slam">💥 Slam</option><option value="loud">📢 Loud</option><option value="gentle">🌸 Gentle</option><option value="invisible ink">🫥 Invisible</option><option value="fireworks">🎆 Fireworks</option></select>' +
            '</div>' +
            '<div class="composer-textarea-wrap"><textarea class="composer-textarea" id="commMessage" rows="3" placeholder="Type a message... (Ctrl+Enter to send)"></textarea><div class="composer-char-count" id="commCharCount_' + bk + '">0</div></div>' +
            '<div class="composer-bottom-row">' +
              '<button class="composer-btn-send" onclick="SLProspective.sendMessage(\'' + bk + '\')"><span id="sendBtnText_' + bk + '">📤 Send</span></button>' +
              '<button class="composer-btn-note" onclick="SLProspective.addNote(\'' + bk + '\')">📝 Note</button>' +
              '<button class="composer-btn-schedule" onclick="SLProspective.scheduleMessage(\'' + bk + '\')">⏰ Schedule</button>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="prosp-info-card" style="margin-top:12px"><h4>📅 Activity Timeline (' + timeline.length + ')</h4><div style="max-height:200px;overflow-y:auto">' + timelineHtml + '</div></div>' +
      '<div class="prosp-action-footer">' +
        '<button class="btn-officialize" onclick="SLProspective.officialize(\'' + bk + '\')">☘️ Officialize Bond</button>' +
        '<div class="prosp-stage-select"><label style="font-size:12px;color:var(--muted)">Stage:</label><select class="outreach-select" onchange="SLProspective.promptStage(\'' + bk + '\',this.value)">' +
          stages.map(function(s) { return '<option value="' + s + '"' + (bond.stage === s ? ' selected' : '') + '>' + stageLabel(s) + '</option>'; }).join('') +
        '</select></div>' +
        '<button class="btn-archive" style="font-size:12px" onclick="SLProspective.archiveLead(\'' + bk + '\')">📦 Archive</button>' +
        '<button class="btn-cancel" style="font-size:12px" onclick="SLProspective.promptClose(\'' + bk + '\')">❌ Close Lead</button>' +
      '</div>';

    // Wire char counter and Ctrl+Enter
    var ta = $('commMessage');
    if (ta) {
      ta.addEventListener('input', function() {
        var cc = $('commCharCount_' + bk);
        if (cc) cc.textContent = ta.value.length;
      });
      ta.addEventListener('keydown', function(e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); sendMessage(bk); }
      });
    }
  }

  // ── Conversation Rendering ─────────────────────────────────────────────────
  function renderConversation(comms, bk) {
    return comms.map(function(c, i) {
      var isOut = c.direction !== 'inbound';
      var isNote = c.channel === 'note';
      var ts = c.timestamp ? new Date(c.timestamp).toLocaleString() : '';
      if (isNote) {
        return '<div class="comm-note"><span class="comm-note-icon">📝</span><div class="comm-note-text">' + (c.message || '') + '</div><div class="comm-note-meta">' + ts + ' · ' + (c.agent || 'Agent') + '</div></div>';
      }
      var msgActions = '';
      if (isOut && c.bb_message_guid) {
        msgActions = '<div class="comm-bubble-actions"><button class="cba-btn" title="Unsend" onclick="SLProspective.unsendMsg(\'' + bk + '\',' + i + ')">↩</button><button class="cba-btn" title="Edit" onclick="SLProspective.editMsg(\'' + bk + '\',' + i + ')">✏️</button><button class="cba-btn" title="React" onclick="SLProspective.reactMsg(\'' + bk + '\',' + i + ')">❤️</button></div>';
      } else {
        msgActions = '<div class="comm-bubble-actions"><button class="cba-btn" title="React" onclick="SLProspective.reactMsg(\'' + bk + '\',' + i + ')">❤️</button></div>';
      }
      var statusMark = c.status === 'delivered' ? ' · ✓✓' : c.status === 'sent' ? ' · ✓' : '';
      return '<div class="comm-bubble-wrap ' + (isOut ? 'outbound' : 'inbound') + '"><div class="comm-bubble ' + (isOut ? 'bubble-out' : 'bubble-in') + '"><div class="comm-bubble-text">' + (c.message || '').replace(/\n/g, '<br>') + '</div><div class="comm-bubble-meta">' + channelIcon(c.channel) + ' ' + ts + statusMark + '</div></div>' + msgActions + '</div>';
    }).join('');
  }

  // ── AI Suggestions ─────────────────────────────────────────────────────────
  async function loadAiSuggestions(bk) {
    var el = $('aiSuggestBody_' + bk);
    if (!el) return;
    el.innerHTML = '<div class="ai-suggest-loading">🧠 Generating suggestions...</div>';
    try {
      var bond = _data.find(function(b) { return b.booking_number === bk; });
      var comms = (bond && bond.communication_log) || [];
      var lastInbound = comms.slice().reverse().find(function(c) { return c.direction === 'inbound'; });
      if (!lastInbound) { el.innerHTML = '<div class="ai-suggest-hint">No inbound messages to respond to yet</div>'; return; }
      var r = await fetch(API + '/api/agent-brain/suggest', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_number: bk, defendant_name: (bond && bond.defendant_name) || '', county: (bond && bond.county) || '', inbound_message: lastInbound.message || '', conversation_history: comms.slice(-10), stage: (bond && bond.stage) || 'contacted' })
      });
      var d = await r.json();
      var suggestions = d.suggestions || (d.reply ? [d.reply] : []);
      if (suggestions.length) {
        el.innerHTML = suggestions.map(function(s) {
          return '<div class="ai-suggestion" onclick="SLProspective.useSuggestion(\'' + bk + '\',this.dataset.text)" data-text="' + s.replace(/"/g, '&quot;') + '"><div class="ai-sug-text">' + s + '</div><button class="ai-sug-use">Use →</button></div>';
        }).join('');
      } else {
        el.innerHTML = '<div class="ai-suggest-hint">No suggestions available</div>';
      }
    } catch (e) {
      el.innerHTML = '<div class="ai-suggest-hint" style="color:var(--red)">Failed: ' + e.message + '</div>';
    }
  }

  function useSuggestion(bk, text) {
    var ta = $('commMessage');
    if (ta) { ta.value = text; ta.focus(); ta.dispatchEvent(new Event('input')); }
  }

  // ── Sequence Controls ──────────────────────────────────────────────────────
  async function startSequence(bk) {
    var bond = _data.find(function(b) { return b.booking_number === bk; });
    if (!bond) return;
    try {
      var r = await fetch(API + '/api/outreach/start/' + encodeURIComponent(bk) + '/' + encodeURIComponent((bond && bond.county) || ''), { method: 'POST' });
      var d = await r.json();
      if (d.success) { toast('Sequence started (' + (d.tier || '') + ' tier, ' + (d.steps || 0) + ' steps)', 'success'); load(); setTimeout(function() { openDetail(bk); }, 400); }
      else toast(d.reason || d.error || 'Failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  async function stopSequence(bk) {
    var bond = _data.find(function(b) { return b.booking_number === bk; });
    if (!bond) return;
    if (!confirm('Stop the outreach sequence for this lead?')) return;
    try {
      var r = await fetch(API + '/api/outreach/stop/' + encodeURIComponent(bk) + '/' + encodeURIComponent((bond && bond.county) || ''), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason: 'manual_stop' })
      });
      var d = await r.json();
      if (d.success) { toast('Sequence stopped', 'success'); load(); setTimeout(function() { openDetail(bk); }, 400); }
      else toast(d.error || 'Failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  async function viewSequence(bk) {
    var bond = _data.find(function(b) { return b.booking_number === bk; });
    if (!bond) return;
    var el = $('seqSchedule_' + bk);
    if (!el) return;
    el.innerHTML = '<span style="color:var(--muted)">Loading...</span>';
    try {
      var r = await fetch(API + '/api/outreach/status/' + encodeURIComponent(bk) + '/' + encodeURIComponent((bond && bond.county) || ''));
      var d = await r.json();
      if (!d.found) { el.innerHTML = '<span style="color:var(--muted)">No sequence found</span>'; return; }
      var steps = d.steps || [];
      el.innerHTML = steps.map(function(s) {
        var icon = s.status === 'sent' ? '✅' : s.status === 'scheduled' ? '⏰' : '⏹';
        return '<div class="seq-step ' + s.status + '">' + icon + ' <span class="seq-step-template">' + (s.template_key || '') + '</span> <span class="seq-step-time">' + (s.scheduled_for ? new Date(s.scheduled_for).toLocaleString() : '—') + '</span> <span class="seq-step-status-label ' + s.status + '">' + s.status + '</span></div>';
      }).join('') || '<span style="color:var(--muted)">No steps</span>';
    } catch (e) { el.innerHTML = '<span style="color:var(--red)">Error: ' + e.message + '</span>'; }
  }

  // ── Contact Discovery ──────────────────────────────────────────────────────
  async function discoverContacts(bk) {
    toast('Running contact discovery...', 'info');
    try {
      var r = await fetch(API + '/api/contacts/discover', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ booking_number: bk })
      });
      var d = await r.json();
      if (d.contacts && d.contacts.length) {
        var best = d.contacts[0];
        var nameEl = $('indName');
        var phoneEl = $('indPhone');
        if (nameEl && !nameEl.value && best.name) nameEl.value = best.name;
        if (phoneEl && !phoneEl.value && best.phone) phoneEl.value = best.phone;
        toast('Found ' + d.contacts.length + ' contact(s) — best: ' + (best.name || best.phone), 'success');
      } else {
        toast('No contacts found via discovery', 'info');
      }
    } catch (e) { toast('Discovery failed: ' + e.message, 'error'); }
  }

  // ── Schedule Message ───────────────────────────────────────────────────────
  async function scheduleMessage(bk) {
    var bond = _data.find(function(b) { return b.booking_number === bk; });
    var phone = ($('indPhone') && $('indPhone').value) || (bond && bond.indemnitor && bond.indemnitor.phone) || '';
    var msg = $('commMessage') ? $('commMessage').value.trim() : '';
    if (!phone) { toast('No phone number', 'error'); return; }
    if (!msg) { toast('Enter a message to schedule', 'error'); return; }
    var when = prompt('Schedule for (YYYY-MM-DD HH:MM):');
    if (!when) return;
    var dt = new Date(when);
    if (isNaN(dt)) { toast('Invalid date format', 'error'); return; }
    try {
      var r = await fetch(API + '/api/bb-schedule/court-reminders', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: phone, message: msg, send_at: dt.toISOString(), booking_number: bk })
      });
      var d = await r.json();
      if (d.success !== false) { toast('Scheduled for ' + dt.toLocaleString(), 'success'); if ($('commMessage')) $('commMessage').value = ''; }
      else toast(d.error || 'Failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  // ── Send Message ───────────────────────────────────────────────────────────
  async function sendMessage(bk) {
    var bond = _data.find(function(b) { return b.booking_number === bk; });
    var phone = ($('indPhone') && $('indPhone').value) || (bond && bond.indemnitor && (bond.indemnitor.phone || bond.indemnitor.callback_phone)) || '';
    var channel = $('commChannel') ? $('commChannel').value : 'imessage';
    var effect = $('commEffect') ? $('commEffect').value : '';
    var message = $('commMessage') ? $('commMessage').value.trim() : '';

    if (!message && $('commTemplate') && $('commTemplate').value) {
      applyTemplate(bk);
      message = $('commMessage') ? $('commMessage').value.trim() : '';
    }

    if (!message) { toast('Enter a message', 'error'); return; }

    if (channel === 'phone' || channel === 'left_vm' || channel === 'note') {
      await addNote(bk, message, channel);
      if ($('commMessage')) $('commMessage').value = '';
      return;
    }

    if (!phone) { toast('No phone number on file', 'error'); return; }

    var sendBtn = $('sendBtnText_' + bk);
    if (sendBtn) sendBtn.textContent = '⏳ Sending...';

    try {
      var endpoint = effect ? API + '/api/imessage/send-effect' : API + '/api/imessage/send';
      var body = { phone: phone, message: message, from_number: '2399550178', booking_number: bk, defendant_name: (bond && bond.defendant_name) || '', county: (bond && bond.county) || '', recipient_label: (bond && bond.indemnitor && bond.indemnitor.name) || 'Indemnitor', agent_name: 'Brendan' };
      if (effect) body.effect = effect;

      var r = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

      if (r.status === 409) {
        var dupe = await r.json();
        if (dupe.error === 'duplicate') {
          var force = confirm('⚠️ Already messaged within 24h.\nLast sent: ' + (dupe.last_sent || 'recently') + '\nPreview: "' + (dupe.message_preview || '') + '"\n\nSend anyway?');
          if (force) {
            body.force_send = true;
            var r2 = await fetch(API + '/api/imessage/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
            var d2 = await r2.json();
            if (d2.success !== false) { toast('Message sent ✓', 'success'); if ($('commMessage')) $('commMessage').value = ''; load(); }
            else toast(d2.error || 'Failed', 'error');
          }
          return;
        }
      }

      var d = await r.json();
      if (d.success !== false) {
        toast('Message sent ✓', 'success');
        if ($('commMessage')) $('commMessage').value = '';
        load();
        setTimeout(function() { openDetail(bk); }, 500);
      } else toast(d.error || 'Send failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
    finally { if (sendBtn) sendBtn.textContent = '📤 Send'; }
  }

  // ── Template Application ───────────────────────────────────────────────────
  function applyTemplate(bk) {
    var bond = _data.find(function(b) { return b.booking_number === bk; });
    var template = $('commTemplate') ? $('commTemplate').value : '';
    if (!template) return;
    var name = (bond && bond.defendant_name) || 'your loved one';
    var tpls = {
      intro: 'Hi, this is Brendan with Shamrock Bail Bonds. I\'m reaching out about ' + name + '. We can help get them released quickly. Would you like to discuss the process?',
      followup: 'Hi, just checking in regarding ' + name + '. We\'re here to help whenever you\'re ready. Feel free to call or text us anytime.',
      urgent: 'Hi, we can help get ' + name + ' released today. Our process is fast and easy — we handle everything. Ready to get started?',
      paperwork: 'Hi, your paperwork is ready to sign! It takes less than 5 minutes. Please complete it at your earliest convenience.',
      court: 'Hi, just a reminder about ' + name + '\'s upcoming court date. Please make sure all paperwork is in order. Call us at 239-332-2245.',
    };
    var ta = $('commMessage');
    if (ta && tpls[template]) { ta.value = tpls[template]; ta.dispatchEvent(new Event('input')); }
  }

  // ── Add Note ───────────────────────────────────────────────────────────────
  async function addNote(bk, text, channel) {
    channel = channel || 'note';
    var msg = text || ($('commMessage') ? $('commMessage').value.trim() : '');
    if (!msg) { toast('Enter a note', 'error'); return; }
    try {
      var r = await fetch(API + '/api/prospective-bonds/' + encodeURIComponent(bk) + '/note', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: msg, channel: channel, agent: 'Brendan' })
      });
      var d = await r.json();
      if (d.success) {
        toast('Note saved', 'success');
        if ($('commMessage')) $('commMessage').value = '';
        load();
        setTimeout(function() { openDetail(bk); }, 400);
      } else toast(d.error || 'Failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  // ── Save Indemnitor ────────────────────────────────────────────────────────
  async function saveIndemnitor(bk) {
    var data = {
      name: $('indName') ? $('indName').value : '',
      phone: $('indPhone') ? $('indPhone').value : '',
      email: $('indEmail') ? $('indEmail').value : '',
      relationship: $('indRelation') ? $('indRelation').value : '',
    };
    try {
      var r = await fetch(API + '/api/prospective-bonds/' + encodeURIComponent(bk) + '/indemnitor', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data)
      });
      var d = await r.json();
      if (d.success) { toast('Indemnitor saved ✓', 'success'); load(); }
      else toast(d.error || 'Failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  // ── Stage Management ───────────────────────────────────────────────────────
  function promptStage(bk, newStage) {
    var bond = _data.find(function(b) { return b.booking_number === bk; });
    if (!bond || bond.stage === newStage) return;
    var note = prompt('Moving to "' + stageLabel(newStage) + '".\n\nAdd a note (optional):');
    if (note === null) return;
    updateStage(bk, newStage, note);
  }

  async function updateStage(bk, stage, note) {
    try {
      var r = await fetch(API + '/api/prospective-bonds/' + encodeURIComponent(bk) + '/stage', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stage: stage, note: note })
      });
      var d = await r.json();
      if (d.success) {
        toast('Stage → ' + stageLabel(stage), 'success');
        load();
        if (_currentBk === bk) setTimeout(function() { openDetail(bk); }, 400);
      } else toast(d.error || 'Failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  // ── Close Lead ─────────────────────────────────────────────────────────────
  function promptClose(bk) {
    var reasons = ['not_interested', 'bonded_elsewhere', 'released_own_recognizance', 'charges_dropped', 'do_not_contact', 'other'];
    var labels = ['Not Interested', 'Bonded Elsewhere', 'Released (ROR)', 'Charges Dropped', 'Do Not Contact', 'Other'];
    var choice = prompt('Close this lead?\n\nReason:\n' + labels.map(function(r, i) { return (i + 1) + '. ' + r; }).join('\n') + '\n\nEnter number (1-6):');
    if (!choice) return;
    var idx = parseInt(choice) - 1;
    var reason = reasons[idx] || 'other';
    closeLead(bk, reason);
  }

  async function closeLead(bk, reason) {
    try {
      var r = await fetch(API + '/api/prospective-bonds/' + encodeURIComponent(bk) + '/close', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason: reason, agent: 'Brendan' })
      });
      var d = await r.json();
      if (d.success) { toast('Lead closed', 'success'); closeDetail(); load(); }
      else toast(d.error || 'Failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  // ── Archive / Hide Lead ────────────────────────────────────────────────────
  async function archiveLead(bk) {
    if (!confirm('Archive this lead? It will be hidden from the board but data is preserved.')) return;
    try {
      var r = await fetch(API + '/api/prospective-bonds/' + encodeURIComponent(bk) + '/archive', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agent: 'Brendan' })
      });
      var d = await r.json();
      if (d.success) { toast('📦 Lead archived', 'success'); closeDetail(); load(); }
      else toast(d.error || 'Archive failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  async function restoreLead(bk) {
    try {
      var r = await fetch(API + '/api/prospective-bonds/' + encodeURIComponent(bk) + '/restore', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agent: 'Brendan' })
      });
      var d = await r.json();
      if (d.success) { toast('♻️ Lead restored', 'success'); load(); }
      else toast(d.error || 'Restore failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  async function bulkArchive() {
    var bks = Array.from(_selectedBks);
    if (!bks.length) { toast('No leads selected', 'error'); return; }
    if (!confirm('Archive ' + bks.length + ' selected lead(s)? They will be hidden but data is preserved.')) return;
    try {
      var r = await fetch(API + '/api/prospective-bonds/bulk-archive', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_numbers: bks, agent: 'Brendan' })
      });
      var d = await r.json();
      if (d.success) { toast('📦 ' + d.archived + ' lead(s) archived', 'success'); clearSelection(); load(); }
      else toast(d.error || 'Bulk archive failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  function toggleArchiveView() {
    _showArchived = !_showArchived;
    var btn = $('prospArchiveToggle');
    if (btn) {
      var label = btn.querySelector('span:first-of-type');
      if (label) label.textContent = _showArchived ? '← Back to Board' : 'Archive';
    }
    load();
  }

  // ── Officialize Bond ───────────────────────────────────────────────────────
  async function officialize(bk) {
    var bond = _data.find(function(b) { return b.booking_number === bk; });
    if (!bond) return;
    if (!confirm('Officialize bond for ' + (bond.defendant_name || bk) + '?\n\nThis will create the bond record and trigger the SignNow paperwork workflow.')) return;
    try {
      var r = await fetch(API + '/api/prospective-bonds/' + encodeURIComponent(bk) + '/officialize', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agent: 'Brendan' })
      });
      var d = await r.json();
      if (d.success) {
        toast('Bond officialized! Paperwork workflow started.', 'success');
        closeDetail();
        load();
        if (window.SLActiveBonds && window.SLActiveBonds.load) setTimeout(SLActiveBonds.load, 1000);
      } else toast(d.error || 'Failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  // ── Manual Add Modal ───────────────────────────────────────────────────────
  function openAddModal(stage) {
    stage = stage || 'contacted';
    var modal = $('addLeadModal');
    if (!modal) return;
    var stageEl = $('alStage');
    if (stageEl) stageEl.value = stage;
    modal.classList.add('show');
    setTimeout(function() { var el = $('alDefName'); if (el) el.focus(); }, 100);
  }

  function closeAddModal() {
    var modal = $('addLeadModal');
    if (modal) modal.classList.remove('show');
    ['alDefName', 'alBooking', 'alBondAmount', 'alCharges', 'alIndName', 'alIndPhone', 'alIndEmail', 'alNote'].forEach(function(id) {
      var el = $(id);
      if (el) el.value = '';
    });
    var county = $('alCounty'); if (county) county.value = '';
    var rel = $('alIndRelation'); if (rel) rel.value = '';
  }

  function switchAddSource(source, btn) {
    _addSource = source;
    document.querySelectorAll('.als-tab').forEach(function(t) { t.classList.remove('active'); });
    btn.classList.add('active');
    ['addSourceManual', 'addSourceArrest', 'addSourceIntake'].forEach(function(id) {
      var el = $(id);
      if (el) el.style.display = (id === 'addSource' + source.charAt(0).toUpperCase() + source.slice(1)) ? 'block' : 'none';
    });
    var submitBtn = $('alSubmitBtn');
    if (submitBtn) submitBtn.style.display = source === 'manual' ? 'block' : 'none';
  }

  async function searchArrests() {
    var q = $('alArrestSearch') ? $('alArrestSearch').value.trim() : '';
    if (!q || q.length < 2) return;
    var el = $('alArrestResults');
    if (!el) return;
    el.innerHTML = '<div style="padding:16px;color:var(--muted);text-align:center">Searching...</div>';
    try {
      var r = await fetch(API + '/api/leads?search=' + encodeURIComponent(q) + '&limit=20');
      var d = await r.json();
      var leads = d.leads || [];
      if (!leads.length) { el.innerHTML = '<div style="padding:16px;color:var(--muted);text-align:center">No results</div>'; return; }
      el.innerHTML = leads.map(function(l) {
        return '<div class="al-result-row" onclick="SLProspective.addFromArrest(\'' + l.booking_number + '\',\'' + (l.full_name || '').replace(/'/g, "\\'") + '\',\'' + (l.county || '') + '\',' + (l.bond_amount || 0) + ')"><div style="font-weight:700">' + (l.full_name || '—') + '</div><div style="font-size:12px;color:var(--muted)">' + (l.county || '—') + ' · ' + (l.booking_number || '') + ' · ' + money(l.bond_amount) + '</div><div style="font-size:11px;color:var(--muted)">' + (l.charges || '—') + '</div></div>';
      }).join('');
    } catch (e) { el.innerHTML = '<div style="padding:16px;color:var(--red)">Error: ' + e.message + '</div>'; }
  }

  async function addFromArrest(bk, name, county, bondAmount) {
    try {
      var r = await fetch(API + '/api/prospective-bonds', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_number: bk, defendant_name: name, county: county, bond_amount: bondAmount, stage: $('alStage') ? $('alStage').value : 'contacted', agent: 'Brendan' })
      });
      var d = await r.json();
      if (d.success !== false) { toast(name + ' added to pipeline', 'success'); closeAddModal(); load(); }
      else toast(d.error || 'Failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  async function searchIntake() {
    var q = $('alIntakeSearch') ? $('alIntakeSearch').value.trim() : '';
    if (!q || q.length < 2) return;
    var el = $('alIntakeResults');
    if (!el) return;
    el.innerHTML = '<div style="padding:16px;color:var(--muted);text-align:center">Searching...</div>';
    try {
      var r = await fetch(API + '/api/intake?search=' + encodeURIComponent(q) + '&limit=20');
      var d = await r.json();
      var items = d.items || d.queue || [];
      if (!items.length) { el.innerHTML = '<div style="padding:16px;color:var(--muted);text-align:center">No results</div>'; return; }
      el.innerHTML = items.map(function(item) {
        return '<div class="al-result-row" onclick="SLProspective.addFromIntake(\'' + (item._id || item.intake_id || '') + '\',\'' + (item.defendant_name || '').replace(/'/g, "\\'") + '\')"><div style="font-weight:700">' + (item.defendant_name || '—') + '</div><div style="font-size:12px;color:var(--muted)">' + (item.county || '—') + ' · ' + (item.booking_number || '—') + ' · ' + money(item.bond_amount) + '</div><div style="font-size:11px;color:var(--accent)">' + (item.indemnitor_name || '') + (item.indemnitor_phone ? ' · ' + item.indemnitor_phone : '') + '</div></div>';
      }).join('');
    } catch (e) { el.innerHTML = '<div style="padding:16px;color:var(--red)">Error: ' + e.message + '</div>'; }
  }

  async function addFromIntake(intakeId, name) {
    try {
      var r = await fetch(API + '/api/prospective-bonds/from-intake', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ intake_id: intakeId, stage: $('alStage') ? $('alStage').value : 'contacted', agent: 'Brendan' })
      });
      var d = await r.json();
      if (d.success) { toast(name + ' promoted from Intake Queue', 'success'); closeAddModal(); load(); }
      else toast(d.error || 'Failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  async function submitAddLead() {
    var defName = $('alDefName') ? $('alDefName').value.trim() : '';
    var county = $('alCounty') ? $('alCounty').value : '';
    if (!defName) { toast('Defendant name is required', 'error'); return; }
    if (!county) { toast('County is required', 'error'); return; }
    var btn = $('alSubmitBtn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Adding...'; }
    try {
      var payload = {
        defendant_name: defName,
        booking_number: ($('alBooking') && $('alBooking').value.trim()) || ('MANUAL-' + Date.now()),
        county: county,
        bond_amount: parseFloat($('alBondAmount') ? $('alBondAmount').value : '0') || 0,
        charges: ($('alCharges') && $('alCharges').value.trim()) || '',
        stage: ($('alStage') && $('alStage').value) || 'contacted',
        indemnitor: {
          name: ($('alIndName') && $('alIndName').value.trim()) || '',
          phone: ($('alIndPhone') && $('alIndPhone').value.trim()) || '',
          email: ($('alIndEmail') && $('alIndEmail').value.trim()) || '',
          relationship: ($('alIndRelation') && $('alIndRelation').value) || '',
        },
        initial_note: ($('alNote') && $('alNote').value.trim()) || '',
        agent: 'Brendan',
      };
      var r = await fetch(API + '/api/prospective-bonds', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
      });
      var d = await r.json();
      if (d.success !== false) {
        toast(defName + ' added to pipeline ✓', 'success');
        var bk = (d.prospective_bond && d.prospective_bond.booking_number) || payload.booking_number;
        if ($('alAutoSequence') && $('alAutoSequence').checked && payload.indemnitor.phone) {
          try { await fetch(API + '/api/outreach/start/' + encodeURIComponent(bk) + '/' + encodeURIComponent(county), { method: 'POST' }); } catch (e) { /* non-fatal */ }
        }
        if ($('alContactDiscover') && $('alContactDiscover').checked) {
          try { await fetch(API + '/api/contacts/discover', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ booking_number: bk }) }); } catch (e) { /* non-fatal */ }
        }
        closeAddModal();
        load();
        setTimeout(function() { openDetail(bk); }, 600);
      } else toast(d.error || 'Failed to add lead', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
    finally { if (btn) { btn.disabled = false; btn.textContent = '＋ Add to Pipeline'; } }
  }

  // ── Batch Outreach ─────────────────────────────────────────────────────────
  async function batchStartOutreach() {
    if (!confirm('Start outreach sequences for all new arrests in the last 24 hours?')) return;
    try {
      var r = await fetch(API + '/api/outreach/batch/start', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ hours_back: 24, limit: 100 })
      });
      var d = await r.json();
      if (d.success) toast('Batch: ' + (d.started || 0) + ' started, ' + (d.skipped || 0) + ' skipped', 'success');
      else toast(d.error || 'Failed', 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  // ── Export ─────────────────────────────────────────────────────────────────
  function exportPipeline() {
    if (!_data.length) { toast('No data to export', 'info'); return; }
    var headers = ['Booking #', 'Defendant', 'County', 'Bond Amount', 'Stage', 'Lead Score', 'Indemnitor', 'Phone', 'Last Activity', 'Messages'];
    var rows = _data.map(function(b) {
      return [
        b.booking_number || '', b.defendant_name || '', b.county || '', b.bond_amount || 0,
        b.stage || '', b.lead_score || 0, (b.indemnitor && b.indemnitor.name) || '',
        (b.indemnitor && b.indemnitor.phone) || '',
        b.updated_at ? new Date(b.updated_at).toLocaleDateString() : '',
        (b.communication_log || []).length,
      ];
    });
    var csv = [headers].concat(rows).map(function(r) { return r.map(function(v) { return '"' + String(v).replace(/"/g, '""') + '"'; }).join(','); }).join('\n');
    var blob = new Blob([csv], { type: 'text/csv' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'pipeline_' + new Date().toISOString().slice(0, 10) + '.csv';
    a.click();
    URL.revokeObjectURL(url);
    toast('Pipeline exported', 'success');
  }

  // ── Search ─────────────────────────────────────────────────────────────────
  function debounceSearch() { clearTimeout(_searchTimer); _searchTimer = setTimeout(load, 300); }

  function clearSearch() {
    var inp = $('prospSearch');
    if (inp) { inp.value = ''; inp.focus(); }
    var clr = $('prospSearchClear');
    if (clr) clr.style.display = 'none';
    load();
  }

  function setStage(stage, btn) {
    _stage = stage;
    document.querySelectorAll('#prospStageFilter button').forEach(function(b) { b.classList.remove('active'); });
    if (btn) btn.classList.add('active');
    load();
  }

  // ── Auto-Reply Panel ───────────────────────────────────────────────────────
  async function loadAutoReplyConfig() {
    try {
      var r = await fetch(API + '/api/imessage/auto-reply/config');
      _autoReplyConfig = await r.json();
      renderAutoReplyPanel();
    } catch (e) { console.warn('Auto-reply config load failed:', e); }
  }

  function renderAutoReplyPanel() {
    var panel = $('autoReplyPanel');
    if (!panel) return;
    var cfg = _autoReplyConfig;
    var isLive = cfg.enabled;
    panel.innerHTML =
      '<div class="auto-reply-header' + (isLive ? ' agent-live' : '') + '" onclick="this.parentElement.classList.toggle(\'expanded\')">' +
        '<span style="display:flex;align-items:center;gap:8px">🤖 AI Outreach Agent' +
          (isLive ? '<span class="ar-status on"><span class="ar-pulse"></span>LIVE</span>' : '<span class="ar-status off">OFF</span>') +
          (isLive && cfg.ai_enabled ? '<span class="ar-mode-badge">GPT-4o</span>' : '') +
          (isLive && cfg.conversational_mode !== false ? '<span class="ar-mode-badge">Multi-Turn</span>' : '') +
          (cfg.last_poll_at ? '<span style="font-size:10px;color:var(--muted)">Polled ' + timeAgo(cfg.last_poll_at) + '</span>' : '') +
        '</span>' +
        '<span style="display:flex;align-items:center;gap:8px"><label class="ar-switch" onclick="event.stopPropagation()"><input type="checkbox"' + (cfg.enabled ? ' checked' : '') + ' onchange="SLProspective.updateAutoReply({enabled:this.checked})"><span class="ar-slider"></span></label><span class="ar-toggle-arrow">▼</span></span>' +
      '</div>' +
      '<div class="auto-reply-body">' +
        '<div class="ar-grid">' +
          '<div class="ar-row"><label>AI-Powered (GPT-4o)</label><input type="checkbox"' + (cfg.ai_enabled ? ' checked' : '') + ' onchange="SLProspective.updateAutoReply({ai_enabled:this.checked})"></div>' +
          '<div class="ar-row"><label>Conversational Mode</label><input type="checkbox"' + (cfg.conversational_mode !== false ? ' checked' : '') + ' onchange="SLProspective.updateAutoReply({conversational_mode:this.checked})"></div>' +
          '<div class="ar-row"><label>Simulate Typing</label><input type="checkbox"' + (cfg.simulate_typing ? ' checked' : '') + ' onchange="SLProspective.updateAutoReply({simulate_typing:this.checked})"></div>' +
          '<div class="ar-row"><label>Auto Mark Read</label><input type="checkbox"' + (cfg.auto_mark_read ? ' checked' : '') + ' onchange="SLProspective.updateAutoReply({auto_mark_read:this.checked})"></div>' +
          '<div class="ar-row"><label>Reply Cooldown</label><div style="display:flex;align-items:center;gap:6px"><input type="number" value="' + (cfg.cooldown_minutes || 5) + '" min="1" max="120" style="width:60px" onchange="SLProspective.updateAutoReply({cooldown_minutes:parseInt(this.value)})"><span style="font-size:11px;color:var(--muted)">min</span></div></div>' +
        '</div>' +
        '<div style="display:flex;align-items:center;gap:12px;margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">' +
          '<button class="btn-poll" onclick="SLProspective.manualPoll()">🔄 Poll Inbox Now</button>' +
          '<span style="font-size:11px;color:var(--muted)">Last: ' + (cfg.last_poll_at ? timeAgo(cfg.last_poll_at) : 'never') + ' · Every ' + (cfg.poll_interval_seconds || 30) + 's</span>' +
        '</div>' +
      '</div>';
    if (isLive) panel.classList.add('expanded');
  }

  async function updateAutoReply(updates) {
    try {
      var r = await fetch(API + '/api/imessage/auto-reply/config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(updates)
      });
      var d = await r.json();
      if (d.success) { _autoReplyConfig = d.config; renderAutoReplyPanel(); toast('Auto-reply updated', 'success'); }
    } catch (e) { toast('Update failed: ' + e.message, 'error'); }
  }

  async function manualPoll() {
    try {
      toast('Polling inbox...', 'info');
      var r = await fetch(API + '/api/imessage/inbox/poll', { method: 'POST' });
      var d = await r.json();
      toast('Poll: ' + (d.matched || 0) + ' matched, ' + (d.replied || 0) + ' replied', 'success');
      loadAutoReplyConfig();
      load();
    } catch (e) { toast('Poll failed: ' + e.message, 'error'); }
  }

  // ── Message Actions ────────────────────────────────────────────────────────
  async function unsendMsg(bk, commIdx) {
    var bond = _data.find(function(b) { return b.booking_number === bk; });
    var comm = bond && bond.communication_log && bond.communication_log[commIdx];
    if (!comm || !comm.bb_message_guid) { toast('Cannot unsend — no message GUID', 'error'); return; }
    if (!confirm('Unsend this message?')) return;
    try {
      var r = await fetch(API + '/api/imessage/unsend', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message_guid: comm.bb_message_guid })
      });
      var d = await r.json();
      if (d.success) { toast('Message unsent', 'success'); setTimeout(function() { openDetail(bk); }, 300); }
      else toast('Unsend failed: ' + (d.error || ''), 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  async function editMsg(bk, commIdx) {
    var bond = _data.find(function(b) { return b.booking_number === bk; });
    var comm = bond && bond.communication_log && bond.communication_log[commIdx];
    if (!comm) return;
    var newText = prompt('Edit message:', comm.message || '');
    if (!newText || newText === comm.message) return;
    try {
      var r = await fetch(API + '/api/imessage/edit', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message_guid: comm.bb_message_guid || '', new_text: newText })
      });
      var d = await r.json();
      if (d.success) { toast('Message edited', 'success'); setTimeout(function() { openDetail(bk); }, 300); }
      else toast('Edit failed: ' + (d.error || ''), 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  async function reactMsg(bk, commIdx) {
    var bond = _data.find(function(b) { return b.booking_number === bk; });
    var comm = bond && bond.communication_log && bond.communication_log[commIdx];
    if (!comm) return;
    var phone = (bond && bond.indemnitor && (bond.indemnitor.phone || bond.indemnitor.callback_phone)) || '';
    if (!phone) { toast('No phone number for chat', 'error'); return; }
    var reactions = ['love', 'like', 'dislike', 'laugh', 'emphasize', 'question'];
    var emojis = ['❤️', '👍', '👎', '😂', '❗', '❓'];
    var choice = prompt('React with:\n' + reactions.map(function(r, i) { return (i + 1) + '. ' + emojis[i] + ' ' + r; }).join('\n') + '\n\nEnter number (1-6):');
    if (!choice) return;
    var idx = parseInt(choice) - 1;
    if (idx < 0 || idx >= reactions.length) return;
    try {
      var chatGuid = 'iMessage;-;' + (phone.startsWith('+') ? phone : '+1' + phone.replace(/\D/g, ''));
      var r = await fetch(API + '/api/imessage/react', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_guid: chatGuid, message_guid: comm.bb_message_guid || '', reaction: reactions[idx] })
      });
      var d = await r.json();
      if (d.success) toast(emojis[idx] + ' Reaction sent', 'success');
      else toast('React failed: ' + (d.error || ''), 'error');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
  }

  // ── SSE Integration ────────────────────────────────────────────────────────
  function handleSSEEvent(eventType, data) {
    var bk = (data && (data.booking_number || (data.lead && data.lead.booking_number))) || null;
    if (eventType === 'sms_received' || eventType === 'message_received' || eventType === 'new_reply') {
      var card = bk ? document.querySelector('.pipeline-card[data-bk="' + bk + '"]') : null;
      if (card) { card.classList.add('has-new-reply'); }
      if (_currentBk === bk) {
        load().then(function() { openDetail(bk); });
      } else {
        load();
      }
    } else if (eventType === 'hot_lead' || eventType === 'new_arrest') {
      load();
    }
  }

  // ── Keyboard Shortcuts ─────────────────────────────────────────────────────
  document.addEventListener('keydown', function(e) {
    var tab = document.getElementById('tabProspective');
    if (!tab || tab.style.display === 'none') return;
    if (e.key === 'Escape') { closeDetail(); closeAddModal(); }
    if (e.key === 'n' && !e.ctrlKey && !e.metaKey && !e.target.matches('input,textarea,select')) openAddModal();
  });

  // ── Init ───────────────────────────────────────────────────────────────────
  setTimeout(loadAutoReplyConfig, 800);

  // ── Public API ─────────────────────────────────────────────────────────────
  return {
    load: load, setStage: setStage, debounceSearch: debounceSearch, clearSearch: clearSearch,
    openDetail: openDetail, closeDetail: closeDetail, navDetail: navDetail,
    openAddModal: openAddModal, closeAddModal: closeAddModal, switchAddSource: switchAddSource, submitAddLead: submitAddLead,
    searchArrests: searchArrests, addFromArrest: addFromArrest, searchIntake: searchIntake, addFromIntake: addFromIntake,
    toggleSelect: toggleSelect, clearSelection: clearSelection,
    bulkAdvanceStage: bulkAdvanceStage, bulkSendMessage: bulkSendMessage, bulkStartSequence: bulkStartSequence, bulkClose: bulkClose,
    applyBulkTemplate: applyBulkTemplate, executeBulkMessage: executeBulkMessage,
    quickMessage: quickMessage, quickAdvance: quickAdvance, showIntel: showIntel,
    promptStage: promptStage, updateStage: updateStage,
    saveIndemnitor: saveIndemnitor, discoverContacts: discoverContacts,
    sendMessage: sendMessage, applyTemplate: applyTemplate, addNote: addNote, scheduleMessage: scheduleMessage,
    startSequence: startSequence, stopSequence: stopSequence, viewSequence: viewSequence,
    loadAiSuggestions: loadAiSuggestions, useSuggestion: useSuggestion,
    officialize: officialize, promptClose: promptClose,
    archiveLead: archiveLead, restoreLead: restoreLead, bulkArchive: bulkArchive, toggleArchiveView: toggleArchiveView,
    batchStartOutreach: batchStartOutreach, exportPipeline: exportPipeline,
    loadAutoReplyConfig: loadAutoReplyConfig, renderAutoReplyPanel: renderAutoReplyPanel, updateAutoReply: updateAutoReply, manualPoll: manualPoll,
    unsendMsg: unsendMsg, editMsg: editMsg, reactMsg: reactMsg,
    handleSSEEvent: handleSSEEvent,
    trackLead: function(bk) { openDetail(bk); },
    viewInDefendants: function(bk) {
      // Switch to Defendants tab and pre-filter to this booking number
      var tabBtn = document.querySelector('[data-tab="tabDefendants"]');
      if (tabBtn) { tabBtn.click(); }
      setTimeout(function() {
        var si = document.getElementById('defSearch') || document.getElementById('defendantSearch');
        if (si) { si.value = bk; si.dispatchEvent(new Event('input')); }
      }, 300);
    },
    viewInActiveBonds: function(bk) {
      var tabBtn = document.querySelector('[data-tab="tabActiveBonds"]');
      if (tabBtn) { tabBtn.click(); }
      setTimeout(function() {
        var si = document.getElementById('abSearch');
        if (si) { si.value = bk; si.dispatchEvent(new Event('input')); }
      }, 300);
    },
    toggleAIPanel: function() {
      var wrap = document.getElementById('autoReplyPanelWrap');
      var btn  = document.getElementById('aiAgentToggle');
      if (!wrap) return;
      var visible = wrap.style.display !== 'none';
      wrap.style.display = visible ? 'none' : 'block';
      if (btn) btn.style.background = visible ? '' : 'rgba(139,92,246,.2)';
      if (!visible) loadAutoReplyConfig();
    },
  };
})();
