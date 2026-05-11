"""
Shamrock Bond Tracker — Traccar API Proxy
==========================================
Add this to your existing Flask dashboard (dashboard/app.py) to proxy
Traccar API calls and avoid CORS issues when the dashboard is served
from a different origin than Traccar (port 5050 vs 8082).

Usage:
  from traccar_proxy import register_traccar_proxy
  register_traccar_proxy(app)

Or run standalone:
  python traccar_proxy.py
"""

import os
import requests
from flask import Flask, request, Response, jsonify, send_from_directory

TRACCAR_URL   = os.environ.get('TRACCAR_URL',   'http://localhost:8082')
TRACCAR_TOKEN = os.environ.get('TRACCAR_TOKEN', '')

# ── Proxy blueprint ────────────────────────────────────────────────────────

def register_traccar_proxy(app):
    """Register Traccar proxy routes on an existing Flask app."""

    @app.route('/traccar/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])
    def traccar_proxy(path):
        """Proxy all /traccar/* requests to Traccar API."""
        # Handle CORS preflight
        if request.method == 'OPTIONS':
            resp = Response('', status=204)
            resp.headers['Access-Control-Allow-Origin'] = '*'
            resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
            resp.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
            return resp
        target = f"{TRACCAR_URL}/api/{path}"
        headers = {
            'Content-Type': 'application/json',
        }
        if TRACCAR_TOKEN:
            headers['Authorization'] = f'Bearer {TRACCAR_TOKEN}'

        # Forward query params
        params = dict(request.args)

        try:
            resp = requests.request(
                method  = request.method,
                url     = target,
                headers = headers,
                params  = params,
                json    = request.get_json(silent=True),
                timeout = 10,
            )
            # Pass through response
            excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
            response_headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded_headers}
            response_headers['Access-Control-Allow-Origin'] = '*'
            return Response(resp.content, status=resp.status_code, headers=response_headers)

        except requests.exceptions.ConnectionError:
            return jsonify({'error': 'Cannot connect to Traccar at ' + TRACCAR_URL}), 503
        except requests.exceptions.Timeout:
            return jsonify({'error': 'Traccar request timed out'}), 504
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/traccar-ui')
    def traccar_ui():
        """Serve the Bond Tracker dashboard."""
        return send_from_directory(
            os.path.join(os.path.dirname(__file__)),
            'bond-tracker.html'
        )

    @app.route('/traccar/health', methods=['GET', 'OPTIONS'])
    def traccar_health():
        """Quick health check for Traccar connectivity."""
        try:
            r = requests.get(f"{TRACCAR_URL}/api/server", timeout=5,
                             headers={'Authorization': f'Bearer {TRACCAR_TOKEN}'} if TRACCAR_TOKEN else {})
            data = r.json()
            return jsonify({
                'connected': True,
                'version':   data.get('version'),
                'newServer': data.get('newServer'),
            })
        except Exception as e:
            return jsonify({'connected': False, 'error': str(e)}), 503


# ── Standalone server (for testing) ───────────────────────────────────────

if __name__ == '__main__':
    app = Flask(__name__, static_folder='.')

    @app.route('/')
    def index():
        return send_from_directory('.', 'bond-tracker.html')

    register_traccar_proxy(app)

    print("🍀 Shamrock Bond Tracker proxy running on http://0.0.0.0:5051")
    print(f"   Proxying to Traccar at: {TRACCAR_URL}")
    print(f"   Open: http://YOUR_VPS_IP:5051")
    app.run(host='0.0.0.0', port=5051, debug=False)
