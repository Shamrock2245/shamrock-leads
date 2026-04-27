#!/usr/bin/env python3
"""Transfer all dashboard files from base64-encoded chunks."""
import base64, os, glob

b64_dir = "/opt/shamrock-leads/_b64_staging"
target_map = {
    "sl_data": "dashboard/sl-data.js",
    "sl_features": "dashboard/sl-features.js",
    "sl_app": "dashboard/app.py",
    "sl_env": ".env.example",
}

os.chdir("/opt/shamrock-leads")

for key, target in target_map.items():
    b64_file = os.path.join(b64_dir, f"{key}.b64")
    if os.path.exists(b64_file):
        with open(b64_file, 'r') as f:
            b64_data = f.read().strip()
        raw = base64.b64decode(b64_data)
        with open(target, 'wb') as f:
            f.write(raw)
        print(f"OK: {len(raw):>6} bytes -> {target}")
    else:
        print(f"SKIP: {b64_file} not found")

print("\nDone. Cleaning up staging dir...")
import shutil
shutil.rmtree(b64_dir, ignore_errors=True)
