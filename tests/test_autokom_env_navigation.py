"""Tests du sélecteur AUTOKOM, sans TTY ni secrets."""
import sys
import types
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'lib'))
import autokom_env_manager as em


class EnvNavigationTests(unittest.TestCase):
    def test_backend_many_variables_uses_paginated_menu(self):
        calls = []
        fake = types.SimpleNamespace(
            pick=lambda *a, **kw: self.fail('pick ne supporte pas 40 choix'),
            pick_paginated=lambda *a, **kw: (calls.append((a, kw)) or 34))
        with mock.patch.dict(sys.modules, {'menu': fake}), mock.patch.object(em, 'tty'):
            picked = em.select('Modifier backend', ['ENV_'+str(n) for n in range(40)])
        self.assertEqual(picked, 'ENV_33')
        self.assertEqual(calls[0][1]['page_size'], 9)

    def test_short_menu_preserves_keyboard_selection(self):
        fake = types.SimpleNamespace(
            pick=lambda *a, **kw: 2,
            pick_paginated=lambda *a, **kw: self.fail('Menu paginé inutile'))
        with mock.patch.dict(sys.modules, {'menu': fake}), mock.patch.object(em, 'tty'):
            self.assertEqual(em.select('Variables frontend', ['FRONTEND_URL', 'BACKEND_URL']), 'BACKEND_URL')

    def test_cancel_from_paginated_menu_does_not_edit(self):
        fake = types.SimpleNamespace(pick_paginated=lambda *a, **kw: 0, pick=None)
        with mock.patch.dict(sys.modules, {'menu': fake}), mock.patch.object(em, 'tty'):
            with self.assertRaises(em.EnvError):
                em.select('Variables backend', ['X'+str(i) for i in range(40)])


if __name__ == '__main__':
    unittest.main()
