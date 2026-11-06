from django.urls import path
from apps.projects.project.views import (ProjectOrganizationsAPIView, ProjectListAPIView,
    ProjectCreateAPIView, ProjectDetailAPIView, ProjectUpdateAPIView, ProjectArchiveAPIView, ProjectDeleteAPIView, ProjectRestoreAPIView, ProjectMembersListAPIView, ProjectMemberAddAPIView,
    ProjectMemberUpdateAPIView, ProjectMemberRemoveAPIView)

app_name = 'projects'
urlpatterns = [
    path('organizations/', ProjectOrganizationsAPIView.as_view(), name='organizations'),
    path('', ProjectListAPIView.as_view(), name='list'),
    path('create/', ProjectCreateAPIView.as_view(), name='create'),
    path('<uuid:project_id>/', ProjectDetailAPIView.as_view(), name='detail'),
    path('<uuid:project_id>/update/', ProjectUpdateAPIView.as_view(), name='update'),
    path('<uuid:project_id>/members/', ProjectMembersListAPIView.as_view(), name='members-list'),
    path('<uuid:project_id>/members/add/', ProjectMemberAddAPIView.as_view(), name='members-add'),
    path('<uuid:project_id>/members/<uuid:membership_id>/update/', ProjectMemberUpdateAPIView.as_view(), name='members-update'),
    path('<uuid:project_id>/members/<uuid:membership_id>/remove/', ProjectMemberRemoveAPIView.as_view(), name='members-remove'),
    path('<uuid:project_id>/archive/', ProjectArchiveAPIView.as_view(), name='archive'),
    path('<uuid:project_id>/delete/', ProjectDeleteAPIView.as_view(), name='delete'),
    path('<uuid:project_id>/restore/', ProjectRestoreAPIView.as_view(), name='restore'),
]
