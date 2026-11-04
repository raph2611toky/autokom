#!/usr/bin/env python3
"""Parallel, per-service .tech -> .mg migrations. Never remove old DNS/vhosts.

Only simple, locally-owned reverse proxies are migrated automatically. A named
NGINX upstream is allowed only if it resolves unambiguously to one loopback
server. Complex multi-location, static, wildcard and remote upstreams need review.
"""
from __future__ import annotations
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time

OLD_SUFFIX='ik0m.tech'
NEW_SUFFIX='ikom.mg'
NGINX_DIRS=(Path('/etc/nginx/sites-enabled'),Path('/etc/nginx/conf.d'))
SITES_AVAILABLE=Path('/etc/nginx/sites-available')
SITES_ENABLED=Path('/etc/nginx/sites-enabled')
STATE=Path('/var/lib/autokom/domain-migrations')
ACME=Path('/var/lib/autokom/acme')
HOST_RE=re.compile(r'^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$')
class MigrationError(RuntimeError):pass


def run(args,timeout=30):
    try:return subprocess.run(args,capture_output=True,text=True,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:raise MigrationError('Commande système indisponible : '+args[0]) from exc


def check_hostname(host):
    value=host.lower().strip().rstrip('.')
    if not HOST_RE.fullmatch(value) or len(value)>253 or any(len(x)>63 for x in value.split('.')):
        raise MigrationError('Nom DNS invalide.')
    return value


def destination(source, old_zone=OLD_SUFFIX, new_zone=NEW_SUFFIX):
    source=check_hostname(source)
    old_zone=check_hostname(old_zone)
    new_zone=check_hostname(new_zone)
    if source!=old_zone and not source.endswith('.'+old_zone):raise MigrationError('Source hors '+old_zone+'.')
    dest=source[:-len(old_zone)]+new_zone
    if dest.startswith(('ns1.','ns2.','infra.')) or dest in ('ns1.ikom.mg','ns2.ikom.mg'):
        raise MigrationError('Service DNS critique exclu de la migration web.')
    return check_hostname(dest)


def _resolve_proxy(text):
    """Return (port, kind, reason) for a *single* safe localhost proxy.

    Conservative on purpose: reject dynamic proxy_pass values, HTTP paths,
    multiple upstreams/backends, additional locations and location rewrites.
    Never execute or import directives from a client vhost.
    """
    if re.search(r'\b(?:root|alias)\s', text):
        return 0, '', 'directives root/alias (contenu statique ou ACME spécifique)'
    if re.search(r'\b(?:fastcgi_pass|uwsgi_pass|scgi_pass)\s', text):
        return 0, '', 'backend FastCGI/uWSGI'

    proxies=[v.strip() for v in re.findall(r'\bproxy_pass\s+([^;]+);',text)]
    if not proxies or len(set(proxies))!=1:
        return 0, '', 'proxy HTTP absent ou plusieurs cibles'

    locations=re.findall(r'\blocation\s+([^\s{]+)\s*\{([^{}]*)\}',text)
    if len(locations)!=1 or len(re.findall(r'\blocation\s',text))!=1 or locations[0][0]!='/':
        return 0, '', 'plusieurs locations ou configuration de location non standard'
    location_body=locations[0][1]
    if re.search(r'\b(?:return|rewrite|try_files|error_page|auth_request|proxy_cache|proxy_redirect|set)\s',location_body):
        return 0, '', 'directives de traitement spécifiques dans location /'

    proxy=proxies[0]
    direct=re.fullmatch(r'http://(?:127\.0\.0\.1|localhost):(\d+)',proxy)
    if direct:
        port=int(direct.group(1))
        return (port,'direct','') if 1024<=port<=65535 else (0,'','port local non autorisé')

    name=re.fullmatch(r'http://([A-Za-z_][A-Za-z0-9_.-]*)',proxy)
    if not name:
        return 0,'','proxy non local ou dynamique (Docker/upstream/localhost/HTTPS)'

    # A single, inline upstream, with exactly one server on 127.0.0.1.
    # Never guess a definition from included snippets or remote peers.
    groups=list(re.finditer(r'\bupstream\s+([A-Za-z_][A-Za-z0-9_.-]*)\s*\{([^{}]*)\}',text))
    if len(groups)!=1 or len(re.findall(r'\bupstream\s+',text))!=1:
        return 0,'','upstream non localisé ou multiple'
    group=groups[0]
    if group.group(1)!=name.group(1):
        return 0,'','nom upstream différent du proxy_pass'
    statements=[part.strip() for part in group.group(2).split(';') if part.strip()]
    servers=[]
    for statement in statements:
        server=re.fullmatch(r'server\s+127\.0\.0\.1:(\d+)',statement)
        keepalive=re.fullmatch(r'keepalive\s+([1-9][0-9]{0,3})',statement)
        if server:
            servers.append(int(server.group(1)))
        elif keepalive:
            pass
        else:
            return 0,'','directive upstream non prise en charge'
    if len(servers)!=1 or not 1024<=servers[0]<=65535:
        return 0,'','upstream non local ou plusieurs serveurs'
    return servers[0],'upstream', ''


def discover_nginx(old_zone=OLD_SUFFIX):
    """Inventory only. Never evaluate NGINX directives or follow user input."""
    found={}
    for folder in NGINX_DIRS:
        if not folder.is_dir():continue
        for f in sorted(folder.iterdir()):
            if not f.is_file() or f.name.startswith('.'):continue
            try:raw=f.read_text(encoding='utf-8')
            except (UnicodeError,OSError):continue
            text='\n'.join(line.split('#',1)[0] for line in raw.splitlines())
            domains=[]
            for match in re.finditer(r'(?m)^\s*server_name\s+([^;]+);',text):
                for candidate in match.group(1).split():
                    try:
                        candidate=check_hostname(candidate)
                        if candidate==old_zone or candidate.endswith('.'+old_zone):domains.append(candidate)
                    except MigrationError:pass
            if not domains:continue
            port,kind,reason=_resolve_proxy(text)
            for source in dict.fromkeys(domains):
                if source not in found:
                    found[source]={'sources':[str(f)], 'port':port, 'supported':bool(port),
                                   'proxy_kind':kind, 'review_reason':reason}
                    continue
                item=found[source]
                if str(f) not in item['sources']:item['sources'].append(str(f))
                if not port or item['port']!=port or item['proxy_kind']!=kind:
                    item['supported']=False
                    item['review_reason']='Définitions Nginx incohérentes ou non prises en charge'
    return found


def authoritative_ns_ok(domain='ikom.mg'):
    # Read-only authoritative delegation evidence, never infer from an A response.
    p=run(['dig','+time=3','+tries=1','+short','NS',domain],timeout=6)
    return p.returncode==0 and bool(p.stdout.strip()), p.stdout.strip().splitlines()


def cloudflare(zone=NEW_SUFFIX):
    requested_zone=check_hostname(zone)
    from autokom_dns_cloudflare import Cloudflare
    cf=Cloudflare()
    # The zone ID is opaque: verify it really corresponds to ikom.mg before writes.
    # Token needs Zone Read in addition to DNS Edit.
    try:
        data=cf.request('GET','/zones/'+cf.zone_id)
    except Exception as exc:
        raise MigrationError('Cloudflare Zone Read requis (et Zone DNS Edit sur ikom.mg).') from exc
    zone=data.get('result') or {}
    if zone.get('name')!=requested_zone or zone.get('status')!='active':
        raise MigrationError('La zone Cloudflare doit être ACTIVE et correspondre au Zone ID.')
    return cf


def cloudflare_preflight(cf, hostname, ip, alias=None):
    items=cf.records(record_name=hostname)
    matching=[v for v in items if v.get('name')==hostname]
    if matching:
        if len(matching)!=1:
            raise MigrationError('DNS déjà présent avec plusieurs enregistrements.')
        item=matching[0]
        matching_a=(item.get('type')=='A' and item.get('content')==ip and not bool(item.get('proxied')))
        matching_cname=(alias is not None and item.get('type')=='CNAME' and item.get('content','').rstrip('.')==alias and not bool(item.get('proxied')))
        if not (matching_a or matching_cname):
            raise MigrationError('Enregistrement DNS déjà présent ou incompatible : aucune modification.')
        return item
    return None


def local_ip():
    from autokom_env_manager import read_env,file_path
    values=read_env(file_path('runtime'))
    value=values.get('AUTOKOM_INGRESS_IPV4','')
    try:
        address=ipaddress.IPv4Address(value)
        if not address.is_global: raise ValueError('not global')
    except ValueError as exc:raise MigrationError('Configurer AUTOKOM_INGRESS_IPV4 avec « autokom runtime env setup ».') from exc
    return value


def _managed_conf(host,port,ssl=False):
    host=check_hostname(host)
    # Strictly controlled template; no copied client Nginx snippets.
    main=(f'    location / {{\n        proxy_pass http://127.0.0.1:{port};\n'
          '        proxy_set_header Host $host;\n        proxy_set_header X-Forwarded-Proto $scheme;\n'
          '        proxy_set_header X-Real-IP $remote_addr;\n'
          '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
          '        proxy_connect_timeout 5s;\n        proxy_read_timeout 60s;\n    }\n')
    if not ssl:
        return (f'# AUTOKOM MIGRATION · {host}\nserver {{\n    listen 80;\n    server_name {host};\n'
                '    location /.well-known/acme-challenge/ { root /var/lib/autokom/acme; }\n'
                +main+'}\n')
    cert='/etc/letsencrypt/live/'+host
    return (f'# AUTOKOM MIGRATION · {host}\nserver {{\n    listen 80;\n    server_name {host};\n'
            '    location /.well-known/acme-challenge/ { root /var/lib/autokom/acme; }\n'
            '    location / { return 308 https://$host$request_uri; }\n}\n'
            f'server {{\n    listen 443 ssl;\n    server_name {host};\n'
            f'    ssl_certificate {cert}/fullchain.pem;\n    ssl_certificate_key {cert}/privkey.pem;\n'
            '    ssl_protocols TLSv1.2 TLSv1.3;\n'
            '    client_max_body_size 16m;\n'+main+'}\n')


def atomic_text(path,content,mode=0o644):
    if path.is_symlink():raise MigrationError('Lien symbolique inattendu : modification refusée.')
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=str(path.parent),prefix='.autokom-migrate-')
    try:
        with os.fdopen(fd,'w') as f:f.write(content);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,mode);os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def _commit_nginx(host,port,ssl=False):
    available=SITES_AVAILABLE/('autokom-migrate-'+host+'.conf')
    enabled=SITES_ENABLED/available.name
    if available.exists():
        existing=available.read_text()
        allowed={_managed_conf(host,port,False),_managed_conf(host,port,True)}
        if existing not in allowed:
            raise MigrationError('Vhost existant modifié ou non administré : refus d’écraser.')
    if enabled.is_symlink() and enabled.resolve()!=available.resolve():
        raise MigrationError('Lien Nginx concurrent vers une autre configuration : refus.')
    if enabled.exists() and not enabled.is_symlink():
        raise MigrationError('Fichier Nginx enabled non administré : refus de le remplacer.')
    previous=available.read_text() if available.exists() else None
    expected=_managed_conf(host,port,ssl)
    # Re-running a successful migration must never disable its HTTPS listener.
    if not ssl and previous==_managed_conf(host,port,True) and enabled.is_symlink():
        return
    if previous==expected and enabled.is_symlink():
        return
    try:
        atomic_text(available,_managed_conf(host,port,ssl))
        if not enabled.exists():enabled.symlink_to(available)
        check=run(['nginx','-t'],timeout=20)
        if check.returncode:raise MigrationError('Nginx a refusé la nouvelle configuration (rollback).')
        refresh=run(['systemctl','reload','nginx'],timeout=20)
        if refresh.returncode:raise MigrationError('Nginx n’a pas pu être rechargé (rollback).')
    except Exception:
        if previous is None:
            if enabled.is_symlink():enabled.unlink()
            available.unlink(missing_ok=True)
        else:atomic_text(available,previous)
        run(['nginx','-t'],timeout=20)
        run(['systemctl','reload','nginx'],timeout=20)
        raise


