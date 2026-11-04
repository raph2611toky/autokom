"""Optional actual resource utilization; only fixed tenant-scoped Prometheus queries."""
from urllib.parse import urlsplit
import requests
from django.conf import settings
from rest_framework.exceptions import APIException

class MetricsUnavailable(APIException):
    status_code = 503
    default_detail = 'Les métriques Prometheus de cet environnement ne sont pas disponibles.'


def _prom_query(endpoint, query):
    try:
        result = requests.get(endpoint + '/api/v1/query', params={'query':query},
                              timeout=(3,8),allow_redirects=False)
        if result.status_code != 200:
            raise MetricsUnavailable()
        vectors = result.json().get('data', {}).get('result', [])
        if not vectors:
            return None
        value = float(vectors[0]['value'][1])
        if not (-1e30 < value < 1e30):
            return None
        return value
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        raise MetricsUnavailable()


def fetch_usage(env):
    endpoint = getattr(settings,'AUTOKOM_PROMETHEUS_URL','').rstrip('/')
    parsed=urlsplit(endpoint)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or
            parsed.query or parsed.fragment or (parsed.scheme != 'https' and not
            (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1','localhost','::1')))):
        raise MetricsUnavailable()
    namespace='autokom-'+env.id.hex[:20]
    selector='namespace="'+namespace+'",container="app"'
    cpu=_prom_query(endpoint, f'sum(rate(container_cpu_usage_seconds_total{{{selector}}}[5m]))')
    memory=_prom_query(endpoint, f'sum(container_memory_working_set_bytes{{{selector}}})')
    return {
        'source':'prometheus', 'namespace':namespace,
        'cpu_millicores':round(cpu*1000,2) if cpu is not None else None,
        'memory_mb':round(memory / (1024*1024),2) if memory is not None else None,
        'ready':cpu is not None or memory is not None,
    }
