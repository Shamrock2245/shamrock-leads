/**
 * ShamrockLeads — Operations Help Drawer (SLHelp)
 * F1 contextual guide · dual CRM / Social modes · Fortune-50 interaction polish
 */
(function () {
  'use strict';

  let activeGuide = 'crm';
  let currentSlideCRM = 0;
  let currentSlideSocial = 0;
  let isOpen = false;
  let lastFocus = null;

  const CRM_SLIDES = [
    {
      title: 'Step 1 · Lead Explorer',
      content: `
        <p>Arrests from <strong>10 states</strong> score automatically:</p>
        <ul>
          <li>🔥 <strong style="color:#f87171">Hot (80–100)</strong> — prioritize; Slack may alert <code>#leads</code>.</li>
          <li>🟡 <strong style="color:#fbbf24">Warm (50–79)</strong> — follow up when capacity allows.</li>
          <li>❄️ <strong style="color:#94a3b8">Cold / DQ</strong> — $0 bond, ROR, or no-bond — skip unless directed.</li>
        </ul>
        <p style="font-size:12px;color:#64748b">Always filter <strong>State</strong> then <strong>County</strong>. Lee FL ≠ Lee SC.</p>
      `,
    },
    {
      title: 'Step 2 · Write a Bond',
      content: `
        <p>From a lead open <strong>Write Bond</strong>:</p>
        <ol>
          <li>Confirm defendant, charges, and amounts.</li>
          <li>Premium auto-calcs (~10%, <strong>$100 min per charge</strong>).</li>
          <li>Surety: <strong>OSI</strong> preferred in FL, else <strong>Palmetto</strong>.</li>
          <li>Accept the next <strong>POA</strong> — never invent powers.</li>
          <li>Enter indemnitor (scan ID when possible).</li>
        </ol>
        <p style="font-size:12px;color:#64748b">Full walkthrough: <a href="/guide" target="_blank" rel="noopener">/guide → Chapter 2</a></p>
      `,
    },
    {
      title: 'Step 3 · Paperwork & DocuSeal',
      content: `
        <ul>
          <li>Send with <strong>DocuSeal</strong> (new packets — not SignNow).</li>
          <li>Cosigner may sign <em>before</em> defendant is fully matched; staff binds later.</li>
          <li>Signed PDF auto-files: <code>LastName_MMDDYY_SURETY.pdf</code>.</li>
          <li>Lost link → resend from Paperwork. Don’t duplicate packets.</li>
        </ul>
        <p style="font-size:12px;color:#64748b">Portal · paperwork.shamrockbailbonds.biz</p>
      `,
    },
    {
      title: 'Step 4 · Active Bonds board',
      content: `
        <p>After posting, manage lifecycle on the Kanban:</p>
        <ul>
          <li><strong>Active → Monitoring → Alert → Exonerated / Forfeited / Surrendered → Reinstated</strong></li>
          <li>Exonerated / Surrendered can <strong>auto-release POAs</strong>.</li>
          <li>Read confirm dialogs on forfeiture — high stakes.</li>
        </ul>
      `,
    },
    {
      title: 'Step 5 · Bail School',
      content: `
        <p><strong>school.shamrockbailbonds.biz</strong> is education — not Write Bond.</p>
        <ul>
          <li>Point students to enroll (typical tracks <strong>$199</strong> / <strong>$649</strong>).</li>
          <li>Never mix student accounts with bond packets.</li>
          <li>If down: note time + error, escalate — don’t invent enrollment status.</li>
        </ul>
      `,
    },
    {
      title: 'Step 6 · Ops health',
      content: `
        <ul>
          <li>📊 <strong>Multi-State Ops</strong> — scraper health across 10 states.</li>
          <li>🧹 Data hygiene — managers only; protect Atlas storage.</li>
          <li>Printable SOP: <a href="/guide" target="_blank" rel="noopener">leads…/guide</a></li>
        </ul>
      `,
    },
  ];

  const POSTIZ_SLIDES = [
    {
      title: 'Step 1 · Connect channels',
      content: `
        <p>Open <strong style="color:#38bdf8">social.shamrockbailbonds.biz</strong> → <strong>Integrations</strong>:</p>
        <ul>
          <li>Facebook Page · Instagram <code>@shamrock_bail_bonds</code></li>
          <li>X <code>@ShamrockBail_FL</code> · YouTube <code>@Shamrock2245</code></li>
          <li>Google Business Profile</li>
        </ul>
        <p style="font-size:12px;color:#64748b">Green check = connected.</p>
      `,
    },
    {
      title: 'Step 2 · Create a post',
      content: `
        <ol>
          <li>Click <strong>Create Post</strong>.</li>
          <li>Select channels.</li>
          <li>Write a clear caption (include <strong>(239) 334-2245</strong> when relevant).</li>
          <li>Add 3–5 local hashtags.</li>
        </ol>
        <p style="font-size:12px;color:#34d399">Use the mobile preview before publishing.</p>
      `,
    },
    {
      title: 'Step 3 · Media rules',
      content: `
        <ul>
          <li>Images: PNG/JPG · 1080×1080 or 1080×1350</li>
          <li>Video: MP4 under 100MB · prefer under 60s for Reels/Shorts</li>
        </ul>
        <p style="font-size:12px;color:#64748b">No private arrest details or client mugshots without approval.</p>
      `,
    },
    {
      title: 'Step 4 · Schedule vs now',
      content: `
        <ul>
          <li>🚀 <strong>Post Now</strong> — immediate publish</li>
          <li>📅 <strong>Schedule</strong> — pick date/time</li>
        </ul>
        <p style="font-size:12px;color:#64748b">Review <strong>Calendar</strong> / <strong>Queue</strong> so two people don’t double-post.</p>
      `,
    },
    {
      title: 'Step 5 · AI queue',
      content: `
        <ul>
          <li>Automated drafts may appear for education / non-PII stats.</li>
          <li>Always review the <strong>Queue</strong> before anything sensitive goes live.</li>
        </ul>
      `,
    },
  ];

  const TAB_CONTEXTS = {
    leads: {
      name: 'Lead Explorer',
      text: 'Pick a hot/warm arrest → Write Bond. Filter by state/county so you never mix same-name counties.',
    },
    'active-bonds': {
      name: 'Active Bonds',
      text: 'Drag cases across statuses. Exonerated/surrendered can release POAs. Confirm before forfeiture.',
    },
    paperwork: {
      name: 'Paperwork',
      text: 'Send DocuSeal links, check status, resend. Signed files auto-file to Drive as LastName_MMDDYY_SURETY.pdf.',
    },
    'multi-state': {
      name: 'Multi-State Ops',
      text: 'Scraper health and KPIs across FL, GA, SC, NC, TN, TX, LA, AL, CT, MS.',
    },
    social: {
      name: 'Social',
      text: 'Create and schedule posts at social.shamrockbailbonds.biz. No private client details.',
    },
    intake: {
      name: 'Intake',
      text: 'Match cosigners carefully. If two defendants could match, stop and escalate.',
    },
  };

  function getActiveSlides() {
    return activeGuide === 'crm' ? CRM_SLIDES : POSTIZ_SLIDES;
  }

  function getActiveSlideIndex() {
    return activeGuide === 'crm' ? currentSlideCRM : currentSlideSocial;
  }

  function setActiveSlideIndex(idx) {
    if (activeGuide === 'crm') currentSlideCRM = idx;
    else currentSlideSocial = idx;
  }

  function setGuideMode(mode) {
    activeGuide = mode === 'social' ? 'social' : 'crm';
    const crmBtn = document.getElementById('slGuideTabCRM');
    const socialBtn = document.getElementById('slGuideTabSocial');
    const wizard = document.getElementById('slHelpWizard');

    if (crmBtn && socialBtn) {
      const isCrm = activeGuide === 'crm';
      crmBtn.classList.toggle('active-crm', isCrm);
      socialBtn.classList.toggle('active-social', !isCrm);
      crmBtn.classList.toggle('active-social', false);
      socialBtn.classList.toggle('active-crm', false);
      crmBtn.setAttribute('aria-selected', isCrm ? 'true' : 'false');
      socialBtn.setAttribute('aria-selected', isCrm ? 'false' : 'true');
    }
    if (wizard) {
      wizard.classList.toggle('mode-social', activeGuide === 'social');
    }
    updateSlideUI();
  }

  function renderProgress(slideIdx, total) {
    const el = document.getElementById('wizardProgress');
    if (!el) return;
    let html = '';
    for (let i = 0; i < total; i++) {
      const cls = i < slideIdx ? 'done' : i === slideIdx ? 'active' : '';
      html += `<span class="sl-help-progress-dot ${cls}"></span>`;
    }
    el.innerHTML = html;
  }

  function updateSlideUI() {
    const slides = getActiveSlides();
    const slideIdx = getActiveSlideIndex();
    const slide = slides[slideIdx] || slides[0];
    if (!slide) return;

    const titleEl = document.getElementById('wizardGuideTitle');
    const contentEl = document.getElementById('wizardSlideContent');
    const indicatorEl = document.getElementById('wizardStepIndicator');
    const prevBtn = document.getElementById('wizardPrevBtn');
    const nextBtn = document.getElementById('wizardNextBtn');

    if (!contentEl) return;

    if (titleEl) {
      titleEl.textContent =
        activeGuide === 'crm' ? 'ShamrockLeads Auto-CRM' : 'Postiz Social Media';
    }
    if (indicatorEl) {
      indicatorEl.textContent = `Step ${slideIdx + 1} of ${slides.length}`;
    }

    contentEl.innerHTML = `
      <div class="slide-title">${slide.title}</div>
      ${slide.content}
    `;

    renderProgress(slideIdx, slides.length);

    if (prevBtn) {
      const atStart = slideIdx === 0;
      prevBtn.style.opacity = atStart ? '0.45' : '1';
      prevBtn.style.pointerEvents = atStart ? 'none' : 'auto';
      prevBtn.disabled = atStart;
    }

    if (nextBtn) {
      const atEnd = slideIdx === slides.length - 1;
      nextBtn.textContent = atEnd ? 'Restart ↺' : 'Next →';
    }
  }

  function updateContextBanner() {
    let activeTabKey = 'leads';
    const activeTabEl = document.querySelector(
      '.tab-btn.active, .nav-item.active, [data-tab].active, .nav-tab.active'
    );
    if (activeTabEl) {
      const tabId =
        activeTabEl.getAttribute('data-tab') ||
        activeTabEl.getAttribute('id') ||
        activeTabEl.textContent ||
        '';
      const lower = String(tabId).toLowerCase();
      for (const k of Object.keys(TAB_CONTEXTS)) {
        if (lower.includes(k)) {
          activeTabKey = k;
          break;
        }
      }
    }

    const ctx = TAB_CONTEXTS[activeTabKey] || TAB_CONTEXTS.leads;
    const nameEl = document.getElementById('slHelpActiveTabName');
    const textEl = document.getElementById('slHelpContextText');
    if (nameEl) nameEl.textContent = ctx.name;
    if (textEl) textEl.textContent = ctx.text;

    if (activeTabKey === 'social' && activeGuide !== 'social') {
      setGuideMode('social');
    }
  }

  function open() {
    const drawer = document.getElementById('slHelpDrawer');
    const backdrop = document.getElementById('slHelpBackdrop');
    if (!drawer) return;
    lastFocus = document.activeElement;
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    if (backdrop) {
      backdrop.classList.add('open');
      backdrop.setAttribute('aria-hidden', 'false');
    }
    isOpen = true;
    updateSlideUI();
    updateContextBanner();
    // Focus first control for keyboard users
    setTimeout(() => {
      const closeBtn = drawer.querySelector('.sl-help-close');
      if (closeBtn) closeBtn.focus();
    }, 40);
  }

  function close() {
    const drawer = document.getElementById('slHelpDrawer');
    const backdrop = document.getElementById('slHelpBackdrop');
    if (!drawer) return;
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    if (backdrop) {
      backdrop.classList.remove('open');
      backdrop.setAttribute('aria-hidden', 'true');
    }
    isOpen = false;
    if (lastFocus && typeof lastFocus.focus === 'function') {
      try {
        lastFocus.focus();
      } catch (e) {}
    }
  }

  function toggle() {
    if (isOpen) close();
    else open();
  }

  function nextSlide() {
    const slides = getActiveSlides();
    let idx = getActiveSlideIndex();
    idx = (idx + 1) % slides.length;
    setActiveSlideIndex(idx);
    updateSlideUI();
  }

  function prevSlide() {
    let idx = getActiveSlideIndex();
    if (idx > 0) {
      idx -= 1;
      setActiveSlideIndex(idx);
      updateSlideUI();
    }
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'F1') {
      e.preventDefault();
      toggle();
      return;
    }
    if (!isOpen) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
      return;
    }
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      nextSlide();
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      prevSlide();
    }
  });

  document.addEventListener('DOMContentLoaded', function () {
    const backdrop = document.getElementById('slHelpBackdrop');
    if (backdrop) {
      backdrop.addEventListener('click', close);
    }
    // Initial progress render if drawer markup is present
    updateSlideUI();
  });

  window.SLHelp = {
    open,
    close,
    toggle,
    nextSlide,
    prevSlide,
    setGuideMode,
  };
})();
