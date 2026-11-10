#!/usr/bin/env python3
"""Loopback-only Prometheus exposition of GCP HA readiness and LB metrics.

Requires `gcloud` installed, active workload identity with Compute read and
Monitoring read. For use behind a private local Prometheus scrape target only.
"""
from __future__ import annotations

import argparse
import http.server
import threading
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autokom_ha_preflight as pf


class MetricCache:
    def __init__(self, args, refresh_seconds=60):
        self.args = args
        self.refresh_seconds = refresh_seconds
        self.lock = threading.Lock()
        self.until = 0.0
        self.data = ''

    def get(self):
        with self.lock:
            if time.monotonic() < self.until and self.data:
                return self.data
            try:
                report = pf.inventory(self.args.project, self.args.region, self.args.mig,
                                      self.args.backend, self.args.url_map, self.args.forwarding_rule, True)
                self.data = pf.to_prometheus(report) + 'autokom_ha_metrics_scrape_success 1\n'
            except Exception:
                # Never display access tokens, connection strings, or secret-bearing exceptions.
                self.data = 'autokom_ha_metrics_scrape_success 0\n'
            self.until = time.monotonic() + self.refresh_seconds
            return self.data


def main() -> None:
    parser = argparse.ArgumentParser(description='Exporter Prometheus AUTOKOM GCP HA (127.0.0.1 only)')
    parser.add_argument('--port', type=int, default=19191)
    parser.add_argument('--project', default=pf.DEFAULT_PROJECT)
    parser.add_argument('--region', default=pf.DEFAULT_REGION)
    parser.add_argument('--mig', default=pf.DEFAULT_MIG)
    parser.add_argument('--backend', default=pf.DEFAULT_LB_BACKEND)
    parser.add_argument('--url-map', default=pf.DEFAULT_LB_MAP)
    parser.add_argument('--forwarding-rule', default=pf.DEFAULT_FORWARD_RULE)
    args = parser.parse_args()
    if args.port < 1024 or args.port > 65535:
        parser.error('Port TCP non privilegie requis.')
    cache = MetricCache(args)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/metrics':
                self.send_error(404)
                return
            body = cache.get().encode('ascii', 'replace')
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'AUTOKOM HA metrics exporter sur http://127.0.0.1:{args.port}/metrics', flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        server.server_close()

if __name__ == '__main__':
    main()
