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
import shutil
import signal
import subprocess
import sys
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
        lengths = []
        for field in ('rx', 'tx'):
            size = row.get(field)
            lengths.append(int(size) if isinstance(size, str) and re.fullmatch(r'[0-9]{1,16}', size) else None)
        return timestamp, row['method'], int(row['status']), float(row['seconds']), int(row['bytes']), marker, *lengths
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
    timestamp, method, status, seconds, size, tag, _, _ = event
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


def _volume(value, *, per_second=False):
    if value is None:
        return '—'
    for unit in ('o', 'Kio', 'Mio', 'Gio', 'Tio', 'Pio', 'Eio'):
        if value < 1024 or unit == 'Eio':
            number = str(int(value)) if unit == 'o' and not per_second else f'{value:.1f}'
            return f'{number} {unit}' + ('/s' if per_second else '')
        value /= 1024


def dashboard_lines(host, nodes, ready, failed, counts, errors, traffic, prev_counts, prev_traffic,
                    elapsed, interval, phase, *, tag=None, width=120):
    """A stable row per replica. HTTP bytes are distinct from NIC throughput."""
    total = sum(counts.values())
    rate = (total - sum(prev_counts.values())) / max(interval, .001)
    title = f'AUTOKOM HA  ·  {host}'
    lines = [title, f'{phase}  ·  {elapsed:.1f} s  ·  {total} req  ·  {rate:.1f} req/s']
    # Keep the compact table usable over ordinary 80-column SSH sessions.
    wide = width >= 119
    if wide:
        lines.append(f'{"Worker":<22} {"Zone":<14} {"Req":>8} {"Req/s":>7} {"Part":>7} '
                     f'{"Reçu/s":>12} {"Envoyé/s":>12} {"5xx":>6}  Répartition')
    else:
        lines.append(f'{"Worker":<20} {"Req":>7} {"Req/s":>7} {"Part":>7} {"Reçu/s":>11} {"Envoyé/s":>11} {"5xx":>5}')
    lines.append('─' * min(width, 119 if wide else 77))

    def volume_cell(worker, side):
        octets = traffic[worker, side] - prev_traffic[worker, side]
        known = traffic[worker, side, 'known'] - prev_traffic[worker, side, 'known']
        delta = counts[worker] - prev_counts[worker]
        if delta and known != delta:
            return '—'
        if not delta and not traffic[worker, side, 'known']:
            return '—'
        return _volume(octets / max(interval, .001), per_second=True)

    for node in nodes:
        worker, zone = node['name'], node['zone']
        prefix = f'{worker:<22} {zone:<14}' if wide else f'{worker:<20}'
        if worker not in ready or worker in failed:
            lines.append(prefix + ('  Flux interrompu' if worker in failed else '  Connexion…'))
            continue
        hits = counts[worker]
        share = hits / total if total else 0
        share_text = f'{100 * share:.1f}%' if total else '—'
        qps = (hits - prev_counts[worker]) / max(interval, .001)
        rx, tx = volume_cell(worker, 'rx'), volume_cell(worker, 'tx')
        if wide:
            bars = min(10, int(10 * share + .5))
            bar = '█' * bars + '░' * (10 - bars)
            lines.append(f'{prefix} {hits:8d} {qps:7.1f} {share_text:>7} {rx:>12} {tx:>12} {errors[worker]:6d}  {bar}')
        else:
            lines.append(f'{prefix} {hits:7d} {qps:7.1f} {share_text:>7} {rx:>11} {tx:>11} {errors[worker]:5d}')
    lines.append('─' * min(width, 119 if wide else 77))
    volumes = []
    for side, label in (('rx', 'Reçu'), ('tx', 'Envoyé')):
        values = sum(traffic[node['name'], side] for node in nodes)
        known = sum(traffic[node['name'], side, 'known'] for node in nodes)
        volumes.append(f'{label} {_volume(values if total and known == total else None)}')
    lines.append('Cumul HTTP  ·  ' + '  ·  '.join(volumes) + f'  ·  {sum(errors.values())} HTTP 5xx')
    lines.append(f'Tag {tag}  ·  Ctrl+C pour arrêter' if tag else 'Ctrl+C pour arrêter  ·  Relancer si le placement change')
    return [line[:width] for line in lines]


