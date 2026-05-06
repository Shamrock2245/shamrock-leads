/* ═══════════════════════════════════════════════════════════════════════════
   ShamrockLeads — iMessage Control Center  (sl-imessage.js)
   BlueBubbles integration: health, compose, inbox, automation toggles,
   FindMy geofence, paperwork chase controls.

   API surface consumed:
     GET  /api/bb-health/status          → connection health + server info
     GET  /api/imessage/inbox            → recent inbound messages
     GET  /api/imessage/inbox/poll       → long-poll for new messages (POST)
     GET  /api/imessage/findmy           → FindMy device list
     POST /api/imessage/send             → send iMessage
     POST /api/imessage/mark-read        → mark conversation read
     POST /api/imessage/typing           → typing indicator
     GET  /api/automation/config         → automation flags
     POST /api/automation/toggle/<key>   → flip a toggle
     GET  /api/imessage/auto-reply/config → auto-reply settings
     POST /api/imessage/auto-reply/config → update auto-reply settings
   ═══════════════════════════════════════════════════════════════════════════ */

'use strict';

const SLiMessage = (() => {
  /* ── State ─────────────────────────────────────────────────────────────── */
  let _state = {
    health:         null,
    inbox:          [],
    findmy:         [],
    automation:     {},
    autoReplyConf:  {},
    pollTimer:      null,
    activeThread:   null,   // handle (phone / chat GUID) of open conversation
    sending:        false,
    inboxPage:      0,
    inboxLimit:     30,
    filter:         'all',  // 'all' | 'unread' | 'intake' | 'checkin'
    searchQ:        '',
    healthInterval: null,
    findMyInterval: null,
    initialized:    false,
  };


  /* ── Constants ─────────────────────────────────────────────────────────── */
  const POLL_MS      = 20_000;   // inbox poll every 20s
  const HEALTH_MS    = 60_000;   // health check every 60s
  const FINDMY_MS    = 900_000;  // FindMy every 15 min
  const API          = window.API_BASE || '';

  /* ── Helpers ───────────────────────────────────────────────────────────── */
  const $  = id => document.getElementById(id);
  const $$ = sel => document.querySelectorAll(sel);
  const html = s => s;   // tagged-template noop — keeps IDE highlighting

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
    if (diff < 60_000)  return `${Math.floor(diff/1000)}s ago`;
    if (diff < 3_600_000) return `${Math.floor(diff/60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff/3_600_000)}h ago`;
    return `${Math.floor(diff/86_400_000)}d ago`;
  }

  function clampText(s, n = 60) {
    if (!s) return '';
    return s.length > n ? s.slice(0, n) + '…' : s;
  }

  function tagClass(tag) {
    const map = {
      intake: 'sl-badge-blue',
      checkin: 'sl-badge-green',
      geo: 'sl-badge-purple',
      paperwork: 'sl-badge-yellow',
      reply: 'sl-badge-orange',
      unknown: 'sl-badge-gray',
    };
    return map[tag] || 'sl-badge-gray';
  }

  async function apiFetch(path, opts = {}) {
    const r = await fetch(API + path, {
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      ...opts,
    });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  }

  function setStatus(el, status, label) {
    if (!el) return;
    el.className = `bb-status-dot bb-status-${status}`;
    el.setAttribute('title', label);
  }

  /* ── Init ──────────────────────────────────────────────────────────────── */
  function init() {
    if (_state.initialized) {
      // Already running — just refresh data, don't stack intervals
      loadHealth(); loadInbox(); loadFindMy();
      return;
    }
    _state.initialized = true;

    // Kick off all initial loads in parallel
    loadHealth();
    loadInbox();
    loadFindMy();
    loadAutomationConfig();
    loadAutoReplyConfig();

    // Polling
    _state.pollTimer      = setInterval(loadInbox,          POLL_MS);
    _state.healthInterval = setInterval(loadHealth,         HEALTH_MS);
    _state.findMyInterval = setInterval(loadFindMy,         FINDMY_MS);

    // Compose area listeners
    const compose = $('bbComposeText');
    if (compose) {
      compose.addEventListener('keydown', e => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') sendMessage();
      });
      // Gap 5: send/stop typing indicator as user types
      let _typingTimer = null;
      compose.addEventListener('input', () => {
        const btn = $('bbSendBtn');
        if (btn) btn.disabled = !compose.value.trim();
        // Fire typing indicator if a thread is open
        if (!_state.activeThread) return;
        clearTimeout(_typingTimer);
        apiFetch('/api/imessage/typing', {
          method: 'POST',
          body: JSON.stringify({ chat_guid: 'any;-;' + _state.activeThread, active: true }),
        }).catch(() => {});
        // Auto-stop after 5 seconds of no typing
        _typingTimer = setTimeout(() => {
          apiFetch('/api/imessage/typing', {
            method: 'POST',
            body: JSON.stringify({ chat_guid: 'any;-;' + _state.activeThread, active: false }),
          }).catch(() => {});
        }, 5000);
      });
    }

    // Search
    const srch = $('bbInboxSearch');
    if (srch) srch.addEventListener('input', debounce(() => {
      _state.searchQ = srch.value.trim();
      _state.inboxPage = 0;
      renderInbox();
    }, 250));

    // Gap 6: Manual poll button — trigger live BB fetch, then refresh DB read
    const refreshBtn = $('bbInboxRefresh');
    if (refreshBtn) {
      // Remove the inline onclick set in index.html and replace with live-poll handler
      refreshBtn.removeAttribute('onclick');
      refreshBtn.addEventListener('click', async () => {
        const orig = refreshBtn.textContent;
        refreshBtn.textContent = '⏳';
        refreshBtn.disabled = true;
        try {
          await apiFetch('/api/imessage/inbox/poll', { method: 'POST' });
        } catch (_) { /* silent — poll may not be critical */ }
        await loadInbox();
        refreshBtn.textContent = orig;
        refreshBtn.disabled = false;
      });
    }
  }

  function destroy() {
    clearInterval(_state.pollTimer);
    clearInterval(_state.healthInterval);
    clearInterval(_state.findMyInterval);
  }

  /* ── Health ────────────────────────────────────────────────────────────── */
  async function loadHealth() {
    const panel = $('bbHealthBody');
    if (!panel) return;

    try {
      const data = await apiFetch('/api/bb-health/status');
      _state.health = data;
      renderHealth(data);
    } catch (err) {
      renderHealthError(err.message);
    }
  }

  function renderHealth(d) {
    // Top KPI dots
    const dot = $('bbConnDot');
    const lbl = $('bbConnLabel');
    const online = d.connected || d.status === 'ok' || d.server_online;
    setStatus(dot, online ? 'ok' : 'offline', online ? 'Connected' : 'Offline');
    if (lbl) lbl.textContent = online ? 'Connected' : 'Offline';

    // Header KPIs
    const kpis = [
      { id: 'bbKpiStatus',   val: online ? '🟢 Online'  : '🔴 Offline' },
      { id: 'bbKpiVersion',  val: d.version || d.server_version || '—' },
      { id: 'bbKpiPrivApi',  val: d.private_api_enabled ? '✅ Enabled' : '⚠️ Off' },
      { id: 'bbKpiMessages', val: d.total_messages != null ? d.total_messages.toLocaleString() : '—' },
    ];
    kpis.forEach(({ id, val }) => { const el = $(id); if (el) el.textContent = val; });

    // Detail panel
    const body = $('bbHealthBody');
    if (!body) return;
    body.innerHTML = `
      <div class="bb-health-grid">
        <div class="bb-health-item">
          <span class="bb-health-key">Server Address</span>
          <span class="bb-health-val">${d.server_url || d.url || '—'}</span>
        </div>
        <div class="bb-health-item">
          <span class="bb-health-key">OS Version</span>
          <span class="bb-health-val">${d.macos_version || d.os_version || '—'}</span>
        </div>
        <div class="bb-health-item">
          <span class="bb-health-key">Contacts</span>
          <span class="bb-health-val">${d.contact_count != null ? d.contact_count.toLocaleString() : '—'}</span>
        </div>
        <div class="bb-health-item">
          <span class="bb-health-key">Last Check</span>
          <span class="bb-health-val">${timeAgo(d.checked_at || d.timestamp || new Date().toISOString())}</span>
        </div>
        <div class="bb-health-item">
          <span class="bb-health-key">FindMy Enabled</span>
          <span class="bb-health-val">${d.findmy_enabled ? '✅ Yes' : '—'}</span>
        </div>
        <div class="bb-health-item">
          <span class="bb-health-key">iCloud Account</span>
          <span class="bb-health-val">${d.icloud_account || '—'}</span>
        </div>
      </div>`;
  }

  function renderHealthError(msg) {
    const dot = $('bbConnDot');
    const lbl = $('bbConnLabel');
    setStatus(dot, 'offline', 'Cannot reach BlueBubbles');
    if (lbl) lbl.textContent = 'Offline';
    const kpis = ['bbKpiStatus','bbKpiVersion','bbKpiPrivApi','bbKpiMessages'];
    kpis.forEach(id => { const el = $(id); if (el) el.textContent = '—'; });
    const body = $('bbHealthBody');

    if (body) body.innerHTML = `
      <div class="sl-empty-state">
        <div class="sl-empty-state-icon">🔌</div>
        <div class="sl-empty-state-title">BlueBubbles Unreachable</div>
        <div class="sl-empty-state-desc">${msg}<br><br>Ensure the ngrok tunnel is active and BB_SERVER_URL is set in .env</div>
        <button class="sl-btn sl-btn-secondary" style="margin-top:12px" onclick="SLiMessage.loadHealth()">↻ Retry</button>
      </div>`;
  }

  /* ── Inbox ─────────────────────────────────────────────────────────────── */
  async function loadInbox() {
    try {
      const data = await apiFetch('/api/imessage/inbox');
      _state.inbox = Array.isArray(data) ? data : (data.messages || data.chats || []);
      updateInboxBadge();
      renderInbox();
    } catch (e) {
      // Silent — don't thrash the UI on polling errors
    }
  }

  function updateInboxBadge() {
    const unread = _state.inbox.filter(m => m.unread || m.is_unread).length;
    const badge = $('bbInboxBadge');
    if (badge) {
      badge.textContent = unread > 0 ? unread : '';
      badge.style.display = unread > 0 ? 'flex' : 'none';
    }
    // Also update tab badge
    const tabBadge = $('imessageBadge');
    if (tabBadge) {
      tabBadge.textContent = unread > 0 ? unread : '—';
    }
  }

  function filteredInbox() {
    let msgs = _state.inbox;
    if (_state.filter === 'unread')   msgs = msgs.filter(m => m.unread || m.is_unread);
    // Gap 2 fix: use category (MongoDB field) with fallback to classification
    if (_state.filter === 'intake')   msgs = msgs.filter(m => (m.category || m.classification) === 'intake');
    if (_state.filter === 'checkin')  msgs = msgs.filter(m => (m.category || m.classification) === 'checkin');
    if (_state.searchQ) {
      const q = _state.searchQ.toLowerCase();
      msgs = msgs.filter(m =>
        (m.recipient_phone || m.handle || m.phone || m.address || '').toLowerCase().includes(q) ||
        (m.message || m.text || '').toLowerCase().includes(q) ||
        (m.text || m.last_message || m.preview || '').toLowerCase().includes(q) ||
        (m.contact_name || m.display_name || '').toLowerCase().includes(q)
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
          <div class="sl-empty-state-desc">${_state.filter !== 'all' ? 'Try clearing filters' : 'Inbox is empty'}</div>
        </div>`;
      return;
    }
    body.innerHTML = msgs.map(m => {
      // MongoDB schema (bb_webhook_receiver.py): recipient_phone, message, sent_at, category, bb_message_guid
      const handle   = m.recipient_phone || m.handle || m.phone || m.address || m.chat_identifier || '';
      const name     = m.contact_name || m.display_name || fmtPhone(handle);
      const preview  = clampText(m.message || m.text || m.last_message || m.preview || '', 72);
      const ts       = timeAgo(m.sent_at || m.date || m.timestamp || m.last_message_date);
      const unread   = m.unread || m.is_unread;
      const tag      = m.category || m.classification || m.intent;
      const active   = _state.activeThread === handle ? 'active' : '';

      return `
        <div class="bb-thread-row ${active} ${unread ? 'unread' : ''}" onclick="SLiMessage.openThread('${handle}', '${name.replace(/'/g,"\\'")}')">
          <div class="bb-thread-avatar">${name.charAt(0).toUpperCase()}</div>
          <div class="bb-thread-meta">
            <div class="bb-thread-name">
              ${name}
              ${unread ? '<span class="bb-unread-dot"></span>' : ''}
            </div>
            <div class="bb-thread-preview">${preview || '<em>No preview</em>'}</div>
          </div>
          <div class="bb-thread-right">
            <div class="bb-thread-time">${ts}</div>
            ${tag ? `<span class="sl-badge ${tagClass(tag)}" style="font-size:9px">${tag}</span>` : ''}
          </div>
        </div>`;
    }).join('');
  }

  function openThread(handle, name) {
    _state.activeThread = handle;
    // Highlight selected thread
    $$('.bb-thread-row').forEach(r => r.classList.remove('active'));
    const rows = $$('.bb-thread-row');
    // Re-render highlights
    renderInbox();
    // Load compose target
    const target = $('bbComposeTarget');
    if (target) {
      target.value = handle;
      target.dataset.name = name;
    }
    const toLabel = $('bbComposeTo');
    if (toLabel) toLabel.textContent = `To: ${name} (${fmtPhone(handle)})`;
    const composeArea = $('bbComposeArea');
    if (composeArea) composeArea.style.display = 'flex';
    // Mark read optimistically
    markRead(handle);
  }

  async function markRead(handle) {
    try {
      await apiFetch('/api/imessage/mark-read', {
        method: 'POST',
        body: JSON.stringify({ handle }),
      });
      _state.inbox = _state.inbox.map(m =>
        (m.recipient_phone || m.handle || m.address || m.chat_identifier) === handle
          ? { ...m, unread: false, is_unread: false }
          : m
      );
      updateInboxBadge();
    } catch (e) { /* silent */ }
  }

  /* ── Compose & Send ────────────────────────────────────────────────────── */
  async function sendMessage() {
    if (_state.sending) return;
    const text   = $('bbComposeText')?.value.trim();
    const handle = $('bbComposeTarget')?.value.trim();
    if (!text || !handle) return;

    _state.sending = true;
    const btn = $('bbSendBtn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="sl-spinner"></span>'; }

    try {
      // Gap 3 fix: backend expects 'phone' and 'message' (not 'handle'/'text')
      await apiFetch('/api/imessage/send', {
        method: 'POST',
        body: JSON.stringify({ phone: handle, message: text, method: 'private-api' }),
      });
      // Gap 5: stop typing indicator after successful send
      if (_state.activeThread) {
        apiFetch('/api/imessage/typing', {
          method: 'POST',
          body: JSON.stringify({ chat_guid: 'any;-;' + _state.activeThread, active: false }),
        }).catch(() => {});
      }
      if ($('bbComposeText')) $('bbComposeText').value = '';
      if (btn) btn.innerHTML = '✅';
      setTimeout(() => {
        if (btn) { btn.innerHTML = '⬆ Send'; btn.disabled = false; }
      }, 1500);
      showToast('Message sent', 'success');
      loadInbox(); // refresh
    } catch (err) {
      showToast('Send failed: ' + err.message, 'error');
      if (btn) { btn.innerHTML = '⬆ Send'; btn.disabled = false; }
    } finally {
      _state.sending = false;
    }
  }

  function newCompose() {
    const target = $('bbComposeTarget');
    if (target) { target.value = ''; target.dataset.name = ''; }
    const toLabel = $('bbComposeTo');
    if (toLabel) toLabel.textContent = 'To: New Message';
    const composeArea = $('bbComposeArea');
    if (composeArea) composeArea.style.display = 'flex';
    $('bbComposeTarget')?.focus();
  }

  /* ── FindMy ────────────────────────────────────────────────────────────── */
  async function loadFindMy() {
    const body = $('bbFindMyBody');
    if (!body) return;

    try {
      const data = await apiFetch('/api/imessage/findmy');
      _state.findmy = Array.isArray(data) ? data : (data.devices || data.items || []);
      renderFindMy();
    } catch (err) {
      if (body) body.innerHTML = `
        <div class="sl-empty-state" style="padding:24px">
          <div class="sl-empty-state-icon">📍</div>
          <div class="sl-empty-state-title">FindMy Unavailable</div>
          <div class="sl-empty-state-desc">${err.message}</div>
        </div>`;
    }
  }

  function renderFindMy() {
    const body = $('bbFindMyBody');
    if (!body) return;
    const devices = _state.findmy;
    if (!devices.length) {
      // Gap 7A: proper empty state per Antigravity spec
      body.innerHTML = `
        <div class="sl-empty-state">
          <div class="sl-empty-state-icon">📍</div>
          <div class="sl-empty-state-text">No FindMy devices enrolled</div>
          <div class="sl-empty-state-sub">Enable FindMy location sharing on the defendant's device</div>
        </div>`;
      return;
    }
    body.innerHTML = devices.map(d => {
      const loc     = d.location || {};
      const lat     = loc.latitude  != null ? loc.latitude.toFixed(4)  : '—';
      const lng     = loc.longitude != null ? loc.longitude.toFixed(4) : '—';
      const acc     = loc.accuracy  != null ? `±${Math.round(loc.accuracy)}m` : '';
      const ts      = timeAgo(loc.timestamp || d.timestamp);
      const bat     = d.battery_level != null ? `${Math.round(d.battery_level * 100)}%` : '—';
      const name    = d.name || d.device_name || 'Unknown Device';
      const model   = d.model || d.device_type || '';
      const geofenced = d.geofence_breach ? '🚨 BREACH' : (d.in_geofence ? '✅ In Zone' : '');

      return `
        <div class="bb-device-card ${d.geofence_breach ? 'breach' : ''}">
          <div class="bb-device-header">
            <span class="bb-device-icon">${model.toLowerCase().includes('phone') ? '📱' : model.toLowerCase().includes('mac') ? '💻' : model.toLowerCase().includes('watch') ? '⌚' : '📡'}</span>
            <div>
              <div class="bb-device-name">${name}</div>
              <div class="bb-device-model">${model}</div>
            </div>
            ${geofenced ? `<span class="sl-badge ${d.geofence_breach ? 'sl-badge-red' : 'sl-badge-green'}" style="margin-left:auto">${geofenced}</span>` : ''}
          </div>
          <div class="bb-device-stats">
            <div class="bb-device-stat"><span>📍</span><span>${lat}, ${lng} ${acc}</span></div>
            <div class="bb-device-stat"><span>🔋</span><span>${bat}</span></div>
            <div class="bb-device-stat"><span>🕐</span><span>${ts}</span></div>
          </div>
          ${(lat !== '—' && lng !== '—') ? `
            <a href="https://maps.google.com/?q=${lat},${lng}" target="_blank" class="sl-btn sl-btn-secondary sl-btn-sm" style="margin-top:8px;width:100%;justify-content:center">
              🗺 Open in Maps
            </a>` : ''}
        </div>`;
    }).join('');
  }

  /* ── Automation Toggles ────────────────────────────────────────────────── */
  async function loadAutomationConfig() {
    try {
      const data = await apiFetch('/api/automation/config');
      _state.automation = data.config || data || {};
      renderToggles();
      // Gap 7B: update findmy_geofence toggle description with configured radius
      const geofenceCfg = _state.automation['findmy_geofence'] || {};
      const miles = geofenceCfg.geofence_miles || _state.automation['findmy_geofence.geofence_miles'] || 25;
      const desc = document.querySelector('[data-toggle-desc="findmy_geofence"]');
      if (desc) desc.textContent = `Alert on breach of ${miles}-mile Lee County geofence`;
    } catch (e) { /* silent */ }
  }

  function renderToggles() {
    const cfg   = _state.automation;
    const panel = $('bbAutoToggles');
    if (!panel) return;

    const toggles = [
      { key: 'speed_to_contact',  label: '⚡ Speed-to-Contact',   desc: 'iMessage within 60s of new arrest',   icon: '⚡' },
      { key: 'paperwork_chase',   label: '📄 Paperwork Chase',    desc: 'Reminders every 2h until signed',     icon: '📄' },
      { key: 'intake_recovery',   label: '♻️ Intake Recovery',    desc: 'Follow up on abandoned intakes',      icon: '♻️' },
      { key: 'auto_reply',        label: '🤖 Auto-Reply AI',      desc: 'AI responds to inbound messages',     icon: '🤖' },
      { key: 'findmy_geofence',   label: '🛡 FindMy Geofence',   desc: 'Alert on Lee County boundary breach', icon: '🛡' },
    ];

    panel.innerHTML = toggles.map(t => {
      const on = cfg[t.key] === true || cfg[t.key] === 'enabled';
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

  async function toggle(key) {
    const btn = $(`toggle_${key}`);
    if (!btn) return;
    const wasOn = btn.classList.contains('on');

    // Optimistic UI
    btn.classList.toggle('on', !wasOn);
    btn.classList.toggle('off', wasOn);
    _state.automation[key] = !wasOn;

    try {
      await apiFetch(`/api/automation/toggle/${key}`, { method: 'POST' });
      showToast(`${key.replace(/_/g,' ')} ${!wasOn ? 'enabled' : 'disabled'}`, 'success');
    } catch (err) {
      // Rollback
      btn.classList.toggle('on', wasOn);
      btn.classList.toggle('off', !wasOn);
      _state.automation[key] = wasOn;
      showToast('Toggle failed: ' + err.message, 'error');
    }
  }

  /* ── Auto-Reply Config ─────────────────────────────────────────────────── */
  async function loadAutoReplyConfig() {
    try {
      const data = await apiFetch('/api/imessage/auto-reply/config');
      _state.autoReplyConf = data.config || data || {};
      const el = $('bbAutoReplyKeywords');
      if (el && _state.autoReplyConf.keywords) el.value = _state.autoReplyConf.keywords.join(', ');
      const thresh = $('bbAutoReplyThresh');
      if (thresh && _state.autoReplyConf.confidence_threshold != null) {
        thresh.value = _state.autoReplyConf.confidence_threshold;
      }
    } catch (e) { /* silent */ }
  }

  async function saveAutoReplyConfig() {
    const btn = $('bbSaveAutoReplyBtn');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    try {
      const keywords = ($('bbAutoReplyKeywords')?.value || '').split(',').map(s => s.trim()).filter(Boolean);
      const threshold = parseFloat($('bbAutoReplyThresh')?.value || '0.8');
      await apiFetch('/api/imessage/auto-reply/config', {
        method: 'POST',
        body: JSON.stringify({ keywords, confidence_threshold: threshold }),
      });
      showToast('Auto-reply config saved', 'success');
    } catch (err) {
      showToast('Save failed: ' + err.message, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '💾 Save'; }
    }
  }

  /* ── Public helpers (called from HTML) ─────────────────────────────────── */
  function setFilter(f, el) {
    _state.filter = f;
    $$('.bb-inbox-filter-btn').forEach(b => b.classList.remove('active'));
    if (el) el.classList.add('active');
    _state.inboxPage = 0;
    renderInbox();
  }

  /* ── Toast ─────────────────────────────────────────────────────────────── */
  function showToast(msg, type = 'info') {
    if (window.SLToast?.show) { SLToast.show(msg, type); return; }
    if (window.SL?.toast) { SL.toast(msg, type); return; }
    console.log(`[BB] ${type}: ${msg}`);
  }

  /* ── Debounce ──────────────────────────────────────────────────────────── */
  function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  /* ── Public API ────────────────────────────────────────────────────────── */
  return {
    init,
    destroy,
    loadHealth,
    loadInbox,
    loadFindMy,
    loadAutomationConfig,
    sendMessage,
    newCompose,
    openThread,
    toggle,
    setFilter,
    saveAutoReplyConfig,
    // Expose for inline HTML handlers
    refresh() {
      loadHealth();
      loadInbox();
      loadFindMy();
    },
  };
})();
