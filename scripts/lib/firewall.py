#!/usr/bin/env python3
"""AUTOKOM Phase 2: protect host-side admin/DB listeners while allowing Docker bridges.

This guards the host INPUT chain, *not* Docker-published FORWARD ports.  Fail
closed on unexpected Docker publication and require cloud firewall validation.
"""
import ipaddress
import os
import json
import re
import shutil
import subprocess
from pathlib import Path

PRIVATE_TCP_PORTS = (2375, 2376, 3000, 3306, 33060, 5432, 5601,
                     6379, 8080, 9000, 9090, 9093, 9100, 9200, 1521)
DATABASE_PORTS = {'mysql': (3306,), 'postgresql': (5432,), 'oracle': (1521,), 'none': ()}


def execute(argv, check=True):
    r = subprocess.run(argv, text=True, capture_output=True, check=False)
    if check and r.returncode:
        raise RuntimeError(f"{argv[0]} : {((r.stderr or r.stdout) or '').strip()[-450:]}")
    return r


def wan_interface(executor=execute):
    r = executor(['ip', '-4', 'route', 'get', '1.1.1.1'], check=False)
    if r.returncode:
        raise RuntimeError('Interface WAN non identifiable (ip -4 route get)')
    m = re.search(r'\bdev\s+([a-zA-Z0-9_.:@-]+)', r.stdout)
    if not m or m.group(1) in ('lo', 'docker0') or m.group(1).startswith('br-'):
        raise RuntimeError('Interface WAN ambiguë, aucune règle appliquée')
    return m.group(1)


def docker_bridges(executor=execute):
    r = executor(['ip', '-o', '-4', 'addr', 'show'], check=False)
    if r.returncode:
        raise RuntimeError('Impossible de détecter les interfaces internes Docker')
    names = []
    for line in r.stdout.splitlines():
        m = re.match(r'^\d+:\s+([^\s:@]+)(?:@\S+)?\s+inet\s+(\S+)', line)
        if m and (m.group(1) == 'docker0' or re.fullmatch(r'br-[0-9a-fA-F]+', m.group(1))):
            names.append(m.group(1))
    return sorted(set(names))


def docker_published(executor=execute):
    """Returns publicly published application/admin ports; UFW alone is insufficient."""
    if shutil.which('docker') is None:
        return []
    r = executor(['docker', 'ps', '-q'], check=False)
    if r.returncode:
        raise RuntimeError('Docker actif mais impossible à interroger : vérifier les publications avant application')
    ids = [p.strip() for p in r.stdout.splitlines() if p.strip()]
    if not ids:
        return []
    r = executor(['docker', 'inspect'] + ids, check=True)
    results = []
    for obj in json.loads(r.stdout):
        name = obj.get('Name', '?').strip('/')
        bindings = (obj.get('NetworkSettings') or {}).get('Ports') or {}
        for _, targets in bindings.items():
            for target in targets or []:
                address = target.get('HostIp', '')
                port = target.get('HostPort', '')
                if address not in ('127.0.0.1', '::1') and port.isdigit() and int(port) not in (80, 443):
                    results.append((name, address or 'all', int(port)))
        # Host network bypasses Docker ports mapping; report as manual check.
        if (obj.get('HostConfig') or {}).get('NetworkMode') == 'host':
            results.append((name, 'host-network', 0))
    return results


def ufw_active(executor=execute):
    if shutil.which('ufw') is None:
        return False
    r = executor(['ufw', 'status'], check=False)
    if r.returncode:
        raise RuntimeError('Impossible de lire UFW')
    return bool(re.search(r'^Status:\s+active\s*$', r.stdout, re.M | re.I))


def ufw_added(executor=execute):
    r = executor(['ufw', 'show', 'added'], check=False)
    if r.returncode:
        raise RuntimeError('Impossible de lister les règles UFW')
    return r.stdout.lower()


def has_rule(added, verdict, iface, port):
    # Output of "ufw show added" is usually "ufw deny in on IF to any port PORT proto tcp".
    needle = f'{verdict} in on {iface} to any port {port} proto tcp'
    return needle in added.lower()


def rule_command(verdict, iface, port):
    return ['ufw', 'insert', '1', verdict, 'in', 'on', iface,
            'to', 'any', 'port', str(port), 'proto', 'tcp']


