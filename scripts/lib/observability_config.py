#!/usr/bin/env python3
"""Validated, secret-free AUTOKOM Observability configuration.

Precedence: package defaults -> /etc/autokom/observability.ini ->
--config explicit file -> --set section.key=value -> specific CLI domain flags.
Never accepts credentials; local bind by design. No system access during parsing.
"""
from __future__ import annotations

from configparser import ConfigParser, Error as IniError
from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Sequence

DEFAULTS = Path(__file__).resolve().parents[2] / 'config/observability/defaults.ini'
SYSTEM_OVERRIDES = Path('/etc/autokom/observability.ini')

ALLOWED = {
    'jenkins': {'host', 'port', 'metrics_path', 'job_name'},
    'prometheus': {'host', 'port', 'scrape_interval', 'retention', 'storage_path'},
    'grafana': {'host', 'port', 'datasource_name', 'datasource_uid'},
    'domains': {'prometheus', 'grafana', 'letsencrypt_email'},
}

class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    jenkins_host: str = '127.0.0.1'
    jenkins_port: int = 8080
    jenkins_metrics_path: str = '/prometheus/'
    jenkins_job: str = 'jenkins'
    prometheus_host: str = '127.0.0.1'
    prometheus_port: int = 9090
    scrape_interval: str = '30s'
    retention: str = '15d'
    storage_path: str = '/var/lib/prometheus/metrics2'
    grafana_host: str = '127.0.0.1'
    grafana_port: int = 3000
    datasource_name: str = 'AUTOKOM Prometheus'
    datasource_uid: str = 'autokom-prometheus'
    prometheus_domain: str = ''
    grafana_domain: str = ''
    email: str = ''

    @property
    def jenkins_base(self):
        return f'http://{self.jenkins_host}:{self.jenkins_port}'

    @property
    def jenkins_metrics_url(self):
        return self.jenkins_base + self.jenkins_metrics_path

    @property
    def prometheus_base(self):
        return f'http://{self.prometheus_host}:{self.prometheus_port}'

    @property
    def grafana_base(self):
        return f'http://{self.grafana_host}:{self.grafana_port}'

    def display(self):
        return {
            'jenkins.metrics_url': self.jenkins_metrics_url,
            'jenkins.job_name': self.jenkins_job,
            'prometheus.url': self.prometheus_base,
            'prometheus.scrape_interval': self.scrape_interval,
            'prometheus.retention': self.retention,
            'prometheus.storage_path': self.storage_path,
            'grafana.url': self.grafana_base,
            'grafana.datasource_name': self.datasource_name,
            'grafana.datasource_uid': self.datasource_uid,
            'domains.prometheus': self.prometheus_domain or '(local)',
            'domains.grafana': self.grafana_domain or '(local)',
            'domains.letsencrypt_email': self.email or '(none)',
        }


def _validate_domain(value):
    value = value.strip().lower().rstrip('.')
    if value and (len(value) > 253 or not re.fullmatch(
        r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', value
    )):
        raise ConfigError('Nom de domaine invalide: ' + repr(value))
    return value