def _needs_backend_settings(host):
    return host.startswith('backend.')


def _append_backend_hosts(new_host, frontend=False):
    """Add new host without replacing legacy host. Preserve env backup for rollback."""
    from autokom_env_manager import file_path,read_env,write_env,backup
    path=file_path('backend')
    values=read_env(path)
    if not values:raise MigrationError('Backend centralisé absent : autokom app backend env setup requis.')
    result=dict(values)
    pairs=([('CORS_ALLOWED_ORIGINS','https://'+new_host),('CSRF_TRUSTED_ORIGINS','https://'+new_host)]
           if frontend else [('ALLOWED_HOSTS',new_host),('CSRF_TRUSTED_ORIGINS','https://'+new_host)])
    for key,add in pairs:
        existing=[s.strip() for s in values.get(key,'').split(',') if s.strip()]
        if add not in existing: existing.append(add)
        result[key]=','.join(existing)
    if frontend:
        for key in ('CORS_ALLOWED_ORIGINS','CSRF_TRUSTED_ORIGINS'):
            existing=[s.strip() for s in values.get(key,'').split(',') if s.strip()]
            if 'https://'+new_host not in existing:existing.append('https://'+new_host)
            result[key]=','.join(existing)
    if result!=values:
        backup(path);write_env(path,result)
        return values
    return None


