#!/usr/bin/env python3
"""Safe Cloudflare CNAME routing for parallel AUTOKOM domain migrations.

The only centrally managed A record is origin.<zone>. Individual HTTP
subdomains are DNS-only CNAMEs to that hostname. Pre-existing A records are
preserved until an explicit, confirmed conversion after a successful HTTPS
migration. No .env files, credentials, or arbitrary DNS records are written by
this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress


class DNSMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DNSPlan:
    zone: str
    hostname: str
    origin: str
    ip: str
    origin_record: dict | None
    site_record: dict | None
    site_mode: str  # new, cname, legacy_a


def _single(cf, name):
    records = [r for r in cf.records(record_name=name) if r.get('name', '').rstrip('.').lower() == name]
    if len(records) > 1:
        raise DNSMigrationError('DNS Cloudflare : plusieurs enregistrements existent pour ' + name)
    return records[0] if records else None


def plan(cf, zone, hostname, ip, origin_label='origin'):
    zone = zone.lower().rstrip('.')
    hostname = hostname.lower().rstrip('.')
    if not hostname.endswith('.' + zone):
        raise DNSMigrationError('Le nom migré doit être un sous-domaine de la zone Cloudflare.')
    if hostname.startswith(('ns1.', 'ns2.', 'infra.')):
        raise DNSMigrationError('DNS critique interdit pour les migrations web.')
    if origin_label not in ('origin', 'edge'):
        raise DNSMigrationError('Nom du point d’entrée DNS non autorisé.')
    origin = origin_label + '.' + zone
    if hostname == origin:
        raise DNSMigrationError('La destination ne peut pas remplacer le point d’entrée DNS.')
    try:
        if not ipaddress.IPv4Address(ip).is_global:
            raise ValueError('non publique')
    except ValueError as exc:
        raise DNSMigrationError('IP publique valide requise.') from exc
    o = _single(cf, origin)
    if o and (o.get('type') != 'A' or o.get('content') != ip or bool(o.get('proxied'))):
        raise DNSMigrationError('Point d’entrée DNS déjà configuré autrement : ' + origin)
    s = _single(cf, hostname)
    if s is None:
        mode = 'new'
    elif s.get('type') == 'CNAME' and s.get('content', '').rstrip('.').lower() == origin and not bool(s.get('proxied')):
        mode = 'cname'
    elif s.get('type') == 'A' and s.get('content') == ip and not bool(s.get('proxied')):
        mode = 'legacy_a'
    else:
        raise DNSMigrationError('DNS destination déjà occupé : aucune suppression ou modification automatique.')
    return DNSPlan(zone, hostname, origin, ip, o, s, mode)


def ensure(cf, p):
    # Re-plan immediately before writing to protect against API races.
    current = plan(cf, p.zone, p.hostname, p.ip)
    if current.site_mode != p.site_mode:
        raise DNSMigrationError('La zone DNS a changé depuis la prévisualisation : relancer la commande.')
    if current.origin_record is None:
        cf.create(p.zone, 'A', p.origin, p.ip, ttl=120, proxied=False)
    if current.site_mode == 'new':
        cf.create(p.zone, 'CNAME', p.hostname, p.origin, ttl=120, proxied=False)
    return current.site_mode


def convert_legacy_a(cf, p):
    """Explicit-only conversion, with best-effort restoration of the old A.

    Call after HTTPS is serving the new hostname. Caller must request a typed
    confirmation. Never convert a proxied/unrelated/multi-value DNS record.
    """
    current = plan(cf, p.zone, p.hostname, p.ip)
    if current.site_mode == 'cname':
        return 'already_cname'
    if current.site_mode != 'legacy_a':
        raise DNSMigrationError('Seul un enregistrement A préexistant peut être converti.')
    old = current.site_record
    record_id = old.get('id', '')
    if not record_id:
        raise DNSMigrationError('Identifiant DNS absent : conversion refusée.')
    # Zone must still have valid centrally controlled A record.
    if not current.origin_record:
        raise DNSMigrationError('CNAME impossible sans le point d’entrée DNS central.')
    cf.delete(record_id)
    try:
        cf.create(p.zone, 'CNAME', p.hostname, p.origin, ttl=120, proxied=False)
    except Exception as exc:
        try:
            cf.create(p.zone, 'A', p.hostname, p.ip, ttl=int(old.get('ttl') or 120), proxied=False)
        except Exception as restore_exc:
            raise DNSMigrationError('Conversion refusée et restauration DNS échouée : vérifier Cloudflare immédiatement.') from restore_exc
        raise DNSMigrationError('Conversion CNAME échouée : ancien A restauré.') from exc
    return 'converted'
