#!/usr/bin/env python3
"""AUTOKOM Jenkins plugin manager: official Update Center + local authorized Jenkins.

Never removes unrelated data, never changes plugins in --check, never puts tokens
in argv. A successful CLI download is NOT proof of activation; restart is explicit.
"""
from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from cli_colors import pretty_print as print
from menu import pick, ask, pick_paginated
from tool_banner import section, Activity
from terminal_table import Cell, render_table

ID = re.compile(r'^[a-z][a-z0-9-]{0,90}$')
CENTER = 'https://updates.jenkins.io/update-center.actual.json'
HOME = Path('/var/lib/jenkins')

class PluginError(RuntimeError): pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):return None

def urlopen(url, *, headers=None, data=None, timeout=15, method=None):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    return opener.open(urllib.request.Request(url,headers=headers or {},data=data,method=method),timeout=timeout)

def local_url():
    from observability_config import load
    return 'http://127.0.0.1:'+str(load().jenkins_port)+'/'

def auth_header(auth_file):
    if not auth_file:raise PluginError('Jenkins requiert un jeton API : --auth-file /chemin/root-0600 (jamais de secret dans la CLI).')
    from jenkins_postinstall import secure_file
    path=secure_file(auth_file)
    raw=path.read_text(encoding='utf-8').strip()
    if not raw or ':' not in raw:raise PluginError('Fichier API Jenkins : attendu identifiant:jeton')
    return 'Basic '+base64.b64encode(raw.encode('utf-8')).decode('ascii')

def api(path, auth_file):
    try:
        with urlopen(local_url()+path,headers={'Authorization':auth_header(auth_file), 'Accept':'application/json'},timeout=12) as r:
            if r.status != 200:raise PluginError('API Jenkins : réponse non conforme')
            return json.loads(r.read(12_000_000))
    except urllib.error.HTTPError as e:raise PluginError(f'API Jenkins locale HTTP {e.code} : contrôler jeton/permissions') from None
    except (urllib.error.URLError,OSError,ValueError) as e:raise PluginError('API Jenkins locale indisponible ou réponse invalide') from e

def controller_version():
    try:
        with urlopen(local_url()+'login',timeout=4) as r:
            return r.headers.get('X-Jenkins','').strip()
    except urllib.error.HTTPError as e:return e.headers.get('X-Jenkins','').strip()
    except (OSError,urllib.error.URLError):return ''

def catalogue():
    """Official update metadata; include controller version for server-side filtering."""
    version=controller_version()
    # Query official version-filtered metadata, never experimental updates.
    url=CENTER+('?' +urllib.parse.urlencode({'version':version}) if version else '')
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'AUTOKOM-Jenkins-Plugins/1','Accept':'application/json'})
        with urllib.request.urlopen(req,timeout=35) as response:
            payload=response.read(35_000_000)
        obj=json.loads(payload)
    except (urllib.error.URLError, OSError,ValueError) as exc:
        raise PluginError('Update Center Jenkins indisponible ; installation refusée sans catalogue réel') from exc
    items=obj.get('plugins') if isinstance(obj,dict) else None
    if not isinstance(items,dict) or not items:raise PluginError('Format Update Center non reconnu')
    return {key:value for key,value in items.items() if ID.fullmatch(key) and isinstance(value,dict)}

def search(items,query,limit=15,category=""):
    words=[w for w in re.findall(r'[a-z0-9]+',query.casefold()) if len(w)>2]
    synonyms={'securite':['security','credentials','auth'], 'pipeline':['workflow','pipeline','ci'],
              'docker':['container','docker'], 'git':['scm','git'],
              'test':['testing','test'], 'alertes':['notification','alert'],
              'jenkins':['automation'], 'kubernetes':['kubernetes','k8s']}
    extended=set(words)
    for w in words:extended.update(synonyms.get(w,[]))
    matches=[]
    for plugin_id,item in items.items():
        if category:
            tags=item.get('labels') or item.get('categories') or []
            if isinstance(tags,str):tags=[tags]
            if not isinstance(tags,list) or category.casefold() not in {str(t).casefold() for t in tags}:
                continue
        name=str(item.get('name') or plugin_id)
        desc=str(item.get('excerpt') or item.get('description') or '')
        haystack=(plugin_id+' '+name).casefold()
        score=sum(15 for w in extended if w in haystack)+sum(2 for w in extended if w in desc.casefold())
        if score:matches.append((score,plugin_id,item))
    return sorted(matches,key=lambda v:(-v[0],v[1]))[:limit]

