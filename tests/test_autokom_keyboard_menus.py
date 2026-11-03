"""Tests : tous les choix de l’app backend passent par le menu partagé."""
import sys
from pathlib import Path
from types import ModuleType
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import autokom_admin_manager as admin
import autokom_app_manager as backend
import autokom_mysql_repair as mysql


def mock_menu(selection):
    module = ModuleType('menu')
    calls = []
    def pick(*args, **kwargs):
        calls.append((args, kwargs))
        return selection
    module.pick = pick
    return module, calls


class KeyboardMenus(TestCase):
    def test_admin_menu_selects_through_autokom_pick(self):
        menu, calls = mock_menu(3)
        with patch.dict(sys.modules, {'menu': menu}), patch.object(admin, '_interactive', return_value=True):
            self.assertEqual(admin._menu(), 'reset-password')
        self.assertEqual(calls[0][1]['cancel_label'], 'Quitter sans modification')

    def test_admin_cancel_keeps_state(self):
        menu, _ = mock_menu(0)
        with patch.dict(sys.modules, {'menu': menu}), patch.object(admin, '_interactive', return_value=True):
            self.assertIsNone(admin._menu())

    def test_admin_requires_tty_without_subcommand(self):
        with patch.object(admin, '_interactive', return_value=False):
            with self.assertRaises(admin.AdminError): admin._menu()

    def test_disable_defaults_to_no(self):
        menu, calls = mock_menu(1)
        with patch.dict(sys.modules, {'menu': menu}):
            self.assertFalse(admin._confirm_account_change('disable', 'admin@example.org'))
        self.assertEqual(calls[0][1]['default'], 1)

    def test_enable_confirmation_requires_yes(self):
        menu, _ = mock_menu(2)
        with patch.dict(sys.modules, {'menu': menu}):
            self.assertTrue(admin._confirm_account_change('enable', 'admin@example.org'))

    def test_setup_choose(self):
        menu, calls = mock_menu(2)
        with patch.dict(sys.modules, {'menu': menu}):
            self.assertEqual(backend._choose('Mode', [('systemd','Système'),('docker','Docker')], 'systemd'), 'docker')
        self.assertEqual(calls[0][1]['default'], 1)

    def test_setup_choose_default_existing(self):
        menu, calls = mock_menu(1)
        with patch.dict(sys.modules, {'menu': menu}):
            backend._choose('Mode', [('systemd','Système'),('docker','Docker')], 'docker')
        self.assertEqual(calls[0][1]['default'], 2)

    def test_setup_cancel_blocks(self):
        menu, _ = mock_menu(0)
        with patch.dict(sys.modules, {'menu': menu}):
            with self.assertRaises(backend.AppError):
                backend._choose('Mode', [('systemd','Système')], 'systemd')

    def test_mysql_doctor_choice_defaults_no(self):
        menu, calls = mock_menu(1)
        with patch.dict(sys.modules, {'menu': menu}):
            self.assertFalse(mysql._yesno('Réparer ?'))
        self.assertEqual(calls[0][1]['default'], 1)

    def test_mysql_doctor_yes(self):
        menu, _ = mock_menu(2)
        with patch.dict(sys.modules, {'menu': menu}):
            self.assertTrue(mysql._yesno('Réparer ?'))
