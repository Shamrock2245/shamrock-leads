/* ═══════════════════════════════════════════════════════════════════════════
   ShamrockLeads — iMessage Control Center  (sl-imessage.js)
   BlueBubbles bridge: health, compose, inbox, automation toggles,
   FindMy geofence, paperwork chase controls.

   KEY FIX: Every API call is individually try/caught — a single 502 (e.g.
   FindMy not configured) NEVER crashes init() or blanks the tab.

   API surface:
     GET  /api/bb-health/status          → connection health + server info
     GET  /api/imessage/inbox            → recent inbound messages (MongoDB)
     POST /api/imessage/inbox/poll       → trigger live BB fetch
     GET  /api/imessage/thread/<phone>   → full conversation history for a phone number
     GET  /api/imessage/findmy           → FindMy device/friend list
     POST /api/imessage/send             → send iMessage {phone, message}
     POST /api/imessage/mark-read        → mark conversation read
     POST /api/imessage/typing           → typing indicator {chat_guid, active}
     GET  /api/automation/config         → automation flags
     POST /api/automation/toggle/<key>   → flip a toggle
     GET  /api/imessage/auto-reply/config → auto-reply settings
     POST /api/imessage/auto-reply/config → update auto-reply settings
   ═══════════════════════════════════════════════════════════════════════════ */
