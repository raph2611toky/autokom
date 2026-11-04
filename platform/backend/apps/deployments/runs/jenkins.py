"""Jenkins API adapter. No client-supplied job/URL/command; controller job is trusted."""
import re
from urllib.parse import urlsplit, quote
import requests
from django.conf import settings
from rest_framework.exceptions import APIException

class JenkinsUnavailable(APIException):
    status_code = 503
    default_detail = 'Jenkins est indisponible ou le job approuvé n’est pas prêt.'


def verify_settings():
    base = getattr(settings, 'AUTOKOM_JENKINS_URL', '').rstrip('/')
    job = getattr(settings, 'AUTOKOM_JENKINS_JOB', '')
    user = getattr(settings, 'AUTOKOM_JENKINS_USER', '')
    token = getattr(settings, 'AUTOKOM_JENKINS_API_TOKEN', '')
    parsed = urlsplit(base)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise JenkinsUnavailable('URL Jenkins invalide : seul le serveur de confiance est accepté.')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1','localhost','::1')):
        raise JenkinsUnavailable('Jenkins doit utiliser HTTPS (sauf loopback).')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', job or ''):
        raise JenkinsUnavailable('Job Jenkins dédié non configuré.')
    if not user or not token:
        raise JenkinsUnavailable('Compte de service Jenkins/API token manquant.')
    return base, job


class JenkinsClient:
    def __init__(self):
        self.base, self.job = verify_settings()
        self.session = requests.Session()
        self.session.auth = (settings.AUTOKOM_JENKINS_USER, settings.AUTOKOM_JENKINS_API_TOKEN)
        self.session.headers.update({'Accept': 'application/json', 'User-Agent': 'AUTOKOM-Platform-Deployer/1.0'})
        self.job_path = '/job/' + quote(self.job, safe='')

    def request(self, method, path, **kwargs):
        if not path.startswith('/') or '//' in path or '..' in path:
            raise JenkinsUnavailable('Chemin Jenkins invalide.')
        try:
            response = self.session.request(method, self.base + path,
                                            timeout=(4, 18), allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            raise JenkinsUnavailable('Jenkins ne répond pas.') from exc
        if response.status_code not in (200, 201, 202):
            raise JenkinsUnavailable('Jenkins a refusé une requête ou ses permissions sont insuffisantes.')
        return response

    def dispatch(self, run):
        # Only the random UUID. The trusted job fetches a signed manifest over TLS.
        response = self.request('POST', self.job_path + '/buildWithParameters', data={'AUTOKOM_RUN_ID': str(run.pk)})
        location = response.headers.get('Location', '')
        match = re.search(r'/queue/item/(\d+)/?$', location)
        if not match or not location.startswith(self.base + '/queue/'):
            raise JenkinsUnavailable('Jenkins ne fournit pas d’identifiant de file fiable.')
        return 'Q:' + match.group(1)

    def get_state(self, external_id):
        if re.fullmatch(r'Q:\d+', external_id or ''):
            data = self.request('GET', f'/queue/item/{external_id[2:]}/api/json').json()
            if data.get('cancelled'):
                return {'status': 'CANCELLED', 'external_id': external_id}
            executable = data.get('executable') or {}
            number = executable.get('number')
            if type(number) is int and number > 0:
                return {'status': 'RUNNING', 'external_id': f'B:{number}'}
            return {'status': 'RUNNING', 'external_id': external_id}
        if re.fullmatch(r'B:\d+', external_id or ''):
            data = self.request('GET', f'{self.job_path}/{external_id[2:]}/api/json').json()
            if data.get('building'):
                return {'status': 'RUNNING', 'external_id': external_id}
            result = data.get('result')
            mapping = {'SUCCESS':'SUCCEEDED', 'ABORTED':'CANCELLED', 'FAILURE':'FAILED',
                       'UNSTABLE':'FAILED', 'NOT_BUILT':'BLOCKED'}
            if result not in mapping:
                raise JenkinsUnavailable('Résultat Jenkins inconnu.')
            return {'status': mapping[result], 'external_id': external_id}
        raise JenkinsUnavailable('Identifiant Jenkins inconnu.')

    def console(self, external_id, start=0):
        if not re.fullmatch(r'B:\d+', external_id or ''):
            return '', start
        response = self.request('GET', f'{self.job_path}/{external_id[2:]}/logText/progressiveText',
                                params={'start': start}, headers={'Accept':'text/plain'})
        chunk = response.content[:65536]
        text = chunk.decode('utf-8', 'replace')
        try:
            size = int(response.headers.get('X-Text-Size', start))
        except (TypeError, ValueError):
            size = start
        if len(response.content) > len(chunk):
            size = start + len(chunk)
        if size < start:
            size = start
        return text, size

    def cancel(self, external_id):
        if re.fullmatch(r'Q:\d+', external_id or ''):
            self.request('POST', f'/queue/cancelItem', params={'id': external_id[2:]})
        elif re.fullmatch(r'B:\d+', external_id or ''):
            self.request('POST', f'{self.job_path}/{external_id[2:]}/stop')
        else:
            raise JenkinsUnavailable('Identifiant Jenkins inconnu.')
