#!/usr/bin/env python3
"""Read-only per-worker Compute/Monitoring inventory for AUTOKOM HA."""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import urllib.parse
import urllib.error
import urllib.request
from typing import Any

import autokom_ha_preflight as pf

REGEX = re.compile(r'[a-z][a-z0-9-]{0,62}\Z')
ZONE = re.compile(r'[a-z]+-[a-z0-9]+[0-9]-[a-z]\Z')
METRICS = {
    'cpu_utilization': ('compute.googleapis.com/instance/cpu/utilization', 'ALIGN_MEAN'),
    'network_receive_Bps': ('compute.googleapis.com/instance/network/received_bytes_count', 'ALIGN_RATE'),
    'network_send_Bps': ('compute.googleapis.com/instance/network/sent_bytes_count', 'ALIGN_RATE'),
}


def compute_instance(project: str, zone: str, name: str) -> dict[str, Any]:
    if not (ZONE.fullmatch(zone) and REGEX.fullmatch(name)):
        raise ValueError('Invalid GCE zone or instance')
    if pf._metadata_mode():
        return pf._compute_api('GET', f'/{project}/zones/{zone}/instances/{name}')
    return pf.command('compute', 'instances', 'describe', name,
                      f'--zone={zone}', f'--project={project}')


def serial_log(project: str, zone: str, name: str, lines: int = 80) -> str:
    if not (ZONE.fullmatch(zone) and REGEX.fullmatch(name) and 1 <= lines <= 500):
        raise ValueError('Invalid serial log request')
    if pf._metadata_mode():
        response = pf._compute_api('GET', f'/{project}/zones/{zone}/instances/{name}/serialPort?port=1')
        content = response.get('contents') or ''
    else:
        proc = subprocess.run(['gcloud', 'compute', 'instances', 'get-serial-port-output', name,
                               '--port=1', f'--zone={zone}', f'--project={project}'],
                              capture_output=True, text=True, timeout=45, check=False)
        if proc.returncode:
            raise RuntimeError('Serial console unavailable: ' + proc.stderr[:150])
        content = proc.stdout
    # Scope only to target; never print credentials, metadata tokens or OAuth responses.
    return '\n'.join(content.splitlines()[-lines:])


def health_by_instance(payload: Any) -> dict[str, str]:
    result = {}
    def visit(node):
        if isinstance(node, dict):
            statuses = node.get('healthStatus')
            if isinstance(statuses, list):
                for entry in statuses:
                    if isinstance(entry, dict):
                        name = str(entry.get('instance') or '').rsplit('/', 1)[-1]
                        if name:
                            result[name] = str(entry.get('healthState') or 'UNKNOWN').upper()
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(payload)
    return result


def _monitoring(project: str, metric: str, alignment: str) -> dict[str, float]:
    finish = dt.datetime.now(dt.timezone.utc)
    start = finish - dt.timedelta(minutes=8)
    stamp = lambda x: x.isoformat(timespec='seconds').replace('+00:00', 'Z')
    query = {
        'filter': 'metric.type="' + metric + '" AND resource.type="gce_instance"',
        'interval.startTime': stamp(start), 'interval.endTime': stamp(finish),
        'aggregation.alignmentPeriod': '300s',
        'aggregation.perSeriesAligner': alignment,
        'view': 'FULL', 'pageSize': '100',
    }
    token = pf.oauth_token()
    seen = {}
    for _ in range(5):
        url = f'https://monitoring.googleapis.com/v3/projects/{urllib.parse.quote(project)}/timeSeries?' + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token,
                                                   'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as response:
            doc = json.load(response)
        for series in doc.get('timeSeries', []):
            resource = series.get('resource') or {}
            instance_id = str((resource.get('labels') or {}).get('instance_id') or '')
            if not instance_id:
                continue
            points = series.get('points') or []
            if not points:
                continue
            try:
                number = (points[0].get('value') or {}).get('doubleValue')
                if number is None:
                    number = (points[0].get('value') or {}).get('int64Value')
                number = float(number)
            except (TypeError, ValueError):
                continue
            seen[instance_id] = seen.get(instance_id, 0.0) + number
        if not doc.get('nextPageToken'):
            return seen
        query['pageToken'] = doc['nextPageToken']
    raise RuntimeError('Too many GCE time series; query requires filtering')


def enrich(report: dict[str, Any], *, cpu_and_network: bool = False) -> dict[str, Any]:
    project = report['project']
    statuses = health_by_instance(pf.command('compute', 'backend-services', 'get-health',
                                              report['backend'], '--global', f'--project={project}'))
    for row in report['instances']:
        row['health'] = statuses.get(row['name'], 'UNKNOWN')
        row['action'] = 'UNKNOWN'
        row['private_ip'] = None
        row['public_ip'] = None
        row['instance_id'] = None
        row['template'] = ''
        try:
            details = compute_instance(project, row['zone'], row['name'])
            row['instance_id'] = str(details.get('id') or '')
            nics = details.get('networkInterfaces') or []
            if nics:
                row['private_ip'] = nics[0].get('networkIP')
                configs = nics[0].get('accessConfigs') or []
                row['public_ip'] = configs[0].get('natIP') if configs else None
            row['template'] = str(details.get('metadata', {}).get('items', ''))[:0]  # deliberately no metadata secrets
        except (ValueError, RuntimeError, OSError, TimeoutError):
            row['details_unavailable'] = True
    # MIG knows action and version, which must not be guessed from GCE instance status.
    try:
        nodes = pf.command('compute', 'instance-groups', 'managed', 'list-instances',
                           report['mig'], f'--region={report["region"]}', f'--project={project}')
        actions = {str(n.get('instance', '')).rsplit('/', 1)[-1]: n for n in nodes}
        for row in report['instances']:
            original = actions.get(row['name']) or {}
            row['action'] = original.get('currentAction', 'UNKNOWN')
            row['template'] = str(original.get('version', {}).get('instanceTemplate') or
                                  original.get('instanceTemplate') or '').rsplit('/', 1)[-1]
    except (ValueError, RuntimeError, OSError, TimeoutError):
        report.setdefault('warnings', []).append('Actions détaillées MIG indisponibles.')
    if cpu_and_network:
        for key, (metric_type, aligner) in METRICS.items():
            try:
                values = _monitoring(project, metric_type, aligner)
                for row in report['instances']:
                    if row.get('instance_id') in values:
                        row[key] = values[row['instance_id']]
            except (ValueError, RuntimeError, OSError, TimeoutError, urllib.error.URLError):
                report.setdefault('warnings', []).append('Métrique cloud indisponible : ' + key)
    return report


def to_prometheus(report: dict[str, Any]) -> str:
    def esc(value: str) -> str:
        return value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    out = []
    for row in report['instances']:
        label = f'instance="{esc(row["name"])}",zone="{esc(row["zone"])}"'
        out.append(f'autokom_ha_worker_running{{{label}}} {int(row["status"] == "RUNNING")}\n')
        out.append(f'autokom_ha_worker_healthy{{{label}}} {int(row.get("health") == "HEALTHY")}\n')
        out.append(f'autokom_ha_worker_update_action{{{label},action="{esc(row.get("action", "UNKNOWN"))}"}} 1\n')
        for metric in METRICS:
            if metric in row:
                out.append(f'autokom_ha_worker_{metric}{{{label}}} {row[metric]:.6f}\n')
    return ''.join(out)
