#!/usr/bin/env python3
"""AUTOKOM Loki/Alloy observability integration with existing Prometheus.

No parallel Prometheus, Grafana, or Alertmanager is installed. Only new
namespaced scrape jobs, one rule file, and private canary units are managed.
Unknown or user-modified configuration causes an explicit error.
"""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request

PROM = Path('/etc/prometheus/prometheus.yml')
RULES = Path('/etc/prometheus/rules/autokom-logging.yml')
CANARY_SERVICE=Path('/etc/systemd/system/autokom-logs-canary.service')
CANARY_TIMER=Path('/etc/systemd/system/autokom-logs-canary.timer')
EXPORTER_SERVICE=Path('/etc/systemd/system/autokom-logs-exporter.service')
CANARY_SCRIPT = Path(__file__).resolve().with_name('logs_canary.py')
MARKER='# AUTOKOM MANAGED — Loki/Alloy v21\n'

class MonitorError(RuntimeError):pass

def command(*args, timeout=40):
    from tool_banner import Activity
    with Activity('Exécution : '+ ' '.join(str(x) for x in args[:2])):
        p=subprocess.run(list(args),capture_output=True,text=True,timeout=timeout)
    if p.returncode:
        raise MonitorError('Commande échouée : '+args[0]+' (exit '+str(p.returncode)+')')
    return p.stdout

def rules_yaml():
    return MARKER+'''groups:
  - name: autokom-logging-availability
    interval: 30s
    rules:
      - alert: AUTOKOMLokiUnavailable
        expr: up{job="autokom-loki"} == 0 or absent(up{job="autokom-loki"})
        for: 3m
        labels: {severity: critical, component: logging}
        annotations:
          summary: "AUTOKOM : Loki indisponible"
          description: "La cible Prometheus de Loki ne répond plus depuis 3 minutes."
      - alert: AUTOKOMAlloyUnavailable
        expr: up{job="autokom-alloy"} == 0 or absent(up{job="autokom-alloy"})
        for: 3m
        labels: {severity: critical, component: logging}
        annotations:
          summary: "AUTOKOM : collecteur Alloy indisponible"
      - alert: AUTOKOMLogsCanaryUnavailable
        expr: up{job="autokom-logs-canary"} == 0 or absent(up{job="autokom-logs-canary"})
        for: 5m
        labels: {severity: warning, component: logging}
        annotations:
          summary: "AUTOKOM : export des sondes de journaux indisponible"
      - alert: AUTOKOMLogsEndToEndFailed
        expr: autokom_logs_canary_success{job="autokom-logs-canary"} == 0
        for: 12m
        labels: {severity: critical, component: logging}
        annotations:
          summary: "AUTOKOM : aucun journal canari confirmé dans Loki"
          description: "Examiner journald, Alloy, les labels et Loki; ne pas se fier au statut du conteneur seul."
      - alert: AUTOKOMLogsCanaryStale
        expr: (time() - autokom_logs_canary_last_run_timestamp_seconds{job="autokom-logs-canary"} > 900)
        for: 5m
        labels: {severity: warning, component: logging}
        annotations:
          summary: "AUTOKOM : dernière sonde de logs trop ancienne (> 15 minutes)"
      - alert: AUTOKOMLoggingDiskPressure
        expr: (node_filesystem_avail_bytes{job="node",fstype!~"tmpfs|overlay|squashfs",mountpoint=~"/|/var/lib/docker|/var/lib"} / node_filesystem_size_bytes{job="node",fstype!~"tmpfs|overlay|squashfs",mountpoint=~"/|/var/lib/docker|/var/lib"}) < 0.15
        for: 15m
        labels: {severity: warning, component: logging}
        annotations:
          summary: "AUTOKOM : espace disque faible pour les journaux"
          description: "Moins de 15% disponible sur un volume local surveillé. Identifier le volume utilisé par Docker/Loki."
      - alert: AUTOKOMLokiIngestionRejected
        expr: sum(rate(loki_discarded_samples_total{job="autokom-loki"}[5m])) > 0
        for: 5m
        labels: {severity: warning, component: logging}
        annotations:
          summary: "AUTOKOM : Loki rejette des entrées de logs"
'''

JOBS={
 'autokom-loki': {'job_name':'autokom-loki','metrics_path':'/metrics','scrape_interval':'30s','static_configs':[{'targets':['127.0.0.1:3100']}]},
 'autokom-alloy': {'job_name':'autokom-alloy','metrics_path':'/metrics','scrape_interval':'30s','static_configs':[{'targets':['127.0.0.1:12345']}]},
 'autokom-logs-canary': {'job_name':'autokom-logs-canary','metrics_path':'/metrics','scrape_interval':'30s','static_configs':[{'targets':['127.0.0.1:19109']}]},
}