def secure_firewall(db, apply=False, activate=False, ssh_cidr='', ssh_port=22,
                    confirmation=False, say=print, executor=execute):
    """Enforce narrow host rules, leave all Docker-managed forwarding untouched.

    UFW inactive: refuse apply unless explicitly activated with SSH guard.
    AWS SG/GCP policies must be assessed separately.
    """
    interface = wan_interface(executor)
    bridges = docker_bridges(executor)
    exposed = docker_published(executor)
    active = ufw_active(executor)
    say(f'Firewall : interface externe={interface}; UFW={"actif" if active else "inactif"}')
    say(f'Docker : interfaces privées={", ".join(bridges) or "aucune"}')
    if exposed:
        for name, addr, port in exposed:
            say(f'ALERTE Docker : {name} publie {addr}:{port} (UFW ne suffit pas)')
    if not apply:
        say('Simulation uniquement : aucune règle réseau modifiée')
        return {'wan': interface, 'bridges': bridges, 'active': active, 'published': exposed}

    if exposed:
        raise RuntimeError('Port Docker publié sur une interface externe : binder à 127.0.0.1 ou utiliser Nginx avant --apply')
    if shutil.which('ufw') is None:
        raise RuntimeError('UFW absent. Installer le paquet ufw et relancer la simulation')
    if not active:
        if not activate or not confirmation or not ssh_cidr:
            raise RuntimeError('UFW inactif : activation protégée exigée (--enable-ufw --ssh-cidr X/32 --confirm-firewall)')
        net = ipaddress.ip_network(ssh_cidr, strict=False)
        if net.prefixlen == 0:
            raise RuntimeError('Refus d’ouvrir SSH à tout Internet')
        if not 1 <= int(ssh_port) <= 65535:
            raise RuntimeError('Port SSH invalide')
        # When available, confirm the requested CIDR actually contains this SSH client.
        ssh_parts = os.environ.get('SSH_CONNECTION', '').split()
        if len(ssh_parts) == 4:
            client_ip = ipaddress.ip_address(ssh_parts[0])
            remote_server_port = int(ssh_parts[3])
            if client_ip not in net or int(ssh_port) != remote_server_port:
                raise RuntimeError('Le CIDR/port SSH choisi ne couvre pas la session SSH actuelle : activation refusée')
        # Add the SSH rule *before* enabling: remote access must not be lost.
        executor(['ufw', 'allow', 'from', str(net), 'to', 'any', 'port', str(ssh_port), 'proto', 'tcp'])
        executor(['ufw', 'allow', '80/tcp'])
        executor(['ufw', 'allow', '443/tcp'])
        executor(['ufw', 'default', 'deny', 'incoming'])
        executor(['ufw', 'default', 'allow', 'outgoing'])
        executor(['ufw', '--force', 'enable'])
        if not ufw_active(executor):
            raise RuntimeError('UFW non actif après activation')
        say('UFW activé avec SSH restreint et HTTP/HTTPS autorisés')

    added = ufw_added(executor)
    # Inserted denies precede any pre-existing broad ALLOW rule, on the WAN only.
    for port in PRIVATE_TCP_PORTS:
        if not has_rule(added, 'deny', interface, port):
            executor(rule_command('deny', interface, port))
            say(f'WAN {interface}: TCP/{port} bloqué')
            added = ufw_added(executor)
            if not has_rule(added, 'deny', interface, port):
                raise RuntimeError(f'Règle UFW pour {port} non vérifiable')
    # Allow containers to access HOST database listener over actual bridge interface.
    for bridge in bridges:
        for port in DATABASE_PORTS[db]:
            if not has_rule(added, 'allow', bridge, port):
                executor(rule_command('allow', bridge, port))
                say(f'Docker {bridge}: TCP/{port} autorisé vers la base hôte')
                added = ufw_added(executor)
                if not has_rule(added, 'allow', bridge, port):
                    raise RuntimeError(f'Autorisation Docker non vérifiable ({bridge}:{port})')
    say('Règles UFW pour services internes vérifiées. Vérifier aussi AWS Security Groups.')
    return {'wan': interface, 'bridges': bridges, 'active': True, 'published': []}
