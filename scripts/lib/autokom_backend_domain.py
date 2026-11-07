#!/usr/bin/env python3
"""Parallel AUTOKOM Django backend hostname migration, without touching frontend.

Root-operated Core control plane only; Django never shells out. Public DNS must
be authoritative on a configured Cloudflare zone. Root-only env survives in
/etc/autokom/secrets/backend.env. Old hostname, MySQL and GitHub callbacks stay.
"""
from __future__ import annotations

from autokom_nginx_bind_policy import https_listens

import argparse
from dataclasses import dataclass, replace
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
import time

import autokom_domain_migration as dm
from autokom_domain_dns import plan as dns_plan, ensure as dns_ensure, DNSMigrationError
from autokom_env_manager import file_path, read_env, write_env, backup
from autokom_dns_cloudflare import Cloudflare
from autokom_app_manager import STATE_FILE, SERVICE, AppError, verify_health

AVAILABLE = Path('/etc/nginx/sites-available')
ENABLED = Path('/etc/nginx/sites-enabled')
ACME = Path('/var/lib/autokom/acme')
CERTS = Path('/etc/letsencrypt/live')
MANAGED = '# AUTOKOM BACKEND DOMAIN V1'


class DomainError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackendDomainPlan:
    legacy: str
    target: str
    zone: str
    port: int
    ingress: str
    acme_email: str
    dns_mode: str
    frontend: str


def _read_state():
    if STATE_FILE.is_symlink() or not STATE_FILE.is_file():
        raise DomainError('Backend AUTOKOM non configuré : état système absent.')
    try:
        state = json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise DomainError('État du backend illisible.') from exc
    if state.get('runtime') != 'systemd':
        raise DomainError('Migration du backend actuellement réservée à systemd/Gunicorn.')
    return state


def _domain(value):
    try:
        return dm.check_hostname(value)
    except dm.MigrationError as exc:
        raise DomainError(str(exc)) from exc


def _derive(legacy, zone, explicit=None):
    legacy = _domain(legacy)
    zone = _domain(zone)
    if explicit:
        target = _domain(explicit)
    elif legacy.endswith('.ik0m.tech'):
        target = legacy[:-len('ik0m.tech')] + zone
    else:
        raise DomainError('Domaine source non standard : fournir --target explicitement.')
    if not target.startswith('backend.') or not target.endswith('.' + zone) or target == legacy:
        raise DomainError('Domaine backend non autorisé ou extérieur à la zone Cloudflare.')
    if any(label in ('ns1','ns2','infra','origin') for label in target.split('.')[:-2]):
        raise DomainError('Nom DNS critique/réservé interdit.')
    return target


def _is_deep_domain(host, zone):
    # Universal SSL of a full Cloudflare zone only covers apex and *.zone.
    return len(host[:-len(zone)-1].split('.')) > 1


def create_plan(*, target=None, dns_mode='auto'):
    state = _read_state()
    runtime = read_env(file_path('runtime'))
    cloudflare = read_env(file_path('cloudflare'))
    backend = read_env(file_path('backend'))
    if not backend:
        raise DomainError('Backend centralisé absent : autokom app backend env setup.')
    zone = _domain(cloudflare.get('CLOUDFLARE_ZONE_NAME') or '')
    old = _domain(state.get('domain') or '')
    new = _derive(old, zone, target)
    if dns_mode == 'auto':
        dns_mode = 'a' if _is_deep_domain(new, zone) else 'cname'
    if dns_mode not in ('cname', 'a'):
        raise DomainError('Utiliser --dns-mode cname ou a.')
    try:
        port = int(state.get('port', 8172))
    except (ValueError, TypeError) as exc:
        raise DomainError('Port du backend invalide dans son état.') from exc
    if port < 1024 or port > 65535:
        raise DomainError('Port du backend hors plage autorisée.')
    ingress = runtime.get('AUTOKOM_INGRESS_IPV4','')
    try:
        if not ipaddress.IPv4Address(ingress).is_global:
            raise ValueError('not global')
    except ValueError as exc:
        raise DomainError('Configurer AUTOKOM_INGRESS_IPV4 dans runtime env.') from exc
    email = runtime.get('AUTOKOM_ACME_EMAIL', '')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise DomainError('Configurer AUTOKOM_ACME_EMAIL dans runtime env.')
    return BackendDomainPlan(old,new,zone,port,ingress,email,dns_mode,
                              backend.get('FRONTEND_URL', state.get('frontend','')))


