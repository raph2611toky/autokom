"""Optional client runtime logs via preconfigured Loki; no caller-defined LogQL."""
from urllib.parse import urlsplit
import requests
from django.conf import settings
from rest_framework.exceptions import APIException
from apps.deployments.runs.worker import redact

class RuntimeLogsUnavailable(APIException):
    status_code = 503
    default_detail = 'Loki/Alloy n’est pas encore configuré pour les logs applicatifs.'


def fetch_runtime_logs(env, limit=100):
    url = getattr(settings, 'AUTOKOM_LOKI_URL', '').rstrip('/')
    parsed = urlsplit(url)
    if (not url or not parsed.hostname or parsed.username or parsed.password or
            parsed.path not in ('', '/') or parsed.query or parsed.fragment or
            (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1','localhost','::1')))):
        raise RuntimeLogsUnavailable()
    if not getattr(settings, 'AUTOKOM_RUNTIME_LOGS_ENABLED', False):
        raise RuntimeLogsUnavailable()
    # Fixed label comes from authenticated DB object, not from client-provided LogQL.
    query = '{autokom_environment="' + str(env.pk) + '"}'
    try:
        response = requests.get(url + '/loki/api/v1/query_range',
            params={'query':query,'limit':min(limit,100),'direction':'backward'},
            timeout=(3,8),allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeLogsUnavailable()
        streams = response.json().get('data', {}).get('result', [])
        values = []
        for stream in streams[:20]:
            for timestamp, message in stream.get('values', [])[:100]:
                values.append({'timestamp':str(timestamp), 'message':redact(message)[:800]})
        values.sort(key=lambda entry:entry['timestamp'], reverse=True)
        return values[:limit]
    except (requests.RequestException, ValueError, TypeError, KeyError):
        raise RuntimeLogsUnavailable()
