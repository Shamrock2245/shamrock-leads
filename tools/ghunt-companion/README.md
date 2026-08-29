# GHunt Companion — unpacked (Chrome)

Chrome Web Store listing is gone. This is an MV3 unpack of [mxrch/ghunt_companion](https://github.com/mxrch/ghunt_companion) for ShamrockLeads. It still emits the Method 2 `{cookies, oauth_token}` blob that `POST /api/osint/ghunt/login` accepts.

## Load in Chrome

1. Open `chrome://extensions`
2. Turn on **Developer mode** (top right)
3. **Load unpacked**
4. Select this folder:
   `shamrock-leads/tools/ghunt-companion`
5. Pin the extension.

## Capture a session

1. Sign into a **dedicated research Google account** in Chrome.
2. Click the Companion icon → **Synchronize to GHunt**.
3. Finish Google sign-in if asked.
4. On **Connected to Google**, click **Copy blob** (Method 2).
5. ShamrockLeads → OSINT → Engines → paste → **Save GHunt session**.

Do not paste the blob into Slack. Method 1 (port 60067) does not work with the Docker worker.

Upstream credits: see `credits.txt`. Cosmetic login-page rewrite from the original MV2 extension is omitted (Chrome MV3 cannot intercept Google HTML).