def _assert_destination_unclaimed(host):
    """Fail before DNS writes if another enabled NGINX vhost owns the name."""
    host=check_hostname(host)
    managed=(SITES_AVAILABLE/('autokom-migrate-'+host+'.conf')).resolve()
    for directory in NGINX_DIRS:
        if not directory.is_dir():
            continue
        for conf in directory.iterdir():
            if not conf.is_file() or conf.name.startswith('.'):
                continue
            if conf.resolve()==managed:
                # The managed vhost is checked for tampering by _commit_nginx.
                continue
            try:
                raw=conf.read_text(encoding='utf-8')
            except (OSError,UnicodeError):
                raise MigrationError('Impossible de vérifier les collisions Nginx.')
            text='\n'.join(line.split('#',1)[0] for line in raw.splitlines())
            for matched in re.findall(r'\bserver_name\s+([^;]+);',text):
                for name in matched.split():
                    name=name.lower().rstrip('.')
                    if name==host or (name.startswith('*.') and host.endswith(name[1:])):
                        raise MigrationError('Nom DNS déjà défini dans un autre vhost Nginx : '+host)


def _dns_authoritative_ready(dest, ip, nameservers, origin=None):
    """Query actual Cloudflare authoritative servers, not a stale local resolver.

    Responses with a CNAME followed by an A are supported; no proxy mode
    should be enabled for the initial certificate issuance.
    """
    servers=[n.rstrip('.') for n in nameservers if n.strip()][:2]
    if not servers:
        return False
    for ns in servers:
        result=run(['dig','+time=2','+tries=1','+short','@'+ns,'A',dest],timeout=5)
        if result.returncode:
            return False
        answers=[v.strip().rstrip('.') for v in result.stdout.splitlines() if v.strip()]
        if ip in answers:
            continue
        # An authoritative server may answer with only the CNAME (no A in the
        # additional section). Resolve the controlled origin independently.
        if origin and origin in answers:
            extra=run(['dig','+time=2','+tries=1','+short','@'+ns,'A',origin],timeout=5)
            if extra.returncode==0 and ip in extra.stdout.splitlines():
                continue
        return False
    return True


