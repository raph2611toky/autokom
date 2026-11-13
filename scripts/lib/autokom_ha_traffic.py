"""Read application access logs on the existing private MIG management path."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
import os
from queue import Empty, Full, Queue
import re
import shlex
import signal
import subprocess
from threading import Event, Thread
import time

from autokom_ha_apps import _read_delivery
from autokom_ha_worker_sync import KEY, discover, gcloud_flags

HOST = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}')
TAG = re.compile(r'akbench-[a-z0-9-]{1,40}')
LOG_ROOT = '/var/log/nginx/'
READ_ROTATIONS = r"""import gzip, pathlib, re, shutil, sys
base = pathlib.Path(sys.argv[1])
archive = re.compile(re.escape(base.name) + r'\.([1-9][0-9]{0,3})(\.gz)?')
entries = []
for path in base.parent.glob(base.name + '.*'):
    match = archive.fullmatch(path.name)
    if match and path.is_file() and not path.is_symlink():
        entries.append((int(match[1]), path))
for _, path in sorted(entries, reverse=True) + [(0, base)]:
    if path.is_symlink() or not path.is_file():
        continue
    with (gzip.open(path, 'rb') if path.suffix == '.gz' else path.open('rb')) as source:
        shutil.copyfileobj(source, sys.stdout.buffer)
    sys.stdout.buffer.flush()
