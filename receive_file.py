#!/usr/bin/env python3
"""Receive base64-encoded file content and write to target path."""
import sys, base64

if len(sys.argv) != 2:
    print("Usage: python3 receive_file.py <target_path>")
    sys.exit(1)

target = sys.argv[1]
b64_data = sys.stdin.read().strip()
raw_data = base64.b64decode(b64_data)

with open(target, 'wb') as f:
    f.write(raw_data)

print(f"OK: {len(raw_data)} bytes -> {target}")
