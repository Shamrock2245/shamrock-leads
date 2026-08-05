/* ═══════════════════════════════════════════════════════════════════
   ShamrockLeads — ALPR / LPR Watch desk
   FL511 plate hits · watchlist · ad-hoc image scan
   ═══════════════════════════════════════════════════════════════════ */
/* global SL */
(function () {
  'use strict';

  const API = window.API || '';

  let _status = null;
  let _hits = [];
  let _watch = [];
  let _poll = null;
  let _inited = false;

  window.SLALPR = {
    init,
    load,
    refreshStatus,
    searchHits,
    loadWatchlist,
    addWatch,
    scanImage,
    stopPoll,
    viewCameraModal,
    refreshFl511Cameras,
  };

  const $ = (id) => document.getElementById(id);
  const toast = (msg, type) => {
    if (window.SL?.toast) SL.toast(msg, type);
    else console.log(msg);
  };
  const fmt = (ts) => {
    if (!ts) return '—';
    try {
      return new Date(ts).toLocaleString();
    } catch {
      return String(ts);
    }
  };
  const esc = (s) =>
    String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');

  function headers(json) {
    const h = {};
    if (json) h['Content-Type'] = 'application/json';
    return h;
  }

  async function init() {
    if (!_inited) {
      _bind();
      _bindFeedClicks();
      _inited = true;
    }
    await load();
    stopPoll();
    _poll = setInterval(() => {
      refreshStatus().catch(() => {});
    }, 45000);
  }

  function stopPoll() {
    if (_poll) {
      clearInterval(_poll);
      _poll = null;
    }
  }

  function _bind() {
    const searchBtn = $('alprSearchBtn');
    if (searchBtn) searchBtn.addEventListener('click', () => searchHits());

    const plateInput = $('alprPlateFilter');
    if (plateInput) {
      plateInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') searchHits();
      });
    }

    const addBtn = $('alprWatchAddBtn');
    if (addBtn) addBtn.addEventListener('click', () => addWatch());

    const scanBtn = $('alprScanBtn');
    const fileInput = $('alprScanFile');
    if (scanBtn && fileInput) {
      scanBtn.addEventListener('click', () => fileInput.click());
      fileInput.addEventListener('change', () => {
        if (fileInput.files?.[0]) scanImage(fileInput.files[0]);
      });
    }

    const refreshBtn = $('alprRefreshBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', () => load());
  }

  async function load() {
    await Promise.all([refreshStatus(), searchHits(), loadWatchlist()]);
  }

  async function refreshStatus() {
    const el = $('alprStatusPanel');
    try {
      const r = await fetch(`${API}/api/alpr/status`, {
        credentials: 'same-origin',
        headers: headers(),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      _status = await r.json();
      _renderStatus();
      const badge = $('alprBadge');
      if (badge) {
        const n = _status.hits_last_24h || 0;
        if (n > 0) {
          badge.style.display = '';
          badge.textContent = String(n);
        } else {
          badge.style.display = 'none';
        }
      }
    } catch (e) {
      if (el) {
        el.innerHTML = `<div class="alpr-error">Status unavailable: ${esc(e.message)}. Deploy dashboard with ALPR router and ensure Mongo is reachable.</div>`;
      }
    }
  }

  function _renderStatus() {
    const el = $('alprStatusPanel');
    if (!el || !_status) return;
    const w = _status.worker || {};
    const deps = _status.deps || {};
    const engOk = w.engine_ready === true || deps.engine_ready === true;
    const engCls = engOk ? 'ok' : 'warn';
    const streams = w.streams || {};
    const connected = streams.cameras_connected ?? '—';
    const enabled = _status.cameras_enabled ?? streams.cameras_enabled ?? '—';

    el.innerHTML = `
      <div class="alpr-kpi-row">
        <div class="alpr-kpi">
          <div class="alpr-kpi-label">Worker engine</div>
          <div class="alpr-kpi-value ${engCls}">${engOk ? 'Ready' : 'Not ready'}</div>
          <div class="alpr-kpi-sub">${esc(w.engine_error || deps.error || 'Fast-ALPR + OpenCV')}</div>
        </div>
        <div class="alpr-kpi">
          <div class="alpr-kpi-label">Cameras</div>
          <div class="alpr-kpi-value">${esc(connected)} <span class="alpr-kpi-muted">/ ${esc(enabled)}</span></div>
          <div class="alpr-kpi-sub">connected / enabled</div>
        </div>
        <div class="alpr-kpi">
          <div class="alpr-kpi-label">Hits (24h)</div>
          <div class="alpr-kpi-value">${esc(_status.hits_last_24h ?? 0)}</div>
          <div class="alpr-kpi-sub">matched watchlist</div>
        </div>
        <div class="alpr-kpi">
          <div class="alpr-kpi-label">Watchlist</div>
          <div class="alpr-kpi-value">${esc(_status.watchlist_count ?? 0)}</div>
          <div class="alpr-kpi-sub">active plates</div>
        </div>
        <div class="alpr-kpi">
          <div class="alpr-kpi-label">Worker cycle</div>
          <div class="alpr-kpi-value">${esc(w.cycle ?? '—')}</div>
          <div class="alpr-kpi-sub">${esc(w.updated_at ? fmt(w.updated_at) : 'no heartbeat yet')}</div>
        </div>
      </div>
      ${w.last_error ? `<div class="alpr-error">Last worker error: ${esc(w.last_error)}</div>` : ''}
      ${_renderStreamTable(streams.streams || [])}
    `;
  }

  function _fl511PublicUrl(id, streamUrl) {
    if (streamUrl && /^https?:\/\/(www\.)?fl511\.com\//i.test(streamUrl)) {
      return streamUrl;
    }
    const bare = String(id || '').replace(/^fl511_/, '');
    return bare ? `https://fl511.com/map/Cctv/${encodeURIComponent(bare)}` : 'https://fl511.com/';
  }

  /** Same-origin staff snapshot proxy — keeps the SPA session; never navigates to /login. */
  function _snapshotUrl(id) {
    return `${API}/api/alpr/cameras/${encodeURIComponent(id)}/snapshot?t=${Date.now()}`;
  }

  function _renderStreamTable(list) {
    if (!list || !list.length) {
      return `
        <div class="alpr-card" style="margin-top:14px">
          <div class="alpr-card-header" style="display:flex;justify-content:space-between;align-items:center;">
            <div class="alpr-card-title">Live FL511 Camera Streams (Statewide)</div>
            <button type="button" class="btn btn-sm btn-primary" data-alpr-action="refresh-feeds" style="background:#0ea5e9;border:none;padding:6px 12px;border-radius:6px;cursor:pointer;color:#fff;font-weight:600;">🔄 Refresh Statewide Feeds</button>
          </div>
          <div class="alpr-muted" style="margin-top:12px">No active stream telemetry yet. Click "Refresh Statewide Feeds" above or start worker with <code>docker compose --profile alpr up -d alpr-worker</code>.</div>
        </div>`;
    }
    const rows = list
      .map((s) => {
        const camId = String(s.id || '');
        const camName = String(s.name || s.id || '');
        const streamUrl = String(s.stream_url || _fl511PublicUrl(camId, ''));
        return `
      <tr>
        <td><strong>${esc(camName)}</strong> <div style="font-size:0.75rem;color:#94a3b8;">ID: ${esc(camId)}</div></td>
        <td><span class="alpr-pill ${s.connected ? 'ok' : 'off'}">${s.connected ? 'live' : 'down'}</span></td>
        <td>${esc(s.stream_type || 'jpeg')}</td>
        <td>${esc(s.frames_ok ?? 0)} / ${esc(s.frames_fail ?? 0)}</td>
        <td>
          <button type="button" class="btn btn-sm alpr-view-feed-btn"
            data-alpr-action="view-feed"
            data-cam-id="${esc(camId)}"
            data-cam-name="${esc(camName)}"
            data-stream-url="${esc(streamUrl)}"
            style="background:#0284c7;color:#fff;border:none;border-radius:4px;padding:4px 10px;font-size:0.8rem;cursor:pointer;font-weight:600;">👁️ View Feed</button>
        </td>
      </tr>`;
      })
      .join('');
    return `
      <div class="alpr-card" style="margin-top:14px">
        <div class="alpr-card-header" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
          <div class="alpr-card-title">Live FL511 Camera Streams (${list.length} active feeds)</div>
          <button type="button" class="btn btn-sm" data-alpr-action="refresh-feeds" style="background:#0ea5e9;color:#fff;border:none;padding:5px 12px;border-radius:6px;cursor:pointer;font-size:0.85rem;font-weight:600;">🔄 Refresh 284 Statewide Feeds</button>
        </div>
        <div class="alpr-table-wrap">
          <table class="alpr-table">
            <thead><tr><th>Camera</th><th>Status</th><th>Type</th><th>OK / Fail</th><th>Live View</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
  }

  let _liveModalTimer = null;
  let _feedClickBound = false;

  function _bindFeedClicks() {
    if (_feedClickBound) return;
    _feedClickBound = true;
    // Event delegation — avoids broken inline onclick when names contain quotes
    document.addEventListener('click', (ev) => {
      const btn = ev.target && ev.target.closest ? ev.target.closest('[data-alpr-action]') : null;
      if (!btn) return;
      const action = btn.getAttribute('data-alpr-action');
      if (action === 'refresh-feeds') {
        ev.preventDefault();
        refreshFl511Cameras();
        return;
      }
      if (action === 'view-feed') {
        ev.preventDefault();
        ev.stopPropagation();
        viewCameraModal(
          btn.getAttribute('data-cam-id') || '',
          btn.getAttribute('data-cam-name') || '',
          btn.getAttribute('data-stream-url') || ''
        );
      }
    });
  }

  function _closeLiveModal(overlay) {
    if (_liveModalTimer) {
      clearInterval(_liveModalTimer);
      _liveModalTimer = null;
    }
    if (!overlay) return;
    overlay.classList.remove('show', 'active');
    overlay.style.display = 'none';
    overlay.setAttribute('aria-hidden', 'true');
  }

  function viewCameraModal(id, name, streamUrl) {
    _bindFeedClicks();
    const camId = String(id || '').trim();
    if (!camId) {
      toast('Missing camera id', 'error');
      return;
    }
    const publicUrl = _fl511PublicUrl(camId, streamUrl);
    // Prefer same-origin proxy so the browser never navigates to a relative
    // /map/Cctv/* path on leads.shamrockbailbonds.biz (that hits PIN login).
    const proxyBase = `${API}/api/alpr/cameras/${encodeURIComponent(camId)}/snapshot`;
    const bust = () => `${proxyBase}?t=${Date.now()}`;

    const modalId = 'alprCamViewModal';
    let overlay = document.getElementById(modalId);
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = modalId;
      // Do NOT use only class "modal-overlay" without .show — global CSS forces
      // display:none !important on .modal-overlay:not(.show):not(.active).
      overlay.className = 'alpr-live-modal-overlay';
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-modal', 'true');
      document.body.appendChild(overlay);
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) _closeLiveModal(overlay);
      });
    }

    overlay.innerHTML = `
      <div class="alpr-live-modal" style="background:#0f172a;border:1px solid #334155;border-radius:12px;width:90%;max-width:750px;padding:20px;box-shadow:0 25px 50px -12px rgba(0,0,0,0.8);color:#f8fafc;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;border-bottom:1px solid #1e293b;padding-bottom:10px;">
          <div>
            <h3 style="margin:0;font-size:1.1rem;color:#38bdf8;">📹 Live Traffic Camera: ${esc(name || camId)}</h3>
            <div style="font-size:0.8rem;color:#94a3b8;margin-top:2px;">Camera ID: <code>${esc(camId)}</code> · Auto-refresh 2.5s · same-origin proxy</div>
          </div>
          <button type="button" id="closeCamModalBtn" style="background:#334155;border:none;color:#fff;border-radius:6px;padding:6px 14px;cursor:pointer;font-weight:600;">✕ Close</button>
        </div>
        <div style="text-align:center;background:#020617;border-radius:8px;padding:12px;min-height:340px;display:flex;align-items:center;justify-content:center;overflow:hidden;border:1px solid #1e293b;position:relative;">
          <img id="alprLiveImg" alt="Live FL511 camera ${esc(camId)}"
            src="${esc(bust())}"
            style="max-width:100%;max-height:480px;border-radius:6px;object-fit:contain;background:#020617;"
            data-fallback="${esc(publicUrl)}" />
          <div id="alprLiveImgStatus" style="display:none;position:absolute;inset:0;align-items:center;justify-content:center;color:#94a3b8;font-size:0.9rem;padding:20px;">Loading frame…</div>
          <div style="position:absolute;bottom:16px;right:16px;background:rgba(15,23,42,0.85);color:#38bdf8;padding:4px 10px;border-radius:20px;font-size:0.75rem;font-weight:600;border:1px solid #0ea5e9;">🔴 LIVE SNAPSHOT</div>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:14px;gap:12px;flex-wrap:wrap;">
          <a href="${esc(publicUrl)}" target="_blank" rel="noopener noreferrer"
            style="color:#0ea5e9;font-size:0.85rem;text-decoration:none;font-weight:500;">🔗 Open on FL511 ↗</a>
          <button type="button" id="refreshCamModalBtn" style="background:#0ea5e9;border:none;color:#fff;border-radius:6px;padding:6px 14px;cursor:pointer;font-size:0.85rem;font-weight:600;">🔄 Instant Refresh</button>
        </div>
      </div>
    `;

    // Explicit show — inline styles + dedicated class (not global modal-overlay hide rule)
    overlay.classList.add('show');
    overlay.style.cssText =
      'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:10050;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(6px);padding:16px;';
    overlay.setAttribute('aria-hidden', 'false');

    const img = document.getElementById('alprLiveImg');
    if (img) {
      img.onerror = () => {
        // Fallback: load FL511 directly (public traveler image, CORS open)
        const fb = img.getAttribute('data-fallback');
        if (fb && !img.dataset.usedFallback) {
          img.dataset.usedFallback = '1';
          img.src = `${fb}${fb.includes('?') ? '&' : '?'}t=${Date.now()}`;
          return;
        }
        img.alt = 'Frame unavailable';
        toast('Could not load live frame for this camera', 'error');
      };
    }

    if (_liveModalTimer) clearInterval(_liveModalTimer);
    _liveModalTimer = setInterval(() => {
      const el = document.getElementById('alprLiveImg');
      if (!el || overlay.getAttribute('aria-hidden') === 'true') return;
      // Prefer proxy; if already on fallback, keep refreshing fallback
      if (el.dataset.usedFallback === '1') {
        const fb = el.getAttribute('data-fallback') || publicUrl;
        el.src = `${fb}${fb.includes('?') ? '&' : '?'}t=${Date.now()}`;
      } else {
        el.src = bust();
      }
    }, 2500);

    const closeBtn = document.getElementById('closeCamModalBtn');
    if (closeBtn) {
      closeBtn.onclick = (e) => {
        e.preventDefault();
        _closeLiveModal(overlay);
      };
    }
    const refreshBtn = document.getElementById('refreshCamModalBtn');
    if (refreshBtn) {
      refreshBtn.onclick = (e) => {
        e.preventDefault();
        const el = document.getElementById('alprLiveImg');
        if (!el) return;
        if (el.dataset.usedFallback === '1') {
          const fb = el.getAttribute('data-fallback') || publicUrl;
          el.src = `${fb}${fb.includes('?') ? '&' : '?'}t=${Date.now()}`;
        } else {
          el.src = bust();
        }
      };
    }
  }

  async function refreshFl511Cameras() {
    toast('Resolving live FL511 traffic cameras across Florida…', 'info');
    try {
      const r = await fetch(`${API}/api/alpr/refresh-cameras`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: headers(true),
        body: JSON.stringify({}),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      toast(`✅ Resolved ${data.resolved_count} live FL511 cameras statewide!`, 'ok');
      await refreshStatus();
    } catch (e) {
      toast(`Failed to refresh FL511 cameras: ${e.message}`, 'error');
    }
  }

  async function searchHits() {
    const plate = ($('alprPlateFilter')?.value || '').trim();
    const defendantId = ($('alprDefFilter')?.value || '').trim();
    const matchedOnly = $('alprMatchedOnly')?.checked !== false;
    const since = ($('alprSince')?.value || '').trim();

    const params = new URLSearchParams();
    params.set('limit', '100');
    params.set('matched_only', matchedOnly ? 'true' : 'false');
    if (plate) params.set('plate', plate);
    if (defendantId) params.set('defendant_id', defendantId);
    if (since) {
      // date input → ISO start of day
      params.set('since', new Date(since + 'T00:00:00').toISOString());
    }

    const el = $('alprHitsBody');
    if (el) el.innerHTML = `<tr><td colspan="7" class="alpr-muted">Loading…</td></tr>`;

    try {
      const r = await fetch(`${API}/api/alpr/hits?${params}`, {
        credentials: 'same-origin',
        headers: headers(),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      _hits = data.hits || [];
      _renderHits();
    } catch (e) {
      if (el) {
        el.innerHTML = `<tr><td colspan="7" class="alpr-error">Failed to load hits: ${esc(e.message)}</td></tr>`;
      }
    }
  }

  function _renderHits() {
    const el = $('alprHitsBody');
    if (!el) return;
    if (!_hits.length) {
      el.innerHTML = `<tr><td colspan="7" class="alpr-muted">No hits for this filter.</td></tr>`;
      return;
    }
    el.innerHTML = _hits
      .map((h) => {
        const conf =
          h.confidence != null ? `${Math.round(Number(h.confidence) * 100)}%` : '—';
        return `<tr>
          <td><code class="alpr-plate">${esc(h.plate_text)}</code> <span class="alpr-muted">${esc(h.state || 'FL')}</span></td>
          <td>${esc(h.defendant_name || '—')}</td>
          <td class="alpr-muted">${esc(h.case_number || '—')}</td>
          <td>${esc(h.camera_name || h.camera_id || '—')}</td>
          <td>${esc(conf)}</td>
          <td>${h.matched ? '<span class="alpr-pill ok">match</span>' : '<span class="alpr-pill off">scan</span>'}</td>
          <td class="alpr-muted">${esc(fmt(h.timestamp))}</td>
        </tr>`;
      })
      .join('');
  }

  async function loadWatchlist() {
    const el = $('alprWatchBody');
    if (el) el.innerHTML = `<tr><td colspan="5" class="alpr-muted">Loading…</td></tr>`;
    try {
      const r = await fetch(`${API}/api/alpr/watchlist?active_only=true`, {
        credentials: 'same-origin',
        headers: headers(),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      _watch = data.watchlist || [];
      _renderWatch();
    } catch (e) {
      if (el) {
        el.innerHTML = `<tr><td colspan="5" class="alpr-error">${esc(e.message)}</td></tr>`;
      }
    }
  }

  function _renderWatch() {
    const el = $('alprWatchBody');
    if (!el) return;
    if (!_watch.length) {
      el.innerHTML = `<tr><td colspan="5" class="alpr-muted">No plates on watchlist yet.</td></tr>`;
      return;
    }
    el.innerHTML = _watch
      .map(
        (w) => `<tr>
        <td><code class="alpr-plate">${esc(w.plate_text)}</code></td>
        <td>${esc(w.defendant_name || '—')}</td>
        <td class="alpr-muted">${esc(w.defendant_id || '—')}</td>
        <td class="alpr-muted">${esc(w.case_number || '—')}</td>
        <td class="alpr-muted">${esc(fmt(w.updated_at || w.created_at))}</td>
      </tr>`
      )
      .join('');
  }

  async function addWatch() {
    const plate = ($('alprWatchPlate')?.value || '').trim();
    const defendantId = ($('alprWatchDefId')?.value || '').trim();
    const defendantName = ($('alprWatchDefName')?.value || '').trim();
    const caseNumber = ($('alprWatchCase')?.value || '').trim();
    const notes = ($('alprWatchNotes')?.value || '').trim();

    if (!plate || !defendantId) {
      toast('Plate and defendant ID are required', 'error');
      return;
    }

    try {
      const r = await fetch(`${API}/api/alpr/watchlist`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: headers(true),
        body: JSON.stringify({
          plate_text: plate,
          defendant_id: defendantId,
          defendant_name: defendantName,
          case_number: caseNumber,
          notes,
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      toast(`Watching plate ${plate.toUpperCase()}`, 'success');
      ['alprWatchPlate', 'alprWatchDefId', 'alprWatchDefName', 'alprWatchCase', 'alprWatchNotes'].forEach(
        (id) => {
          const n = $(id);
          if (n) n.value = '';
        }
      );
      await loadWatchlist();
      await refreshStatus();
    } catch (e) {
      toast(e.message || 'Failed to add watchlist entry', 'error');
    }
  }

  async function scanImage(file) {
    const out = $('alprScanResults');
    if (out) out.innerHTML = `<div class="alpr-muted">Scanning ${esc(file.name)}…</div>`;
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch(`${API}/api/alpr/scan-image`, {
        method: 'POST',
        credentials: 'same-origin',
        body: fd,
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      const dets = data.detections || [];
      if (!dets.length) {
        if (out) out.innerHTML = `<div class="alpr-muted">No plates detected (or model not ready on this host).</div>`;
        toast('No plates found', 'info');
        return;
      }
      if (out) {
        out.innerHTML = `
          <div class="alpr-card-title" style="margin-bottom:8px">${dets.length} plate(s) in ${esc(file.name)}</div>
          <ul class="alpr-scan-list">
            ${dets
              .map(
                (d) =>
                  `<li><code class="alpr-plate">${esc(d.plate_text)}</code>
                   <span class="alpr-muted">${Math.round((d.confidence || 0) * 100)}% · ${esc(d.state || 'FL')}</span>
                   <button type="button" class="alpr-link-btn" data-plate="${esc(d.plate_text)}">Add to watchlist</button>
                   </li>`
              )
              .join('')}
          </ul>`;
        out.querySelectorAll('[data-plate]').forEach((btn) => {
          btn.addEventListener('click', () => {
            const p = btn.getAttribute('data-plate');
            const inp = $('alprWatchPlate');
            if (inp) inp.value = p;
            toast('Plate filled — set defendant ID and save', 'info');
            $('alprWatchPlate')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
          });
        });
      }
      toast(`Found ${dets.length} plate(s)`, 'success');
    } catch (e) {
      if (out) out.innerHTML = `<div class="alpr-error">${esc(e.message)}</div>`;
      toast(e.message || 'Scan failed', 'error');
    } finally {
      const fi = $('alprScanFile');
      if (fi) fi.value = '';
    }
  }
})();
