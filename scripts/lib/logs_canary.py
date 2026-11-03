#!/usr/bin/env python3
"""AUTOKOM Loki E2E canary and private Prometheus exporter (Python stdlib).

A probe emits a unique journald record and searches for that exact record in
Loki. The exporter serves only a small locally persisted result, never the log
contents, from 127.0.0.1. Timer and HTTP server have separate systemd units.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = Path('/var/lib/autokom/logs-monitor/canary.json')
LOKI_URL = 'http://127.0.0.1:3100'

def loki_api(path, params=None, timeout=5):
    query = urllib.parse.urlencode(params or {})
    url = LOKI_URL + path + ('?' + query if query else '')
    request = urllib.request.Request(url, headers={'User-Agent':'AUTOKOM-Canary/1'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        result = json.load(response)
    if result.get('status') != 'success':
        raise RuntimeError('Loki API response is not successful')
    return result

def emit(token):
    p = subprocess.run(['logger','-t','autokom-logs-canary',token],
                       capture_output=True,text=True,timeout=8)
    if p.returncode:
        raise RuntimeError('logger command failed')

def seen(token, started):
    query = '{platform="autokom",source="systemd"} |= '+json.dumps(token)
    result = loki_api('/loki/api/v1/query_range',{
        'query':query,'start':str((started-30)*1_000_000_000),
        'end':str((int(time.time())+5)*1_000_000_000), 'limit':'50'})
    return any(token in line for item in result.get('data',{}).get('result',[])
               for _,line in item.get('values',[]))

def atomic_result(payload, path=STATE):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
    fd, temp = tempfile.mkstemp(prefix='.canary-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(payload,f)
            f.write('\n')
        os.chmod(temp,0o644)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp): os.unlink(temp)

def probe(wait=45):
    started=int(time.time())
    token='AUTOKOM_CANARY_'+secrets.token_hex(12)
    ok=False
    detail='timeout'
    t0=time.monotonic()
    try:
        emit(token)
        from tool_banner import Activity
        with Activity('Vérification de l’ingestion journald → Alloy → Loki'):
            while time.monotonic()-t0 < wait:
                try:
                    if seen(token,started):
                        ok=True
                        detail='verified'
                        break
                    detail='not_ingested_yet'
                except Exception as exc:
                    detail=type(exc).__name__
                time.sleep(3)
    except Exception as exc:
        detail=type(exc).__name__
    result={'schema':1,'success':int(ok),'timestamp':int(time.time()),
            'duration_seconds':round(time.monotonic()-t0,3),'state':detail}
    atomic_result(result)
    print('AUTOKOM Loki canary:', 'PASS' if ok else 'FAIL', '('+detail+')')
    return 0 if ok else 1

def metrics(path=STATE):
    try:
        record=json.loads(path.read_text())
        if record.get('schema')!=1:raise ValueError('unsupported schema')
        success=int(record['success']); ts=int(record['timestamp']); dur=float(record['duration_seconds'])
    except (OSError,ValueError,TypeError,KeyError):
        success=0;ts=0;dur=0.0
    return ('# HELP autokom_logs_canary_success Most recent journal-to-Loki canary result (1 success).\n'
            '# TYPE autokom_logs_canary_success gauge\n'
            f'autokom_logs_canary_success {success}\n'
            '# HELP autokom_logs_canary_last_run_timestamp_seconds Unix time of last probe.\n'
            '# TYPE autokom_logs_canary_last_run_timestamp_seconds gauge\n'
            f'autokom_logs_canary_last_run_timestamp_seconds {ts}\n'
            '# HELP autokom_logs_canary_duration_seconds Last probe walltime.\n'
            '# TYPE autokom_logs_canary_duration_seconds gauge\n'
            f'autokom_logs_canary_duration_seconds {dur}\n')

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/metrics','/healthz'):
            self.send_error(404)
            return
        data=(metrics() if self.path=='/metrics' else 'ok\n').encode()
        self.send_response(200)
        self.send_header('Content-Type','text/plain; version=0.0.4; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self,*args):pass

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('probe','serve','metrics'))
    parser.add_argument('--wait',type=int,default=45)
    args=parser.parse_args(argv)
    if args.action=='probe':return probe(max(5,min(args.wait,120)))
    if args.action=='metrics':print(metrics(),end='');return 0
    ThreadingHTTPServer(('127.0.0.1',19109),Handler).serve_forever()
    return 0

if __name__=='__main__':raise SystemExit(main())
