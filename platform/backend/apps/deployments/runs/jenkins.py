"""Jenkins façade: Django never contacts Jenkins or executes host operations.
The AUTOKOM privileged broker holds Jenkins credentials and validates all paths.
"""
import re
from rest_framework.exceptions import APIException
from apps.deployments.runs.autokom_socket import request_control


class JenkinsUnavailable(APIException):
    status_code=503
    default_detail='Jenkins ou AUTOKOM n’est pas disponible.'


def verify_settings():
    status=request_control('ping',timeout=4)
    if not status.get('jenkins_configured'):
        raise JenkinsUnavailable('Compte de service Jenkins non configuré dans AUTOKOM Core.')
    return 'AUTOKOM-UNIX-SOCKET','autokom-client-deploy'


class JenkinsClient:
    def __init__(self):
        verify_settings()

    def dispatch(self,run):
        return request_control('jenkins.dispatch',{'run_id':str(run.pk)},timeout=30)['external_id']

    def get_state(self,external_id):
        return request_control('jenkins.state',{'external_id':external_id},timeout=20)

    def console(self,external_id,start=0):
        result=request_control('jenkins.console',{'external_id':external_id,'offset':start},timeout=20)
        return result['text'],result['offset']

    def cancel(self,external_id):
        request_control('jenkins.cancel',{'external_id':external_id},timeout=20)
