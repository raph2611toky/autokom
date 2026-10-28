#!/usr/bin/env python3
"""Opt-in host metrics and SMTP alerting for AUTOKOM.

No remote listeners; never overwrite unknown third-party configuration. All
passwords are read from a TTY and stored only in a root-controlled file.
"""
from __future__ import annotations

from pathlib import Path
import os
import re
import socket
import subprocess
import time
import urllib.request

NODE_UNIT='prometheus-node-exporter'
ALERT_UNIT='prometheus-alertmanager'
NODE_OVERRIDE=Path('/etc/systemd/system/prometheus-node-exporter.service.d/90-autokom.conf')
ALERT_OVERRIDE=Path('/etc/systemd/system/prometheus-alertmanager.service.d/90-autokom.conf')
ALERT_CONFIG=Path('/etc/prometheus/alertmanager.yml')
ALERT_PASSWORD=Path('/etc/prometheus/.autokom-smtp-password')
RULES=Path('/etc/prometheus/rules/autokom-alerts.yml')
PROM_CONFIG=Path('/etc/prometheus/prometheus.yml')
NODE_PORT=9100
ALERT_PORT=9093


def validate_smtp(host, port, login, sender, recipient):
    import re
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', host) or '.' not in host or '..' in host:
        raise ValueError('Serveur SMTP : nom DNS invalide')
    if not 1 <= port <= 65535 or port not in (465, 587, 2525):
        raise ValueError('Port SMTP : utiliser TLS 465, STARTTLS 587 ou 2525')
    for name, value in [('email_login', login), ('from_email', sender), ('email_to', recipient)]:
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError(name + ' : adresse e-mail invalide')
    return host, port, login, sender, recipient


def collect_options(o, args):
    """Prompt Node Exporter, then Alertmanager; no interaction in --check."""
    args.setup_node = False
    args.setup_alertmanager = False
    args.smtp = None
    if getattr(args,'mode','check') != 'apply' or getattr(args,'non_interactive',False):
        return
    o.section('Node Exporter · métriques hôte', 'CPU · mémoire · disques · système')
    if o.package_installed('prometheus-node-exporter'):
        args.setup_node = True
        o.out('✓', 'Node Exporter déjà installé : contrôle et correction ciblés')
    else:
        args.setup_node = o.yesno('Installer Node Exporter (écoute privée 127.0.0.1:9100) ?', True)
    o.section('Alertmanager · gestion des alertes', 'Routage · notifications · SMTP optionnel')
    if o.package_installed('prometheus-alertmanager'):
        o.out('›', 'Alertmanager présent : préserver toute configuration tierce')
    args.setup_alertmanager = o.yesno('Configurer Alertmanager et les règles AUTOKOM ?', True)
    if args.setup_alertmanager and not ALERT_CONFIG.is_file():
        if o.yesno('Configurer les notifications par e-mail SMTP ?', True):
            for _ in range(3):
                host=o.prompt_text('email_host (SMTP)')
                port_raw=o.prompt_text('Port SMTP TLS/STARTTLS', default='587')
                login=o.prompt_text('email_login')
                sender=o.prompt_text('from_email (expéditeur)')
                recipient=o.prompt_text('email_to (destinataire)')
                try:
                    smtp=validate_smtp(host,int(port_raw),login,sender,recipient)
                    break
                except (ValueError, TypeError) as exc:
                    o.out('!', str(exc))
            else:
                raise o.SetupError('Paramètres SMTP invalides après trois saisies')
            password=o.secret('SMTP — mot de passe (saisie masquée)', args.non_interactive)
            args.smtp = (*smtp,password)
        else:
            o.out('›', 'Alertmanager sans e-mail : alertes consultables localement, notifications non activées')
    elif args.setup_alertmanager and ALERT_CONFIG.is_file():
        o.out('✓', 'Configuration Alertmanager existante : aucune demande SMTP')


def local_http_ok(port, path='/metrics'):
    url=f'http://127.0.0.1:{port}{path}'
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url,timeout=3) as response:
            return response.status == 200
    except (OSError,TimeoutError):
        return False


def wait_private_http(o, unit, port, path, *, attempts=25, interval=2):
    with o.Activity(f'{unit} · disponibilité locale',delay=0.4):
        for attempt in range(attempts):
            listeners=o.listener(port)
            if listeners and not o.private_listener(listeners):
                raise o.SetupError(f'{unit} : port {port} exposé au réseau : {listeners}')
            if listeners and local_http_ok(port,path):
                return
            if attempt<attempts-1:
                time.sleep(interval)
    raise o.SetupError(f'{unit} non prêt sur 127.0.0.1:{port}. Examiner systemctl status {unit} et journalctl -u {unit}')


def node_override():
    return ('# AUTOKOM MANAGED — local metrics only\n[Service]\nExecStart=\n'
            'ExecStart=/usr/bin/prometheus-node-exporter --web.listen-address=127.0.0.1:9100\n')


