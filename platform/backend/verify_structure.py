"""Validation structurelle sans importer Django ; les tests de migrations exigent MySQL."""
from __future__ import annotations
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
inventory = json.loads((ROOT / 'model_inventory.json').read_text())
settings_ast = ast.parse((ROOT / 'config/settings.py').read_text())
apps = next(node.value for node in settings_ast.body if isinstance(node,ast.Assign)
    and any(isinstance(t,ast.Name) and t.id=='LOCAL_APPS' for t in node.targets))
installed = ast.literal_eval(apps)
assert len(installed) == 21, f'Expected 21 concrete Django applications: {len(installed)}'
assert len(inventory) == 67
assert len({x['model'] for x in inventory})==67
all_labels = {x['app'] for x in inventory}
assert len(all_labels)==21
model_to_label = {x['model']: x['app'] for x in inventory}
model_paths = {x['module'] for x in inventory}
for app_path in installed:
    mod, config = app_path.rsplit('.',1)
    directory = ROOT / mod.removesuffix('.apps').replace('.','/')
    assert (directory/'apps.py').is_file(), app_path
    app_data=(directory/'apps.py').read_text()
    app_tree=ast.parse(app_data)
    assert any(isinstance(n,ast.ClassDef) and n.name == config for n in app_tree.body)
    assert f"name = '{mod.removesuffix('.apps')}'" in app_data, app_path
    for file in ['models.py','admin.py','apps.py','views.py','serializers.py','urls.py',
                 'permissions.py','services.py','tests.py','swagger/__init__.py','migrations/__init__.py']:
        assert (directory/file).is_file(), f'{app_path} missing {file}'
for source in model_paths:
    file=ROOT/(source.replace('.','/')+'.py')
    tree=ast.parse(file.read_text())
    defs={n.name:n for n in tree.body if isinstance(n,ast.ClassDef)}
    for item in [x for x in inventory if x['module']==source]:
        assert item['model'] in defs, item
        m=defs[item['model']]
        assert any(isinstance(n,ast.FunctionDef) and n.name=='__str__' for n in m.body), item
        assert any(isinstance(n,ast.ClassDef) and n.name=='Meta' for n in m.body), item
    for old_label, model in re.findall(r"['\"](projects|deployments|infrastructure|assistance)\.([A-Z]\w*)['\"]",file.read_text()):
        assert old_label == model_to_label[model], (file,old_label,model)
for p in (ROOT/'apps').rglob('*.py'):
    assert '/models/' not in p.as_posix(), p
    assert 'TimestampedUUIDModel' not in p.read_text(), p
    ast.parse(p.read_text(),filename=str(p))
for p in ROOT.rglob('*.py'):
    ast.parse(p.read_text(),filename=str(p))
assert "django.db.backends.mysql" in (ROOT/'config/settings.py').read_text()
assert "drf_yasg" in (ROOT/'config/urls.py').read_text()
print(f'OK: {len(installed)} real apps, {len(inventory)} models, complete skeleton, absolute model refs, MySQL/drf-yasg')