'use strict';
window.SLiMessage = (() => {

  /* ── State ─────────────────────────────────────────────────────────────── */
  let _state = {
    health:         null,
    inbox:          [],
    findmy:         [],
    automation:     {},
    autoReplyConf:  {},
    pollTimer:      null,
    healthInterval: null,
    findMyInterval: null,
    activeThread:   null,
    sending:        false,
    inboxPage:      0,
    inboxLimit:     50,
    filter:         'all',
    searchQ:        '',
    initialized:    false,
  };

  /* ── Constants ─────────────────────────────────────────────────────────── */
  const POLL_MS    = 20_000;
  const HEALTH_MS  = 60_000;
  const FINDMY_MS  = 900_000;
  const API        = window.API_BASE || '';

  /* ── Helpers ───────────────────────────────────────────────────────────── */
  const $  = id => document.getElementById(id);
  const $$ = sel => document.querySelectorAll(sel);

  function fmtPhone(p) {
    if (!p) return '—';
    const d = p.replace(/\D/g, '');
    if (d.length === 10) return `(${d.slice(0,3)}) ${d.slice(3,6)}-${d.slice(6)}`;
    if (d.length === 11) return `+${d[0]} (${d.slice(1,4)}) ${d.slice(4,7)}-${d.slice(7)}`;
    return p;
  }

  function timeAgo(ts) {
    if (!ts) return '—';
    const diff = Date.now() - new Date(ts).getTime();
    if (isNaN(diff)) return '—';
    if (diff < 60_000)    return `${Math.floor(diff/1000)}s ago`;
    if (diff < 3_600_000) return `${Math.floor(diff/60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff/3_600_000)}h ago`;
    return new Date(ts).toLocaleDateString();
  }

  function clampText(s, n = 72) {
    if (!s) return '';
    return s.length > n ? s.slice(0, n) + '…' : s;
  }

  function tagClass(tag) {
    const map = {
      intake: 'sl-badge-blue', checkin: 'sl-badge-green',
      geo: 'sl-badge-purple', payment: 'sl-badge-yellow',
      court: 'sl-badge-orange', general: 'sl-badge-gray',
    };
    return map[tag] || 'sl-badge-gray';
  }

  function setStatus(el, status, label) {
    if (!el) return;
    el.className = `bb-status-dot bb-status-${status}`;
    el.setAttribute('title', label);
  }

  /* ── Safe fetch — NEVER throws, always returns {ok, data, error} ──────── */
  async function safeFetch(path, opts = {}) {
    try {
      const r = await fetch(API + path, {
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        ...opts,
      });
      const data = await r.json().catch(() => ({}));
      return { ok: r.ok, status: r.status, data };
    } catch (err) {
      return { ok: false, status: 0, data: {}, error: err.message };
    }
  }

  /* ── apiFetch — throws on non-ok (for callers that want to catch) ──────── */
  async function apiFetch(path, opts = {}) {
    const { ok, status, data, error } = await safeFetch(path, opts);
    if (!ok) throw new Error(error || `${status}`);
    return data;
  }

  function showToast(msg, type = 'info') {
    if (window.SLToast) { SLToast(msg, type); return; }
    if (window.showToast && window.showToast !== showToast) { window.showToast(msg, type); return; }
    if (window.SL?.toast) { SL.toast(msg, type); return; }
    console.log(`[BB ${type}] ${msg}`);
  }

  function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  /* ── Init ──────────────────────────────────────────────────────────────── */
  function init() {
    if (_state.initialized) {
      loadHealth(); loadInbox(); loadFindMy();
      return;
    }
    _state.initialized = true;

    /* Render the layout skeleton immediately so the tab is never blank */
    _renderSkeleton();

    /* Kick off all data loads independently — failures are isolated */
    loadHealth();
    loadInbox();
    loadFindMy();
    loadAutomationConfig();
    loadAutoReplyConfig();

    /* Polling intervals */
    _state.pollTimer      = setInterval(loadInbox,  POLL_MS);
    _state.healthInterval = setInterval(loadHealth, HEALTH_MS);
    _state.findMyInterval = setInterval(loadFindMy, FINDMY_MS);

    /* Compose listeners */
    const compose = $('bbComposeText');
    if (compose) {
      compose.addEventListener('keydown', e => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') sendMessage();
      });
      let _typingTimer = null;
      compose.addEventListener('input', () => {
        const btn = $('bbSendBtn');
        if (btn) btn.disabled = !compose.value.trim();
        if (!_state.activeThread) return;
        clearTimeout(_typingTimer);
        safeFetch('/api/imessage/typing', {
          method: 'POST',
          body: JSON.stringify({ chat_guid: 'any;-;' + _state.activeThread, active: true }),
        });
        _typingTimer = setTimeout(() => {
          safeFetch('/api/imessage/typing', {
            method: 'POST',
            body: JSON.stringify({ chat_guid: 'any;-;' + _state.activeThread, active: false }),
          });
        }, 5000);
      });
    }

    /* Search */
    const srch = $('bbInboxSearch');
    if (srch) srch.addEventListener('input', debounce(() => {
      _state.searchQ = srch.value.trim();
      _state.inboxPage = 0;
      renderInbox();
    }, 250));

    /* Manual refresh button — triggers live BB poll then DB read */
    const refreshBtn = $('bbInboxRefresh');
    if (refreshBtn) {
      refreshBtn.removeAttribute('onclick');
      refreshBtn.addEventListener('click', async () => {
        const origHtml = refreshBtn.innerHTML;
        refreshBtn.innerHTML = '⏳ Polling…';
        refreshBtn.disabled = true;
        await safeFetch('/api/imessage/inbox/poll', { method: 'POST' });
        await loadInbox();
        refreshBtn.innerHTML = origHtml;
        refreshBtn.disabled = false;
      });
    }
  }

  /* ── Render skeleton so the tab is never blank ─────────────────────────── */
  function _renderSkeleton() {
    const inboxEl = $('bbInboxList');
    if (inboxEl && inboxEl.children.length <= 1) {
      inboxEl.innerHTML = `
        <div class="sl-empty-state" style="padding:40px 16px">
          <div class="sl-empty-state-icon">💬</div>
          <div class="sl-empty-state-title">Loading inbox…</div>
          <div class="sl-empty-state-desc">Fetching messages from MongoDB</div>
        </div>`;
    }
    const findMyEl = $('bbFindMyBody');
    if (findMyEl && findMyEl.children.length <= 1) {
      findMyEl.innerHTML = `
        <div class="sl-empty-state" style="padding:24px">
          <div class="sl-empty-state-icon">📍</div>
          <div class="sl-empty-state-title">Loading FindMy…</div>
        </div>`;
    }
    const healthEl = $('bbHealthBody');
    if (healthEl && healthEl.children.length <= 1) {
      healthEl.innerHTML = `
        <div class="sl-empty-state" style="padding:24px">
          <div class="sl-empty-state-icon">🔌</div>
          <div class="sl-empty-state-title">Connecting to BlueBubbles…</div>
        </div>`;
    }
  }

  function destroy() {
    clearInterval(_state.pollTimer);
    clearInterval(_state.healthInterval);
    clearInterval(_state.findMyInterval);
    _state.initialized = false;
  }

  /* ── Health ────────────────────────────────────────────────────────────── */
  async function loadHealth() {
    const { ok, data } = await safeFetch('/api/bb-health/status');
    if (ok && data) {
      _state.health = data;
      renderHealth(data);
    } else {
      _renderHealthOffline('BlueBubbles unreachable — check tunnel');
    }
  }

  function renderHealth(d) {
    const servers = d.servers || [];
    const first   = servers[0] || d;

    /* Status dot + label */
    const online = first.reachable || d.connected || d.status === 'healthy';
    setStatus($('bbConnDot'), online ? 'ok' : 'offline', online ? 'Connected' : 'Offline');
    const lbl = $('bbConnLabel');
    if (lbl) lbl.textContent = online ? '🟢 Connected' : '🔴 Offline';

    /* KPI strip */
    const kpis = {
      bbKpiStatus:   online ? '🟢 Online' : '🔴 Offline',
      bbKpiVersion:  first.version || d.version || '—',
      bbKpiPrivApi:  (first.private_api_connected || d.private_api_enabled) ? '✅ Active' : '⚠️ Off',
      bbKpiMessages: (first.message_count || d.message_count || 0).toLocaleString(),
      bbKpiUptime:   _fmtUptime(first.uptime || first.uptime_seconds || d.uptime || 0),
    };
    Object.entries(kpis).forEach(([id, val]) => { const el = $(id); if (el) el.textContent = val; });

    /* Detail panel */
    const body = $('bbHealthBody');
    if (!body) return;

    const serverRows = servers.length > 0 ? servers.map(s => `
      <div class="bb-health-item" style="grid-column:1/-1;border-top:1px solid var(--border);padding-top:8px;margin-top:4px">
        <span class="bb-health-key">${s.server || s.label || 'Server'}</span>
        <span class="bb-health-val" style="color:${s.status==='healthy'?'#10b981':s.status==='degraded'?'#f59e0b':'#ef4444'}">
          ${s.status?.toUpperCase() || '—'}
          ${s.issues?.length ? `<br><small style="color:var(--text-muted);font-weight:400">${s.issues.join(' · ')}</small>` : ''}
        </span>
      </div>`).join('') : '';

    body.innerHTML = `
      <div class="bb-health-grid">
        <div class="bb-health-item">
          <span class="bb-health-key">Overall Status</span>
          <span class="bb-health-val" style="color:${online?'#10b981':'#ef4444'}">${d.overall_status?.toUpperCase() || (online?'HEALTHY':'OFFLINE')}</span>
        </div>
        <div class="bb-health-item">
          <span class="bb-health-key">Private API</span>
          <span class="bb-health-val">${(first.private_api_connected||d.private_api_enabled)?'✅ Connected':'⚠️ Disabled'}</span>
        </div>
        <div class="bb-health-item">
          <span class="bb-health-key">Messages.app</span>
          <span class="bb-health-val">${first.messages_app_running!==false?'✅ Running':'🔴 Stopped'}</span>
        </div>
        <div class="bb-health-item">
          <span class="bb-health-key">Uptime</span>
          <span class="bb-health-val">${_fmtUptime(first.uptime||first.uptime_seconds||d.uptime||0)}</span>
        </div>
        <div class="bb-health-item">
          <span class="bb-health-key">Last Checked</span>
          <span class="bb-health-val">${timeAgo(d.checked_at||d.timestamp)}</span>
        </div>
        <div class="bb-health-item">
          <span class="bb-health-key">Total Messages</span>
          <span class="bb-health-val">${(first.message_count||d.message_count||0).toLocaleString()}</span>
        </div>
        ${serverRows}
      </div>
      <div style="margin-top:12px;display:flex;gap:8px">
        <button class="sl-btn sl-btn-ghost sl-btn-sm" onclick="SLiMessage.loadHealth()">↻ Re-check</button>
        <button class="sl-btn sl-btn-ghost sl-btn-sm" onclick="SLiMessage._restartMessages()">🔄 Restart Messages.app</button>
      </div>
      ${_reconnectPanelHTML()}`;
  }

  function _renderHealthOffline(msg) {
    setStatus($('bbConnDot'), 'offline', 'Offline');
    const lbl = $('bbConnLabel');
    if (lbl) lbl.textContent = '🔴 Offline';
    ['bbKpiStatus','bbKpiVersion','bbKpiPrivApi','bbKpiMessages','bbKpiUptime']
      .forEach(id => { const el = $(id); if (el) el.textContent = '—'; });
    const body = $('bbHealthBody');
    if (body) body.innerHTML = `
      <div class="sl-empty-state">
        <div class="sl-empty-state-icon">🔌</div>
        <div class="sl-empty-state-title">BlueBubbles Unreachable</div>
        <div class="sl-empty-state-desc">${msg}</div>
        <button class="sl-btn sl-btn-secondary" style="margin-top:12px" onclick="SLiMessage.loadHealth()">↻ Retry</button>
      </div>
      ${_reconnectPanelHTML()}`;
  }

  /* ── Reconnect Panel — paste ngrok URL, hot-swap without rebuild ──── */  function _reconnectPanelHTML() {
    return `
    <div class="bb-reconnect-panel" id="bbReconnectPanel">
      <div class="bb-reconnect-header">
        <span class="bb-reconnect-icon">🔗</span>
        <div>
          <div class="bb-reconnect-title">Update Tunnel URL</div>
          <div class="bb-reconnect-subtitle">ngrok tunnel URLs may change on BlueBubbles restart. Paste the new URL here — no VPS rebuild needed.</div>        </div>
      </div>
      <div class="bb-reconnect-fields">
        <div class="bb-reconnect-field">
          <label class="bb-reconnect-label">Phone Line</label>
          <select id="bbReconnectSuffix" class="sl-select sl-select-sm">
            <option value="0178">239-955-0178 (Office iMac)</option>
            <option value="0314">239-955-0314 (Brendan Mac)</option>
          </select>
        </div>
        <div class="bb-reconnect-field bb-reconnect-field--url">
          <label class="bb-reconnect-label">New Tunnel URL <span style="color:var(--text-muted);font-weight:400">(from BlueBubbles → Settings → Connection)</span></label>
          <div class="bb-reconnect-url-row">
            <input id="bbReconnectUrl" class="sl-input sl-input-sm" type="url"
              placeholder="https://something-new.ngrok-free.app"              onkeydown="if(event.key==='Enter')SLiMessage.updateTunnelUrl()"
            />
            <button id="bbReconnectBtn" class="sl-btn sl-btn-primary sl-btn-sm" onclick="SLiMessage.updateTunnelUrl()">
              🔌 Reconnect
            </button>
          </div>
        </div>
      </div>
      <div id="bbReconnectStatus" class="bb-reconnect-status" style="display:none"></div>
    </div>`;
  }

  async function _restartMessages() {
    showToast('Restarting Messages.app…', 'info');
    const { ok } = await safeFetch('/api/bb-health/restart-messages', { method: 'POST' });
    showToast(ok ? 'Messages.app restart triggered' : 'Restart failed — check logs', ok ? 'success' : 'error');
  }

  async function updateTunnelUrl() {
    const urlInput    = $('bbReconnectUrl');
    const suffixEl    = $('bbReconnectSuffix');
    const btn         = $('bbReconnectBtn');
    const statusEl    = $('bbReconnectStatus');
    if (!urlInput) return;

    const newUrl = urlInput.value.trim();
    if (!newUrl) { showToast('Paste a tunnel URL first', 'warning'); return; }
    if (!newUrl.startsWith('https://')) { showToast('URL must start with https://', 'warning'); return; }

    const suffix = suffixEl?.value || '0178';

    // Disable button, show spinner
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Connecting…'; }
    if (statusEl) { statusEl.style.display = 'none'; }

    const { ok, data } = await safeFetch('/api/bb-health/update-url', {
      method: 'PATCH',
      body: JSON.stringify({
        suffix,
        url: newUrl,
        api_key: 'shamrock-bb-sync-2245',
      }),
    });

    if (btn) { btn.disabled = false; btn.textContent = '🔌 Reconnect'; }

    if (ok && data.success) {
      const reachable = data.connectivity?.reachable;
      if (statusEl) {
        statusEl.style.display = 'block';
        statusEl.className = `bb-reconnect-status ${reachable ? 'bb-reconnect-status--ok' : 'bb-reconnect-status--warn'}`;
        statusEl.textContent = reachable
          ? `✅ Connected! BlueBubbles is live at ${newUrl}`
          : `⚠️ URL saved but server responded: ${data.connectivity?.message || 'unreachable'} — is the tunnel running?`;
      }
      showToast(reachable ? 'iMessage bridge reconnected!' : 'URL saved — verify tunnel is active', reachable ? 'success' : 'warning');
      // Refresh health display after a brief delay
      setTimeout(loadHealth, 1500);
    } else {
      const errMsg = data?.error || 'Update failed';
      if (statusEl) {
        statusEl.style.display = 'block';
        statusEl.className = 'bb-reconnect-status bb-reconnect-status--error';
        statusEl.textContent = `❌ ${errMsg}`;
      }
      showToast(`Reconnect failed: ${errMsg}`, 'error');
    }
  }

  function _fmtUptime(s) {
    if (!s) return '—';
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h > 24) return `${Math.floor(h/24)}d ${h%24}h`;
    return `${h}h ${m}m`;
  }

  /* ── Inbox ─────────────────────────────────────────────────────────────── */
  async function loadInbox() {
    const { ok, data } = await safeFetch('/api/imessage/inbox?limit=50');
    if (ok) {
      _state.inbox = Array.isArray(data) ? data : (data.messages || data.chats || []);
      updateInboxBadge();
      renderInbox();
    }
  }

  function updateInboxBadge() {
    const unread = _state.inbox.filter(m => m.unread || m.is_unread).length;
    const badge = $('bbInboxBadge');
    if (badge) {
      badge.textContent = unread > 0 ? unread : '';
      badge.style.display = unread > 0 ? 'flex' : 'none';
    }
    const tabBadge = $('imessageBadge');
    if (tabBadge) {
      tabBadge.textContent = unread > 0 ? unread : '';
      tabBadge.style.display = unread > 0 ? 'inline' : 'none';
    }
    /* Update the inbox count KPI */
    const kpiInbox = $('bbKpiInbox');
    if (kpiInbox) kpiInbox.textContent = _state.inbox.length.toString();
  }

  function filteredInbox() {
    let msgs = _state.inbox;
    if (_state.filter === 'unread')  msgs = msgs.filter(m => m.unread || m.is_unread);
    if (_state.filter === 'intake')  msgs = msgs.filter(m => (m.category||m.classification) === 'intake');
    if (_state.filter === 'checkin') msgs = msgs.filter(m => (m.category||m.classification) === 'checkin');
    if (_state.filter === 'geo')     msgs = msgs.filter(m => (m.category||m.classification) === 'geo');
    if (_state.searchQ) {
      const q = _state.searchQ.toLowerCase();
      msgs = msgs.filter(m =>
        (m.recipient_phone||m.handle||m.phone||'').toLowerCase().includes(q) ||
        (m.message||m.text||'').toLowerCase().includes(q) ||
        (m.contact_name||m.display_name||'').toLowerCase().includes(q) ||
        (m.booking_number||'').toLowerCase().includes(q)
      );
    }
    return msgs;
  }

  function renderInbox() {
    const body = $('bbInboxList');
    if (!body) return;
    const msgs = filteredInbox();

    if (msgs.length === 0) {
      body.innerHTML = `
        <div class="sl-empty-state" style="padding:32px 16px">
          <div class="sl-empty-state-icon">💬</div>
          <div class="sl-empty-state-title">No messages</div>
          <div class="sl-empty-state-desc">${_state.filter !== 'all' ? 'Try clearing the filter' : 'Inbox is empty — messages appear here when defendants reply'}</div>
        </div>`;
      return;
    }

    body.innerHTML = msgs.map(m => {
      const handle  = m.recipient_phone || m.handle || m.phone || m.address || '';
      const name    = m.contact_name || m.display_name || fmtPhone(handle);
      const preview = clampText(m.message || m.text || m.last_message || '', 72);
      const ts      = timeAgo(m.sent_at || m.date || m.timestamp);
      const unread  = m.unread || m.is_unread;
      const tag     = m.category || m.classification || m.intent;
      const booking = m.booking_number ? `<span style="font-size:9px;color:var(--text-muted)">#${_esc(m.booking_number)}</span>` : '';
      const active  = _state.activeThread === handle ? 'active' : '';
      const dir     = m.direction === 'outbound' ? '↗' : '↙';
      /* Escape handle/name for safe embedding in onclick attribute */
      const safeHandle = handle.replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'&quot;');
      const safeName   = _esc(name).replace(/'/g,"\\'");

      return `
        <div class="bb-thread-row ${active} ${unread ? 'unread' : ''}"
             onclick="SLiMessage.openThread('${safeHandle}', '${safeName}')">
          <div class="bb-thread-avatar" style="background:${_avatarColor(handle)}">${_esc(name).charAt(0).toUpperCase()}</div>
          <div class="bb-thread-meta">
            <div class="bb-thread-name">
              ${_esc(name)} ${booking}
              ${unread ? '<span class="bb-unread-dot"></span>' : ''}
            </div>
            <div class="bb-thread-preview">${dir} ${_esc(preview) || '<em style="opacity:.5">No preview</em>'}</div>
          </div>
          <div class="bb-thread-right">
            <div class="bb-thread-time">${ts}</div>
            ${tag ? `<span class="sl-badge ${tagClass(tag)}" style="font-size:9px;margin-top:2px">${_esc(tag)}</span>` : ''}
          </div>
        </div>`;
    }).join('');
  }

  function _avatarColor(handle) {
    const colors = ['#3b82f6','#10b981','#f59e0b','#8b5cf6','#ef4444','#06b6d4','#ec4899'];
    let h = 0;
    for (let i = 0; i < handle.length; i++) h = (h * 31 + handle.charCodeAt(i)) & 0xffffffff;
    return colors[Math.abs(h) % colors.length];
  }

  function openThread(handle, name) {
    _state.activeThread = handle;
    _state.activeThreadName = name;
    renderInbox();

    const target = $('bbComposeTarget');
    if (target) { target.value = handle; target.dataset.name = name; }
    const newRecip = $('bbNewRecipient');
    if (newRecip) newRecip.value = handle;

    /* Hide the "To:" row when viewing an existing thread */
    const toRow = document.querySelector('.im-to-row');
    if (toRow) toRow.style.display = 'none';

    /* Show loading state in thread view */
    const composeArea = $('bbComposeArea');
    if (composeArea) {
      composeArea.style.padding = '';
      composeArea.style.overflow = '';
      composeArea.innerHTML = `
        <div class="im-thread-loading">
          <div class="im-thread-loading-spinner"></div>
          <div style="font-size:12px;color:var(--muted)">Loading conversation…</div>
        </div>`;
    }

    /* Enable compose */
    const sendBtn = $('bbSendBtn');
    if (sendBtn) sendBtn.disabled = !$('bbComposeText')?.value.trim();

    /* Fetch thread history */
    _loadThread(handle, name);
    markRead(handle);
  }

  async function _loadThread(handle, name) {
    const composeArea = $('bbComposeArea');
    if (!composeArea) return;

    const { ok, data } = await safeFetch(`/api/imessage/thread/${encodeURIComponent(handle)}?limit=100`);

    if (!ok || !data?.messages?.length) {
      composeArea.style.padding = '';
      composeArea.style.overflow = '';
      composeArea.innerHTML = `
        <div class="im-thread-header">
          <div class="im-thread-header-avatar" style="background:${_avatarColor(handle)}">${(name||'?').charAt(0).toUpperCase()}</div>
          <div class="im-thread-header-info">
            <div class="im-thread-header-name">${_esc(name)}</div>
            <div class="im-thread-header-phone">${fmtPhone(handle)}</div>
          </div>
        </div>
        <div class="im-empty-state" style="flex:1">
          <div class="im-empty-icon">💬</div>
          <div class="im-empty-title">No messages yet</div>
          <div class="im-empty-sub">Start the conversation by typing a message below.</div>
        </div>`;
      return;
    }

    const messages = data.messages;
    _state.threadMessages = messages;

    /* Build thread HTML with date separators */
    let html = `
      <div class="im-thread-header">
        <div class="im-thread-header-avatar" style="background:${_avatarColor(handle)}">${(name||'?').charAt(0).toUpperCase()}</div>
        <div class="im-thread-header-info">
          <div class="im-thread-header-name">${_esc(name)}</div>
          <div class="im-thread-header-phone">${fmtPhone(handle)}</div>
        </div>
        <div class="im-thread-header-count">${messages.length} message${messages.length !== 1 ? 's' : ''}</div>
      </div>
      <div id="bbThreadMessages" style="flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:4px;padding:16px;min-height:0">`;

    let lastDateStr = '';
    for (const msg of messages) {
      const ts = msg.sent_at || msg.timestamp || msg.date;
      const dateObj = ts ? new Date(ts) : null;
      const dateStr = dateObj ? dateObj.toLocaleDateString('en-US', { month:'short', day:'numeric', year:'numeric' }) : '';

      /* Insert date separator when date changes */
      if (dateStr && dateStr !== lastDateStr) {
        html += `<div class="im-date-sep">${dateStr}</div>`;
        lastDateStr = dateStr;
      }

      const dir = msg.direction === 'outbound' ? 'outbound' : 'inbound';
      const text = msg.message || msg.text || '';
      const time = dateObj ? dateObj.toLocaleTimeString('en-US', { hour:'numeric', minute:'2-digit' }) : '';
      const agent = msg.sent_by || msg.agent || '';
      const status = msg.status || '';

      /* Status icon for outbound */
      let statusIcon = '';
      if (dir === 'outbound') {
        if (status === 'read') statusIcon = '<span class="im-bubble-status">✓✓</span>';
        else if (status === 'delivered') statusIcon = '<span class="im-bubble-status">✓</span>';
        else if (status === 'sent') statusIcon = '<span class="im-bubble-status">↑</span>';
        else if (status === 'failed') statusIcon = '<span class="im-bubble-status" style="color:#ef4444">✕</span>';
      }

      html += `
        <div class="im-bubble-row ${dir}">
          <div>
            <div class="im-bubble">${_esc(text)}</div>
            <div class="im-bubble-meta">
              ${time ? `<span>${time}</span>` : ''}
              ${agent && dir === 'outbound' ? `<span>• ${_esc(agent)}</span>` : ''}
              ${statusIcon}
            </div>
          </div>
        </div>`;
    }

    html += '</div>';
    composeArea.innerHTML = html;
    /* Override parent scroll/padding — inner bbThreadMessages handles it now */
    composeArea.style.padding = '0';
    composeArea.style.overflow = 'hidden';

    /* Scroll to bottom — target inner message list, fall back to parent */
    requestAnimationFrame(() => {
      const inner = $('bbThreadMessages');
      const target = inner || composeArea;
      if (target) target.scrollTop = target.scrollHeight;
    });

    /* Focus compose */
    $('bbComposeText')?.focus();
  }

  function _esc(s) {
    if (!s) return '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  async function markRead(handle) {
    await safeFetch('/api/imessage/mark-read', {
      method: 'POST',
      body: JSON.stringify({ handle }),
    });
    _state.inbox = _state.inbox.map(m =>
      (m.recipient_phone||m.handle||m.address||m.chat_identifier) === handle
        ? { ...m, unread: false, is_unread: false }
        : m
    );
    updateInboxBadge();
  }

  /* ── Compose & Send ────────────────────────────────────────────────────── */
  async function sendMessage() {
    if (_state.sending) return;
    const text   = $('bbComposeText')?.value.trim();
    const handle = $('bbComposeTarget')?.value.trim() || $('bbNewRecipient')?.value.trim();
    if (!text || !handle) { showToast('Enter a recipient and message', 'error'); return; }

    _state.sending = true;
    const btn = $('bbSendBtn');
    const _sendSvg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>';
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="sl-spinner"></span>'; }

    const { ok, status, data } = await safeFetch('/api/imessage/send', {
      method: 'POST',
      body: JSON.stringify({ phone: handle, message: text, method: 'private-api' }),
    });

    /* Stop typing indicator */
    if (_state.activeThread) {
      safeFetch('/api/imessage/typing', {
        method: 'POST',
        body: JSON.stringify({ chat_guid: 'any;-;' + _state.activeThread, active: false }),
      });
    }

    if (ok) {
      if ($('bbComposeText')) $('bbComposeText').value = '';
      if (btn) btn.innerHTML = '✅';
      setTimeout(() => { if (btn) { btn.innerHTML = _sendSvg; btn.disabled = false; } }, 1800);
      showToast('Message sent via iMessage', 'success');

      /* Append sent bubble to thread immediately (optimistic) */
      const threadView = $('bbThreadMessages');
      if (threadView) {
        const now = new Date();
        const time = now.toLocaleTimeString('en-US', { hour:'numeric', minute:'2-digit' });
        const bubbleHtml = `
          <div class="im-bubble-row outbound">
            <div>
              <div class="im-bubble">${_esc(text)}</div>
              <div class="im-bubble-meta">
                <span>${time}</span>
                <span>• dashboard</span>
                <span class="im-bubble-status">↑</span>
              </div>
            </div>
          </div>`;
        threadView.insertAdjacentHTML('beforeend', bubbleHtml);
        threadView.scrollTop = threadView.scrollHeight;
      }

      await loadInbox();
    } else {
      showToast(`Send failed (${status}) — ${data?.error || 'check BB connection'}`, 'error');
      if (btn) { btn.innerHTML = _sendSvg; btn.disabled = false; }
    }
    _state.sending = false;
  }

  function newCompose() {
    _state.activeThread = null;
    _state.activeThreadName = null;
    const target = $('bbComposeTarget');
    if (target) { target.value = ''; target.dataset.name = ''; }
    const newRecip = $('bbNewRecipient');
    if (newRecip) { newRecip.value = ''; newRecip.focus(); }

    /* Show the "To:" row for new compose */
    const toRow = document.querySelector('.im-to-row');
    if (toRow) toRow.style.display = 'flex';

    const toLabel = $('bbComposeTo');
    if (toLabel) toLabel.textContent = 'New Message';
    const composeArea = $('bbComposeArea');
    if (composeArea) {
      /* Restore default layout when leaving thread view */
      composeArea.style.padding = '';
      composeArea.style.overflow = '';
      composeArea.innerHTML = `
        <div class="im-empty-state" style="flex:1">
          <div class="im-empty-icon">✏️</div>
          <div class="im-empty-title">New Message</div>
          <div class="im-empty-sub">Enter a phone number above to start a new iMessage conversation.</div>
        </div>`;
    }
    const sendBtn = $('bbSendBtn');
    if (sendBtn) sendBtn.disabled = true;
  }

  /* ── FindMy ────────────────────────────────────────────────────────────── */
  async function loadFindMy() {
    const body = $('bbFindMyBody');
    if (!body) return;

    /* FindMy returns 502 when not configured — catch silently, show helpful state */
    const { ok, status, data } = await safeFetch('/api/imessage/findmy');

    if (!ok) {
      const msg = status === 502
        ? 'FindMy not active on this BlueBubbles server. Enable FindMy sharing on the Mac.'
        : status === 503
        ? 'BlueBubbles not configured — set BB_SERVER_URL in .env'
        : `FindMy unavailable (${status})`;
      body.innerHTML = `
        <div class="sl-empty-state" style="padding:24px">
          <div class="sl-empty-state-icon">📍</div>
          <div class="sl-empty-state-title">FindMy Not Available</div>
          <div class="sl-empty-state-desc">${msg}</div>
          <button class="sl-btn sl-btn-ghost sl-btn-sm" style="margin-top:12px" onclick="SLiMessage.loadFindMy()">↻ Retry</button>
        </div>`;
      return;
    }

    _state.findmy = Array.isArray(data) ? data : (data.devices || data.items || data.data || []);
    renderFindMy();
  }

  function renderFindMy() {
    const body = $('bbFindMyBody');
    if (!body) return;
    const devices = _state.findmy;

    if (!devices.length) {
      body.innerHTML = `
        <div class="sl-empty-state" style="padding:24px">
          <div class="sl-empty-state-icon">📍</div>
          <div class="sl-empty-state-title">No FindMy Devices Enrolled</div>
          <div class="sl-empty-state-desc">Enable FindMy location sharing on the defendant's device, then tap Retry.</div>
          <button class="sl-btn sl-btn-ghost sl-btn-sm" style="margin-top:12px" onclick="SLiMessage.loadFindMy()">↻ Retry</button>
        </div>`;
      return;
    }

    body.innerHTML = devices.map(d => {
      const loc   = d.location || {};
      const lat   = loc.latitude  != null ? loc.latitude.toFixed(4)  : '—';
      const lng   = loc.longitude != null ? loc.longitude.toFixed(4) : '—';
      const acc   = loc.horizontalAccuracy != null ? `±${Math.round(loc.horizontalAccuracy)}m` : '';
      const ts    = timeAgo(loc.timestamp || d.timestamp || d.lastSeen);
      const name  = d.name || d.deviceName || d.handle || 'Unknown Device';
      const model = d.deviceModel || d.model || '';
      const bat   = d.batteryLevel != null ? `🔋 ${Math.round(d.batteryLevel * 100)}%` : '';
      const mapLink = lat !== '—' ? `https://maps.google.com/?q=${lat},${lng}` : null;

      return `
        <div style="padding:10px 12px;border-bottom:1px solid var(--border);display:flex;flex-direction:column;gap:4px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div style="font-weight:600;font-size:0.85rem">📱 ${name}</div>
            <div style="font-size:0.75rem;color:var(--text-muted)">${bat}</div>
          </div>
          ${model ? `<div style="font-size:0.75rem;color:var(--text-muted)">${model}</div>` : ''}
          <div style="font-size:0.78rem;color:var(--text-muted)">
            ${lat !== '—' ? `${lat}, ${lng} ${acc}` : 'Location unavailable'}
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-top:2px">
            <span style="font-size:0.72rem;color:var(--text-muted)">${ts}</span>
            ${mapLink ? `<a href="${mapLink}" target="_blank" class="sl-btn sl-btn-ghost sl-btn-sm" style="font-size:0.72rem;padding:2px 8px">🗺 Map</a>` : ''}
          </div>
        </div>`;
    }).join('');
  }

  /* ── Automation Toggles ────────────────────────────────────────────────── */
  async function loadAutomationConfig() {
    const { ok, data } = await safeFetch('/api/automation/config');
    if (ok) {
      _state.automation = data.config || data || {};
      renderToggles();
      /* Update geofence radius in description */
      const gf = _state.automation['findmy_geofence'] || {};
      const miles = gf.geofence_miles || 25;
      const desc = document.querySelector('[data-toggle-desc="findmy_geofence"]');
      if (desc) desc.textContent = `Alert on breach of ${miles}-mile Lee County geofence`;
    }
  }

  function renderToggles() {
    const cfg   = _state.automation;
    const panel = $('bbAutoToggles');
    if (!panel) return;

    const toggles = [
      { key: 'speed_to_contact', label: '⚡ Speed-to-Contact',  desc: 'Auto-outreach for hot leads via iMessage', isStc: true },
      { key: 'paperwork_chase',  label: '📄 Paperwork Chase',   desc: 'Reminders every 2h until signed' },
      { key: 'intake_recovery',  label: '♻️ Intake Recovery',   desc: 'Follow up on abandoned intakes' },
      { key: 'auto_reply',       label: '🤖 Auto-Reply AI',     desc: 'AI responds to inbound messages' },
      { key: 'findmy_geofence',  label: '🛡 FindMy Geofence',  desc: 'Alert on Lee County boundary breach' },
    ];

    panel.innerHTML = toggles.map(t => {
      const section = cfg[t.key] || {};
      const on = section.enabled === true;

      // Speed-to-Contact gets a 3-level mode selector instead of simple toggle
      if (t.isStc) {
        const mode = section.mode || 'off';
        const isOff = !on || mode === 'off';
        const isReview = on && mode === 'review';
        const isAuto = on && mode === 'full_auto';

        return `
          <div class="bb-toggle-row" id="toggle_row_${t.key}" style="flex-direction:column;align-items:stretch;gap:8px">
            <div style="display:flex;justify-content:space-between;align-items:center">
              <div class="bb-toggle-info">
                <div class="bb-toggle-label">${t.label}</div>
                <div class="bb-toggle-desc" data-toggle-desc="${t.key}">${t.desc}</div>
              </div>
            </div>
            <div class="stc-mode-selector" style="display:flex;gap:0;border-radius:8px;overflow:hidden;border:1px solid var(--border)">
              <button class="stc-mode-btn ${isOff ? 'stc-mode-active stc-mode-off' : ''}"
                      onclick="SLiMessage.setStcMode('off')" style="flex:1;padding:8px 4px;font-size:11px;font-weight:600;border:0;cursor:pointer;transition:all .2s;
                      background:${isOff ? '#ef4444' : 'var(--surface)'};color:${isOff ? '#fff' : 'var(--text-muted)'}">
                🔴 Off
              </button>
              <button class="stc-mode-btn ${isReview ? 'stc-mode-active stc-mode-review' : ''}"
                      onclick="SLiMessage.setStcMode('review')" style="flex:1;padding:8px 4px;font-size:11px;font-weight:600;border:0;border-left:1px solid var(--border);border-right:1px solid var(--border);cursor:pointer;transition:all .2s;
                      background:${isReview ? '#f59e0b' : 'var(--surface)'};color:${isReview ? '#fff' : 'var(--text-muted)'}">
                🟡 Review
              </button>
              <button class="stc-mode-btn ${isAuto ? 'stc-mode-active stc-mode-auto' : ''}"
                      onclick="SLiMessage.setStcMode('full_auto')" style="flex:1;padding:8px 4px;font-size:11px;font-weight:600;border:0;cursor:pointer;transition:all .2s;
                      background:${isAuto ? '#10b981' : 'var(--surface)'};color:${isAuto ? '#fff' : 'var(--text-muted)'}">
                🟢 Full Auto
              </button>
            </div>
            <div style="font-size:10px;color:var(--text-muted);line-height:1.3;padding:0 2px">
              ${isOff ? 'Disabled — no auto-outreach' : isReview ? 'Messages queued for your approval before sending' : 'Messages sent automatically to hot leads (score ≥ ' + (section.min_lead_score || 70) + ')'}
            </div>
          </div>`;
      }

      // Standard on/off toggle for other services
      return `
        <div class="bb-toggle-row" id="toggle_row_${t.key}">
          <div class="bb-toggle-info">
            <div class="bb-toggle-label">${t.label}</div>
            <div class="bb-toggle-desc" data-toggle-desc="${t.key}">${t.desc}</div>
          </div>
          <button class="bb-toggle-switch ${on ? 'on' : 'off'}"
                  id="toggle_${t.key}"
                  onclick="SLiMessage.toggle('${t.key}')"
                  aria-label="${t.label} ${on ? 'on' : 'off'}">
            <span class="bb-toggle-knob"></span>
          </button>
        </div>`;
    }).join('');
  }

  async function setStcMode(mode) {
    // mode: 'off' | 'review' | 'full_auto'
    const enabled = mode !== 'off';
    const { ok, data } = await safeFetch('/api/automation/config', {
      method: 'POST',
      body: JSON.stringify({
        "speed_to_contact.enabled": enabled,
        "speed_to_contact.mode": mode,
      }),
    });
    if (ok) {
      // Re-fetch full config to sync state
      await loadAutomationConfig();
      const labels = { off: 'Off', review: 'Review Queue', full_auto: 'Full Auto' };
      showToast(`⚡ Speed-to-Contact → ${labels[mode] || mode}`, 'success');
    } else {
      showToast('Mode change failed — check logs', 'error');
    }
  }

  async function toggle(key) {
    const btn = $(`toggle_${key}`);
    if (btn) btn.style.opacity = '0.5';
    const { ok, data } = await safeFetch(`/api/automation/toggle/${key}`, { method: 'POST' });
    if (ok) {
      _state.automation = data.config || _state.automation;
      renderToggles();
      showToast(`${key.replace(/_/g,' ')} ${data.enabled ? 'enabled' : 'disabled'}`, 'success');
    } else {
      showToast(`Toggle failed for ${key}`, 'error');
      if (btn) btn.style.opacity = '1';
    }
  }

  /* ── Auto-Reply Config ─────────────────────────────────────────────────── */
  async function loadAutoReplyConfig() {
    const { ok, data } = await safeFetch('/api/imessage/auto-reply/config');
    if (!ok) return;
    _state.autoReplyConf = data.config || data || {};
    const el = $('bbAutoReplyKeywords');
    if (el && _state.autoReplyConf.keywords) el.value = _state.autoReplyConf.keywords.join(', ');
    const thresh = $('bbAutoReplyThresh');
    if (thresh && _state.autoReplyConf.confidence_threshold != null) {
      thresh.value = _state.autoReplyConf.confidence_threshold;
    }
  }

  async function saveAutoReplyConfig() {
    const btn = $('bbSaveAutoReplyBtn');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    const keywords  = ($('bbAutoReplyKeywords')?.value || '').split(',').map(s => s.trim()).filter(Boolean);
    const threshold = parseFloat($('bbAutoReplyThresh')?.value || '0.8');
    const { ok } = await safeFetch('/api/imessage/auto-reply/config', {
      method: 'POST',
      body: JSON.stringify({ keywords, confidence_threshold: threshold }),
    });
    showToast(ok ? 'Auto-reply config saved ✅' : 'Save failed', ok ? 'success' : 'error');
    if (btn) { btn.disabled = false; btn.textContent = '💾 Save Config'; }
  }

  /* ── Filter / Search ───────────────────────────────────────────────────── */
  function setFilter(f, el) {
    _state.filter = f;
    $$('.bb-inbox-filter-btn').forEach(b => b.classList.remove('active'));
    if (el) el.classList.add('active');
    _state.inboxPage = 0;
    renderInbox();
  }

  /* ── Public API ────────────────────────────────────────────────────────── */
  /* ── openCompose: callable from any tab ─────────────────────────────────── */
  function openCompose(bookingOrPhone, name) {
    // Switch to iMessage tab first
    if (typeof SL !== 'undefined' && typeof SL.switchTab === 'function') {
      SL.switchTab('tabImessage');
    }
    // Ensure the iMessage tab is initialized (loads inbox, health, etc.)
    init();

    // Determine if the first arg is a phone number or a booking number
    const digits = (bookingOrPhone || '').replace(/\D/g, '');
    const isPhone = digits.length >= 10;

    // Pre-fill the compose area after a short delay for tab transition
    setTimeout(() => {
      const phoneEl  = $('bbNewRecipient');
      const targetEl = $('bbComposeTarget');
      const toLabel  = $('bbComposeTo');

      if (isPhone) {
        // It's a real phone number — pre-fill both fields
        const formatted = digits.length === 10 ? '+1' + digits : '+' + digits;
        if (phoneEl)  phoneEl.value = formatted;
        if (targetEl) targetEl.value = formatted;
        if (toLabel)  toLabel.textContent = name || formatted;
      } else {
        // It's a booking number — clear the phone field and prompt user
        if (phoneEl)  { phoneEl.value = ''; phoneEl.placeholder = 'Enter phone number for ' + (name || bookingOrPhone); }
        if (targetEl) targetEl.value = '';
        if (toLabel)  toLabel.textContent = name ? `${name} (${bookingOrPhone})` : bookingOrPhone || '';
      }

      // Show the To: row for new compose
      const toRow = document.querySelector('.im-to-row');
      if (toRow) toRow.style.display = 'flex';

      // Show empty compose area (new message state)
      const composeArea = $('bbComposeArea');
      if (composeArea) {
        composeArea.style.padding = '';
        composeArea.style.overflow = '';
        composeArea.innerHTML = `
          <div class="im-empty-state" style="flex:1">
            <div class="im-empty-icon">💬</div>
            <div class="im-empty-title">${name ? 'Message ' + _esc(name) : 'New Message'}</div>
            <div class="im-empty-sub">${isPhone ? 'Type your message below and hit send.' : 'Enter the phone number above, then type your message.'}</div>
          </div>`;
      }

      const sendBtn = $('bbSendBtn');
      if (sendBtn) sendBtn.disabled = !isPhone;

      // Focus the right field
      if (isPhone) {
        const textEl = $('bbComposeText');
        if (textEl) textEl.focus();
      } else {
        if (phoneEl) phoneEl.focus();
      }
    }, 200);
  }

  return {
    init, destroy,
    loadHealth, loadInbox, loadFindMy,
    loadAutomationConfig, loadAutoReplyConfig,
    sendMessage, newCompose, openThread, openCompose,
    toggle, setStcMode, setFilter,
    saveAutoReplyConfig,
    updateTunnelUrl,
    _restartMessages,
    refresh() { loadHealth(); loadInbox(); loadFindMy(); },
  };
})();