def _wait_dns(dest, ip, nameservers, attempts=4, origin=None):
    for i in range(attempts):
        if _dns_authoritative_ready(dest, ip, nameservers, origin=origin):
            print('  \033[32m✓\033[0m DNS autoritaire vérifié :',dest)
            return True
        if i+1 < attempts:
            print('  \033[32m↻\033[0m DNS · vérification des serveurs autoritaires…',f'{i+1}/{attempts}',end='\r',flush=True)
            time.sleep(2)
    print('  › DNS autoritaire en attente. Le reverse proxy HTTP est déjà préparé.')
    return False


def apply_one(source,host_data,ip,cf,acme_email, *, target=None, zone=NEW_SUFFIX, nameservers=(), convert=False):
    """Resumable independent phases: nginx HTTP -> DNS -> TLS -> HTTPS.

    All new configs live in autokom-migrate-<host>.conf. Original website,
    DNS and certificate are never touched. A previous legacy A stays valid
    until an explicit opt-in conversion AFTER HTTPS health verification.
    """
    from autokom_domain_dns import plan, ensure, convert_legacy_a, DNSMigrationError
    dest=check_hostname(target or destination(source,new_zone=zone))
    if not dest.endswith('.'+zone):
        raise MigrationError('Destination hors de la zone Cloudflare contrôlée.')
    if not host_data['supported']:
        raise MigrationError('Configuration Nginx complexe : revue manuelle requise.')
    port=host_data['port']
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=2): pass
    except OSError as exc:
        raise MigrationError('Service source indisponible sur 127.0.0.1:'+str(port)) from exc
    _assert_destination_unclaimed(dest)
    try:
        dns=plan(cf,zone,dest,ip)
    except DNSMigrationError as exc:
        raise MigrationError(str(exc)) from exc
    print(f'  › Service : {source} → {dest} · port local {port}')
    print(f'  › DNS : {dest} CNAME {dns.origin}' if dns.site_mode=='new' else
          f'  › DNS existant : {dns.site_mode} (aucune suppression automatique)')
    # IMPORTANT: ensure the reverse proxy exists BEFORE publishing a new name.
    _commit_nginx(dest,port,ssl=False)
    print('  \033[32m✓\033[0m Reverse proxy Nginx HTTP prêt :',dest)
    try:
        ensure(cf,dns)
    except DNSMigrationError as exc:
        raise MigrationError(str(exc)) from exc
    except Exception as exc:
        raise MigrationError('Cloudflare a refusé la configuration DNS : vérifier le token et les droits Zone DNS Edit.') from exc
    if dns.site_mode=='legacy_a':
        print('  \033[32m✓\033[0m DNS A existant conservé :',dest,'· point central prêt :',dns.origin)
    else:
        print('  \033[32m✓\033[0m DNS CNAME Cloudflare prêt :',dest,'→',dns.origin)
    if not _wait_dns(dest,ip,nameservers,origin=dns.origin):
        return 'dns_pending'
    cert=Path('/etc/letsencrypt/live')/dest/'fullchain.pem'
    if not cert.is_file():
        print('  \033[32m↻\033[0m Certbot · émission HTTPS…',flush=True)
        p=run(['certbot','certonly','--webroot','-w',str(ACME),'-d',dest,
               '--non-interactive','--agree-tos','--email',acme_email,'--keep-until-expiring'],timeout=180)
        if p.returncode or not cert.is_file():
            print('  › Certificat en attente : HTTP prêt, ancien domaine inchangé. Consulter /var/log/letsencrypt/letsencrypt.log.')
            return 'tls_pending'
    _commit_nginx(dest,port,ssl=True)
    print('  \033[32m✓\033[0m Nginx HTTPS activé :',dest)
    if _needs_backend_settings(dest) or source=='auto.ik0m.tech':
        from autokom_env_manager import write_env,file_path
        before=_append_backend_hosts(dest,frontend=(source=='auto.ik0m.tech'))
        if before is not None:
            restart=run(['systemctl','restart','autokom-backend.service'],timeout=40)
            if restart.returncode:
                write_env(file_path('backend'),before)
                run(['systemctl','restart','autokom-backend.service'],timeout=40)
                raise MigrationError('Redémarrage Django refusé, configuration backend restaurée.')
    # Check origin TLS with proper SNI / Host and without relying on local DNS cache.
    probe=run(['curl','--silent','--output','/dev/null','--write-out','%{http_code}',
               '--resolve',dest+':443:'+ip,
               '--connect-timeout','5','--max-time','12','https://'+dest+'/'],timeout=15)
    if probe.returncode or probe.stdout not in ('200','301','302','303','307','308','401','403','404'):
        print('  › HTTPS non encore validé : vérifier application, CORS ou callbacks. Ancien domaine conservé.')
        return 'verify_pending'
    dns_mode='legacy_a' if dns.site_mode=='legacy_a' else 'cname'
    if convert and dns.site_mode=='legacy_a':
        try:
            convert_legacy_a(cf,dns)
            dns_mode='cname'
            print('  \033[32m✓\033[0m Ancien DNS A converti en CNAME :',dest)
        except DNSMigrationError as exc:
            print('  ! Migration HTTPS réussie, mais conversion DNS à vérifier :',str(exc))
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(STATE,0o700)
    atomic_text(STATE/(dest+'.json'),json.dumps({'source':source,'destination':dest,
                     'port':port,'verified':True,'dns_alias':dns.origin,'dns_mode':dns_mode},indent=2)+'\n',0o600)
    print('  \033[32m✓\033[0m Nouveau domaine accessible : https://'+dest)
    return 'verified'


