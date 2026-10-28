#!/usr/bin/env python3
"""v16: upstream, Node Exporter, Alertmanager SMTP and file editing security."""
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'/'lib'))
import autokom_cli as cli
import observability as o
import observability_extras as x
import tools_registry as reg
import tool_files as tf

class Upstreams(unittest.TestCase):
    def test_named_upstreams_and_kept_live_websocket(self):
        p=o.vhost_content('prom.example.com',9090,prometheus=True,gated=False)
        self.assertIn('upstream autokom_prometheus_9090',p)
        self.assertIn('server 127.0.0.1:9090;',p)
        self.assertIn('proxy_pass http://autokom_prometheus_9090;',p)
        self.assertIn('proxy_set_header Connection "";',p)
        self.assertNotIn('proxy_set_header Connection "upgrade";',p)
        g=o.vhost_content('gra.example.com',3000,prometheus=False,gated=False)
        self.assertIn('upstream autokom_grafana_3000',g)
        self.assertIn('location /api/live/',g)
        self.assertIn('Connection "upgrade";',g)

    def test_old_and_new_proxy_recognized(self):
        self.assertTrue(o.vhost_target_ok(['location / { proxy_pass http://127.0.0.1:9090; }'],9090))
        self.assertTrue(o.vhost_target_ok(['location / { proxy_pass http://autokom_prometheus_9090; }'],9090))
        self.assertFalse(o.vhost_target_ok(['proxy_pass http://other:9090;'],9090))

    def test_gated_http_no_public_proxy(self):
        text=o.vhost_content('prom.example.com',9090,prometheus=True,gated=True)
        self.assertNotIn('proxy_pass',text)
        self.assertIn('return 503',text)

class Exporter(unittest.TestCase):
    def test_local_port_and_utility(self):
        self.assertIn('127.0.0.1:9100',x.node_override())
        self.assertIn('/usr/bin/prometheus-node-exporter',x.node_override())
        self.assertIn('127.0.0.1:9093',x.alert_override())
        self.assertNotIn('0.0.0.0:',x.node_override()+x.alert_override())
        self.assertIn('--cluster.listen-address=',x.alert_override())

    def test_node_insert_and_preserve_other_jobs(self):
        old='global:\n  scrape_interval: 30s\nscrape_configs:\n- job_name: jenkins\n  static_configs:\n  - targets: ["localhost:8080"]\n'
        new=x.merge_prom_extras(old,node=True)
        self.assertIn('jenkins',new)
        self.assertIn('127.0.0.1:9100',new)
        self.assertEqual(x.merge_prom_extras(new,node=True),new)

    def test_reject_custom_node_job(self):
        raw='scrape_configs:\n- job_name: node\n  static_configs:\n  - targets: ["other.local:9100"]\n'
        with self.assertRaises(ValueError):x.merge_prom_extras(raw,node=True)

    def test_alertmanager_merge_preserves_existing(self):
        raw='scrape_configs:\n- job_name: external\n  static_configs:\n  - targets: ["other.local:9090"]\nrule_files:\n- "/etc/prometheus/keep.yml"\n'
        new=x.merge_prom_extras(raw,alerts=True)
        self.assertIn('/etc/prometheus/keep.yml',new)
        self.assertIn('127.0.0.1:9093',new)
        self.assertIn('autokom-alerts.yml',new)
        self.assertEqual(new,x.merge_prom_extras(new,alerts=True))

    def test_no_install_when_declined(self):
        with patch.object(o,'out'),patch.object(x,'update_prom_config') as up:
            x.ensure_node(o,SimpleNamespace(setup_node=False))
            x.ensure_alertmanager(o,SimpleNamespace(setup_alertmanager=False))
        up.assert_not_called()

class AlertMail(unittest.TestCase):
    def test_smtp_validation(self):
        data=x.validate_smtp('smtp.example.org',587,'login@example.org','noreply@example.org','alerts@example.org')
        self.assertEqual(data[1],587)
        for hostname,port in [('https://smtp.example.org',587),('smtp.example.org',25),('smtp..invalid',465)]:
            with self.subTest(hostname=hostname,port=port):
                with self.assertRaises(ValueError):
                    x.validate_smtp(hostname,port,'login@example.org','noreply@example.org','alerts@example.org')

    def test_no_password_in_alertmanager_config(self):
        password='DO-NOT-LEAK-EXAMPLE-PASSWORD'
        text=x.smtp_config(('smtp.example.org',587,'login@example.org','noreply@example.org','alerts@example.org',password))
        self.assertIn('auth_password_file',text)
        self.assertIn('smtp_require_tls: true',text)
        self.assertNotIn(password,text)
        self.assertNotIn('auth_password:',text)

    def test_rules_have_for_and_severity(self):
        text=x.alert_rules()
        self.assertIn('AUTOKOMNodeDown',text)
        self.assertIn('AUTOKOMJenkinsDown',text)
        self.assertIn('for: 5m',text)
        self.assertIn('severity: warning',text)

