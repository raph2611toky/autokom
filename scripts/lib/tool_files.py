#!/usr/bin/env python3
"""Safe, allowlisted file inspection and controlled systemd environment edits.

Aliases are derived from declared tool files and conservative on-host discovery.
No shell command strings, arbitrary paths, secret values on argv or auto reloads.
"""
from __future__ import annotations
from pathlib import Path
import getpass
import os
import re
import shutil
import subprocess
import tempfile
import time

CONFIG_ROOTS=(Path('/etc/nginx/'),Path('/etc/prometheus/'),Path('/etc/grafana/'),
              Path('/etc/systemd/system/'),Path('/etc/default/'),Path('/etc/autokom/'),
              Path('/etc/jenkins/'),Path('/var/lib/jenkins/'))
PROTECTED=('secret','passwd','password','token','credentials','privkey','private','htpasswd')
MANAGED_ENV_ROOT=Path('/etc/autokom/env')
ENV_TOOLS=frozenset(('prometheus','grafana','node-exporter','alertmanager'))
ENV_KEY=re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')


def aliases(name):
    from tools_registry import get,discovered_service_files
    t=get(name)
    entries={}
    candidates=list(t.files)+discovered_service_files(name)
    alias_table={
      'prometheus': {'prometheus.yml':'prometheus-configuration',
                     'autokom-alerts.yml':'alert-rules',
                     'alertmanager.yml':'alertmanager-configuration'},
      'grafana': {'grafana.ini':'grafana-configuration',
                  'autokom-prometheus.yaml':'prometheus-datasource'},
      'nginx': {'nginx.conf':'nginx-configuration'},
      'alertmanager': {'alertmanager.yml':'alertmanager-configuration',
                       'autokom-alerts.yml':'alert-rules'},
    }
    for path,label in candidates:
        candidate=Path(path)
        if not candidate.exists() or not candidate.is_file():
            continue
        short=alias_table.get(name,{}).get(candidate.name)
        if not short and candidate.name.startswith('autokom-prometheus-'):
            short='nginx-prometheus'
        if not short and candidate.name.startswith('autokom-grafana-'):
            short='nginx-grafana'
        if not short and candidate.name.startswith('jenkins.'):
            short='nginx-jenkins'
        if not short and candidate.suffix=='.service':
            short='service'
        if not short and candidate.name.startswith('90-autokom'):
            short='service-override'
        if not short:
            short=re.sub(r'[^a-z0-9]+','-',candidate.stem.lower()).strip('-')
        if not short:
            continue
        if short in entries and entries[short][0] != candidate:
            # Explicit disambiguation; don't pick an arbitrary duplicate.
            short=f'{short}-{len(entries)+1}'
        entries[short]=(candidate,label)
    if name in ('prometheus','grafana'):
        # Stable aliases to the per-service Nginx virtual host.
        for key,(candidate,desc) in list(entries.items()):
            if candidate.name.startswith(f'autokom-{name}-'):
                entries[f'nginx-{name}']=(candidate,desc)
    if name in ENV_TOOLS:
        managed=MANAGED_ENV_ROOT/f'{name}.env'
        if managed.exists(): entries['autokom-environment']=(managed,'Variables AUTOKOM (secrets possibles)')
    return dict(sorted(entries.items()))


def resolve_alias(name,key):
    from tools_registry import ToolError
    available=aliases(name)
    if not key or key not in available:
        raise ToolError('Alias de fichier introuvable : utiliser autokom tools '+name+' files')
    candidate,label=available[key]
    resolved=candidate.resolve(strict=True)
    if not resolved.is_file() or resolved.stat().st_size>2_000_000:
        raise ToolError('Seuls les fichiers de configuration de 2 Mio maximum sont lisibles')
    return candidate,resolved,label