def audit_reasons(host_data):
    """Describe the exact reason for the conservative decision, no secrets."""
    if host_data.get('supported'):
        return []
    reason=host_data.get('review_reason')
    if reason:
        return [reason]
    return ['structure Nginx non reconnue (relecture obligatoire)']


def print_audit(found):
    """Short source-specific diagnostics, no secrets or Nginx config copied."""
    for source,item in found.items():
        print('  ◈ '+source+' → '+destination(source))
        print('    › Vhost : '+', '.join(item.get('sources') or ['introuvable']))
        if item.get('supported'):
            print('    ✓ Proxy local unique : 127.0.0.1:'+str(item['port']) + (' (upstream nommé)' if item.get('proxy_kind')=='upstream' else ''))
        else:
            for reason in audit_reasons(item):
                print('    ! '+reason)
            print('    › Non éligible à --apply. Ancien site conservé.')

def _migration_zones(args):
    from autokom_env_manager import read_env, file_path
    cloudflare_env=read_env(file_path('cloudflare'))
    # Destination is the selected Cloudflare zone, never an unchecked hostname.
    target=check_hostname(args.to_zone or cloudflare_env.get('CLOUDFLARE_ZONE_NAME') or NEW_SUFFIX)
    source=check_hostname(args.from_zone or OLD_SUFFIX)
    if source==target:
        raise MigrationError('Ancienne et nouvelle zones identiques : refus.')
    return source,target


