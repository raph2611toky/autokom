"""Guard new direct-overlay delivery: no need for root, external APIs or VMs."""
from __future__ import annotations
from contextlib import redirect_stdout
import io
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/repair'))
# Some legacy tests monkeypatch sys.modules['cli_colors'] without restoring it.
# Load the actual implementation under a private name to avoid order pollution.
COLOR_PATH = Path(__file__).resolve().parents[1] / 'scripts/lib/cli_colors.py'
COLOR_SPEC = importlib.util.spec_from_file_location('autokom_color_under_test_20261108', COLOR_PATH)
colors = importlib.util.module_from_spec(COLOR_SPEC)
COLOR_SPEC.loader.exec_module(colors)
import logging_runtime as lr
import autokom_recover as recover


class ColorRegression(unittest.TestCase):
    class TTY(io.StringIO):
        def isatty(self): return True

    def test_recovery_statuses_are_multicolored_only_in_tty(self):
        buf = self.TTY()
        with patch.dict(os.environ, {'TERM':'xterm-256color'}, clear=True):
            colors.pretty_print('  Backend Django : ✓ prêt ; Journaux : ✗ ERREUR', file=buf)
        value = buf.getvalue()
        self.assertIn(colors.PALETTE['heading'], value)
        self.assertIn(colors.PALETTE['ok'], value)
        self.assertIn(colors.PALETTE['error'], value)
        self.assertIn('Backend Django', value)

    def test_redirected_logs_and_no_color_remain_plain(self):
        buf = io.StringIO()
        colors.pretty_print('  Frontend : ✓ prêt', file=buf)
        self.assertEqual(buf.getvalue(), '  Frontend : ✓ prêt\n')
        buf = self.TTY()
        with patch.dict(os.environ, {'TERM': 'xterm', 'NO_COLOR':'1'}):
            colors.pretty_print('  Frontend : ✓ prêt', file=buf)
        self.assertNotIn('\x1b', buf.getvalue())


class NativePaths(unittest.TestCase):
    def test_services_get_traversable_paths_outside_django(self):
        self.assertNotEqual(lr.NATIVE, lr.CONFIG_DIR)
        self.assertEqual(lr.DATA, Path('/var/lib/autokom-loki'))
        self.assertEqual(lr.CONFIG_DIR, Path('/etc/autokom-logging'))
        with patch.object(lr, 'DATA', Path('/tmp/autokom-loki-test')):
            native = lr.native_files()
            self.assertIn('/tmp/autokom-loki-test', native['loki.yaml'])
            self.assertIn('127.0.0.1:3100', native['config.alloy'])

    def test_legacy_data_copy_preserves_original(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            old=root/'old'; new=root/'new'; old.mkdir()
            (old/'chunk').write_text('preserve')
            with patch.object(lr,'LEGACY_DATA',old), patch.object(lr,'DATA',new):
                with redirect_stdout(io.StringIO()):lr.prepare_native_data()
            self.assertEqual((new/'chunk').read_text(),'preserve')
            self.assertEqual((old/'chunk').read_text(),'preserve')

    def test_refuses_data_merge_and_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); old=root/'old'; new=root/'new'
            old.mkdir();new.mkdir()
            (old/'chunk').write_text('a');(new/'chunk').write_text('b')
            with patch.object(lr,'LEGACY_DATA',old),patch.object(lr,'DATA',new):
                with self.assertRaisesRegex(lr.RuntimeErrorSafe,'fusion interdite'):
                    lr.prepare_native_data()
            (new/'chunk').unlink()
            (old/'link').symlink_to('/etc/shadow')
            with patch.object(lr,'LEGACY_DATA',old),patch.object(lr,'DATA',new):
                with self.assertRaisesRegex(lr.RuntimeErrorSafe,'Lien symbolique'):
                    lr.prepare_native_data()

    def test_native_units_reference_public_configs(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            native=d/'old-config'; native.mkdir()
            conf=d/'public-config'; data=d/'data'; olddata=d/'old-data'
            out={}
            def safe_save(path,content,mode=0o644):
                p=Path(path)
                out[str(p)]=content
                if not str(p).startswith('/etc/systemd/'):
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(content)
            with patch.object(lr,'NATIVE',native),patch.object(lr,'CONFIG_DIR',conf),\
                 patch.object(lr,'DATA',data),patch.object(lr,'LEGACY_DATA',olddata),\
                 patch.object(lr,'secure_save',side_effect=safe_save),\
                 patch.object(lr,'cmd', return_value=Mock(returncode=0)),\
                 patch.object(lr.shutil,'which',side_effect=lambda s:'/usr/bin/'+s):
                with redirect_stdout(io.StringIO()):lr.prepare_native()
            self.assertIn(str(conf/'loki.yaml'), out['/etc/systemd/system/loki.service.d/90-autokom.conf'])
            self.assertIn(str(conf/'config.alloy'), out[str(native/'alloy.env')])
            self.assertNotIn('/etc/autokom/logs-native/loki.yaml',out['/etc/systemd/system/loki.service.d/90-autokom.conf'])
            self.assertEqual(conf.stat().st_mode & 0o777,0o755)


class HTTPSRegression(unittest.TestCase):
    def test_existing_partition_skips_change(self):
        with patch('autokom_nginx_bind_policy.addresses',return_value=('10.128.0.4','127.0.0.1')) ,\
             patch('repair_nginx_443_bind.main_ipv4',return_value=('10.128.0.4','ens4')),\
             patch('repair_nginx_443_bind.run', return_value='# configuration file /etc/nginx/nginx.conf:\n'),\
             patch('repair_nginx_443_bind.plan',side_effect=Exception('Aucune directive IPv4 listen 443 globale trouvée')):
            # Unknown errors must fail safely rather than claiming HTTPS fixed.
            with self.assertRaises(Exception):recover.repair_https_partition()


if __name__=='__main__': unittest.main()
