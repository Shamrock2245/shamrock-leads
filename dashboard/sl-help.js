/**
 * ShamrockLeads — In-App Ecosystem Help Drawer & Postiz Wizard (SLHelp)
 * Controls F1 shortcut, drawer toggling, contextual tab detection, and Postiz slides.
 */
(function() {
  'use strict';

  let currentSlide = 0;
  let isOpen = false;

  const POSTIZ_SLIDES = [
    {
      title: "Step 1: Connect Your Social Accounts",
      content: `
        <p style="margin-bottom:8px">Log into <strong style="color:#38bdf8">social.shamrockbailbonds.biz</strong> and click <strong>Integrations</strong> on the left menu.</p>
        <ul style="padding-left:18px;margin-bottom:8px">
          <li>📘 <strong>Facebook</strong>: Connects official Facebook Page.</li>
          <li>📸 <strong>Instagram</strong>: Connects <code>@shamrock_bail_bonds</code>.</li>
          <li>🪶 <strong>X / Twitter</strong>: Connects <code>@ShamrockBail_FL</code>.</li>
          <li>📺 <strong>YouTube</strong>: Connects <code>@shamrock_2245</code>.</li>
          <li>📍 <strong>Google My Business</strong>: Connects office listing.</li>
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

  function updateSlideUI() {
    const slide = POSTIZ_SLIDES[currentSlide];
    const contentEl = document.getElementById('postizSlideContent');
    const indicatorEl = document.getElementById('postizStepIndicator');
    const prevBtn = document.getElementById('postizPrevBtn');
    const nextBtn = document.getElementById('postizNextBtn');

    if (!contentEl) return;

    indicatorEl.textContent = `Step ${currentSlide + 1} of ${POSTIZ_SLIDES.length}`;
    contentEl.innerHTML = `
      <div style="font-weight:700;color:#f8fafc;margin-bottom:8px;font-size:14px">${slide.title}</div>
      ${slide.content}
    `;

    prevBtn.style.opacity = currentSlide === 0 ? '0.5' : '1';
    prevBtn.style.pointerEvents = currentSlide === 0 ? 'none' : 'auto';

    if (currentSlide === POSTIZ_SLIDES.length - 1) {
      nextBtn.textContent = 'Restart Guide ↺';
      nextBtn.style.background = '#0284c7';
    } else {
      nextBtn.textContent = 'Next Step →';
      nextBtn.style.background = '#059669';
    }
  }

  function updateContextBanner() {
    // Detect active tab from DOM
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
    currentSlide = (currentSlide + 1) % POSTIZ_SLIDES.length;
    updateSlideUI();
  }

  function prevSlide() {
    if (currentSlide > 0) {
      currentSlide--;
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
    prevSlide
  };
})();
