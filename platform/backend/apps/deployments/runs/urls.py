from django.urls import path
from apps.deployments.runs.views import (DeploymentPreviewAPIView, DeploymentEnqueueAPIView, DeploymentRestoreAPIView,
    DeploymentListAPIView, DeploymentDetailAPIView, DeploymentLogsAPIView, DeploymentLogDownloadAPIView, DeploymentCancelAPIView,
    HighAvailabilityStatusAPIView, HighAvailabilityRequestAPIView, RuntimeLogsAPIView, DeploymentUsageAPIView)

app_name = 'client_deployments'
urlpatterns = [
    path('<uuid:project_id>/environments/<uuid:environment_id>/deployments/preview/', DeploymentPreviewAPIView.as_view(), name='deployment-preview'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/deployments/', DeploymentListAPIView.as_view(), name='deployment-list'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/deployments/launch/', DeploymentEnqueueAPIView.as_view(), name='deployment-launch'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/deployments/<uuid:run_id>/', DeploymentDetailAPIView.as_view(), name='deployment-detail'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/deployments/<uuid:run_id>/logs/', DeploymentLogsAPIView.as_view(), name='deployment-logs'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/deployments/<uuid:run_id>/logs/download/', DeploymentLogDownloadAPIView.as_view(), name='deployment-log-download'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/deployments/<uuid:run_id>/restore/', DeploymentRestoreAPIView.as_view(), name='deployment-restore'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/deployments/<uuid:run_id>/cancel/', DeploymentCancelAPIView.as_view(), name='deployment-cancel'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/usage/', DeploymentUsageAPIView.as_view(), name='resource-usage'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/runtime-logs/', RuntimeLogsAPIView.as_view(), name='runtime-logs'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/high-availability/', HighAvailabilityStatusAPIView.as_view(), name='ha-status'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/high-availability/request/', HighAvailabilityRequestAPIView.as_view(), name='ha-request'),
]
