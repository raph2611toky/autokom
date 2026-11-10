import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('jenkins_spawn_doctor', ROOT / 'scripts/lib/autokom_jenkins_spawn_doctor.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class JenkinsSpawnDoctorTests(unittest.TestCase):
    def test_process_pid_from_systemd(self):
        class Result:
            returncode = 0
            stdout = '1234\n'
        self.assertEqual(mod.agent_pid(lambda *args: Result()), 1234)

    def test_refuse_empty_pid(self):
        class Result:
            returncode = 0
            stdout = '0\n'
        with self.assertRaisesRegex(RuntimeError, 'arrêté'):
            mod.agent_pid(lambda *args: Result())

    def test_no_implicit_jvm_flags_or_controller_restart(self):
        source = (ROOT / 'scripts/lib/autokom_jenkins_spawn_doctor.py').read_text()
        self.assertIn('restart\', UNIT', source)
        self.assertNotIn("restart', 'jenkins.service'", source)
        self.assertIn('--confirm-no-builds', source)
        self.assertNotIn('launchMechanism=FORK', source)

    def test_repair_requires_explicit_confirmation(self):
        from unittest.mock import Mock
        with patch.object(mod, 'agent_pid', return_value=500), \
             patch.object(mod, 'inspect', return_value={'pid':500,'java':'/usr/bin/java',
                'helper':'/usr/lib/jvm/lib/jspawnhelper','healthy':False,'reason':'stale'}), \
             patch.object(mod, 'run') as run:
            with patch('sys.argv', ['doctor','--repair']):
                self.assertEqual(mod.main(),2)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
