import * as utils from "../lib/utils.js";

async function run(choice) {
  const status = document.getElementById("status_text");
  const stored = await chrome.storage.local.get("ghunt");
  if (!stored.ghunt || !stored.ghunt.ready) {
    status.textContent = "No Google session captured. Click the extension icon and sign in again.";
    return;
  }
  const encoded = utils.encodeCookies(stored.ghunt.cookies);

  if (choice === "base64") {
    try {
      await navigator.clipboard.writeText(encoded);
      await chrome.storage.local.remove("ghunt");
      status.textContent = "Blob copied. Paste it into ShamrockLeads OSINT → Save GHunt session, then you can close this tab.";
    } catch (err) {
      status.textContent = "Clipboard blocked. Select and copy this blob manually: " + encoded.slice(0, 40) + "…";
      const ta = document.createElement("textarea");
      ta.value = encoded;
      ta.style.width = "100%";
      ta.rows = 4;
      document.querySelector(".wrap").appendChild(ta);
      ta.select();
    }
    return;
  }

  if (choice === "server") {
    status.textContent = "Trying 127.0.0.1:60067 (will fail on Docker GHunt)…";
    try {
      const ping = await fetch("http://127.0.0.1:60067/ghunt_ping");
      if (!ping.ok) throw new Error("no ping");
      await fetch("http://127.0.0.1:60067/ghunt_feed", { method: "POST", body: encoded });
      await chrome.storage.local.remove("ghunt");
      status.textContent = "Fed local GHunt listener.";
    } catch (e) {
      status.textContent = "Cannot reach GHunt on 127.0.0.1:60067. Use Method 2 and paste into the dashboard.";
    }
  }
}

document.getElementById("method_base64").addEventListener("click", () => run("base64"));
document.getElementById("method_server").addEventListener("click", () => run("server"));
