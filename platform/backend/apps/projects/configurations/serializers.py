from rest_framework import serializers
from apps.projects.configurations.models import DeploymentConfig
from apps.projects.common import safe_relative_path


class DeploymentConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeploymentConfig
        fields = ('id', 'environment', 'created_by', 'version', 'is_draft', 'mode', 'resource_plan', 'runtime_type',
                  'build_method', 'branch', 'root_directory', 'dockerfile_path',
                  'build_command', 'start_command', 'output_directory',
                  'healthcheck_path', 'auto_deploy', 'auto_deploy_branch', 'high_availability_requested', 'settings', 'created_at')
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
    healthcheck_path = serializers.CharField(max_length=250, required=False)
    auto_deploy = serializers.BooleanField(required=False)
    high_availability_requested = serializers.BooleanField(required=False)
    auto_deploy_branch = serializers.CharField(max_length=255, required=False, allow_blank=True)
    settings = serializers.DictField(required=False)

    def validate(self, attrs):
        # Port listening is a server-side runtime concern, not a public API setting.
        if attrs.get('build_method') in (DeploymentConfig.BuildMethod.STATIC, DeploymentConfig.BuildMethod.AUTO):
            profile = (attrs.get('settings') or {}).get('frontend_profile')
            backend_profiles = {'python_fastapi', 'python_flask', 'python_django', 'python_wsgi',
                                'node_express', 'node_nest', 'java_spring_maven', 'java_spring_gradle',
                                'php_plain', 'php_laravel', 'php_symfony'}
            attrs['internal_port'] = (8000 if attrs.get('build_method') == DeploymentConfig.BuildMethod.AUTO
                                      and profile in backend_profiles else 8080)
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
            if keys - {'node_version', 'python_version', 'java_version', 'php_version', 'package_manager', 'install_command', 'frontend_profile'}:
                raise serializers.ValidationError({'settings': 'Paramètre non autorisé. Utiliser les variables d’environnement pour les autres valeurs.'})
            profile = attrs['settings'].get('frontend_profile')
            if profile is not None and (not isinstance(profile, str) or profile not in {'vite', 'npm_static', 'next_export', 'next_server', 'html', 'python_fastapi', 'python_flask', 'python_django', 'python_wsgi', 'node_express', 'node_nest', 'java_spring_maven', 'java_spring_gradle', 'php_plain', 'php_laravel', 'php_symfony'}):
                raise serializers.ValidationError({'settings': 'Recette frontend inconnue.'})
        return attrs
