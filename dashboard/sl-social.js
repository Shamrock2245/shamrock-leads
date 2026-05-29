/**
 * ShamrockLeads — Social Media Command Center
 * ═══════════════════════════════════════════
 * Frontend module for the Social Engine microservice.
 * Communicates via /api/social/* proxy (dashboard auth).
 *
 * Features:
 *  - OAuth 2.0 "Connect with..." for Google/GBP/YouTube, X, LinkedIn, Meta
 *  - Content queue with approve/reject/edit flow
 *  - Grok-powered content generation (news-aware)
 *  - Gmail Grok post harvesting
 *  - Humanizer scoring + rewriting
 *  - Platform status indicators with connect/disconnect buttons
 *  - Real-time queue stats
 */

const SLSocial = (() => {
  'use strict';

  // ── State ──────────────────────────────────────────────────────────────
  let _posts = [];
  let _stats = {};
  let _platforms = {};
  let _oauthAccounts = [];
  let _filter = 'all';
  let _platformFilter = '';
  let _loaded = false;

  const API = '/api/social';

  // Maps visual platform → OAuth provider name
  const PLATFORM_OAUTH_MAP = {
    twitter:   'twitter',
    linkedin:  'linkedin',
    facebook:  'meta',
    instagram: 'meta',
    gbp:       'google',
    youtube:   'google',
  };

  // Branded button configs
  const CONNECT_BRANDS = {
    google:   { label: 'Sign in with Google',          bg: '#fff',    color: '#3c4043', border: '#dadce0', icon: '🔵' },
    twitter:  { label: 'Connect X Account',            bg: '#000',    color: '#fff',    border: '#000',    icon: '𝕏' },
    linkedin: { label: 'Connect with LinkedIn',        bg: '#0A66C2', color: '#fff',    border: '#0A66C2', icon: '💼' },
    meta:     { label: 'Connect Facebook & Instagram', bg: '#1877F2', color: '#fff',    border: '#1877F2', icon: 'f' },
  };

  // ── Helpers ────────────────────────────────────────────────────────────
  async function _fetch(path, opts = {}) {
    try {
      const url = `${API}${path}`;
      const res = await fetch(url, {
        headers: { 'Content-Type': 'application/json', ...opts.headers },
        ...opts,
      });
      return await res.json();
    } catch (e) {
      console.error('[SLSocial] Fetch error:', path, e);
      return { success: false, error: e.message };
    }
  }

  function _ago(dateStr) {
    if (!dateStr) return '—';
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
  }

  function _statusBadge(status) {
    const map = {
      pending:  { cls: 'soc-badge-pending',  icon: '⏳', label: 'Pending' },
      approved: { cls: 'soc-badge-approved', icon: '✅', label: 'Approved' },
      rejected: { cls: 'soc-badge-rejected', icon: '❌', label: 'Rejected' },
      posted:   { cls: 'soc-badge-posted',   icon: '🚀', label: 'Posted' },
      failed:   { cls: 'soc-badge-failed',   icon: '⚠️', label: 'Failed' },
      expired:  { cls: 'soc-badge-expired',  icon: '🕐', label: 'Expired' },
    };
    const s = map[status] || { cls: '', icon: '❓', label: status };
    return `<span class="soc-badge ${s.cls}">${s.icon} ${s.label}</span>`;
  }

  function _platformIcon(p) {
    const icons = {
      twitter: '𝕏', linkedin: 'in', facebook: 'f',
      instagram: '📷', threads: '🧵', tiktok: '♪',
      telegram: '✈️', gbp: '📍', youtube: '▶️',
      reddit: '🤖', bluesky: '☁️', mastodon: '🐘',
      pinterest: '📌',
    };
    return icons[p] || p;
  }

  function _platformLabel(p) {
    const labels = {
      twitter: 'X / Twitter', linkedin: 'LinkedIn', facebook: 'Facebook',
      instagram: 'Instagram', gbp: 'Google Business', youtube: 'YouTube',
      threads: 'Threads', tiktok: 'TikTok', telegram: 'Telegram',
      reddit: 'Reddit', bluesky: 'Bluesky', mastodon: 'Mastodon',
      pinterest: 'Pinterest',
    };
    return labels[p] || (p || '').charAt(0).toUpperCase() + (p || '').slice(1);
  }

  function _truncate(text, max = 140) {
    if (!text) return '';
    return text.length > max ? text.slice(0, max) + '…' : text;
  }

  function _escHtml(str) {
    const el = document.createElement('span');
    el.textContent = str || '';
    return el.innerHTML;
  }

  // ── OAuth Helpers ─────────────────────────────────────────────────────
  function _getConnectedAccount(platformKey) {
    return _oauthAccounts.find(acct => {
      if (acct.status !== 'active') return false;
      if (acct.platform === platformKey) return true;
      if (acct.sub_platforms && acct.sub_platforms.includes(platformKey)) return true;
      return false;
    });
  }

  function _getAllConnectedAccounts(platformKey) {
    return _oauthAccounts.filter(acct => {
      if (acct.status !== 'active') return false;
      if (acct.platform === platformKey) return true;
      if (acct.sub_platforms && acct.sub_platforms.includes(platformKey)) return true;
      return false;
    });
  }

  // ── Load ───────────────────────────────────────────────────────────────
  async function load() {
    if (!_loaded) {
      _loaded = true;
      _listenForOAuthCallback();
    }
    await Promise.all([loadQueue(), loadStats(), loadPlatforms(), loadOAuthStatus()]);
  }

  async function loadQueue() {
    const params = new URLSearchParams({ limit: '100', skip: '0' });
    if (_filter && _filter !== 'all') params.set('status', _filter);
    if (_platformFilter) params.set('platform', _platformFilter);

    const data = await _fetch(`/queue?${params}`);
    if (data.posts || data.queue) {
      _posts = data.posts || data.queue || [];
    } else if (Array.isArray(data)) {
      _posts = data;
    }
    renderQueue();
  }

  async function loadStats() {
    const data = await _fetch('/queue/stats');
    _stats = data?.stats || data || {};
    renderKPIs();
  }

  async function loadPlatforms() {
    const data = await _fetch('/platforms');
    _platforms = data?.platforms || data || {};
  }

  async function loadOAuthStatus() {
    try {
      const data = await _fetch('/oauth/status');
      _oauthAccounts = data?.accounts || [];
    } catch (e) {
      console.warn('[SLSocial] OAuth status fetch failed:', e);
      _oauthAccounts = [];
    }
    renderPlatformStatus();
  }

  // ── OAuth: Connect / Disconnect ───────────────────────────────────────
  function connectPlatform(provider) {
    const url = `${API}/oauth/${provider}/login`;
    const w = 600, h = 700;
    const left = (screen.width - w) / 2;
    const top = (screen.height - h) / 2;
    window.open(url, `shamrock_oauth_${provider}`,
      `width=${w},height=${h},left=${left},top=${top},toolbar=no,menubar=no,scrollbars=yes`
    );
  }

  async function disconnectPlatform(provider, accountId) {
    if (!confirm(`Disconnect this ${provider} account?`)) return;
    const data = await _fetch(`/oauth/${provider}/disconnect`, {
      method: 'POST',
      body: JSON.stringify({ account_id: accountId }),
    });
    if (data.success !== false) {
      SL.toast(`${provider} disconnected`, 'info');
      await loadOAuthStatus();
    } else {
      SL.toast(data.error || 'Disconnect failed', 'error');
    }
  }

  function _listenForOAuthCallback() {
    window.addEventListener('message', (event) => {
      if (event.data?.type === 'shamrock_oauth_callback') {
        const { success, provider, display_name, error } = event.data;
        if (success) {
          SL.toast(`Connected to ${_platformLabel(provider) || provider}! (${display_name})`, 'success');
        } else {
          SL.toast(`Connection failed: ${error || 'Unknown error'}`, 'error');
        }
        loadOAuthStatus();
      }
    });
  }

  // ── Render: KPI Strip ──────────────────────────────────────────────────
  function renderKPIs() {
    const el = (id, val) => {
      const e = document.getElementById(id);
      if (e) e.textContent = val;
    };
    el('socKpiTotal', _stats.total ?? '—');
    el('socKpiPending', _stats.pending ?? '—');
    el('socKpiApproved', _stats.approved ?? '—');
    el('socKpiPosted', _stats.posted ?? '—');
    el('socKpiGrokHarvested', _stats.grok_harvested ?? '—');
    el('socKpiHumanizerScore', _stats.avg_humanizer_score ? Math.round(_stats.avg_humanizer_score) : '—');
  }

  // ── Render: Platform Status ────────────────────────────────────────────
  function renderPlatformStatus() {
    const container = document.getElementById('socPlatformGrid');
    if (!container) return;

    const platforms = [
      'twitter', 'linkedin', 'facebook', 'instagram',
      'gbp', 'youtube', 'threads', 'telegram',
    ];

    container.innerHTML = platforms.map(p => {
      const oauthProvider = PLATFORM_OAUTH_MAP[p];
      const connectedAccounts = _getAllConnectedAccounts(p);
      const isConnected = connectedAccounts.length > 0;
      const firstAcct = connectedAccounts[0];

      // Profile picture
      let avatarHtml = '';
      if (isConnected && firstAcct?.profile_picture) {
        avatarHtml = `<img src="${firstAcct.profile_picture}" class="soc-platform-avatar" alt="">`;
      }

      // Connection status and account names
      let statusHtml;
      let accountsHtml = '';
      if (isConnected) {
        statusHtml = `<div class="soc-platform-status"><span class="soc-conn-dot green"></span> Connected</div>`;
        accountsHtml = connectedAccounts.map(a =>
          `<div class="soc-acct-row">
            <span class="soc-acct-name">${_escHtml(a.display_name)}</span>
            <button class="soc-disconnect-btn" title="Disconnect" onclick="event.stopPropagation();SLSocial.disconnectPlatform('${a.platform}','${a.account_id}')">✕</button>
          </div>`
        ).join('');
      } else {
        statusHtml = `<div class="soc-platform-status">⚪ Not connected</div>`;
      }

      // Connect button
      let connectBtnHtml = '';
      if (oauthProvider) {
        const brand = CONNECT_BRANDS[oauthProvider];
        if (!isConnected && brand) {
          connectBtnHtml = `
            <button class="soc-oauth-btn"
                    style="background:${brand.bg};color:${brand.color};border-color:${brand.border}"
                    onclick="event.stopPropagation();SLSocial.connectPlatform('${oauthProvider}')">
              ${brand.icon} ${brand.label}
            </button>`;
        } else if (isConnected && (p === 'gbp' || p === 'youtube')) {
          // GBP/YouTube allow adding additional accounts
          connectBtnHtml = `
            <button class="soc-oauth-add-btn"
                    onclick="event.stopPropagation();SLSocial.connectPlatform('${oauthProvider}')"
                    title="Add another account">
              + Add Account
            </button>`;
        }
      }

      return `
        <div class="soc-platform-card ${isConnected ? 'active' : 'disabled'}">
          <div class="soc-platform-icon">${_platformIcon(p)}</div>
          <div class="soc-platform-name">${_platformLabel(p)}</div>
          ${avatarHtml}
          ${statusHtml}
          ${accountsHtml}
          ${connectBtnHtml}
        </div>`;
    }).join('');
  }

  // ── Render: Queue ──────────────────────────────────────────────────────
  function renderQueue() {
    const body = document.getElementById('socQueueBody');
    if (!body) return;

    if (!_posts.length) {
      body.innerHTML = `
        <div class="soc-empty">
          <div class="soc-empty-icon">📝</div>
          <div class="soc-empty-title">No posts in queue</div>
          <div class="soc-empty-sub">Generate content with Grok, scan Gmail, or add a post manually.</div>
        </div>`;
      return;
    }

    body.innerHTML = _posts.map(post => {
      const id = post.post_id || post._id || post.id || '';
      const content = _escHtml(_truncate(post.content, 200));
      const platform = post.platform || 'twitter';
      const status = post.status || 'pending';
      const source = post.source_type || 'manual';
      const humanScore = post.humanizer_score != null
        ? `<span class="soc-h-score" title="AI likelihood (lower is better)">${Math.round(post.humanizer_score)}</span>`
        : '';
      const created = _ago(post.created_at);
      const tone = post.tone || '';

      return `
        <div class="soc-post-card" data-id="${id}" data-status="${status}">
          <div class="soc-post-header">
            <div class="soc-post-meta">
              <span class="soc-platform-pill">${_platformIcon(platform)} ${_platformLabel(platform)}</span>
              ${_statusBadge(status)}
              <span class="soc-source-pill">${source}</span>
              ${humanScore}
              ${tone ? `<span class="soc-tone-pill">${tone}</span>` : ''}
            </div>
            <span class="soc-post-time">${created}</span>
          </div>
          <div class="soc-post-content">${content}</div>
          <div class="soc-post-actions">
            ${status === 'pending' ? `
              <button class="soc-btn soc-btn-approve" onclick="SLSocial.approve('${id}')">✅ Approve</button>
              <button class="soc-btn soc-btn-edit" onclick="SLSocial.openEdit('${id}')">✏️ Edit</button>
              <button class="soc-btn soc-btn-humanize" onclick="SLSocial.humanize('${id}')">🧹 Humanize</button>
              <button class="soc-btn soc-btn-reject" onclick="SLSocial.reject('${id}')">❌</button>
            ` : ''}
            ${status === 'approved' ? `
              <button class="soc-btn soc-btn-publish" onclick="SLSocial.publish('${id}')">🚀 Publish Now</button>
            ` : ''}
          </div>
        </div>`;
    }).join('');
  }

  // ── Actions ────────────────────────────────────────────────────────────
  async function approve(id) {
    const data = await _fetch(`/approve/${id}`, { method: 'POST' });
    if (data.success !== false) {
      SL.toast('Post approved ✅', 'success');
      await loadQueue();
    } else {
      SL.toast(data.error || 'Approve failed', 'error');
    }
  }

  async function reject(id) {
    const data = await _fetch(`/reject/${id}`, {
      method: 'POST',
      body: JSON.stringify({ reason: 'Rejected from dashboard' }),
    });
    if (data.success !== false) {
      SL.toast('Post rejected', 'info');
      await loadQueue();
    }
  }

  async function publish(id) {
    SL.toast('Publishing…', 'info');
    const data = await _fetch(`/publish/${id}`, { method: 'POST' });
    if (data.success !== false) {
      SL.toast('Published! 🚀', 'success');
      await load();
    } else {
      SL.toast(data.error || 'Publish failed', 'error');
    }
  }

  async function humanize(id) {
    const post = _posts.find(p => (p.post_id || p._id || p.id) === id);
    if (!post) return;
    SL.toast('Humanizing…', 'info');
    const data = await _fetch('/humanize', {
      method: 'POST',
      body: JSON.stringify({ text: post.content }),
    });
    if (data.humanized) {
      const before = data.score_before?.score ?? data.score_before ?? 0;
      const after = data.score_after?.score ?? data.score_after ?? 0;
      SL.toast(`Humanized! Score: ${Math.round(before)} → ${Math.round(after)}`, 'success');
      await _fetch(`/edit/${id}`, {
        method: 'POST',
        body: JSON.stringify({ content: data.humanized }),
      });
      await loadQueue();
    } else {
      SL.toast(data.error || 'Humanizer failed', 'error');
    }
  }

  // ── Edit Modal ─────────────────────────────────────────────────────────
  function openEdit(id) {
    const post = _posts.find(p => (p._id || p.id || p.post_id) === id);
    if (!post) return;
    const modal = document.getElementById('socEditModal');
    const textarea = document.getElementById('socEditText');
    const idField = document.getElementById('socEditId');
    if (textarea) textarea.value = post.content || '';
    if (idField) idField.value = id;
    if (modal) modal.style.display = 'flex';
  }

  async function saveEdit() {
    const id = document.getElementById('socEditId')?.value;
    const content = document.getElementById('socEditText')?.value;
    if (!id || !content) return;
    const data = await _fetch(`/edit/${id}`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    });
    if (data.success !== false) {
      SL.toast('Post updated ✏️', 'success');
      closeEdit();
      await loadQueue();
    } else {
      SL.toast(data.error || 'Edit failed', 'error');
    }
  }

  function closeEdit() {
    const modal = document.getElementById('socEditModal');
    if (modal) modal.style.display = 'none';
  }

  // ── Grok Generation ────────────────────────────────────────────────────
  async function grokGenerate() {
    const topic = document.getElementById('socGrokTopic')?.value;
    const platform = document.getElementById('socGrokPlatform')?.value || 'twitter';
    if (!topic) { SL.toast('Enter a topic', 'error'); return; }
    SL.toast('Asking Grok…', 'info');
    const data = await _fetch('/grok/generate', {
      method: 'POST',
      body: JSON.stringify({ topic, platform }),
    });
    if (data.success !== false && data.post) {
      SL.toast('Grok content generated! ✨', 'success');
      await loadQueue();
      await loadStats();
    } else {
      SL.toast(data.error || 'Grok generation failed', 'error');
    }
  }

  async function grokNews() {
    SL.toast('Grok scanning news…', 'info');
    const platform = document.getElementById('socGrokNewsPlatform')?.value || 'twitter';
    const data = await _fetch('/grok/news', {
      method: 'POST',
      body: JSON.stringify({ platform }),
    });
    if (data.success !== false) {
      SL.toast('News hook post created! 📰', 'success');
      await loadQueue();
      await loadStats();
    } else {
      SL.toast(data.error || 'News generation failed', 'error');
    }
  }

  async function grokImage() {
    const prompt = document.getElementById('socGrokImagePrompt')?.value;
    if (!prompt) { SL.toast('Enter an image prompt', 'error'); return; }
    SL.toast('Generating image…', 'info');
    const data = await _fetch('/grok/image', {
      method: 'POST',
      body: JSON.stringify({ prompt }),
    });
    if (data.success !== false) {
      SL.toast('Image generated! 🖼️', 'success');
    } else {
      SL.toast(data.error || 'Image gen failed', 'error');
    }
  }

  // ── Gmail Scanning ─────────────────────────────────────────────────────
  async function gmailScan() {
    SL.toast('Scanning Gmail for Grok posts…', 'info');
    const data = await _fetch('/gmail/scan', { method: 'POST' });
    if (data.success !== false) {
      const count = data.imported || data.count || 0;
      SL.toast(`Gmail scan complete — ${count} post(s) found`, 'success');
      await loadQueue();
      await loadStats();
    } else {
      SL.toast(data.error || 'Gmail scan failed', 'error');
    }
  }

  async function gmailBacklog() {
    SL.toast('Scanning Gmail backlog (this may take a moment)…', 'info');
    const data = await _fetch('/gmail/backlog', { method: 'POST' });
    if (data.success !== false) {
      const count = data.imported || data.count || 0;
      SL.toast(`Backlog scan complete — ${count} post(s) harvested`, 'success');
      await loadQueue();
      await loadStats();
    } else {
      SL.toast(data.error || 'Backlog scan failed', 'error');
    }
  }

  // ── Batch Actions ──────────────────────────────────────────────────────
  async function approveAll() {
    if (!confirm('Approve all pending posts?')) return;
    SL.toast('Approving all pending…', 'info');
    const data = await _fetch('/approve/batch', { method: 'POST' });
    if (data.success !== false) {
      SL.toast(`${data.count || 'All'} posts approved`, 'success');
      await load();
    }
  }

  async function publishBatch() {
    if (!confirm('Publish all approved posts now?')) return;
    SL.toast('Publishing…', 'info');
    const data = await _fetch('/publish/batch', { method: 'POST' });
    if (data.success !== false) {
      SL.toast(`${data.count || 'All'} posts published 🚀`, 'success');
      await load();
    }
  }

  // ── Filters ────────────────────────────────────────────────────────────
  function setFilter(f, el) {
    _filter = f;
    document.querySelectorAll('.soc-filter-btn').forEach(b => b.classList.remove('active'));
    if (el) el.classList.add('active');
    loadQueue();
  }

  function setPlatformFilter(p) {
    _platformFilter = p;
    loadQueue();
  }

  // ── Manual Post ────────────────────────────────────────────────────────
  function openManualModal() {
    document.getElementById('socManualModal').style.display = 'flex';
  }

  function closeManualModal() {
    document.getElementById('socManualModal').style.display = 'none';
  }

  async function submitManual() {
    const content = document.getElementById('socManualContent')?.value;
    const platform = document.getElementById('socManualPlatform')?.value || 'twitter';
    if (!content) { SL.toast('Enter post content', 'error'); return; }
    const data = await _fetch('/manual', {
      method: 'POST',
      body: JSON.stringify({ content, platform }),
    });
    if (data.success !== false) {
      SL.toast('Post queued ✅', 'success');
      closeManualModal();
      document.getElementById('socManualContent').value = '';
      await loadQueue();
      await loadStats();
    } else {
      SL.toast(data.error || 'Failed to queue post', 'error');
    }
  }

  // ── Health Check ───────────────────────────────────────────────────────
  async function checkHealth() {
    const data = await _fetch('/health');
    const el = document.getElementById('socEngineStatus');
    if (el) {
      if (data.status === 'healthy' || data.status === 'ok') {
        el.innerHTML = '<span class="soc-conn-dot green"></span> Engine Online';
      } else {
        el.innerHTML = '<span class="soc-conn-dot red"></span> Engine Offline';
      }
    }
    const postiz = await _fetch('/postiz/health');
    const pel = document.getElementById('socPostizStatus');
    if (pel) {
      if (postiz.connected) {
        pel.innerHTML = `<span class="soc-conn-dot green"></span> Postiz: ${postiz.user || 'Connected'}`;
      } else {
        pel.innerHTML = '<span class="soc-conn-dot amber"></span> Postiz: Not connected';
      }
    }
  }

  // ── Public ─────────────────────────────────────────────────────────────
  return {
    load, loadQueue, loadStats, loadOAuthStatus,
    approve, reject, publish, humanize,
    openEdit, saveEdit, closeEdit,
    grokGenerate, grokNews, grokImage,
    gmailScan, gmailBacklog,
    approveAll, publishBatch,
    setFilter, setPlatformFilter,
    openManualModal, closeManualModal, submitManual,
    checkHealth,
    connectPlatform, disconnectPlatform,
  };
})();
