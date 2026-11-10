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


def _private_record(path, *, max_bytes=8192):
    """Read-only root-owned JSON, no symlinks and no world/group-write files."""
    import stat
    try:
        if path.is_symlink():
            return None
        st = path.stat(follow_symlinks=False)
        if st.st_uid != 0 or not stat.S_ISREG(st.st_mode) or st.st_mode & 0o022 or st.st_size > max_bytes:
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def _selected_hosts(environment, root):
    """Mimics the delivery controller's domain owner verification."""
    import stat
    hosts = []
    host_pattern = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z')
    for path in root.glob('domain-owner-*.txt'):
        try:
            st = path.stat(follow_symlinks=False)
            name = path.name[len('domain-owner-'):-len('.txt')]
            if (path.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or
                    st.st_size > 128 or not host_pattern.fullmatch(name)):
                continue
            if path.read_text(encoding='utf-8').strip() == environment:
                hosts.append(name)
        except (OSError, UnicodeError):
            continue
    return sorted(set(hosts))[:8]


def _delivery_blocks(current, published, *, hosts=(), pending_previous=False):
    """Codes lisibles calqués sur les gardes de committed_apps() du contrôleur.

    N'affiche ni paramètres .env ni image complète.
    """
    errors = []
    if current.get('cloud_delivery_requested') is not True:
        errors.append('CLOUD_DELIVERY_NON_DEMANDE')
    if pending_previous:
        errors.append('PUBLICATION_PRECEDENTE_NON_FINALISEE')
    if current.get('variables_present'):
        errors.append('VARIABLES_NON_PRISES_EN_CHARGE')
    if current.get('stack_project_id'):
        errors.append('STACK_NON_ATOMIQUE')
    if not isinstance(published, dict) or published.get('run_id') != current.get('run_id'):
        errors.append('IMAGE_REGISTRY_ABSENTE')
    else:
        image = published.get('image')
        prefix = 'us-central1-docker.pkg.dev/plucky-stacker-480421-c4/autokom-ha-images/'
        if not isinstance(image, str) or not image.startswith(prefix) or not re.fullmatch('sha256:[a-f0-9]{64}', image.rsplit('@', 1)[-1]) or '@' not in image:
            errors.append('IMAGE_REGISTRY_INVALIDE')
    port = current.get('internal_port')
    if type(port) is not int or not (1024 <= port <= 65535):
        errors.append('PORT_INTERNE_NON_ELIGIBLE')
    health = current.get('healthcheck')
    if (not isinstance(health, str) or not health.startswith('/') or len(health) > 120 or
        any(char in health for char in ('?', '#', '%', '\\', '\r', '\n', ' ', '://'))):
        errors.append('HEALTHCHECK_NON_ELIGIBLE')
    memory = current.get('memory_mb')
    cpu = current.get('cpu_millicores')
    if type(memory) is not int or not 128 <= memory <= 1400:
        errors.append('RAM_HORS_BORNE_DELIVERY')
    if type(cpu) is not int or not 100 <= cpu <= 1800:
        errors.append('CPU_HORS_BORNE_DELIVERY')
    if not hosts:
        errors.append('DOMAINE_NON_ASSOCIE')
    return errors


def diagnostic_local_releases(inventory, *, state_root=Path('/var/lib/autokom/client-runtime'),
                              published_root=Path('/var/lib/autokom/ha/published')):
    """Why public local releases are not assigned to cloud workers.

    Does not query Docker, edit state or expose secrets. Root required for 0600 metadata.
    """
    if not state_root.is_dir() or state_root.is_symlink():
        return []
    present = {app['environment'] for app in inventory.get('applications', [])}
    output = []
    for folder in sorted(state_root.iterdir()):
        if len(output) >= 200:
            break
        if folder.is_symlink() or not folder.is_dir() or not _uuid(folder.name):
            continue
        doc = _private_record(folder / 'current.json')
        if not doc or doc.get('environment_id') != folder.name or not _uuid(doc.get('run_id')):
            continue
        published = _private_record(published_root / (doc['run_id'] + '.json'))
        hosts = _selected_hosts(folder.name, state_root)
        issues = _delivery_blocks(doc, published, hosts=hosts,
                                  pending_previous=(folder / 'previous.json').exists())
        assigned = folder.name in present
        output.append({'environment': folder.name, 'run_id': doc['run_id'],
                       'host': hosts[0] if hosts else '-',
                       'ha_requested': doc.get('high_availability_requested') is True,
                       'cloud_delivery_requested': doc.get('cloud_delivery_requested') is True,
                       'delivery_eligible': not issues,
                       'assigned': assigned,
                       'status': ('AFFECTE' if assigned else
                                  'ELIGIBLE_SANS_AFFECTATION' if not issues else 'EXCLU'),
                       'reasons': issues})
    return output


