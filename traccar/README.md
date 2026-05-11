# 🍀 Shamrock Bond Tracker — Traccar Integration

GPS-based defendant location tracking for Shamrock Bail Bonds, powered by [Traccar](https://www.traccar.org/) v6.13.3.

---

## Quick Start

### Step 1 — Create the Admin Account

Run this **on your VPS** (not locally):

```bash
cd /opt/shamrock-leads
bash traccar/setup_admin.sh
```

**Credentials created:**

| Field    | Value                         |
|----------|-------------------------------|
| Email    | `admin@shamrockbailbonds.biz` |
| Password | `Shamrock@Traccar2026!`       |
| Web UI   | `http://YOUR_VPS_IP:8082`     |

> **Save the API token** printed at the end — you'll need it for the dashboard.

---

### Step 2 — Open the Bond Tracker Dashboard

The dashboard is a single HTML file: `traccar/bond-tracker.html`

**Option A — Serve via your existing Flask dashboard:**

Add to `dashboard/app.py`:
```python
from traccar.traccar_proxy import register_traccar_proxy
register_traccar_proxy(app)
```

Then access at: `http://YOUR_VPS_IP:5050/traccar-ui`

**Option B — Run standalone:**
```bash
TRACCAR_URL=http://localhost:8082 \
TRACCAR_TOKEN=your_api_token \
python traccar/traccar_proxy.py
```

Then access at: `http://YOUR_VPS_IP:5051`

**Option C — Open directly in browser:**

Open `bond-tracker.html` in any browser, then go to **Settings** (top right) and enter:
- Traccar URL: `http://YOUR_VPS_IP:8082`
- API Token: (from setup script)

---

### Step 3 — Install Traccar Client on Defendant's Phone

1. Have the defendant install **Traccar Client** (free):
   - Android: [Google Play](https://play.google.com/store/apps/details?id=org.traccar.client)
   - iOS: [App Store](https://apps.apple.com/app/traccar-client/id843156974)

2. In the app, set:
   - **Server URL**: `http://YOUR_VPS_IP:5055`
   - **Device Identifier**: Use the defendant's booking number (e.g., `BK-2026-001`)
   - **Frequency**: 60 seconds

3. In the Bond Tracker dashboard:
   - Go to **Devices** tab → **Register Device**
   - Enter the same Device Identifier (booking number)
   - Go to **Defendants** tab → **Add Defendant**
   - Assign the device to the defendant

---

### Step 4 — Add a GPS Hardware Tracker (Optional)

For defendants without smartphones, use a hardware GPS tracker:

| Tracker Model | Protocol | Port |
|---------------|----------|------|
| Concox GT06   | GT06     | 5013 |
| Teltonika FMB | Teltonika| 5023 |
| Queclink GL300| GL200    | 5093 |
| Any OsmAnd    | OsmAnd   | 5055 |

Configure the tracker to send data to `YOUR_VPS_IP:PORT` using the appropriate protocol.

---

## Dashboard Features

| Tab | Description |
|-----|-------------|
| **Live Map** | Real-time map of all defendants with GPS devices. Color-coded: green=live, yellow=stale, red=breach, gray=offline |
| **Defendants** | Card view of all defendants on bond with location status, bond amount, court date |
| **Location History** | Query historical movement for any defendant over any date range, with route visualization |
| **Geofences** | View and manage geofence zones; breach alerts fire automatically |
| **Alerts** | Log of all geofence entries/exits, device online/offline events |
| **Devices** | Manage all registered GPS devices and their assignments |

---

## Geofencing

Geofences let you define zones where defendants must stay (or must not enter).

**Common use cases:**
- Home address radius (must stay within 5 miles)
- State border (must not cross FL state line)
- Victim address (must not approach)
- Court location (must appear for hearings)

**To create a geofence:**
1. Open `http://YOUR_VPS_IP:8082` (Traccar web UI)
2. Go to **Geofences** → draw a circle or polygon on the map
3. Assign the geofence to a device under **Permissions**
4. The Bond Tracker dashboard will show breach events automatically

---

## Architecture

```
Defendant Phone/GPS Tracker
         │
         │ GPS data (OsmAnd/GT06/Teltonika protocol)
         ▼
   Traccar Server :8082
         │
         │ REST API / WebSocket
         ▼
   Bond Tracker Dashboard
   (bond-tracker.html + traccar_proxy.py)
         │
         │ Served via Flask
         ▼
   Shamrock Dashboard :5050
```

---

## Environment Variables

Add to your `.env` file:

```env
TRACCAR_URL=http://localhost:8082
TRACCAR_TOKEN=your_api_token_here
```

---

## Security Notes

- The Traccar web UI on port 8082 should be **firewalled** from public access
- Only expose port 8082 to your VPS's internal network or via VPN
- GPS tracker ports (5013, 5023, 5055) must be publicly accessible for devices to report
- The Bond Tracker dashboard (5051) should be behind your existing auth

**Recommended nginx config:**
```nginx
# Block public access to Traccar UI
location /traccar-admin {
    deny all;
}

# Allow Bond Tracker dashboard (protected by your auth)
location /traccar-ui {
    proxy_pass http://localhost:5051;
}
```

---

## Traccar Client App Setup (Screenshot Guide)

```
App Settings:
  Device identifier: [booking number, e.g. BK-2026-001]
  Server URL:        http://YOUR_VPS_IP:5055
  Frequency:         60 (seconds)
  Distance:          100 (meters)
  Angle:             90 (degrees)
  Status:            [toggle ON]
```

---

*Shamrock Bail Bonds · Fort Myers, FL · shamrockbailbonds.biz*
