#!/usr/bin/env python3
"""Cloudflare DNS administration with a zone-scoped API token stored off Git.

No API credentials are supplied as CLI flags, URL parameters or in logging.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode
import urllib.request
import urllib.error

from autokom_dns_core import DNSError, validate_name, validate_ip

CONFIG = Path('/etc/autokom/secrets/cloudflare-dns.env')
BASE = 'https://api.cloudflare.com/client/v4'


def dotenv(path=CONFIG):
    if not path.is_file():
        raise DNSError(f'Fichier Cloudflare absent : {path}. Créer ce fichier hors Git (root:0600).')
    if path.stat().st_mode & 0o077:
        raise DNSError(f'Permissions trop ouvertes : {path}. Attendu : 0600.')
    result = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line or line.lstrip().startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        result[k.strip()] = v.strip().strip('"\'')
    return result


class Cloudflare:
    def __init__(self, config=None):
        config = config or dotenv()
        self.token = config.get('CLOUDFLARE_API_TOKEN', '')
        self.zone_id = config.get('CLOUDFLARE_ZONE_ID', '')
        if not self.token or not self.zone_id:
            raise DNSError('CLOUDFLARE_API_TOKEN et CLOUDFLARE_ZONE_ID sont requis dans cloudflare-dns.env.')
        if any(x in self.zone_id for x in '/?&#'):
            raise DNSError('CLOUDFLARE_ZONE_ID invalide.')

    def request(self, method, path, data=None, params=None):
        url = BASE + path
        if params:
            url += '?' + urlencode(params)
        raw = json.dumps(data).encode('utf-8') if data is not None else None
        req = urllib.request.Request(url, data=raw, method=method,
            headers={'Authorization': 'Bearer ' + self.token,
                     'Content-Type': 'application/json', 'User-Agent': 'AUTOKOM-DNS/1'})
        try:
            with urllib.request.urlopen(req, timeout=18) as response:
                body = json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            # Do not reflect response bodies from an external API; they may contain data.
            raise DNSError(f'Cloudflare : réponse HTTP {exc.code}. Vérifier le jeton et les droits DNS.') from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise DNSError('Cloudflare : erreur réseau ou réponse invalide.') from exc
        if body.get('success') is not True:
            errors = [str(x.get('code', '?')) for x in body.get('errors', [])]
            raise DNSError('Cloudflare : opération refusée (codes : ' + ','.join(errors[:3]) + ').')
        return body

    def records(self, record_name=None, kind=None):
        results = []
        for page in range(1, 21):
            params = {'per_page': 100, 'page': page}
            if record_name: params['name'] = record_name
            if kind: params['type'] = kind
            result = self.request('GET', f'/zones/{self.zone_id}/dns_records', params=params)
            results.extend(result.get('result') or [])
            n = result.get('result_info', {}).get('total_pages', 1)
            if page >= n:
                break
        return results

    def create(self, zone, kind, name, value, ttl=300, priority=None, proxied=False):
        zone = validate_name(zone)
        name = name.strip().lower().rstrip('.')
        if name == '@':
            name = zone
        elif '.' not in name:
            name = name + '.' + zone
        if len(name) > 253 or not re.fullmatch(r'[a-z0-9_-]+(?:\.[a-z0-9_-]+)+', name):
            raise DNSError('Nom d’enregistrement invalide (ex. _dmarc ou selector._domainkey).')
        if not (name == zone or name.endswith('.' + zone)):
            raise DNSError('Enregistrement extérieur à la zone Cloudflare.')
        kind = kind.upper()
        if kind not in ('A', 'AAAA', 'CNAME', 'NS', 'MX', 'TXT'):
            raise DNSError('Types Cloudflare : A, AAAA, CNAME, NS, MX, TXT.')
        if kind == 'A': value = validate_ip(value, 4)
        if kind == 'AAAA': value = validate_ip(value, 6)
        if kind in ('CNAME', 'NS', 'MX'): value = validate_name(value)
        if kind == 'TXT' and (not value or len(value) > 4096 or any(ord(ch) < 32 for ch in value)):
            raise DNSError('Valeur TXT invalide ou trop longue.')
        if not 60 <= int(ttl) <= 86400:
            raise DNSError('TTL invalide (60 à 86400).')
        if proxied and kind not in ('A', 'AAAA', 'CNAME'):
            raise DNSError('Proxy Cloudflare réservé à A, AAAA et CNAME.')
        payload = {'type': kind, 'name': name, 'content': value.strip(), 'ttl': int(ttl)}
        if kind in ('A', 'AAAA', 'CNAME'): payload['proxied'] = bool(proxied)
        if kind == 'MX':
            if priority is None or not 0 <= int(priority) <= 65535:
                raise DNSError('MX exige --priority entre 0 et 65535.')
            payload['priority'] = int(priority)
        # Idempotency: rerunning an AUTOKOM DNS play must not duplicate a record.
        for item in self.records(record_name=name, kind=kind):
            if item.get('name') == name and item.get('content') == payload['content']:
                if kind != 'MX' or int(item.get('priority', -1)) == int(priority):
                    if kind in ('A', 'AAAA', 'CNAME') and bool(item.get('proxied')) != bool(proxied):
                        raise DNSError('Proxy Cloudflare différent : corriger cet enregistrement existant avant de continuer.')
                    item['_autokom_unchanged'] = True
                    return item
        return self.request('POST', f'/zones/{self.zone_id}/dns_records', data=payload)['result']

    def ensure_jenkins_a(self, zone, hostname, ipv4):
        """Reconcile only the admin-selected Jenkins host with the VM public IPv4.

        Never change zone apex, unrelated records, CNAME, AAAA or NS entries.
        Existing records' proxy mode/TTL are preserved. No delete operation.
        """
        zone = validate_name(zone).lower().rstrip('.')
        hostname = validate_name(hostname).lower().rstrip('.')
        ipv4 = validate_ip(ipv4, 4)
        if hostname == zone or not hostname.endswith('.' + zone):
            raise DNSError('Cloudflare Jenkins : hôte extérieur à la zone, ou domaine racine interdit.')
        if hostname.split('.')[0] in ('ns1', 'ns2', 'infra', 'origin'):
            raise DNSError('Cloudflare Jenkins : nom de service DNS réservé, refus de modification.')
        # Cloudflare validates that the record belongs to its zone ID. Avoid
        # calling GET /zones/{id}, which requires an extra Zone Read grant:
        # DNS Read + DNS Write scoped to this zone are sufficient.
        records = [rec for rec in self.records(record_name=hostname)
                   if str(rec.get('name', '')).lower().rstrip('.') == hostname]
        aliases = [rec for rec in records if rec.get('type') in ('CNAME', 'AAAA', 'NS')]
        if aliases:
            raise DNSError('Cloudflare Jenkins : CNAME, AAAA ou NS préexistant, conservé ; ne peut pas ajouter A.')
        addresses = [rec for rec in records if rec.get('type') == 'A']
        if len(addresses) > 1:
            raise DNSError('Cloudflare Jenkins : plusieurs A existants, modification automatique refusée.')
        if addresses:
            current = addresses[0]
            if current.get('content') == ipv4:
                return 'unchanged'
            record_id = str(current.get('id') or '')
            if not re.fullmatch(r'[a-fA-F0-9]{32}', record_id):
                raise DNSError('Cloudflare Jenkins : identifiant du A existant invalide.')
            # Patch content only; no change in TTL, proxy mode or record metadata.
            self.request('PATCH', f'/zones/{self.zone_id}/dns_records/{record_id}',
                         data={'content': ipv4})
            return 'updated'
        self.request('POST', f'/zones/{self.zone_id}/dns_records', data={
            'type': 'A', 'name': hostname, 'content': ipv4,
            'ttl': 120, 'proxied': False,
        })
        return 'created'

    def delete(self, record_id):
        if not record_id.isalnum() or len(record_id) > 64:
            raise DNSError('Identifiant d’enregistrement Cloudflare invalide.')
        return self.request('DELETE', f'/zones/{self.zone_id}/dns_records/{record_id}')
