"""
Shared service modules used by workers and the dashboard.

Keep heavy optional deps (OpenCV, fast-alpr) imported lazily so the dashboard
image can import lightweight helpers without GPU/vision packages.
"""
