from django.urls import path
from apps.projects.configurations.views import (ConfigurationListAPIView, ConfigurationCreateAPIView,
    ConfigurationDetailAPIView, ConfigurationUpdateAPIView, ConfigurationFinalizeAPIView, ConfigurationOffersAPIView)

app_name = 'project_configurations'
urlpatterns = [
    path('<uuid:project_id>/environments/<uuid:environment_id>/configurations/', ConfigurationListAPIView.as_view(), name='list'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/configurations/create/', ConfigurationCreateAPIView.as_view(), name='create'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/configurations/offers/', ConfigurationOffersAPIView.as_view(), name='offers'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/configurations/<uuid:configuration_id>/', ConfigurationDetailAPIView.as_view(), name='detail'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/configurations/<uuid:configuration_id>/update/', ConfigurationUpdateAPIView.as_view(), name='update'),
    path('<uuid:project_id>/environments/<uuid:environment_id>/configurations/<uuid:configuration_id>/finalize/', ConfigurationFinalizeAPIView.as_view(), name='finalize'),
]
