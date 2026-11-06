"""Only host integration from Django: typed requests over AUTOKOM's Unix socket.

No subprocess, Docker SDK, Nginx/Certbot writes, SSH or shell in backend.
"""
import json
import logging
import re

logger = logging.getLogger("autokom.runtime.control")
import socket
from django.conf import settings
from rest_framework.exceptions import APIException


class AutokomControlUnavailable(APIException):
    status_code = 503
    default_detail = 'Service de contrôle AUTOKOM indisponible.'


def request_control(action, params=None, *, timeout=30):
    if action not in {
        'ping', 'jenkins.dispatch','jenkins.state','jenkins.console','jenkins.cancel',
        'runtime.deploy','runtime.status','runtime.logs','runtime.rollback','runtime.commit','runtime.deprovision',
        'domain.verify','domain.dns.create','domain.activate','domain.deactivate',
    }:
        raise AutokomControlUnavailable('Opération non autorisée.')
    body = (json.dumps({'action':action,'params':params or {}},separators=(',',':'))+'\n').encode('utf-8')
    if len(body)>512*1024:
        raise AutokomControlUnavailable('Taille de requête supérieure à la limite.')
    path = getattr(settings,'AUTOKOM_CONTROL_SOCKET','/run/autokom/platform-control.sock')
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as stream:
            stream.settimeout(timeout)
            stream.connect(path)
            stream.sendall(body)
            buffer=b''
            while not buffer.endswith(b'\n') and len(buffer)<=128*1024:
                data=stream.recv(8192)
                if not data:break
                buffer+=data
        reply=json.loads(buffer)
        if not reply.get('ok'):
            # Error text is generic, never reveal socket path, process args or secrets.
            code = reply.get('code','')
            safe_code = code if isinstance(code,str) and re.fullmatch(r'AUTOKOM_[A-Z0-9_]{3,80}',code) else None
            logger.warning('Broker refuse %s code=%s', action, safe_code or 'AUTOKOM_BROKER_REFUSED')
            error=AutokomControlUnavailable()
            error.safe_code=safe_code
            raise error
        return reply['result']
    except AutokomControlUnavailable:
        raise
    except (OSError,ValueError,KeyError,TypeError,TimeoutError) as exc:
        # No exception text: paths, credentials and command output are never logged.
        logger.warning('Broker injoignable action=%s cause=%s', action, type(exc).__name__)
        raise AutokomControlUnavailable() from exc
