from rest_framework import serializers
from apps.deployments.domains.models import Domain
from apps.projects.common import validate_hostname

class DomainCreateSerializer(serializers.Serializer):
    hostname=serializers.CharField(max_length=253)
    domain_type=serializers.ChoiceField(choices=Domain.Type.choices)

    def validate_hostname(self,value):
        return validate_hostname(value)
