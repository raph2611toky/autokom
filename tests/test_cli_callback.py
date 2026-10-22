"""Smoke test with mocked Ansible CallbackBase; makes no infrastructure changes."""
import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path

base = types.ModuleType('ansible')
plugins = types.ModuleType('ansible.plugins')
callback = types.ModuleType('ansible.plugins.callback')

class Display:
    def display(self, line):
        print(line)

class CallbackBase:
    def __init__(self):
        self._display = Display()

callback.CallbackBase = CallbackBase
sys.modules.update({'ansible': base, 'ansible.plugins': plugins, 'ansible.plugins.callback': callback})
path = Path(__file__).resolve().parents[1] / 'ansible/plugins/callback/autokom.py'
spec = importlib.util.spec_from_file_location('autokom_test_plugin', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class Task:
    def __init__(self, name): self.name = name
    def get_name(self): return self.name

class Result:
    def __init__(self, data): self._result = data

with tempfile.NamedTemporaryFile() as log:
    os.environ['AUTOKOM_LOG_FILE'] = log.name
    os.environ['AUTOKOM_CHECK_MODE'] = '1'
    cli = module.CallbackModule()
    cli.v2_playbook_on_play_start(Task('Foundation'))
    cli.v2_playbook_on_task_start(Task('Installer Git'), False)
    cli.v2_runner_on_ok(Result({'changed': True}))
    cli.v2_playbook_on_task_start(Task('Masquer secrets'), False)
    cli.v2_runner_on_ok(Result({'changed': False, '_ansible_no_log': True, 'secret': 'should-not-appear'}))
    cli.v2_playbook_on_task_start(Task('Nikto'), False)
    cli.v2_runner_on_failed(Result({'msg': 'repo universe missing'}), ignore_errors=True)
    cli.v2_playbook_on_stats(None)
    contents = Path(log.name).read_text()
    assert 'should-not-appear' not in contents
    assert cli.counts['changed'] == 1 and cli.counts['warn'] == 1
print('AUTOKOM CLI callback smoke test: PASS')
