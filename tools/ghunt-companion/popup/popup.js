import * as co from "../lib/constants.js";
import * as utils from "../lib/utils.js";

const statusEl = document.getElementById("popup_status");

function setStatus(html, color = "#94a3b8") {
  if (statusEl) {
    statusEl.innerHTML = html;
    statusEl.style.color = color;
  }
}

document.getElementById("start_sync").addEventListener("click", async () => {
  const tabs = await chrome.tabs.query({ url: "https://accounts.google.com/*" });
  if (tabs.length > 0) {
    await chrome.tabs.update(tabs[0].id, { active: true, url: co.START_LOGIN_URL });
  } else {
    await chrome.tabs.create({ url: co.START_LOGIN_URL });
  }
  window.close();
});

document.getElementById("copy_now").addEventListener("click", async () => {
  setStatus("Checking cookies…", "#60a5fa");
  const res = await utils.getStoredOrLiveBlob();
  if (res.ok && res.blob) {
    try {
      await navigator.clipboard.writeText(res.blob);
      setStatus("✅ <strong>Blob copied to clipboard!</strong> Paste into ShamrockLeads GHunt box.", "#34d399");
    } catch (e) {
      setStatus("Copy failed. Try selecting and copying from Connected page.", "#f87171");
    }
  } else {
    const missing = (res.missing || []).join(", ");
    if (!res.found || !res.found.length) {
      setStatus("❌ Not logged into Google in this profile. Sign into Google first.", "#f87171");
    } else {
      setStatus(`⚠️ Missing tokens (${missing || "oauth_token"}). Click Synchronize, or use a profile with only 1 Google account.`, "#fbbf24");
    }
  }
});

