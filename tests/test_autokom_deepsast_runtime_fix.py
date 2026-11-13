import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts/lib'), str(ROOT/'scripts/runtime')]
import autokom_deepsast_admin as admin
import autokom_deep_sast as ai
import autokom_nginx_bind_policy as policy

spec = importlib.util.spec_from_file_location('autokom_preprod_probe', ROOT/'scripts/preprod/configure.py')
preprod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preprod)


class DeepSASTRuntimeFixTests(TestCase):
    def test_install_missing_lfs_with_apt(self):
        with patch.object(admin.shutil, 'which', side_effect=[None, '/usr/bin/apt-get', '/usr/bin/git-lfs']), patch.object(admin, 'run') as run:
            admin.ensure_git_lfs()
        self.assertEqual(run.call_args_list[0].args[0], ['apt-get', 'update'])
        self.assertEqual(run.call_args_list[1].args[0][-1], 'git-lfs')

    def test_lfs_present_does_not_touch_apt(self):
        with patch.object(admin.shutil, 'which', return_value='/usr/bin/git-lfs'), patch.object(admin, 'run') as run:
            admin.ensure_git_lfs()
        run.assert_not_called()

    def test_lfs_reads_commit_pointer_without_touching_checkout(self):
        pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{admin.SHA}\nsize 437958648\n'.encode()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(admin, 'ensure_git_lfs'), patch('autokom_private_update._gh_user', return_value=''), patch.object(admin, 'run') as run:
                run.side_effect = [subprocess.CompletedProcess([], 0, str(len(pointer)).encode()),
                                   subprocess.CompletedProcess([], 0, pointer),
                                   subprocess.CompletedProcess([], 0)]
                admin.fetch_weights(root/'source', root/'model.safetensors', root/'cache')
            cmd = run.call_args.args[0]
            self.assertIn('smudge', cmd)
            self.assertIn('lfs.storage='+str(root/'cache'), cmd)
            self.assertEqual(run.call_args.kwargs['input'], pointer)
            self.assertNotIn('GIT_LFS_SKIP_SMUDGE', run.call_args.kwargs['env'])

    def test_clean_sources_still_yield_code_without_vendor_or_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'app.py').write_text('print("hello")')
            (root/'vendor').mkdir()
            (root/'vendor'/'lib.py').write_text('unused')
            (root/'shadow.py').symlink_to(root/'app.py')
            self.assertEqual(list(ai.source_samples(root, 24)), ['print("hello")'])

    def test_https_uses_ingress_ip_with_sni_and_validation(self):
        result = subprocess.CompletedProcess([], 0, '{"data":{"status":"ok"}}', '')
        with patch.object(policy, 'addresses', return_value=('10.20.0.2','127.0.0.1')), patch.object(preprod.subprocess, 'run', return_value=result) as curl:
            preprod.probe_https()
        cmd = curl.call_args.args[0]
        self.assertIn('pre.auto.ikom.mg:443:10.20.0.2', cmd)
        self.assertNotIn('-k', cmd)

    def test_stage_marker_is_recognized(self):
        sys.path.insert(0, str(ROOT/'platform/backend'))
        from apps.deployments.runs.console_stream import parse_marker
        self.assertEqual(parse_marker('14:30:29 AUTOKOM_STAGE:deepsast:SUCCESS'), 'AUTOKOM_STAGE:deepsast:SUCCESS')
