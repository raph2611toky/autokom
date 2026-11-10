#!/usr/bin/env python3
"""AUTOKOM iac ha : read-only inventory and controlled observability provisioning."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/ha'))
import autokom_ha_preflight as pf
import autokom_ha_extra as extra
import autokom_ha_observability as observe


class HaError(RuntimeError):
    pass


def _use_gce_identity() -> None:
    if os.environ.get('AUTOKOM_GCP_AUTH_MODE') == 'metadata':
        return
    request = urllib.request.Request('http://169.254.169.254/computeMetadata/v1/instance/id',
                                     headers={'Metadata-Flavor': 'Google'})
    try:
        with urllib.request.urlopen(request, timeout=1.2) as response:
            if response.headers.get('Metadata-Flavor') == 'Google':
                os.environ['AUTOKOM_GCP_AUTH_MODE'] = 'metadata'
    except (OSError, TimeoutError):
        pass  # Kali workstation can use gcloud credentials.


def _report(*, metrics: bool = False, details: bool = True) -> dict:
    _use_gce_identity()
    report = pf.inventory(pf.DEFAULT_PROJECT, pf.DEFAULT_REGION,
                          pf.DEFAULT_MIG, pf.DEFAULT_LB_BACKEND,
                          pf.DEFAULT_LB_MAP, pf.DEFAULT_FORWARD_RULE, metrics)
    if details:
        report = extra.enrich(report, cpu_and_network=metrics)
    return report


def _n(value, width=16):
    if value is None:
        return '-'.ljust(width)
    return str(value)[:width].ljust(width)


def cmd_list(args):
    report = _report(metrics=not args.fast)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print(f'AUTOKOM HA · MIG {report["mig"]} · IP publique du LB {report.get("forwarding_ip") or "-"}')
    print('INSTANCE             ZONE           IP PRIVÉE       ETAT     SANTÉ      ACTION       CPU    ENTRANT  SORTANT')
    for row in report['instances']:
        cpu = f'{100*row["cpu_utilization"]:.1f}%' if 'cpu_utilization' in row else '-'
        recv = f'{row["network_receive_Bps"] / 1024:.1f}kB/s' if 'network_receive_Bps' in row else '-'
        sent = f'{row["network_send_Bps"] / 1024:.1f}kB/s' if 'network_send_Bps' in row else '-'
        print(f'{_n(row["name"],20)} {_n(row["zone"],14)} {_n(row.get("private_ip"),15)} '
              f'{_n(row["status"],8)} {_n(row.get("health"),10)} {_n(row.get("action"),12)} '
              f'{_n(cpu,6)} {_n(recv,8)} {sent}')
    print(f'Load Balancer : healthy={report["healthy_backends"]} ; mode={report["bootstrap_mode"]}')
    if 'lb_requests_5m' in report:
        print('Requêtes LB (5 min) : ' + ', '.join(f'{key}={value}' for key, value in report['lb_requests_5m'].items()))
    print('HA applicative : ' + ('active' if report['can_host_customer_applications'] else 'NON ACTIVE'))
    if not args.fast:
        for issue in report['issues']:
            print('BLOQUANT : ' + issue)
    return 0


def cmd_logs(args):
    if args.source == 'publisher':
        return _journal('autokom-ha-autopublish.service', args.lines)
    if args.source == 'exporter':
        return _journal('autokom-ha-metrics.service', args.lines)
    report = _report(details=False)
    candidates = {node['name']: node['zone'] for node in report['instances']}
    if not args.instance:
        print('Workers dans le MIG : ' + ', '.join(candidates))
        print('Choisir --instance NOM, ou --source publisher/exporter')
        return 0
    if args.instance not in candidates:
        raise HaError('Nom absent du MIG actuel : lecture refusée')
    print(f'Console série de {args.instance} ({candidates[args.instance]}) :')
    print(extra.serial_log(report['project'], candidates[args.instance], args.instance, args.lines))
    return 0


def _journal(service, lines):
    result = subprocess.run(['journalctl', '-u', service, '-n', str(lines), '--no-pager', '--output=short-iso'],
                            capture_output=True, text=True, timeout=20, check=False)
    if result.returncode:
        raise HaError('Journaux systemd indisponibles : ' + service)
    print(result.stdout)
    return 0


def _get_json(url):
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=5) as resp:
        return json.load(resp)


def prom_query(expr):
    from observability_config import load
    cfg = load()
    uri = cfg.prometheus_base + '/api/v1/query?' + urllib.parse.urlencode({'query': expr})
    doc = _get_json(uri)
    if doc.get('status') != 'success':
        raise HaError('Requête Prometheus non valide')
    return doc.get('data', {}).get('result', [])


def _prom_targets():
    from observability_config import load
    cfg = load()
    obj = _get_json(cfg.prometheus_base + '/api/v1/targets?state=active')
    for target in obj.get('data', {}).get('activeTargets', []):
        if target.get('labels', {}).get('job') == 'autokom-gcp-ha':
            return {'health': target.get('health', 'unknown'), 'error': target.get('lastError', ''),
                    'last_scrape': target.get('lastScrape', '')}
    return {'health': 'absent', 'error': 'Scrape AUTOKOM HA not installed'}


def cmd_monitor(args):
    from observability_config import load
    cfg = load()
    if args.apply or args.check:
        observe.install(apply=args.apply, datasource_uid=cfg.datasource_uid)
        if args.check:
            return 0
    print('Prometheus : ' + cfg.prometheus_base)
    print('Grafana : ' + cfg.grafana_base)
    print('Dashboard HA : /d/autokom-gcp-ha-20261110')
    try:
        target = _prom_targets()
        print('Cible autokom-gcp-ha : ' + target['health'])
        if target.get('error'):
            print('Scrape : ' + str(target['error'])[:180])
        for expr in ('autokom_ha_mig_instances','autokom_ha_lb_healthy_backends',
                     'autokom_ha_readiness_ok','autokom_ha_metrics_scrape_success'):
            results = prom_query(expr)
            if results:
                print(expr + ' : ' + str(results[0].get('value', [None, 'absent'])[-1]))
            else:
                print(expr + ' : absent (pas zéro)')
    except (OSError, ValueError, RuntimeError, HaError) as exc:
        print('Monitoring non vérifiable : ' + type(exc).__name__)
        return 2
    return 0


def cmd_doctor(args):
    report = _report(metrics=True)
    critical = 0
    print('AUTOKOM HA · diagnostic')
    print('  MIG : ' + report['mig'])
    print('  Workers : ' + str(report['instance_count']))
    print('  Backends sains : ' + str(report['healthy_backends']))
    print('  Endpoint public : ' + str(report['forwarding_ip']))
    for issue in report['issues']:
        print('  BLOQUANT : ' + issue)
        critical += 1
    print('  Applications déployées dans le MIG : non vérifiées (pas de réconciliation)')
    print('  Publication automatique : ' + ('timer présent' if _systemd_enabled('autokom-ha-autopublish.timer') else 'timer absent'))
    if not _systemd_enabled('autokom-ha-autopublish.timer'):
        critical += 1
    folder = Path('/var/lib/autokom/ha/publish-queue')
    if folder.is_dir():
        print('  Images en file de publication : ' + str(len(list(folder.glob('*.json')))))
    try:
        target = _prom_targets()
        print('  Prometheus HA : ' + target['health'])
        if target['health'] != 'up':
            critical += 1
    except (OSError, ValueError, RuntimeError):
        print('  Prometheus HA : indisponible')
        critical += 1
    print('  Haute disponibilité client : ' + ('confirmée' if report['can_host_customer_applications'] else 'NON VALIDÉE'))
    print('  STATUT GLOBAL : ' + ('BLOQUÉ' if critical else 'à vérifier au niveau applicatif'))
    return 2 if critical else 0


def _systemd_enabled(unit):
    try:
        result = subprocess.run(['systemctl', 'is-enabled', '--quiet', unit], timeout=5,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def main(argv=None):
    p = argparse.ArgumentParser(prog='autokom iac ha', description='MIG, LB, logs, supervision et diagnostic HA')
    commands = p.add_subparsers(dest='action', required=True)
    a = commands.add_parser('list', help='VM, IP, état, actions, CPU, réseau, LB')
    a.add_argument('--json', action='store_true')
    a.add_argument('--fast', action='store_true', help='Sans requêtes de métriques Cloud Monitoring')
    b = commands.add_parser('logs', help='Logs série worker ou systemd exporter/publisher')
    b.add_argument('--instance', default=None)
    b.add_argument('--source', choices=('worker','publisher','exporter'), default='worker')
    b.add_argument('--lines', type=int, default=80)
    c = commands.add_parser('monitor', help='Prometheus/Grafana, installation HA idempotente')
    group = c.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true', help='Simulation, sans changement')
    group.add_argument('--apply', action='store_true', help='Provisionner (root + passphrase)')
    commands.add_parser('doctor', help='Contrôles HA end-to-end, sans faux positifs')
    args = p.parse_args(argv)
    if args.action == 'list':
        return cmd_list(args)
    if args.action == 'logs':
        if not (1 <= args.lines <= 500):
            raise HaError('--lines : de 1 à 500')
        return cmd_logs(args)
    if args.action == 'monitor':
        return cmd_monitor(args)
    if args.action == 'doctor':
        return cmd_doctor(args)
    return 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (HaError, ValueError, OSError, RuntimeError) as exc:
        print('AUTOKOM_HA_ERROR ' + str(exc), file=sys.stderr)
        raise SystemExit(2)
