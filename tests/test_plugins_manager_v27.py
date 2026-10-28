"""V27: shared command routing, official catalog, idempotent dry-run, guardrails."""
import argparse
from pathlib import Path
import os
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/lib'))
import autokom_cli as cli
import jenkins_plugins as j
import grafana_plugins as g


def test_jenkins_search_rank_exact():
    hits=j.search({'git':{'name':'Git','excerpt':'scm'},'kubernetes':{'name':'Kubernetes','excerpt':'pipelines'}},'git')
    assert [x[1] for x in hits]==['git']


def test_jenkins_catalogue_unknown_is_refused(monkeypatch):
    monkeypatch.setattr(j,'catalogue',lambda:{'git':{'name':'Git','version':'5.0'}})
    with pytest.raises(j.PluginError,match='absent'):
        j.run(argparse.Namespace(action='install',plugin_id='fictive',apply=False,auth_file=None))


def test_jenkins_install_no_apply_does_not_call_admin(monkeypatch):
    monkeypatch.setattr(j,'catalogue',lambda:{'git':{'version':'5.0'}})
    monkeypatch.setattr(j,'ensure_admin',lambda _:pytest.fail('mutation during check'))
    assert j.run(argparse.Namespace(action='install',plugin_id='git',apply=False,auth_file=None))==0


def test_jenkins_upgrade_requires_authenticated_truth():
    with pytest.raises(j.PluginError,match='authentifié'):
        j.installed(None,strict=True)


def test_jenkins_offline_update_detection_rejected():
    with pytest.raises(j.PluginError,match='auth-file'):
        j.main(['list','--updates'])


def test_jenkins_dependency_blocks_remove():
    rows=[{'shortName':'alpha','dependencies':[]},
          {'shortName':'beta','dependencies':[{'shortName':'alpha','optional':False}]}]
    assert j.reverse_dependencies(rows,'alpha')==['beta']


def test_jenkins_optional_dependency_not_blocker():
    rows=[{'shortName':'alpha','dependencies':[]},
          {'shortName':'beta','dependencies':[{'shortName':'alpha','optional':True}]}]
    assert j.reverse_dependencies(rows,'alpha')==[]


def test_jenkins_missing_dependency_metadata_fails_closed():
    with pytest.raises(j.PluginError,match='non vérifiables'):
        j.reverse_dependencies([{'shortName':'alpha'},{'shortName':'beta'}],'alpha')


def test_jenkins_reject_bad_id(monkeypatch):
    with pytest.raises(j.PluginError,match='invalide'):
        j.run(argparse.Namespace(action='install',plugin_id='../etc/passwd',apply=False,auth_file=None))


def test_jenkins_upgrade_empty(monkeypatch):
    monkeypatch.setattr(j,'installed',lambda *a,**kw:[{'shortName':'git','hasUpdate':False}])
    assert j.run(argparse.Namespace(action='upgrade',auth_file='test',apply=False))==0


def test_jenkins_parser_subhelp(capsys):
    with pytest.raises(SystemExit) as err:j.main(['upgrade','--help'])
    assert err.value.code==0
    assert '--apply' in capsys.readouterr().out


def test_grafana_remove_preinstalled(monkeypatch):
    monkeypatch.setattr(g,'preinstalled',lambda i:True)
    monkeypatch.setattr(g,'installed',lambda :'foo-app plugin foo')
    with pytest.raises(g.PluginsError,match='preinstall'):
        g.manage_plugin('remove','foo-app',apply=False)


def test_grafana_remove_unknown(monkeypatch):
    monkeypatch.setattr(g,'preinstalled',lambda i:False)
    monkeypatch.setattr(g,'installed',lambda :'other-plugin @ 1.0')
    with pytest.raises(g.PluginsError,match='non visible'):
        g.manage_plugin('remove','foo-app',apply=False)


def test_grafana_update_no_apply_no_mutation(monkeypatch):
    monkeypatch.setattr(g,'installed',lambda :'foo-app @ 1.0')
    monkeypatch.setattr(g,'detail',lambda x:{'name':x})
    monkeypatch.setattr(g,'execute_mutation',lambda *a,**kw:pytest.fail('mutation'))
    assert g.manage_plugin('update','foo-app',apply=False)==0


def test_grafana_upgrade_no_apply(monkeypatch):
    monkeypatch.setattr(g,'installed',lambda :'foo-app @ 1.0')
    monkeypatch.setattr(g,'backup_plugins',lambda:pytest.fail('backup in check'))
    assert g.manage_plugin('upgrade',apply=False)==0


def test_grafana_updated_parser(capsys):
    with pytest.raises(SystemExit) as err:g.main(['upgrade','--help'])
    assert err.value.code==0
    assert '--apply' in capsys.readouterr().out


def test_cli_jenkins_plugins_route(monkeypatch):
    monkeypatch.setattr(j,'main',lambda args:0 if args==['list','--filter','git'] else 7)
    assert cli.main(['tools','jenkins','plugins','list','--filter','git'])==0


def test_cli_grafana_plugins_route(monkeypatch):
    monkeypatch.setattr(g,'main',lambda args:0 if args==['upgrade','--check'] else 7)
    assert cli.main(['tools','grafana','plugins','upgrade','--check'])==0


def test_cli_misspelled_plugins_alias(monkeypatch):
    monkeypatch.setattr(j,'main',lambda args:3)
    assert cli.main(['tools','jenkins','pluggins','list'])==3


def test_service_without_action_help(capsys):
    with pytest.raises(SystemExit) as result:
        cli.main(['tools','jenkins'])
    assert result.value.code==0
    output=capsys.readouterr().out
    assert 'plugins' in output


def test_legacy_files_still_parses():
    a=cli.make_parser().parse_args(['tools','jenkins','files'])
    assert a.file_action=='files' and a.tool_cmd=='jenkins'

def test_grafana_installed_table_uses_real_cli_text():
    text=g.format_installed('installed plugins:\ngrafana-lokiexplore-app @ 1.2.3\ngrafana-clock-panel @ 2.0.0','loki')
    assert 'grafana-lokiexplore-app' in text
    assert 'grafana-clock-panel' not in text


def test_jenkins_search_category_filter():
    items={'foo':{'name':'Foo','labels':['cloud']},'bar':{'name':'Foo Bar','labels':['security']}}
    assert [p[1] for p in j.search(items,'foo',category='cloud')]==['foo']
