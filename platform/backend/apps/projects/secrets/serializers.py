import re
from rest_framework import serializers
from apps.projects.secrets.models import SecretReference


class SecretSerializer(serializers.ModelSerializer):
    has_value = serializers.SerializerMethodField()

    class Meta:
        model = SecretReference
        fields = ('id', 'project', 'environment', 'name', 'scope', 'is_active',
                  'version_ref', 'has_value', 'created_at', 'updated_at')
        read_only_fields = fields

    def get_has_value(self, obj):
        return bool(obj.encrypted_value or obj.vault_reference)


class SecretWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160)
    scope = serializers.ChoiceField(choices=SecretReference.Scope.choices, default='ENVIRONMENT')
    environment_id = serializers.UUIDField(required=False, allow_null=True)
    value = serializers.CharField(trim_whitespace=False, allow_blank=False, write_only=True, max_length=10000)

    def validate_name(self, name):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise serializers.ValidationError('Nom de secret invalide.')
        return name

    def validate(self, attrs):
        if attrs['scope'] == 'ENVIRONMENT' and not attrs.get('environment_id'):
            raise serializers.ValidationError({'environment_id': 'Requis pour un secret d’environnement.'})
        if attrs['scope'] == 'PROJECT' and attrs.get('environment_id'):
            raise serializers.ValidationError({'environment_id': 'Doit être omis pour un secret de projet.'})
        return attrs


class SecretReplaceSerializer(serializers.Serializer):
    value = serializers.CharField(trim_whitespace=False, allow_blank=False, max_length=10000)