def _choose_service(found, old_zone, new_zone):
    """Native AUTOKOM arrow-key selector; never a numeric 'Choix :' prompt."""
    from autokom_env_manager import select
    eligible=[(source,item) for source,item in found.items() if item.get('supported')]
    if not eligible:
        return None
    choices=[source+' → '+destination(source,old_zone,new_zone)+
             ' · 127.0.0.1:'+str(item['port']) for source,item in eligible]
    choice=select('Service à migrer · ↑ ↓ Entrée',choices+['Retour sans modifier'],default=1)
    if choice=='Retour sans modifier':
        return None
    return eligible[choices.index(choice)]


def main(argv=None):
    p=argparse.ArgumentParser(prog='autokom domains',description='Migration progressive, interactive et réversible de domaines')
    sub=p.add_subparsers(dest='action')
    m=sub.add_parser('migrate',help='Découvrir, auditer et migrer un service HTTPS en parallèle')
    m.add_argument('--source',help='Service existant (facultatif : sélecteur interactif)')
    m.add_argument('--target',help='Nom DNS final dans la zone Cloudflare (avec --source)')
    m.add_argument('--from-zone',dest='from_zone',help='Zone source à découvrir dans Nginx (défaut ik0m.tech)')
    m.add_argument('--to-zone',dest='to_zone',help='Zone Cloudflare cible (détectée dans secrets/cloudflare-dns.env)')
    group=m.add_mutually_exclusive_group()
    group.add_argument('--check',action='store_true',help='Inventaire uniquement')
    group.add_argument('--audit',action='store_true',help='Audit Nginx sans modification')
    group.add_argument('--apply',action='store_true',help='Migration interactive : Nginx + CNAME + HTTPS')
    args=p.parse_args(argv)
    if not args.action:
        args.action='migrate';args.check=True;args.source=None;args.target=None;args.from_zone=None;args.to_zone=None;args.apply=False;args.audit=False
    if args.action!='migrate':raise MigrationError('Action inconnue.')
    old_zone,new_zone=_migration_zones(args)
    if args.target and not args.source:
        raise MigrationError('--target nécessite --source.')
    found=discover_nginx() if old_zone==OLD_SUFFIX else discover_nginx(old_zone)
    if args.source:
        source=check_hostname(args.source)
        if source not in found:raise MigrationError('Domaine absent des vhosts Nginx locaux.')
        found={source:found[source]}
    print(f'  ◈ AUTOKOM · MIGRATION DE DOMAINES · {old_zone} → {new_zone}')
    if not found:
        print('  › Aucun vhost source détecté sur cet hôte.')
        return 0
    for source,item in found.items():
        state='prêt (proxy local)' if item['supported'] else 'revue manuelle nécessaire'
        print(f'  › {source} → {destination(source,old_zone,new_zone)} · {state}')
    ok,ns=authoritative_ns_ok(new_zone)
    print('  › Délégation DNS :','NS trouvés' if ok else 'non confirmée')
    if ns:print('  › Serveurs autoritaires :',', '.join(ns[:4]))
    print('  › Ancien domaine et certificats conservés. BIND non modifié.')
    if args.audit or (args.check and not any(x['supported'] for x in found.values())):
        print('  ◈ AUDIT NGINX · lecture seule')
        if new_zone==NEW_SUFFIX and old_zone==OLD_SUFFIX:
            print_audit(found)
        else:
            for source,item in found.items():
                print('  ◈ '+source+' → '+destination(source,old_zone,new_zone))
                if item['supported']:
                    print('    ✓ Proxy local : 127.0.0.1:'+str(item['port']))
                else:
                    for reason in audit_reasons(item):print('    ! '+reason)
    if not args.apply:return 0
    if not any(x['supported'] for x in found.values()):
        print('  ✗ Aucun vhost automatiquement éligible : --apply n’a rien modifié.')
        print('  › Utiliser « autokom domains migrate --audit » pour identifier les obstacles.')
        return 2
    if os.geteuid()!=0:raise MigrationError('Exécuter sudo autokom domains migrate --apply.')
    if not os.isatty(0):raise MigrationError('Confirmation interactive obligatoire.')
    from autokom_env_manager import select,read_env,file_path
    if args.source:
        source,item=next(iter(found.items()))
        if not item['supported']:
            raise MigrationError('Service non éligible : audit Nginx requis.')
    else:
        chosen=_choose_service(found,old_zone,new_zone)
        if chosen is None:
            print('  › Aucun changement.');return 0
        source,item=chosen
    default=destination(source,old_zone,new_zone)
    target=args.target or input(f'  Nouveau domaine [{default}] : ').strip() or default
    target=check_hostname(target)
    if not target.endswith('.'+new_zone) or target.startswith(('ns1.','ns2.','infra.')):
        raise MigrationError('Nouveau domaine non autorisé : zone Cloudflare ou DNS critique.')
    if target==('origin.'+new_zone):
        raise MigrationError('Point d’entrée DNS réservé : '+target)
    _assert_destination_unclaimed(target)
    if not ok or not ns:
        raise MigrationError('Délégation publique non confirmée : migration refusée.')
    cf=cloudflare() if new_zone==NEW_SUFFIX else cloudflare(new_zone)
    info=cf.request('GET','/zones/'+cf.zone_id).get('result') or {}
    registered={str(n).lower().rstrip('.') for n in info.get('name_servers', [])}
    delegated={str(n).lower().rstrip('.') for n in ns}
    if not registered or registered!=delegated:
        raise MigrationError('Délégation DNS différente des nameservers Cloudflare : migration refusée.')
    ip=local_ip()
    values=read_env(file_path('runtime'))
    email=values.get('AUTOKOM_ACME_EMAIL','')
    if not email or '@' not in email:raise MigrationError('E-mail Certbot manquant : autokom runtime env edit AUTOKOM_ACME_EMAIL.')
    from autokom_domain_dns import plan, DNSMigrationError
    try:dns_plan=plan(cf,new_zone,target,ip)
    except DNSMigrationError as exc:raise MigrationError(str(exc)) from exc
    print('  ◈ PLAN DE MIGRATION · '+source)
    print('  › Reverse proxy local : 127.0.0.1:'+str(item['port']))
    print('  › Nouveau site : https://'+target)
    print('  › DNS central : origin.'+new_zone+' → '+ip)
    print('  › DNS destination :', 'CNAME → '+dns_plan.origin if dns_plan.site_mode=='new' else dns_plan.site_mode)
    print('  › Nginx HTTP → validation DNS → Certbot → HTTPS → healthcheck')
    print('  › Ancien domaine conservé, Nginx testé avant chaque rechargement.')
    choice=select('Migrer '+source, ['Annuler · aucun changement', 'Créer le domaine en parallèle'],default=2)
    if choice.startswith('Annuler'):
        print('  › Annulé.');return 0
    convert=False
    if dns_plan.site_mode=='legacy_a':
        choice=select('Ancien enregistrement A détecté pour '+target,
            ['Conserver le A existant', 'Convertir en CNAME après validation HTTPS'], default=1)
        convert=choice.startswith('Convertir')
    if input('  Saisir MIGRER pour confirmer : ').strip()!='MIGRER':
        print('  › Migration annulée.');return 0
    if convert:
        if input('  Saisir CONVERTIR pour autoriser le remplacement du A : ').strip()!='CONVERTIR':
            convert=False
            print('  › Le A existant sera conservé.')
    ACME.mkdir(parents=True,exist_ok=True)
    try:
        result=apply_one(source,item,ip,cf,email,target=target,zone=new_zone,nameservers=ns,convert=convert)
    except MigrationError as exc:
        print('  ✗',exc)
        return 1
    if result!='verified':
        print('  › Pour reprendre : autokom domains migrate --source '+source+' --target '+target+' --apply')
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except MigrationError as exc:raise SystemExit('  ✗ '+str(exc))
