import importlib.util
import io
import json
import gzip
from collections import Counter
from pathlib import Path
from queue import Queue
import subprocess
import sys
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import autokom_ha_traffic as traffic

HOST = 'portfoliolady.auto.ikom.mg'
TAG = 'akbench-demo01'
WORKERS = [
    {'name': 'autokom-ha-web-wx10', 'zone': 'us-central1-a', 'status': 'RUNNING', 'action': 'NONE'},
    {'name': 'autokom-ha-web-4qql', 'zone': 'us-central1-f', 'status': 'RUNNING', 'action': 'NONE'},
    {'name': 'autokom-ha-web-h4bl', 'zone': 'us-central1-f', 'status': 'RUNNING', 'action': 'NONE'},
]
REPORT = {'applications': [{'hosts': [HOST], 'workers': WORKERS[:2], 'status': 'PUBLIC_HTTPS_ACTIVE'}]}
LINE = json.dumps({'time': '2026-11-13T23:00:22+00:00', 'method': 'GET', 'status': '200',
                   'seconds': '0.016', 'bytes': '777', 'tag': TAG})


class TrafficTests(unittest.TestCase):
    def test_only_exact_host_and_actual_placements(self):
        self.assertEqual(traffic.select_workers(HOST, WORKERS, REPORT, follow=True)[1], WORKERS[:2])
        self.assertEqual(traffic.select_workers(HOST, WORKERS, REPORT, follow=False)[1], WORKERS)
        with self.assertRaises(ValueError):
            traffic.select_workers('portfoliolady.ikom.mg', WORKERS, REPORT, follow=True)

    def test_tag_and_validation(self):
        self.assertEqual(traffic.event_from_line(LINE, TAG)[1:], ('GET', 200, .016, 777, TAG, None, None))
        modern = json.dumps({**json.loads(LINE), 'rx': '210', 'tx': '1007'})
        self.assertEqual(traffic.event_from_line(modern, TAG)[6:], (210, 1007))
        self.assertIsNone(traffic.event_from_line(LINE, 'akbench-other'))
        self.assertIsNone(traffic.event_from_line(LINE.replace('GET', 'GET\\u001b')))

    def test_worker_config_and_cli_use_same_private_log_and_exclude_health(self):
        spec = importlib.util.spec_from_file_location('traffic_worker', ROOT /
            'terraform/modules/gcp_regional_ha/files/worker-agent.py')
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        with patch.object(worker, 'container_ipv4', return_value='172.18.0.2'):
            config = worker.render_nginx([{'hosts': [HOST], 'internal_port': 8080}])
        path = next(line.split()[1] for line in config.splitlines() if line.startswith('    access_log '))
        cmd = traffic.ssh_command(WORKERS[0], HOST, True, 60)
        self.assertIn('--internal-ip', cmd)
        self.assertTrue(any(path in arg and '--follow=name --retry' in arg for arg in cmd))
        self.assertIn('location ^~ /__autokom_health/ { access_log off;', config)
        self.assertIn('"rx":"$request_length","tx":"$bytes_sent"', config)
        for unsafe in ('$request_uri', '$remote_addr', '$http_authorization', '$http_cookie'):
            self.assertNotIn(unsafe, config)
        with self.assertRaises(ValueError):
            traffic.ssh_command(WORKERS[0], 'x;false', False, 10)

    def test_live_logs_come_from_each_stream(self):
        queue = Queue()
        for worker in WORKERS[:2]:
            name = worker['name']
            process = SimpleNamespace(stdout=io.StringIO('AKLOG_READY\n' + LINE + '\n'), wait=lambda: 0)
            traffic._read_stream(name, process, queue, TAG)
            self.assertEqual(queue.get()[:2], ('ready', name))
            self.assertEqual(queue.get()[:2], ('event', name))
            self.assertEqual(queue.get(), ('end', name, 0))
        self.assertTrue(queue.empty())

    def test_cli_dispatch(self):
        from autokom_ha_admin import main
        with patch.object(traffic, 'run', return_value=0) as run:
            self.assertEqual(main(['app', HOST, 'follow', '--seconds', '60', '--tag', TAG]), 0)
            self.assertEqual(run.call_args.args[0].tag, TAG)
            self.assertEqual(main(['app', HOST, 'logs', '--lines', '42']), 0)
            self.assertEqual(run.call_args.args[0].lines, 42)

    def test_dashboard_shows_per_worker_rates_and_missing_old_bytes(self):
        a, b = [row['name'] for row in WORKERS[:2]]
        counts = Counter({a: 8, b: 2})
        data = Counter({(a, 'rx'): 800, (a, 'tx'): 8192, (a, 'rx', 'known'): 8,
                        (a, 'tx', 'known'): 8, (b, 'tx'): 1000, (b, 'tx', 'known'): 2})
        frame = traffic.dashboard_lines(HOST, WORKERS[:2], {a, b}, set(), counts, Counter({a: 1}),
                                        data, Counter(), Counter(), 2, 2, 'Suivi prêt', width=119)
        self.assertIn('80.0%', frame[4])
        self.assertIn('400.0 o/s', frame[4])
        self.assertIn('4.0 Kio/s', frame[4])
        self.assertIn('20.0%', frame[5])
        self.assertIn('—', frame[5])
        self.assertIn('Reçu —', frame[7])
        self.assertIn('Envoyé 9.0 Kio', frame[7])
        self.assertTrue(all(len(line) <= 119 for line in frame))
        compact = traffic.dashboard_lines(HOST, WORKERS[:2], {a}, {b}, counts, Counter(), data,
                                          Counter(), Counter(), 2, 2, 'Suivi partiel', width=79)
        self.assertIn('Flux interrompu', compact[5])
        self.assertTrue(all(len(line) <= 79 for line in compact))

    def test_table_frames_reuse_rows_without_clearing_terminal_history(self):
        stream = io.StringIO()
        table = traffic.TerminalDashboard(stream)
        table.draw(['Header', 'value 1'])
        table.draw(['Header', 'value 2'])
        self.assertEqual(stream.getvalue(), '\x1b[2KHeader\n\x1b[2Kvalue 1\n'
                         '\x1b[2F\x1b[2KHeader\n\x1b[2Kvalue 2\n')

    def test_full_logs_read_numbered_rotations_without_other_paths(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'autokom-ha-test.log'
            path.write_bytes(b'current\n')
            Path(str(path) + '.1').write_bytes(b'previous\n')
            Path(str(path) + '.2.gz').write_bytes(gzip.compress(b'oldest\n'))
            Path(str(path) + '.other').write_bytes(b'not in log rotation\n')
            Path(str(path) + '.3').symlink_to(path)
            out = subprocess.check_output([sys.executable, '-c', traffic.READ_ROTATIONS, str(path)])
            self.assertEqual(out, b'oldest\nprevious\ncurrent\n')


if __name__ == '__main__':
    unittest.main()