class Editing(unittest.TestCase):
    def test_alias_blocks_arbitrary_paths(self):
        with patch.object(tf,'aliases',return_value={'config':(Path('/tmp/alpha.conf'),'test')}):
            with self.assertRaises(reg.ToolError):tf.resolve_alias('prometheus','../../etc/shadow')

    def test_read_uses_alias_not_shell(self):
        with TemporaryDirectory() as td:
            p=Path(td)/'test.conf';p.write_text('first\nsecond\n',encoding='utf-8')
            with patch.object(tf,'aliases',return_value={'config':(p,'Configuration')}),patch.object(tf,'sensitive',return_value=False):
                with patch('builtins.print') as printing:
                    tf.file_operation('prometheus','cat','config')
            self.assertEqual([c.args[0] for c in printing.call_args_list],['first','second'])

    def test_reject_unknown_env_edit(self):
        with self.assertRaises(reg.ToolError):tf._env_path('ufw')
        with self.assertRaises(reg.ToolError):tf.env_manage('prometheus','set','BAD-NAME')

    def test_managed_env_roundtrip(self):
        with TemporaryDirectory() as td:
            with patch.object(tf,'MANAGED_ENV_ROOT',Path(td)):
                p=Path(td)/'prometheus.env'
                p.write_text('HELLO="world"\nTOKEN="secret value"\n',encoding='utf-8')
                self.assertEqual(tf.managed_env('prometheus'),{'HELLO':'world','TOKEN':'secret value'})

    def test_commands_both_aliases(self):
        for alias in ('tools','services'):
            for action in ('files','nano','nano-end','head','tail','cat'):
                args=cli.make_parser().parse_args([alias,'prometheus',action,'nginx-prometheus'])
                self.assertEqual(args.cmd,alias)
                self.assertEqual(args.tool_cmd,'prometheus')
                self.assertEqual(args.file_action,action)
            args=cli.make_parser().parse_args([alias,'prometheus','env','set','TOKEN'])
            self.assertEqual(args.file_extra,'TOKEN')

    def test_existing_terminal_list_style_unchanged(self):
        self.assertEqual(cli.make_parser().parse_args(['tools','list']).tool_cmd,'list')
        self.assertEqual(reg.service_state.__name__,'service_state')

    def test_ufw_uses_real_firewall_status(self):
        def proc(a,*args,**kwargs):
            return SimpleNamespace(returncode=0,stdout='Status: inactive\n')
        with patch.object(reg,'proc',side_effect=proc):
            self.assertEqual(reg.service_state(reg.get('ufw')),'inactive')

class Plan(unittest.TestCase):
    def test_stage_pending_without_exporter_and_alertmanager(self):
        state=cli.Snapshot(True,True,True,True,True,(),True,True,True,True,True,True,True)
        self.assertFalse(cli.actions(state)['observability'])
        from dataclasses import replace
        self.assertTrue(cli.actions(replace(state,node_ready=False))['observability'])
        self.assertTrue(cli.actions(replace(state,alertmanager_ready=False))['observability'])

if __name__=='__main__':unittest.main()

class AuthPortal(unittest.TestCase):
    def test_auth_request_no_basic_popup(self):
        from auth_portal import migrate_vhost
        before=o.vhost_content('prom.ik0m.tech',9090,prometheus=True,gated=False)
        after=migrate_vhost(before)
        self.assertNotIn('auth_basic_user_file',after)
        self.assertIn('auth_request /internal/autokom/authz',after)
        self.assertIn('proxy_pass http://127.0.0.1:9091/api/authz/auth-request',after)
        self.assertIn('error_page 401 =302',after)
        self.assertIn('listen 443 ssl',after)
        self.assertEqual(migrate_vhost(after),after)

    def test_auth_unknown_vhost_refused(self):
        from auth_portal import migrate_vhost
        with self.assertRaises(ValueError):
            migrate_vhost('server { location / { proxy_pass http://127.0.0.1:9090; } }')

    def test_authelia_probe_fail_closed(self):
        from auth_portal import authelia_probe
        with patch('urllib.request.OpenerDirector.open',side_effect=ConnectionRefusedError):
            self.assertFalse(authelia_probe('prometheus.ik0m.tech'))

class PreserveExisting(unittest.TestCase):
    def test_node_public_existing_requires_intervention(self):
        with patch.object(o,'package_installed',return_value=True),\
             patch.object(o,'active',return_value=True),\
             patch.object(o,'listener',return_value=['0.0.0.0:9100']),\
             patch.object(x,'NODE_OVERRIDE',SimpleNamespace(exists=lambda:False)):
            with self.assertRaises(o.SetupError):
                x.ensure_node(o,SimpleNamespace(setup_node=True))

    def test_alertmanager_external_config_not_overwritten(self):
        with patch.object(o,'package_installed',return_value=True),\
             patch.object(x,'ALERT_CONFIG',SimpleNamespace(exists=lambda:True,read_text=lambda **kwargs:'route: {}')),\
             patch.object(o,'out') as output,\
             patch.object(o,'owned_write') as write:
            x.ensure_alertmanager(o,SimpleNamespace(setup_alertmanager=True,smtp=None))
        write.assert_not_called()
        self.assertIn('conservé',str(output.call_args_list))

    def test_no_port_collision_for_selected_extras(self):
        args=SimpleNamespace(setup_node=True,setup_alertmanager=True,prometheus_domain='',grafana_domain='',mode='apply')
        with patch.object(o,'listener',side_effect=lambda p: ['0.0.0.0:9100'] if p==9100 else []),\
             patch.object(o,'active',return_value=False),\
             patch.object(o,'shutil') as sh:
            sh.which.return_value='/usr/bin/exe'
            with patch('pathlib.Path.is_file',return_value=True),\
                 patch('pathlib.Path.read_text',return_value='ID=ubuntu'):
                with self.assertRaises(o.SetupError):o.preflight(args)
