#!/usr/bin/env python3
"""Inventaire HA par worker, strictement en lecture seule.

Sépare : manifeste GCS désiré, santé effective par endpoint du backend GCP,
rapport du contrôleur, et validation du basculement HTTPS public.
Une VM MIG HEALTHY ne prouve jamais qu'une application est HEALTHY.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/ha'))

UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z')
WORKER = re.compile(r'autokom-ha-web-[a-z0-9]{1,12}\Z')
MAX_MANIFEST_APPS = 200


def _uuid(value):
    return isinstance(value, str) and bool(UUID.fullmatch(value))


def _read_delivery(path=Path('/var/lib/autokom/ha/delivery-status.json')):
    """Rapport borné : jamais de lien symbolique ni de chemin fourni par l'utilisateur."""
    if path.is_symlink() or not path.is_file():
        return None, 'rapport de reconciliation absent'
    try:
        if path.stat().st_size > 262144:
            return None, 'rapport de reconciliation trop volumineux'
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError):
        return None, 'rapport de reconciliation illisible'
    if not isinstance(doc, dict) or not isinstance(doc.get('applications'), list):
        return None, 'format du rapport de reconciliation invalide'
    return doc, None


def _fetch_desired(worker_name, gcs_get):
    """Ne jamais traiter un echec GCS comme un manifeste vide."""
    if not WORKER.fullmatch(worker_name):
        return None, 'identifiant worker invalide'
    try:
        doc = gcs_get(f'desired/{worker_name}.json')
    except (OSError, RuntimeError, ValueError) as exc:
        return None, 'lecture GCS impossible (' + type(exc).__name__ + ')'
    if doc is None:
        return None, 'aucun manifeste GCS pour ce worker'
    if not isinstance(doc, dict) or doc.get('instance') != worker_name or not isinstance(doc.get('applications'), list):
        return None, 'manifeste GCS invalide'
    if len(doc['applications']) > MAX_MANIFEST_APPS:
        return None, 'limite de taille des applications GCS depassee'
    return doc['applications'], None


def _names(assignment):
    rows = assignment.get('workers') or []
    return [w.get('name') for w in rows if isinstance(w, dict) and isinstance(w.get('name'), str)]


def _health_for_app(app, worker, compute, base):
    """Retourne un état APP par endpoint, jamais la santé générale du MIG.

    GET backendService + POST getHealth sont des lectures Compute Engine.
    Si le service/NEG n'est pas présent ou l'API échoue, l'état est inconnu.
    """
    env, run = app['environment'], app['run_id']
    tag = env.replace('-', '')[:12] + '-' + run.replace('-', '')[:12]
    backend_name = 'ak-b-' + tag
    neg_name = 'ak-n-' + tag
    # GCP can canonicalize selfLinks to www.googleapis.com rather than
    # compute.googleapis.com; compare the validated resource path, not the host.
    suffix = f'/projects/{base.rsplit("/projects/", 1)[-1]}/zones/{worker["zone"]}/networkEndpointGroups/{neg_name}'
    try:
        service = compute('GET', '/global/backendServices/' + backend_name, missing_ok=True)
        if not isinstance(service, dict):
            return 'ABSENT'
        backends = service.get('backends') or []
        groups = [b.get('group') for b in backends if isinstance(b, dict)]
        group = next((g for g in groups if isinstance(g, str) and
                      g.startswith('https://') and g.endswith(suffix)), None)
        if not group:
            return 'ABSENT'
        doc = compute('POST', '/global/backendServices/' + backend_name + '/getHealth', {'group': group})
        statuses = doc.get('healthStatus') if isinstance(doc, dict) else None
        if not isinstance(statuses, list):
            return 'INCONNU'
        states = [str(s.get('healthState') or '').upper() for s in statuses
                  if isinstance(s, dict) and str(s.get('instance') or '').rsplit('/', 1)[-1] == worker['name']]
        if 'HEALTHY' in states:
            return 'HEALTHY'
        return states[0] if states else 'INCONNU'
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return 'INCONNU'


