#!/usr/bin/env python3
"""Regression tests: big logos only on first real installation; clean auditing."""
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'lib'))
import tool_banner
import observability
import services


class LifeCycleTests(unittest.TestCase):
    def setUp(self):
        tool_banner._SEEN.clear()

    def test_installation_predicate(self):
        for mode, present, desired in [('check', False, False),
                                       ('check', True, False),
                                       ('apply', True, False),
                                       ('apply', False, True)]:
            with self.subTest(mode=mode, installed=present):
                self.assertEqual(tool_banner.first_install(mode, present), desired)

    def test_logo_never_in_check_or_unqualified_render(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertFalse(tool_banner.render('jenkins'))
            self.assertFalse(tool_banner.render('grafana', initial_install=False))
        self.assertEqual(out.getvalue(), '')

    def test_banners_off_even_for_first_install(self):
        with patch.dict('os.environ', {'AUTOKOM_BANNERS': 'off'}):
            self.assertFalse(tool_banner.render('prometheus', initial_install=True, force=True))

    def test_compact_section_has_frame_no_logo(self):
        with redirect_stdout(io.StringIO()) as out:
            tool_banner.section('Jenkins', 'Audit en lecture seule')
        result = out.getvalue()
        self.assertIn('╭', result)
        self.assertIn('╰', result)
        self.assertIn('AUTOKOM', result)
        self.assertIn('JENKINS', result)
        self.assertNotIn('█████', result)

    def test_observability_check_does_not_render_logos(self):
        with patch.object(observability, 'active', return_value=False), \
             patch.object(observability, 'package_installed', return_value=False), \
             patch.object(observability, 'listener', return_value=[]), \
             patch.object(observability, 'autokom_banner') as big, \
             redirect_stdout(io.StringIO()) as out:
            observability.check_only(SimpleNamespace(prometheus_domain='', grafana_domain='', mode='check'))
        big.assert_not_called()
        self.assertIn('Jenkins', out.getvalue())
        self.assertNotIn('████', out.getvalue())

    def test_missing_service_in_check_stays_small(self):
        with patch.object(observability, 'package_installed', return_value=False), \
             patch.object(observability, 'autokom_banner') as big, \
             redirect_stdout(io.StringIO()):
            observability.ensure_prometheus(SimpleNamespace(mode='check'))
            with patch.object(observability, 'previous_state', return_value={}):
                observability.ensure_grafana(SimpleNamespace(mode='check'))
        big.assert_not_called()

    def test_jenkins_package_probe_is_read_only(self):
        with patch.object(services.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='install ok installed')) as run:
            self.assertTrue(services.jenkins_package_installed())
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][:2], ['dpkg-query', '-W'])

    def test_prometheus_config_uses_custom_job(self):
        from dataclasses import replace
        current = observability.SETTINGS
        with patch.object(observability, 'SETTINGS', replace(current, jenkins_job='autokom-jenkins')):
            result = observability.prom_scrape_config()
        self.assertIn('job_name: autokom-jenkins', result)
        self.assertIn('metrics_path: /prometheus/', result)

    def test_aws_audit_does_not_depend_on_banner_output(self):
        spec = importlib.util.spec_from_file_location('autokom_test_audit', ROOT/'scripts/verify/09-check-stack.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with patch.object(tool_banner, 'render', side_effect=AssertionError('Grand logo interdit')):
            with patch.object(mod, 'unit_active', return_value=False), \
                 patch.object(mod, 'port_addresses', return_value=[]), \
                 patch.object(mod, 'local_http', return_value=(0, '')), \
                 patch.object(mod, 'command', return_value=None), \
                 redirect_stdout(io.StringIO()) as out:
                r = mod.Report()
                mod.jenkins(r, observability.SETTINGS)
        self.assertIn('02 / 03', out.getvalue())
        self.assertNotIn('█████', out.getvalue())


    def test_grafana_source_api_confirms_actual_url(self):
        cfg = observability.SETTINGS
        response = '{"type":"prometheus", "url":"%s"}' % cfg.prometheus_base
        with patch.object(observability, 'grafana_api', return_value=(200, response)) as api, \
             redirect_stdout(io.StringIO()) as printed:
            observability.verify_grafana_datasource('admin', 'hidden-token', attempts=1)
        self.assertIn(cfg.prometheus_base, printed.getvalue())
        self.assertNotIn('hidden-token', printed.getvalue())
        self.assertEqual(api.call_args.args[-1], '/api/datasources/uid/' + cfg.datasource_uid)

    def test_grafana_source_api_rejects_wrong_backend(self):
        with patch.object(observability, 'grafana_api', return_value=(200, '{"type":"prometheus", "url":"http://bad.example:9090"}')):
            with self.assertRaises(observability.SetupError):
                observability.verify_grafana_datasource('admin', 'token', attempts=1)

    def test_grafana_source_api_requires_result(self):
        with patch.object(observability, 'grafana_api', return_value=(404, '{}')):
            with self.assertRaises(observability.SetupError):
                observability.verify_grafana_datasource('admin', 'token', attempts=1)


if __name__ == '__main__':
    unittest.main()
