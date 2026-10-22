"""AUTOKOM callback tests with mocked Ansible. No root privileges or modifications."""
import importlib.util
import io
import os
import sys
import tempfile
import time
import types
from contextlib import redirect_stdout
from pathlib import Path

ansible = types.ModuleType('ansible')
plugins = types.ModuleType('ansible.plugins')
callback = types.ModuleType('ansible.plugins.callback')


class Display:
    def display(self, line):
        print(line)


class CallbackBase:
    def __init__(self):
        self._display = Display()


callback.CallbackBase = CallbackBase
sys.modules.update({
    'ansible': ansible,
    'ansible.plugins': plugins,
    'ansible.plugins.callback': callback,
})
filename = Path(__file__).resolve().parents[1] / 'ansible/plugins/callback/autokom.py'
spec = importlib.util.spec_from_file_location('autokom_test_plugin', filename)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Task:
    def __init__(self, name, action='ansible.builtin.apt'):
        self.name, self.action = name, action

    def get_name(self):
        return self.name


class Result:
    def __init__(self, data):
        self._result = data


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


def check_noninteractive(logpath):
    out = io.StringIO()
    with redirect_stdout(out):
        os.environ['TERM'] = 'xterm-256color'
        os.environ['AUTOKOM_LOG_FILE'] = logpath
        os.environ['AUTOKOM_CHECK_MODE'] = '1'
        os.environ.pop('CI', None)
        inst = module.CallbackModule()
        assert not inst.live
        inst.v2_playbook_on_play_start(Task('Foundation'))
        inst.v2_playbook_on_task_start(Task('Install base'), False)
        time.sleep(0.48)
        inst.v2_runner_on_ok(Result({'changed': True, 'ansible_facts': {'ansible_env': {'PASSWORD': 'never-log'}}, 'secret': 'never-log'}))
        inst.v2_playbook_on_task_start(Task('Secrets'), False)
        inst.v2_runner_on_ok(Result({'changed': False, '_ansible_no_log': True, 'secret': 'never-log'}))
        inst.v2_playbook_on_task_start(Task('nikto'), False)
        inst.v2_runner_on_failed(Result({'msg': 'Install failed password=private-token'}), ignore_errors=True)
        inst.v2_playbook_on_stats(None)
        assert inst.counts['changed'] == 1 and inst.counts['warn'] == 1
    assert '\033[3A' not in out.getvalue()
    assert 'PRÉVU' in out.getvalue()
    assert 'never-log' not in Path(logpath).read_text()
    assert 'private-token' not in out.getvalue()
    assert 'private-token' not in Path(logpath).read_text()


def check_interactive(logpath):
    fake = FakeTTY()
    with redirect_stdout(fake):
        os.environ['AUTOKOM_LOG_FILE'] = logpath
        os.environ['AUTOKOM_CHECK_MODE'] = '0'
        inst = module.CallbackModule()
        assert inst.live
        inst.v2_playbook_on_task_start(Task('Install dependencies'), False)
        time.sleep(0.90)
        inst.v2_runner_on_ok(Result({'changed': True}))
        inst.v2_playbook_on_task_start(Task('quick check', action='ansible.builtin.stat'), False)
        inst.v2_runner_on_ok(Result({'changed': False}))
        inst.v2_playbook_on_stats(None)
    raw = fake.getvalue()
    assert raw.count('\033[32m') >= 3, 'animated green lines missing'
    assert '\033[3A' in raw, 'transient output not erased'
    assert '\033[2K' in raw, 'clear line missing'
    assert 'MODIFIÉ' in raw and 'PRÉSENT' in raw
    assert 'Temps écoulé' in raw
    assert ('\033[3A' + ('\r\033[2K\n' * 3) + '\033[3A') in raw, 'panel was not erased before permanent output'


with tempfile.TemporaryDirectory() as temp:
    check_noninteractive(str(Path(temp) / 'noninteractive.log'))
    check_interactive(str(Path(temp) / 'interactive.log'))
print('AUTOKOM callback v2: non-TTY + TTY + redaction + cleanup PASS')