def assemble(mig, report, desired, health=None):
    """Fusion déterministe, sans E/S. Param health: (worker, environment, run) => état."""
    health = health or {}
    decisions = report.get('applications', []) if isinstance(report, dict) else []
    by_env = {d.get('environment'): d for d in decisions
              if isinstance(d, dict) and _uuid(d.get('environment'))}
    nodes = []
    present = set()
    for worker in mig:
        name = worker['name']
        planned = desired.get(name)
        rows = []
        if isinstance(planned, list):
            for entry in planned:
                if not isinstance(entry, dict):
                    continue
                env = entry.get('environment_id')
                run = entry.get('run_id')
                if not (_uuid(env) and _uuid(run)):
                    continue
                decision = by_env.get(env, {})
                requested = (decision.get('ha') is True) if type(decision.get('ha')) is bool else None
                key = (name, env, run)
                current_health = health.get(key, 'INCONNU')
                present.add(env)
                rows.append({'environment': env, 'run_id': run,
                             'host': next((h for h in entry.get('hosts', []) if isinstance(h, str)), '-'),
                             'ha_requested': requested,
                             'desired_replicas': 2 if requested is True else (1 if requested is False else None),
                             'health': current_health,
                             'delivery_status': str(decision.get('status', 'PLANIFIE'))})
        nodes.append({'name': name, 'zone': worker.get('zone'), 'private_ip': worker.get('private_ip'),
                      'vm_health': worker.get('health', 'INCONNU'),
                      'manifest_available': isinstance(planned, list),
                      'applications': sorted(rows, key=lambda r: (r['host'], r['environment']))})
    # Calcul HA global : deux endpoints APPLICATIFS healthy, deux zones, version identique.
    group = {}
    for w in nodes:
        for app in w['applications']:
            group.setdefault(app['environment'], []).append((w, app))
    public_ready = (isinstance(report, dict) and report.get('cloud_https_ready') is True
                    and report.get('public_routing_verified') is True)
    summary = []
    for env, entries in sorted(group.items()):
        decision = by_env.get(env, {})
        wanted = (decision.get('ha') is True) if type(decision.get('ha')) is bool else None
        healthy = [(w, a) for w, a in entries if a['health'] == 'HEALTHY' and w['vm_health'] == 'HEALTHY']
        zones = {w['zone'] for w, _ in healthy}
        runs = {a['run_id'] for _, a in healthy}
        replicas_ok = (len(healthy) >= 2 and len(zones) >= 2 and len(runs) == 1
                       and decision.get('run_id') in runs)
        # En cas d'incertitude du contrôleur, ne jamais inventer une HA effective.
        if wanted is None:
            ha_state = 'INCONNUE'
        elif not wanted:
            ha_state = 'NON_DEMANDEE'
        elif replicas_ok and public_ready and decision.get('status') in ('HTTPS_READY', 'ACTIVE'):
            ha_state = 'ACTIVE'
        elif replicas_ok:
            ha_state = 'REPLIQUES_PRETES_HTTPS_NON_VALIDE'
        else:
            ha_state = 'NON_VALIDEE'
        summary.append({'environment': env,
                        'host': entries[0][1]['host'],
                        'ha_requested': wanted,
                        'healthy_app_replicas': len(healthy),
                        'desired_replicas': 2 if wanted is True else (1 if wanted is False else None),
                        'workers': [w['name'] for w, _ in entries],
                        'ha_status': ha_state})
    pending = []
    for env, decision in sorted(by_env.items()):
        if env not in present:
            pending.append({'environment': env, 'status': str(decision.get('status', 'INCONNU')),
                            'ha_requested': decision.get('ha') is True})
    return {'workers': nodes, 'applications': summary, 'unassigned': pending,
            'cloud_https_ready': bool(isinstance(report, dict) and report.get('cloud_https_ready') is True),
            'public_routing_verified': bool(public_ready)}


