#!/usr/bin/env python3
"""Non privileged, strictly technical health endpoint for an AUTOKOM GCP worker.

This endpoint does NOT make customer applications available and is not an
application health check. It is only the MIG infrastructure health check.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path

SETTINGS = Path('/etc/autokom-ha/worker.json')
READY = Path('/run/autokom-ha/worker-ready')
DOCKER_SOCK = Path('/var/run/docker.sock')


def prepared(ready=READY, docker_socket=DOCKER_SOCK) -> bool:
    return ready.is_file() and docker_socket.is_socket()


def settings(path=SETTINGS) -> tuple[int, str]:
    cfg = json.loads(path.read_text(encoding='utf-8'))
    port, path = cfg['health_port'], cfg['health_path']
    if not isinstance(port, int) or not (1024 <= port <= 65535):
        raise ValueError('Invalid health port')
    if not isinstance(path, str) or not path.startswith('/') or len(path) > 128:
        raise ValueError('Invalid health path')
    return port, path


def handler_factory(health_path: str, check=prepared):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if path != '/' and path != health_path:
                self.send_error(404)
                return
            is_ready = check()
            if path == '/':
                body = b'AUTOKOM HA worker preparation only - no customer app deployed\n'
                code = 200 if is_ready else 503
            else:
                body = b'ok\n' if is_ready else b'not-ready\n'
                code = 200 if is_ready else 503
            self.send_response(code)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    return Handler


def main() -> None:
    port, health_path = settings()
    server = ThreadingHTTPServer(('0.0.0.0', port), handler_factory(health_path))
    server.serve_forever(poll_interval=0.5)


if __name__ == '__main__':
    main()
