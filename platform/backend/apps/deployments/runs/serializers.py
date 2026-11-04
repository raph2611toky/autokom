from rest_framework import serializers
from apps.deployments.runs.models import DeploymentRun, PipelineStageRun, DeploymentLogEntry

class DeploymentStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PipelineStageRun
        fields = ('stage_key','name','position','tool_name','status','started_at','finished_at','summary')
        read_only_fields = fields

class DeploymentRunSerializer(serializers.ModelSerializer):
    stages = DeploymentStageSerializer(many=True, read_only=True)
    class Meta:
        model = DeploymentRun
        fields = ('id','environment','configuration','trigger_type','mode_snapshot','resources_snapshot',
                  'branch','commit_sha','status','ci_provider','created_at','started_at',
                  'finished_at','cancel_requested','error_message','stages')
        read_only_fields = fields

class DeploymentLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeploymentLogEntry
        fields = ('sequence','level','message','created_at')
        read_only_fields = fields

class HighAvailabilityRequestSerializer(serializers.Serializer):
    replicas = serializers.IntegerField(min_value=2, max_value=20)
