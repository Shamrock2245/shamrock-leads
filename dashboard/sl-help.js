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
      title: "Step 1: Lead Explorer & Scoring Engine",
      content: `
        <p style="margin-bottom:8px">Real-time arrest records from <strong>269 scrapers across 10 states</strong> populate here instantly.</p>
        <ul style="padding-left:18px;margin-bottom:8px">
          <li>🔥 <strong style="color:#ef4444">Hot Leads (80–100)</strong>: High bond, cash/surety, clear charges. Triggers Slack alert to <code>#leads</code> + iMessage queue.</li>
          <li>🟡 <strong style="color:#f59e0b">Warm Leads (50–79)</strong>: Standard booking records logged for follow-up.</li>
          <li>❄️ <strong style="color:#94a3b8">Disqualified (<50)</strong>: $0 bond, ROR, or capital/federal charges.</li>
        </ul>
        <p style="font-size:11px;color:#38bdf8">💡 Use top dropdowns to filter by State (FL, GA, SC, NC, TN, TX, LA, AL, CT, MS) or County.</p>
      `
    },
    {
      title: "Step 2: Appearance Bond Auto-Complete",
      content: `
        <p style="margin-bottom:8px">Click <strong>➕ Record Bond</strong> or click any lead row, then select your defendant:</p>
        <ol style="padding-left:18px;margin-bottom:8px">
          <li><strong>Auto-Fills Form</strong>: Defendant Name, Phone, Address, DOB, Booking #, County, Facility, Case #, Court Date/Time.</li>
          <li><strong>Florida 10% Statutory Premium</strong>: Auto-calculates 10% of total bond with the <strong>$100 statutory minimum per charge</strong>.</li>
          <li><strong>Per-Charge Table</strong>: Hydrates rows 1–4 (offenses, case numbers, POAs, amounts).</li>
          <li><strong>Sequential POA Suggestion</strong>: Auto-queries inventory for next sequential power number for OSI or Palmetto.</li>
        </ol>
      `
    },
    {
      title: "Step 3: DocuSeal Mobile E-Signature & Drive",
      content: `
        <p style="margin-bottom:8px">Send legal paperwork instantly to mobile devices:</p>
        <ul style="padding-left:18px;margin-bottom:8px">
          <li>📱 <strong>Mobile Signing Link</strong>: Sent via SMS, iMessage, or WhatsApp to indemnitor.</li>
          <li>🪪 <strong>ID OCR & Verification</strong>: Indemnitor uploads Driver License → system OCR extracts DL# & address → signs on phone.</li>
          <li>📁 <strong>Auto-Drive Filing</strong>: Upon completion, signed PDF is saved to Google Drive formatted as: <br/><code style="color:#34d399">&lt;LastName&gt;_&lt;MMDDYY&gt;_&lt;SURETY&gt;.pdf</code> (e.g. <code>Doe_080926_OSI.pdf</code>).</li>
        </ul>
      `
    },
    {
      title: "Step 4: Active Bond Kanban & Forfeitures",
      content: `
        <p style="margin-bottom:8px">Manage active cases across the 7 lifecycle statuses:</p>
        <ul style="padding-left:18px;margin-bottom:8px">
          <li><strong>Active ➔ Monitoring ➔ Alert ➔ Exonerated / Forfeited / Surrendered ➔ Reinstated</strong></li>
          <li>♻️ <strong>Auto-Release of POAs</strong>: Marking a bond <code>Exonerated</code> or <code>Surrendered</code> automatically releases power numbers back to available inventory.</li>
          <li>📍 <strong>GPS & Check-Ins</strong>: Automated weekly SMS reminders for selfie + GPS check-ins via Traccar integration.</li>
        </ul>
      `
    },
    {
      title: "Step 5: Multi-State Ops & System Health",
      content: `
        <p style="margin-bottom:8px">Operational dashboards for multi-state supervision:</p>
        <ul style="padding-left:18px;margin-bottom:8px">
          <li>📊 <strong>Multi-State Ops Tab</strong>: Live KPI gauges, scraper status, and run times across all 10 states.</li>
          <li>🧹 <strong>Data Hygiene</strong>: One-click tools to purge test records and repair mismatches to protect MongoDB Atlas M0 512MB storage.</li>
          <li>⚡ <strong>Automations Sweeper</strong>: Background watcher re-checking unset/$0 bonds every 30 minutes.</li>
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
          <li>📺 <strong>YouTube</strong>: Connects <code>@shamrock_2245</code>.</li>
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
      text: 'Select an arrest lead to view 0–100 lead score breakdown, OSINT contacts, and start bond recording.'
    },
    'active-bonds': {
      name: 'Active Bonds Kanban',
      text: 'Drag & drop active cases across the 7 statuses. Exonerated & surrendered bonds auto-release POAs.'
    },
    'paperwork': {
      name: 'DocuSeal E-Sign',
      text: 'Generate 14-doc OSI/Palmetto packets. Signed PDFs auto-save to Google Drive formatted as <LastName>_<MMDDYY>_<SURETY>.pdf.'
    },
    'multi-state': {
      name: 'Multi-State Ops',
      text: 'Monitor real-time scrapers across all 10 states (FL, GA, SC, NC, TN, TX, LA, AL, CT, MS).'
    },
    'social': {
      name: 'Postiz Social Engine',
      text: 'Manage social media channels, create posts, schedule calendar items, and monitor AI agent posting.'
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
