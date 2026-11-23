#!/usr/bin/env python3
"""
Web Campus - Attendance Application Backend Server
Lightweight, fast, zero-dependency Python HTTP Server.
Serves the modern frontend dashboard and REST APIs for GEMS attendance data.
"""

import sys
import os
import json
import time
import socket
import threading
import webbrowser
import urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from datetime import datetime, timezone

# Ensure local imports work
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import gems_client

STATIC_DIR = os.path.join(SCRIPT_DIR, "static")

# Simple in-memory session cache
SESSION_CACHE = {}


def get_local_ip():
    """Returns local LAN IP address (e.g. 192.168.x.x) for phone access."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


class WebCampusHandler(SimpleHTTPRequestHandler):
    """Custom HTTP handler for Web Campus."""

    extensions_map = SimpleHTTPRequestHandler.extensions_map.copy()
    extensions_map.update({
        '.manifest': 'application/manifest+json',
        '.webmanifest': 'application/manifest+json',
        '.json': 'application/json',
        '.svg': 'image/svg+xml',
        '.png': 'image/png',
        '.js': 'application/javascript',
        '.css': 'text/css'
    })

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def end_headers(self):
        if self.path.endswith('sw.js'):
            self.send_header('Service-Worker-Allowed', '/')
        self.send_header('Cache-Control', 'no-cache, must-revalidate')
        super().end_headers()

    def _set_headers(self, status=200, content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200, 'text/plain')
        self.wfile.write(b'OK')

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # API Endpoints
        if path == '/api/status':
            self._set_headers(200)
            res = {
                'status': 'online',
                'app': 'Web Campus',
                'description': 'Smart College Attendance & Timetable Intelligence System',
                'version': '1.0.0',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            self.wfile.write(json.dumps(res).encode('utf-8'))
            return

        elif path == '/api/network-info':
            local_ip = get_local_ip()
            port = self.server.server_address[1]
            tunnel_file = os.path.join(SCRIPT_DIR, "tunnel_url.txt")
            https_url = None
            if os.path.exists(tunnel_file):
                try:
                    with open(tunnel_file, "r") as f:
                        https_url = f.read().strip()
                except Exception:
                    pass

            mobile_url = https_url if https_url else f"http://{local_ip}:{port}"
            self._set_headers(200)
            res = {
                'success': True,
                'localIp': local_ip,
                'port': port,
                'localUrl': f"http://localhost:{port}",
                'mobileUrl': mobile_url,
                'httpsUrl': https_url
            }
            self.wfile.write(json.dumps(res).encode('utf-8'))
            return

        elif path == '/api/demo':
            target_str = query.get('target', ['75'])[0]
            try:
                target_pct = float(target_str)
            except ValueError:
                target_pct = 75.0

            data = gems_client.get_demo_student_data(target_pct=target_pct)
            self._set_headers(200)
            self.wfile.write(json.dumps(data).encode('utf-8'))
            return

        elif path.startswith('/api/session/'):
            session_id = path.split('/api/session/')[-1]
            if session_id in SESSION_CACHE:
                self._set_headers(200)
                self.wfile.write(json.dumps(SESSION_CACHE[session_id]).encode('utf-8'))
            else:
                self._set_headers(404)
                self.wfile.write(json.dumps({'error': 'Session expired or not found'}).encode('utf-8'))
            return

        # Fallback to serving static UI files
        if path == '/' or path == '':
            self.path = '/index.html'

        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Read JSON body
        content_len = int(self.headers.get('Content-Length', 0))
        body_bytes = self.rfile.read(content_len) if content_len > 0 else b'{}'
        try:
            body = json.loads(body_bytes.decode('utf-8')) if body_bytes else {}
        except Exception:
            body = {}

        if path == '/api/login':
            username = body.get('username', '').strip()
            password = body.get('password', '').strip()
            target_pct = float(body.get('target', 75.0))

            if not username or not password:
                self._set_headers(400)
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': 'Username and password are required.'
                }).encode('utf-8'))
                return

            client = gems_client.WebCampusGemsClient()
            data = client.scrape_full_data(username, password, target_pct=target_pct)

            if data and data.get('success'):
                # Cache session
                session_key = username.upper()
                SESSION_CACHE[session_key] = data

                self._set_headers(200)
                self.wfile.write(json.dumps({
                    'success': True,
                    'sessionId': session_key,
                    'data': data
                }).encode('utf-8'))
            else:
                self._set_headers(401)
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': 'Invalid credentials or GEMS server unavailable. Please check your login details.'
                }).encode('utf-8'))
            return

        elif path == '/api/recalculate':
            # Recalculate target margins for arbitrary attendance list
            attendance_list = body.get('attendance', [])
            target_pct = float(body.get('target', 75.0))

            updated = []
            tot_att_off = 0
            tot_cond_off = 0
            tot_att_all = 0
            tot_cond_all = 0

            for s in attendance_list:
                att = int(s.get('attended', 0))
                cond = int(s.get('conducted', 0))
                insights = gems_client.calculate_attendance_insights(att, cond, target_pct)

                item = dict(s)
                item['safeBunks'] = insights['safeBunks']
                item['classesNeeded'] = insights['classesNeeded']
                item['badgeColor'] = insights['badgeColor']
                item['message'] = insights['message']
                updated.append(item)

                is_tt = s.get('code', '').upper() in ['TECHNICALTRAINING', 'APTITUDE', 'SOFTSKILLS', 'TT']
                if not is_tt:
                    tot_att_off += att
                    tot_cond_off += cond
                tot_att_all += att
                tot_cond_all += cond

            off_subs = [s for s in updated if s.get('code', '').upper() not in ['TECHNICALTRAINING', 'APTITUDE', 'SOFTSKILLS', 'TT']]
            off_pct = round(sum(s['percentage'] for s in off_subs) / len(off_subs), 2) if off_subs else 0.0
            all_pct = round(sum(s['percentage'] for s in updated) / len(updated), 2) if updated else 0.0

            off_insights = gems_client.calculate_attendance_insights(tot_att_off, tot_cond_off, target_pct)
            all_insights = gems_client.calculate_attendance_insights(tot_att_all, tot_cond_all, target_pct)

            res = {
                'success': True,
                'targetPercentage': target_pct,
                'attendance': updated,
                'overallAttendance': {
                    'percentage': off_pct,
                    'attended': tot_att_off,
                    'conducted': tot_cond_off,
                    'safeBunks': off_insights['safeBunks'],
                    'classesNeeded': off_insights['classesNeeded'],
                    'badgeColor': off_insights['badgeColor'],
                    'label': 'Official Attendance (Academic Core)'
                },
                'overallWithTT': {
                    'percentage': all_pct,
                    'attended': tot_att_all,
                    'conducted': tot_cond_all,
                    'safeBunks': all_insights['safeBunks'],
                    'classesNeeded': all_insights['classesNeeded'],
                    'label': 'Overall Attendance (All Courses)'
                }
            }
            self._set_headers(200)
            self.wfile.write(json.dumps(res).encode('utf-8'))
            return

        elif path == '/api/simulate':
            # What-If simulator
            attendance_list = body.get('attendance', [])
            modifications = body.get('modifications', {})
            target_pct = float(body.get('target', 75.0))

            simulated = []
            tot_att_off = 0
            tot_cond_off = 0

            for s in attendance_list:
                code = s.get('code', '')
                base_att = int(s.get('attended', 0))
                base_cond = int(s.get('conducted', 0))

                mod = modifications.get(code, {})
                add_attend = int(mod.get('attend', 0))
                add_bunk = int(mod.get('bunk', 0))

                new_att = base_att + add_attend
                new_cond = base_cond + add_attend + add_bunk

                insights = gems_client.calculate_attendance_insights(new_att, new_cond, target_pct)

                item = dict(s)
                item['attended'] = new_att
                item['conducted'] = new_cond
                item['percentage'] = insights['percentage']
                item['safeBunks'] = insights['safeBunks']
                item['classesNeeded'] = insights['classesNeeded']
                item['badgeColor'] = insights['badgeColor']
                item['message'] = insights['message']
                item['deltaPct'] = round(insights['percentage'] - s.get('percentage', 0), 2)
                simulated.append(item)

                is_tt = code.upper() in ['TECHNICALTRAINING', 'APTITUDE', 'SOFTSKILLS', 'TT']
                if not is_tt:
                    tot_att_off += new_att
                    tot_cond_off += new_cond

            off_subs = [s for s in simulated if s.get('code', '').upper() not in ['TECHNICALTRAINING', 'APTITUDE', 'SOFTSKILLS', 'TT']]
            off_pct = round(sum(s['percentage'] for s in off_subs) / len(off_subs), 2) if off_subs else 0.0

            res = {
                'success': True,
                'simulatedAttendance': simulated,
                'overallOfficialPct': off_pct,
                'totalAttended': tot_att_off,
                'totalConducted': tot_cond_off
            }
            self._set_headers(200)
            self.wfile.write(json.dumps(res).encode('utf-8'))
            return

        self._set_headers(404)
        self.wfile.write(json.dumps({'error': 'Endpoint not found'}).encode('utf-8'))


def run_server(preferred_port=8080):
    """
    Starts the HTTP server with resilient socket binding for Laptop and Mobile Wi-Fi.
    Binds to 0.0.0.0 so phones on the same Wi-Fi network can connect to Web Campus.
    Automatically cycles through alternate ports if 8080 is blocked by Windows Hyper-V.
    """
    candidate_ports = [preferred_port, 5000, 3000, 8000, 8888, 5050, 8085, 8090]
    candidate_ports = list(dict.fromkeys(candidate_ports))

    # Try 0.0.0.0 first to allow phone connections over Wi-Fi, then fallback to 127.0.0.1
    hosts_to_try = ['0.0.0.0', '127.0.0.1', 'localhost']

    httpd = None
    bound_host = None
    bound_port = None

    for host in hosts_to_try:
        for p in candidate_ports:
            try:
                server_address = (host, p)
                ThreadingHTTPServer.allow_reuse_address = True
                httpd = ThreadingHTTPServer(server_address, WebCampusHandler)
                bound_host = host
                bound_port = p
                break
            except (PermissionError, OSError):
                continue
        if httpd:
            break

    # If predefined ports failed, let the OS dynamically assign an open port
    if not httpd:
        for host in ('0.0.0.0', '127.0.0.1'):
            try:
                ThreadingHTTPServer.allow_reuse_address = True
                httpd = ThreadingHTTPServer((host, 0), WebCampusHandler)
                bound_host = host
                bound_port = httpd.server_address[1]
                break
            except Exception:
                continue

    if not httpd:
        print("❌ Failed to bind to any available network port.")
        sys.exit(1)

    local_ip = get_local_ip()
    local_url = f"http://localhost:{bound_port}"
    mobile_url = f"http://{local_ip}:{bound_port}"

    print("=" * 70)
    print("🎓 MITS CAMPUS WEB - ATTENDANCE APPLICATION SERVER")
    print("=" * 70)
    print(f"💻 Laptop (Localhost): {local_url}")
    if bound_host == '0.0.0.0' and local_ip != '127.0.0.1':
        print(f"📱 Phone (Same Wi-Fi): {mobile_url}")
        print("   👉 Connect your phone to same Wi-Fi & open the link above!")
    print(f"📁 Serving static files from: {STATIC_DIR}")
    print(f"✨ Instant Demo Mode: {local_url}/api/demo")
    print("=" * 70)
    print(f"\n👉 Access MITS CAMPUS WEB at: {local_url}")
    print("   (Press Ctrl+C in terminal to stop server)\n")

    # Automatically open default browser after brief startup pause
    def _auto_open():
        time.sleep(0.8)
        try:
            webbrowser.open(local_url)
        except Exception:
            pass

    threading.Thread(target=_auto_open, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping MITS CAMPUS WEB server...")
        httpd.server_close()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    run_server(port)
