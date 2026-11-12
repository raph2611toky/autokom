"""Offline tests for main controller HA synchronization and remote transaction."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = load('ak_worker_sync', ROOT / 'scripts/lib/autokom_ha_worker_sync.py')
REMOTE = load('ak_worker_apply', ROOT / 'scripts/lib/autokom_ha_worker_remote_apply.py')
PRIVATE = load('ak_private_update', ROOT / 'scripts/lib/autokom_private_update.py')


class WorkerSyncTests(unittest.TestCase):
    def test_read_only_inventory_from_mig(self):
        response = '[{"instance":"https://www.googleapis.com/compute/v1/projects/p/zones/us-central1-f/instances/autokom-ha-web-km01","instanceStatus":"RUNNING","currentAction":"NONE"}]'
        with mock.patch.object(SYNC, 'command', return_value=response):
            nodes = SYNC.discover()
        self.assertEqual(nodes[0]['name'], 'autokom-ha-web-km01')
        self.assertEqual(nodes[0]['zone'], 'us-central1-f')
        self.assertEqual(nodes[0]['status'], 'RUNNING')

    def test_mig_rejects_unknown_worker_name(self):
        response = '[{"instance":"https://example/zones/us-central1-a/instances/untrusted"}]'
        with mock.patch.object(SYNC, 'command', return_value=response):
            with self.assertRaises(SYNC.SyncError):
                SYNC.discover()

    def test_mig_rejects_empty_response(self):
        with mock.patch.object(SYNC, 'command', return_value='[]'):
            with self.assertRaises(SYNC.SyncError):
                SYNC.discover()

    def test_source_python_compiles(self):
        with mock.patch.object(SYNC, 'ROOT', ROOT), mock.patch.object(SYNC, 'SOURCE_MAP', {
            'agent': ROOT / 'scripts/lib/autokom_ha_worker_sync.py',
            'health': ROOT / 'scripts/lib/autokom_ha_worker_remote_apply.py',
            'apply': ROOT / 'scripts/lib/autokom_private_update.py',
        }):
            digests = SYNC.checked_files()
        self.assertEqual(len(digests['agent']), 64)

    def test_gcloud_worker_ssh_scoped_to_internal_ip(self):
        member = {'name': 'autokom-ha-web-5g1f', 'zone': 'us-central1-a'}
        with mock.patch.object(SYNC, 'command', return_value='ok') as cmd:
            SYNC.remote_ssh(member, 'true')
        args = cmd.call_args.args[0]
        self.assertIn('--internal-ip', args)
        self.assertIn('--ssh-flag=-oBatchMode=yes', args)
        self.assertIn('--project=' + SYNC.PROJECT, args)
        self.assertIn('--command=true', args)

    def test_no_direct_remote_command_when_node_unstable(self):
        member = {'name': 'autokom-ha-web-5g1f', 'zone': 'us-central1-a', 'status': 'RUNNING', 'action': 'RECREATING'}
        with mock.patch.object(SYNC, 'remote_ssh') as ssh:
            self.assertEqual(SYNC.synchronize_member(member, {'agent': '0'*64, 'health': '0'*64}), 'skip')
            ssh.assert_not_called()

    def test_named_gh_user_from_update_env(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / 'update.env'
            file.write_text('AUTOKOM_UPDATE_GITHUB_USER=raph2611toky\n')
            self.assertEqual(PRIVATE._config_value('AUTOKOM_UPDATE_GITHUB_USER', path=file), 'raph2611toky')

    def test_gh_user_keeps_named_account(self):
        with mock.patch.object(PRIVATE, '_config_value', return_value='raph2611toky'), mock.patch.dict(PRIVATE.os.environ, {}, clear=True):
            self.assertEqual(PRIVATE._gh_user(), 'raph2611toky')

    def test_git_update_ha_hook_not_run_without_opt_in(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            timer = root / 'not-activated.timer'
            with mock.patch.object(PRIVATE, '_git') as git:
                PRIVATE._sync_ha_workers_after_update(root, 'old', 'new', timer=timer, production_root=root)
                git.assert_not_called()

    def test_git_update_ha_hook_only_for_worker_sources(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            timer = root / 'activated.timer'
            timer.write_text('timer')
            with mock.patch.object(PRIVATE, '_git', return_value='platform/frontend/index.html\n'), \
                 mock.patch.object(PRIVATE.subprocess, 'run') as run:
                PRIVATE._sync_ha_workers_after_update(root, 'old', 'new', timer=timer, production_root=root)
                run.assert_not_called()

    def test_git_update_ha_hook_invokes_script_on_source_change(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            script = root / 'scripts/lib/autokom_ha_worker_sync.py'
            script.parent.mkdir(parents=True)
            script.write_text('pass\n')
            timer = root / 'activated.timer'
            timer.write_text('timer')
            with mock.patch.object(PRIVATE, '_git', return_value='terraform/modules/gcp_regional_ha/files/worker-agent.py\n'), \
                 mock.patch.object(PRIVATE.subprocess, 'run', return_value=mock.Mock(returncode=0)) as run:
                PRIVATE._sync_ha_workers_after_update(root, 'old', 'new', timer=timer, production_root=root)
                self.assertEqual(run.call_args.args[0][1:], [str(script), '--apply'])


class RemoteApplyTests(unittest.TestCase):
    def test_validates_digest_before_any_service_action(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            agent = p / 'agent.py'
            health = p / 'health.py'
            agent.write_text('print("safe")\n')
            health.write_text('print("safe")\n')
            with self.assertRaises(REMOTE.WorkerSyncError):
                REMOTE.validate_payload({'agent': agent, 'health': health},
                                        {'agent': 'f'*64, 'health': 'f'*64})

    def test_rejects_syntax_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            agent = p / 'agent.py'
            health = p / 'health.py'
            agent.write_text('def unsafe(:\n')
            health.write_text('print("safe")\n')
            with self.assertRaises(SyntaxError):
                REMOTE.validate_payload({'agent': agent, 'health': health},
                                        {'agent': REMOTE.sha256(agent), 'health': REMOTE.sha256(health)})

    def test_rejects_symbolic_payload(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            agent = p / 'agent.py'
            health = p / 'health.py'
            real = p / 'source.py'
            real.write_text('pass\n')
            agent.symlink_to(real)
            health.write_text('pass\n')
            with self.assertRaises(REMOTE.WorkerSyncError):
                REMOTE.validate_payload({'agent': agent, 'health': health},
                                        {'agent': REMOTE.sha256(agent), 'health': REMOTE.sha256(health)})

    def test_atomic_replace_keeps_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            src, dst = p / 'incoming.py', p / 'installed.py'
            src.write_text('print(1)\n')
            dst.write_text('print(0)\n')
            REMOTE.replace_from(src, dst)
            self.assertEqual(dst.read_text(), 'print(1)\n')
            self.assertEqual(dst.stat().st_mode & 0o777, 0o644)

    def test_no_changes_skips_restart(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            files = {'agent': root / 'agent.py', 'health': root / 'health.py'}
            for path in files.values():
                path.write_text('pass\n')
            with mock.patch.object(REMOTE, 'FILES', files), mock.patch.object(REMOTE.os, 'geteuid', return_value=0), \
                 mock.patch.object(REMOTE, 'run', return_value='') as run:
                result = REMOTE.apply(files, {k: REMOTE.sha256(v) for k, v in files.items()})
            self.assertEqual(result['result'], 'up-to-date')
            self.assertEqual(run.call_count, 2)
            for call in run.call_args_list:
                self.assertEqual(call.args[1], 'is-active')

    def test_transaction_restarts_services_when_changed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            srcs = {'agent': root / 'next_agent.py', 'health': root / 'next_health.py'}
            dests = {'agent': root / 'agent.py', 'health': root / 'health.py'}
            for path in srcs.values():
                path.write_text('print("new")\n')
            for path in dests.values():
                path.write_text('print("old")\n')
            with mock.patch.object(REMOTE, 'FILES', dests), mock.patch.object(REMOTE, 'BACKUP_ROOT', root / 'backups'), \
                 mock.patch.object(REMOTE.os, 'geteuid', return_value=0), mock.patch.object(REMOTE, 'run', return_value='') as run:
                result = REMOTE.apply(srcs, {k: REMOTE.sha256(v) for k, v in srcs.items()})
            self.assertEqual(result['result'], 'updated')
            self.assertEqual(set(result['changed']), {'agent', 'health'})
            self.assertEqual(dests['agent'].read_text(), 'print("new")\n')
            self.assertGreaterEqual(run.call_count, 5)


if __name__ == '__main__':
    unittest.main()