class TerminalDashboard:
    def __init__(self, out):
        self.out = out
        self.rows = 0

    def draw(self, lines):
        # One write per frame. Only overwrite the previous table, not shell history.
        prefix = f'\x1b[{self.rows}F' if self.rows else ''
        self.out.write(prefix + ''.join('\x1b[2K' + line + '\n' for line in lines))
        self.out.flush()
        self.rows = len(lines)


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
    public_confirmed = (row.get('status') == 'PUBLIC_HTTPS_ACTIVE' and
                        type(report.get('updated')) is int and 0 <= time.time() - report['updated'] <= 330)
    size = shutil.get_terminal_size((120, 30))
    dashboard = (TerminalDashboard(sys.stdout) if follow and not args.requests and not args.plain
                 and sys.stdout.isatty() and os.environ.get('TERM') not in ('dumb', '')
                 and size.lines > len(nodes) + 8 and size.columns >= 76 else None)
    if not dashboard:
        print(f'AUTOKOM HA · {host} · {row.get("status", "INCONNU")}', flush=True)
        if follow and not public_confirmed:
            print('Attention · Le routage public GCP n’est pas confirmé dans un rapport récent.', flush=True)
        print('Workers observés · ' + ', '.join(f'{w["name"]} ({w["zone"]})' for w in nodes), flush=True)
    if follow and not dashboard:
        if not complete_placement:
            print('Suivi partiel · tous les réplicas prévus ne sont pas disponibles.', flush=True)
        print('Ctrl+C pour arrêter · redémarrer le suivi si le placement change.', flush=True)
    queue = Queue(maxsize=4096)
    stop = Event()
    processes, threads, ended, ready, failed, absent, missing = [], [], set(), set(), set(), set(), set()
    counts, errors, traffic = Counter(), Counter(), Counter()
    previous, previous_traffic = Counter(), Counter()
    history = []
    connected_at = None
    started = time.monotonic()
    deadline = started + (limit + 45 if follow else 180 if all_logs else 60)
    next_print = deadline
    last_print = started

    def redraw(now, phase):
        if not public_confirmed:
            phase += ' · HA publique non confirmée'
        dashboard.draw(dashboard_lines(host, nodes, ready, failed, counts, errors, traffic,
                       previous, previous_traffic, now - (connected_at or started), now - last_print,
                       phase, tag=args.tag, width=shutil.get_terminal_size((120, 30)).columns - 1))

    try:
        if dashboard:
            redraw(started, 'Connexion aux workers')
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
                if dashboard:
                    redraw(now, 'Suivi partiel' if failed or not complete_placement else 'Suivi en direct')
                else:
                    print_counts(counts, errors, names, ready, now - connected_at,
                                 previous=previous, interval=now - last_print)
                previous, last_print = counts.copy(), now
                previous_traffic = traffic.copy()
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
                    if not dashboard:
                        print(f'{worker} · {reason}', flush=True)
            elif follow:
                counts[worker] += 1
                errors[worker] += int(500 <= value[2] < 600)
                for side, amount in zip(('rx', 'tx'), value[6:8]):
                    if amount is not None:
                        traffic[worker, side] += amount
                        traffic[worker, side, 'known'] += 1
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
                previous_traffic = traffic.copy()
                deadline = connected_at + limit
                next_print = connected_at + 2
                phase = 'Suivi prêt' if not failed and complete_placement else 'Suivi partiel'
                if dashboard:
                    redraw(connected_at, phase)
                else:
                    print(phase + (' · démarrer le benchmark depuis l’autre terminal.' if phase == 'Suivi prêt'
                                   else ' · ne pas interpréter les pourcentages comme ceux de tout le MIG.'), flush=True)
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
        finished = time.monotonic()
        if dashboard:
            # Final rate is the session average; the table remains visible.
            previous, previous_traffic = Counter(), Counter()
            last_print = connected_at or started
            redraw(finished, 'Bilan partiel' if failed or not complete_placement or ready != set(names) else 'Bilan final')
        else:
            print_counts(counts, errors, names, ready - failed, finished - (connected_at or started))
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
