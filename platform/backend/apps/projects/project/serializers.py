from rest_framework import serializers
from apps.projects.project.models import Project, ProjectMember


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ('id', 'organization', 'created_by', 'git_connection', 'repository_external_id',
                  'repository_name', 'repository_url', 'default_branch', 'name', 'slug',
                  'description', 'framework', 'status', 'created_at', 'updated_at')
        read_only_fields = fields


class ProjectCreateSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    installation_id = serializers.IntegerField(min_value=1)
    repository_id = serializers.IntegerField(min_value=1)
    name = serializers.CharField(max_length=160, trim_whitespace=True)
    description = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    domain = serializers.CharField(required=False, allow_blank=True, max_length=253)


class ProjectPatchSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160, required=False)
    description = serializers.CharField(max_length=4000, required=False, allow_blank=True)
    framework = serializers.CharField(max_length=80, required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError('Aucune modification fournie.')
        return attrs


class ProjectMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectMember
        fields = ('id', 'project', 'user', 'role', 'created_at')
        read_only_fields = fields


class ProjectMemberCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=ProjectMember.Role.choices, default=ProjectMember.Role.VIEWER)


class ProjectMemberUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ProjectMember.Role.choices)
