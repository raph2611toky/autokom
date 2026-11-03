from django.urls import path
from apps.projects.environments.views import (EnvironmentListAPIView, EnvironmentCreateAPIView,
    EnvironmentDetailAPIView, EnvironmentUpdateAPIView, EnvironmentVariablesListAPIView,
    EnvironmentVariableCreateAPIView, EnvironmentVariableUpdateAPIView, EnvironmentVariableDeleteAPIView,
    EnvironmentDomainsAPIView, EnvironmentDomainCreateAPIView, DomainReassignAPIView)

app_name = 'project_environments'
urlpatterns = [
    path('<uuid:project_id>/environments/', EnvironmentListAPIView.as_view(), name='list'),
    path('<uuid:project_id>/environments/create/', EnvironmentCreateAPIView.as_view(), name='create'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/', EnvironmentDetailAPIView.as_view(), name='detail'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/update/', EnvironmentUpdateAPIView.as_view(), name='update'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/variables/', EnvironmentVariablesListAPIView.as_view(), name='variables-list'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/variables/create/', EnvironmentVariableCreateAPIView.as_view(), name='variables-create'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/variables/<uuid:variable_id>/update/', EnvironmentVariableUpdateAPIView.as_view(), name='variables-update'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/variables/<uuid:variable_id>/delete/', EnvironmentVariableDeleteAPIView.as_view(), name='variables-delete'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/domains/', EnvironmentDomainsAPIView.as_view(), name='domains-list'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/domains/create/', EnvironmentDomainCreateAPIView.as_view(), name='domains-create'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/domains/<uuid:domain_id>/reassign/', DomainReassignAPIView.as_view(), name='domain-reassign'),
]
