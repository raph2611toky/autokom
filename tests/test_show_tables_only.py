"""Non-régression: tableaux réservés à `tools/services list` et `show`."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import autokom_cli as cli
import terminal_table as tables
import tools_registry as tools


class ShowOnlyLayout(unittest.TestCase):
    def capture(self, fn, *args, **kwargs):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*args, **kwargs)
        return buf.getvalue()

    def test_catalogue_list_est_un_tableau_groupé_colorable(self):
        with mock.patch.object(tools, 'service_state', return_value='active'):
            output = self.capture(tools.list_tools)
        self.assertIn('00 · Fondations', output)
        self.assertIn('06 · Sécurité', output)
        self.assertIn('│ Outil', output)
        self.assertIn('│ État', output)
        self.assertIn('│ Fonction', output)
        self.assertIn('jenkins', output)
        self.assertIn('╭', output)
        self.assertIn('╰', output)
        self.assertNotIn('Propriété', output)

    def test_services_et_tools_list_meme_rendu(self):
        with mock.patch.object(tools,'service_state',return_value='active'):
            output_tools = self.capture(cli.main, ['tools','list'])
            output_services = self.capture(cli.main, ['services','list'])
        self.assertEqual(output_services, output_tools)
        self.assertIn('│ Outil', output_tools)

    def test_list_exclut_absents_par_defaut_et_all_les_inclut(self):
        def state(t):
            return 'active' if t.name == 'jenkins' else 'absent'
        with mock.patch.object(tools, 'service_state', side_effect=state):
            base = self.capture(tools.list_tools)
            expanded = self.capture(tools.list_tools, all_tools=True)
        self.assertIn('jenkins', base)
        self.assertNotIn('wazuh-manager', base)
        self.assertIn('wazuh-manager', expanded)
        self.assertIn('− absent', expanded)

    def test_list_une_sonde_par_outil(self):
        with mock.patch.object(tools, 'service_state', return_value='active') as probe:
            self.capture(tools.list_tools, all_tools=True)
        self.assertEqual(probe.call_count, len(tools.SPECS))

    def test_list_groupes_sans_diviseurs_entre_lignes(self):
        with mock.patch.object(tools,'service_state',return_value='active'):
            output = self.capture(tools.list_tools)
        # La première catégorie compte quatre outils et un seul trait sous les titres.
        group = output.split('00 · Fondations', 1)[1].split('01 · Réseau', 1)[0]
        self.assertEqual(group.count('├'), 1)

    def test_status_conserve_son_entete_compact_et_lignes(self):
        s=types.SimpleNamespace(
            foundation_ok=True, jenkins_installed=True, jenkins_active=True,
            jenkins_private=True, jenkins_plugins_missing=(), prometheus_active=False,
            grafana_active=False, metrics_up=False, datasource_ok=False,
            domains_ready=False, ufw_active=False,
        )
        with mock.patch.object(cli,'snapshot',return_value=s):
            output=self.capture(cli.brief, mock.sentinel.config)
        self.assertIn('Fondation système',output)
        self.assertIn('✓ conforme', output)
        self.assertNotIn('│ Phase ',output)
        self.assertNotIn('Observation',output)

    def test_install_plan_conserve_statuts_classiques(self):
        s=types.SimpleNamespace(ufw_active=True,jenkins_active=True,
            jenkins_installed=True,jenkins_private=True,jenkins_plugins_missing=())
        plan={name:False for name in cli.STAGES}
        output=self.capture(cli.print_plan,s,plan,cli.STAGES)
        self.assertIn('✓  déjà conforme, ignorer',output)
        self.assertNotIn('│ Phase ',output)

    def test_autokom_config_conserve_lignes_non_tabulees(self):
        output=self.capture(cli.main,['config'])
        self.assertIn('jenkins.metrics_url',output)
        self.assertNotIn('Valeur effective',output)
        self.assertNotIn('│ Composant ',output)

    def test_tools_change_apercu_conserve_le_style_anterieur(self):
        with mock.patch.object(tools,'load_observability'),mock.patch.object(tools,'require_root',side_effect=AssertionError('root appelé')):
            output=self.capture(tools.change,'prometheus',['port=9091'])
        self.assertIn('+ prometheus.port=9091',output)
        self.assertIn('Simulation',output)
        self.assertNotIn('│ Composant ',output)

    def test_show_en_tableaux_avec_les_fichiers_et_les_roles(self):
        with mock.patch.object(tools,'service_state',return_value='active'):
            output=self.capture(tools.show,'jenkins')
        for fragment in ('Propriété','Valeur','Présence','Chemin absolu','Rôle / explication',
                          '/var/lib/jenkins/config.xml','jenkins.service.d','Variables · noms'):
            self.assertIn(fragment,output)
        self.assertIn('╭',output)

    def test_environnements_noms_uniquement_sans_fuite(self):
        with mock.patch.object(tools,'effective_env',return_value={'JENKINS_API_TOKEN':'supersecret','HOME':'/home/jenkins'}):
            output=self.capture(tools.environments,'jenkins',values=False)
        self.assertIn('Variable',output)
        self.assertIn('JENKINS_API_TOKEN',output)
        self.assertIn('masquée',output)
        self.assertNotIn('supersecret',output)
        self.assertNotIn('/home/jenkins',output)

    def test_environnements_valeurs_root_et_confirmation_obligatoires(self):
        with mock.patch.object(tools.os,'geteuid',return_value=1000):
            with self.assertRaises(tools.ToolError):
                self.capture(tools.environments,'jenkins',values=True)
        with mock.patch.object(tools.os,'geteuid',return_value=0),\
             mock.patch.object(tools,'confirm_sensitive',return_value=False):
            with self.assertRaises(tools.ToolError):
                self.capture(tools.environments,'jenkins',values=True)

    def test_environnements_valeurs_avec_echappement_des_controles(self):
        evil='abc\x1b[2Jdef\nbonjour'
        with mock.patch.object(tools.os,'geteuid',return_value=0),\
             mock.patch.object(tools,'confirm_sensitive',return_value=True),\
             mock.patch.object(tools,'effective_env',return_value={'TOKEN':evil}):
            output=self.capture(tools.environments,'jenkins',values=True)
        self.assertIn('abc\\x1b[2Jdef\\nbonjour',output)
        self.assertNotIn(evil,output)
        self.assertIn('Données sensibles',output)

    def test_table_responsive_sans_troncature(self):
        p='/etc/systemd/system/jenkins.service.d/override.conf'
        output=tables.render_table(['État','Chemin','Explication'],[('présent',p,'override systemd')],width=49)
        self.assertEqual(''.join(tables._chunks(p,11)),p)
        for line in output.splitlines():
            self.assertLessEqual(tables.visible_width(line),49)

    def test_tableau_compact_adapte_aux_terminaux_etroits(self):
        rows = [(tables.Cell('jenkins','info'), tables.Cell('✓ active','success'), 'Automatisation CI/CD'),
                ('ansible','outil CLI','Orchestration Ansible')]
        for width in (40, 60, 95):
            with self.subTest(width=width):
                rendered=tables.render_table(['Outil','État','Fonction'], rows, width=width,
                                             row_dividers=False, stream=io.StringIO())
                self.assertEqual(rendered.count('├'), 1)
                self.assertIn('jenkins', rendered)
                for line in rendered.splitlines():
                    self.assertLessEqual(tables.visible_width(line), width)

    def test_status_dans_tableau_coloré_uniquement_en_tty(self):
        rendered=tables.render_table(['Outil','État','Fonction'],
             [('mysql',tables.Cell('✓ active','success'),'Serveur SQL')],
             width=70, color=True)
        self.assertIn('\x1b[38;2;134;239;172m', rendered)
        without=tables.render_table(['Outil','État','Fonction'],
             [('mysql',tables.Cell('✓ active','success'),'Serveur SQL')],
             width=70, color=False)
        self.assertNotIn('\x1b[', without)

    def test_no_color_sur_sortie_redirigee(self):
        output=tables.render_table(['Clé','Valeur'],[('KEY',tables.Cell('secret','warning'))],stream=io.StringIO())
        self.assertNotIn('\x1b[',output)


if __name__ == '__main__':
    unittest.main()
