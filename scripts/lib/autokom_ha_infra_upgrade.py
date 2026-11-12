#!/usr/bin/env python3
"""Plan/apply the AUTOKOM 2-vCPU/4-GiB/100-GiB regional MIG upgrade.

Only the approved Terraform-managed MIG template, MIG updater and autoscaler
may change. No remote SSH, unmanaged resource deletion, or implicit DNS cutover.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
ENV_DIR = ROOT / 'terraform/environments/gcp-ha'
TFVARS = ENV_DIR / 'terraform.tfvars'
SAFETY = {'machine_type': '"e2-custom-2-4096"', 'boot_disk_size_gb': '100',
          'min_replicas': '2', 'max_replicas': '6', 'cpu_target': '0.55',
          'cooldown_seconds': '90'}
ALLOWED = {
    'module.regional_web_ha.google_compute_instance_template.web',
    'module.regional_web_ha.google_compute_region_instance_group_manager.web',
    'module.regional_web_ha.google_compute_region_autoscaler.web',
}


class UpgradeError(RuntimeError):
    pass


def render_tfvars(text):
    """Keep all user-specific subnet, network, region, NAT, backend settings."""
    for key, value in SAFETY.items():
        expression = re.compile(r'^(\s*' + re.escape(key) + r'\s*=\s*)[^\n#]*(#[^\n]*)?$', re.M)
        if expression.search(text):
            text = expression.sub(lambda m: m.group(1) + value + (m.group(2) or ''), text)
        else:
            text = text.rstrip() + f'\n{key} = {value}\n'
    return text


def checked(cmd, *, cwd=ENV_DIR, timeout=900, capture=False):
    result = subprocess.run(cmd, cwd=cwd, timeout=timeout, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None,
                            check=False)
    if result.returncode:
        raise UpgradeError('Commande echouee : ' + cmd[0] + ' ' + cmd[1] +
                           (' (voir les details precedents)' if not capture else '\n' + result.stderr[-1200:]))
    return result.stdout if capture else ''


def validate_plan(doc):
    """Refuse any unrelated Terraform change, including accidental service removal."""
    entries = doc.get('resource_changes', [])
    if not isinstance(entries, list):
        raise UpgradeError('Plan Terraform mal forme')
    changed = []
    for row in entries:
        change = row.get('change', {})
        actions = change.get('actions', [])
        if actions == ['no-op']:
            continue
        address = row.get('address')
        if address not in ALLOWED:
            raise UpgradeError('Changement non autorise dans le plan : ' + str(address))
        if actions not in (['create'], ['update'], ['delete', 'create'], ['create', 'delete']):
            raise UpgradeError('Action Terraform non autorisee : ' + str(address) + ' ' + str(actions))
        if 'instance_group_manager' in address and actions != ['update']:
            raise UpgradeError('Le MIG ne doit pas etre detruit/remplace')
        if 'autoscaler' in address and actions != ['update']:
            raise UpgradeError('L autoscaler ne doit pas etre detruit/remplace')
        changed.append((address, actions))
    return changed


def update_file(path, data):
    if path.is_symlink() or not path.is_file():
        raise UpgradeError('terraform.tfvars non present ou lien symbolique')
    old = path.read_text()
    if old == data:
        return None
    backup = path.with_name(path.name + '.bak-' + time.strftime('%Y%m%d-%H%M%S'))
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    fd, tmp = tempfile.mkstemp(prefix='.ha-vars-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return backup


def run(*, apply):
    if os.geteuid() != 0:
        raise UpgradeError('Utiliser sudo sur le serveur principal GCP')
    if not TFVARS.is_file():
        raise UpgradeError('terraform.tfvars absent dans le vrai depot GCP')
    if not (ENV_DIR / '.terraform').is_dir():
        raise UpgradeError('Backend Terraform absent : lancer ./manage.sh init avant')
    if not shutil.which('terraform'):
        raise UpgradeError('Terraform absent')
    original = TFVARS.read_text()
    # Do not reset the active worker/NAT/GCS configuration by mistake.
    if not re.search(r'(?m)^\s*enable_app_delivery\s*=\s*true\b', original):
        raise UpgradeError('enable_app_delivery=true requis (ne pas detruire le bucket GCS actif)')
    if not re.search(r'(?m)^\s*bootstrap_mode\s*=\s*"worker"', original):
        raise UpgradeError('bootstrap_mode="worker" requis : controle des VM deja en production')
    changed = render_tfvars(original)
    if not apply:
        print('AUTOKOM HA : previsualisation 2 vCPU / 4 GiB / 100 Go, 2-6 workers, CPU 55 %')
        print('Ancien fichier terraform.tfvars preserve. Parametres planifies :')
        for k, v in SAFETY.items():
            print(f'  {k} = {v}')
    else:
        print('AUTOKOM HA : backup de terraform.tfvars, puis plan et remplacement progressif des VM')
        backup = update_file(TFVARS, changed)
        if backup:
            print('Sauvegarde : ' + str(backup))
    checked(['terraform', 'validate'], timeout=60)
    with tempfile.TemporaryDirectory(prefix='ak-ha-plan-', dir='/tmp') as folder:
        plan = str(Path(folder) / 'upgrade.tfplan')
        cmd = ['terraform', 'plan', '-input=false', '-lock-timeout=30s', '-out=' + plan]
        if not apply:
            for k, v in SAFETY.items():
                cmd.extend(['-var', f'{k}={v.strip(chr(34))}' if k == 'machine_type' else f'{k}={v}'])
        checked(cmd, timeout=1200)
        doc = json.loads(checked(['terraform', 'show', '-json', plan], capture=True, timeout=60))
        changes = validate_plan(doc)
        print('Changements autorises :')
        for addr, actions in changes:
            print(' ', addr, ' => ', '/'.join(actions))
        if not changes:
            print('Infrastructure deja conforme. Aucune ressource a remplacer.')
            return 0
        if not apply:
            print('Previsualisation terminee : aucune VM remplacee.')
            return 0
        if not sys.stdin.isatty():
            raise UpgradeError('Confirmation interactive requise : session TTY')
        if input('Ecrire APPLIQUER-GCP pour changer le MIG : ').strip() != 'APPLIQUER-GCP':
            raise UpgradeError('Application annulee; sauvegarde tfvars conservee')
        checked(['terraform', 'apply', '-input=false', plan], timeout=3600)
    print('Plan applique. Les VMs MIG sont remplacees progressivement. Surveiller la sante HA.')
    print('HTTPS/DNS public ne sont pas modifies par cette commande.')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--check', action='store_true')
    g.add_argument('--apply', action='store_true')
    a = p.parse_args(argv)
    return run(apply=a.apply)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (UpgradeError, ValueError, OSError, subprocess.TimeoutExpired) as e:
        print('AUTOKOM_HA_UPGRADE_ERROR ' + str(e), file=sys.stderr)
        raise SystemExit(2)
