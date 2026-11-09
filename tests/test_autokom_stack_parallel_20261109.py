"""Source-level regression for parallel, atomic stack builds and delayed safe commit.

The production Django worker is deliberately not imported without its DB/runtime.
The tests inspect its exact Python AST to guard deployment invariants.
"""
import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
FILES={
    'stack':ROOT/'platform/backend/apps/projects/configurations/stack_services.py',
    'worker':ROOT/'platform/backend/apps/deployments/runs/worker.py',
    'reconcile':ROOT/'platform/backend/apps/deployments/runs/stack_worker.py',
    'serializers':ROOT/'platform/backend/apps/deployments/runs/serializers.py',
}

def function(name, path, cls=None):
    tree=ast.parse(path.read_text(encoding='utf8'))
    parents=tree.body if not cls else next(c for c in tree.body if isinstance(c,ast.ClassDef) and c.name==cls).body
    return next(n for n in parents if isinstance(n,ast.FunctionDef) and n.name==name)

class StackParallelTests(unittest.TestCase):
    def test_launch_creates_two_runs_inside_single_atomic_transaction(self):
        fn=function('launch_stack',FILES['stack'])
        txs=[n for n in ast.walk(fn) if isinstance(n,ast.With) and
             any(ast.unparse(item.context_expr)=='transaction.atomic()' for item in n.items)]
        self.assertEqual(len(txs),1)
        transaction_body=txs[0]
        run_calls=[n for n in ast.walk(transaction_body) if isinstance(n,ast.Call)
                   and isinstance(n.func,ast.Name) and n.func.id=='_create_run']
        self.assertEqual(len(run_calls),2)
        self.assertTrue(any('stack.backend_environment' in ast.unparse(n) for n in run_calls))
        self.assertTrue(any('stack.frontend_environment' in ast.unparse(n) for n in run_calls))
        creations=[n for n in ast.walk(transaction_body) if isinstance(n,ast.Call)
                   and ast.unparse(n.func)=='StackDeployment.objects.create']
        self.assertEqual(len(creations),1)
        fields={arg.arg:ast.unparse(arg.value) for arg in creations[0].keywords}
        self.assertEqual(fields['backend_run'],'backend_run')
        self.assertEqual(fields['frontend_run'],'frontend_run')

    def test_frontend_jenkins_succeeds_before_backend_private_candidate_only_waits(self):
        fn=function('poll_one',FILES['worker'])
        code=ast.unparse(fn)
        self.assertIn("stack_role == 'frontend'",code)
        self.assertIn("backend_status != DeploymentRun.Status.PREPARED",code)
        self.assertIn('return False',code)
        self.assertIn('_release_docker_and_proxy',code)

    def test_worker_dispatches_two_requests_without_duplicate_retries(self):
        fn=function('run_tick',FILES['worker'])
        code=ast.unparse(fn)
        self.assertIn('for _ in range(2):',code)
        self.assertIn('if not dispatch_one(client):',code)
        self.assertIn('poll_one(client, run_id)',code)

    def test_failed_group_cancels_queued_and_running_peer(self):
        fn=function('_fail_group',FILES['reconcile'])
        code=ast.unparse(fn)
        self.assertIn("run.status == 'QUEUED'",code)
        self.assertIn("run.status == 'RUNNING'",code)
        self.assertIn('cancel_requested=True',code)
        reconciler=ast.unparse(function('reconcile_stack_deployments',FILES['reconcile']))
        self.assertIn('group.frontend_run_id is None',reconciler)
        self.assertNotIn("back == 'PREPARED' and group.frontend_run_id is None",reconciler)
        self.assertIn("back == 'PREPARED' and front == 'PREPARED'",reconciler)

    def test_serializers_show_only_whitelisted_diagnostic_ids(self):
        import re
        from types import SimpleNamespace
        for name, source, result in (
          ('get_error_message', 'Accès refusé token=private Code de diagnostic : AUTOKOM_CONTAINER_HEALTHCHECK_FAILED',
             'AUTOKOM_CONTAINER_HEALTHCHECK_FAILED'),
          ('get_jenkins_reference', 'B:27', 'Build Jenkins #27'),
        ):
            method=function(name,FILES['serializers'],cls='DeploymentRunSerializer')
            namespace={'re':re}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                         str(FILES['serializers']),'exec'),namespace)
            instance=(SimpleNamespace(error_message=source) if name=='get_error_message'
                      else SimpleNamespace(external_run_id=source))
            observed=namespace[name](None,instance)
            self.assertIn(result,observed)
            self.assertNotIn('private',observed)

    def test_journal_does_not_serialize_internal_jenkins_urls(self):
        serializer=FILES['serializers'].read_text(encoding='utf8')
        self.assertIn('jenkins_reference',serializer)
        self.assertIn("r'([BQ]):([1-9][0-9]{0,9})'",serializer)
        self.assertNotIn("'external_run_id','stages'",serializer)

    def test_existing_isolation_and_atomic_commit_remain(self):
        re=FILES['reconcile'].read_text(encoding='utf8')
        self.assertIn("'runtime.commit_pair'",re)
        self.assertIn("'runtime.rollback'",re)
        self.assertIn("AUTOKOM_STACK_COMMIT_RECONCILE_REQUIRED",re)
        jenkins=(ROOT/'scripts/runtime/Jenkinsfile.autokom-local').read_text()
        self.assertIn("agent { label 'autokom-isolated' }",jenkins)
        self.assertNotIn('disableConcurrentBuilds()',jenkins)
        self.assertIn('autokom-runner-key',jenkins)
        agent=(ROOT/'scripts/lib/autokom_jenkins_agent.py').read_text()
        self.assertIn("'numExecutors': 2",agent)
        self.assertIn('currentExecutable',agent)
        self.assertIn('Agent occupé',agent)

if __name__=='__main__':unittest.main()
