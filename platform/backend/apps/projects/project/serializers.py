from rest_framework import serializers
from apps.projects.project.models import Project, ProjectMember


class ProjectSerializer(serializers.ModelSerializer):
    public_site = serializers.SerializerMethodField()

    def get_public_site(self, project):
        # No guessed URLs: a publicly usable link requires verified HTTPS activation.
        from apps.deployments.domains.models import Domain
        qs = Domain.objects.filter(environment__project=project, environment__kind='PRODUCTION', is_disabled=False).order_by('-is_primary', 'created_at')
        selected = qs.filter(status=Domain.Status.ACTIVE, tls_status='ACTIVE',
            environment__hosted_application__observed_state='RUNNING').first()
        planned = selected or qs.first()
        return {
            'hostname': planned.hostname if planned else None,
            'url': 'https://' + selected.hostname if selected else None,
            'status': 'ACTIVE' if selected else (planned.status if planned else 'NONE'),
            'https': bool(selected),
        }

    class Meta:
        model = Project
        fields = ('id', 'organization', 'created_by', 'git_connection', 'repository_external_id',
                  'repository_name', 'repository_url', 'default_branch', 'name', 'slug',
                  'description', 'framework', 'status', 'created_at', 'updated_at', 'public_site')
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
