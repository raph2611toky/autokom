#!/usr/bin/env python3
"""Plan/apply the AUTOKOM 2-vCPU/4-GiB/70-GiB regional MIG upgrade.

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
# Backups must never dirty the Git checkout used by `autokom update`.
BACKUP_DIR = Path('/var/backups/autokom/terraform-gcp-ha')
SURGE_WORKERS = 2  # Two-zone regional MIG needs one spare worker per zone.
SAFETY = {'machine_type': '"e2-custom-2-4096"', 'boot_disk_size_gb': '70',
          'min_replicas': '2', 'max_replicas': '5', 'cpu_target': '0.55',
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


def read_quota(quotas, *, disk_gb=70, surge=SURGE_WORKERS):
    """Return free SSD quota (GiB) without misreading other disk quotas."""
    if not isinstance(quotas, list):
        raise UpgradeError('Reponse quota regionale GCP mal formee')
    for entry in quotas:
        if not isinstance(entry, dict):
            continue
        if str(entry.get('metric', '')).replace('-', '_') == 'SSD_TOTAL_GB':
            try:
                used = float(entry['usage'])
                limit = float(entry['limit'])
            except (TypeError, ValueError, KeyError) as exc:
                raise UpgradeError('Valeur du quota SSD GCP invalide') from exc
            if limit < 0 or used < 0:
                raise UpgradeError('Valeur du quota SSD GCP invalide')
            required = disk_gb * surge
            return {'usage': used, 'limit': limit, 'free': max(0.0, limit - used),
                    'required': required, 'sufficient': limit - used >= required}
    raise UpgradeError('Quota SSD_TOTAL_GB absent dans la reponse regionale GCP')


def tfvar_string(tfvars, key):
    result = re.search(r'(?m)^\s*' + re.escape(key) + r'\s*=\s*"([a-z0-9-]+)"\s*(?:#.*)?$', tfvars)
    if not result:
        raise UpgradeError('Variable Terraform manquante : ' + key)
    return result.group(1)


def check_ssd_quota(tfvars, *, fatal):
    """Query actual GCP SSD quota before attempting a surge-based replacement."""
    region, project = (tfvar_string(tfvars, key) for key in ('region', 'project_id'))
    try:
        payload = checked(['gcloud', 'compute', 'regions', 'describe', region,
                           '--project=' + project, '--format=json(quotas)'],
                          timeout=45, capture=True)
        data = json.loads(payload)
        result = read_quota(data.get('quotas'), disk_gb=70)
    except (OSError, ValueError, UpgradeError, subprocess.TimeoutExpired) as exc:
        if fatal:
            raise UpgradeError('Verification obligatoire du quota SSD impossible : '
                               + str(exc)[:300]) from exc
        print('  ! Quota SSD non verifie : ' + str(exc)[:200])
        return None
    print(f"Quota SSD us-central1 : {result['usage']:g}/{result['limit']:g} Go utilises ; "
          f"{result['free']:g} Go disponibles ; {result['required']} Go necessaires au surge")
    if not result['sufficient']:
        message = ('SSD_TOTAL_GB insuffisant pour deux VMs pd-balanced de 70 Go en parallele. '
                   'Ne pas forcer le remplacement ni supprimer de disques attaches aux VMs actives.')
        if fatal:
            raise UpgradeError(message)
        print('  ! ' + message)
    return result


def update_file(path, data, *, backup_dir=BACKUP_DIR):
    """Keep an owner-only backup OUTSIDE Git; never overwrite existing backup."""
    if path.is_symlink() or not path.is_file():
        raise UpgradeError('terraform.tfvars non present ou lien symbolique')
    old = path.read_text(encoding='utf-8')
    if old == data:
        return None
    if backup_dir.is_symlink():
        raise UpgradeError('Repertoire de sauvegarde non autorise : lien symbolique')
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if backup_dir.is_symlink():
        raise UpgradeError('Repertoire de sauvegarde non autorise : lien symbolique')
    os.chmod(backup_dir, 0o700)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    # Exclusive creation prevents replacing a backup after fast successive runs.
    backup = None
    for index in range(100):
        candidate = backup_dir / f'terraform.tfvars.bak-{stamp}-{index:02d}'
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        backup = candidate
        break
    if backup is None:
        raise UpgradeError('Impossible de creer une sauvegarde Terraform unique')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(old)
            stream.flush()
            os.fsync(stream.fileno())
        fd, tmp = tempfile.mkstemp(prefix='.ha-vars-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except BaseException:
        # No half-written candidate is installed if atomic replacement failed.
        raise
    return backup


def terraform_plan(folder, *, overrides):
    plan = str(Path(folder) / ('preview.tfplan' if overrides else 'upgrade.tfplan'))
    cmd = ['terraform', 'plan', '-input=false', '-lock-timeout=30s', '-out=' + plan]
    if overrides:
        for key, value in SAFETY.items():
            cmd.extend(['-var', f'{key}={value.strip(chr(34))}' if key == 'machine_type' else f'{key}={value}'])
    checked(cmd, timeout=1200, capture=True)
    doc = json.loads(checked(['terraform', 'show', '-json', plan], capture=True, timeout=60))
    return plan, validate_plan(doc)


def run(*, apply):
    if os.geteuid() != 0:
        raise UpgradeError('Utiliser sudo sur le serveur principal GCP')
    if not TFVARS.is_file():
        raise UpgradeError('terraform.tfvars absent dans le vrai depot GCP')
    if not (ENV_DIR / '.terraform').is_dir():
        raise UpgradeError('Backend Terraform absent : lancer ./manage.sh init avant')
    if not shutil.which('terraform'):
        raise UpgradeError('Terraform absent')
    original = TFVARS.read_text(encoding='utf-8')
    if not re.search(r'(?m)^\s*enable_app_delivery\s*=\s*true\b', original):
        raise UpgradeError('enable_app_delivery=true requis (ne pas detruire le bucket GCS actif)')
    if not re.search(r'(?m)^\s*bootstrap_mode\s*=\s*"worker"', original):
        raise UpgradeError('bootstrap_mode="worker" requis : controle des VM deja en production')
    desired = render_tfvars(original)
    print('AUTOKOM HA : 2 vCPU / 4 GiB / 70 Go pd-balanced ; 2-5 workers ; CPU 55 %')
    print('Configuration cible :')
    for key, value in SAFETY.items():
        print('  ' + key + ' = ' + value)
    print('Aucun fichier terraform.tfvars modifie avant le controle du plan et du quota.')
    checked(['terraform', 'validate'], timeout=60)
    with tempfile.TemporaryDirectory(prefix='ak-ha-plan-', dir='/tmp') as folder:
        preview_plan, changes = terraform_plan(folder, overrides=True)
        print('Changements autorises :')
        for addr, actions in changes:
            print('  ' + addr + ' => ' + '/'.join(actions))
        replacement = any('google_compute_instance_template' in addr for addr, _ in changes)
        if replacement:
            check_ssd_quota(desired, fatal=apply)
        if not apply:
            if not changes:
                print('Etat Terraform deja conforme a la cible ; controler les VMs, le MIG et la sante reelle.')
            else:
                print('Previsualisation terminee : aucun changement applique.')
            return 0
        if not changes:
            backup = update_file(TFVARS, desired)
            if backup:
                print('Configuration locale actualisee ; sauvegarde hors depot : ' + str(backup))
            print('Etat Terraform deja conforme : aucune ressource remplacee.')
            return 0
        if not sys.stdin.isatty():
            raise UpgradeError('Confirmation interactive requise : session TTY')
        if input('Ecrire APPLIQUER-GCP pour changer le MIG : ').strip() != 'APPLIQUER-GCP':
            raise UpgradeError('Application annulee : aucun fichier modifie')
        backup = update_file(TFVARS, desired)
        if backup:
            print('Sauvegarde hors depot : ' + str(backup))
        actual_plan, actual_changes = terraform_plan(folder, overrides=False)
        if actual_changes != changes:
            raise UpgradeError('Plan reel different de la previsualisation : abandon, sauvegarde conservee')
        checked(['terraform', 'apply', '-input=false', actual_plan], timeout=3600)
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
