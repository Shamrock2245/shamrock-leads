/* Florida statutory premium: $100 min per charge; 10% when penal > $1000. */
(function (w) {
  'use strict';

  function statutoryPremium(bondAmount, opts) {
    opts = opts || {};
    const chargeAmounts = (opts.chargeAmounts || []).map(Number).filter(a => a > 0);
    if (chargeAmounts.length) {
      return Math.round(chargeAmounts.reduce((s, a) => s + Math.max(100, a * 0.10), 0));
    }
    const ba = parseFloat(bondAmount) || 0;
    if (ba <= 0) return 0;
    const n = Math.max(1, parseInt(opts.chargeCount, 10) || 1);
    return Math.round(Math.max(100 * n, ba * 0.10));
  }

  function countCharges(text) {
    if (!text) return 1;
    const parts = String(text).split(/\s*[|;]\s*/).map(s => s.trim()).filter(Boolean);
    return Math.max(1, parts.length);
  }

  w.SLPremium = { statutoryPremium, countCharges };
})(window);
