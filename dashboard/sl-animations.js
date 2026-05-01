/**
 * ShamrockLeads — Premium Dashboard Animations (GSAP)
 * =====================================================
 * Micro-animations that make the dashboard feel alive and premium.
 * Uses GSAP 3.12 via CDN — no build system needed.
 *
 * Features:
 *   - Card entrance animations (staggered fade-up)
 *   - Counter animations (numbers count from 0)
 *   - Tab crossfade transitions
 *   - Stat card hover pulses
 *   - Table row entrance stagger
 *   - Loading skeleton shimmer
 */

const SLAnimations = (() => {
  'use strict';

  // Check if GSAP is loaded
  const _hasGSAP = () => typeof gsap !== 'undefined';

  // ── Card Entrance Animation ──
  function animateCards(selector = '.stat-card') {
    if (!_hasGSAP()) return;
    const cards = document.querySelectorAll(selector);
    if (!cards.length) return;

    gsap.fromTo(cards,
      { opacity: 0, y: 24, scale: 0.96 },
      {
        opacity: 1,
        y: 0,
        scale: 1,
        duration: 0.5,
        stagger: 0.08,
        ease: 'power2.out',
        clearProps: 'transform',
      }
    );
  }

  // ── Counter Animation ──
  // Makes numbers count up from 0 to their target value
  function animateCounters(selector = '.stat-value') {
    if (!_hasGSAP()) return;
    const elements = document.querySelectorAll(selector);

    elements.forEach(el => {
      const text = el.textContent.trim();
      // Extract numeric value (handles $1,234 / 1,234 / 56.7%)
      const cleaned = text.replace(/[$,%]/g, '').replace(/,/g, '');
      const target = parseFloat(cleaned);
      if (isNaN(target) || target === 0) return;

      const hasComma = text.includes(',');
      const hasDollar = text.startsWith('$');
      const hasPercent = text.endsWith('%');
      const isDecimal = cleaned.includes('.');

      const counter = { value: 0 };
      gsap.to(counter, {
        value: target,
        duration: 1.2,
        ease: 'power2.out',
        onUpdate: () => {
          let formatted;
          if (isDecimal) {
            formatted = counter.value.toFixed(1);
          } else {
            formatted = Math.round(counter.value).toLocaleString();
          }
          if (hasDollar) formatted = '$' + formatted;
          if (hasPercent) formatted += '%';
          el.textContent = formatted;
        },
      });
    });
  }

  // ── Tab Crossfade Transition ──
  function animateTabSwitch(outEl, inEl) {
    if (!_hasGSAP()) {
      if (outEl) outEl.classList.remove('active');
      if (inEl) inEl.classList.add('active');
      return;
    }

    const tl = gsap.timeline();

    if (outEl) {
      tl.to(outEl, {
        opacity: 0,
        y: -8,
        duration: 0.15,
        ease: 'power1.in',
        onComplete: () => {
          outEl.classList.remove('active');
          outEl.style.display = 'none';
          gsap.set(outEl, { clearProps: 'all' });
        },
      });
    }

    if (inEl) {
      tl.call(() => {
        inEl.classList.add('active');
        inEl.style.display = 'block';
      });
      tl.fromTo(inEl,
        { opacity: 0, y: 12 },
        {
          opacity: 1,
          y: 0,
          duration: 0.3,
          ease: 'power2.out',
          clearProps: 'transform',
        }
      );
    }

    return tl;
  }

  // ── Table Row Entrance ──
  function animateTableRows(tableSelector) {
    if (!_hasGSAP()) return;
    const rows = document.querySelectorAll(`${tableSelector} tbody tr`);
    if (!rows.length) return;

    gsap.fromTo(rows,
      { opacity: 0, x: -12 },
      {
        opacity: 1,
        x: 0,
        duration: 0.3,
        stagger: 0.03,
        ease: 'power2.out',
        clearProps: 'transform',
      }
    );
  }

  // ── Stat Card Hover Effects ──
  function initHoverEffects() {
    if (!_hasGSAP()) return;

    document.querySelectorAll('.stat-card').forEach(card => {
      card.addEventListener('mouseenter', () => {
        gsap.to(card, {
          y: -3,
          scale: 1.02,
          boxShadow: '0 8px 32px rgba(16, 185, 129, 0.15)',
          duration: 0.25,
          ease: 'power2.out',
        });
      });

      card.addEventListener('mouseleave', () => {
        gsap.to(card, {
          y: 0,
          scale: 1,
          boxShadow: '',
          duration: 0.3,
          ease: 'power2.out',
          clearProps: 'boxShadow',
        });
      });
    });
  }

  // ── Modal Entrance Animation ──
  function animateModalIn(modalEl) {
    if (!_hasGSAP() || !modalEl) return;

    const content = modalEl.querySelector('.modal-content') || modalEl.firstElementChild;
    modalEl.style.display = 'flex';

    const tl = gsap.timeline();
    tl.fromTo(modalEl,
      { opacity: 0 },
      { opacity: 1, duration: 0.2, ease: 'power1.out' }
    );

    if (content) {
      tl.fromTo(content,
        { scale: 0.92, y: 20 },
        { scale: 1, y: 0, duration: 0.3, ease: 'back.out(1.4)' },
        '-=0.15'
      );
    }

    return tl;
  }

  function animateModalOut(modalEl, callback) {
    if (!_hasGSAP() || !modalEl) {
      if (modalEl) modalEl.style.display = 'none';
      if (callback) callback();
      return;
    }

    gsap.to(modalEl, {
      opacity: 0,
      duration: 0.2,
      ease: 'power1.in',
      onComplete: () => {
        modalEl.style.display = 'none';
        gsap.set(modalEl, { clearProps: 'all' });
        if (callback) callback();
      },
    });
  }

  // ── Badge Pulse ──
  function pulseBadge(badgeEl) {
    if (!_hasGSAP() || !badgeEl) return;
    gsap.fromTo(badgeEl,
      { scale: 1 },
      {
        scale: 1.3,
        duration: 0.2,
        yoyo: true,
        repeat: 1,
        ease: 'power2.out',
      }
    );
  }

  // ── Notification Toast ──
  function animateToast(toastEl) {
    if (!_hasGSAP() || !toastEl) return;

    gsap.fromTo(toastEl,
      { opacity: 0, y: 40, scale: 0.9 },
      {
        opacity: 1,
        y: 0,
        scale: 1,
        duration: 0.4,
        ease: 'back.out(1.6)',
      }
    );

    // Auto-dismiss after 4 seconds
    gsap.to(toastEl, {
      opacity: 0,
      y: -20,
      delay: 4,
      duration: 0.3,
      ease: 'power2.in',
      onComplete: () => toastEl.remove(),
    });
  }

  // ── Refresh Spin ──
  function animateRefreshButton(btn) {
    if (!_hasGSAP() || !btn) return;
    gsap.to(btn, {
      rotation: 360,
      duration: 0.6,
      ease: 'power2.inOut',
      onComplete: () => gsap.set(btn, { clearProps: 'rotation' }),
    });
  }

  // ── Init on DOM Ready ──
  function init() {
    if (!_hasGSAP()) {
      console.warn('[SLAnimations] GSAP not loaded — animations disabled');
      return;
    }

    console.log('[SLAnimations] ✅ GSAP loaded — initializing animations');

    // Initial entrance animations
    requestAnimationFrame(() => {
      animateCards();
      animateCounters();
      initHoverEffects();
    });
  }

  // Auto-init when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // ── Public API ──
  return {
    animateCards,
    animateCounters,
    animateTabSwitch,
    animateTableRows,
    animateModalIn,
    animateModalOut,
    pulseBadge,
    animateToast,
    animateRefreshButton,
    initHoverEffects,
    init,
  };
})();