def _local_plugins():
    """Offline list from plugin archives; no claims about API updates or active state."""
    result=[]
    if not (HOME/'plugins').exists():return result
    for path in sorted((HOME/'plugins').glob('*')):
        if path.suffix not in ('.jpi','.hpi') or not ID.fullmatch(path.stem):continue
        version='inconnue'
        try:
            with zipfile.ZipFile(path) as z:
                manifest=z.read('META-INF/MANIFEST.MF').decode('utf-8','replace')
            m=re.search(r'^Plugin-Version:\s*(.+)',manifest,re.M)
            if m:version=m.group(1).strip()
        except (OSError,ValueError,KeyError,zipfile.BadZipFile):pass
        result.append({'shortName':path.stem,'version':version,'active':not (path.parent/(path.name+'.disabled')).exists(),
                       'hasUpdate':None,'dependencies':None,'source':'disque'})
    return result

def installed(auth_file=None, *, strict=False):
    if auth_file:
        obj=api('pluginManager/api/json?depth=2&tree=plugins[shortName,longName,version,active,enabled,hasUpdate,dependencies[shortName,optional]]',auth_file)
        plugins=obj.get('plugins') if isinstance(obj,dict) else None
        if not isinstance(plugins,list):raise PluginError('Liste Jenkins installée non conforme')
        return [dict(p,source='API') for p in plugins if isinstance(p,dict) and ID.fullmatch(str(p.get('shortName') or ''))]
    if strict:raise PluginError('État Jenkins authentifié nécessaire (--auth-file).')
    return _local_plugins()

def show_installed(items, filter_text='', only_updates=False):
    values=[x for x in items if (not only_updates or x.get('hasUpdate') is True) and
            (not filter_text or filter_text.casefold() in (x['shortName']+' '+str(x.get('longName') or '')).casefold())]
    print('  ◈ AUTOKOM / PLUGINS JENKINS INSTALLÉS')
    if not values:print('  › Aucun résultat dans cette sélection.');return
    print(render_table(['Plugin','Version installée','État','Mise à jour'],[
        (Cell(x['shortName'],'info'),x.get('version','?'),Cell('actif' if x.get('active') else 'inactif','success' if x.get('active') else 'warning'),
         Cell('disponible' if x.get('hasUpdate') is True else ('non vérifiée' if x.get('hasUpdate') is None else 'aucune'),'warning' if x.get('hasUpdate') is True else 'muted'))
        for x in values[:120]]))
    if len(values)>120:print(f'  › {len(values)-120} plugins supplémentaires : filtrer avec --filter')

def require_apply(args, title, summary):
    print('  › '+summary)
    if not args.apply:
        print('  › Simulation uniquement : ajouter --apply pour modifier les plugins.');return False
    if os.geteuid()!=0:raise PluginError('Opération exige sudo')
    answer=pick(title,['Confirmer les modifications annoncées'],cancel_label='Annuler sans modification',
                hint='Sauvegarde préalable ; un redémarrage Jenkins peut être nécessaire.')
    return answer == 1

def ensure_admin(auth_file):
    if not auth_file:raise PluginError('Opération refusée sans --auth-file root:0600')
    from jenkins_postinstall import verify_admin, ensure_cli_jar
    # Reuses existing security verification; never disables Jenkins auth.
    ensure_cli_jar(True)
    verify_admin(auth_file)

def install_ids(ids, auth_file):
    from jenkins_postinstall import cli
    # Jenkins update center only; no arbitrary URLs or user-supplied archives.
    with Activity('Installation Jenkins Update Center ('+str(len(ids))+' plugin(s))'):
        return cli(auth_file,['install-plugin',*ids],timeout=1200)

