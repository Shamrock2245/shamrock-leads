/**
 * ShamrockLeads — Social Media Command Center
 * ═══════════════════════════════════════════
 * Frontend module for the Social Engine microservice.
 * Communicates via /api/social/* proxy (dashboard auth).
 * 
 * Features:
 *  - Content queue with approve/reject/edit flow
 *  - Grok-powered content generation (news-aware)
 *  - Gmail Grok post harvesting
 *  - Humanizer scoring + rewriting
 *  - Platform status indicators
 *  - Real-time queue stats
 */

const SLSocial = (() => {
  'use strict';

  // ── State ──────────────────────────────────────────────────────────────
  let _posts = [];
  let _stats = {};
  let _platforms = {};
  let _filter = 'all';
  let _platformFilter = '';
  let _loaded = false;

  const API = '/api/social';

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
      pending:  { cls: 'soc-badge-pending', icon: '⏳', label: 'Pending' },
      approved: { cls: 'soc-badge-approved', icon: '✅', label: 'Approved' },
      rejected: { cls: 'soc-badge-rejected', icon: '❌', label: 'Rejected' },
      posted:   { cls: 'soc-badge-posted', icon: '🚀', label: 'Posted' },
      failed:   { cls: 'soc-badge-failed', icon: '⚠️', label: 'Failed' },
      expired:  { cls: 'soc-badge-expired', icon: '🕐', label: 'Expired' },
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
    return (p || '').charAt(0).toUpperCase() + (p || '').slice(1);
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

  // ── Load ───────────────────────────────────────────────────────────────
  async function load() {
    if (!_loaded) {
      _loaded = true;
    }
    await Promise.all([loadQueue(), loadStats(), loadPlatforms()]);
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
    renderPlatformStatus();
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
      'twitter', 'linkedin', 'facebook', 'instagram', 'threads',
      'tiktok', 'youtube', 'reddit', 'telegram', 'bluesky',
      'mastodon', 'pinterest', 'gbp',
    ];
    container.innerHTML = platforms.map(p => {
      const pData = _platforms[p] || {};
      const enabled = pData.enabled ?? false;
      const lastPost = pData.last_post;
      const picture = pData.picture ? `<img src="${pData.picture}" class="soc-platform-avatar" alt="">` : '';
      return `
        <div class="soc-platform-card ${enabled ? 'active' : 'disabled'}">
          <div class="soc-platform-icon">${_platformIcon(p)}</div>
          <div class="soc-platform-name">${_platformLabel(p)}</div>
          ${picture}
          <div class="soc-platform-status">${enabled ? '🟢 Connected' : '⚪ Not configured'}</div>
          <div class="soc-platform-last">${lastPost ? _ago(lastPost) : '—'}</div>
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
      const humanScore = post.humanizer_score != null ? `<span class="soc-h-score" title="AI likelihood (lower is better)">${Math.round(post.humanizer_score)}</span>` : '';
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
      // Update post content via edit
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
    const post = _posts.find(p => (p._id || p.id) === id);
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
    // Check social engine
    const data = await _fetch('/health');
    const el = document.getElementById('socEngineStatus');
    if (el) {
      if (data.status === 'healthy' || data.status === 'ok') {
        el.innerHTML = '<span class="soc-conn-dot green"></span> Engine Online';
      } else {
        el.innerHTML = '<span class="soc-conn-dot red"></span> Engine Offline';
      }
    }

    // Check Postiz
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
    load, loadQueue, loadStats,
    approve, reject, publish, humanize,
    openEdit, saveEdit, closeEdit,
    grokGenerate, grokNews, grokImage,
    gmailScan, gmailBacklog,
    approveAll, publishBatch,
    setFilter, setPlatformFilter,
    openManualModal, closeManualModal, submitManual,
    checkHealth,
  };
})();
