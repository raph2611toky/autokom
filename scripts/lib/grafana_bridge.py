#!/usr/bin/env python3
"""Safely inspect effective Grafana configuration and test its Loki datasource.
No dependency on Grafana credentials for metadata-only SQLite check; API queries
require an explicitly supplied service-account token. Never print token/log body.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.error
import urllib.request

BASE='http://127.0.0.1:3000'
LOKI='http://127.0.0.1:3100'
DB=Path('/var/lib/grafana/grafana.db')
DASH=Path('/var/lib/grafana/dashboards/autokom')
DS=Path('/etc/grafana/provisioning/datasources/autokom-loki.yaml')
PROVIDER=Path('/etc/grafana/provisioning/dashboards/autokom.yaml')

def local_state(path=DB):
    """Read metadata only. No secrets or complete dashboard exported."""
    if not path.is_file(): return {'available':False, 'reason':'SQLite Grafana indisponible (autre stockage possible)'}
    try:
        con=sqlite3.connect('file:'+urllib.parse.quote(str(path),safe='/')+'?mode=ro',uri=True,timeout=2)
        with con:
            names={row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'data_source' not in names or 'dashboard' not in names:
                return {'available':False,'reason':'Schéma SQLite inattendu'}
            columns={r[1] for r in con.execute('PRAGMA table_info(data_source)')}
            ds=con.execute('SELECT uid, type, url FROM data_source WHERE uid=?',('autokom-loki',)).fetchone() if {'uid','type','url'}<=columns else None
            dashboard_columns={r[1] for r in con.execute('PRAGMA table_info(dashboard)')}
            entries={}
            dashboard_total=con.execute('SELECT COUNT(*) FROM dashboard').fetchone()[0]
            readable={'uid','data'}<=dashboard_columns
            if readable:
                for uid in ('autokom-logs','autokom-logging-health','autokom-logging-security','autokom-logging-cicd'):
                    r=con.execute('SELECT data FROM dashboard WHERE uid=?',(uid,)).fetchone()
                    if r:
                        try:
                            data=json.loads(r[0]); entries[uid]={'panels':len(data.get('panels',[])), 'queries':[t.get('expr') for panel in data.get('panels',[]) for t in panel.get('targets',[]) if isinstance(t.get('expr'),str)]}
                        except (ValueError,TypeError): entries[uid]={'panels':-1,'queries':[]}
            return {'available':True, 'datasource':dict(zip(('uid','type','url'),ds)) if ds else None,
                    'dashboards':entries,'dashboard_total':dashboard_total,
                    'dashboards_authoritative':bool(dashboard_total and readable)}
    except (sqlite3.Error,OSError,ValueError) as exc:
        return {'available':False,'reason':type(exc).__name__}
    finally:
        if 'con' in locals():con.close()

def request(path,token,*,method='GET',body=None,base=BASE):
    headers={'Accept':'application/json','User-Agent':'AUTOKOM-Grafana-Bridge/1'}
    if token:headers['Authorization']='Bearer '+token
    if body is not None:headers['Content-Type']='application/json'
    data=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(base+path,data=data,headers=headers,method=method)
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req,timeout=7) as r: return json.loads(r.read(1_500_000)), None
    except urllib.error.HTTPError as exc:return None,'HTTP '+str(exc.code)
    except (urllib.error.URLError,TimeoutError,ValueError,OSError):return None,'Connexion/JSON impossible'

def api_probe(token, now=None):
    if not token:return {'checked':False,'reason':'AUTOKOM_GRAFANA_TOKEN non fourni'}
    ds,err=request('/api/datasources/uid/autokom-loki',token)
    if err:return {'checked':True,'ok':False,'error':'Datasource Grafana : '+err}
    if ds.get('type')!='loki' or ds.get('url')!=LOKI:
        return {'checked':True,'ok':False,'error':'Datasource Grafana UID/type/URL incorrects'}
    now=int(time.time()*1000) if now is None else now
    body={'from':str(now-3600000),'to':str(now), 'queries':[
        {'refId':'A','datasource':{'uid':'autokom-loki','type':'loki'},
         'expr':'{platform="autokom"}','queryType':'range','maxLines':10,'intervalMs':60000,'maxDataPoints':30}]}
    result,err=request('/api/ds/query',token,method='POST',body=body)
    if err:return {'checked':True,'ok':False,'error':'Query via Grafana : '+err}
    entry=result.get('results',{}).get('A',{}) if isinstance(result,dict) else {}
    if entry.get('error') or entry.get('status',200)>=400:
        return {'checked':True,'ok':False,'error':'Grafana query error '+str(entry.get('status', ''))+' '+str(entry.get('error',''))[:150]}
    frames=entry.get('frames',[]) if isinstance(entry,dict) else []
    samples=sum(len(f.get('data',{}).get('values',[[]])[0]) for f in frames if f.get('data',{}).get('values'))
    return {'checked':True,'ok':True,'frames':len(frames),'samples':samples}

def drift(state,dir=DASH):
    """Metadata drift: files vs effective SQLite Grafana dashboard queries."""
    differences=[]
    if not state.get('available'):return differences
    if state.get('datasource') != {'uid':'autokom-loki','type':'loki','url':LOKI}:
        differences.append('Datasource Loki absente ou URL incorrecte dans la base Grafana')
    for uid,live in state.get('dashboards',{}).items():
        file=dir/(uid+'.json')
        if file.exists():
            try:
                disk=json.loads(file.read_text())
                expected=[t.get('expr') for panel in disk.get('panels',[]) for t in panel.get('targets',[]) if isinstance(t.get('expr'),str)]
                if live['queries']!=expected:differences.append(uid+': requêtes en base différentes des fichiers')
            except (OSError,ValueError,TypeError):differences.append(uid+': JSON invalide')
    # Empty SQLite dashboards table cannot disprove dashboards displayed by the
    # running Grafana (non-default DB, wrong path or alternative storage).
    if not state.get('dashboards_authoritative',True):return differences
    for file in dir.glob('autokom-logging-*.json'):
        uid=file.stem
        if uid not in state.get('dashboards',{}):differences.append(uid+': dashboard absent de la base Grafana')
    if (dir/'autokom-logs.json').exists() and 'autokom-logs' not in state.get('dashboards',{}):
        differences.append('autokom-logs: dashboard absent de la base Grafana')
    return differences

def report():
    from cli_colors import pretty_print as print
    print('  ◈ AUTOKOM / GRAFANA → LOKI · VALIDATION RÉELLE')
    state=local_state()
    if not state['available']:
        print('  ! SQLite : '+state['reason'])
    else:
        ds=state.get('datasource')
        print('  '+('✓' if ds=={'uid':'autokom-loki','type':'loki','url':LOKI} else '✗')+' Datasource en base : '+(ds.get('url','incorrecte') if ds else 'ABSENTE'))
        if state.get('dashboards_authoritative',True):
            print('  ✓ Dashboards identifiés dans SQLite : '+str(len(state.get('dashboards',{}))))
        else:
            print('  ! Dashboards SQLite : état NON CONFIRMÉ (0 trouvé dans cette base; pas une preuve d’absence dans Grafana)')
        for item in drift(state):print('  ! '+item)
    token=os.environ.get('AUTOKOM_GRAFANA_TOKEN','')
    test=api_probe(token)
    if not test['checked']:
        print('  ! API Grafana : vérification de requête authentifiée indisponible ; définir AUTOKOM_GRAFANA_TOKEN (jeton à portée minimale).')
    elif test['ok']:
        print(f"  ✓ Grafana → Loki : requête acceptée ({test['frames']} frames, {test['samples']} échantillons en 1 h)")
    else:print('  ✗ Grafana → Loki : '+test['error'])
    print('  › Fichier provisioning datasource : '+('présent' if DS.is_file() else 'ABSENT'))
    print('  › Fournisseur dashboards : '+('présent' if PROVIDER.is_file() else 'ABSENT'))
    return state, test



def datasource_loaded():
    """Only prove datasource presence; dashboard status may require Grafana API."""
    state=local_state()
    if not state.get('available'):
        return None
    return state.get('datasource')=={'uid':'autokom-loki','type':'loki','url':LOKI}


def finalize_install(*,changed=False):
    """Finish Grafana provisioning as part of `install logs` / reconcile.

    Restart at most once if an initial config change or an absent datasource
    warrants it. Check service status and datasource afterwards. This is not
    called from --check/status/audit. Any failures are explicit.
    """
    from cli_colors import pretty_print as print
    import shutil
    if not DS.is_file():raise RuntimeError('Datasource AUTOKOM Loki absente sur disque')
    if not PROVIDER.is_file():raise RuntimeError('Provider AUTOKOM dashboards absent sur disque')
    active=subprocess.run(['systemctl','is-active','--quiet','grafana-server'],capture_output=True,timeout=8)
    if active.returncode:
        print('  ! Grafana n’est pas actif : provisioning préparé, validation différée')
        return False
    loaded=datasource_loaded()
    if not changed and loaded is True:
        print('  ✓ Grafana : datasource déjà chargée, configuration inchangée ; redémarrage évité')
        return True
    if not changed and loaded is None:
        print('  ! Grafana SQLite non vérifiable : aucun redémarrage automatique sans preuve de décalage')
        return False
    from tool_banner import Activity
    print('  › Provisioning Grafana modifié ou datasource manquante : rechargement contrôlé (Grafana seul)')
    with Activity('Chargement des datasources et dashboards Grafana'):
        proc=subprocess.run(['systemctl','restart','grafana-server'],capture_output=True,text=True,timeout=90)
    if proc.returncode:raise RuntimeError('Grafana n’a pas redémarré : journalctl -u grafana-server -n 100')
    deadline=time.monotonic()+35
    while time.monotonic()<deadline:
        response,err=request('/api/health','')
        if err is None and isinstance(response,dict) and response.get('database')=='ok':break
        time.sleep(1)
    else:raise RuntimeError('Grafana ne répond pas après rechargement : contrôler journalctl -u grafana-server')
    check=datasource_loaded()
    if check is False:raise RuntimeError('Grafana redémarré mais datasource Loki absente : vérifier provisioning Grafana')
    if check is None:print('  ! Datasource non confirmée dans SQLite ; contrôle API authentifié recommandé')
    else:print('  ✓ Grafana : datasource Loki chargée après rechargement')
    print('  › Dashboards : fournisseur actif au démarrage ; vérification API utile pour leur visibilité')
    return check is True


def reconcile(*,apply=False):
    from cli_colors import pretty_print as print
    state,test=report()
    issues=drift(state) if state.get('available') else []
    if not PROVIDER.is_file():issues.append('Provider Grafana AUTOKOM manquant')
    if test.get('checked') and not test.get('ok'):
        print('  ! Requête Grafana non validée : '+test['error'])
        # A query or permission error is not evidence that a restart helps.
        # Only proven provisioning drift authorizes a controlled reload.

    if not apply:
        print('  › Pour recharger volontairement : sudo autokom logs grafana --apply')
        if issues:return 1
        if not state.get('available') and not test.get('ok'):return 2
        if not state.get('dashboards_authoritative',True) and not test.get('checked'):
            print('  ! Vérification partielle : absence de preuve API pour les dashboards ; code 2 (inconclusif)')
            return 2
        return 0
    if os.geteuid()!=0:raise RuntimeError('sudo requis')
    if not DS.is_file():raise RuntimeError('Datasource absente sur disque ; exécuter sudo autokom logs reconcile --apply')
    if not state.get('available') and not test.get('ok'):
        raise RuntimeError('État de Grafana non prouvé (ni SQLite ni API) : redémarrage interdit')
    if not issues:
        print('  ✓ Aucun décalage de provisioning confirmé ; redémarrage Grafana évité.')
        return 0
    if not state.get('available'):
        raise RuntimeError('Impossible de prouver un décalage ; fournir un jeton Grafana avant toute modification')
    from menu import pick
    answer=pick('AUTOKOM · Recharger uniquement Grafana ?',
                ['Redémarrer grafana-server pour charger les fichiers provisionnés'],
                hint='Interruption courte de Grafana/évaluation des alertes',cancel_label='Annuler sans modification')
    if answer!=1:
        print('  ! Opération annulée');return 1
    from logging_stack import provision_grafana, sync_grafana_after_install
    changed=provision_grafana()
    # A reload may be needed even if files are byte-for-byte unchanged
    # (datasource was missing in running Grafana).
    sync_grafana_after_install(changed=changed or bool(issues))
    print('  ✓ Intégration Grafana réconciliée ; contrôler les panneaux et l’API Grafana')
    return 0
