import contextlib
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

HERE=Path(__file__).resolve().parents[1]/'scripts'/'lib'
sys.path.insert(0,str(HERE))

def load(name):
    spec=importlib.util.spec_from_file_location(name,HERE/f'{name}.py')
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

sys.modules.setdefault("cli_colors", types.SimpleNamespace(pretty_print=print))
security=load('autokom_security')
repair=load('autokom_mysql_repair')


class AuthRetries(unittest.TestCase):
    def test_two_errors_then_success_no_global_lock(self):
        attempts={'failures':0,'blocked_until':0}
        def read(path,default=None):
            if path==security.STATE:return {'passphrase':'dummy'}
            if path==security.ATTEMPTS:return dict(attempts)
            return default
        def write(path,value):
            if path==security.ATTEMPTS:attempts.update(value)
        with (patch.object(security,'require_root'),
              patch.object(security,'_lock',lambda: contextlib.nullcontext()),
              patch.object(security,'_read',side_effect=read),
              patch.object(security,'_write',side_effect=write),
              patch.object(security,'_tty_secret',side_effect=['bad','bad','good']) as ask,
              patch.object(security,'_verify',side_effect=lambda value,_:value=='good')):
            self.assertTrue(security.authorize('app'))
        self.assertEqual(ask.call_count,3)
        self.assertEqual(attempts['failures'],0)
        self.assertEqual(attempts['blocked_until'],0)

    def test_three_errors_counted_then_stopped(self):
        attempts={'failures':0,'blocked_until':0}
        def read(path,default=None):
            return {'passphrase':'dummy'} if path==security.STATE else dict(attempts)
        def write(path,value):
            if path==security.ATTEMPTS:attempts.update(value)
        with (patch.object(security,'require_root'),
              patch.object(security,'_lock',lambda: contextlib.nullcontext()),
              patch.object(security,'_read',side_effect=read),
              patch.object(security,'_write',side_effect=write),
              patch.object(security,'_tty_secret',return_value='bad') as ask,
              patch.object(security,'_verify',return_value=False)):
            with self.assertRaises(security.SecurityError):security.authorize('app')
        self.assertEqual(ask.call_count,3)
        self.assertEqual(attempts['failures'],3)

    def test_global_lockout_after_fifth_even_inside_three_tries(self):
        attempts={'failures':4,'blocked_until':0}
        def read(path,default=None):
            return {'passphrase':'dummy'} if path==security.STATE else dict(attempts)
        def write(path,value):
            if path==security.ATTEMPTS:attempts.update(value)
        with (patch.object(security,'require_root'),
              patch.object(security,'_lock',lambda: contextlib.nullcontext()),
              patch.object(security,'_read',side_effect=read),
              patch.object(security,'_write',side_effect=write),
              patch.object(security,'_tty_secret',return_value='bad') as ask,
              patch.object(security,'_verify',return_value=False)):
            with self.assertRaises(security.SecurityError) as ctx:security.authorize('app')
        self.assertEqual(ask.call_count,1)
        self.assertIn('Trop de tentatives',str(ctx.exception))
        self.assertEqual(attempts['failures'],5)
        self.assertGreater(attempts['blocked_until'],0)

