/* ═══════════════════════════════════════════════════════════════════════════
   ShamrockLeads — Pipeline UI Upgrade  v2.0
   Replaces the generic "AI Agent" button with specific AI feature buttons.
   Upgrades Kanban cards with drag-drop, better actions, and visual hierarchy.
   Patches into the existing SLProspective module without breaking it.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const API = window.API || '';
  const $ = id => document.getElementById(id);
  const toast = (m, t) => { if (window.SL?.toast) SL.toast(m, t); };
  const money = n => '$' + (parseFloat(n) || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  const timeAgo = ts => {
    if (!ts) return '';
    const d = new Date(ts), now = new Date(), diff = Math.floor((now - d) / 1000);
    if (diff < 60) return diff + 's ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  };

  /* ── 1. Upgrade the action bar ─────────────────────────────────────────── */
  function upgradeActionBar() {
    var bar = document.querySelector('.outreach-action-bar');
    if (!bar || bar.dataset.upgraded) return;
    bar.dataset.upgraded = '1';
    // HTML now uses design-system classes directly — no runtime patching needed
  }

  /* ── 2. Inject AI Feature Bar ──────────────────────────────────────────── */
  function injectAIFeatureBar() {
    if ($('slAIFeatureBar')) return;

    var wrap = $('autoReplyPanelWrap');
    if (!wrap) return;

    var bar = document.createElement('div');
    bar.id = 'slAIFeatureBar';
    bar.className = 'sl-ai-feature-bar';
    bar.innerHTML = `
      <span class="sl-ai-feature-bar-label">✦ AI Features</span>

      <button class="sl-ai-feature-btn" id="aiFeatOpener" onclick="SLPipelineUI.runAIOpener()" title="Generate an opening message tailored to this lead">
        <span class="ai-dot"></span> AI Opener
      </button>

      <button class="sl-ai-feature-btn" id="aiFeatFollowUp" onclick="SLPipelineUI.runAIFollowUp()" title="Generate a context-aware follow-up based on conversation history">
        <span class="ai-dot"></span> AI Follow-Up
      </button>

      <button class="sl-ai-feature-btn" id="aiFeatObjection" onclick="SLPipelineUI.runAIObjectionHandler()" title="Get AI-suggested responses to common objections">
        <span class="ai-dot"></span> Objection Handler
      </button>

      <button class="sl-ai-feature-btn" id="aiFeatSummary" onclick="SLPipelineUI.runAISummary()" title="Summarize this lead's full history and recommend next action">
        <span class="ai-dot"></span> Lead Summary
      </button>

      <button class="sl-ai-feature-btn" id="aiFeatScore" onclick="SLPipelineUI.runAIRescoreAll()" title="Re-score all leads with AI based on latest activity">
        <span class="ai-dot"></span> Re-Score All
      </button>

      <button class="sl-ai-feature-btn" id="aiFeatDraft" onclick="SLPipelineUI.runAIDraftSequence()" title="Draft a full outreach sequence for selected leads">
        <span class="ai-dot"></span> Draft Sequence
      </button>

      <span style="flex:1"></span>

      <button class="sl-ai-feature-btn" id="aiFeatAutoReply" onclick="SLPipelineUI.toggleAutoReplyPanel()" title="Configure the AI auto-reply agent" style="border-color:rgba(124,58,237,.4)">
        🤖 Auto-Reply Agent <span id="aiFeatAutoReplyStatus" class="sl-badge sl-badge-gray" style="margin-left:4px">OFF</span>
      </button>
    `;

    // Insert before the KPI row
    var kpiRow = document.querySelector('.outreach-kpi-row');
    if (kpiRow) {
      kpiRow.parentNode.insertBefore(bar, kpiRow);
    } else {
      wrap.parentNode.insertBefore(bar, wrap.nextSibling);
    }
  }

  /* ── 3. Upgrade the AI panel rendering ────────────────────────────────── */
  function upgradeAIPanel() {
    var wrap = $('autoReplyPanelWrap');
    if (!wrap || wrap.dataset.upgraded) return;
    wrap.dataset.upgraded = '1';
    // Panel stays hidden by default — only shown via AI Tools toggle
    var panel = $('autoReplyPanel');
    if (panel) {
      panel.style.margin = '0';
    }
  }

  /* ── 4. Upgrade KPI cards ──────────────────────────────────────────────── */
  function upgradeKPICards() {
    var row = document.querySelector('.outreach-kpi-row');
    if (!row || row.dataset.upgraded) return;
    row.dataset.upgraded = '1';
    row.querySelectorAll('.outreach-kpi-card').forEach(function (card) {
      // Make KPI cards clickable to filter by stage
      var label = card.querySelector('.okpi-label');
      if (!label) return;
      var text = label.textContent.toLowerCase();
      var stage = text.includes('contacted') ? 'contacted'
        : text.includes('negotiating') ? 'negotiating'
        : text.includes('paperwork') ? 'paperwork'
        : text.includes('ready') ? 'ready'
        : null;
      if (stage) {
        card.style.cursor = 'pointer';
        card.title = 'Filter by ' + stage;
        card.addEventListener('click', function () {
          if (window.SLProspective) {
            var btn = document.querySelector('#prospStageFilter button[onclick*="' + stage + '"]');
            if (btn) btn.click();
          }
        });
      }
    });
  }

  /* ── 5. Upgrade pipeline column headers ───────────────────────────────── */
  var STAGE_COLORS = {
    contacted:  '#3b82f6',
    negotiating: '#f59e0b',
    paperwork:  '#8b5cf6',
    ready:      '#10b981'
  };

  function upgradeColumnHeaders() {
    document.querySelectorAll('.pipeline-col').forEach(function (col) {
      if (col.dataset.upgraded) return;
      col.dataset.upgraded = '1';
      var stage = col.dataset.stage;
      var color = STAGE_COLORS[stage] || 'var(--accent)';
      var header = col.querySelector('.pipeline-col-header');
      if (header) {
        header.style.borderBottom = '2px solid ' + color;
        header.style.borderRadius = '10px 10px 0 0';
      }
      // Add value display to header
      var valueEl = document.createElement('span');
      valueEl.className = 'sl-pcol-value';
      valueEl.id = 'colValue_' + stage;
      valueEl.style.cssText = 'font-size:10px;color:var(--muted);font-weight:600;margin-left:auto;margin-right:4px';
      if (header) header.appendChild(valueEl);
    });
  }

  /* ── 6. Upgrade pipeline cards ─────────────────────────────────────────── */
  function upgradeCards() {
    document.querySelectorAll('.pipeline-card:not([data-ui-upgraded])').forEach(function (card) {
      card.dataset.uiUpgraded = '1';
      var bk = card.dataset.bk;
      if (!bk) return;

      // Add drag handle
      if (!card.querySelector('.sl-pcard-drag-handle')) {
        var handle = document.createElement('span');
        handle.className = 'sl-pcard-drag-handle';
        handle.innerHTML = '⠿';
        handle.setAttribute('draggable', 'false');
        card.style.paddingLeft = '18px';
        card.insertBefore(handle, card.firstChild);
      }

      // Upgrade quick action buttons
      var actionsDiv = card.querySelector('.card-quick-actions');
      if (actionsDiv && !actionsDiv.dataset.upgraded) {
        actionsDiv.dataset.upgraded = '1';
        actionsDiv.querySelectorAll('.cqa-btn').forEach(function (btn) {
          btn.className = btn.className.replace('cqa-btn', 'sl-pcard-btn');
          if (btn.className.includes('cqa-msg') || btn.title === 'Send iMessage') btn.classList.add('msg');
          if (btn.className.includes('cqa-intel') || btn.title === 'AI Intelligence') btn.classList.add('ai');
          if (btn.className.includes('cqa-advance')) btn.classList.add('advance');
          if (btn.className.includes('cqa-officialize')) btn.classList.add('official');
        });

        // Add AI Opener button to card actions
        if (!actionsDiv.querySelector('.ai-opener-btn')) {
          var aiOpenerBtn = document.createElement('button');
          aiOpenerBtn.className = 'sl-pcard-btn ai ai-opener-btn';
          aiOpenerBtn.title = 'Generate AI opening message';
          aiOpenerBtn.innerHTML = '✦ Opener';
          aiOpenerBtn.setAttribute('onclick', 'event.stopPropagation();SLPipelineUI.runAIOpenerForCard("' + bk + '")');
          actionsDiv.appendChild(aiOpenerBtn);
        }
      }
    });
  }

  /* ── 7. Update column value totals ─────────────────────────────────────── */
  function updateColumnValues() {
    if (!window.SLProspective || !window.SLProspective._data) return;
    var data = window.SLProspective._data || [];
    ['contacted', 'negotiating', 'paperwork', 'ready'].forEach(function (stage) {
      var cards = data.filter(function (b) { return b.stage === stage; });
      var total = cards.reduce(function (sum, b) { return sum + (parseFloat(b.bond_amount) || 0); }, 0);
      var el = $('colValue_' + stage);
      if (el && total > 0) el.textContent = money(total);
    });
  }

  /* ── 8. Drag-and-drop between columns ──────────────────────────────────── */
  var _dragBk = null;
  var _dragStage = null;

  function initDragDrop() {
    // Make cards draggable
    document.addEventListener('dragstart', function (e) {
      var card = e.target.closest('.pipeline-card');
      if (!card) return;
      _dragBk = card.dataset.bk;
      _dragStage = card.dataset.stage;
      card.style.opacity = '.5';
      e.dataTransfer.effectAllowed = 'move';
    });

    document.addEventListener('dragend', function (e) {
      var card = e.target.closest('.pipeline-card');
      if (card) card.style.opacity = '';
      document.querySelectorAll('.pipeline-col').forEach(function (col) {
        col.classList.remove('drag-over');
      });
    });

    document.addEventListener('dragover', function (e) {
      var col = e.target.closest('.pipeline-col');
      if (!col) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      document.querySelectorAll('.pipeline-col').forEach(function (c) { c.classList.remove('drag-over'); });
      col.classList.add('drag-over');
    });

    document.addEventListener('drop', function (e) {
      var col = e.target.closest('.pipeline-col');
      if (!col || !_dragBk) return;
      e.preventDefault();
      col.classList.remove('drag-over');
      var newStage = col.dataset.stage;
      if (newStage && newStage !== _dragStage) {
        dropCard(_dragBk, newStage);
      }
      _dragBk = null;
      _dragStage = null;
    });

    // Make pipeline cards draggable attribute
    document.addEventListener('mouseenter', function (e) {
      if (!e.target || typeof e.target.closest !== 'function') return;
      var card = e.target.closest('.pipeline-card');
      if (card && !card.draggable) card.draggable = true;
    }, true);
  }

  async function dropCard(bk, newStage) {
    try {
      toast('Moving to ' + newStage + '…', 'info');
      var r = await fetch(API + '/api/prospective-bonds/' + bk + '/stage', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stage: newStage, agent: 'Dashboard' })
      });
      var d = await r.json();
      if (d.success || d.ok) {
        toast('Moved to ' + newStage, 'success');
        if (window.SLProspective) SLProspective.load();
      } else {
        toast('Move failed: ' + (d.error || 'unknown'), 'error');
      }
    } catch (err) {
      toast('Move failed: ' + err.message, 'error');
    }
  }

  /* ── 9. AI Feature Functions ────────────────────────────────────────────── */

  // Get the currently focused lead (from detail panel or first selected)
  function _getFocusedBk() {
    var selected = Array.from(document.querySelectorAll('.pipeline-card.card-selected'));
    if (selected.length) return selected[0].dataset.bk;
    // Check if detail panel is open
    var panel = $('prospDetailPanel');
    if (panel && panel.classList.contains('open')) {
      var title = $('prospDetailTitle');
      if (title && title.dataset.bk) return title.dataset.bk;
    }
    return null;
  }

  async function runAIOpener(bk) {
    bk = bk || _getFocusedBk();
    if (!bk) { toast('Select a lead first', 'warning'); return; }
    toast('Generating AI opening message…', 'info');
    try {
      var r = await fetch(API + '/api/agent-brain/opener', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_number: bk, agent: 'Brendan' })
      });
      var d = await r.json();
      if (d.message) {
        showAIResultModal('✦ AI Opener', d.message, bk, 'opener');
      } else {
        // Fallback: use suggest endpoint
        runAISuggestFallback(bk, 'opener');
      }
    } catch (e) {
      runAISuggestFallback(bk, 'opener');
    }
  }

  async function runAIFollowUp(bk) {
    bk = bk || _getFocusedBk();
    if (!bk) { toast('Select a lead first', 'warning'); return; }
    toast('Generating AI follow-up…', 'info');
    try {
      var r = await fetch(API + '/api/agent-brain/suggest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_number: bk, type: 'followup', agent: 'Brendan' })
      });
      var d = await r.json();
      var suggestions = d.suggestions || d.messages || [];
      if (suggestions.length) {
        showAIResultModal('✦ AI Follow-Up', suggestions, bk, 'followup');
      } else {
        toast('No follow-up suggestions generated', 'warning');
      }
    } catch (e) {
      toast('Follow-up generation failed: ' + e.message, 'error');
    }
  }

  async function runAIObjectionHandler(bk) {
    bk = bk || _getFocusedBk();
    if (!bk) { toast('Select a lead first', 'warning'); return; }
    toast('Loading objection responses…', 'info');
    try {
      var r = await fetch(API + '/api/agent-brain/suggest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_number: bk, type: 'objection', agent: 'Brendan' })
      });
      var d = await r.json();
      var suggestions = d.suggestions || d.messages || [];
      if (suggestions.length) {
        showAIResultModal('✦ Objection Handler', suggestions, bk, 'objection');
      } else {
        // Show common objection templates
        showAIResultModal('✦ Objection Handler', [
          "I understand your concern about the premium. Our 10% rate is the state-regulated minimum — we can discuss payment plans that work for your family.",
          "Many families feel overwhelmed at first. Let me walk you through exactly what happens step by step so there are no surprises.",
          "The court date is set, but we can get your loved one home tonight. The sooner we start the paperwork, the sooner they're released."
        ], bk, 'objection');
      }
    } catch (e) {
      toast('Objection handler failed: ' + e.message, 'error');
    }
  }

  async function runAISummary(bk) {
    bk = bk || _getFocusedBk();
    if (!bk) { toast('Select a lead first', 'warning'); return; }
    toast('Generating lead summary…', 'info');
    try {
      var r = await fetch(API + '/api/agent-brain/summary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_number: bk })
      });
      var d = await r.json();
      if (d.summary) {
        showAIResultModal('✦ Lead Summary', d.summary, bk, 'summary');
      } else {
        toast('Summary not available', 'warning');
      }
    } catch (e) {
      toast('Summary failed: ' + e.message, 'error');
    }
  }

  async function runAIRescoreAll() {
    if (!confirm('Re-score all active leads with AI? This may take a moment.')) return;
    toast('Re-scoring all leads…', 'info');
    try {
      var r = await fetch(API + '/api/agent-brain/rescore-all', { method: 'POST' });
      var d = await r.json();
      toast('Re-scored ' + (d.updated || 0) + ' leads', 'success');
      if (window.SLProspective) SLProspective.load();
    } catch (e) {
      toast('Re-score failed: ' + e.message, 'error');
    }
  }

  async function runAIDraftSequence() {
    var selected = Array.from(document.querySelectorAll('.pipeline-card.card-selected'));
    if (!selected.length) { toast('Select leads first to draft a sequence', 'warning'); return; }
    var bks = selected.map(function (c) { return c.dataset.bk; });
    toast('Drafting sequence for ' + bks.length + ' leads…', 'info');
    try {
      var r = await fetch(API + '/api/agent-brain/draft-sequence', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_numbers: bks, agent: 'Brendan' })
      });
      var d = await r.json();
      if (d.sequence) {
        showAIResultModal('✦ Draft Sequence', d.sequence, bks[0], 'sequence');
      } else {
        toast('Sequence draft not available', 'warning');
      }
    } catch (e) {
      toast('Draft failed: ' + e.message, 'error');
    }
  }

  async function runAIOpenerForCard(bk) {
    await runAIOpener(bk);
  }

  async function runAISuggestFallback(bk, type) {
    try {
      var r = await fetch(API + '/api/agent-brain/suggest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ booking_number: bk, type: type, agent: 'Brendan' })
      });
      var d = await r.json();
      var suggestions = d.suggestions || d.messages || [];
      if (suggestions.length) {
        showAIResultModal('✦ AI ' + type.charAt(0).toUpperCase() + type.slice(1), suggestions, bk, type);
      } else {
        toast('No AI suggestions available', 'warning');
      }
    } catch (e) {
      toast('AI request failed: ' + e.message, 'error');
    }
  }

  /* ── 10. AI Result Modal ────────────────────────────────────────────────── */
  function showAIResultModal(title, content, bk, type) {
    var existing = $('slAIResultModal');
    if (existing) existing.remove();

    var isArray = Array.isArray(content);
    var bodyHtml = '';

    if (isArray) {
      bodyHtml = content.map(function (s, i) {
        return '<div class="sl-ai-suggestion" data-text="' + escAttr(s) + '">' +
          '<div class="sl-ai-sug-text">' + escHtml(s) + '</div>' +
          '<div class="sl-ai-sug-actions">' +
          '<button class="sl-pcard-btn msg" onclick="SLPipelineUI.useAISuggestion(\'' + bk + '\',this.closest(\'.sl-ai-suggestion\').dataset.text)">Use →</button>' +
          '<button class="sl-pcard-btn" onclick="navigator.clipboard.writeText(this.closest(\'.sl-ai-suggestion\').dataset.text).then(()=>SL.toast(\'Copied\',\'success\'))">Copy</button>' +
          '</div></div>';
      }).join('');
    } else {
      bodyHtml = '<div class="sl-ai-summary-text">' + escHtml(String(content)) + '</div>' +
        '<div style="display:flex;gap:8px;margin-top:12px">' +
        '<button class="sl-btn sl-btn-secondary" onclick="navigator.clipboard.writeText(' + JSON.stringify(String(content)) + ').then(()=>SL.toast(\'Copied\',\'success\'))">Copy</button>' +
        '</div>';
    }

    var modal = document.createElement('div');
    modal.id = 'slAIResultModal';
    modal.className = 'modal-overlay show';
    modal.style.cssText = 'z-index:9999';
    modal.innerHTML = `
      <div class="modal" style="max-width:520px;border:1px solid rgba(124,58,237,.3)">
        <div class="modal-header" style="background:linear-gradient(135deg,rgba(124,58,237,.08) 0%,var(--surface) 100%)">
          <h2 style="color:#c4b5fd">${escHtml(title)}</h2>
          <button class="modal-close" onclick="document.getElementById('slAIResultModal').remove()">✕</button>
        </div>
        <div class="modal-body" style="max-height:420px;overflow-y:auto">
          <div id="slAIResultBody">${bodyHtml}</div>
        </div>
        <div class="modal-footer">
          <button class="sl-btn sl-btn-ghost" onclick="document.getElementById('slAIResultModal').remove()">Close</button>
          ${bk ? '<button class="sl-btn sl-btn-secondary" onclick="SLProspective&&SLProspective.openDetail(\'' + bk + '\');document.getElementById(\'slAIResultModal\').remove()">Open Lead →</button>' : ''}
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    // Close on overlay click
    modal.addEventListener('click', function (e) {
      if (e.target === modal) modal.remove();
    });
  }

  function useAISuggestion(bk, text) {
    $('slAIResultModal') && $('slAIResultModal').remove();
    if (window.SLProspective) {
      SLProspective.openDetail(bk);
      setTimeout(function () {
        var msgInput = document.querySelector('#prospDetailBody .comm-input, #prospDetailBody textarea');
        if (msgInput) {
          msgInput.value = text;
          msgInput.focus();
        }
      }, 400);
    }
  }

  function toggleAutoReplyPanel() {
    var wrap = $('autoReplyPanelWrap');
    if (!wrap) return;
    var isHidden = wrap.style.display === 'none' || !wrap.style.display;
    wrap.style.display = isHidden ? 'block' : 'none';
    if (isHidden && window.SLProspective) SLProspective.toggleAIPanel();
  }

  /* ── 11. Update auto-reply status badge ────────────────────────────────── */
  function updateAutoReplyBadge() {
    var badge = $('aiFeatAutoReplyStatus');
    if (!badge) return;
    // Check if config is live
    fetch(API + '/api/imessage/auto-reply/config')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var cfg = d.config || d;
        if (cfg.enabled) {
          badge.className = 'sl-badge sl-badge-green';
          badge.textContent = 'LIVE';
          var btn = $('aiFeatAutoReply');
          if (btn) {
            var dot = btn.querySelector('.ai-dot');
            if (!dot) {
              dot = document.createElement('span');
              dot.className = 'ai-dot ai-live';
              btn.insertBefore(dot, btn.firstChild);
            }
            dot.classList.add('ai-live');
          }
        } else {
          badge.className = 'sl-badge sl-badge-gray';
          badge.textContent = 'OFF';
        }
      })
      .catch(function () {});
  }

  /* ── 12. Upgrade filter bar ─────────────────────────────────────────────── */
  function upgradeFilterBar() {
    var filterBar = document.querySelector('.outreach-filter-bar');
    if (!filterBar || filterBar.dataset.upgraded) return;
    filterBar.dataset.upgraded = '1';

    // Style the stage filter buttons as segmented control
    var stageFilter = $('prospStageFilter');
    if (stageFilter) stageFilter.className = 'sl-segmented';

    // Style search input
    var searchInput = $('prospSearch');
    if (searchInput) searchInput.className = 'sl-filter-input';

    // Style selects
    filterBar.querySelectorAll('.outreach-select').forEach(function (sel) {
      sel.className = 'sl-filter-select';
    });
  }

  /* ── 13. Upgrade bulk action bar ───────────────────────────────────────── */
  function upgradeBulkBar() {
    var bar = $('bulkActionBar');
    if (!bar || bar.dataset.upgraded) return;
    bar.dataset.upgraded = '1';
    bar.querySelectorAll('.bulk-btn').forEach(function (btn) {
      if (btn.classList.contains('bulk-btn-danger')) {
        btn.className = 'sl-btn sl-btn-danger';
      } else if (btn.classList.contains('bulk-btn-ghost')) {
        btn.className = 'sl-btn sl-btn-ghost';
      } else {
        btn.className = 'sl-btn sl-btn-secondary';
      }
    });
  }

  /* ── Helpers ────────────────────────────────────────────────────────────── */
  function escHtml(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function escAttr(s) { return String(s || '').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }

  /* ── 14. MutationObserver — re-upgrade cards after SLProspective.load() ── */
  function watchBoard() {
    var board = $('pipelineBoard');
    if (!board) return;
    var observer = new MutationObserver(function () {
      upgradeCards();
      upgradeColumnHeaders();
      updateColumnValues();
    });
    observer.observe(board, { childList: true, subtree: true });
  }

  /* ── 15. Init ───────────────────────────────────────────────────────────── */
  function init() {
    upgradeActionBar();
    injectAIFeatureBar();
    upgradeAIPanel();
    upgradeKPICards();
    upgradeColumnHeaders();
    upgradeCards();
    upgradeBulkBar();
    upgradeFilterBar();
    initDragDrop();
    watchBoard();
    updateAutoReplyBadge();
    // Re-check auto-reply status every 30s
    setInterval(updateAutoReplyBadge, 30000);
  }

  // Run after DOM ready and after SLProspective initializes
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(init, 300); });
  } else {
    setTimeout(init, 300);
  }

  // Also run when the Prospective tab is activated
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[onclick*="tabProspective"], [data-tab="tabProspective"]');
    if (btn) setTimeout(init, 200);
  });

  /* ── Public API ─────────────────────────────────────────────────────────── */
  window.SLPipelineUI = {
    runAIOpener: runAIOpener,
    runAIFollowUp: runAIFollowUp,
    runAIObjectionHandler: runAIObjectionHandler,
    runAISummary: runAISummary,
    runAIRescoreAll: runAIRescoreAll,
    runAIDraftSequence: runAIDraftSequence,
    runAIOpenerForCard: runAIOpenerForCard,
    useAISuggestion: useAISuggestion,
    toggleAutoReplyPanel: toggleAutoReplyPanel,
    refresh: init
  };

})();
