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

def parser():
    p=argparse.ArgumentParser(prog='autokom grafana plugins',description='Catalogue réel Grafana : recherche sémantique légère, filtres, installation confirmée.')
    s=p.add_subparsers(dest='action')
    q=s.add_parser('search',help='Chercher dynamiquement dans le catalogue officiel')
    q.add_argument('query',nargs='+',help='Ex. logs drilldown, image export, heatmap')
    q.add_argument('--type',choices=TYPES,default='all',help='Filtrer par catégorie')
    q.add_argument('--signed-only',action='store_true',help='Seulement les signatures connues du catalogue')
    q.add_argument('--limit',type=int,choices=range(1,41),metavar='1..40',default=12)
    q.add_argument('--select',action='store_true',help='Sélecteur clavier AUTOKOM ; affiche la commande (lecture seule)')
    s.add_parser('list',help='Plugins installés localement (CLI Grafana)')
    info=s.add_parser('info',help='Fiche officielle d’un plugin')
    info.add_argument('plugin_id')
    add=s.add_parser('install',help='Installer un plugin officiellement vérifié après confirmation')
    add.add_argument('plugin_id')
    add.add_argument('--version',help='Version épinglée, si compatible')
    grp=add.add_mutually_exclusive_group()
    grp.add_argument('--apply',action='store_true',help='Installer réellement (sudo et confirmation TTY)')
    grp.add_argument('--check',action='store_true',help='Vérification seule (défaut)')
    return p

def main(args=None):
    p=parser(); a=p.parse_args(args)
    if not a.action:p.print_help();return 0
    if a.action=='list':
        print('  ◈ PLUGINS GRAFANA INSTALLÉS');print(installed());return 0
    if a.action=='info':
        data=detail(a.plugin_id)
        for k in ('name','slug','type','description','status','signature'):
            print('  '+k+': '+str(data.get(k,'non indiqué'))[:250])
        return 0
    if a.action=='install':return install(a.plugin_id,apply=a.apply,version=a.version)
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
    print(render_table(['ID officiel','Nom','Type','Signature'],
       [(x['id'],x['name'],x['type'] or '—',x['signature'] or 'non indiquée') for x in items]))
    if a.select:
        from menu import pick
        if len(items)>8:items=items[:8]
        selection=pick('AUTOKOM · Choisir un plugin (aucune installation)',
            [(x['name']+' / '+x['id'])[:68] for x in items],cancel_label='Annuler')
        if selection:
            print('  › Vérifier puis installer : sudo autokom grafana plugins install '+items[selection-1]['id']+' --apply')
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except PluginsError as exc:
        print('  ✗ '+str(exc));raise SystemExit(1)
