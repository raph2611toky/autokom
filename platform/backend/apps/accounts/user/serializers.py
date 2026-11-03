from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from apps.accounts.user.models import User, UserSession

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'email', 'full_name', 'avatar', 'is_active', 'is_email_verified',
                  'email_verified_at', 'created_at', 'last_login_at')
        read_only_fields = fields

class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    full_name = serializers.CharField(max_length=180, min_length=2)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    password_confirm = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        attrs['email'] = attrs['email'].strip().lower()
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({'password_confirm': ['Les mots de passe ne correspondent pas.']})
        probe_user = User(email=attrs['email'], full_name=attrs['full_name'])
        validate_password(attrs['password'], user=probe_user)
        if User.objects.filter(email__iexact=attrs['email']).exists():
            raise serializers.ValidationError({'email': ['Adresse e-mail déjà utilisée.']})
        return attrs

class TokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=300, trim_whitespace=False)

class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField()

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)

class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField(trim_whitespace=False)

class LogoutSerializer(RefreshSerializer):
    pass

class ResetPasswordSerializer(TokenSerializer):
    token = serializers.CharField(max_length=2048, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password_confirm = serializers.CharField(write_only=True, trim_whitespace=False)
    def validate(self, attrs):
        if attrs['new_password'] != attrs['new_password_confirm']:
            raise serializers.ValidationError({'new_password_confirm': ['Les mots de passe ne correspondent pas.']})
        validate_password(attrs['new_password'])
        return attrs

class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password_confirm = serializers.CharField(write_only=True, trim_whitespace=False)
    def validate(self, attrs):
        if attrs['new_password'] != attrs['new_password_confirm']:
            raise serializers.ValidationError({'new_password_confirm': ['Les mots de passe ne correspondent pas.']})
        validate_password(attrs['new_password'], user=self.context['request'].user)
        return attrs

class MePatchSerializer(serializers.Serializer):
    full_name = serializers.CharField(required=False, min_length=2, max_length=180)


class MeAvatarSerializer(serializers.Serializer):
    avatar = serializers.ImageField(required=True, allow_null=False)

class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserSession
        fields = ('id', 'created_at', 'expires_at', 'revoked_at', 'last_seen_at', 'ip_address', 'user_agent')
        read_only_fields = fields


class GitHubExchangeSerializer(serializers.Serializer):
    ticket = serializers.CharField(min_length=40, max_length=256, trim_whitespace=False)
    browser_binding = serializers.CharField(min_length=40, max_length=256, trim_whitespace=False, write_only=True)