"""


def select_workers(host, members, report, *, follow):
    """Only actual MIG members are SSH targets. History includes past placements."""
    rows = [row for row in report['applications'] if isinstance(row, dict)
            and isinstance(row.get('hosts'), list) and host in row['hosts']]
    if len(rows) != 1:
        raise ValueError('Domaine absent ou ambigu dans le rapport HA. Utilise le nom exact indiqué par autokom iac ha apps.')
    placement = {(w.get('name'), w.get('zone')) for w in rows[0].get('workers', [])
                 if isinstance(w, dict)}
    selected = [node for node in members if node['status'] == 'RUNNING' and node['action'] == 'NONE'
                and (not follow or (node['name'], node['zone']) in placement)]
    if not selected:
        raise ValueError('Aucun worker stable pour ce domaine dans le MIG.')
    return rows[0], selected


def event_from_line(line, tag=None):
    if len(line) > 1024:
        return None
    try:
        row = json.loads(line)
        if not isinstance(row, dict):
            return None
        timestamp = datetime.fromisoformat(row['time'])
        if timestamp.tzinfo is None or not re.fullmatch(r'[A-Z-]{1,20}', row['method']):
            return None
        if not re.fullmatch(r'[0-9]{3}', row['status']):
            return None
        if not re.fullmatch(r'[0-9]{1,9}(?:\.[0-9]{1,6})?', row['seconds']):
            return None
        if not re.fullmatch(r'[0-9]{1,16}', row['bytes']):
            return None
        marker = row.get('tag', '')
        if not isinstance(marker, str) or (marker and not TAG.fullmatch(marker)):
            return None
        if tag and marker != tag:
            return None
        return timestamp, row['method'], int(row['status']), float(row['seconds']), int(row['bytes']), marker
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def ssh_command(worker, host, follow, limit, *, all_logs=False):
    if len(host) > 253 or not HOST.fullmatch(host):
        raise ValueError('Nom DNS invalide.')
    path = LOG_ROOT + 'autokom-ha-' + sha256(host.encode('ascii')).hexdigest() + '.log'
    tail = ('timeout --signal=TERM ' + str(limit + 15) +
            ' tail -n 0 --follow=name --retry -- ') if follow else ('tail -n ' + str(limit) + ' -- ')
    # Only the hostname digest, an integer, and fixed flags are used remotely.
    # The initial check also distinguishes an absent log from an empty log.
    read = ('python3 -c ' + shlex.quote(READ_ROTATIONS) + ' ' + path) if all_logs else tail + path
    remote = (f'if test -L {path} || ! test -f {path}; then printf "AKLOG_MISSING\\n"; exit 44; fi; '
              f'printf "AKLOG_READY\\n"; exec {read}')
    return ['gcloud', 'compute', 'ssh', 'root@' + worker['name'], *gcloud_flags(worker),
            '--command=' + remote, '--ssh-flag=-oBatchMode=yes',
            '--ssh-flag=-oConnectTimeout=8', '--ssh-flag=-oServerAliveInterval=5',
            '--ssh-flag=-oServerAliveCountMax=2', '--ssh-flag=-T']


def _read_stream(worker, process, queue, tag, stop=None):
    def send(message):
        while stop is None or not stop.is_set():
            try:
                queue.put(message, timeout=0.2)
                return True
            except Full:
                continue
        return False
    try:
        for line in process.stdout:
            if line.strip() == 'AKLOG_MISSING':
                send(('missing', worker, None))
                continue
            if line.strip() == 'AKLOG_READY':
                if not send(('ready', worker, None)):
                    break
                continue
            event = event_from_line(line, tag)
            if event and not send(('event', worker, event)):
                break
    finally:
        if stop is None or not stop.is_set():
            send(('end', worker, process.wait()))


def _stop(processes):
    for process in processes:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    for process in processes:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)


def _print_event(worker, event):
    timestamp, method, status, seconds, size, tag = event
    print(f'{timestamp.isoformat()}  {worker}  {method}  {status}  '
          f'{seconds * 1000:.1f} ms  {size} o' + (f'  {tag}' if tag else ''))


def print_counts(counts, errors, workers, ready, elapsed, *, previous=None, interval=None):
    total = sum(counts.values())
    print(f'AUTOKOM HA · {total} requêtes observées sur {elapsed:.1f} s', flush=True)
    for worker in workers:
        hits = counts[worker]
        share = f'{100 * hits / total:.1f}%' if total else '—'
        if worker not in ready:
            print(f'  {worker}  flux indisponible', flush=True)
        else:
            rate = hits / max(elapsed, .001) if previous is None else (hits - previous[worker]) / max(interval, .001)
            print(f'  {worker}  {hits} req · {rate:.1f} req/s · '
                  f'{share} du total observé · {errors[worker]} HTTP 5xx', flush=True)


def run(args):
    host = args.hostname.lower().rstrip('.')
    if len(host) > 253 or not HOST.fullmatch(host):
        raise ValueError('Indiquer un domaine DNS exact, sans URL.')
    if args.tag and not TAG.fullmatch(args.tag):
        raise ValueError('Marqueur invalide, format attendu akbench- suivi de 1 à 40 caractères a-z, 0-9 ou tirets.')
    follow = args.app_action == 'follow'
    all_logs = not follow and args.all
    limit = args.seconds if follow else args.lines
    if not 1 <= limit <= (900 if follow else 5000):
        raise ValueError('Limite hors plage. follow : 1 à 900 secondes. logs : 1 à 5000 lignes par worker.')
    if os.geteuid() != 0:
        raise RuntimeError('Exécuter avec sudo sur le contrôleur AUTOKOM.')
    if KEY.is_symlink() or not KEY.is_file():
        raise RuntimeError('Clé privée du synchroniseur HA absente sur ce contrôleur.')
    key_mode = KEY.stat()
    if key_mode.st_uid != 0 or key_mode.st_mode & 0o077:
        raise RuntimeError('Permissions de la clé SSH HA invalides.')
    report, error = _read_delivery()
    if error:
        raise RuntimeError(error)
    row, nodes = select_workers(host, discover(), report, follow=follow)
    names = [worker['name'] for worker in nodes]
    wanted = {worker.get('name') for worker in row.get('workers', []) if isinstance(worker, dict)}
    complete_placement = not follow or (set(names) == wanted and (row.get('ha') is not True or len(names) >= 2))
    print(f'AUTOKOM HA · {host} · {row.get("status", "INCONNU")}', flush=True)
    if follow and (row.get('status') != 'PUBLIC_HTTPS_ACTIVE' or
                   type(report.get('updated')) is not int or not 0 <= time.time() - report['updated'] <= 330):
        print('Attention · Le routage public GCP n’est pas confirmé dans un rapport récent.', flush=True)
    print('Workers observés · ' + ', '.join(f'{w["name"]} ({w["zone"]})' for w in nodes), flush=True)
    if follow:
        if not complete_placement:
            print('Suivi partiel · tous les réplicas prévus ne sont pas disponibles.', flush=True)
        print('Ctrl+C pour arrêter · redémarrer le suivi si le placement change.', flush=True)
    queue = Queue(maxsize=4096)
    stop = Event()
    processes, threads, ended, ready, failed, absent, missing = [], [], set(), set(), set(), set(), set()
    counts, errors = Counter(), Counter()
    previous = Counter()
    history = []
    connected_at = None
    started = time.monotonic()
    deadline = started + (limit + 45 if follow else 180 if all_logs else 60)
    next_print = deadline
    last_print = started
    try:
        for worker in nodes:
            proc = subprocess.Popen(ssh_command(worker, host, follow, limit, all_logs=all_logs), stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, errors='replace',
                                    env={**os.environ, 'CLOUDSDK_CORE_DISABLE_PROMPTS': '1'}, start_new_session=True)
            processes.append(proc)
            thread = Thread(target=_read_stream, args=(worker['name'], proc, queue, args.tag, stop), daemon=True)
            thread.start()
            threads.append(thread)
        while len(ended) < len(names) and time.monotonic() < deadline:
            now = time.monotonic()
            if follow and now >= next_print:
                print_counts(counts, errors, names, ready, now - connected_at,
                             previous=previous, interval=now - last_print)
                previous, last_print = counts.copy(), now
                next_print = now + 2
            try:
                kind, worker, value = queue.get(timeout=0.2)
            except Empty:
                continue
            if kind == 'ready':
                ready.add(worker)
            elif kind == 'missing':
                missing.add(worker)
            elif kind == 'end':
                ended.add(worker)
                if not follow and worker in missing:
                    absent.add(worker)
                    print(f'{worker} · aucun fichier courant pour ce domaine', flush=True)
                elif value not in (0, 124) or worker not in ready or (follow and time.monotonic() < deadline):
                    failed.add(worker)
                    reason = 'journal absent, vérifier la synchronisation' if worker in missing else 'flux interrompu ou SSH indisponible'
                    print(f'{worker} · {reason}', flush=True)
            elif follow:
                counts[worker] += 1
                errors[worker] += int(500 <= value[2] < 600)
                if args.requests:
                    _print_event(worker, value)
            elif all_logs:
                counts[worker] += 1
                _print_event(worker, value)
            else:
                history.append((value, worker))
            if follow and ready and ready | ended == set(names) and connected_at is None:
                connected_at = time.monotonic()
                last_print, previous = connected_at, counts.copy()
                deadline = connected_at + limit
                next_print = connected_at + 2
                print(('Suivi prêt · démarrer le benchmark depuis l’autre terminal.' if not failed and complete_placement
                       else 'Suivi partiel · ne pas interpréter les pourcentages comme ceux de tout le MIG.'),
                      flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        _stop(processes)
        for thread, process in zip(threads, processes):
            thread.join(timeout=0.5)
            if not thread.is_alive():
                process.stdout.close()
    if follow:
        print_counts(counts, errors, names, ready, time.monotonic() - (connected_at or started))
    elif all_logs:
        print(f'{sum(counts.values())} ligne(s) · fichiers courants et rotations présentes')
    else:
        for event, worker in sorted(history):
            _print_event(worker, event)
        print(f'{len(history)} ligne(s) · fichiers courants · au plus {limit} lignes lues par worker')
    complete = (complete_placement and bool(ready) and (ready | absent) == set(names)
                and not failed and (follow or ended == set(names)))
    if not complete:
        print('Résultat partiel · au moins un journal n’a pas pu être lu entièrement.', flush=True)
    return 0 if complete else 3
