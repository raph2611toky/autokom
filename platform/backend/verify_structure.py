#!/usr/bin/env python3
"""Contrôles statiques hors Django ; pas de base nécessaire."""
import ast
import re
from pathlib import Path

root = Path(__file__).resolve().parent
apps = sorted((root / 'apps').rglob('apps.py'))
assert len(apps) == 17, f'17 apps attendues, {len(apps)} trouvées'
count = 0
tables = set()
for app_file in apps:
    folder = app_file.parent
    for name in ('models.py','admin.py','serializers.py','views.py','urls.py','permissions.py','services.py', 'tests.py', 'migrations/__init__.py', 'swagger/__init__.py'):
        assert (folder / name).exists(), f'{folder}: {name} manquant'
    ast.parse((folder/'models.py').read_text())
    t = ast.parse((folder/'models.py').read_text())
    for node in t.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name not in ('Administrator',) and not any(isinstance(b, ast.Attribute) and b.attr == 'Model' for b in node.bases):
            continue
        assert any(isinstance(n,ast.FunctionDef) and n.name == '__str__' for n in node.body), node.name
        count += 1
        match=re.search(r"db_table\s*=\s*['\"]([^'\"]+)['\"]",ast.get_source_segment((folder/'models.py').read_text(),node))
        assert match, f'{node.name} missing table'
        assert match.group(1) not in tables, match.group(1)
        tables.add(match.group(1))
assert count == 27, count
assert not (root/'apps/infrastructure').exists()
assert (root/'apps/plans/catalog/models.py').exists()
print(f'OK : {len(apps)} apps, {count} models, {len(tables)} tables uniques, 0 app infrastructure')
