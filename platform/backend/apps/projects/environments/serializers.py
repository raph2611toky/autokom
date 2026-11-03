import re
from rest_framework import serializers
from apps.projects.environments.models import Environment, EnvironmentVariable


class EnvironmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Environment
        fields = ('id', 'project', 'name', 'slug', 'kind', 'is_active', 'created_at', 'updated_at')
        read_only_fields = fields


class EnvironmentCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    kind = serializers.ChoiceField(choices=Environment.Kind.choices, default=Environment.Kind.DEVELOPMENT)


class EnvironmentUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    is_active = serializers.BooleanField(required=False)


class EnvironmentVariableSerializer(serializers.ModelSerializer):
    secret_id = serializers.UUIDField(source='secret.id', read_only=True, allow_null=True)

    class Meta:
        model = EnvironmentVariable
        fields = ('id', 'environment', 'key', 'value', 'secret_id', 'created_at', 'updated_at')
        read_only_fields = fields


class EnvironmentVariableWriteSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=160)
    value = serializers.CharField(required=False, allow_blank=True, allow_null=False)
    secret_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_key(self, value):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
            raise serializers.ValidationError('Clé de variable invalide (ex : DATABASE_URL).')
        return value

    def validate(self, attrs):
        if attrs.get('secret_id') and attrs.get('value'):
            raise serializers.ValidationError('Une variable ne peut contenir une valeur et un secret simultanément.')
        return attrs


class DomainSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    hostname = serializers.CharField(read_only=True)
    domain_type = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    tls_status = serializers.CharField(read_only=True)
    environment_id = serializers.UUIDField(source='environment.id', read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class DomainCreateSerializer(serializers.Serializer):
    hostname = serializers.CharField(required=False, allow_blank=True, max_length=253)


class DomainReassignSerializer(serializers.Serializer):
    new_environment_id = serializers.UUIDField()
    confirm_reassignment = serializers.BooleanField()

    def validate_confirm_reassignment(self, value):
        if not value:
            raise serializers.ValidationError('Confirmer explicitement le changement de rattachement.')
        return value
