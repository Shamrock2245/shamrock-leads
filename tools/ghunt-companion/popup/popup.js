import * as co from "../lib/constants.js";

document.getElementById("start_sync").addEventListener("click", async () => {
  const tabs = await chrome.tabs.query({ url: "https://accounts.google.com/*" });
  if (tabs.length > 0) {
    await chrome.tabs.update(tabs[0].id, { active: true, url: co.START_LOGIN_URL });
  } else {
    await chrome.tabs.create({ url: co.START_LOGIN_URL });
  }
  window.close();
});
