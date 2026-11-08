"""Offline, non-root regression tests: isolated scanner cache, exact diagnostic CLI."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from scripts.lib import autokom_runtime_integrity as ri
from scripts.lib import autokom_runtime_admin as admin


class TrivyPrivateCacheTests(unittest.TestCase):
    def test_cache_initialized_under_builder_home_not_root_cache(self):
        with TemporaryDirectory() as directory:
            home = Path(directory) / 'builder'
            home.mkdir(mode=0o700)
            cache = home / '.cache'
            target = cache / 'trivy'
            account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
            done = SimpleNamespace(returncode=0, stdout='', stderr='')
            with patch.object(ri, 'BUILDER_HOME', str(home)), \
                 patch.object(ri, 'BUILDER_CACHE', cache), \
                 patch.object(ri, 'TRIVY_CACHE', target), \
                 patch.object(ri.pwd, 'getpwnam', return_value=account), \
                 patch.object(ri, '_run', return_value=done) as run:
                self.assertTrue(ri._ensure_trivy_cache())
                self.assertEqual(target.stat().st_mode & 0o777, 0o700)
                self.assertEqual(cache.stat().st_mode & 0o777, 0o700)
                self.assertTrue(ri._ensure_trivy_cache())
                self.assertIn('TRIVY_CACHE_DIR=' + str(target), run.call_args[0][0])

    def test_symlink_private_cache_is_refused(self):
        with TemporaryDirectory() as directory:
            home=Path(directory)/'builder';home.mkdir(mode=0o700)
            destination=Path(directory)/'other';destination.mkdir()
            cache=home/'.cache';cache.symlink_to(destination, target_is_directory=True)
            account=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
            with patch.object(ri,'BUILDER_HOME',str(home)),\
                 patch.object(ri,'BUILDER_CACHE',cache),\
                 patch.object(ri,'TRIVY_CACHE',cache/'trivy'),\
                 patch.object(ri.pwd,'getpwnam',return_value=account):
                with self.assertRaisesRegex(ri.IntegrityError,'symbolique'):
                    ri._ensure_trivy_cache()

    def test_version_zero_with_unusable_cache_is_not_healthy(self):
        with TemporaryDirectory() as directory:
            missing=Path(directory)/'missing'
            done=SimpleNamespace(returncode=0,stdout='Version: 0.76.2',stderr='')
            account=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
            with patch.object(ri.shutil,'which',return_value='/usr/bin/trivy'),\
                 patch.object(ri.pwd,'getpwnam',return_value=account),\
                 patch.object(ri,'_run',return_value=done),\
                 patch.object(ri,'TRIVY_CACHE',missing):
                probe=ri._probe_builder_tool('trivy')
                self.assertFalse(probe.ok)
                self.assertEqual(probe.problem,'cache-missing-or-symlink')

    def test_runtime_cli_exposes_diagnose_and_logs_without_shell(self):
        import autokom_runtime_integrity as direct_ri
        with patch.object(admin.os,'geteuid',return_value=0),\
             patch.object(direct_ri,'trivy_diagnose',return_value=ri.ToolProbe(True,0,'ok','/usr/bin/trivy')),\
             patch.object(direct_ri,'_probe_builder_tool',return_value=ri.ToolProbe(True,0,'ok','/usr/bin/trivy')):
            self.assertEqual(admin.main(['scanners','--diagnose']),0)
        with patch.object(admin.os,'geteuid',return_value=0),\
             patch.object(admin.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(admin.main(['logs','--lines','40']),0)
            args=run.call_args[0][0]
            self.assertEqual(args[0],'journalctl')
            self.assertIn('autokom-jenkins-agent.service',args)
            self.assertIn('40',args)
        with patch.object(admin.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(admin.SetupError,'--lines'):
                admin.main(['logs','--lines','1001'])

    def test_runner_sets_private_cache_for_trivy_only(self):
        from scripts.runtime import autokom_agent_local as runner
        with patch.object(runner.subprocess,'run') as run:
            runner.execute(['trivy','--version'])
            env=run.call_args.kwargs['env']
            self.assertEqual(env['TRIVY_CACHE_DIR'],'/var/lib/autokom-jenkins-agent-v2/.cache/trivy')
        with patch.object(runner.subprocess,'run') as run:
            runner.execute(['semgrep','--version'])
            env=run.call_args.kwargs['env']
            # This module must not add a Trivy-specific environment to Semgrep.
            if 'TRIVY_CACHE_DIR' not in os.environ:
                self.assertNotIn('TRIVY_CACHE_DIR',env)

if __name__=='__main__':
    unittest.main()