def get_inventory(mig_report, *, delivery_reader=_read_delivery, fetcher=None, health_probe=None):
    """Construit un inventaire à partir des VMs MIG, GCS et de la santé GCP.

    Tous les appels réseau sont des lectures. Les erreurs sont visibles, pas des listes vides.
    """
    from autokom_ha_delivery import gcs_get, compute, BASE
    get_manifest = fetcher or gcs_get
    get_health = health_probe or (lambda app, worker: _health_for_app(app, worker, compute, BASE))
    report, error = delivery_reader()
    nodes = []
    desired = {}
    problems = []
    if error:
        problems.append(error)
    for raw in mig_report.get('instances', []):
        name = raw.get('name')
        if not isinstance(name, str) or not WORKER.fullmatch(name):
            continue
        zone = raw.get('zone')
        if zone not in ('us-central1-a', 'us-central1-f'):
            continue
        node = {'name': name, 'zone': zone,
                'private_ip': raw.get('private_ip'), 'health': raw.get('health')}
        nodes.append(node)
        entries, problem = _fetch_desired(name, get_manifest)
        desired[name] = entries
        if problem:
            problems.append(f'{name}: {problem}')
    values = {}
    for node in nodes:
        manifests = desired.get(node['name'])
        if not isinstance(manifests, list):
            continue
        for app in manifests:
            if not isinstance(app, dict) or not (_uuid(app.get('environment_id')) and _uuid(app.get('run_id'))):
                continue
            key = (node['name'], app['environment_id'], app['run_id'])
            values[key] = get_health({'environment': app['environment_id'], 'run_id': app['run_id']}, node)
    inventory = assemble(nodes, report, desired, values)
    inventory['errors'] = problems
    inventory['report_available'] = report is not None
    return inventory


def output(inventory, *, ha_only=False, as_json=False):
    if as_json:
        # Même représentation, aucune image Docker, aucun jeton, aucune variable .env.
        doc = dict(inventory)
        if ha_only:
            selected = {a['environment'] for a in doc['applications'] if a['ha_requested']}
            doc['workers'] = [dict(w, applications=[a for a in w['applications']
                                                   if a['environment'] in selected]) for w in doc['workers']]
            doc['applications'] = [a for a in doc['applications'] if a['ha_requested']]
            doc['unassigned'] = [a for a in doc['unassigned'] if a['ha_requested']]
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0
    summary = {a['environment']: a for a in inventory['applications']}
    print('AUTOKOM HA · Applications réparties par worker GCP (lecture seule)')
    print('Source : manifests GCS + santé applicative GCP + rapport delivery')
    for worker in inventory['workers']:
        rows = [a for a in worker['applications'] if not ha_only or a['ha_requested']]
        print(f'\nWORKER {worker["name"]} · {worker["zone"]} · VM {worker["vm_health"]}')
        if not worker['manifest_available']:
            print('  Inventaire des applications INDISPONIBLE (manifest GCS non vérifié)')
        elif not rows:
            print('  Aucune application déclarée' + (' avec HA' if ha_only else ''))
        for app in rows:
            global_status = summary.get(app['environment'], {}).get('ha_status', 'INCONNU')
            print(f'  {app["host"]} · env={app["environment"][:8]} · app={app["health"]}'
                  f' · HA={global_status} · livraison={app["delivery_status"]}')
    selected = [x for x in inventory['applications'] if not ha_only or x['ha_requested']]
    print('\nBILAN : applications=' + str(len(selected)) +
          ' ; HA actives=' + str(sum(x['ha_status'] == 'ACTIVE' for x in selected)) +
          ' ; HA non validées=' + str(sum(x['ha_requested'] and x['ha_status'] != 'ACTIVE' for x in selected)))
    pending = [x for x in inventory['unassigned'] if not ha_only or x['ha_requested']]
    for item in pending:
        print(f'EN ATTENTE : env={item["environment"][:8]} · livraison={item["status"]} · HA demandée={item["ha_requested"]}')
    if not inventory['cloud_https_ready']:
        print('HA PUBLIQUE : NON VALIDEE (HTTPS/DNS du Load Balancer non basculés)')
    for item in inventory['errors']:
        print('ATTENTION : ' + item)
    print('Note : un manifeste GCS est une affectation désirée ; seul HEALTHY prouve un endpoint applicatif sain.')
    return 0
