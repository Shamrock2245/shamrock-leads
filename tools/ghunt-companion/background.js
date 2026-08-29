import * as co from "./lib/constants.js";
import * as utils from "./lib/utils.js";

chrome.webRequest.onCompleted.addListener(
  async (details) => {
    const auth = await utils.checkSession(details);
    if (auth.is_connected && auth.tab && auth.tab.id) {
      await chrome.tabs.update(auth.tab.id, {
        active: true,
        url: chrome.runtime.getURL(co.CONNECTED_LOCAL_PAGE),
      });
    }
  },
  { urls: [co.CONNECTED_URL_PATTERN] }
);