def files(name):
    from terminal_table import table,Cell
    data=aliases(name)
    print('  ◈ AUTOKOM / '+name.upper()+' · FICHIERS ACCESSIBLES')
    if not data:
        print('  − Aucun fichier inspectable détecté')
        return
    table(['Alias','Chemin absolu','Fonction'],[
        (Cell(key,'info'),str(path),desc)
        for key,(path,desc) in data.items()],row_dividers=False)
    print('  › autokom tools '+name+' cat ALIAS')
    print('  › sudo autokom tools '+name+' nano-end ALIAS')


def sensitive(p):
    return any(word in str(p).lower() for word in PROTECTED) or p.stat().st_mode & 0o077 == 0


def file_operation(name,action,key):
    from tools_registry import ToolError,require_root,confirm_sensitive,_tty_challenge
    if action=='files':
        return files(name)
    original,resolved,label=resolve_alias(name,key)
    # No arbitrary path resolution by the user; only registered aliases.
    if action in ('cat','head','tail'):
        if sensitive(resolved) and (os.geteuid()!=0 or not confirm_sensitive()):
            raise ToolError('Fichier sensible : sudo et confirmation nécessaires')
        lines=resolved.read_text(encoding='utf-8',errors='replace').splitlines()
        if action=='head': lines=lines[:30]
        elif action=='tail':lines=lines[-50:]
        for line in lines: print(line)
        return
    if action not in ('nano','nano-end'):
        raise ToolError('Action fichier inconnue')
    require_root()
    if (not any(resolved.is_relative_to(root) for root in CONFIG_ROOTS) or
            resolved.is_relative_to(Path('/var/lib/jenkins')) or not original.exists()):
        raise ToolError('Édition interdite hors des répertoires de configuration autorisés')
    if sensitive(resolved) and not confirm_sensitive():
        raise ToolError('Édition sensible annulée')
    if any(part in ('secrets','plugins','jobs','certificates','live') for part in resolved.parts):
        raise ToolError('Édition directe des données d’application et des secrets refusée')
    if not _tty_challenge(f'Modifier {resolved} (sauvegarde et contrôle syntaxique)',name.upper()):
        raise ToolError('Édition annulée')
    backup=resolved.with_name(resolved.name+f'.autokom.bak.{int(time.time())}')
    if backup.exists():
        raise ToolError('Sauvegarde déjà existante ; recommencer')
    shutil.copy2(resolved,backup,follow_symlinks=False)
    os.chmod(backup,0o600)
    editor=['nano']
    if action=='nano-end':
        editor.append('+'+str(len(resolved.read_text(encoding='utf-8',errors='replace').splitlines())+1))
    editor.append(str(resolved))
    try:
        status=subprocess.run(editor,check=False).returncode
    except OSError as exc:
        raise ToolError('Nano indisponible') from exc
    if status:
        raise ToolError('Nano a été interrompu, sauvegarde disponible : '+str(backup))
    validate=[]
    if resolved.is_relative_to(Path('/etc/nginx/')):
        validate=['nginx','-t']
    elif resolved.name in ('prometheus.yml','autokom-alerts.yml'):
        validate=['promtool','check','rules' if resolved.name=='autokom-alerts.yml' else 'config',str(resolved)]
    elif resolved.name=='alertmanager.yml':
        validate=['amtool','check-config',str(resolved)]
    if validate:
        proc=subprocess.run(validate,capture_output=True,text=True,timeout=30,check=False)
        if proc.returncode:
            shutil.copy2(backup,resolved,follow_symlinks=False)
            raise ToolError('Configuration invalide, sauvegarde restaurée. '+
                            'Relancer la validation et consulter les journaux ; aucun reload exécuté.')
    print('  ✓ Configuration sauvegardée : '+str(resolved))
    print('  ✓ Copie de sécurité : '+str(backup))
    print('  ! Aucun service redémarré ni rechargé automatiquement')


def _env_path(name):
    from tools_registry import ToolError,get
    t=get(name)
    if name not in ENV_TOOLS or not t.unit:
        raise ToolError('Variables modifiables uniquement pour les services AUTOKOM approuvés : '+', '.join(sorted(ENV_TOOLS)))
    return MANAGED_ENV_ROOT/(name+'.env'),Path('/etc/systemd/system')/(t.unit+'.service.d')/'99-autokom-env.conf'


