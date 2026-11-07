"""Regressions of interrupted Jenkins phase on GCP; never contacts Jenkins/ACME/host."""
import argparse
import os
from pathlib import Path
import pty
import select
import signal
import sys
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'lib'))
import services


def options(**kw):
    values = dict(db='mysql', bind='local', allow='', create_app_db=True,
                  docker_access=True, domain='jenkins.ik0m.tech', tls='certbot',
                  cert=None, key=None, email=None)
    values.update(kw)
    return argparse.Namespace(**values)


class AcmeEmailTest(unittest.TestCase):
    def test_valid_email_trimmed_and_preserved(self):
        self.assertEqual(services.valid_acme_email('  admin@example.com  '), 'admin@example.com')
        self.assertEqual(services.valid_acme_email('name+tag@ik0m.tech'), 'name+tag@ik0m.tech')

    def test_blank_and_invalid_email_not_accepted(self):
        for value in (None, '', '  ', 'not-email', 'a@@example.com', 'x@example..com',
                      'x@local', '.x@example.com', 'x..y@example.com',
                      'x@example.com\nother=line', 'admin <x@example.com>'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'e-mail'):
                services.valid_acme_email(value)

    def test_empty_answer_and_typo_retry_without_resetting_admin_choices(self):
        initial = options()
        original = (initial.db, initial.bind, initial.create_app_db,
                    initial.docker_access, initial.domain, initial.tls)
        with mock.patch.object(services, 'ask', side_effect=['', 'wrong address', 'admin@ik0m.tech']) as ask, \
             mock.patch.object(services, 'pick', side_effect=AssertionError('admin choices reset')), \
             mock.patch.object(services, 'nginx_inventory', return_value={}), \
             mock.patch.object(services, 'say'):
            result = services.interactive(initial)
        self.assertEqual(ask.call_count, 3)
        self.assertEqual(result.email, 'admin@ik0m.tech')
        self.assertEqual((result.db, result.bind, result.create_app_db,
                          result.docker_access, result.domain, result.tls), original)
        self.assertEqual(services.check_user_input(result).email, 'admin@ik0m.tech')

    def test_existing_valid_email_does_not_prompt(self):
        current = options(email='admin@ik0m.tech')
        with mock.patch.object(services, 'ask', side_effect=AssertionError('unwanted prompt')), \
             mock.patch.object(services, 'pick', side_effect=AssertionError('admin choices reset')), \
             mock.patch.object(services, 'nginx_inventory', return_value={}):
            result = services.interactive(current)
        self.assertEqual(result.email, 'admin@ik0m.tech')

    def test_invalid_preentered_address_can_be_corrected(self):
        current = options(email='wrong')
        with mock.patch.object(services, 'ask', return_value='admin@ik0m.tech') as ask, \
             mock.patch.object(services, 'pick', side_effect=AssertionError('admin choices reset')), \
             mock.patch.object(services, 'nginx_inventory', return_value={}), \
             mock.patch.object(services, 'say'):
            self.assertEqual(services.interactive(current).email, 'admin@ik0m.tech')
        ask.assert_called_once()

    def test_noninteractive_invalid_email_fails_before_host_changes(self):
        with self.assertRaisesRegex(ValueError, 'e-mail'):
            services.check_user_input(options(email=''))
        with self.assertRaisesRegex(ValueError, 'e-mail'):
            services.check_user_input(options(email='wrong'))

    def test_no_email_required_when_certbot_not_selected(self):
        self.assertEqual(services.check_user_input(options(tls='none')).domain, 'jenkins.ik0m.tech')
        self.assertEqual(services.check_user_input(options(domain='', tls='certbot')).email, None)

    def test_real_pty_blank_then_valid_is_accepted(self):
        """Reproduce sudo bash terminal /dev/tty prompt: blank Enter, then email."""
        pid, master = pty.fork()
        if pid == 0:
            try:
                value = services.prompt_acme_email()
                print('ACME_RESULT=' + value, flush=True)
                os._exit(0)
            except BaseException as exc:
                print('ACME_ERROR=' + repr(exc), flush=True)
                os._exit(2)
        output = bytearray()
        try:
            deadline = time.monotonic() + 6
            responses = [b'\r', b'admin@ik0m.tech\r']
            sent = 0
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        chunk = os.read(master, 4096)
                    except OSError:
                        break
                    output.extend(chunk)
                occurrences = output.count(b'Adresse e-mail pour Let')
                if occurrences > sent and sent < len(responses):
                    os.write(master, responses[sent])
                    sent += 1
                if b'ACME_RESULT=' in output or b'ACME_ERROR=' in output:
                    break
            else:
                self.fail('PTY input loop timed out: ' + output.decode(errors='replace')[-600:])
            done, status = os.waitpid(pid, os.WNOHANG)
            if done == 0:
                # A short grace period for child after emitting output.
                time.sleep(0.1)
                done, status = os.waitpid(pid, os.WNOHANG)
            self.assertEqual(done, pid, output.decode(errors='replace'))
            self.assertTrue(os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0,
                            output.decode(errors='replace'))
            self.assertIn(b'ACME_RESULT=admin@ik0m.tech', output)
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            os.close(master)


if __name__ == '__main__':
    unittest.main()
