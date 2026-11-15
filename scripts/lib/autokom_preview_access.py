"""Server-side visibility and per-environment WireGuard credentials."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys
import time

import autokom_wireguard as wg

HOSTS = Path('/var/lib/autokom-preview-dns')
POLICY = 'preview-access.json'
MARKER = '# AUTOKOM_WIREGUARD_ONLY'


def broker():
    main = sys.modules.get('__main__')
    if main is not None and Path(getattr(main, '__file__', '')).name == 'autokom_platform_broker.py':
        return main
    import autokom_platform_broker
    return autokom_platform_broker


def error(code, message):
    return broker().BrokerError('AUTOKOM_PREVIEW_' + code + ': ' + message)


def policy_path(root, env):
    return root / env / POLICY


def policy(root, env):
    path = policy_path(root, env)
    if not os.path.lexists(path):
        return None
    try:
        doc = json.loads(wg.read(path, 512))
        address = ipaddress.IPv4Address(doc['address'])
        if address not in wg.POOL or str(address) == wg.SERVER or doc['environment_id'] != env:
            raise ValueError
        return str(address)
    except (OSError, ValueError, KeyError, TypeError, wg.WireGuardError) as exc:
        raise error('STATE_INVALID', 'Politique privée illisible. Publication bloquée.') from exc


@contextmanager
def lock(path, busy, *, code='PEER_BUSY'):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.geteuid() or st.st_mode & 0o077 or not stat.S_ISREG(st.st_mode):
            raise error('STATE_INVALID', 'Verrou non conforme.')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise error(code, busy) from exc
        yield
    finally:
        os.close(fd)


def paths(b, host):
    name = 'autokom-customer-' + host + '.conf'
    return b.PROXY / name, b.ENABLED / name, b.STATE / ('domain-owner-' + host + '.txt')


def dns_path(host):
    return HOSTS / host


def private_render(public, client_ip):
    """Keep public TLS/ACME behavior, but only proxy on the protected listener.

    The original socket address cannot be replaced via HTTP headers. The
    loopback destination is an independent, local-only HTTPS health probe.
    """
    port = wg.HTTPS_PORT
    client = str(ipaddress.IPv4Address(client_ip))
    public_part, tls_part = public.split('}\nserver {\n', 1)
    denied = '    location / { return 404; }'
    public_part = public_part.replace('    location / { return 308 https://$host$request_uri; }', denied)
    # The public TLS listener accepts SNI but never reaches a proxy location.
    tls_header = tls_part.split('    client_max_body_size', 1)[0]
    private = re.sub(r'(?m)^    listen .*;\n', '', tls_part)
    control = (
        f'    listen 0.0.0.0:{port} ssl;\n'
        '    set $autokom_preview_addr "$server_addr:$realip_remote_addr";\n'
        '    if ($autokom_preview_addr !~ "^('
        + wg.SERVER.replace('.', '[.]') + ':' + client.replace('.', '[.]')
        + '|127[.]0[.]0[.]1:127[.]0[.]0[.]1)$") { return 404; }\n'
        '    add_header Cache-Control "private, no-store" always;\n'
    )
    return (MARKER + '\n' + public_part + '}\nserver {\n' + tls_header
            + denied + '\n}\nserver {\n' + control + private)


def publish_dns(host):
    wg.atomic(dns_path(host), f'{wg.SERVER} {host}\n', 0o644)


def remove_dns(host):
    dns_path(host).unlink(missing_ok=True)


def verify_split_dns(b, host):
    for attempt in range(5):
        # Inotify publication in dnsmasq is asynchronous; check the actual
        # private resolver, never Cloudflare/Google for a VPN-only hostname.
        try:
            reply = b.run(['dig', '@' + wg.SERVER, '+short', '+time=1', '+tries=1', 'A', host],
                          timeout=3, max_output=2048)
        except b.BrokerError:
            reply = ''
        if set(reply.split()) == {wg.SERVER}:
            return
        if attempt < 4:
            time.sleep(0.5)
    raise error('PRIVATE_DNS', 'Le résolveur WireGuard n’a pas confirmé le domaine.')


def verify_private_https(b, host, healthcheck):
    verify_split_dns(b, host)
    # Successful private HTTPS is not sufficient: prove WAN serves a 404.
    b._verify_customer_https(host, healthcheck, address='127.0.0.1', port=wg.HTTPS_PORT)
    address = b._customer_https_probe_address()
    for _ in range(4):
        try:
            if b._customer_https_probe_once(host, healthcheck, address) == 404:
                return
        except (OSError, ssl.SSLError, ValueError):
            pass
        time.sleep(0.5)
    raise error('PUBLIC_ROUTE', 'L’accès public au vhost n’a pas été confirmé fermé.')


def requested(b, params):
    env = b.valid_id(params.get('environment_id'))
    kind = params.get('kind')
    if kind != 'STAGING' and not (b.PREPROD and kind == 'PRODUCTION'):
        raise error('STAGING_ONLY', 'Option réservée à la préproduction.')
    return b, env


def _domain_names(b, env, names, *, require_all=True):
    if not isinstance(names, list) or not 1 <= len(names) <= 20:
        raise error('DOMAIN', 'Sous-domaine de préproduction requis.')
    runtime = b._runtime_dns_env()
    suffix = runtime.get('AUTOKOM_MANAGED_DOMAIN_SUFFIX', '')
    suffix = suffix.strip('.').lower()
    approved = runtime.get('AUTOKOM_PREPROD_ALLOWED_HOSTS', '').split(',')
    names = [b.valid_hostname(h) for h in names]
    if len(set(names)) != len(names) or not suffix:
        raise error('DOMAIN', 'Sous-domaines de préproduction non conformes.')
    for name in names:
        preprod_slug = name.startswith('pre.') and name.count('.') == suffix.count('.') + 2
        if (not b._managed_client_host(name, suffix) or name in ('pre.' + suffix, 'backend.' + suffix, 'www.' + suffix)
                or (not b.PREPROD and not preprod_slug)
                or (b.PREPROD and name not in {h.strip() for h in approved})):
            raise error('DOMAIN', 'Sous-domaine hors du périmètre de préproduction.')
    if not require_all:
        return names
    for row in b.STATE.glob('domain-owner-*.txt'):
        if not row.is_symlink() and row.is_file() and row.read_text().strip() == env:
            name = row.name[len('domain-owner-'):-len('.txt')]
            if name not in names:
                raise error('DOMAIN', 'Un alias actif manque à la liste. Aucune modification.')
    return names


def active_domains(b, env, names):
    active = {}
    for host in names:
        conf, link, owner = paths(b, host)
        if b._deactivate_owned_vhost(host, env, validate_only=True):
            if not conf.read_text().startswith(MARKER) and not b.tls_detected(conf.read_text()):
                raise error('DOMAIN', 'Configuration HTTPS non conforme.')
            active[host] = conf.read_text()
    return active


def endpoint(b):
    return b._runtime_dns_env().get('AUTOKOM_INGRESS_IPV4', '')


def visibility(b, params):
    _, env = requested(b, params)
    private = params.get('private')
    if type(private) is not bool:
        raise error('INVALID', 'Visibilité non reconnue.')
    _domain_names(b, env, params.get('hostnames'))
    if private:
        if (b.STATE / env / 'previous.json').exists():
            raise error('RELEASE_PENDING', 'Une publication est en cours de finalisation.')
        wg.provision(b.run)
    # Same lock ordering as runtime.deprovision. In particular an already
    # running HA reconciliation must not republish a preview during cutover.
    if (not b.PREPROD and b.delivery_configured() and
            os.path.lexists(b.STATE / env / 'current.json')):
        d = b.delivery_module()
        with lock(d.LOCK, 'Réconciliation GCP en cours.', code='HA_BUSY'):
            return b._runtime_lock_call(lambda p: _visibility(b, p, cloud_locked=True), params)
    return b._runtime_lock_call(lambda p: _visibility(b, p), params)


def _visibility(b, params, *, cloud_locked=False):
    _, env = requested(b, params)
    private = params['private']
    names = _domain_names(b, env, params.get('hostnames'))
    path = policy_path(b.STATE, env)
    old_ip = policy(b.STATE, env)
    if not private and old_ip is None:
        return {'private': False}

    if (path.parent / 'previous.json').exists():
        raise error('RELEASE_PENDING', 'Une publication est en cours de finalisation.')
    active = active_domains(b, env, names)
    current = b._load_state(env)
    if current and current.get('stack_project_id'):
        raise error('STAGING_ONLY', 'Le mode privé s’applique à une application simple.')
    if active and not current:
        raise error('STATE_INVALID', 'Version active non trouvée.')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise error('STATE_INVALID', 'Dossier runtime non conforme.')
    with lock(wg.ROOT / '.peers.lock', 'Modification WireGuard en cours. Réessayez.'):
        peers = wg.registry()
        identity = wg.scope(env, b.PREPROD)
        if old_ip and (peers.get(identity) or {}).get('address') != old_ip:
            raise error('STATE_INVALID', 'L’adresse du tunnel n’est plus réservée.')
        address = None
        if private:
            wg.ready(b.run, endpoint(b))
            if not HOSTS.is_dir() or HOSTS.is_symlink():
                raise error('VPN_UNAVAILABLE', 'Le DNS privé WireGuard n’est pas configuré.')
            address = old_ip or wg.plan(peers, identity)
            # A prior public worker may still be reachable even without its
            # DNS record. Block that route before reporting a private site.
            if not b.PREPROD and b.delivery_configured() and (current or active):
                if not cloud_locked:
                    # Runtime became active between read and lock. Retry in
                    # the established cloud -> runtime lock order.
                    raise error('HA_BUSY', 'Publication apparue pendant la préparation.')
                delivery = b.delivery_module()
                from autokom_ha_dns import marker, _change
                try:
                    for host in active:
                        if marker(host) and _change(host, env, None, False, None, retiring=True) != 'PRIMARY':
                            raise delivery.DeliveryError('Cloud DNS did not return to primary')
                    delivery.isolate_preview(env, names)
                except (delivery.DeliveryError, OSError, ValueError) as exc:
                    raise error('CLOUD_ROUTE', 'Le retrait de la publication GCP n’a pas été confirmé.') from exc
            if identity not in peers:
                wg.persist(peers, {**peers, identity: {'address': address}}, b.run)
        if not private:
            # A never-public preview has no A record. Prepare it before
            # switching the vhost and leave the VPN policy in place on delay.
            for host in active:
                if not (b.STATE / env / 'current.json').is_file():
                    raise error('RELEASE_PENDING', 'Publication locale non confirmée.')
                b._cloudflare_record(host)
                try:
                    b._check_dns(host, None, True)
                except b.BrokerError as exc:
                    if str(exc).startswith(('AUTOKOM_DNS_A_MISMATCH:', 'AUTOKOM_DIG_COMMAND_FAILED:')):
                        raise error('PUBLIC_DNS_PENDING', 'Propagation du DNS public en cours.') from exc
                    raise
        old_policy = wg.read(path, 512) if old_ip else None
        old_dns = {host: dns_path(host).exists() for host in active}
        try:
            if private:
                wg.atomic(path, json.dumps({'environment_id': env, 'address': address}) + '\n')
            for host in active:
                conf, _, _ = paths(b, host)
                content = b._nginx_config(host, current['port'], tls=True,
                                          gateway_port=current.get('gateway_port'),
                                          gateway_hostname=current.get('gateway_hostname'),
                                          vpn_address=address)
                wg.atomic(conf, content, 0o644)
                if private:
                    publish_dns(host)
                else:
                    remove_dns(host)
            if active:
                b.run(['nginx', '-t'], timeout=8)
                b.run(['systemctl', 'reload', 'nginx'], timeout=8)
                for host in active:
                    if private:
                        verify_private_https(b, host, current.get('healthcheck', '/'))
                    else:
                        b._verify_customer_https(host, current.get('healthcheck', '/'))
            if not private:
                revoke(b, env, peers=peers)
                path.unlink(missing_ok=True)
        except Exception:
            for host, contents in active.items():
                wg.atomic(paths(b, host)[0], contents, 0o644)
                if old_dns[host]:
                    publish_dns(host)
                else:
                    remove_dns(host)
            if old_policy:
                wg.atomic(path, old_policy)
            else:
                path.unlink(missing_ok=True)
            if active:
                b.run(['nginx', '-t'], timeout=8)
                b.run(['systemctl', 'reload', 'nginx'], timeout=8)
            raise
    return {'private': private}


def revoke(b, env, *, peers=None):
    identity = wg.scope(env, b.PREPROD)
    if peers is None:
        with lock(wg.ROOT / '.peers.lock', 'Modification WireGuard en cours. Réessayez.'):
            return revoke(b, env, peers=wg.registry())
    row = peers.get(identity)
    if row:
        updated = dict(peers)
        # Never reassign a tunnel identity to another app. Gracefully
        # reloading Nginx can leave old requests alive for a brief period.
        updated[identity] = {'address': row['address']}
        if updated != peers:
            wg.persist(peers, updated, b.run)


def credentials(b, params):
    _, env = requested(b, params)
    address = policy(b.STATE, env)
    if not address:
        raise error('NOT_PRIVATE', 'Activez le mode WireGuard avant de créer un accès.')
    action = params.get('operation')
    identity = wg.scope(env, b.PREPROD)
    with lock(wg.ROOT / '.peers.lock', 'Modification WireGuard en cours. Réessayez.'):
        peers = wg.registry()
        if (peers.get(identity) or {}).get('address') != address:
            raise error('STATE_INVALID', 'Configuration WireGuard incohérente.')
        issued = bool(peers[identity].get('public_key'))
        if action == 'status':
            return {'issued': issued}
        if action == 'revoke':
            revoke(b, env, peers=peers)
            return {'issued': False}
        if action != 'issue':
            raise error('INVALID', 'Opération WireGuard inconnue.')
        names = _domain_names(b, env, params.get('hostnames'))
        active = active_domains(b, env, names)
        if not active or not all(content.startswith(MARKER) for content in active.values()):
            raise error('NOT_DEPLOYED', 'Déployez la préproduction privée avant de générer le fichier.')
        public_ip, server_key = wg.ready(b.run, endpoint(b))
        return {'issued': True, 'config': wg.issue(peers, identity, b.run, public_ip, server_key)}


def retire(b, env):
    path = policy_path(b.STATE, env)
    if os.path.lexists(path):
        revoke(b, env)
        path.unlink()
