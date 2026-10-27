#!/usr/bin/env python3
"""AUTOKOM: audit AWS en lecture seule (sans ports ouverts ni secrets)."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
from observability_config import load, ConfigError
from tool_banner import section


class Report:
    def __init__(self, strict=False):
        self.strict = strict
        self.rows = []

    def add(self, state, name, detail=''):
        assert state in ('OK', 'WARN', 'FAIL')
        self.rows.append((state, name, detail))
        symbol = {'OK':'✓', 'WARN':'!', 'FAIL':'✗'}[state]
        if sys.stdout.isatty() and os.environ.get('NO_COLOR') is None:
            symbol = f"\033[{ {'OK':32, 'WARN':33, 'FAIL':31}[state] }m{symbol}\033[0m"
        print(f'  {symbol} {name}' + (f' — {detail}' if detail else ''), flush=True)

    def missing(self, name, detail='absent ou non actif'):
        self.add('FAIL' if self.strict else 'WARN', name, detail)

    def summary(self):
        counts = {s:sum(x[0]==s for x in self.rows) for s in ('OK', 'WARN', 'FAIL')}
        print('\n'+'─'*56)
        print(f"  Résultat : {counts['OK']} OK · {counts['WARN']} avertissements · {counts['FAIL']} échecs")
        return int(counts['FAIL'] > 0)


def command(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=7, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def unit_active(unit):
    p = command(['systemctl','is-active','--quiet',unit])
    return p is not None and p.returncode == 0


def port_addresses(port):
    p = command(['ss','-H','-lnt'])
    if not p or p.returncode:
        return None
    matches = []
    for line in p.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[3].rsplit(':',1)[-1] == str(port):
            matches.append(fields[3])
    return matches


def is_loopback(endpoint):
    return endpoint.rsplit(':',1)[0].strip('[]') in ('127.0.0.1','::1')


def check_bind(report, label, port):
    addresses = port_addresses(port)
    if addresses is None:
        report.add('WARN', label, 'ss indisponible')
    elif not addresses:
        report.missing(label, f'port {port} non écouté')
    elif not all(map(is_loopback, addresses)):
        report.add('FAIL', label, f'port {port} exposé : {", ".join(addresses)}')
    else:
        report.add('OK', label, f'port {port} privé')


def local_http(port, path):
    """Interdit les proxies et redirections, requêtes uniquement loopback."""
    if not path.startswith('/') or '\\' in path or '://' in path:
        raise ValueError('Chemin local invalide')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{port}{path}',
                                     headers={'User-Agent':'AUTOKOM-Audit/1'})
        with opener.open(req, timeout=4) as r:
            return r.status, r.read(512_000).decode('utf-8','replace')
    except urllib.error.HTTPError as e:
        return e.code, ''
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, ''


def foundation(r):
    print('\n01 / 03 — Fondation')
    for binary in ('nginx','certbot','docker','ansible-playbook','trivy','semgrep','nikto'):
        (r.add('OK',binary,'présent') if shutil.which(binary) else r.missing(binary))
    p = command(['nginx','-t'])
    r.add('OK' if p and p.returncode==0 else 'FAIL','Nginx','syntaxe valide' if p and p.returncode==0 else 'nginx -t échoue')
    p = command(['ufw','status'])
    r.add('OK' if p and 'Status: active' in p.stdout else 'WARN',
          'UFW','actif' if p and 'Status: active' in p.stdout else 'inactif / indéterminé')
    r.add('WARN','AWS Security Group','règles cloud à contrôler séparément, jamais modifiées par cet audit')


def jenkins(r, cfg):
    section('02 / 03 · Jenkins', 'Santé et réseau — vérification seulement')
    r.add('OK' if unit_active('jenkins') else ('FAIL' if r.strict else 'WARN'),
          'Jenkins systemd','actif' if unit_active('jenkins') else 'inactif')
    check_bind(r,'Jenkins écoute',cfg.jenkins_port)
    http, _ = local_http(cfg.jenkins_port,'/login')
    r.add('OK' if http in (200,302,403) else ('FAIL' if r.strict else 'WARN'),
          'Jenkins HTTP',f'HTTP {http or "inaccessible"}')
    http, text = local_http(cfg.jenkins_port,cfg.jenkins_metrics_path)
    if http==200 and ('# HELP ' in text or '# TYPE ' in text):
        r.add('OK','Exporter Jenkins','métriques accessibles localement')
    elif http in (401,403):
        r.add('WARN','Exporter Jenkins','authentification ; vérifier scrape Prometheus')
    else:
        r.missing('Exporter Jenkins',f'HTTP {http or "inaccessible"}')
    p = command(['id','-nG','jenkins'])
    if p and p.returncode==0 and 'docker' in p.stdout.split():
        r.add('WARN','Groupe Jenkins/Docker','activé, privilèges root-équivalents')
    else:
        r.add('WARN','Groupe Jenkins/Docker','groupe Docker non détecté ou compte absent')


def prometheus_grafana(r, cfg):
    section('03 / 03 · Observabilité', 'Collecte Jenkins → Prometheus → Grafana')
    (r.add('OK','Prometheus service','actif') if unit_active('prometheus') else r.missing('Prometheus service'))
    check_bind(r,'Prometheus écoute',cfg.prometheus_port)
    code, _ = local_http(cfg.prometheus_port,'/-/ready')
    (r.add('OK','Prometheus readiness','HTTP 200') if code==200 else r.missing('Prometheus readiness',f'HTTP {code or "inaccessible"}'))
    code, raw = local_http(cfg.prometheus_port,'/api/v1/targets')
    if code==200:
        try:
            targets = json.loads(raw).get('data',{}).get('activeTargets',[])
            jobs = [j for j in targets if j.get('labels',{}).get('job')==cfg.jenkins_job]
            if any(j.get('health')=='up' for j in jobs):
                r.add('OK','Prometheus → Jenkins','cible UP')
            elif jobs:
                r.add('FAIL','Prometheus → Jenkins','cible DOWN')
            else:
                r.missing('Prometheus → Jenkins','job non trouvé')
        except (ValueError, TypeError, AttributeError):
            r.add('FAIL','Prometheus API','JSON invalide')
    else:
        r.missing('Prometheus API',f'HTTP {code or "inaccessible"}')
    (r.add('OK','Grafana service','actif') if unit_active('grafana-server') else r.missing('Grafana service'))
    check_bind(r,'Grafana écoute',cfg.grafana_port)
    code, raw = local_http(cfg.grafana_port,'/api/health')
    if code==200:
        try:
            database = json.loads(raw).get('database')
            r.add('OK' if database=='ok' else 'FAIL','Grafana health',str(database))
        except (ValueError, TypeError, AttributeError):
            r.add('FAIL','Grafana health','JSON invalide')
    else:
        r.missing('Grafana health',f'HTTP {code or "inaccessible"}')
    datasource = Path('/etc/grafana/provisioning/datasources/autokom-prometheus.yaml')
    if datasource.is_file():
        urls = re.findall(r'^\s+url:\s*(.+?)\s*$', datasource.read_text(encoding='utf-8'), re.M)
        r.add('OK' if cfg.prometheus_base in urls else 'FAIL','Grafana datasource',
              cfg.prometheus_base if cfg.prometheus_base in urls else 'URL non conforme')
    else:
        r.missing('Grafana datasource','provisionnement AUTOKOM absent')
    for kind, domain in (('Prometheus',cfg.prometheus_domain),('Grafana',cfg.grafana_domain)):
        if not domain:
            r.add('OK',f'{kind} domaine','local uniquement')
            continue
        cert = Path('/etc/letsencrypt/live') / domain / 'fullchain.pem'
        p = command(['openssl','x509','-in',str(cert),'-noout','-checkend','604800']) if cert.is_file() else None
        q = command(['openssl','x509','-in',str(cert),'-noout','-checkhost',domain]) if cert.is_file() else None
        r.add('OK' if p and q and p.returncode==0 and q.returncode==0 else 'FAIL',
              f'TLS {kind}',f'{domain} : valide ≥ 7 jours' if p and q and p.returncode==0 and q.returncode==0 else 'certificat absent/invalide')


def main(argv=None):
    p = argparse.ArgumentParser(description='AUTOKOM : audit en lecture seule')
    p.add_argument('--scope', choices=('all','foundation','jenkins','observability'),default='all')
    p.add_argument('--strict', action='store_true', help='absences = échecs')
    p.add_argument('--config',default=None)
    p.add_argument('--set',action='append',default=[],metavar='SECTION.KEY=VALUE')
    args = p.parse_args(argv)
    cfg = load(custom_path=args.config, changes=args.set)
    r = Report(strict=args.strict)
    section('Audit AWS', 'Lecture seule · aucun service modifié')
    if args.scope in ('all','foundation'): foundation(r)
    if args.scope in ('all','jenkins'): jenkins(r,cfg)
    if args.scope in ('all','observability'): prometheus_grafana(r,cfg)
    return r.summary()


if __name__=='__main__':
    try:
        sys.exit(main())
    except (ConfigError, ValueError, OSError) as exc:
        print('  ✗ Audit impossible : '+str(exc),file=sys.stderr)
        sys.exit(2)
