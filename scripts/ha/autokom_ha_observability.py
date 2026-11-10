#!/usr/bin/env python3
"""Provision AUTOKOM GCP HA into the EXISTING Prometheus / Grafana services.

Default dry-run; apply is atomic with backup + rollback for Prometheus. Never
reinstall or replace an existing Prometheus configuration without validation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
PROM = Path('/etc/prometheus/prometheus.yml')
GRAFANA = Path('/etc/grafana/provisioning/dashboards/autokom-ha.yaml')
DASH_DIR = Path('/var/lib/grafana/dashboards/autokom-ha')
DASH_PATH = DASH_DIR / 'autokom-ha-gcp.json'
RULES = Path('/etc/prometheus/rules/autokom-ha.yml')
UNITS_DIR = Path('/etc/systemd/system')
MANAGED = '# AUTOKOM HA MANAGED v2\n'
JOB = {'job_name': 'autokom-gcp-ha', 'scrape_interval': '60s',
       'scrape_timeout': '45s',
       'static_configs': [{'targets': ['127.0.0.1:19191'], 'labels': {'service': 'autokom-ha'}}]}


class ProvisionError(RuntimeError):
    pass


def _command(*args, timeout=30):
    try:
        proc = subprocess.run(list(args), capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisionError('Commande indisponible : ' + args[0]) from exc
    if proc.returncode:
        raise ProvisionError('Échec système : ' + ' '.join(args[:2]))
    return proc.stdout


def merge_prometheus(text: str) -> str:
    try:
        import yaml
    except ImportError as exc:
        raise ProvisionError('PyYAML absent. Installer l’observabilité AUTOKOM.') from exc
    try:
        cfg = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProvisionError('Prometheus YAML invalide.') from exc
    if not isinstance(cfg, dict) or not isinstance(cfg.get('scrape_configs'), list):
        raise ProvisionError('Configuration Prometheus incompatible : scrape_configs manquant')
    existing = [job for job in cfg['scrape_configs'] if isinstance(job, dict) and job.get('job_name') == JOB['job_name']]
    if len(existing) > 1 or (existing and existing[0] != JOB):
        raise ProvisionError('Job HA déjà présent mais différent : modification manuelle refusée')
    if not existing:
        cfg['scrape_configs'].append(JOB)
    rule_files = cfg.setdefault('rule_files', [])
    if not isinstance(rule_files, list):
        raise ProvisionError('rule_files Prometheus incompatible')
    if str(RULES) not in rule_files:
        rule_files.append(str(RULES))
    return yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)


def dashboard(uid: str) -> dict:
    base = json.loads((ROOT / 'scripts/ha/autokom-ha-grafana-dashboard.json').read_text())
    # The source originally targeted manual Grafana import; use AUTOKOM's existing
    # provisioned Prometheus UID, not a Grafana interactive datasource selector.
    data = json.loads(json.dumps(base).replace('${DS_PROMETHEUS}', uid))
    data.pop('__inputs', None)
    data.pop('id', None)
    data['uid'] = 'autokom-gcp-ha-20261110'
    data['editable'] = False
    data['title'] = 'AUTOKOM · GCP HA · MIG / Load Balancer'
    panels = data.get('panels') or []
    expressions = [
        ('Workers RUNNING par zone', 'autokom_ha_worker_running', 'short'),
        ('Workers HEALTHY par zone', 'autokom_ha_worker_healthy', 'short'),
        ('CPU worker', '100 * autokom_ha_worker_cpu_utilization', 'percent'),
        ('Trafic réseau reçu des workers', 'autokom_ha_worker_network_receive_Bps', 'Bps'),
        ('Trafic réseau envoyé des workers', 'autokom_ha_worker_network_send_Bps', 'Bps'),
        ('Cible Prometheus AUTOKOM HA', 'up{job="autokom-gcp-ha"}', 'short'),
        ('Erreurs LB 5xx en 5 min', 'autokom_ha_lb_requests_5m{response_class="5xx"}', 'short'),
    ]
    y = max((p.get('gridPos', {}).get('y', 0) + p.get('gridPos', {}).get('h', 8) for p in panels), default=0)
    offset = max((p.get('id', 0) for p in panels), default=0)
    for idx, (title, expr, unit) in enumerate(expressions):
        pos = {'x': 12 * (idx % 2), 'y': y + 8 * (idx // 2), 'w': 12, 'h': 8}
        panels.append({'id': offset + idx + 1, 'title': title, 'type': 'timeseries',
                       'gridPos': pos,
                       'datasource': {'type': 'prometheus', 'uid': uid},
                       'targets': [{'refId': 'A', 'expr': expr,
                                    'legendFormat': '{{instance}} {{zone}}',
                                    'datasource': {'type': 'prometheus', 'uid': uid}}],
                       'fieldConfig': {'defaults': {'unit': unit}, 'overrides': []},
                       'options': {}})
    data['panels'] = panels
    return data


def rules() -> str:
    return MANAGED + '''groups:
  - name: autokom-ha-infrastructure
    interval: 60s
    rules:
      - alert: AUTOKOMHAMetricsMissing
        expr: absent(up{job="autokom-gcp-ha"}) or up{job="autokom-gcp-ha"} == 0
        for: 5m
        labels: {severity: warning, component: autokom-ha}
        annotations:
          summary: "AUTOKOM : export des métriques HA absent"
      - alert: AUTOKOMHAGCPCollectionFailed
        expr: autokom_ha_metrics_scrape_success == 0
        for: 5m
        labels: {severity: warning, component: autokom-ha}
        annotations:
          summary: "AUTOKOM : collecte Compute Engine / Cloud Monitoring indisponible"
      - alert: AUTOKOMHAInsufficientInfrastructure
        expr: autokom_ha_lb_healthy_backends < 2 or autokom_ha_mig_instances < 2
        for: 5m
        labels: {severity: critical, component: autokom-ha}
        annotations:
          summary: "AUTOKOM : moins de deux backends GCP sains (infra, pas santé applicative)"
      - alert: AUTOKOMHALoadBalancer5xx
        expr: autokom_ha_lb_requests_5m{response_class="5xx"} > 0
        for: 5m
        labels: {severity: warning, component: autokom-ha}
        annotations:
          summary: "AUTOKOM : erreurs 5xx remontées par le Load Balancer"
'''


def provider() -> str:
    return MANAGED + '''apiVersion: 1
providers:
  - name: autokom-gcp-ha
    orgId: 1
    folder: AUTOKOM
    type: file
    disableDeletion: true
    allowUiUpdates: false
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards/autokom-ha
'''


def managed_unit(name: str) -> str:
    source = ROOT / 'scripts/ha/systemd' / name
    if not source.is_file():
        raise ProvisionError('Unité AUTOKOM absente : ' + name)
    return source.read_text(encoding='utf-8')


def _safe_replace(path: Path, content: str, *, managed: bool = True) -> bool:
    if path.is_symlink():
        raise ProvisionError('Lien symbolique refusé : ' + str(path))
    if path.exists():
        old = path.read_text(encoding='utf-8')
        if old == content:
            return False
        if managed and not (old.startswith(MANAGED) or
                            ('AUTOKOM' in old and 'HA' in old and path.parent == UNITS_DIR)):
            raise ProvisionError('Fichier non géré, refus de remplacer : ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.autokom-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o644)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return True


def _prometheus_install_if_missing() -> None:
    if PROM.is_file() and shutil.which('promtool') and shutil.which('grafana-server'):
        return
    # Reuse the existing AUTOKOM observability installer; never add a second
    # Prometheus/Grafana stack. Noninteractive avoids collecting new secrets.
    install = ROOT / 'scripts/install/observability.sh'
    if not install.exists():
        raise ProvisionError('Installateur observabilité AUTOKOM introuvable.')
    _command('/bin/bash', str(install), '--apply', '--non-interactive', timeout=1800)
    if not PROM.is_file() or not shutil.which('promtool') or not shutil.which('grafana-server'):
        raise ProvisionError('Observabilité AUTOKOM absente après installation.')


def _promtool_validate(candidate: str, *, rule_file: Path | None = None) -> None:
    if not shutil.which('promtool'):
        raise ProvisionError('promtool absent')
    with tempfile.TemporaryDirectory(prefix='autokom-ha-prom-') as tempdir:
        file = Path(tempdir) / 'prometheus.yml'
        file.write_text(candidate, encoding='utf-8')
        # Staging references the staged rule, not an absent production path.
        # A second validation of the actual file runs after provisioning.
        if rule_file:
            check = candidate.replace(str(RULES), str(rule_file))
            file.write_text(check, encoding='utf-8')
        _command('promtool', 'check', 'config', str(file), timeout=25)


def install(*, apply: bool, datasource_uid: str, install_missing: bool = True) -> bool:
    if os.geteuid() != 0 and apply:
        raise ProvisionError('Utiliser sudo autokom iac ha monitor --apply')
    if not apply:
        print('Plan HA : 2 unités systemd (exporter + publisher), 1 timer, 1 job Prometheus, 1 dashboard Grafana')
        print('Aucun site client, routage DNS, MIG ou déploiement cloud modifié.')
        print('Appliquer avec : sudo autokom iac ha monitor --apply')
        return False
    if install_missing:
        _prometheus_install_if_missing()
    if not PROM.is_file() or not shutil.which('promtool') or not shutil.which('grafana-server'):
        raise ProvisionError('Prometheus/Grafana absents (autokom install --only observability).')
    old_prom = PROM.read_text(encoding='utf-8')
    new_prom = merge_prometheus(old_prom)
    if RULES.is_symlink() or (RULES.exists() and not RULES.read_text().startswith(MANAGED)):
        raise ProvisionError('Règles Prometheus HA non gérées : ' + str(RULES))
    with tempfile.TemporaryDirectory(prefix='autokom-ha-rules-') as directory:
        staged = Path(directory) / 'autokom-ha.yml'
        staged.write_text(rules(), encoding='utf-8')
        _command('promtool', 'check', 'rules', str(staged))
        _promtool_validate(new_prom, rule_file=staged)
    # Preflight ownership checks BEFORE changing any files.
    units = {
        'autokom-ha-metrics.service': managed_unit('autokom-ha-metrics.service'),
        'autokom-ha-autopublish.service': managed_unit('autokom-ha-autopublish.service'),
        'autokom-ha-autopublish.timer': managed_unit('autokom-ha-autopublish.timer'),
    }
    for name, content in units.items():
        path = UNITS_DIR / name
        if path.is_symlink():
            raise ProvisionError('Lien d’unité systemd refusé : ' + name)
        if path.exists() and path.read_text() != content and not ('AUTOKOM' in path.read_text() and 'HA' in path.read_text()):
            raise ProvisionError('Unité systemd étrangère : ' + name)
    if GRAFANA.exists() and not GRAFANA.read_text().startswith(MANAGED):
        raise ProvisionError('Provider Grafana non géré : ' + str(GRAFANA))
    if DASH_PATH.is_symlink():
        raise ProvisionError('Dashboard Grafana symbolique refusé')
    if DASH_PATH.exists():
        current = json.loads(DASH_PATH.read_text())
        if current.get('uid') != 'autokom-gcp-ha-20261110':
            raise ProvisionError('Dashboard HA non géré')
    if PROM.is_symlink():
        raise ProvisionError('Prometheus config symbolic link refused')
    backup = PROM.with_name('prometheus.yml.autokom-ha-before-' + str(int(time.time())))
    if new_prom != old_prom:
        # Durable operator recovery copy, never overwrite.
        with open(backup, 'x', encoding='utf-8') as stream:
            os.chmod(backup, 0o600)
            stream.write(old_prom)
    try:
        for name, content in units.items():
            _safe_replace(UNITS_DIR / name, content)
        Path('/var/lib/autokom/ha').mkdir(parents=True, exist_ok=True, mode=0o700)
        (Path('/var/lib/autokom/ha/publish-queue')).mkdir(parents=True, exist_ok=True, mode=0o700)
        (Path('/var/lib/autokom/ha/published')).mkdir(parents=True, exist_ok=True, mode=0o700)
        # Refuse unsafe preexisting files/dirs to protect images and metadata.
        for directory in (Path('/var/lib/autokom/ha'), Path('/var/lib/autokom/ha/publish-queue'),
                          Path('/var/lib/autokom/ha/published')):
            if directory.is_symlink() or directory.stat().st_uid != 0 or directory.stat().st_mode & 0o077:
                raise ProvisionError('Répertoire HA non privé : ' + str(directory))
        _safe_replace(RULES, rules())
        grafana_provider_changed = _safe_replace(GRAFANA, provider())
        # JSON managed identity is validated separately; writer is atomic.
        _safe_replace(DASH_PATH, json.dumps(dashboard(datasource_uid), ensure_ascii=False, indent=2) + '\n', managed=False)
        if new_prom != old_prom:
            _safe_replace(PROM, new_prom, managed=False)
        _command('promtool', 'check', 'config', str(PROM))
        _command('systemctl', 'daemon-reload')
        _command('systemctl', 'enable', '--now', 'autokom-ha-metrics.service')
        _command('systemctl', 'restart', 'autokom-ha-metrics.service')
        _command('systemctl', 'enable', '--now', 'autokom-ha-autopublish.timer')
        if new_prom != old_prom:
            _command('systemctl', 'kill', '--kill-who=main', '--signal=HUP', 'prometheus.service')
        # Grafana discovers new providers on startup, but dashboard JSON is
        # watched automatically; avoid unnecessary Grafana restarts on reruns.
        if grafana_provider_changed:
            _command('systemctl', 'restart', 'grafana-server.service', timeout=70)
    except Exception:
        # Restore Prometheus config if the reload phase fails.
        if new_prom != old_prom and backup.exists():
            _safe_replace(PROM, backup.read_text(encoding='utf-8'), managed=False)
            try:
                _command('systemctl', 'kill', '--kill-who=main', '--signal=HUP', 'prometheus.service')
            except ProvisionError:
                pass
        raise
    print('AUTOKOM HA provisioning: exporter, publication automatique, Prometheus et Grafana configurés.')
    print('Dashboard : /d/autokom-gcp-ha-20261110 (sur votre Grafana existant).')
    return True
