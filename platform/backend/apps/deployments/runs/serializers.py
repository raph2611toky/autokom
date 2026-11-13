from django.conf import settings
from rest_framework import serializers
import re
from apps.deployments.runs.models import DeploymentRun, PipelineStageRun, DeploymentLogEntry

class DeploymentStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PipelineStageRun
        fields = ('stage_key','name','position','status','started_at','finished_at','summary')
        read_only_fields = fields

class DeploymentRunSerializer(serializers.ModelSerializer):
    error_message = serializers.SerializerMethodField()
    security_feedback_enabled = serializers.SerializerMethodField()
    release_status = serializers.SerializerMethodField()
    security_actions = serializers.SerializerMethodField()
    remediation_suggestions = serializers.SerializerMethodField()

    def get_release_status(self, run):
        release = next(iter(run.releases.all()), None)
        return release.status if release else ''

    def get_security_actions(self, run):
        return {item.channel: {'status':'SENT' if item.completed_at else 'UNCONFIRMED',
                               'number':item.result_number if item.channel == 'ISSUE' and item.completed_at else None}
                for item in run.security_feedback.all() if item.channel in ('ISSUE','EMAIL')}

    def get_remediation_suggestions(self, run):
        from apps.deployments.runs.security_feedback import SUGGESTIONS
        return {key:SUGGESTIONS[key] for key in (run.sast_report.get('rules') or {}) if key in SUGGESTIONS}


    def get_security_feedback_enabled(self, instance):
        return settings.AUTOKOM_SECURITY_FEEDBACK_ENABLED

    def get_error_message(self, instance):
        if not instance.error_message:
            return ''
        # Never echo an arbitrary exception, repository output or a credential.
        # Known AUTOKOM diagnostic IDs are safe and help users locate the real
        # failure in the per-service technical journal.
        code = re.search(r'\bAUTOKOM_[A-Z0-9_]{3,80}\b', instance.error_message)
        message = 'La publication a échoué ou a été bloquée. Consultez le journal technique et les étapes pour plus de détails.'
        return message + (' Code : ' + code.group(0) if code else '')

    def get_jenkins_reference(self, instance):
        # Display ONLY the numeric queue/build reference, never Jenkins URL,
        # credentials, private internal paths or raw external IDs.
        reference = instance.external_run_id or ''
        match = re.fullmatch(r'([BQ]):([1-9][0-9]{0,9})', reference)
        if not match:
            return ''
        return ('Build Jenkins #' if match.group(1) == 'B' else 'File Jenkins #') + match.group(2)

    jenkins_reference = serializers.SerializerMethodField()
    stages = DeploymentStageSerializer(many=True, read_only=True)
    class Meta:
        model = DeploymentRun
        fields = ('id','environment','configuration','trigger_type','mode_snapshot','resources_snapshot',
                  'branch','commit_sha','status','created_at','started_at',
                  'finished_at','cancel_requested','error_message','jenkins_reference','stages','sast_report','sca_report','security_feedback_enabled','security_actions','remediation_suggestions','release_status','rollback_source')
        read_only_fields = fields

class DeploymentLogSerializer(serializers.ModelSerializer):
    stage_key = serializers.SerializerMethodField()

    def get_stage_key(self, instance):
        if instance.level == 'TECH' and instance.message.startswith('AUTOKOM_TECH:'):
            parts = instance.message.split(':', 2)
            if len(parts) == 3 and parts[1] in ('source','sast','deepsast','sca','build','release','dast','health'):
                return parts[1]
        return ''

    class Meta:
        model = DeploymentLogEntry
        fields = ('sequence','level','message','stage_key','created_at')
        read_only_fields = fields

class HighAvailabilityRequestSerializer(serializers.Serializer):
    replicas = serializers.IntegerField(min_value=2, max_value=20)
