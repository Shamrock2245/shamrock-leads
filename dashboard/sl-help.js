/**
 * ShamrockLeads — In-App Ecosystem Help Drawer & Dual Guide Wizard (SLHelp)
 * Controls F1 shortcut, drawer toggling, contextual tab detection, and interactive wizards
 * for both ShamrockLeads Auto-CRM (leads.shamrockbailbonds.biz) and Postiz Social Media (social.shamrockbailbonds.biz).
 */
(function() {
  'use strict';

  let activeGuide = 'crm'; // 'crm' or 'social'
  let currentSlideCRM = 0;
  let currentSlideSocial = 0;
  let isOpen = false;

  const CRM_SLIDES = [
    {
      title: "Step 1: Lead Explorer (find the arrest)",
      content: `
        <p style="margin-bottom:8px">Arrests from <strong>10 states</strong> show up automatically with a score:</p>
        <ul style="padding-left:18px;margin-bottom:8px">
          <li>🔥 <strong style="color:#ef4444">Hot (80–100)</strong> — call/text first; Slack may alert <code>#leads</code>.</li>
          <li>🟡 <strong style="color:#f59e0b">Warm (50–79)</strong> — good follow-ups when you have time.</li>
          <li>❄️ <strong style="color:#94a3b8">Cold / DQ</strong> — often $0 bond, ROR, or no bond — skip unless a bondsman says otherwise.</li>
        </ul>
        <p style="font-size:11px;color:#38bdf8">Filter by <strong>State</strong> then <strong>County</strong>. Lee FL is not Lee SC.</p>
      `
    },
    {
      title: "Step 2: How to Write a Bond",
      content: `
        <p style="margin-bottom:8px">Open <strong>➕ Record Bond / ✍️ Write Bond</strong> from a lead:</p>
        <ol style="padding-left:18px;margin-bottom:8px">
          <li>Confirm defendant, charges, bond amounts.</li>
          <li>Premium auto-calcs (~10%, <strong>$100 min per charge</strong> in FL style).</li>
          <li>Pick surety <strong>OSI</strong> (preferred in FL) or <strong>Palmetto</strong>.</li>
          <li>Accept the next <strong>POA</strong> from inventory — never invent power numbers.</li>
          <li>Enter indemnitor (scan ID when possible).</li>
        </ol>
        <p style="font-size:11px;color:#34d399">Full write-up: open <a href="/guide" target="_blank" style="color:#38bdf8">/guide</a> → Chapter 2.</p>
      `
    },
    {
      title: "Step 3: Paperwork (DocuSeal) & signing",
      content: `
        <p style="margin-bottom:8px">After Write Bond fields look right:</p>
        <ul style="padding-left:18px;margin-bottom:8px">
          <li>Send packet with provider <strong>DocuSeal</strong> (not SignNow).</li>
          <li>Client signs on phone via link, or on office iPad (portal).</li>
          <li>Signed PDF auto-files to Drive: <code style="color:#34d399">LastName_MMDDYY_SURETY.pdf</code>.</li>
          <li>Lost link? Use Paperwork tab → status / resend — don’t create a second packet.</li>
        </ul>
        <p style="font-size:11px;color:#94a3b8">Portal: paperwork.shamrockbailbonds.biz · Sign: sign.shamrockbailbonds.biz</p>
      `
    },
    {
      title: "Step 4: Active Bonds board",
      content: `
        <p style="margin-bottom:8px">After posting, manage the case on the Kanban:</p>
        <ul style="padding-left:18px;margin-bottom:8px">
          <li><strong>Active → Monitoring → Alert → Exonerated / Forfeited / Surrendered → Reinstated</strong></li>
          <li>Exonerated / Surrendered can <strong>auto-release POAs</strong> back to inventory.</li>
          <li>Read confirm dialogs on forfeiture — those are serious.</li>
        </ul>
      `
    },
    {
      title: "Step 5: Bail School website (staff)",
      content: `
        <p style="margin-bottom:8px"><strong>school.shamrockbailbonds.biz</strong> is education — not Write Bond.</p>
        <ul style="padding-left:18px;margin-bottom:8px">
          <li>Point students to the school site to enroll (typical tracks: <strong>$199</strong> / <strong>$649</strong>).</li>
          <li>Don’t mix student logins with bond packets.</li>
          <li>If the site is down, note the time + error and escalate — don’t invent enrollment status.</li>
        </ul>
      `
    },
    {
      title: "Step 6: Ops health (optional)",
      content: `
        <ul style="padding-left:18px;margin-bottom:8px">
          <li>📊 <strong>Multi-State Ops</strong> — scraper health across 10 states.</li>
          <li>🧹 Data hygiene tools — managers only; protect database size.</li>
          <li>Full employee manual: <a href="/guide" target="_blank" style="color:#38bdf8">leads…/guide</a> (also print-friendly).</li>
        </ul>
      `
    }
  ];

  const POSTIZ_SLIDES = [
    {
      title: "Step 1: Connect Social Accounts",
      content: `
        <p style="margin-bottom:8px">Log into <strong style="color:#38bdf8">social.shamrockbailbonds.biz</strong> and click <strong>Integrations</strong> on the left menu.</p>
        <ul style="padding-left:18px;margin-bottom:8px">
          <li>📘 <strong>Facebook</strong>: Official Shamrock Facebook Page.</li>
          <li>📸 <strong>Instagram</strong>: Connects <code>@shamrock_bail_bonds</code>.</li>
          <li>🪶 <strong>X / Twitter</strong>: Connects <code>@ShamrockBail_FL</code>.</li>
          <li>📺 <strong>YouTube</strong>: Connects <code>@Shamrock2245</code>.</li>
          <li>📍 <strong>Google My Business</strong>: Office listing.</li>
        </ul>
        <p style="font-size:11px;color:#94a3b8">A green checkmark ✅ appears when authorization completes.</p>
      `
    },
    {
      title: "Step 2: Create a New Post",
      content: `
        <p style="margin-bottom:8px">Click the <strong>Create Post</strong> button (pencil icon) on the top left.</p>
        <ol style="padding-left:18px;margin-bottom:8px">
          <li>Select target social channels by clicking their icons.</li>
          <li>Write your caption (e.g. <em>"Open 24/7 in Lee County! Call 239-334-2245"</em>).</li>
          <li>Add 3–5 hashtags (e.g. <code>#BailBonds #LeeCounty #ShamrockBail</code>).</li>
        </ol>
        <p style="font-size:11px;color:#34d399">💡 Tip: Use the mobile preview on the right to double-check text spacing!</p>
      `
    },
    {
      title: "Step 3: Media & Video Guidelines",
      content: `
        <p style="margin-bottom:8px">Click <strong>Add Media</strong> to upload photos or videos:</p>
        <ul style="padding-left:18px;margin-bottom:8px">
          <li>🖼 <strong>Images</strong>: PNG or JPG (1080x1080px square or 1080x1350px portrait).</li>
          <li>🎥 <strong>Videos</strong>: MP4 under 100MB (keep under 60 seconds for Reels/Shorts).</li>
        </ul>
        <p style="font-size:11px;color:#94a3b8">Postiz automatically optimizes image resolutions for each network.</p>
      `
    },
    {
      title: "Step 4: Schedule vs Publish Now",
      content: `
        <p style="margin-bottom:8px">Choose when your post goes live:</p>
        <ul style="padding-left:18px;margin-bottom:8px">
          <li>🚀 <strong>Post Now</strong>: Sends message immediately across all channels.</li>
          <li>📅 <strong>Schedule</strong>: Pick exact date and time (e.g. Tomorrow at 9:00 AM).</li>
        </ul>
        <p style="font-size:11px;color:#e2e8f0">Click <strong>Calendar</strong> on the left menu anytime to view or edit upcoming scheduled posts.</p>
      `
    },
    {
      title: "Step 5: Postiz MCP AI Automation",
      content: `
        <p style="margin-bottom:8px">Our system includes automated <strong>Postiz MCP</strong> integration:</p>
        <ul style="padding-left:18px;margin-bottom:8px">
          <li>🤖 <strong>AI Arrest Highlights</strong>: Auto-generates non-PII county stats.</li>
          <li>📢 <strong>Bail Education</strong>: Posts rights & legal FAQs automatically.</li>
        </ul>
        <p style="font-size:11px;color:#38bdf8">Check the <strong>Queue</strong> tab to review AI drafts before they post!</p>
      `
    }
  ];

  const TAB_CONTEXTS = {
    'leads': {
      name: 'Lead Explorer',
      text: 'Pick a hot/warm arrest → open Write Bond. Filter by state/county first so you never mix Lee FL with Lee SC.'
    },
    'active-bonds': {
      name: 'Active Bonds Kanban',
      text: 'Drag cases across statuses. Exonerated/surrendered can release POAs. Confirm before forfeiture.'
    },
    'paperwork': {
      name: 'Paperwork / DocuSeal',
      text: 'Send sign links, check status, resend. Signed files auto-file to Drive as LastName_MMDDYY_SURETY.pdf.'
    },
    'multi-state': {
      name: 'Multi-State Ops',
      text: 'Scraper health and KPIs across FL, GA, SC, NC, TN, TX, LA, AL, CT, MS.'
    },
    'social': {
      name: 'Social (Postiz)',
      text: 'Create/schedule posts at social.shamrockbailbonds.biz. No private client arrest details in posts.'
    },
    'intake': {
      name: 'Intake',
      text: 'Match cosigners to defendants carefully. If two people could match, stop and escalate.'
    }
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
    activeGuide = mode;
    const crmBtn = document.getElementById('slGuideTabCRM');
    const socialBtn = document.getElementById('slGuideTabSocial');

    if (crmBtn && socialBtn) {
      if (mode === 'crm') {
        crmBtn.style.background = '#059669';
        crmBtn.style.color = '#fff';
        socialBtn.style.background = '#1e293b';
        socialBtn.style.color = '#94a3b8';
      } else {
        socialBtn.style.background = '#0284c7';
        socialBtn.style.color = '#fff';
        crmBtn.style.background = '#1e293b';
        crmBtn.style.color = '#94a3b8';
      }
    }
    updateSlideUI();
  }

  function updateSlideUI() {
    const slides = getActiveSlides();
    const slideIdx = getActiveSlideIndex();
    const slide = slides[slideIdx];

    const titleEl = document.getElementById('wizardGuideTitle');
    const contentEl = document.getElementById('wizardSlideContent');
    const indicatorEl = document.getElementById('wizardStepIndicator');
    const prevBtn = document.getElementById('wizardPrevBtn');
    const nextBtn = document.getElementById('wizardNextBtn');

    if (!contentEl) return;

    if (titleEl) {
      titleEl.textContent = activeGuide === 'crm' ? 'ShamrockLeads Auto-CRM Guide' : 'Postiz Social Media Guide';
    }

    indicatorEl.textContent = `Step ${slideIdx + 1} of ${slides.length}`;
    contentEl.innerHTML = `
      <div style="font-weight:700;color:#f8fafc;margin-bottom:8px;font-size:14px">${slide.title}</div>
      ${slide.content}
    `;

    prevBtn.style.opacity = slideIdx === 0 ? '0.5' : '1';
    prevBtn.style.pointerEvents = slideIdx === 0 ? 'none' : 'auto';

    if (slideIdx === slides.length - 1) {
      nextBtn.textContent = 'Restart Guide ↺';
      nextBtn.style.background = '#0284c7';
    } else {
      nextBtn.textContent = 'Next Step →';
      nextBtn.style.background = activeGuide === 'crm' ? '#059669' : '#0284c7';
    }
  }

  function updateContextBanner() {
    let activeTabKey = 'leads';
    const activeTabEl = document.querySelector('.tab-btn.active, .nav-item.active, [data-tab].active');
    if (activeTabEl) {
      const tabId = activeTabEl.getAttribute('data-tab') || activeTabEl.getAttribute('id') || '';
      for (const k in TAB_CONTEXTS) {
        if (tabId.includes(k)) {
          activeTabKey = k;
          break;
        }
      }
    }

    const ctx = TAB_CONTEXTS[activeTabKey] || TAB_CONTEXTS['leads'];
    const nameEl = document.getElementById('slHelpActiveTabName');
    const textEl = document.getElementById('slHelpContextText');

    if (nameEl) nameEl.textContent = ctx.name;
    if (textEl) textEl.textContent = ctx.text;

    // Auto-switch default guide tab if on social tab
    if (activeTabKey === 'social' && activeGuide !== 'social') {
      setGuideMode('social');
    }
  }

  function open() {
    const drawer = document.getElementById('slHelpDrawer');
    if (!drawer) return;
    drawer.style.display = 'flex';
    setTimeout(() => {
      drawer.style.right = '0px';
    }, 10);
    isOpen = true;
    updateSlideUI();
    updateContextBanner();
  }

  function close() {
    const drawer = document.getElementById('slHelpDrawer');
    if (!drawer) return;
    drawer.style.right = '-480px';
    setTimeout(() => {
      drawer.style.display = 'none';
    }, 300);
    isOpen = false;
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
      idx--;
      setActiveSlideIndex(idx);
      updateSlideUI();
    }
  }

  // Keyboard shortcut handler (F1)
  document.addEventListener('keydown', function(e) {
    if (e.key === 'F1') {
      e.preventDefault();
      toggle();
    } else if (e.key === 'Escape' && isOpen) {
      close();
    }
  });

  // Export SLHelp global module
  window.SLHelp = {
    open,
    close,
    toggle,
    nextSlide,
    prevSlide,
    setGuideMode
  };
})();
