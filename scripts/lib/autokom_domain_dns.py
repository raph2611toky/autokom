#!/usr/bin/env python3
"""Cloudflare records for parallel migrations. The zone apex (@) is the A anchor.

Never alter or silently create a zone-apex record: it may serve existing services.
An A of @ already managed in Cloudflare points to the authorized ingress IPv4.
CNAMEs to a proxied apex inherit Cloudflare proxy behavior. There is no extra
origin hostname, server or registry. A legacy target A is never overwritten
without explicit opt-in after HTTPS verification.
"""
from __future__ import annotations
from dataclasses import dataclass
import ipaddress

class DNSMigrationError(RuntimeError): pass

@dataclass(frozen=True)
class DNSPlan:
    zone: str
    hostname: str
    origin: str  # zone apex, not origin.<zone> (compatibility with old callers)
    ip: str
    origin_record: dict
    site_record: dict | None
    site_mode: str
    site_proxied: bool


def _records(cf, name):
    return [r for r in cf.records(record_name=name)
            if r.get('name','').lower().rstrip('.') == name]


def plan(cf, zone, hostname, ip):
    zone=zone.strip().lower().rstrip('.')
    hostname=hostname.strip().lower().rstrip('.')
    if hostname==zone or not hostname.endswith('.'+zone):
        raise DNSMigrationError('Une application doit utiliser un sous-domaine de la zone.')
    if hostname.startswith(('ns1.','ns2.','infra.','origin.')):
        raise DNSMigrationError('Nom réservé / DNS critique exclu des migrations web.')
    try:
        if not ipaddress.IPv4Address(ip).is_global: raise ValueError('not global')
    except ValueError as exc:raise DNSMigrationError('IPv4 publique invalide.') from exc
    # Apex can also have TXT/MX/CAA: ignore those, reject additional address records.
    apex = [r for r in _records(cf,zone) if r.get('type') in ('A','AAAA','CNAME')]
    if len(apex)!=1 or apex[0].get('type')!='A' or apex[0].get('content')!=ip:
        raise DNSMigrationError('Le A racine (@) doit exister et pointer sur AUTOKOM_INGRESS_IPV4 ; aucune modification automatique du domaine principal.')
    a=apex[0]
    site=_records(cf,hostname)
    if len(site)>1:raise DNSMigrationError('Plusieurs enregistrements pour la destination : refus.')
    s=site[0] if site else None
    if s is None: mode='new'
    elif s.get('type')=='CNAME' and s.get('content','').lower().rstrip('.')==zone:
        mode='cname'
    elif s.get('type')=='A' and s.get('content')==ip and not s.get('proxied',False):
        mode='legacy_a'
    else:
        raise DNSMigrationError('Destination DNS occupée par un autre enregistrement : aucune modification.')
    # DNS-only CNAME pointing to proxied apex is effectively proxied.
    return DNSPlan(zone,hostname,zone,ip,a,s,mode,bool(a.get('proxied',False)))


def ensure(cf,p):
    current=plan(cf,p.zone,p.hostname,p.ip)
    if current.site_mode!=p.site_mode:
        raise DNSMigrationError('La zone DNS a changé depuis le plan : relancer l’audit.')
    if current.site_mode=='new':
        cf.create(p.zone,'CNAME',p.hostname,p.zone,ttl=120,proxied=p.site_proxied)
    return current.site_mode


def convert_legacy_a(cf,p):
    """Only after successful HTTPS and confirmation; best effort restore original A."""
    cur=plan(cf,p.zone,p.hostname,p.ip)
    if cur.site_mode=='cname':return 'already_cname'
    if cur.site_mode!='legacy_a':raise DNSMigrationError('Conversion uniquement depuis un A existant.')
    record=cur.site_record
    record_id=record.get('id','')
    if not record_id:raise DNSMigrationError('ID DNS absent : conversion refusée.')
    cf.delete(record_id)
    try:
        cf.create(p.zone,'CNAME',p.hostname,p.zone,ttl=120,proxied=p.site_proxied)
    except Exception as exc:
        try:
            cf.create(p.zone,'A',p.hostname,p.ip,ttl=int(record.get('ttl') or 120),proxied=False)
        except Exception as restore_exc:
            raise DNSMigrationError('Conversion échouée, restauration DNS impossible : intervention nécessaire.') from restore_exc
        raise DNSMigrationError('Conversion CNAME refusée : A restauré.') from exc
    return 'converted'


def inspect_obsolete_origin(cf,zone,ip):
    """Read-only: NEVER delete origin.<zone> if referenced by any CNAME.

    Used solely by explicit maintenance subcommand. Do not touch root A or NS.
    """
    old='origin.'+zone
    all_records=cf.records()
    candidates=[r for r in all_records if r.get('name','').lower().rstrip('.')==old]
    if not candidates:return None,[]
    references=[r.get('name','') for r in all_records if r.get('type')=='CNAME' and r.get('content','').lower().rstrip('.')==old]
    if len(candidates)!=1 or candidates[0].get('type')!='A' or candidates[0].get('content')!=ip or candidates[0].get('proxied'):
        raise DNSMigrationError('Ancien origin modifié ou non administré : suppression interdite.')
    return candidates[0],references
