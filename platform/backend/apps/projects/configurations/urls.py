from django.urls import path
from apps.projects.configurations.views import (ConfigurationListAPIView, ConfigurationCreateAPIView,
    ConfigurationDetailAPIView, ConfigurationUpdateAPIView, ConfigurationFinalizeAPIView, ConfigurationOffersAPIView)

from apps.projects.configurations.stack_views import StackAPIView, StackLaunchAPIView, StackDeploymentDetailAPIView

app_name = 'project_configurations'
urlpatterns = [
    path('<uuid:project_id>/stack/', StackAPIView.as_view(), name='stack'),
    path('<uuid:project_id>/stack/launch/', StackLaunchAPIView.as_view(), name='stack-launch'),
    path('<uuid:project_id>/stack/deployments/<uuid:stack_run_id>/', StackDeploymentDetailAPIView.as_view(), name='stack-run'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/configurations/', ConfigurationListAPIView.as_view(), name='list'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/configurations/create/', ConfigurationCreateAPIView.as_view(), name='create'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/configurations/offers/', ConfigurationOffersAPIView.as_view(), name='offers'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/configurations/<uuid:configuration_id>/', ConfigurationDetailAPIView.as_view(), name='detail'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/configurations/<uuid:configuration_id>/update/', ConfigurationUpdateAPIView.as_view(), name='update'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/configurations/<uuid:configuration_id>/finalize/', ConfigurationFinalizeAPIView.as_view(), name='finalize'),
]
