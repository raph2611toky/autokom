#!/usr/bin/env python3
"""Guarded Nginx HTTPS runtime recovery for an AUTOKOM-managed ingress.

Never kill a process or reclaim an occupied TCP listener. A successful
configuration check/reload does NOT establish actual listener readiness.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


class NginxRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Listener:
    port: int
    name: str
    pid: int | None
    address: str


def _call(argv, timeout=15):
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NginxRuntimeError('Commande indisponible : ' + argv[0]) from exc


def listeners(ports=(80, 443)):
    """Inspect real kernel TCP listeners, with no application log heuristics."""
    result = _call(['ss', '-H', '-ltnp'])
    if result.returncode:
        raise NginxRuntimeError('ss -ltnp indisponible : inspecter avec sudo ss -ltnp')
    found = {port: [] for port in ports}
    for line in result.stdout.splitlines():
        fields = line.split(None, 6)
        if len(fields) < 5 or fields[0] != 'LISTEN':
            continue
        address = fields[3]
        match = re.search(r':(\d+)$', address)
        if not match:
            continue
        port = int(match.group(1))
        if port not in found:
            continue
        owners = re.findall(r'\("([^"\n]{1,60})",pid=(\d+)', line)
        if owners:
            for name, pid in owners:
                # Never output arbitrary user-controlled process names.
                safe = name if re.fullmatch(r'[A-Za-z0-9_.+:-]{1,60}', name) else 'inconnu'
                found[port].append(Listener(port, safe, int(pid), address))
        else:
            # Lack of process details means we MUST NOT assume the port is free.
            found[port].append(Listener(port, 'propriétaire non visible', None, address))
    return found


def summary(found):
    lines = []
    for port in (80, 443):
        items = found.get(port, [])
        if not items:
            lines.append(f'TCP/{port}: aucun listener')
        else:
            unique = sorted({f'{item.name} PID {item.pid}' if item.pid else item.name
                             for item in items})
            lines.append(f'TCP/{port}: ' + ', '.join(unique))
    return '; '.join(lines)


def _checked(argv, *, description):
    p = _call(argv, timeout=25)
    if p.returncode:
        # Do not stream arbitrary program stdout, which may include private data.
        raise NginxRuntimeError(f'{description} a échoué (code {p.returncode}); '
                                'consulter sudo journalctl -u nginx -n 60 --no-pager')


def guarded_recover_nginx(*, socket_scan=listeners, command=_checked):
    """Once-only recovery when port 443 is FREE and no foreign HTTP listener.

    With any 443 listener already bound, never restart Nginx: an overlapping
    or external listener must be inspected by the administrator. Reload is
    tried first; restart only when the listener is still truly absent.
    """
    current = socket_scan()
    if current.get(443):
        raise NginxRuntimeError('TCP/443 déjà occupé : ' + summary(current) +
                                '. AUTOKOM ne stoppe pas le processus propriétaire. '
                                'Analyser les écoutes avec sudo autokom tools nginx repair --check.')
    foreign_http = [item for item in current.get(80, []) if item.name != 'nginx']
    if foreign_http:
        raise NginxRuntimeError('TCP/80 occupé par un service autre que Nginx : ' +
                                summary(current) + '. Aucun redémarrage automatique.')
    command(['nginx', '-t'], description='nginx -t')
    state = _call(['systemctl', 'is-active', '--quiet', 'nginx'])
    if state.returncode == 0:
        # Reload does not imply application of the new config; verify again.
        command(['systemctl', 'reload', 'nginx'], description='Rechargement Nginx')
        after = socket_scan()
        if after.get(443):
            if any(item.name != 'nginx' for item in after[443]):
                raise NginxRuntimeError('Port 443 pris par un service étranger après reload : ' +
                                        summary(after) + '. Aucune intervention sur ce processus.')
            return 'rechargé : port 443 maintenant occupé (validation HTTPS requise)'
    else:
        # Start only if 80/443 are still free of foreign listeners.
        after = socket_scan()
        if after.get(443) or any(x.name != 'nginx' for x in after.get(80, [])):
            raise NginxRuntimeError('Conflit de port avant démarrage : ' + summary(after))
    # A reload can report success while the master logs bind failures. Only a
    # single restart is attempted, exclusively when no TCP/443 listener exists.
    current = socket_scan()
    if current.get(443) or any(x.name != 'nginx' for x in current.get(80, [])):
        raise NginxRuntimeError('Conflit détecté après reload : ' + summary(current))
    command(['systemctl', 'restart', 'nginx'], description='Redémarrage contrôlé Nginx')
    after = socket_scan()
    if not after.get(443):
        raise NginxRuntimeError('Nginx redémarré mais aucun listener TCP/443 : '+
                                summary(after) + '; vérifier sudo journalctl -u nginx -n 60 --no-pager')
    if any(item.name != 'nginx' for item in after[443]):
        raise NginxRuntimeError('Port 443 occupé après redémarrage, mais pas par Nginx : ' +
                                summary(after))
    return 'redémarré : port 443 occupé par Nginx (validation HTTPS requise)'


def ensure_validated_ingress(domains, *, verifier=None, recovery=None):
    """Verify service SNI first; recover once if needed, then re-verify.

    Never mistake `nginx -t` or `systemctl reload` for proof of HTTPS.
    Integrates with the existing final AUTOKOM stage without new user prompts.
    """
    from autokom_ingress_health import verify_observability, IngressError
    verifier = verifier or verify_observability
    recovery = recovery or guarded_recover_nginx
    try:
        verifier(domains)
        return 'conforme'
    except IngressError as first:
        try:
            outcome = recovery()
            verifier(domains)
            return outcome
        except (NginxRuntimeError, IngressError) as second:
            raise NginxRuntimeError('Échec du contrôle HTTPS initial : ' + str(first) +
                                    '. Réparation impossible ou non validée : ' + str(second)) from second