def _validate_port(value, label):
    try:
        number = int(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(f'{label} : port non entier') from e
    if number < 1024 or number > 65535:
        raise ConfigError(f'{label} : port autorisé entre 1024 et 65535')
    return number


def _validate_duration(value, label):
    if not re.fullmatch(r'[1-9][0-9]{0,5}(?:ms|s|m|h|d|w|y)', value):
        raise ConfigError(f'{label} : durée invalide (ex. 30s, 7d)')
    return value


def _validate_host(host, label):
    if host != '127.0.0.1':
        raise ConfigError(f'{label}: uniquement 127.0.0.1 dans cette phase ; aucun bind WAN')
    return host


def validate(settings):
    for service in ('jenkins', 'prometheus', 'grafana'):
        _validate_host(getattr(settings, f'{service}_host'), service)
        _validate_port(getattr(settings, f'{service}_port'), service)
    ports = [settings.jenkins_port, settings.prometheus_port, settings.grafana_port]
    if len(set(ports)) != len(ports):
        raise ConfigError('Les ports Jenkins, Prometheus et Grafana doivent être distincts')
    if not re.fullmatch(r'/[a-zA-Z0-9/_-]{1,150}/', settings.jenkins_metrics_path):
        raise ConfigError('jenkins.metrics_path : chemin absolu avec / final requis')
    for name, value in [('jenkins.job_name', settings.jenkins_job),
                        ('grafana.datasource_uid', settings.datasource_uid)]:
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_.-]{0,63}', value):
            raise ConfigError(name + ' : identifiant invalide')
    if not re.fullmatch(r'[\w .-]{1,80}', settings.datasource_name, flags=re.UNICODE):
        raise ConfigError('grafana.datasource_name : libellé invalide')
    _validate_duration(settings.scrape_interval, 'prometheus.scrape_interval')
    _validate_duration(settings.retention, 'prometheus.retention')
    storage = Path(settings.storage_path)
    if not storage.is_absolute() or '..' in storage.parts or not str(storage).startswith('/var/lib/prometheus/'):
        raise ConfigError('prometheus.storage_path doit être sous /var/lib/prometheus/')
    prom = _validate_domain(settings.prometheus_domain)
    graf = _validate_domain(settings.grafana_domain)
    if prom and prom == graf:
        raise ConfigError('Prometheus et Grafana ne peuvent pas utiliser le même domaine')
    if settings.email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', settings.email):
        raise ConfigError('Email Let’s Encrypt invalide')
    return replace(settings, prometheus_domain=prom, grafana_domain=graf)


def load(*, custom_path=None, changes: Sequence[str] = (),
         system_path=SYSTEM_OVERRIDES, defaults_path=DEFAULTS):
    ini = ConfigParser(interpolation=None, strict=True)
    if not Path(defaults_path).is_file():
        raise ConfigError('Fichier de valeurs par défaut manquant : ' + str(defaults_path))
    for path in (defaults_path, system_path, custom_path):
        if path is not None and Path(path).is_file():
            try:
                with open(path, encoding='utf-8') as fh:
                    ini.read_file(fh, source=str(path))
            except (IniError, UnicodeError, OSError) as e:
                raise ConfigError(f'INI invalide ou illisible : {path} : {e}') from e
        elif path is not None and path == custom_path:
            raise ConfigError('Fichier --config introuvable : ' + str(path))
    for section in ini.sections():
        if section not in ALLOWED:
            raise ConfigError('Section inconnue : ' + section)
        for key in ini.options(section):
            if key not in ALLOWED[section]:
                raise ConfigError(f'Option inconnue : {section}.{key}')
    for pair in changes:
        if '=' not in pair or '.' not in pair.split('=', 1)[0]:
            raise ConfigError('--set exige section.option=valeur')
        key, value = pair.split('=', 1)
        section, option = key.split('.', 1)
        if section not in ALLOWED or option not in ALLOWED[section]:
            raise ConfigError('Clé --set inconnue : ' + key)
        if not ini.has_section(section):
            ini.add_section(section)
        ini.set(section, option, value)
    def v(section, key):
        return ini.get(section, key).strip()
    result = Settings(
        jenkins_host=v('jenkins', 'host'),
        jenkins_port=_validate_port(v('jenkins', 'port'), 'jenkins'),
        jenkins_metrics_path=v('jenkins', 'metrics_path'),
        jenkins_job=v('jenkins', 'job_name'),
        prometheus_host=v('prometheus', 'host'),
        prometheus_port=_validate_port(v('prometheus', 'port'), 'prometheus'),
        scrape_interval=v('prometheus', 'scrape_interval'),
        retention=v('prometheus', 'retention'),
        storage_path=v('prometheus', 'storage_path'),
        grafana_host=v('grafana', 'host'),
        grafana_port=_validate_port(v('grafana', 'port'), 'grafana'),
        datasource_name=v('grafana', 'datasource_name'),
        datasource_uid=v('grafana', 'datasource_uid'),
        prometheus_domain=v('domains', 'prometheus'),
        grafana_domain=v('domains', 'grafana'),
        email=v('domains', 'letsencrypt_email'),
    )
    return validate(result)