def backup_plugin_archives(ids):
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    root=Path('/var/backups/autokom/jenkins-plugins')/stamp
    archives=[]
    for plugin_id in ids:
        for suffix in ('.jpi','.hpi'):
            file=HOME/'plugins'/(plugin_id+suffix)
            if file.is_file() and not file.is_symlink():archives.append(file)
    root.mkdir(parents=True,exist_ok=False,mode=0o700)
    os.chmod(root,0o700)
    for file in archives:
        with open(file,'rb') as reader,open(root/file.name,'xb') as writer:
            shutil.copyfileobj(reader,writer)
        os.chmod(root/file.name,0o600)
    return root

def reverse_dependencies(items,target):
    """Never guess dependency safety when metadata is incomplete."""
    dependent=[]
    for p in items:
        if p.get('shortName')==target:continue
        dependencies=p.get('dependencies')
        if not isinstance(dependencies,list):raise PluginError('Dépendances Jenkins non vérifiables : suppression refusée')
        for dep in dependencies:
            if isinstance(dep,dict) and dep.get('shortName')==target and not dep.get('optional',False):
                dependent.append(p.get('shortName','?'))
    return dependent

def remove_archive(plugin_id):
    paths=[HOME/'plugins'/(plugin_id+suffix) for suffix in ('.jpi','.hpi')]
    found=False
    for path in paths:
        if path.is_symlink():raise PluginError('Archive du plugin est un lien symbolique : suppression refusée')
        if path.is_file():path.unlink();found=True
    if not found:raise PluginError('Archive non trouvée ; aucun fichier de configuration supprimé')

def _jenkins_credential(args):
    from autokom_security import JENKINS
    if args.auth_file:return args.auth_file
    # A stored credential is read only for root-managed actions; never copied to argv.
    return str(JENKINS) if os.geteuid()==0 and JENKINS.is_file() else None


def _execute_selection(plugin_id, args):
    """Selection starts the real installation workflow, not a printed recipe.

    Actual modification still requires sudo, the AUTOKOM passphrase, backend
    API rights and the final confirmation inside require_apply().
    """
    if os.geteuid()!=0:
        print('  › Plugin sélectionné : demande de privilèges sudo pour continuer')
        cmd=['sudo','autokom','tools','jenkins','plugins','install',plugin_id,'--apply']
        if args.auth_file:cmd.extend(['--auth-file',args.auth_file])
        return subprocess.call(cmd)
    return run(argparse.Namespace(action='install',plugin_id=plugin_id,auth_file=_jenkins_credential(args),apply=True))


