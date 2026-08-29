export function encodeCookies(cookies) {
  const copy = { ...cookies };
  const oauth_token = copy.oauth_token;
  delete copy.oauth_token;
  return btoa(JSON.stringify({ cookies: copy, oauth_token }));
}

export async function checkSession(details) {
  if (!details || details.tabId < 0) {
    return { is_connected: false };
  }
  let target_tab;
  try {
    target_tab = await chrome.tabs.get(details.tabId);
  } catch (e) {
    return { is_connected: false };
  }
  if (!target_tab) {
    return { is_connected: false };
  }

  const raw = await chrome.cookies.getAll({
    domain: "google.com",
    storeId: details.cookieStoreId,
  });
  const wanted = [
    "SID", "SSID", "APISID", "SAPISID", "HSID", "LSID",
    "__Secure-3PSID", "oauth_token",
  ];
  const ghunt_cookies = Object.fromEntries(
    raw.filter((c) => wanted.includes(c.name)).map((c) => [c.name, c.value])
  );
  if (Object.keys(ghunt_cookies).length >= wanted.length) {
    await chrome.storage.local.set({
      ghunt: { cookies: ghunt_cookies, ready: true },
    });
    return { tab: target_tab, is_connected: true, ghunt_cookies };
  }
  return { is_connected: false };
}
