"""Import-free regression tests for the real stack history and detail API methods.

Django is not needed on Kali: compile the exact method bodies from the source
and exercise them with in-memory QuerySets that enforce project scope.
"""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'platform/backend/apps/projects/configurations/stack_views.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf8'))


def method(class_name, name='get'):
    cls = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    fn = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    mod = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    return compile(mod, str(SOURCE), 'exec')


class Query:
    def __init__(self, items): self.items = list(items)
    def filter(self, **kwargs):
        data = self.items
        if 'environment' in kwargs: data = [x for x in data if x.environment == kwargs['environment']]
        if 'backend_stack_execution__isnull' in kwargs:
            data = [x for x in data if not x.is_child]
        if 'stack__project' in kwargs: data = [x for x in data if x.stack.project == kwargs['stack__project']]
        if 'pk' in kwargs: data = [x for x in data if x.pk == kwargs['pk']]
        if 'project' in kwargs: data = [x for x in data if x.project == kwargs['project']]
        if 'slug' in kwargs: data = [x for x in data if x.slug == kwargs['slug']]
        return Query(data)
    def select_related(self, *args): return self
    def prefetch_related(self, *args): return self
    def order_by(self, *args): return Query(sorted(self.items, key=lambda x:(x.created_at,str(x.id)),reverse=True))
    def count(self): return len(self.items)
    def first(self): return self.items[0] if self.items else None
    def none(self): return Query([])
    def __getitem__(self, idx): return self.items[idx]


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.project = SimpleNamespace(id='project',repository_name='owner/project')
        self.production = SimpleNamespace(project=self.project, slug='production')
        self.now = datetime(2026,11,9,12,tzinfo=timezone.utc)
        self.requests = SimpleNamespace(user='authorized', query_params={'page':'1','page_size':'20'})
        self.legacy = [SimpleNamespace(id='old',pk='old',environment=self.production,is_child=False,
                          created_at=self.now-timedelta(minutes=10),status='SUCCEEDED',branch='main')]
        self.child = SimpleNamespace(id='front',pk='front',environment=self.production,is_child=True,
                          created_at=self.now+timedelta(minutes=4),status='RUNNING')
        backend = SimpleNamespace(id='backend-run', environment_id='backend-env', status='PREPARED',trigger_type='MANUAL',sca_report={},sast_report={})
        self.group = SimpleNamespace(id='group',pk='group',created_at=self.now,
            stack=SimpleNamespace(project=self.project,frontend_environment_id='frontend-env'),
            backend_run=backend,backend_run_id=backend.id,frontend_run=None,frontend_run_id=None,
            status='PREPARING',error_code='',finished_at=None)
        self.scoped_projects = []
        self.context = {
            'get_project': self.get_project,
            'active_group_runs': lambda project: set(),
            'group_release_status': lambda run, active: '',
            'int_param': lambda request,name,default,maximum: int(request.query_params.get(name,default)),
            'Environment': SimpleNamespace(objects=Query([self.production])),
            'DeploymentRun': SimpleNamespace(objects=Query([*self.legacy,self.child])),
            'StackDeployment': SimpleNamespace(objects=Query([self.group])),
            'DeploymentRunSerializer': lambda run: SimpleNamespace(data={'id':run.id,'status':run.status,'created_at':run.created_at}),
            'api_success': lambda data,message: data,
            'handle_error': lambda err,code: (_ for _ in ()).throw(err),
            'NotFound': ValueError,
        }

    def get_project(self, user, project_id):
        self.assertEqual(user,'authorized')
        self.assertEqual(project_id,'project')
        self.scoped_projects.append(project_id)
        return self.project

    def run_method(self, name, **kwargs):
        context = dict(self.context)
        exec(method(name), context)
        return context['get'](object(),self.requests,'project',**kwargs)

    def test_project_feed_deduplicates_child_and_keeps_single_run(self):
        feed = self.run_method('StackPublicationHistoryAPIView')
        self.assertEqual(feed['count'],2)
        self.assertEqual([(x['id'],x['kind']) for x in feed['runs']],[('group','stack'),('old','single')])
        self.assertEqual(feed['runs'][0]['backend_status'],'PREPARED')
        self.assertEqual(feed['runs'][0]['frontend_status'],'PENDING')
        self.assertTrue(self.scoped_projects)

    def test_five_old_deployments_do_not_hide_completed_backend_and_frontend_group(self):
        self.legacy.extend(SimpleNamespace(
            id=f'legacy-{i}',pk=f'legacy-{i}',environment=self.production,is_child=False,
            created_at=self.now-timedelta(hours=i+1),status='SUCCEEDED',branch='main'
        ) for i in range(4))
        self.context['DeploymentRun'] = SimpleNamespace(objects=Query([*self.legacy,self.child]))
        self.group.status = 'SUCCEEDED'
        self.group.finished_at = self.now + timedelta(minutes=10)
        self.group.backend_run.status = 'SUCCEEDED'
        self.group.frontend_run = SimpleNamespace(
            id='frontend-run',environment_id='frontend-env',status='SUCCEEDED',sca_report={},sast_report={})
        feed = self.run_method('StackPublicationHistoryAPIView')
        self.assertEqual(feed['count'], 6)
        self.assertEqual(feed['runs'][0]['kind'], 'stack')
        self.assertEqual(feed['runs'][0]['status'], 'SUCCEEDED')
        self.assertEqual(feed['runs'][0]['backend_status'], 'SUCCEEDED')
        self.assertEqual(feed['runs'][0]['frontend_status'], 'SUCCEEDED')
        self.assertEqual(sum(row['kind']=='single' for row in feed['runs']), 5)

    def test_feed_page_two_is_stable(self):
        self.requests.query_params = {'page':'2','page_size':'1'}
        feed = self.run_method('StackPublicationHistoryAPIView')
        self.assertEqual(feed['count'],2)
        self.assertEqual([item['id'] for item in feed['runs']],['old'])
        self.requests.query_params = {'page':'3','page_size':'1'}
        self.assertEqual(self.run_method('StackPublicationHistoryAPIView')['runs'],[])

    def test_group_detail_provides_real_child_and_environment_without_inventing_frontend(self):
        info = self.run_method('StackDeploymentDetailAPIView',stack_run_id='group')['deployment']
        self.assertEqual(info['backend']['run_id'],'backend-run')
        self.assertEqual(info['backend']['environment_id'],'backend-env')
        self.assertIsNone(info['frontend']['run_id'])
        self.assertEqual(info['frontend']['environment_id'],'frontend-env')
        self.assertEqual(info['frontend']['status'],'PENDING')

    def test_view_keeps_isclientuser_and_scopes_to_project(self):
        for name in ('StackDeploymentDetailAPIView','StackPublicationHistoryAPIView'):
            cl = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == name)
            self.assertIn('IsClientUser',ast.unparse(cl))
            self.assertIn('get_project(request.user, project_id)',ast.unparse(cl))
        routing = (ROOT/'platform/backend/apps/projects/configurations/urls.py').read_text()
        self.assertIn("stack/history/",routing)


if __name__ == '__main__': unittest.main()