def _site_name(host):
    return 'autokom-backend-domain-' + host + '.conf'


def _nginx_config(p, *, tls):
    # No configuration lines copied from potentially untrusted vhost input.
    base = f'''{MANAGED}
# Ancien backend conservé : {p.legacy}
# Backend Gunicorn : 127.0.0.1:{p.port}
server {{
    listen 80;
    server_name {p.target};
    location ^~ /.well-known/acme-challenge/ {{
        root /var/lib/autokom/acme;
        default_type text/plain;
    }}
'''
    proxy = f'''    location /static/ {{
        alias /var/lib/autokom/staticfiles/;
        autoindex off;
    }}
    location / {{
        proxy_pass http://127.0.0.1:{p.port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 90s;
    }}
'''
    if not tls:
        return base + '    client_max_body_size 20m;\n' + proxy + '}\n'
    certificate = str(CERTS / p.target)
    return (base + '    location / { return 308 https://$host$request_uri; }\n}\n'
            +f'''server {{
{https_listens().rstrip()}
    server_name {p.target};
    ssl_certificate {certificate}/fullchain.pem;
    ssl_certificate_key {certificate}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 20m;
''' + proxy + '}\n')


def _check_claim(p):
    """Refuse other active vhosts using the target, including wildcard matches."""
    own = _site_name(p.target)
    for directory in (ENABLED, Path('/etc/nginx/conf.d')):
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            if entry.name.startswith('.') or not entry.is_file() or (directory == ENABLED and entry.name == own):
                continue
            try:
                data = entry.read_text(encoding='utf-8')
            except (OSError, UnicodeError) as exc:
                raise DomainError('Lecture Nginx impossible : validation des conflits arrêtée.') from exc
            # Comments excluded from collision checks.
            data = '\n'.join(line.partition('#')[0] for line in data.splitlines())
            for match in re.findall(r'(?m)^\s*server_name\s+([^;]+);', data):
                for host in match.lower().split():
                    if host == p.target or (host.startswith('*.') and p.target.endswith(host[1:])):
                        raise DomainError('Nom de domaine déjà utilisé par Nginx : ' + entry.name)


def _run_ok(command, *, timeout=25):
    result = dm.run(command, timeout=timeout)
    if result.returncode:
        raise DomainError('Commande système échouée : ' + Path(command[0]).name + ' (voir journal).')
    return result


def _nginx_commit(p, *, tls):
    available = AVAILABLE / _site_name(p.target)
    enabled = ENABLED / available.name
    if available.is_symlink():
        raise DomainError('Vhost cible sous forme de lien symbolique : refus.')
    if enabled.is_symlink() and enabled.resolve() != available.resolve():
        raise DomainError('Activation Nginx pointant vers un autre fichier.')
    if enabled.exists() and not enabled.is_symlink():
        raise DomainError('Vhost existant non géré : refus.')
    old = available.read_text(encoding='utf-8') if available.exists() else None
    allowed = {_nginx_config(p,tls=False), _nginx_config(p,tls=True)}
    if old is not None and old not in allowed:
        raise DomainError('Vhost AUTOKOM modifié manuellement : écrasement refusé.')
    if old == _nginx_config(p, tls=True) and not tls:
        return False  # resume must never downgrade from HTTPS to HTTP
    content = _nginx_config(p,tls=tls)
    if old == content and enabled.is_symlink():
        return False
    _check_claim(p)
    if not AVAILABLE.is_dir() or not ENABLED.is_dir():
        raise DomainError('Structure Nginx sites-available/sites-enabled absente.')
    was_linked = enabled.is_symlink()
    try:
        dm.atomic_text(available, content)
        if not was_linked:
            enabled.symlink_to(available)
        _run_ok(['nginx','-t'],timeout=20)
        _run_ok(['systemctl','reload','nginx'],timeout=20)
        return True
    except Exception:
        if old is None:
            available.unlink(missing_ok=True)
        else:
            dm.atomic_text(available,old)
        if not was_linked and enabled.is_symlink():
            enabled.unlink()
        dm.run(['nginx','-t'],timeout=20)
        dm.run(['systemctl','reload','nginx'],timeout=20)
        raise


