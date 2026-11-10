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
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autokom_ha_preflight as pf
import autokom_ha_extra as extra


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
                # Per-worker metrics are best-effort; aggregate MIG and LB data
                # remain available if individual Cloud Monitoring calls fail.
                try:
                    extra.enrich(report, cpu_and_network=True)
                    detailed = extra.to_prometheus(report)
                except Exception:
                    detailed = ''
                cloud_metrics = ''
                path = Path('/run/autokom-ha-app-metrics.prom')
                try:
                    if (path.is_file() and not path.is_symlink() and
                            time.time() - path.stat().st_mtime < 240 and
                            path.stat().st_size < 65536):
                        cloud_metrics = path.read_text(encoding='ascii')
                except (OSError, ValueError, UnicodeError):
                    cloud_metrics = ''
                fresh = 1 if cloud_metrics else 0
                self.data = (pf.to_prometheus(report) + detailed + cloud_metrics +
                             f'autokom_ha_app_metrics_fresh {fresh}\n' +
                             'autokom_ha_metrics_scrape_success 1\n')
            except Exception as exc:
                # Journaliser seulement le type et le code HTTP, jamais un jeton
                # ou le contenu d'une reponse GCP potentiellement sensible.
                import urllib.error
                code = exc.code if isinstance(exc, urllib.error.HTTPError) else None
                safe = f" HTTP {code}" if code is not None else ""
                print(f"AUTOKOM HA scrape failed: {type(exc).__name__}{safe}", file=sys.stderr, flush=True)
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
