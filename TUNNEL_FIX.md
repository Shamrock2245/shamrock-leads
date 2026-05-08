# BlueBubbles Tunnel Fix - May 8, 2026

## Current Status: ONLINE

- Active Tunnel: ngrok permanent domain
- URL: https://pseudospherical-etta-untactually.ngrok-free.dev
- Forwards to: http://localhost:1234 (BlueBubbles on the iMac)
- BlueBubbles version: 1.9.9
- Private API: enabled
- iMessage account: shamrockbailoffice@gmail.com

---

## What Happened (May 8, 2026)

The ngrok tunnel was running but forwarding to port 1880 (Node-RED) instead of
port 1234 (BlueBubbles). This caused the dashboard iMessage tab to show Offline.

### Root Cause
The ngrok agent was started with a config pointing to port 1880. The stale session
was locked in ngrok's cloud, preventing a new session from starting. The session was
stopped via the ngrok dashboard (dashboard.ngrok.com/agents), then restarted.

### Fix Applied
1. Stopped the stale ngrok agent session via the ngrok web dashboard
2. On the iMac Terminal, ran:
     ngrok http 1234 --url=pseudospherical-etta-untactually.ngrok-free.dev
3. Hot-swapped the URL in the VPS dashboard via /api/bb-health/update-url

---

## How to Restart the Tunnel (if it goes down)

On the iMac Terminal, run:
  ngrok http 1234 --url=pseudospherical-etta-untactually.ngrok-free.dev

Keep this terminal window open. If you close it, the tunnel stops.

### To run as a persistent background service (survives reboots):

1. Create ~/.config/ngrok/ngrok.yml with:

   version: "3"
   agent:
     authtoken: 3AlFcEt1wP01NPVBuP9po8vE1y8_56b2KnU8Ydzyvi1CpktSY
   tunnels:
     bluebubbles:
       proto: http
       addr: 1234
       url: pseudospherical-etta-untactually.ngrok-free.dev

2. Install and start as a macOS service:
   ngrok service install --config ~/.config/ngrok/ngrok.yml
   ngrok service start

---

## Cloudflare Tunnel (Future Permanent Branded URL)

A Cloudflare Tunnel named "bluebubbles" is configured and HEALTHY with 4 active
connections from Miami.

- Tunnel ID: bd9101bf-39a5-4b7a-97a8-d024c973c769
- Intended URL: https://bb.shamrockbailbonds.biz
- Ingress rule: bb.shamrockbailbonds.biz -> http://localhost:1234

### Why it is not active yet
The Cloudflare zone for shamrockbailbonds.biz is PENDING. The domain nameservers
still point to Wix (ns6.wixdns.net / ns7.wixdns.net). Cloudflare requires nameserver
delegation to activate the zone and route tunnel traffic.

### To activate the Cloudflare tunnel permanently
At your domain registrar, change nameservers to:
  rita.ns.cloudflare.com
  thaddeus.ns.cloudflare.com

Once the zone activates (~24h after NS change), https://bb.shamrockbailbonds.biz
will work automatically. Update BLUEBUBBLES_URL_0178 in the VPS .env to that URL.

---

## VPS .env Variable
BLUEBUBBLES_URL_0178=https://pseudospherical-etta-untactually.ngrok-free.dev

## Hot-Swap Command (no restart needed)
curl -X PATCH http://leads.shamrockbailbonds.biz/api/bb-health/update-url \
  -H "Content-Type: application/json" \
  -d '{"suffix":"0178","url":"NEW_URL","api_key":"shamrock-bb-sync-2245"}'
