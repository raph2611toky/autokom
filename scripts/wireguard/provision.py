#!/usr/bin/env python3
"""Converge the VPN and its firewall on first use, outside the web/broker sandbox."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'lib'))
import autokom_wireguard as wg
from observability_dns01 import plugin_available

STATUS = Path('/run/autokom-preview-provision/status.json')
UNITS = ('autokom-preview-vpn.service', 'autokom-preview-dns.service')
NAME = re.compile(r'[a-z][a-z0-9-]{0,62}\Z')
ZONE = re.compile(r'[a-z]+-[a-z0-9]+[0-9]-[a-z]\Z')
OWNED_RULE = 'AUTOKOM WireGuard preview for one instance'
VPC = re.compile(r'https://(?:www\.googleapis\.com|compute\.googleapis\.com)/compute/v1/projects/([a-z][a-z0-9-]{0,62})/global/networks/([a-z][a-z0-9-]{0,62})\Z')


class ProvisionError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = build_opener(ProxyHandler({}), NoRedirect())


def metadata(path, *, optional=False):
    req = Request('http://169.254.169.254/computeMetadata/v1/' + path,
                  headers={'Metadata-Flavor': 'Google'})
    try:
        with opener.open(req, timeout=3) as r:
            if r.headers.get('Metadata-Flavor') != 'Google':
                raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_IDENTITY')
            return r.read(16385).decode('utf-8').strip()
    except (HTTPError, URLError, OSError):
        if optional:
            return None
        raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_IDENTITY') from None


class Compute:
    def __init__(self, project):
        self.base = 'https://compute.googleapis.com/compute/v1/projects/' + project
        doc = json.loads(metadata('instance/service-accounts/default/token'))
        self.token = doc.get('access_token')
        if not isinstance(self.token, str) or len(self.token) < 20:
            raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_IAM')

    def request(self, method, route, payload=None, *, missing=False):
        req = Request(self.base + route, method=method,
                      headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'},
                      data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with opener.open(req, timeout=10) as r:
                raw = r.read(1048577)
                if len(raw) > 1048576:
                    raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_RESPONSE')
                return json.loads(raw)
        except HTTPError as exc:
            if missing and exc.code == 404:
                return None
            if exc.code in (401, 403):
                raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_IAM') from None
            if exc.code in (409, 412, 429):
                raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_RETRY') from None
            raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_API') from None
        except (URLError, OSError, ValueError):
            raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_API') from None

    def wait(self, doc, scope):
        name = doc.get('name', '')
        if not NAME.fullmatch(name):
            raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_RESPONSE')
        for attempt in range(18):
            result = self.request('GET', f'/{scope}/operations/{name}')
            if result.get('status') == 'DONE':
                if result.get('error'):
                    raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_API')
                return
            if attempt < 17:
                time.sleep(2)
        raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_RETRY')


def udp_included(rows):
    for item in rows or []:
        if item.get('IPProtocol') in ('all', 'udp', '17'):
            ports = item.get('ports') or []
            if not ports:
                return True
            for value in ports:
                if isinstance(value, str) and re.fullmatch(r'\d{1,5}(?:-\d{1,5})?', value):
                    p = [int(part) for part in value.split('-')]
                    if p[0] <= wg.PORT <= p[-1]:
                        return True
    return False


def policy_blocks(effective):
    # Do not pretend classic VPC rules override a hierarchical/policy deny.
    # Treat ambiguous deny selectors conservatively without rewriting policies.
    for policy in effective.get('firewallPolicys', []):
        for rule in policy.get('rules', []):
            if rule.get('action') != 'deny' or rule.get('disabled') or rule.get('direction') != 'INGRESS':
                continue
            layers = (rule.get('match') or {}).get('layer4Configs') or []
            if not layers or udp_included([{'IPProtocol': x.get('ipProtocol'), 'ports': x.get('ports')}
                                           for x in layers]):
                return True
    return False


def existing_allow(effective):
    """Recognize only an all-IPv4 classic allow, not a narrower policy by mistake."""
    entries = [r for r in effective.get('firewalls', []) if not r.get('disabled') and
               r.get('direction') == 'INGRESS' and not r.get('sourceTags') and not r.get('sourceServiceAccounts')]
    allow = [r['priority'] for r in entries if '0.0.0.0/0' in (r.get('sourceRanges') or []) and
             udp_included(r.get('allowed')) and type(r.get('priority')) is int]
    deny = [r['priority'] for r in effective.get('firewalls', []) if not r.get('disabled') and
            r.get('direction') == 'INGRESS' and udp_included(r.get('denied')) and type(r.get('priority')) is int]
    return not policy_blocks(effective) and bool(allow) and (not deny or min(allow) < min(deny))


def gcp_firewall():
    project = metadata('project/project-id', optional=True)
    if project is None:
        # Do not silently assume an inaccessible GCE metadata server means bare metal.
        for path in ('/sys/class/dmi/id/product_name', '/sys/class/dmi/id/sys_vendor'):
            if Path(path).is_file() and 'google' in Path(path).read_text().lower():
                raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_IDENTITY')
        return
    zone = metadata('instance/zone').rsplit('/', 1)[-1]
    name = metadata('instance/name')
    if not (NAME.fullmatch(project) and ZONE.fullmatch(zone) and NAME.fullmatch(name)):
        raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_IDENTITY')
    api = Compute(project)
    route = f'/zones/{zone}/instances/{name}'
    instance = api.request('GET', route)
    if instance.get('name') != name or instance.get('name', '').startswith('autokom-ha-web-'):
        raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_IDENTITY')
    public = [(nic, a['natIP']) for nic in instance.get('networkInterfaces', [])
              for a in nic.get('accessConfigs', []) if a.get('type') == 'ONE_TO_ONE_NAT' and a.get('natIP')]
    if len(public) != 1:
        raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_ENDPOINT')
    nic, address = public[0]
    wg.endpoint(address)
    expected = []
    from autokom_env_manager import read_env
    for path in (Path('/etc/autokom/secrets/runtime.env'), Path('/etc/autokom-preprod/runtime.env')):
        if path.is_file() and not path.is_symlink() and path.stat().st_uid == 0 and not path.stat().st_mode & 0o077:
            value = read_env(path).get('AUTOKOM_INGRESS_IPV4')
            if value:
                expected.append(value)
    if address not in expected:
        raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_ENDPOINT')
    match = VPC.fullmatch(nic.get('network', ''))
    if not match or not NAME.fullmatch(nic.get('name', '')):
        raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_IDENTITY')

    query = urlencode({'networkInterface': nic['name']})
    effective_route = route + '/getEffectiveFirewalls?' + query
    effective = api.request('GET', effective_route)
    if policy_blocks(effective):
        raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_POLICY')
    if existing_allow(effective):
        return
    # A classic deny at higher priority must not be changed or bypassed.
    if any(r.get('direction') == 'INGRESS' and not r.get('disabled') and
           udp_included(r.get('denied')) and r.get('priority', 1000) <= 1000
           for r in effective.get('firewalls', [])):
        raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_POLICY')
    owner = instance.get('id', '')
    if not isinstance(owner, str) or not re.fullmatch(r'[0-9]{1,24}', owner):
        raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_IDENTITY')
    tag = 'autokom-preview-' + owner
    network_project, network = match.groups()
    if network_project != project:
        raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_SHARED_VPC')
    full_network = f'projects/{project}/global/networks/{network}'
    rule = {'name': tag, 'description': OWNED_RULE, 'network': full_network,
            'direction': 'INGRESS', 'priority': 1000, 'sourceRanges': ['0.0.0.0/0'],
            'targetTags': [tag], 'allowed': [{'IPProtocol': 'udp', 'ports': [str(wg.PORT)]}]}
    path = '/global/firewalls/' + tag
    previous = api.request('GET', path, missing=True)
    if previous is None:
        api.wait(api.request('POST', '/global/firewalls', rule), 'global')
    elif (str(previous.get('network', '')).removeprefix('https://www.googleapis.com/compute/v1/')
          .removeprefix('https://compute.googleapis.com/compute/v1/') != full_network
          or any(previous.get(k) != rule[k] for k in rule if k != 'network')
          or previous.get('disabled') or previous.get('sourceTags') or previous.get('sourceServiceAccounts')):
        raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_CONFLICT')
    tags = instance.get('tags') or {}
    items = list(tags.get('items') or [])
    if tag not in items:
        fingerprint = tags.get('fingerprint')
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_RESPONSE')
        api.wait(api.request('POST', route + '/setTags',
                             {'fingerprint': fingerprint, 'items': items + [tag]}), 'zones/' + zone)
    if not existing_allow(api.request('GET', effective_route)):
        raise ProvisionError('AUTOKOM_PREVIEW_CLOUD_RETRY')


def checked(argv, *, timeout=35, code='AUTOKOM_PREVIEW_HOST_SETUP'):
    try:
        return subprocess.run(argv, check=True, timeout=timeout, capture_output=True, text=True,
                              env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive', 'LC_ALL': 'C'}).stdout
    except (subprocess.SubprocessError, OSError):
        raise ProvisionError(code) from None


def packages():
    wanted = [pkg for tool, pkg in (('wg', 'wireguard-tools'), ('nft', 'nftables'),
                                   ('dnsmasq', 'dnsmasq-base'), ('dig', 'dnsutils'))
              if not shutil.which(tool)]
    if not plugin_available():
        wanted.append('python3-certbot-dns-cloudflare')
    if not wanted:
        return
    checked(['apt-get', '-o', 'Acquire::Retries=2', 'update'], timeout=120,
            code='AUTOKOM_PREVIEW_PACKAGES')
    checked(['apt-get', '-o', 'DPkg::Lock::Timeout=45', 'install', '-y', '--no-install-recommends', *wanted],
            timeout=220, code='AUTOKOM_PREVIEW_PACKAGES')
    if not plugin_available():
        raise ProvisionError('AUTOKOM_PREVIEW_ACME_PLUGIN')


def local_firewall():
    if not shutil.which('ufw') or not checked(['ufw', 'status']).startswith('Status: active'):
        return
    commands = [['allow', f'{wg.PORT}/udp']]
    for port, proto in ((wg.HTTPS_PORT, 'tcp'), (53, 'udp'), (53, 'tcp')):
        commands.append(['allow', 'in', 'on', wg.DEVICE, 'to', wg.SERVER,
                         'port', str(port), 'proto', proto])
    for command in commands:
        checked(['ufw', *command])


def tls_renewal():
    where = Path('/etc/letsencrypt/renewal-hooks/deploy')
    where.mkdir(parents=True, exist_ok=True)
    for folder in (where, where.parent, where.parent.parent):
        if folder.is_symlink() or folder.stat().st_uid != 0 or folder.stat().st_mode & 0o022:
            raise ProvisionError('AUTOKOM_PREVIEW_ACME_RENEWAL')
    hook = where / 'autokom-reload-nginx.sh'
    if hook.is_symlink() or (hook.exists() and (not hook.is_file() or hook.stat().st_uid != 0
                                               or hook.stat().st_mode & 0o022)):
        raise ProvisionError('AUTOKOM_PREVIEW_ACME_RENEWAL')
    if not hook.exists():
        wg.atomic(hook, '#!/bin/sh\n/usr/sbin/nginx -t && /bin/systemctl reload nginx\n', 0o755)
    state = checked(['systemctl', 'show', '--property=LoadState', '--value', 'certbot.timer']).strip()
    if state == 'loaded':
        checked(['systemctl', 'enable', '--now', 'certbot.timer'], code='AUTOKOM_PREVIEW_ACME_RENEWAL')
    else:
        # Snap manages its own renewal service and timer. Don't duplicate it.
        snap = checked(['systemctl', 'show', '--property=LoadState', '--value',
                        'snap.certbot.renew.timer']).strip()
        if snap != 'loaded':
            raise ProvisionError('AUTOKOM_PREVIEW_ACME_RENEWAL')
        checked(['systemctl', 'is-active', '--quiet', 'snap.certbot.renew.timer'])


def report(state, code=None):
    wg.atomic(STATUS, json.dumps({'state': state, 'code': code, 'updated': time.time()}) + '\n')


def main():
    if os.geteuid():
        raise SystemExit('AUTOKOM_PREVIEW_ROOT_REQUIRED')
    report('pending')
    try:
        packages()
        tls_renewal()
        checked(['bash', str(HERE / 'setup.sh')], timeout=90)
        local_firewall()
        gcp_firewall()
        for unit in UNITS:
            checked(['systemctl', 'is-active', '--quiet', unit])
    except (ProvisionError, wg.WireGuardError, OSError, TypeError, ValueError, KeyError) as exc:
        code = str(exc) if isinstance(exc, ProvisionError) else 'AUTOKOM_PREVIEW_HOST_SETUP'
        report('failed', code)
        print(code, file=sys.stderr)
        return 1
    report('ready')
    print('AUTOKOM_PREVIEW_READY')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