def merge_prometheus(existing):
    import yaml
    obj=yaml.safe_load(existing)
    if not isinstance(obj,dict) or not isinstance(obj.get('scrape_configs'),list):
        raise MonitorError('Prometheus non standard : fusion impossible')
    jobs=obj['scrape_configs']
    for name,expected in JOBS.items():
        matches=[job for job in jobs if isinstance(job,dict) and job.get('job_name')==name]
        if len(matches)>1:raise MonitorError('Jobs Prometheus dupliqués : '+name)
        if matches and matches[0]!=expected:
            raise MonitorError('Job personnalisé non modifié : '+name)
        if not matches:jobs.append(expected)
    if obj.get('rule_files') is None:obj['rule_files']=[]
    if not isinstance(obj['rule_files'],list):raise MonitorError('rule_files invalide')
    if str(RULES) not in obj['rule_files']:obj['rule_files'].append(str(RULES))
    return yaml.safe_dump(obj,sort_keys=False,allow_unicode=True)

def managed_write(path, content, *, mode=0o644):
    if path.is_symlink():raise MonitorError('Lien symbolique refusé : '+str(path))
    if path.exists() and not path.read_text().startswith(MARKER):
        raise MonitorError('Configuration non gérée : '+str(path))
    if path.exists() and path.read_text()==content:return False
    if path.exists():raise MonitorError('Configuration AUTOKOM modifiée : refus de l’écraser : '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.autokom-',dir=path.parent)
    try:
        import os
        with os.fdopen(fd,'w') as f:f.write(content)
        os.chmod(tmp,mode)
        os.replace(tmp,path)
    finally:
        if Path(tmp).exists():Path(tmp).unlink()
    return True

def systemd_files():
    script=str(CANARY_SCRIPT)
    return {
     CANARY_SERVICE:MARKER+f'''[Unit]
Description=AUTOKOM journal -> Alloy -> Loki E2E probe
After=network-online.target docker.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {script} probe --wait 45
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/lib/autokom/logs-monitor
''',
     CANARY_TIMER:MARKER+'''[Unit]
Description=AUTOKOM Loki log canary every five minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=15s
Persistent=true
Unit=autokom-logs-canary.service
[Install]
WantedBy=timers.target
''',
     EXPORTER_SERVICE:MARKER+f'''[Unit]
Description=AUTOKOM private Loki canary Prometheus exporter
After=network.target
[Service]
Type=simple
User=nobody
ExecStart=/usr/bin/python3 {script} serve
Restart=on-failure
RestartSec=5s
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
RestrictAddressFamilies=AF_INET AF_UNIX
[Install]
WantedBy=multi-user.target
''',
    }

def provision_units():
    for path,content in systemd_files().items():
        managed_write(path,content)
    store=Path('/var/lib/autokom/logs-monitor')
    store.mkdir(mode=0o755,parents=True,exist_ok=True)
    command('systemctl','daemon-reload')
    command('systemctl','enable','--now','autokom-logs-exporter.service')
    command('systemctl','enable','--now','autokom-logs-canary.timer')

def provision_prometheus():
    if not PROM.is_file():
        raise MonitorError('Prometheus introuvable ; faire sudo autokom install --only observability d’abord')
    if not shutil.which('promtool'):
        raise MonitorError('promtool absent : aucun changement Prometheus effectué')
    old=PROM.read_text()
    new=merge_prometheus(old)
    if RULES.is_symlink() or RULES.exists() and RULES.read_text()!=rules_yaml():
        raise MonitorError('Règles logging modifiées/non gérées : '+str(RULES))
    # Validate rules before they can be loaded by Prometheus.
    with tempfile.TemporaryDirectory() as tmp:
        candidate=Path(tmp)/'autokom-logging.yml'
        candidate.write_text(rules_yaml())
        command('promtool','check','rules',str(candidate))
        # The validation candidate must see the real rule file path; validate
        # candidate config with the staged rule file in the same directory.
        import yaml
        check=yaml.safe_load(new)
        check['rule_files']=[str(candidate) if p==str(RULES) else p for p in check['rule_files']]
        draft=Path(tmp)/'prometheus.yml'
        draft.write_text(yaml.safe_dump(check,sort_keys=False))
        command('promtool','check','config',str(draft))
    # State backup is an operator-recovery artifact and never silently overwritten.
    if new!=old:
        backup=PROM.with_suffix('.yml.autokom-logging-v21-backup')
        if not backup.exists():
            import os
            fd,tmp=tempfile.mkstemp(prefix='.autokom-backup-',dir=PROM.parent)
            with os.fdopen(fd,'w') as f:f.write(old)
            os.chmod(tmp,0o600)
            os.replace(tmp,backup)
    rules_old=RULES.read_text() if RULES.exists() else None
    changed_rules=managed_write(RULES,rules_yaml())
    if new==old and not changed_rules:return False
    if new!=old:
        import os
        fd,tmp=tempfile.mkstemp(prefix='.autokom-prom-',dir=PROM.parent)
        with os.fdopen(fd,'w') as f:f.write(new)
        os.chmod(tmp,0o644)
        os.replace(tmp,PROM)
    try:
        command('promtool','check','config',str(PROM))
        # SIGHUP reload does not restart Jenkins, Grafana or Prometheus.
        command('systemctl','kill','--kill-who=main','--signal=HUP','prometheus.service')
        time.sleep(2)
        command('systemctl','is-active','--quiet','prometheus.service')
    except Exception:
        if new!=old:
            import os
            fd,tmp=tempfile.mkstemp(prefix='.autokom-rollback-',dir=PROM.parent)
            with os.fdopen(fd,'w') as f:f.write(old)
            os.replace(tmp,PROM)
        if rules_old is None:RULES.unlink(missing_ok=True)
        else:managed_write(RULES,rules_old)
        try:command('systemctl','kill','--kill-who=main','--signal=HUP','prometheus.service')
        except Exception:pass
        raise
    return True

def apply():
    # preflight before mutating state. No forced Grafana/Alertmanager restart.
    if not CANARY_SCRIPT.is_file():raise MonitorError('Source canari introuvable')
    if not PROM.is_file():raise MonitorError('Prometheus absent')
    if not shutil.which('promtool'):raise MonitorError('promtool requis')
    for path, content in systemd_files().items():
        if path.is_symlink() or path.exists() and path.read_text()!=content:
            raise MonitorError('Unit non gérée ou modifiée : '+str(path))
    if RULES.is_symlink() or RULES.exists() and RULES.read_text()!=rules_yaml():
        raise MonitorError('Règles logging modifiées : '+str(RULES))
    provision_prometheus()
    provision_units()
    print('  ✓ Supervision Loki/Alloy : 3 jobs Prometheus, alertes, sonde E2E périodique')
    print('  › Vérifier les cibles UP, le canari, puis le routage Alertmanager.')


def report():
    """Read-only health summary: no changes to containers, services or logs."""
    import urllib.error
    import urllib.request
    import json
    checks = [('Loki ready','http://127.0.0.1:3100/ready'),
              ('Alloy metrics','http://127.0.0.1:12345/metrics'),
              ('Sonde Prometheus','http://127.0.0.1:19109/metrics')]
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for title,url in checks:
        try:
            with opener.open(url,timeout=3) as response:
                head=response.read(20000).decode('utf-8','replace')
                available=response.status==200
            if title=='Loki ready':available=available and 'ready' in head.lower()
            elif title=='Alloy metrics':available=available and 'go_' in head
            elif title=='Sonde Prometheus':available=available and 'autokom_logs_canary_success' in head
            print('  '+('✓' if available else '!')+' '+title+': '+('répond' if available else 'réponse inattendue'))
        except (OSError,urllib.error.URLError,ValueError):
            print('  ! '+title+': indisponible / non confirmé')
    for name in JOBS:
        try:
            url='http://127.0.0.1:9090/api/v1/targets'
            with opener.open(url,timeout=3) as response:obj=json.load(response)
            targets=obj.get('data',{}).get('activeTargets',[])
            state=next((t.get('health','unknown') for t in targets
                        if t.get('labels',{}).get('job')==name),None)
            print('  '+('✓' if state=='up' else '!')+' Prometheus '+name+': '+(state or 'absent'))
        except (OSError,urllib.error.URLError,ValueError):
            print('  ! Prometheus '+name+': API indisponible')
    from logs_canary import STATE
    try:
        data=json.loads(STATE.read_text())
        elapsed=max(0,int(time.time())-int(data['timestamp']))
        print('  '+('✓' if data['success'] and elapsed<900 else '!')+
              ' Canary journald → Loki : '+str(data['state'])+' ; âge '+str(elapsed)+' s')
    except (OSError,ValueError,KeyError,TypeError):
        print('  ! Canary : aucune mesure persistée ; lancer sudo autokom logs verify')
    for path in [Path('/etc/grafana/provisioning/datasources/autokom-loki.yaml'),
                 Path('/var/lib/grafana/dashboards/autokom/autokom-logging-health.json')]:
        print('  '+('✓' if path.is_file() else '!')+' Provisioning : '+str(path))
