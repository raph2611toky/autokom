"""Security regressions: customer payloads, broker port binding, and CLI diagnostics."""
import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]

def module(relative,name):
    spec=importlib.util.spec_from_file_location(name, ROOT/relative)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
broker=module('scripts/lib/autokom_platform_broker.py','broker_ports')
admin=module('scripts/lib/autokom_runtime_admin.py','runtime_doctor')

class DeploymentHardeningTests(TestCase):
    def test_allocated_port_from_docker_is_loopback_only(self):
        raw='{"8080/tcp":[{"HostIp":"127.0.0.1","HostPort":"41352"}]}'
        with patch.object(broker,'run',return_value=raw) as cmd:
            self.assertEqual(broker._docker_loopback_port('autokom-'+'a'*20,8080),41352)
        self.assertIn('container',cmd.call_args.args[0])
        self.assertIn('inspect',cmd.call_args.args[0])

    def test_refuse_public_and_duplicate_bindings(self):
        for raw in ('{"8080/tcp":[{"HostIp":"0.0.0.0","HostPort":"45000"}]}',
                    '{"8080/tcp":[{"HostIp":"127.0.0.1","HostPort":"5000"},{"HostIp":"::","HostPort":"5001"}]}',
                    '{"8080/tcp":[]}', '{"8080/tcp":[{"HostIp":"127.0.0.1","HostPort":"80"}]}'):
            with self.subTest(raw=raw),patch.object(broker,'run',return_value=raw):
                with self.assertRaises(broker.BrokerError):broker._docker_loopback_port('autokom-'+'b'*20,8080)

    def test_port_is_not_selected_by_scanning(self):
        source=(ROOT/'scripts/lib/autokom_platform_broker.py').read_text()
        self.assertNotIn("'--publish',f'127.0.0.1::{port}'",source)
        self.assertNotIn('_port_available()',source)
        self.assertIn('relay = ingress.create(',source)
        self.assertIn("external_port = relay['port']",source)
        relay=(ROOT/'scripts/lib/autokom_internal_ingress.py').read_text()
        self.assertIn('listen 127.0.0.1:',relay)
        self.assertNotIn('--publish',relay)

    def test_customer_api_does_not_reveal_ports_or_ci_provider(self):
        config=(ROOT/'platform/backend/apps/projects/configurations/serializers.py').read_text()
        runs=(ROOT/'platform/backend/apps/deployments/runs/serializers.py').read_text()
        self.assertNotIn("'output_directory', 'internal_port'",config)
        self.assertNotIn('internal_port = serializers.IntegerField',config)
        self.assertIn("attrs['internal_port'] = (8000 if", config)
        self.assertIn("and profile in backend_profiles else 8080)", config)
        self.assertNotIn("'tool_name'",runs)
        self.assertNotIn("'ci_provider'",runs)

    def test_doctor_uses_real_autokom_identity_for_socket(self):
        source=(ROOT/'scripts/lib/autokom_runtime_admin.py').read_text()
        self.assertIn("['runuser','-u','autokom'",source)
        self.assertIn("sub.add_parser('doctor'",source)
