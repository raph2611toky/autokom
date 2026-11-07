"""Offline regression tests for cross-cloud metrics + safe authoritative DNS migration."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'scripts/lib'))
import domain_validation as routing
import autokom_dns as dns
import autokom_dns_core as core
import autokom_dns_migrate as migrate

ZONE='infra.ikom.mg'
KEY=b'key "autokom-infra.ikom.mg" { algorithm hmac-sha256; secret "YWJjZGVmMTIzNDU2Nzg5MA=="; };\n'


class FakeResponse:
    headers={'Metadata-Flavor':'Google'}
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*a):return None
    def read(self,n):return self.data


class TestCloudRoute(unittest.TestCase):
    def test_gcp_metadata_header_and_no_proxy(self):
        with patch.object(routing.urllib.request,'build_opener') as mk:
            mk.return_value.open.return_value=FakeResponse(b'136.116.89.163')
            self.assertEqual(routing.gcp_public_ipv4(),'136.116.89.163')
            request=mk.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('Metadata-flavor'),'Google')
            self.assertTrue(request.full_url.endswith('/access-configs/0/external-ip'))
            self.assertEqual(mk.call_args.args[0].proxies,{})

    def test_gcp_reject_missing_header(self):
        resp=FakeResponse(b'136.116.89.163')
        resp.headers={}
        with patch.object(routing.urllib.request,'build_opener') as mk:
            mk.return_value.open.return_value=resp
            self.assertIsNone(routing.gcp_public_ipv4())

    def test_auto_cloud_priority_and_aws_fallback(self):
        self.assertEqual(routing.instance_public_ipv4(gcp=lambda:'136.116.89.163',ec2=lambda:None),('GCP','136.116.89.163'))
        self.assertEqual(routing.instance_public_ipv4(gcp=lambda:None,ec2=lambda:'56.228.86.160'),('AWS','56.228.86.160'))

    def test_gcp_fqdn_matches_natif(self):
        resolver=lambda *a,**k:[(2,1,6,'',('136.116.89.163',80))]
        self.assertEqual(routing.check_direct_instance_domain('prometheus.ikom.mg',
                         metadata=lambda:('GCP','136.116.89.163'),
                         resolver=resolver,ipv6_addresses=lambda:set()),['136.116.89.163'])

    def test_gcp_conflicting_cloud_ip_fails_closed(self):
        resolver=lambda *a,**k:[(2,1,6,'',('56.228.86.160',80))]
        with self.assertRaisesRegex(routing.DomainRoutingError,'corriger DNS'):
            routing.check_direct_instance_domain('prometheus.ikom.mg',
                         metadata=lambda:('GCP','136.116.89.163'),
                         resolver=resolver,ipv6_addresses=lambda:set())
        with self.assertRaisesRegex(routing.DomainRoutingError,'diffère'):
            routing.check_direct_instance_domain('prometheus.ikom.mg',expected_ipv4='56.228.86.160',
                         metadata=lambda:('GCP','136.116.89.163'),
                         resolver=resolver,ipv6_addresses=lambda:set())


class MigrationFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.backup=self.root/'migration'
        self.backup.mkdir(mode=0o700)
        self.archive=str(self.backup/'infra-private.zip')
        self.source_state=self.root/'state-src.json'
        self.source_zone=self.root/'zone-src.db'
        self.source_key=self.root/'tsig-src.key'
        self.dest_state=self.root/'dest'/'state.json'
        self.dest_zone=self.root/'dest'/'db'
        self.dest_key=self.root/'dest'/'tsig.key'
        state={'zone':ZONE,'environment':'production','role':'primary',
               'primary_ip':'56.228.86.160','secondary_ip':None,
               'ns1':'ns1.ikom.mg','ns2':None,'serial':2026110701,
               'records':[core.normalize_record(ZONE,'A','api','198.51.100.7')]}
        self.source_state.write_text(json.dumps(state))
        self.source_zone.write_text(core.zone_text(state))
        self.source_key.write_bytes(KEY)
        self.state=state

    def export_file(self):
        with patch.object(core,'state_path',return_value=self.source_state), \
             patch.object(core,'bind_path',return_value=self.source_zone), \
             patch.object(core,'tsig_path',return_value=self.source_key), \
             patch.object(core,'need_root'):
            migrate.export_zone(ZONE,self.archive)


class TestMigration(MigrationFixture):
    def test_export_integrity_and_original_records(self):
        self.export_file()
        self.assertEqual(os.stat(self.archive).st_mode & 0o777,0o600)
        imported,key=migrate.inspect_archive(self.archive,zone=ZONE)
        self.assertEqual(imported['records'],self.state['records'])
        self.assertEqual(key,KEY)

    def test_no_overwriting_existing_archive(self):
        self.export_file()
        with patch.object(core,'state_path',return_value=self.source_state), \
             patch.object(core,'bind_path',return_value=self.source_zone), \
             patch.object(core,'tsig_path',return_value=self.source_key), \
             patch.object(core,'need_root'):
            with self.assertRaisesRegex(core.DNSError,'existe déjà'):
                migrate.export_zone(ZONE,self.archive)

    def test_reject_noncanonical_zone(self):
        self.source_zone.write_text('malicious or manually edited zone')
        with patch.object(core,'state_path',return_value=self.source_state), \
             patch.object(core,'bind_path',return_value=self.source_zone), \
             patch.object(core,'tsig_path',return_value=self.source_key), \
             patch.object(core,'need_root'):
            with self.assertRaisesRegex(core.DNSError,'différente'):
                migrate.export_zone(ZONE,self.archive)

    def test_reject_tampered_archive_and_unknown_members(self):
        import zipfile
        self.export_file()
        with zipfile.ZipFile(self.archive,'a') as z:
            z.writestr('etc/passwd','dummy')
        with self.assertRaisesRegex(core.DNSError,'quatre fichiers'):
            migrate.inspect_archive(self.archive,zone=ZONE)

    def test_check_does_not_touch_files(self):
        self.export_file()
        with patch.object(core,'state_path',return_value=self.dest_state), \
             patch.object(core,'bind_path',return_value=self.dest_zone), \
             patch.object(core,'tsig_path',return_value=self.dest_key), \
             patch.object(dns,'local_listen_ips',return_value=['10.128.0.4']):
            migrate.import_zone(zone=ZONE,archive=self.archive,public_ip='136.116.89.163',
                               listen_ip='10.128.0.4',apply=False)
        self.assertFalse(self.dest_state.exists())

    def test_refuse_existing_zone_without_overwrite(self):
        self.export_file()
        self.dest_zone.parent.mkdir()
        self.dest_zone.write_text('keep me')
        with patch.object(core,'state_path',return_value=self.dest_state), \
             patch.object(core,'bind_path',return_value=self.dest_zone), \
             patch.object(core,'tsig_path',return_value=self.dest_key), \
             patch.object(dns,'local_listen_ips',return_value=['10.128.0.4']):
            with self.assertRaisesRegex(core.DNSError,'déjà'):
                migrate.import_zone(zone=ZONE,archive=self.archive,public_ip='136.116.89.163',
                                   listen_ip='10.128.0.4',apply=True,confirmed=True)
        self.assertEqual(self.dest_zone.read_text(),'keep me')

    def test_import_gcp_primary_preserves_records_and_rotates_serial(self):
        self.export_file()
        named_options=self.root/'named.conf.options'
        named_options.write_text('options { directory "/var/cache/bind"; listen-on-v6 { any; }; };')
        managed=self.root/'named.conf.autokom'
        local=self.root/'named.conf.local'
        conf=self.root/'managed-zone.conf'
        with patch.object(core,'state_path',return_value=self.dest_state), \
             patch.object(core,'bind_path',return_value=self.dest_zone), \
             patch.object(core,'tsig_path',return_value=self.dest_key), \
             patch.object(core,'managed_zone_conf',return_value=conf), \
             patch.object(core,'MANAGED_INCLUDE',managed), \
             patch.object(core,'LOCAL_INCLUDE',local), \
             patch.object(dns,'local_listen_ips',return_value=['10.128.0.4']), \
             patch.object(dns,'_read_named_options',return_value=(named_options,named_options.read_text())), \
             patch.object(dns,'_key_owner'), patch.object(core,'check_zone'), \
             patch.object(core,'verify_named_conf'),patch.object(dns,'reload_bind'), \
             patch('shutil.which', return_value='/usr/sbin/named-checkzone'), \
             patch.object(migrate,'ensure_authoritative_ufw'),patch.object(core,'need_root'):
            migrate.import_zone(zone=ZONE,archive=self.archive,public_ip='136.116.89.163',
                               listen_ip='10.128.0.4',apply=True,confirmed=True)
            imported=json.loads(self.dest_state.read_text())
        self.assertEqual(imported['primary_ip'],'136.116.89.163')
        self.assertEqual(imported['records'],self.state['records'])
        self.assertGreater(imported['serial'],self.state['serial'])
        self.assertIn('allow-transfer { none; }',conf.read_text())
        self.assertEqual(self.dest_key.read_bytes(),KEY)

    def test_cli_migrate_parse(self):
        args=dns.parse(['migrate','import','--zone',ZONE,'--archive','/root/a.zip','--apply'])
        self.assertEqual(args.action,'import')
        self.assertTrue(args.apply)

    def test_ufw_existing_wireguard_rule_does_not_cover_wan(self):
        status='Status: active\n443/tcp on wg0 ALLOW Anywhere\n53/tcp on wg0 ALLOW Anywhere\n'
        calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if args==['ufw','status']:return Mock(returncode=0,stdout=status)
            if args[:4]==['ip','-4','route','get']:return Mock(returncode=0,stdout='1.1.1.1 via 10.128.0.1 dev ens4 src 10.128.0.4')
            return Mock(returncode=0,stdout='OK')
        with patch('shutil.which',return_value='/usr/sbin/ufw'), patch.object(migrate.subprocess,'run',side_effect=run):
            migrate.ensure_authoritative_ufw()
        self.assertEqual(len([x for x in calls if x[:2]==['ufw','allow']]),2)
        self.assertTrue(all('ens4' in x for x in calls if x[:2]==['ufw','allow']))

    def test_ufw_explicit_deny_is_not_bypassed(self):
        status='Status: active\n53/udp on ens4 DENY Anywhere\n'
        def run(args,**kwargs):
            if args==['ufw','status']:return Mock(returncode=0,stdout=status)
            return Mock(returncode=0,stdout='dev ens4')
        with patch('shutil.which',return_value='/usr/sbin/ufw'), patch.object(migrate.subprocess,'run',side_effect=run):
            with self.assertRaisesRegex(core.DNSError,'refus explicite'):
                migrate.ensure_authoritative_ufw()


if __name__=='__main__':unittest.main()

# Keep these as separate cases so authorizations do not depend on the current VM.
class TestObservabilityResume(unittest.TestCase):
    def test_root_private_choices_roundtrip_excludes_passwords(self):
        import observability as obs
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'observability-selection.json'
            args=SimpleNamespace(mode='apply',prometheus_domain='prometheus.ikom.mg',
                  grafana_domain='grafana.ikom.mg',email='admin@example.org',
                  grafana_user='AdminGrafana',prometheus_password='NOT_IN_JSON',
                  grafana_new_password='SECRET_NOT_IN_JSON')
            with patch.object(obs,'CONFIG_PENDING',path), \
                 patch('autokom_install_permissions.private_install_directory'):
                obs.save_selection(args)
                content=path.read_text()
                self.assertNotIn('NOT_IN_JSON',content)
                self.assertNotIn('SECRET_NOT_IN_JSON',content)
                self.assertEqual(os.stat(path).st_mode & 0o777,0o600)
                if os.geteuid()==0:
                    self.assertEqual(obs.previous_selection()['grafana_domain'],'grafana.ikom.mg')

    def test_check_has_no_state_side_effect(self):
        import observability as obs
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'observability-selection.json'
            args=SimpleNamespace(mode='check',prometheus_domain='prometheus.ikom.mg',
                  grafana_domain='grafana.ikom.mg',email='',grafana_user='')
            with patch.object(obs,'CONFIG_PENDING',path):
                obs.save_selection(args)
            self.assertFalse(path.exists())