def _table(headers, records):
    """Display as one kubectl-like wide table, with predictable columns."""
    if not records:
        print('Aucune ligne à afficher.')
        return
    # Data can originate from a GCS manifest. Never allow ANSI/control output.
    def safe_cell(value):
        value = str(value)
        value = ''.join(ch for ch in value if ch.isprintable() and ch != '\x1b')
        return value[:260]
    records = [tuple(safe_cell(v) for v in row) for row in records]
    widths = [max(len(str(h)), *(len(row[i]) for row in records)) for i, h in enumerate(headers)]
    print('  '.join(str(h).ljust(widths[i]) for i, h in enumerate(headers)))
    for row in records:
        print('  '.join(value.ljust(widths[i]) for i, value in enumerate(row)))


def output(inventory, *, ha_only=False, as_json=False, diagnose=False, wide=True):
    selected = [x for x in inventory['applications'] if not ha_only or x['ha_requested']]
    chosen = {a['environment'] for a in selected}
    visible_workers = []
    for w in inventory['workers']:
        visible_workers.append(dict(w, applications=[a for a in w['applications']
                                                    if not ha_only or a['environment'] in chosen]))
    pending = [a for a in inventory['unassigned'] if not ha_only or a['ha_requested']]
    local = [a for a in inventory.get('local_releases', []) if not ha_only or a['ha_requested']]
    if as_json:
        document = dict(inventory, workers=visible_workers, applications=selected,
                        unassigned=pending)
        if 'local_releases' in document:
            document['local_releases'] = local
        print(json.dumps(document, indent=2, ensure_ascii=False))
        return 0
    print('AUTOKOM HA · Inventaire des applications GCP (lecture seule)')
    print('Manifestes GCS (désiré) + santé NEG (observée) + rapport delivery')
    summary = {a['environment']: a for a in selected}
    records = []
    for w in visible_workers:
        if not w['applications']:
            records.append((w['name'], w.get('zone') or '-', w.get('private_ip') or '-',
                            '-', '-', 'AUCUNE' if w['manifest_available'] else 'INCONNU',
                            '0', '-', '-'))
        for app in w['applications']:
            aggregate = summary.get(app['environment'], {})
            records.append((w['name'], w.get('zone') or '-', w.get('private_ip') or '-',
                            app['host'], app['environment'][:8], app['health'],
                            str(aggregate.get('healthy_app_replicas', 0)) + '/' +
                            str(aggregate.get('desired_replicas') or '?'),
                            aggregate.get('ha_status', 'INCONNU'), app['run_id'][:8]))
    _table(['WORKER', 'ZONE', 'IP_PRIVEE', 'APPLICATION/DOMAINE', 'ENV', 'SANTE_APP',
            'REPLICAS', 'HA', 'RELEASE'], records)
    print('\nBILAN : ' + str(len(selected)) + ' application(s) GCP ; ' +
          str(sum(a['ha_status'] == 'ACTIVE' for a in selected)) + ' HA publique(s) validée(s)')
    if not inventory['cloud_https_ready']:
        print('HA PUBLIQUE : NON VALIDEE — DNS/HTTPS sur le Load Balancer non basculés')
    if not selected:
        print('Aucune application cliente confirmée dans les manifests/NEGs GCP.')
    for item in pending:
        print('SANS WORKER : env=' + item['environment'][:8] + ' · ' + item['status'] +
              ' · HA demandée=' + str(item['ha_requested']))
    if diagnose:
        print('\nPUBLICATIONS LOCALES SUR LE SERVEUR PRINCIPAL (HORS MIG) :')
        _table(['APPLICATION/DOMAINE', 'ENV', 'HA_DEMANDEE', 'REGISTRY/DELIVERY', 'RELEASE'],
               [(app['host'], app['environment'][:8], 'OUI' if app['ha_requested'] else 'NON',
                 app['status'], app['run_id'][:8]) for app in local])
        for app in local:
            if app['reasons']:
                print('DIAGNOSTIC ' + app['environment'][:8] + ' : ' + ', '.join(app['reasons']))
            elif not app['assigned']:
                print('DIAGNOSTIC ' + app['environment'][:8] +
                      ' : éligible d’après les métadonnées locales, mais sans manifeste GCP ; ' +
                      'contrôler le prochain cycle de réconciliation')
        if not local:
            print('Aucune publication locale lisible (exécuter avec sudo pour lire les fichiers 0600).')
    for error in inventory.get('errors', []):
        print('ATTENTION : ' + error)
    print('Une VM HEALTHY ou un manifeste GCS ne prouvent pas une réplique applicative saine.')
    return 0