def alert_override():
    return ('# AUTOKOM MANAGED — private Alertmanager API\n[Service]\nExecStart=\n'
            'ExecStart=/usr/bin/prometheus-alertmanager --config.file=/etc/prometheus/alertmanager.yml '
            '--storage.path=/var/lib/prometheus/alertmanager --web.listen-address=127.0.0.1:9093 '
            '--cluster.listen-address=\n')


def _new_override(o, path, content):
    if path.exists() and (path.is_symlink() or not path.read_text(encoding='utf-8').startswith('# AUTOKOM MANAGED')):
        raise o.SetupError(f'Override inconnu : {path} ; refus d’écraser')
    return o.owned_write(path,content)


def merge_prom_extras(existing, *, node=False, alerts=False):
    """Patch YAML conservatively: preserve all unrelated jobs and rules."""
    import yaml
    result=yaml.safe_load(existing)
    if not isinstance(result,dict) or not isinstance(result.get('scrape_configs'),list):
        raise ValueError('Prometheus YAML non standard : fusion automatique refusée')
    if node:
        jobs=[j for j in result['scrape_configs'] if isinstance(j,dict) and j.get('job_name')=='node']
        if len(jobs)>1:
            raise ValueError('Plusieurs jobs node existants : conflit')
        if not jobs:
            result['scrape_configs'].append({'job_name':'node','static_configs':[{'targets':['127.0.0.1:9100']}]})
        else:
            groups=jobs[0].get('static_configs')
            targets=[t for g in (groups or []) for t in g.get('targets',[])] if isinstance(groups,list) else []
            if targets not in (['localhost:9100'],['127.0.0.1:9100']):
                raise ValueError('Job node personnalisé : correction manuelle nécessaire')
    if alerts:
        ams=result.setdefault('alerting',{}).setdefault('alertmanagers',[])
        if not isinstance(ams,list):
            raise ValueError('Alertmanager existant non standard')
        if not any('127.0.0.1:9093' in g.get('targets',[]) for a in ams if isinstance(a,dict)
                   for g in a.get('static_configs',[]) if isinstance(g,dict)):
            ams.append({'static_configs':[{'targets':['127.0.0.1:9093']}]})
        files=result.setdefault('rule_files',[])
        if not isinstance(files,list):
            raise ValueError('rule_files existants non standard')
        if str(RULES) not in files:
            files.append(str(RULES))
    if yaml.safe_load(existing)==result:
        return existing
    return yaml.safe_dump(result,allow_unicode=True,sort_keys=False)


def update_prom_config(o, *, node=False, alerts=False):
    old=PROM_CONFIG.read_text(encoding='utf-8')
    try:
        new=merge_prom_extras(old,node=node,alerts=alerts)
    except (ValueError,TypeError) as exc:
        raise o.SetupError(str(exc)) from exc
    if old != new:
        import tempfile
        # Validate the candidate before replacing active configuration.
        with tempfile.NamedTemporaryFile('w',suffix='.yml',delete=False,dir=str(PROM_CONFIG.parent)) as f:
            f.write(new)
            temp=Path(f.name)
        try:
            o.run(['promtool','check','config',str(temp)],timeout=30)
            backup=PROM_CONFIG.with_name(PROM_CONFIG.name+'.autokom-backup')
            if not backup.exists():
                o.owned_write(backup,old,mode=0o600)
            o.owned_write(PROM_CONFIG,new)
        finally:
            temp.unlink(missing_ok=True)
        try:
            o.run(['systemctl','reload-or-restart','prometheus'],timeout=120)
            o.wait_service_ready('prometheus',o.cfg().prometheus_port,o.cfg().prometheus_base+'/-/ready')
        except Exception:
            o.owned_write(PROM_CONFIG,old)
            try:
                o.run(['systemctl','reload-or-restart','prometheus'],timeout=120)
            except Exception:
                o.out('!', 'Restauré : vérifier le service Prometheus manuellement')
            raise
    return old != new


def ensure_node(o,args):
    if not args.setup_node:
        o.out('›','Node Exporter ignoré (choix utilisateur)')
        return
    exists=o.package_installed('prometheus-node-exporter')
    reuse_existing=False
    if exists and not NODE_OVERRIDE.exists():
        if o.active(NODE_UNIT):
            listeners=o.listener(NODE_PORT)
            if not listeners or not o.private_listener(listeners):
                raise o.SetupError('Node Exporter existant exposé ou non vérifiable : intervention manuelle requise')
            # Vendor/custom flags must not be replaced on an established node.
            reuse_existing=True
            o.out('✓','Node Exporter existant sur loopback : paramètres conservés')
        else:
            if not o.yesno('Node Exporter déjà installé mais arrêté : appliquer le bind local AUTOKOM ?',False,
                           noninteractive=args.non_interactive):
                raise o.SetupError('Node Exporter inactif : modification non autorisée')
    # Override staged before apt to prevent the default all-interface listener.
    if not reuse_existing:
        _new_override(o,NODE_OVERRIDE,node_override())
        o.run(['systemctl','daemon-reload'])
    if not exists:
        o.run(['apt-get','update','-qq'],timeout=240)
        o.run(['apt-get','install','-y','--no-install-recommends','prometheus-node-exporter'],timeout=420)
        o.run(['systemctl','daemon-reload'])
    o.run(['systemctl','enable','--now',NODE_UNIT],timeout=120)
    if not reuse_existing:
        o.run(['systemctl','restart',NODE_UNIT],timeout=120)
    wait_private_http(o,NODE_UNIT,NODE_PORT,'/metrics')
    update_prom_config(o,node=True)
    o.out('✓','Node Exporter : privé sur 127.0.0.1:9100, job node configuré')


