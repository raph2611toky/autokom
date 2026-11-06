from django.urls import path
from apps.deployments.domains.views import DomainListAPIView,DomainCreateAPIView,DomainVerifyAPIView,DomainPrimaryAPIView,DomainDeactivateAPIView
app_name='client_domains'
urlpatterns=[
    path('<uuid:project_id>/environments/<uuid:environment_id>/domains/',DomainListAPIView.as_view(),name='list'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/domains/create/',DomainCreateAPIView.as_view(),name='create'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/domains/<uuid:domain_id>/verify/',DomainVerifyAPIView.as_view(),name='verify'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/domains/<uuid:domain_id>/primary/',DomainPrimaryAPIView.as_view(),name='primary'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/domains/<uuid:domain_id>/deactivate/',DomainDeactivateAPIView.as_view(),name='deactivate'),
]
