from django.urls import path
from apps.projects.secrets.views import (SecretListAPIView, SecretCreateAPIView,
    SecretReplaceAPIView, SecretDeleteAPIView)

app_name = 'project_secrets'
urlpatterns = [
    path('<uuid:project_id>/secrets/', SecretListAPIView.as_view(), name='list'),
    path('<uuid:project_id>/secrets/create/', SecretCreateAPIView.as_view(), name='create'),
    path('<uuid:project_id>/secrets/<uuid:secret_id>/replace/', SecretReplaceAPIView.as_view(), name='replace'),
    path('<uuid:project_id>/secrets/<uuid:secret_id>/delete/', SecretDeleteAPIView.as_view(), name='delete'),
]