def _dns_current(cf,p):
    """Verify apex and destination, never overwrite unrelated DNS records."""
    try:
        dns = dns_plan(cf,p.zone,p.target,p.ingress)
    except DNSMigrationError as exc:
        raise DomainError(str(exc)) from exc
    # A pre-existing CNAME can be converted only after local TLS and Django
    # have been validated. It requires an additional typed confirmation.
    if p.dns_mode == 'a' and dns.site_mode == 'cname' and not dns.site_record.get('id'):
        raise DomainError('CNAME existant sans identifiant Cloudflare : conversion refusée.')
    if p.dns_mode == 'cname' and dns.site_mode == 'legacy_a':
        # No A->CNAME conversion during backend cutover: prevent downtime.
        return dns
    return dns


def _dns_ensure(cf,p,dns):
    if dns.site_mode == 'cname' and p.dns_mode == 'a':
        return 'CNAME existant préservé pendant la validation HTTPS'
    if dns.site_mode != 'new':
        return 'Enregistrement DNS existant conservé'
    if p.dns_mode == 'a':
        cf.create(p.zone,'A',p.target,p.ingress,ttl=120,proxied=False)
        return 'A DNS-only créé'
    dns_ensure(cf,dns)
    return 'CNAME créé vers le domaine racine'


def _convert_cname_to_a(cf, p):
    """Explicitly authorized CNAME->A conversion with best-effort restoration.

    The target is allowed only if it is still the zone-apex CNAME found by
    the strict DNS planner. No unrelated DNS record can be overwritten.
    """
    current = _dns_current(cf, p)
    if current.site_mode == 'legacy_a':
        return 'Déjà en A DNS-only'
    if current.site_mode != 'cname' or p.dns_mode != 'a':
        raise DomainError('Le DNS a changé : conversion refusée.')
    old = current.site_record
    if not old or not old.get('id'):
        raise DomainError('Identifiant DNS absent : conversion refusée.')
    cf.delete(old['id'])
    try:
        cf.create(p.zone, 'A', p.target, p.ingress, ttl=120, proxied=False)
    except Exception as exc:
        try:
            cf.create(p.zone, 'CNAME', p.target, p.zone,
                      ttl=int(old.get('ttl') or 120), proxied=bool(old.get('proxied')))
        except Exception as restore_exc:
            raise DomainError('Conversion DNS échouée et restauration impossible : intervention Cloudflare nécessaire.') from restore_exc
        raise DomainError('Conversion DNS échouée : CNAME précédent restauré.') from exc
    return 'CNAME remplacé par A DNS-only (AWS)'