def run(args):
    if args.action=='list':
        plugins=installed(_jenkins_credential(args))
        if not getattr(args,'select',False):
            show_installed(plugins,args.filter,args.updates)
        if getattr(args,'select',False):
            relevant=[p for p in plugins if not args.filter or args.filter.casefold() in p['shortName'].casefold()]
            if relevant:
                print(f'  ◈ Plugins installés : {len(relevant)} correspondances · 9 résultats/page')
                selected=pick_paginated('AUTOKOM · Plugins Jenkins installés', [p['shortName']+' · '+p.get('version','?') for p in relevant])
                if selected:print('  › Plugin : '+relevant[selected-1]['shortName']+' · '+relevant[selected-1].get('version','?'))
        return 0
    if args.action in ('search','info'):
        with Activity('Catalogue officiel Jenkins'):
            items=catalogue()
        if args.action=='info':
            if not ID.fullmatch(args.plugin_id) or args.plugin_id not in items:raise PluginError('Plugin inconnu du catalogue officiel')
            p=items[args.plugin_id]
            for k in ('name','version','requiredCore','excerpt','dependencies'):
                print(f'  {k}: {str(p.get(k,"non indiqué"))[:350]}')
            return 0
        q=' '.join(args.query)
        hits=search(items,q,args.limit,getattr(args,'category',''))
        if not hits:print('  › Aucun résultat dans le catalogue officiel');return 0
        if not args.select:
            print(render_table(['ID officiel','Nom','Version','Jenkins minimum'],[(i,str(p.get('name',i)),str(p.get('version','?')),str(p.get('requiredCore','?'))) for _,i,p in hits]))
        else:print(f'  ✓ {len(hits)} plugins officiels trouvés · ←/→ pour changer de page')
        if args.select:
            selected=pick_paginated('AUTOKOM · Plugins Jenkins',[(x[1]+' · '+str(x[2].get('name','')))[:68] for x in hits])
            if selected:return _execute_selection(hits[selected-1][1],args)
        return 0
    if args.action not in ('install','upgrade','remove'):
        raise PluginError('Action inconnue')
    auth=_jenkins_credential(args)
    if args.action=='install':
        if not ID.fullmatch(args.plugin_id):raise PluginError('Identifiant Jenkins invalide')
        with Activity('Vérification du catalogue officiel Jenkins'): items=catalogue()
        if args.plugin_id not in items:raise PluginError('Plugin absent du catalogue officiel')
        already={plugin['shortName'] for plugin in _local_plugins()}
        if args.plugin_id in already:
            print('  ✓ Plugin déjà installé : '+args.plugin_id)
            print('  › Utiliser « upgrade » pour obtenir les mises à jour compatibles.')
            return 0
        p=items[args.plugin_id]
        print('  ◈ '+args.plugin_id+' · version catalogue '+str(p.get('version','?')))
        if not require_apply(args,'AUTOKOM · Installer '+args.plugin_id+' ?', 'Les dépendances seront résolues par Jenkins.'):
            return 0
        from autokom_security import authorize
        authorize('installer le plugin Jenkins '+args.plugin_id)
        ensure_admin(auth)
        with Activity('Sauvegarde plugin Jenkins'):
            backup=backup_plugin_archives([args.plugin_id])
        install_ids([args.plugin_id],auth)
        print('  ✓ Installation demandée ; sauvegarde : '+str(backup))
        print('  ! Redémarrage Jenkins non automatique : planifier après vérification des builds.')
        return 0
    if args.action=='upgrade':
        plugins=installed(auth,strict=True)
        updates=[p['shortName'] for p in plugins if p.get('hasUpdate') is True]
        if not updates:
            print('  ✓ Aucune mise à jour indiquée par le gestionnaire Jenkins.');return 0
        print(render_table(['Plugins à mettre à jour'],[(p,) for p in updates]))
        if not require_apply(args,'AUTOKOM · Mettre à jour '+str(len(updates))+' plugins ?', 'Mises à jour selon Update Center configuré dans Jenkins.'):
            return 0
        from autokom_security import authorize
        authorize('mettre à jour les plugins Jenkins')
        ensure_admin(auth)
        with Activity('Sauvegarde des plugins Jenkins'):
            backup=backup_plugin_archives(updates)
        install_ids(updates,auth)
        print('  ✓ Commande Jenkins terminée ; sauvegarde '+str(backup))
        print('  ! Vérifier les nouvelles versions après redémarrage contrôlé ; aucun restart lancé.')
        return 0
    if args.action=='remove':
        if not ID.fullmatch(args.plugin_id):raise PluginError('Identifiant Jenkins invalide')
        plugins=installed(auth,strict=True)
        found=next((p for p in plugins if p.get('shortName')==args.plugin_id),None)
        if not found:raise PluginError('Plugin non installé')
        dependents=reverse_dependencies(plugins,args.plugin_id)
        if dependents:raise PluginError('Retrait interdit : utilisé par '+', '.join(dependents[:15]))
        from jenkins_postinstall import plugin_ids
        if args.plugin_id in {x.split(':')[0] for x in plugin_ids('devsecops')}:
            raise PluginError('Plugin géré par le profil DevSecOps : retrait refusé ; modifier explicitement le profil auparavant.')
        if not require_apply(args,'AUTOKOM · Retirer '+args.plugin_id+' ?',
                             'Seule archive .jpi/.hpi sera retirée ; configurations conservées.'):
            return 0
        from autokom_security import authorize
        authorize('supprimer le plugin Jenkins '+args.plugin_id)
        ensure_admin(auth)
        with Activity('Sauvegarde avant suppression'):
            backup=backup_plugin_archives([args.plugin_id])
        if not any(backup.iterdir()):raise PluginError('Sauvegarde vide : aucune suppression effectuée')
        remove_archive(args.plugin_id)
        print('  ✓ Archive retirée ; sauvegarde : '+str(backup))
        print('  ! Jenkins doit être redémarré après vérification des builds ; configurations préservées.')
        return 0

