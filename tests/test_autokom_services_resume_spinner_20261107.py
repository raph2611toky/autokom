"""Regression: persist installation preferences, resume after ACME failure, safe progress."""
import argparse
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts/lib'))
import services
import autokom_cli as cli


def opts(**kw):
    obj = dict(db='mysql', bind='all', allow=[], create_app_db=True,
               docker_access=True, domain='jenkins.ik0m.tech', tls='certbot',
               email='admin@example.com', cert=None, key=None, confirm_restart=False,
               non_interactive=False)
    obj.update(kw)
    return argparse.Namespace(**obj)


class PersistAndResumeTests(unittest.TestCase):
    def test_save_load_roundtrip_and_private_mode(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'state.json'
            services.save_choices(opts(), path=p)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            saved=services.load_choices(p)
            self.assertEqual(saved['domain'], 'jenkins.ik0m.tech')
            self.assertEqual(saved['db'], 'mysql')
            self.assertTrue(saved['docker_access'])
            self.assertNotIn('confirm_restart', p.read_text())
            self.assertNotIn('password', p.read_text().lower())

    def test_no_reprompt_when_restored_even_for_no_app_db_or_empty_allow(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json'
            services.save_choices(opts(create_app_db=False, allow=[]), path=p)
            new=opts(db=None, bind=None, create_app_db=None, docker_access=None,
                     domain=None, tls=None, email=None, allow='')
            self.assertTrue(services.restore_choices(new, path=p))
            new._restored_saved = True
            with mock.patch.object(services, 'pick', side_effect=AssertionError('unexpected menu')), \
                 mock.patch.object(services, 'ask', side_effect=AssertionError('unexpected question')), \
                 mock.patch.object(services, 'nginx_inventory', return_value={}), \
                 mock.patch.object(services, 'domain_plan', return_value='new'), \
                 redirect_stdout(io.StringIO()):
                services.interactive(new)
            self.assertEqual(new.db, 'mysql')
            self.assertFalse(new.create_app_db)

    def test_persist_empty_allow_with_app_db_no_reprompt(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json'
            services.save_choices(opts(allow=[]), path=p)
            new=opts(db=None, bind=None, create_app_db=None, docker_access=None,
                     domain=None, tls=None, email=None, allow='')
            services.restore_choices(new, path=p)
            new._restored_saved = True
            with mock.patch.object(services, 'pick', side_effect=AssertionError('unexpected menu')), \
                 mock.patch.object(services, 'ask', side_effect=AssertionError('unexpected question')), \
                 mock.patch.object(services, 'nginx_inventory', return_value={}), \
                 mock.patch.object(services, 'domain_plan', return_value='new'), \
                 redirect_stdout(io.StringIO()):
                services.interactive(new)
            self.assertTrue(new.create_app_db)
            self.assertEqual(new.allow, [])

    def test_cli_value_overrides_saved_selection(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json'
            services.save_choices(opts(), path=p)
            value=opts(db='postgresql', domain='different.example.com', bind=None,
                       docker_access=None)
            services.restore_choices(value, path=p)
            self.assertEqual(value.db, 'postgresql')
            self.assertEqual(value.domain, 'different.example.com')
            self.assertTrue(value.docker_access)

    def test_unsafe_permissions_and_symlink_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json'
            services.save_choices(opts(), path=p)
            p.chmod(0o644)
            with self.assertRaises(RuntimeError): services.load_choices(p)
            p.chmod(0o600)
            link=Path(d)/'link'
            link.symlink_to(p)
            with self.assertRaises(RuntimeError): services.load_choices(link)
            with self.assertRaises(RuntimeError): services.save_choices(opts(), path=link)

    def test_incomplete_marker_means_restart_services_even_with_jenkins_url(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            complete=base/'services.complete'
            incomplete=base/'services.incomplete'
            override=base/'jenkins.conf'
            location=base/'location.groovy'
            override.write_text('# Managed by AUTOKOM: phase-2\n')
            location.write_text('configured')
            self.assertFalse(cli.services_setup_pending(completed=complete,incomplete=incomplete,override=override,location=location))
            incomplete.write_text('unfinished')
            self.assertTrue(cli.services_setup_pending(completed=complete,incomplete=incomplete,override=override,location=location))
            complete.write_text('done')
            self.assertFalse(cli.services_setup_pending(completed=complete,incomplete=incomplete,override=override,location=location))

    def test_restart_uses_spinner_and_emits_private_structured_log(self):
        with tempfile.TemporaryDirectory() as d:
            old=services.EVENT_LOG
            services.EVENT_LOG=Path(d)/'steps.jsonl'
            services.EVENT_LOG.touch(mode=0o600)
            events=[]
            class ActivitySpy:
                def __init__(self,label,**kwargs): events.append(('created',label))
                def __enter__(self): events.append(('begin','')); return self
                def __exit__(self,*exc): events.append(('end',''))
            try:
                with mock.patch.object(services,'Activity',ActivitySpy), \
                     mock.patch.object(services.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout='',stderr='')), \
                     redirect_stdout(io.StringIO()):
                    services.run(['systemctl','restart','jenkins'])
                self.assertEqual([x[0] for x in events], ['created','begin','end'])
                rows=[json.loads(x) for x in services.EVENT_LOG.read_text().splitlines()]
                self.assertEqual([r['status'] for r in rows],['running','succeeded'])
                self.assertEqual(rows[0]['component'],'service:jenkins')
            finally:
                services.EVENT_LOG=old

    def test_reconfigure_exposed_in_cli(self):
        p=cli.make_parser()
        self.assertTrue(p.parse_args(['install','--reconfigure']).reconfigure)

if __name__=='__main__': unittest.main()
