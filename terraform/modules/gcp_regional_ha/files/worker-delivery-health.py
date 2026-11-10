#!/usr/bin/env python3
"""Private readiness service behind nginx: MIG and per-application health."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import urllib.error
import urllib.request

SETTINGS = Path('/etc/autokom-ha/worker.json')
READY = Path('/run/autokom-ha/worker-ready')
DOCKER_SOCK = Path('/var/run/docker.sock')
ROUTES = Path('/etc/autokom-ha/routed.json')
UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z')


def prepared(ready=READY, docker_socket=DOCKER_SOCK) -> bool:
    return ready.is_file() and docker_socket.is_socket()


def settings(path=SETTINGS) -> tuple[int, str]:
    cfg = json.loads(path.read_text(encoding='utf-8'))
    port, path = cfg['health_port'], cfg['health_path']
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid health port')
    if not isinstance(path, str) or not path.startswith('/') or len(path) > 128:
        raise ValueError('Invalid health path')
    return port, path


def healthy_app(env: str, run_id: str, routes: Path = ROUTES) -> bool:
    if not UUID.fullmatch(env) or not UUID.fullmatch(run_id) or routes.is_symlink() or not routes.is_file():
        return False
    try:
        row = json.loads(routes.read_text(encoding='utf-8')).get(env)
        if not isinstance(row, dict) or row.get('run_id') != run_id:
            return False
        port = row['port']; health = row['healthcheck']; host = row['host']
        if type(port) is not int or not 21000 <= port <= 55000 or not isinstance(health, str) or not health.startswith('/') or len(health) > 120 or not isinstance(host, str):
            return False
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
        request = urllib.request.Request(f'http://127.0.0.1:{port}{health}', headers={'Host': host})
        try:
            with opener.open(request, timeout=3) as response:
                return 200 <= response.status < 400
        except urllib.error.HTTPError as exc:
            return 300 <= exc.code < 400
    except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError, TimeoutError):
        return False


def handler_factory(health_path: str, check=prepared, app_check=healthy_app):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if path == '/' or path == health_path:
                okay = check()
                body = b'ok\n' if okay else b'not-ready\n'
            elif path.startswith('/__autokom_health/'):
                segments = path.split('/')
                if len(segments) != 4 or not UUID.fullmatch(segments[2]) or not UUID.fullmatch(segments[3]):
                    self.send_error(404)
                    return
                okay = check() and app_check(segments[2], segments[3])
                body = b'ok\n' if okay else b'not-ready\n'
            else:
                self.send_error(404)
                return
            self.send_response(200 if okay else 503)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    return Handler


def main():
    port, health_path = settings()
    ThreadingHTTPServer(('127.0.0.1', port), handler_factory(health_path)).serve_forever(poll_interval=0.5)


if __name__ == '__main__':
    main()