def smtp_config(data):
    import yaml
    if data:
        host,port,login,sender,recipient,password=data
        validate_smtp(host,port,login,sender,recipient)
        return '# AUTOKOM MANAGED · SMTP receiver\n' + yaml.safe_dump({
            'global': {'smtp_smarthost':f'{host}:{port}','smtp_from':sender,
                       'smtp_require_tls':True},
            'route':{'receiver':'smtp-autokom','group_by':['alertname','instance'],
                     'group_wait':'30s','group_interval':'5m','repeat_interval':'4h'},
            'receivers':[{'name':'smtp-autokom','email_configs':[{'to':recipient,
                         'auth_username':login,'auth_password_file':str(ALERT_PASSWORD),
                         'send_resolved':True}]}]
        },sort_keys=False,allow_unicode=True)
    return ("# AUTOKOM MANAGED · no SMTP receiver; enable explicitly later\n"
            "route:\n  receiver: 'local-only'\nreceivers:\n- name: 'local-only'\n")


def alert_rules():
    return ("# AUTOKOM MANAGED — basic availability alerts\n"
            "groups:\n  - name: autokom-availability\n    rules:\n"
            "      - alert: AUTOKOMJenkinsDown\n        expr: up{job=\"jenkins\"} == 0\n"
            "        for: 5m\n        labels:\n          severity: warning\n"
            "        annotations:\n          summary: 'Jenkins inaccessible depuis Prometheus'\n"
            "      - alert: AUTOKOMNodeDown\n        expr: up{job=\"node\"} == 0\n"
            "        for: 5m\n        labels:\n          severity: warning\n"
            "        annotations:\n          summary: 'Node Exporter inaccessible'\n")


def ensure_alertmanager(o,args):
    if not args.setup_alertmanager:
        o.out('›','Alertmanager ignoré (choix utilisateur)')
        return
    exists=o.package_installed('prometheus-alertmanager')
    # Preexisting third-party config is not overwritten.
    if exists and ALERT_CONFIG.exists() and 'AUTOKOM MANAGED' not in ALERT_CONFIG.read_text(encoding='utf-8'):
        o.out('!', 'Alertmanager existant non géré : conservé, configuration SMTP non remplacée')
        return
    if not exists or ALERT_CONFIG.exists():
        _new_override(o,ALERT_OVERRIDE,alert_override())
        o.run(['systemctl','daemon-reload'])
    if not exists:
        o.run(['apt-get','update','-qq'],timeout=240)
        o.run(['apt-get','install','-y','--no-install-recommends','prometheus-alertmanager'],timeout=420)
        o.run(['systemctl','daemon-reload'])
    import pwd
    user=pwd.getpwnam('prometheus')
    store=Path('/var/lib/prometheus/alertmanager')
    if not store.exists():
        store.mkdir(mode=0o750,parents=True)
        os.chown(store,user.pw_uid,user.pw_gid)
    if exists and ALERT_CONFIG.exists() and 'AUTOKOM MANAGED' in ALERT_CONFIG.read_text(encoding='utf-8') and not args.smtp:
        # Never erase an already-configured SMTP receiver on repeated installs.
        content=ALERT_CONFIG.read_text(encoding='utf-8')
    else:
        content=smtp_config(args.smtp)
    if args.smtp:
        o.owned_write(ALERT_PASSWORD,args.smtp[-1]+'\n',mode=0o640,group='prometheus')
    # Config file never contains a password, but protects routing metadata.
    if not ALERT_CONFIG.exists() or ALERT_CONFIG.read_text(encoding='utf-8') != content:
        o.owned_write(ALERT_CONFIG,content,mode=0o640,group='prometheus')
    o.run(['amtool','check-config',str(ALERT_CONFIG)],timeout=30)
    if RULES.exists() and not RULES.read_text(encoding='utf-8').startswith('# AUTOKOM MANAGED'):
        raise o.SetupError('Règles Prometheus existantes non gérées : refus de remplacement')
    o.owned_write(RULES,alert_rules(),mode=0o644)
    o.run(['promtool','check','rules',str(RULES)],timeout=30)
    o.run(['systemctl','enable','--now',ALERT_UNIT],timeout=120)
    o.run(['systemctl','restart',ALERT_UNIT],timeout=120)
    wait_private_http(o,ALERT_UNIT,ALERT_PORT,'/-/ready')
    update_prom_config(o,alerts=True)
    o.out('✓','Alertmanager actif sur loopback:9093 ; règles intégrées à Prometheus')
    if args.smtp:
        o.out('›','SMTP configuré ; remise effective des e-mails non vérifiée. Tester une alerte contrôlée.')