def _wait_for_health(port, *, timeout=30, interval=1):
    """Give systemd/Gunicorn time to start after a successful restart."""
    deadline = time.monotonic() + timeout
    while True:
        if verify_health(port=port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


def _backend_values(p):
    existing = read_env(file_path('backend'))
    if not existing:
        raise DomainError('backend.env absent : configuration impossible.')
    result = existing.copy()
    for key,value in [('ALLOWED_HOSTS',p.target),('CSRF_TRUSTED_ORIGINS','https://'+p.target)]:
        items = [x.strip() for x in existing.get(key,'').split(',') if x.strip()]
        if value not in items: items.append(value)
        result[key] = ','.join(items)
    # FRONTEND_URL, CORS_ALLOWED_ORIGINS, JWT/MySQL/SMTP/GitHub OAuth remain unchanged.
    return existing,result


def _apply_backend_env(p):
    before,after=_backend_values(p)
    if before == after:
        return False
    backup(file_path('backend'))
    write_env(file_path('backend'),after)
    try:
        _run_ok(['systemctl','restart',SERVICE],timeout=45)
        if not _wait_for_health(p.port, timeout=30):
            raise DomainError('Django ne répond pas après le redémarrage (30 s). '
                              'Examiner journalctl -u autokom-backend et les logs AUTOKOM.')
    except Exception:
        write_env(file_path('backend'),before)
        dm.run(['systemctl','restart',SERVICE],timeout=45)
        raise
    return True


def _tls_probe(p):
    response = _run_ok(['curl','--noproxy','*','--silent','--show-error',
                        '--output','/dev/null','--write-out','%{http_code}',
                        '--resolve',p.target+':443:127.0.0.1',
                        '--max-time','15','https://'+p.target+'/api/health/'],timeout=18)
    if response.stdout.strip() != '200':
        raise DomainError('Le healthcheck HTTPS du nouveau domaine ne répond pas HTTP 200.')
    return True


def _write_state(p):
    state = _read_state()
    aliases = [s for s in state.get('domain_aliases',[]) if isinstance(s,str)]
    if p.target not in aliases:
        aliases.append(p.target)
    state['domain_aliases'] = aliases
    state['preferred_domain'] = p.target
    # Never change the configured original domain: setup/recovery still recognize it.
    dm.atomic_text(STATE_FILE,json.dumps(state,ensure_ascii=False,indent=2)+'\n',mode=0o600)


def _display_plan(p,dns=None):
    print('  ◈ AUTOKOM · BACKEND · MIGRATION DE DOMAINE')
    print('  › Service source : https://'+p.legacy)
    print('  › Nouveau domaine : https://'+p.target)
    print('  › Runtime actuel : systemd / Gunicorn sur 127.0.0.1:'+str(p.port))
    print('  › DNS : '+('CNAME → '+p.zone if p.dns_mode=='cname' else 'A DNS-only → '+p.ingress))
    if _is_deep_domain(p.target, p.zone) and p.dns_mode == 'cname':
        print('  ! Cloudflare Free : ce domaine à plusieurs niveaux exige un certificat Edge supplémentaire si proxifié.')
    if p.dns_mode == 'a':
        print('  › HTTPS géré par Certbot sur AWS ; pas de proxy Cloudflare pour ce nom.')
    if dns:
        print('  › DNS existant : '+dns.site_mode)
        if p.dns_mode == 'cname' and dns.site_mode != 'legacy_a' and dns.site_proxied:
            print('  › Cloudflare : CNAME vers @ proxifié via la racine (ne pas supposer DNS-only).')
    print('  › Nginx : vhost séparé + challenge ACME + certificat HTTPS')
    print('  › Django : ajout ALLOWED_HOSTS et CSRF_TRUSTED_ORIGINS')
    print('  › FRONTEND_URL / CORS / OAuth GitHub / MySQL / Jenkins : inchangés')
    print('  › Ancien domaine, ancien Nginx et anciens certificats : conservés')


def _assert_active_environment():
    result = dm.run(['systemctl','show',SERVICE+'.service',
                     '--property=EnvironmentFiles','--value'],timeout=10)
    if result.returncode or str(file_path('backend')) not in result.stdout:
        raise DomainError('Le service ne référence pas /etc/autokom/secrets/backend.env. '
                          'Exécuter autokom app backend env setup puis contrôler systemd avant migration.')


def _check_local(p):
    _assert_active_environment()
    if not verify_health(port=p.port):
        # verify_health does not mutate and can inspect service TCP+HTTP.
        raise DomainError('Le backend local ne répond pas à /api/health/ : vérifier autokom app status.')
    _check_claim(p)
    entry = AVAILABLE / _site_name(p.target)
    if entry.exists():
        current=entry.read_text(encoding='utf-8')
        if current not in (_nginx_config(p,tls=False),_nginx_config(p,tls=True)):
            raise DomainError('Vhost backend existant modifié : opération interrompue.')
    return True


def _check_dns_ns(cf,p):
    result = cf.request('GET','/zones/'+cf.zone_id).get('result') or {}
    if result.get('name')!=p.zone or result.get('status')!='active':
        raise DomainError('Zone Cloudflare inactive ou Zone ID non correspondant.')
    public = dm.run(['dig','+short','NS',p.zone],timeout=10)
    delegated={line.strip().lower().rstrip('.') for line in public.stdout.splitlines() if line.strip()}
    assigned={str(n).lower().rstrip('.') for n in result.get('name_servers',[])}
    if public.returncode or not delegated or delegated!=assigned:
        raise DomainError('Délégation publique DNS non conforme à la zone Cloudflare.')
    return list(assigned)


def _apply(p):
    if os.geteuid()!=0 or not os.isatty(0):
        raise DomainError('Utiliser sudo dans un terminal interactif pour appliquer la migration.')
    _check_local(p)
    cf=dm.stage('Cloudflare · validation de la zone', Cloudflare)
    nameservers=dm.stage('Cloudflare · contrôle NS',_check_dns_ns,cf,p)
    dns=dm.stage('Cloudflare · analyse du domaine',_dns_current,cf,p)
    _display_plan(p,dns)
    from autokom_env_manager import select
    choice=select('Backend AUTOKOM · nouveau domaine',
        ['Annuler · conserver le domaine actuel','Ajouter '+p.target+' en parallèle'],default=2)
    if choice.startswith('Annuler'):
        print('  › Opération annulée.');return 0
    if input('  Saisir MIGRER pour confirmer : ').strip()!='MIGRER':
        print('  › Aucune modification.');return 0
    convert_dns = p.dns_mode == 'a' and dns.site_mode == 'cname'
    if convert_dns:
        print('  ! Attention : le CNAME actuel sera remplacé par un A DNS-only APRÈS validation TLS locale.')
        if input('  Saisir REMPLACER pour confirmer la conversion DNS : ').strip() != 'REMPLACER':
            print('  › DNS conservé · opération annulée.');return 0
    from autokom_domain_progress import LiveDomainProgress
    with LiveDomainProgress() as live:
        previous=dm._LIVE
        dm._LIVE=live
        try:
            dm.stage('Backend · connexion locale',_check_local,p)
            dm.stage('Nginx · préparation HTTP',_nginx_commit,p,tls=False)
            dm.stage('Cloudflare · enregistrement DNS',_dns_ensure,cf,p,dns)
            # Until a legacy proxied CNAME is converted, public DNS still
            # resolves to Cloudflare IP addresses.
            mode_proxied = (dns.site_mode == 'cname' and dns.site_proxied) or (
                p.dns_mode == 'cname' and dns.site_mode != 'legacy_a' and dns.site_proxied)
            ready=dm.stage('DNS · résolution autoritaire',dm._wait_dns,
                           p.target,p.ingress,nameservers,origin=p.zone,proxied=mode_proxied)
            if not ready:
                print('  › DNS en propagation. Relancer la même commande pour reprendre.');return 2
            certificate=CERTS/p.target/'fullchain.pem'
            if not certificate.is_file():
                if ACME.is_symlink():
                    raise DomainError('ACME webroot ne doit pas être un lien symbolique.')
                if not ACME.is_dir():
                    ACME.mkdir(parents=True,exist_ok=True,mode=0o755)
                result=dm.stage('Certbot · émission HTTPS',dm.run,
                    ['certbot','certonly','--webroot','-w',str(ACME),
                     '--cert-name',p.target,
                     '--non-interactive','--agree-tos','--keep-until-expiring',
                     '-m',p.acme_email,'-d',p.target],timeout=240)
                if result.returncode or not certificate.is_file():
                    print('  › Certificat en attente. HTTP préparé ; ancien backend inchangé.');return 2
            dm.stage('Nginx · activation HTTPS',_nginx_commit,p,tls=True)
            dm.stage('Django · mise à jour des hôtes',_apply_backend_env,p)
            dm.stage('HTTPS · santé du nouveau backend',_tls_probe,p)
            if convert_dns:
                dm.stage('Cloudflare · conversion DNS-only', _convert_cname_to_a, cf, p)
                if not dm.stage('DNS · publication du A', dm._wait_dns,
                                p.target, p.ingress, nameservers,
                                origin=p.zone, proxied=False):
                    print('  › DNS modifié ; propagation encore en cours. Le certificat local est valide.')
            dm.stage('AUTOKOM · état du backend',_write_state,p)
        finally:
            dm._LIVE=previous
    print('  ✓ Backend disponible en HTTPS local sur '+p.target)
    print('  › Ancien domaine toujours actif : '+p.legacy)
    print('  › Validation publique à réaliser depuis un autre réseau : curl -I https://'+p.target+'/api/health/')
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(prog='autokom app backend domain',
        description='Ajouter un nouveau domaine au backend Django, sans frontend et sans migrations MySQL')
    parser.add_argument('--target',help='Nouveau nom du backend (par défaut dérivé du domaine actuel)')
    parser.add_argument('--dns-mode',choices=('cname','a'),default=None,
        help='A DNS-only pour sous-domaines profonds (défaut), sinon CNAME Cloudflare')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--check',action='store_true',help='Contrôle en lecture seule (défaut)')
    mode.add_argument('--apply',action='store_true',help='Ajouter DNS, Nginx, TLS et ALLOWED_HOSTS')
    mode.add_argument('--status',action='store_true',help='Afficher les domaines du backend')
    args=parser.parse_args(argv)
    if args.status:
        state=_read_state()
        print('  ◈ AUTOKOM · BACKEND · DOMAINES')
        print('  › Configuré : '+str(state.get('domain','?')))
        print('  › Préféré : '+str(state.get('preferred_domain',state.get('domain','?'))))
        print('  › Alias : '+', '.join(state.get('domain_aliases',[])) if state.get('domain_aliases') else '  › Aucun alias')
        return 0
    p=create_plan(target=args.target,dns_mode=args.dns_mode or 'auto')
    if args.apply and args.dns_mode is None:
        from autokom_env_manager import select
        dns_choices = [
            'A DNS-only → IP AWS (certificat Certbot)',
            'CNAME → ikom.mg (proxy Cloudflare : certificat Edge nécessaire si nom profond)',
        ]
        selected = select('Mode DNS du backend', dns_choices,
                          default=1 if p.dns_mode == 'a' else 2)
        p=replace(p, dns_mode='a' if selected == dns_choices[0] else 'cname')
    if not args.apply:
        _check_local(p)
        cf=Cloudflare()
        _check_dns_ns(cf,p)
        dns=_dns_current(cf,p)
        _display_plan(p,dns)
        print('  ✓ Prévisualisation terminée · aucun DNS, service ou fichier modifié.')
        return 0
    return _apply(p)


if __name__=='__main__':
    try: raise SystemExit(main())
    except (DomainError,AppError) as exc:
        print('  ✗ '+str(exc),file=sys.stderr)
        raise SystemExit(1)