def interactive(args):
    while True:
        choice=pick('AUTOKOM · PLUGINS JENKINS',[
            'Rechercher dans le catalogue officiel',
            'Lister et filtrer les plugins installés',
            'Installer un plugin',
            'Mettre à jour les plugins disponibles',
            'Supprimer un plugin (dépendances vérifiées)',
        ],cancel_label='Quitter le gestionnaire',hint='↑/↓ · Entrée · chiffres · 0/q pour quitter')
        if not choice:return 0
        try:
            if choice==1:
                q=ask('Recherche Jenkins')
                if q:run(argparse.Namespace(action='search',query=[q],limit=36,category='',select=True,auth_file=args.auth_file))
            elif choice==2:
                q=ask('Filtre (vide = tous)')
                run(argparse.Namespace(action='list',filter=q,updates=False,select=True,auth_file=args.auth_file))
            else:
                name=ask('ID officiel du plugin') if choice in (3,5) else None
                a=argparse.Namespace(action={3:'install',4:'upgrade',5:'remove'}[choice],plugin_id=name,auth_file=args.auth_file,apply=False)
                a.apply=True
                run(a)
        except (PluginError,OSError,ValueError,RuntimeError) as exc:
            print('  ✗ '+str(exc))
            print('  › Retour au menu : aucune opération supplémentaire effectuée.')

def parser():
    p=argparse.ArgumentParser(prog='autokom tools jenkins plugins',description='Gestion officielle, interactive et contrôlée des plugins Jenkins.',formatter_class=argparse.RawDescriptionHelpFormatter,
       epilog='''Exemples :
  autokom tools jenkins plugins
  autokom tools jenkins plugins search "kubernetes security" --select
  autokom tools jenkins plugins list --filter git
  sudo autokom tools jenkins plugins list --auth-file /root/jenkins-api.auth --updates
  sudo autokom tools jenkins plugins install git --auth-file /root/jenkins-api.auth --apply
  sudo autokom tools jenkins plugins upgrade --auth-file /root/jenkins-api.auth --apply
  sudo autokom tools jenkins plugins remove EXEMPLE --auth-file /root/jenkins-api.auth --apply''')
    p.add_argument('--auth-file',default=os.environ.get('AUTOKOM_JENKINS_AUTH_FILE'),help='Fichier root:0600 identifiant:API-token, jamais affiché')
    sub=p.add_subparsers(dest='action')
    s=sub.add_parser('search',help='Rechercher les vrais plugins du Update Center')
    s.add_argument('query',nargs='+');s.add_argument('--limit',type=int,default=36);s.add_argument('--select',action='store_true')
    s.add_argument('--category',default='',help='Filtrer par étiquette officielle du catalogue, si renseignée')
    s=sub.add_parser('list',help='Plugins installés (API si jeton, sinon archives locales)')
    s.add_argument('--filter',default='');s.add_argument('--select',action='store_true',help='Pagination interactive');s.add_argument('--updates',action='store_true',help='Nécessite accès API pour détecter les mises à jour')
    s=sub.add_parser('info',help='Détails du plugin officiel');s.add_argument('plugin_id')
    s=sub.add_parser('install',help='Installer depuis Update Center');s.add_argument('plugin_id')
    s=sub.add_parser('upgrade',help='Mettre à jour tous les plugins signalés par Jenkins')
    s=sub.add_parser('remove',help='Retirer archive sans détruire configuration, sous garde-fous');s.add_argument('plugin_id')
    for name in ('install','upgrade','remove'):
        sub.choices[name].add_argument('--apply',action='store_true',help='Appliquer après confirmation TTY (défaut simulation)')
    # Local auth flag also accepted after the action, not only before.
    for child in sub.choices.values():child.add_argument('--auth-file',default=argparse.SUPPRESS,help='Fichier root:0600 identifiant:API-token')
    return p

def main(argv=None):
    p=parser();a=p.parse_args(argv)
    if not a.action:
        if not sys.stdin.isatty():p.print_help();return 0
        return interactive(a)
    if a.action=='search' and not (1<=a.limit<=90):raise PluginError('Limite attendue : 1..40')
    if a.action=='list' and a.updates and not _jenkins_credential(a):
        raise PluginError('Seule API Jenkins permet de confirmer les mises à jour : --auth-file requis')
    return run(a)

if __name__=='__main__':
    try:sys.exit(main())
    except (PluginError,OSError,ValueError,RuntimeError) as e:
        print('  ✗ '+str(e),file=sys.stderr);sys.exit(1)
