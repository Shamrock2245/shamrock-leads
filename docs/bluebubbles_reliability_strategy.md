# BlueBubbles Reliability & Tunnel Architecture Strategy

## The Problem
BlueBubbles (BB) on the office M1 iMac currently experiences periodic crashes and disconnects. The legacy documentation references `ngrok`, but the live system actually uses a **Cloudflare Named Tunnel** (`bb.shamrockbailbonds.biz`). The crash issue is a known Node/V8 memory leak bug in BB Server versions prior to v1.9.9 when running on Apple Silicon (M-series Macs).

## The Solution

To achieve 24/7 reliability for human and AI agents, we must implement a multi-layered resilience strategy directly on the M1 iMac and the Hetzner VPS.

### 1. Address the Root Cause (M1 Memory Leak)
The BlueBubbles server must be upgraded to **v1.9.9**, which explicitly fixes the M1 crash bug [1]. 
*   **Action:** The office iMac needs to download and install the latest `v1.9.9` DMG.
*   **Action:** The iMac must have the `sudo nvram boot-args=-arm64e_preview_abi` boot argument applied (requires SIP disabled) to prevent the underlying macOS bug from triggering the proxychains crash [1].

### 2. Definitive Tunnel Architecture: Cloudflare
We will **abandon ngrok** completely. Ngrok free tier has connection limits and rotating URLs unless paid. The existing Cloudflare Named Tunnel (`bd9101bf-39a5-4b7a-97a8-d024c973c769`) is the superior, permanent solution.
*   It is already configured to route `bb.shamrockbailbonds.biz` to `localhost:1234` on the iMac.
*   The CNAME record is correctly placed in Wix DNS.
*   **Action:** Ensure `cloudflared` runs as a macOS `LaunchDaemon` (system-level, runs before login) rather than a `LaunchAgent` (user-level, requires login) to ensure the tunnel survives reboots automatically.

### 3. Application Persistence (Watchdog)
Even with v1.9.9, we cannot assume the Electron app will never crash. We need an aggressive watchdog.
*   **Action:** Create a custom macOS `LaunchAgent` (`com.shamrock.bb-watchdog.plist`) that runs a lightweight bash script every 5 minutes.
*   The script will ping `http://localhost:1234/api/v1/ping`. If it fails 3 times, it will forcefully `killall "BlueBubbles"` and relaunch the application using `open -a "BlueBubbles"`.

### 4. VPS-Side Health Monitoring
The FastAPI dashboard on the Hetzner VPS already has a `bb_health_monitor.py` module.
*   **Action:** Update the dashboard to aggressively cache failed requests and queue outbound iMessages if the BB server is temporarily down (e.g., during a watchdog restart), preventing data loss.

## Implementation Steps (Next Phases)

1.  **Trape VPS Deployment:** Write the `setup_trape.sh` script and Nginx config for the VPS.
2.  **Maigret/Blackbird Docker Integration:** Modify the `Dockerfile` to include the necessary Python packages and system dependencies for the OSINT tools.
3.  **BB Reliability Scripts:** Generate the `bb_watchdog.sh` and `com.shamrock.bb-watchdog.plist` files to be deployed to the iMac.

---
**References:**
[1] BlueBubbles Server v1.9.9 Release Notes. GitHub. https://github.com/BlueBubblesApp/bluebubbles-server/releases
