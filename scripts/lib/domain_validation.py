#!/usr/bin/env python3
"""Précontrôles DNS/direct-EC2 d'AUTOKOM (aucune modification du système).

Ce contrôle ne prétend pas prouver l'accès entrant depuis Internet. Pour
HTTP-01, seul le challenge ACME valide le bout en bout (SG, UFW, routage).
"""
from __future__ import annotations

import ipaddress
import json
import socket
import subprocess
import urllib.error
import urllib.request

METADATA = 'http://169.254.169.254/latest/'


class DomainRoutingError(ValueError):
    pass


def ec2_public_ipv4(*, timeout=1.5):
    """IP publique attachée à l'instance via IMDSv2 ; jamais IMDSv1.

    Proxy désactivé explicitement ; URL metadata fixe (aucune entrée utilisateur).
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        request = urllib.request.Request(
            METADATA + 'api/token', method='PUT',
            headers={'X-aws-ec2-metadata-token-ttl-seconds': '30'})
        with opener.open(request, timeout=timeout) as response:
            token = response.read(256).decode('ascii').strip()
        if not token:
            return None
        request = urllib.request.Request(
            METADATA + 'meta-data/public-ipv4',
            headers={'X-aws-ec2-metadata-token': token})
        with opener.open(request, timeout=timeout) as response:
            result = response.read(100).decode('ascii').strip()
        addr = ipaddress.ip_address(result)
        return str(addr) if addr.version == 4 and addr.is_global else None
    except (OSError, UnicodeError, ValueError, urllib.error.URLError):
        return None


def global_interface_ipv6():
    """IPv6 réellement assignées aux interfaces hôte (pas celles des bridges)."""
    try:
        proc = subprocess.run(['ip', '-j', '-6', 'address', 'show', 'scope', 'global'],
                              capture_output=True, text=True, timeout=3, check=False)
        if proc.returncode:
            return set()
        addresses = set()
        for interface in json.loads(proc.stdout):
            for item in interface.get('addr_info', []):
                addr = ipaddress.ip_address(item.get('local', ''))
                if addr.version == 6 and addr.is_global:
                    addresses.add(str(addr))
        return addresses
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return set()


def resolved_addresses(domain, *, resolver=socket.getaddrinfo):
    try:
        ips = {str(ipaddress.ip_address(item[4][0])) for item in resolver(
            domain, 80, type=socket.SOCK_STREAM)}
    except (socket.gaierror, OSError, ValueError) as exc:
        raise DomainRoutingError(f'{domain}: DNS non résolu ; vérifier les enregistrements A/AAAA') from exc
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise DomainRoutingError(f'{domain}: les enregistrements DNS ne sont pas tous publics : {sorted(ips)}')
    return ips


def check_direct_ec2_domain(domain, *, expected_ipv4=None, resolver=socket.getaddrinfo,
                            metadata=ec2_public_ipv4, ipv6_addresses=global_interface_ipv6):
    """Refuse les DNS pointant ailleurs (ALB/CDN/non-EC2 : profil non pris en charge)."""
    current = metadata()
    if expected_ipv4:
        try:
            manual = ipaddress.ip_address(expected_ipv4)
        except ValueError as exc:
            raise DomainRoutingError('--public-ip doit être une adresse IPv4 publique valide') from exc
        if manual.version != 4 or not manual.is_global:
            raise DomainRoutingError('--public-ip doit être une adresse IPv4 publique valide')
        if current and str(manual) != current:
            raise DomainRoutingError(f'--public-ip {manual} diffère de l’IP publique EC2 IMDSv2 {current}')
        current = str(manual)
    if not current:
        raise DomainRoutingError('IP publique EC2 indisponible via IMDSv2. '
                                 'Renseigner --public-ip si la machine possède une adresse publique directe, '
                                 'ou utiliser le mode sans domaine. Un ALB/CDN nécessite un déploiement dédié.')
    ips = resolved_addresses(domain, resolver=resolver)
    ipv4 = {x for x in ips if ipaddress.ip_address(x).version == 4}
    ipv6 = {x for x in ips if ipaddress.ip_address(x).version == 6}
    if ipv4 != {current}:
        raise DomainRoutingError(f'{domain}: DNS A={", ".join(sorted(ipv4)) or "absent"}, '
                                 f'IP publique EC2={current} : corriger DNS avant Certbot')
    actual_ipv6 = ipv6_addresses()
    if ipv6 and not ipv6.issubset(actual_ipv6):
        raise DomainRoutingError(f'{domain}: AAAA={", ".join(sorted(ipv6))} ne correspond pas '
                                 'aux IPv6 publiques de cette machine ; corriger AAAA avant Certbot')
    return sorted(ips)


def check_http01_local(*, connector=socket.create_connection):
    """Contrôle local du port 80. Ne garantit PAS l'accès depuis Internet."""
    try:
        with connector(('127.0.0.1', 80), timeout=2):
            return True
    except (OSError, TimeoutError):
        raise DomainRoutingError('Nginx/HTTP-01 : TCP/80 non accessible localement. '
                                 'Vérifier Nginx, puis les règles entrantes 80/TCP AWS Security Group/UFW.') from None