def managed_env(name):
    path,_=_env_path(name)
    if not path.exists():return {}
    if path.is_symlink() or path.stat().st_size>131072:
        from tools_registry import ToolError
        raise ToolError('Fichier d’environnement non conforme')
    pairs={}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'):continue
        if '=' not in line: raise ValueError('Ligne d’environnement non prise en charge')
        key,raw=line.split('=',1)
        if not ENV_KEY.fullmatch(key):raise ValueError('Clé d’environnement invalide')
        import json
        pairs[key]=json.loads(raw)
        if not isinstance(pairs[key],str):raise ValueError('Valeur environnement non textuelle')
    return pairs


def env_manage(name,action,key):
    from tools_registry import ToolError,require_root,_tty_challenge
    from terminal_table import table,Cell
    path,dropin=_env_path(name)
    if action=='list':
        print('  ◈ AUTOKOM / VARIABLES GÉRÉES '+name.upper())
        table(['Nom','Valeur'],[(Cell(k,'info'),Cell('masquée','muted')) for k in managed_env(name)])
        return
    if action not in ('set','delete') or not ENV_KEY.fullmatch(key):
        raise ToolError('Usage: autokom tools '+name+' env set|delete NOM_VARIABLE')
    require_root()
    if path.parent.is_symlink() or dropin.parent.is_symlink() or dropin.is_symlink():
        raise ToolError('Chemin systemd symbolique non autorisé')
    old=managed_env(name)
    if action=='set':
        try: value=getpass.getpass('  Nouvelle valeur (masquée) : ')
        except (EOFError,OSError) as exc: raise ToolError('TTY requis pour saisir la valeur') from exc
        if not value or '\x00' in value or '\n' in value or '\r' in value:
            raise ToolError('Valeur vide ou multiligne interdite')
        old[key]=value
    else:
        if key not in old:
            raise ToolError('Variable absente du fichier AUTOKOM (variables tierces non modifiables)')
        del old[key]
    if not _tty_challenge(f'{action} {key} pour {name} — effet au prochain redémarrage',name.upper()):
        raise ToolError('Opération annulée')
    if dropin.exists() and 'AUTOKOM MANAGED' not in dropin.read_text(encoding='utf-8'):
        raise ToolError('Override systemd existant non géré par AUTOKOM')
    import json
    data=''.join(k+'='+json.dumps(v,ensure_ascii=False)+'\n' for k,v in sorted(old.items()))
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    dropin.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        backup=path.with_suffix('.env.autokom.bak')
        if not backup.exists():
            shutil.copy2(path,backup,follow_symlinks=False)
            os.chmod(backup,0o600)
    for target,content,mode in ((path,data,0o600),(dropin,
            '# AUTOKOM MANAGED — environment (no secret in unit)\n[Service]\n'
            f'EnvironmentFile=-{path}\n',0o644)):
        if target.is_symlink():raise ToolError('Lien symbolique refusé')
        with tempfile.NamedTemporaryFile('w',dir=str(target.parent),delete=False,
                                         encoding='utf-8',prefix='.autokom-') as fh:
            tmp=Path(fh.name)
            os.fchmod(fh.fileno(),mode)
            fh.write(content)
        os.replace(tmp,target)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    print(f'  ✓ Variable {key} {"définie" if action=="set" else "supprimée"} (valeur non affichée)')
    print('  ! Aucun redémarrage automatique : appliquer après contrôle du service')


def dispatch_tool_files(name,action,target='',extra=''):
    from tools_registry import ToolError
    if action=='auth':
        if name != 'prometheus' or target != 'authelia':
            raise ToolError('Usage: sudo autokom tools prometheus auth authelia')
        from auth_portal import enable_authelia
        return enable_authelia()
    if action=='env':
        if target=='list':return env_manage(name,'list','')
        if target in ('set','delete'):
            return env_manage(name,target,extra)
        raise ToolError('Usage: autokom tools '+name+' env list|set KEY|delete KEY')
    return file_operation(name,action,target)
