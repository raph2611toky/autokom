"""Non-invasive HTTP security assertions against an AUTOKOM-owned candidate.

No crawling, payloads, fingerprinting, redirects or customer-selected targets.
"""
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
import http.client
import re


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def inspect_candidate(port, path, *, open_request=None):
    if (type(port) is not int or not 1024 <= port <= 65535
            or not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9/_~.\-]{0,127}', path)
            or path.startswith('//')):
        raise ValueError('Endpoint candidat invalide.')
    opener = open_request or build_opener(ProxyHandler({}), NoRedirect()).open
    request = Request(f'http://127.0.0.1:{port}{path}', method='GET',
                      headers={'User-Agent':'AUTOKOM-Runtime-Checks/1', 'Connection':'close'})
    try:
        try:
            response = opener(request, timeout=4)
        except HTTPError as exc:
            response = exc
        with response:
            status, headers = response.status, response.headers
            warnings = []
            if 200 <= status < 300 and headers.get('Content-Type','').split(';',1)[0].strip().lower() == 'text/html':
                if not headers.get('Content-Security-Policy'):
                    warnings.append('CSP_MISSING')
                if headers.get('X-Content-Type-Options','').strip().lower() != 'nosniff':
                    warnings.append('NOSNIFF_MISSING')
                if not headers.get('X-Frame-Options') and 'frame-ancestors' not in headers.get('Content-Security-Policy','').lower():
                    warnings.append('FRAME_POLICY_MISSING')
            return {'http': status, 'warnings': warnings}
    except (OSError, URLError, http.client.HTTPException):
        return {'http': 0, 'warnings': []}