class MySQLRepair(unittest.TestCase):
    def setUp(self):
        self.current={'DB_HOST':'127.0.0.1','DB_PORT':'3306','DB_NAME':'autokom_platform',
                     'DB_USER':'autokom','DB_PASSWORD':'wrong'}
        self.source={**self.current,'DB_PASSWORD':'correct'}
        self.payload=dict(runtime_file='/etc/autokom/platform-backend.env',
                          source_file='/etc/autokom/secrets/backend.env',
                          python='/opt/autokom/venv/bin/python',
                          load_config=lambda p: self.source if 'secrets' in str(p) else self.current,
                          save_config=Mock(),verify=Mock())

    def test_repair_refuses_when_not_tty(self):
        with patch.object(repair.os,'geteuid',return_value=0),patch.object(repair.sys.stdin,'isatty',return_value=False):
            self.assertFalse(repair.offer_mysql_repair(**self.payload))
        self.payload['save_config'].assert_not_called()

    def test_declined_repair_does_not_touch_database(self):
        with (patch.object(repair.os,'geteuid',return_value=0),
              patch.object(repair.sys.stdin,'isatty',return_value=True),
              patch.object(repair.sys.stdout,'isatty',return_value=True),
              patch.object(repair,'_yesno',return_value=False),
              patch.object(repair,'_new_dedicated_user') as create):
            self.assertFalse(repair.offer_mysql_repair(**self.payload))
            create.assert_not_called()
        self.payload['save_config'].assert_not_called()

    def test_source_success_syncs_without_sql_write(self):
        with (patch.object(repair.os,'geteuid',return_value=0),
              patch.object(repair.sys.stdin,'isatty',return_value=True),
              patch.object(repair.sys.stdout,'isatty',return_value=True),
              patch.object(repair.Path,'is_file',return_value=True),
              patch.object(repair.Path,'is_symlink',return_value=False),
              patch.object(repair,'_yesno',return_value=True),
              patch.object(repair,'_confirm_word',return_value=True),
              patch.object(repair,'_probe_mysql',return_value={'ok':True}),
              patch.object(repair.os,'chmod'),
              patch.object(repair,'_new_dedicated_user') as create):
            self.assertTrue(repair.offer_mysql_repair(**self.payload))
            create.assert_not_called()
        self.payload['save_config'].assert_called_once()
        self.assertEqual(self.payload['save_config'].call_args.args[0]['DB_PASSWORD'],'correct')

    def test_account_exists_blocks_all_sql_writes(self):
        self.source=dict(self.current)
        with (patch.object(repair.os,'geteuid',return_value=0),
              patch.object(repair.sys.stdin,'isatty',return_value=True),
              patch.object(repair.sys.stdout,'isatty',return_value=True),
              patch.object(repair.Path,'is_file',return_value=True),
              patch.object(repair.Path,'is_symlink',return_value=False),
              patch.object(repair,'_yesno',return_value=True),
              patch.object(repair,'_probe_mysql',return_value={'ok':False,'code':1045}),
              patch.object(repair,'_admin_inspect',return_value={'ok':True,'db_exists':True,'account_exists':True}),
              patch.object(repair,'_new_dedicated_user') as create):
            self.assertFalse(repair.offer_mysql_repair(**self.payload))
            create.assert_not_called()

    def test_approved_new_account_updates_after_create(self):
        self.source=dict(self.current)
        with (patch.object(repair.os,'geteuid',return_value=0),
              patch.object(repair.sys.stdin,'isatty',return_value=True),
              patch.object(repair.sys.stdout,'isatty',return_value=True),
              patch.object(repair.Path,'is_file',return_value=True),
              patch.object(repair.Path,'is_symlink',return_value=False),
              patch.object(repair,'_yesno',return_value=True),
              patch.object(repair,'_confirm_word',return_value=True),
              patch.object(repair,'_probe_mysql',return_value={'ok':False,'code':1045}),
              patch.object(repair,'_admin_inspect',return_value={'ok':True,'db_exists':True,'account_exists':False}),
              patch.object(repair,'_new_dedicated_user',return_value={'ok':True}) as create,
              patch.object(repair,'_atomic_update_source',return_value=True)):
            self.assertTrue(repair.offer_mysql_repair(**self.payload))
            create.assert_called_once()
        self.assertEqual(self.payload['save_config'].call_args.args[0]['DB_USER'],'autokom_backend')
        self.assertGreater(len(self.payload['save_config'].call_args.args[0]['DB_PASSWORD']),40)

    def test_reject_remote_mysql(self):
        self.assertFalse(repair._valid({**self.current,'DB_HOST':'db.company.com'}))


if __name__=='__main__':unittest.main()
