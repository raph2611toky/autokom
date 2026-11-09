"""Prevent drift between StackDeployment model indexes and its initial migration.

Pure stdlib test: runs without Django or a database, including CI fast checks.
Django's `makemigrations --check --dry-run` remains the integration gate.
"""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / 'platform/backend/apps/deployments/runs/models.py'
MIGRATION = ROOT / 'platform/backend/apps/deployments/runs/migrations/0004_stackdeployment.py'


def indexes_with_names(source: Path):
    root = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
    result = []
    for node in ast.walk(root):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != 'Index':
            continue
        kw = {keyword.arg: keyword.value for keyword in node.keywords}
        if 'fields' not in kw or 'name' not in kw:
            continue
        fields = ast.literal_eval(kw['fields'])
        name = ast.literal_eval(kw['name'])
        result.append((tuple(fields), name))
    return result


class StackIndexConsistencyTests(unittest.TestCase):
    def test_named_stack_index_matches_initial_migration(self):
        desired = (('stack', 'created_at'), 'stack_created_idx')
        self.assertEqual(indexes_with_names(MODEL).count(desired), 1)
        self.assertEqual(indexes_with_names(MIGRATION).count(desired), 1)

    def test_no_unidentified_stack_index_in_model(self):
        module = ast.parse(MODEL.read_text(encoding='utf-8'))
        stack = next(node for node in module.body
                     if isinstance(node, ast.ClassDef) and node.name == 'StackDeployment')
        meta = next(node for node in stack.body
                    if isinstance(node, ast.ClassDef) and node.name == 'Meta')
        indexes = next(node.value for node in meta.body
                       if isinstance(node, ast.Assign) and
                       any(isinstance(t, ast.Name) and t.id == 'indexes' for t in node.targets))
        self.assertIsInstance(indexes, ast.List)
        self.assertEqual(len(indexes.elts), 1)
        self.assertTrue(any(k.arg == 'name' and ast.literal_eval(k.value) == 'stack_created_idx'
                            for k in indexes.elts[0].keywords))


if __name__ == '__main__':
    unittest.main()
