from rest_framework import serializers


class GitAuthorizeSerializer(serializers.Serializer):
    # L'organisation est choisie lors de la création du projet, pas pendant OAuth.
    pass


class GitInstallationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    account = serializers.CharField()
    repository_selection = serializers.CharField()
    permissions = serializers.DictField()


class GitRepositorySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    installation_id = serializers.IntegerField()
    full_name = serializers.CharField()
    name = serializers.CharField()
    private = serializers.BooleanField()
    default_branch = serializers.CharField()
    html_url = serializers.URLField()
    description = serializers.CharField(allow_blank=True, allow_null=True)


class GitBranchSerializer(serializers.Serializer):
    name = serializers.CharField()
    protected = serializers.BooleanField()
    sha = serializers.CharField()
