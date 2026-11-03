from rest_framework import serializers
from apps.projects.configurations.models import DeploymentConfig
from apps.projects.common import safe_relative_path


class DeploymentConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeploymentConfig
        fields = ('id', 'environment', 'created_by', 'version', 'is_draft', 'mode', 'resource_plan', 'runtime_type',
                  'build_method', 'branch', 'root_directory', 'dockerfile_path',
                  'build_command', 'start_command', 'output_directory', 'internal_port',
                  'healthcheck_path', 'auto_deploy', 'auto_deploy_branch', 'settings', 'created_at')
        read_only_fields = fields


class DeploymentConfigWriteSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=[('DEVOPS', 'DevOps'), ('DEVSECOPS', 'DevSecOps')], required=False)
    resource_plan = serializers.UUIDField(required=False, allow_null=True)
    runtime_type = serializers.ChoiceField(choices=DeploymentConfig.RuntimeType.choices, required=False)
    build_method = serializers.ChoiceField(choices=DeploymentConfig.BuildMethod.choices, required=False)
    branch = serializers.CharField(max_length=255, required=False)
    root_directory = serializers.CharField(max_length=300, required=False, allow_blank=True)
    dockerfile_path = serializers.CharField(max_length=300, required=False)
    build_command = serializers.CharField(max_length=500, required=False, allow_blank=True)
    start_command = serializers.CharField(max_length=500, required=False, allow_blank=True)
    output_directory = serializers.CharField(max_length=300, required=False, allow_blank=True)
    internal_port = serializers.IntegerField(required=False, min_value=1, max_value=65535)
    healthcheck_path = serializers.CharField(max_length=250, required=False)
    auto_deploy = serializers.BooleanField(required=False)
    auto_deploy_branch = serializers.CharField(max_length=255, required=False, allow_blank=True)
    settings = serializers.DictField(required=False)

    def validate(self, attrs):
        for name in ('root_directory', 'output_directory'):
            if name in attrs:
                attrs[name] = safe_relative_path(attrs[name])
        if 'dockerfile_path' in attrs:
            attrs['dockerfile_path'] = safe_relative_path(attrs['dockerfile_path'], allow_empty=False)
        for name in ('build_command', 'start_command'):
            if name in attrs and ('\x00' in attrs[name] or '\n' in attrs[name]):
                raise serializers.ValidationError({name: 'Commande invalide.'})
        if 'branch' in attrs and (not attrs['branch'].strip() or attrs['branch'].startswith('-') or '..' in attrs['branch']):
            raise serializers.ValidationError({'branch': 'Branche Git invalide.'})
        if 'healthcheck_path' in attrs and not attrs['healthcheck_path'].startswith('/'):
            raise serializers.ValidationError({'healthcheck_path': 'Chemin devant commencer par /.'})
        if 'settings' in attrs:
            keys = set(attrs['settings'])
            if keys - {'node_version', 'python_version', 'java_version', 'php_version', 'package_manager', 'install_command'}:
                raise serializers.ValidationError({'settings': 'Paramètre non autorisé. Utiliser les variables d’environnement pour les autres valeurs.'})
        return attrs
