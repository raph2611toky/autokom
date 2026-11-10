#!/usr/bin/env python3
"""Check Docker + GCE metadata identity + private Artifact Registry access.

No static credentials, no mutations, and no customer workloads.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

CONF = Path('/etc/autokom-ha/worker.json')
METADATA = 'http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token'


def require_config(obj: dict) -> tuple[str, str, str]:
    if not isinstance(obj, dict):
        raise ValueError('Invalid worker settings')
    project, region, repo = (obj.get(k) for k in ('project_id', 'region', 'artifact_repository'))
    if not isinstance(project, str) or not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]', project):
        raise ValueError('Invalid project id')
    if not isinstance(region, str) or not re.fullmatch(r'[a-z]+-[a-z0-9]+[0-9]', region):
        raise ValueError('Invalid region')
    if not isinstance(repo, str) or not re.fullmatch(r'[a-z][a-z0-9-]{1,62}', repo):
        raise ValueError('Invalid Artifact Registry repository')
    return project, region, repo


def probe(config: dict, opener=urllib.request.urlopen, runner=subprocess.run) -> bool:
    project, region, repo = require_config(config)
    docker = runner(['/usr/bin/docker', 'info', '--format', '{{.ServerVersion}}'],
                    capture_output=True, text=True, timeout=15, check=False)
    if docker.returncode or not docker.stdout.strip():
        raise RuntimeError('Docker daemon is not ready')
    req = urllib.request.Request(METADATA, headers={'Metadata-Flavor': 'Google'})
    with opener(req, timeout=6) as response:
        token_doc = json.load(response)
    token = token_doc.get('access_token') if isinstance(token_doc, dict) else None
    if not isinstance(token, str) or len(token) < 16:
        raise RuntimeError('Metadata token unavailable')
    url = f'https://artifactregistry.googleapis.com/v1/projects/{project}/locations/{region}/repositories/{repo}'
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
    with opener(req, timeout=12) as response:
        if response.status != 200:
            raise RuntimeError('Artifact Registry access denied')
        # Do not log any API payload, token or metadata value.
        if not isinstance(json.load(response), dict):
            raise RuntimeError('Artifact Registry reply invalid')
    return True


def main() -> int:
    try:
        config = json.loads(CONF.read_text(encoding='utf-8'))
        probe(config)
    except urllib.error.HTTPError as exc:
        print('AUTOKOM_HA_WORKER_PROBE_ERROR HTTP_' + str(exc.code), file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.TimeoutExpired,
            urllib.error.URLError, json.JSONDecodeError) as exc:
        print('AUTOKOM_HA_WORKER_PROBE_ERROR ' + type(exc).__name__, file=sys.stderr)
        return 2
    print('AUTOKOM_HA_WORKER_PROBE_OK Docker/metadata/ArtifactRegistry')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
