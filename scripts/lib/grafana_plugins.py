#!/usr/bin/env python3
"""Official live Grafana plugin catalogue, selection, installation.
No fictional or preseeded third-party plugin results. Only verified official IDs.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
from pathlib import Path
from datetime import datetime, timezone
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from cli_colors import pretty_print as print

CATALOG='https://grafana.com/api/plugins'
TYPES=('all','panel','datasource','app')
ID=re.compile(r'^[a-z0-9][a-z0-9-]{2,95}$')

class PluginsError(RuntimeError):pass

def remote(path,params=None):
    url=CATALOG+path
    if params:url+='?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={'User-Agent':'AUTOKOM-Plugin-Discovery/1.0','Accept':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=12) as r:
            if urllib.parse.urlsplit(r.url).hostname not in ('grafana.com','www.grafana.com'):
                raise PluginsError('Le catalogue a redirigé vers un hôte non reconnu')
            return json.loads(r.read(5_000_000))
    except (OSError,ValueError,urllib.error.URLError) as exc:
        raise PluginsError('Catalogue Grafana indisponible (HTTPS). Vérifier la connectivité, aucune liste inventée.') from exc

def normalize(value):
    """Accept official list response variants; fail closed when schema changes."""
    if isinstance(value,dict):
        candidates=value.get('plugins') or value.get('items') or value.get('results')
    else:candidates=value
    if isinstance(candidates,dict):candidates=list(candidates.values())
    if not isinstance(candidates,list):raise PluginsError('Format du catalogue Grafana non reconnu')
    out=[]
    for item in candidates:
        if not isinstance(item,dict):continue
        slug=str(item.get('slug') or item.get('id') or '')
        if not ID.fullmatch(slug):continue
        out.append({'id':slug,'name':str(item.get('name') or slug),
                    'type':str(item.get('type') or '').lower(),
                    'description':str(item.get('description') or item.get('shortDescription') or '')[:500],
                    'signature':str(item.get('signature') or item.get('signatureType') or ''),
                    'status':str(item.get('status') or item.get('state') or 'stable')})
    return out

def catalog(page_limit=8):
    """Retrieve a bounded set of real official plugin results, with pagination.

    Response layouts can evolve: stop when page is empty or repeats the same
    IDs, rather than fabricating entries or assuming a fixed server page size.
    """
    seen={}
    for page in range(1,page_limit+1):
        payload=remote('',{'page':page,'perpage':200})
        items=normalize(payload)
        if not items:break
        before=len(seen)
        for item in items:seen[item['id']]=item
        if len(seen)==before:break
    if not seen:raise PluginsError('Aucun plugin vérifié retourné par le catalogue')
    return list(seen.values())

def tokenize(value):
    tokens=re.findall(r'[a-z0-9]+',value.casefold())
    return {t for t in tokens if len(t)>2 and t not in {'the','for','with','plugin','grafana','pour','avec','une','des','les','sur','dans','qui'}}

def rank(items,query,kind='all',signed_only=False,limit=15):
    words=tokenize(query)
    semantic_groups=({'log','logs','journal','journaux','logging','logql'},
                     {'chart','graph','graphique','visualisation','viz','panel'},
                     {'alert','alertes','notification','notification'},
                     {'database','db','data','datasource','donnees'},
                     {'trace','traces','tracing'})
    for group in semantic_groups:
        if words & group:words.update(group)
    if not words:return []
    matches=[]
    for item in items:
        if kind!='all' and item['type']!=kind:continue
        if signed_only and item['signature'].casefold() not in ('grafana','commercial','community','signed','valid'):
            continue
        name=tokenize(item['name']); slug=tokenize(item['id'].replace('-',' ')); description=tokenize(item['description'])
        score=sum(12 for w in words if w in slug)+sum(10 for w in words if w in name)+sum(2 for w in words if w in description)
        if score:matches.append((score,item))
    return [item for _,item in sorted(matches,key=lambda p:(-p[0],p[1]['id']))[:limit]]

def renderer_intent(query):
    words=tokenize(query)
    return bool(words & {'image','images','png','jpg','jpeg','pdf','screenshot','render','renderer','export','exporter','capture'})

def detail(plugin_id):
    if not ID.fullmatch(plugin_id):raise PluginsError('Identifiant de plugin invalide')
    obj=remote('/'+plugin_id)
    if not isinstance(obj,dict):raise PluginsError('Fiche du plugin inconnue')
    actual=str(obj.get('slug') or obj.get('id') or '')
    if actual!=plugin_id:raise PluginsError('ID retourné différent : installation refusée')
    if str(obj.get('status') or obj.get('state') or 'stable').lower() in ('deprecated','withdrawn','removed'):
        raise PluginsError('Plugin déprécié / retiré : installation refusée')
    return obj

def cli_command():
    if shutil.which('grafana'):return ['grafana','cli']
    if shutil.which('grafana-cli'):return ['grafana-cli']
    raise PluginsError('CLI Grafana indisponible sur cet hôte')

def installed():
    cmd=cli_command()+['plugins','ls']
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=25)
    if result.returncode:raise PluginsError('Impossible de lire les plugins installés')
    return result.stdout


def format_installed(raw, needle=''):
    """Best-effort structured Grafana CLI display, preserving unknown lines."""
    from terminal_table import Cell, render_table
    rows=[];other=[]
    for line in raw.splitlines():
        m=re.match(r'^\s*([a-z0-9][a-z0-9-]+)\s+@\s+(\S+)',line,re.I)
        if m:
            if not needle or needle.casefold() in m.group(1).casefold():
                rows.append((Cell(m.group(1),'info'),Cell(m.group(2),'success')))
        elif line.strip():
            other.append(line)
    if rows:
        return render_table(['Plugin officiel','Version installée'],rows,row_dividers=False)
    return '\n'.join(line for line in raw.splitlines() if not needle or needle.casefold() in line.casefold()) or '  › Aucun plugin correspondant'

def install(plugin_id,apply=False,version=None):
    obj=detail(plugin_id)  # Always verify against live official catalogue.
    name=str(obj.get('name') or plugin_id)
    signature=str(obj.get('signature') or obj.get('signatureType') or '').casefold()
    if signature in ('unsigned','invalid','modified'):
        raise PluginsError('Signature du plugin non fiable : installation refusée')
    if plugin_id=='grafana-image-renderer':
        raise PluginsError('Image Renderer plugin abandonné : utiliser le service externe (≥16 Gio recommandés)')
    if version and not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}(?:[-+][a-zA-Z0-9.-]+)?',version):
        raise PluginsError('Version invalide')
    print('  ◈ Plugin officiel vérifié : '+name+' ('+plugin_id+')')
    print('  › Type : '+str(obj.get('type','non indiqué'))+' | version '+(version or 'dernière compatible à valider'))
    if not apply:
        print('  › Simulation : sudo autokom grafana plugins install '+plugin_id+' --apply')
        return 0
    if os.geteuid()!=0:raise PluginsError('Installation exige sudo')
    from autokom_security import authorize
    authorize('installer le plugin Grafana '+plugin_id)
    from menu import pick
    choice=pick('AUTOKOM · Installer ce plugin Grafana ?',
                ['Installer le plugin vérifié, puis redémarrer Grafana'],
                hint='Vérifier signature et compatibilité avant usage. Grafana sera brièvement indisponible.',
                cancel_label='Annuler sans modification')
    if choice!=1:
        print('  ! Annulé');return 1
    cmd=cli_command()+['plugins','install',plugin_id]
    if version:cmd.append(version)
    from tool_banner import Activity
    try:
        with Activity('Téléchargement du plugin '+plugin_id):
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
    except (subprocess.TimeoutExpired,OSError) as exc:
        raise PluginsError('Installation impossible : timeout ou CLI indisponible') from exc
    if result.returncode:raise PluginsError('Échec d’installation (détails: journal Grafana / CLI)')
    with Activity('Rechargement contrôlé de Grafana'):
        result=subprocess.run(['systemctl','restart','grafana-server'],capture_output=True,text=True,timeout=90)
    if result.returncode:raise PluginsError('Plugin déposé mais Grafana non redémarré ; consulter journalctl -u grafana-server')
    print('  ✓ Plugin installé. Contrôler Grafana > Administration > Plugins.')
    return 0


def backup_plugins():
    """Back up user-installed plugin directories before an upgrade or removal."""
    plugin_dir=Path(os.environ.get('GF_PATHS_PLUGINS','/var/lib/grafana/plugins'))
    if plugin_dir.is_symlink():raise PluginsError('Répertoire de plugins Grafana symbolique : sauvegarde refusée')
    backup=Path('/var/backups/autokom/grafana-plugins')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if backup.exists():raise PluginsError('Un répertoire de sauvegarde existe déjà à cette seconde : réessayer')
    backup.mkdir(parents=True,mode=0o700)
    os.chmod(backup,0o700)
    if plugin_dir.is_dir():
        shutil.copytree(plugin_dir,backup/'plugins',symlinks=True)
    return backup


def execute_mutation(command, description):
    from tool_banner import Activity
    try:
        with Activity(description):
            result=subprocess.run(command,capture_output=True,text=True,timeout=1200)
    except (subprocess.TimeoutExpired,OSError) as exc:
        raise PluginsError('Commande Grafana interrompue / délai dépassé') from exc
    if result.returncode:
        # never leak secrets or huge plugin logs; the Grafana CLI uses no auth args
        raise PluginsError('Commande Grafana échouée : '+(result.stderr or result.stdout)[-450:])
    return result


def preinstalled(plugin_id):
    """Prevent deleting Grafana plugins managed by provisioning."""
    from configparser import ConfigParser
    names=os.environ.get('GF_PLUGINS_PREINSTALL','')
    parser=ConfigParser(interpolation=None)
    try:parser.read('/etc/grafana/grafana.ini')
    except Exception as exc:raise PluginsError('Configuration preinstall Grafana illisible : retrait refusé') from exc
    if parser.has_option('plugins','preinstall'):
        names+=','+parser.get('plugins','preinstall')
    return plugin_id in {s.strip().split('@',1)[0] for s in names.split(',') if s.strip()}

def manage_plugin(action, plugin_id='', apply=False):
    if action=='remove' and (not ID.fullmatch(plugin_id)):
        raise PluginsError('Identifiant de plugin invalide')
    available=installed()
    names=available.casefold()
    present=lambda name: bool(re.search(r'(?<![a-z0-9-])'+re.escape(name.casefold())+r'(?![a-z0-9-])',names))
    if action=='remove' and preinstalled(plugin_id):
        raise PluginsError('Plugin géré par Grafana preinstall : modifier d’abord son provisioning, retrait refusé')
    if action=='remove' and not present(plugin_id):
        raise PluginsError('Plugin non visible dans la liste locale : retrait refusé')
    if action=='update' and not ID.fullmatch(plugin_id):
        raise PluginsError('Identifiant de plugin invalide')
    if action=='update':
        detail(plugin_id)  # refuse a nonexistent/retired official plugin
        if not present(plugin_id):raise PluginsError('Plugin non installé')
    print('  ◈ AUTOKOM / PLUGINS GRAFANA · '+action.upper())
    if action=='upgrade':print('  › Mise à jour globale : Grafana CLI update-all (versions compatibles à confirmer).')
    else:print('  › '+plugin_id)
    if not apply:
        print('  › Simulation ; relancer avec --apply après analyse des impacts.')
        return 0
    if os.geteuid()!=0:raise PluginsError('Opération exige sudo')
    from autokom_security import authorize
    authorize('modifier les plugins Grafana')
    from menu import pick
    yes=pick('AUTOKOM · Confirmer '+action+' ?',
             ['Sauvegarder les plugins puis appliquer'],cancel_label='Annuler sans modification',
             hint='Vérifier les plugins utilisés par les dashboards avant suppression.')
    if yes!=1:return 1
    backup=backup_plugins()
    cmd=cli_command()+['plugins']
    if action=='upgrade':cmd+=['update-all']
    elif action=='update':cmd+=['update',plugin_id]
    else:cmd+=['remove',plugin_id]
    execute_mutation(cmd,'Grafana : '+action+' '+(plugin_id or 'tous les plugins'))
    print('  ✓ Opération terminée ; sauvegarde : '+str(backup))
    print('  ! Plugins Grafana : prévoir un redémarrage contrôlé après vérification des dashboards ; aucun restart automatique.')
    return 0


def interface():
    from menu import pick, ask
    while True:
        choice=pick('AUTOKOM · PLUGINS GRAFANA',[
            'Rechercher au catalogue officiel',
            'Lister et filtrer les plugins installés',
            'Installer un plugin',
            'Mettre à jour tous les plugins installés',
            'Mettre à jour un plugin précis',
            'Supprimer un plugin (contrôle préalable)',
        ],cancel_label='Quitter le gestionnaire')
        if not choice:return 0
        if choice==1:
            q=ask('Recherche Grafana')
            if q:main(['search',q,'--select'])
        elif choice==2:
            q=ask('Filtre (vide = tous)')
            main(['list']+(['--filter',q] if q else []))
        else:
            action={3:'install',4:'upgrade',5:'update',6:'remove'}[choice]
            ident=ask('ID officiel du plugin') if action in ('install','update','remove') else ''
            target=([ident] if ident else [])
            main([action]+target)
            if pick('AUTOKOM · Exécuter ?', ['Appliquer après sauvegarde'],cancel_label='Annuler'):
                main([action]+target+['--apply'])

def parser():
    p=argparse.ArgumentParser(prog='autokom grafana plugins',description='Catalogue réel Grafana : recherche sémantique légère, filtres, installation confirmée.')
    s=p.add_subparsers(dest='action')
    q=s.add_parser('search',help='Chercher dynamiquement dans le catalogue officiel')
    q.add_argument('query',nargs='+',help='Ex. logs drilldown, image export, heatmap')
    q.add_argument('--type',choices=TYPES,default='all',help='Filtrer par catégorie')
    q.add_argument('--signed-only',action='store_true',help='Seulement les signatures connues du catalogue')
    q.add_argument('--limit',type=int,choices=range(1,41),metavar='1..40',default=12)
    q.add_argument('--select',action='store_true',help='Sélecteur clavier AUTOKOM ; affiche la commande (lecture seule)')
    listing=s.add_parser('list',help='Plugins installés localement (CLI Grafana)')
    listing.add_argument('--filter',default='',help='Filtrer par nom / ID dans la liste')
    info=s.add_parser('info',help='Fiche officielle d’un plugin')
    info.add_argument('plugin_id')
    add=s.add_parser('install',help='Installer un plugin officiellement vérifié après confirmation')
    add.add_argument('plugin_id')
    add.add_argument('--version',help='Version épinglée, si compatible')
    grp=add.add_mutually_exclusive_group()
    grp.add_argument('--apply',action='store_true',help='Installer réellement (sudo et confirmation TTY)')
    grp.add_argument('--check',action='store_true',help='Vérification seule (défaut)')
    for action,helptext in [('upgrade','Mettre à jour tous les plugins installés'),('update','Mettre à jour un plugin installé'),('remove','Retirer un plugin installé')]:
        sub=s.add_parser(action,help=helptext)
        if action in ('update','remove'):sub.add_argument('plugin_id')
        group=sub.add_mutually_exclusive_group()
        group.add_argument('--apply',action='store_true',help='Appliquer après confirmation TTY (défaut : simulation)')
        group.add_argument('--check',action='store_true',help='Afficher le plan sans changement')
    return p

def main(args=None):
    p=parser(); a=p.parse_args(args)
    if not a.action:
        if os.isatty(0):return interface()
        p.print_help();return 0
    if a.action=='list':
        print('  ◈ PLUGINS GRAFANA INSTALLÉS')
        print(format_installed(installed(),a.filter));return 0
    if a.action=='info':
        data=detail(a.plugin_id)
        for k in ('name','slug','type','description','status','signature'):
            print('  '+k+': '+str(data.get(k,'non indiqué'))[:250])
        return 0
    if a.action=='install':return install(a.plugin_id,apply=a.apply,version=a.version)
    if a.action in ('upgrade','update','remove'):
        return manage_plugin(a.action,getattr(a,'plugin_id',''),apply=a.apply)
    query=' '.join(a.query)
    if renderer_intent(query):
        print('  ! La recherche concerne possiblement l’export d’images : Grafana 13 ne prend plus en charge le plugin Image Renderer.')
        print('  › Solution officielle : service Grafana Image Renderer séparé ; RAM recommandée ≥16 Gio / 4 CPU.')
        print('  › https://grafana.com/docs/grafana/latest/setup-grafana/image-rendering/')
        print('  › AUTOKOM ne l’installe pas automatiquement sur cet EC2 de 7,6 Gio.')
    items=rank(catalog(),query,a.type,a.signed_only,a.limit)
    if not items:
        print('  ! Aucun plugin correspondant trouvé dans le catalogue officiel (aucun résultat inventé).')
        return 0
    from terminal_table import render_table
    if not a.select:
        print(render_table(['ID officiel','Nom','Type','Signature'],
           [(x['id'],x['name'],x['type'] or '—',x['signature'] or 'non indiquée') for x in items]))
    else:print(f'  ✓ {len(items)} plugins officiels trouvés · ←/→ pour changer de page')
    if a.select:
        from menu import pick_paginated, pick
        selection=pick_paginated('AUTOKOM · Choisir un plugin Grafana',[(x['name']+' / '+x['id'])[:68] for x in items])
        if selection:
            plugin_id=items[selection-1]['id']
            # Selection directly launches the guarded official installer.
            if os.geteuid()!=0:
                print('  › Plugin sélectionné : passage à sudo avec confirmation finale')
                return subprocess.call(['sudo','autokom','tools','grafana','plugins','install',plugin_id,'--apply'])
            return install(plugin_id,apply=True)
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except PluginsError as exc:
        print('  ✗ '+str(exc));raise SystemExit(1)
