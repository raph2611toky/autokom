#!/usr/bin/env python3
"""AUTOKOM: read-only, end-to-end Grafana/Loki dashboard audit.

No restarts, credentials, snapshots, journal mutations or log-line printing.
Non-sensitive API metadata only. An optional short-lived Grafana API service
account token can be passed via AUTOKOM_GRAFANA_TOKEN (never echoed).
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from cli_colors import pretty_print as print

LOKI = 'http://127.0.0.1:3100'
PROM = 'http://127.0.0.1:9090'
GRAFANA = 'http://127.0.0.1:3000'
DASH_DIR = Path('/var/lib/grafana/dashboards/autokom')
LDS = Path('/etc/grafana/provisioning/datasources/autokom-loki.yaml')
PDS = Path('/etc/grafana/provisioning/datasources/autokom-prometheus.yaml')


def fetch(base, path, params=None, *, token=None, timeout=4):
    url=base+path
    if params:
        url+='?'+urllib.parse.urlencode(params)
    headers={'Accept':'application/json','User-Agent':'AUTOKOM-Logs-Audit/1'}
    if token:
        headers['Authorization']='Bearer '+token
    request=urllib.request.Request(url,headers=headers)
    # No host-specific outbound proxies. All URLs are fixed local endpoints.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            content=response.read(2_000_000)
        return json.loads(content.decode()), None
    except urllib.error.HTTPError as exc:
        # Never include arbitrary error bodies or request details with credentials.
        return None, 'HTTP '+str(exc.code)
    except (urllib.error.URLError,OSError,ValueError,TimeoutError) as exc:
        return None, type(exc).__name__


def result_count(document):
    values=document.get('data',{}).get('result',[])
    if not isinstance(values,list):return 0
    return sum(len(item.get('values',[])) for item in values if isinstance(item,dict))


def stream_labels(document):
    data=document.get('data',{}) if document else {}
    if isinstance(data,list):return [item for item in data if isinstance(item,dict)]
    if not isinstance(data,dict):return []
    return data.get('result',[]) if isinstance(data.get('result',[]),list) else []


def resolved_query(expression):
    """Approximate the dashboard default All filters for server-side LogQL test.

    This does not emulate the Grafana templating engine; it tests whether each
    concrete panel expression is accepted and has any data in Loki.
    """
    query=expression.replace('${search:regex}','').replace('$source','.*').replace('$service_name','.*')
    if '$' in query:
        raise ValueError('Variable Grafana inconnue : test non exécutable')
    return query


def lint_panels(dashboards, *, now=None):
    now=int(now if now is not None else time.time())
    window={'start':str((now-24*3600)*1_000_000_000),
            'end':str((now+5)*1_000_000_000),'step':'60','limit':'20'}
    results=[]
    for dashboard in dashboards:
        for panel in dashboard.get('panels',[]):
            for target in panel.get('targets',[]):
                expression=target.get('expr','')
                if not expression:continue
                ds=target.get('datasource',panel.get('datasource',{}))
                kind=ds.get('type','') if isinstance(ds,dict) else ''
                if kind!='loki':continue
                try:
                    query=resolved_query(expression)
                    doc,err=fetch(LOKI,'/loki/api/v1/query_range',{**window,'query':query})
                    if err:
                        status=err
                    elif doc.get('status')!='success':
                        status='API returned non-success'
                    else:
                        status='OK'+(' (data)' if result_count(doc) else ' (no matching lines/series in last 24h)')
                except ValueError:
                    status='Grafana-only templating: test skipped'
                results.append((dashboard.get('title',''), panel.get('title',''),status))
    return results


def read_dashboards(path=DASH_DIR):
    dashboards=[]
    if not path.is_dir():return dashboards
    for file in sorted(path.glob('autokom-*.json')):
        try:
            obj=json.loads(file.read_text())
            if 'logs' in obj.get('tags',[]):dashboards.append(obj)
        except (OSError,ValueError):pass
    return dashboards


def run_audit():
    print('  ◈ AUTOKOM / AUDIT DES JOURNAUX GRAFANA · lecture seule')
    failures=0
    now=int(time.time())
    t={'start':str((now-86400)*1_000_000_000),'end':str((now+5)*1_000_000_000),'limit':'10'}
    for name,base,path in [('Loki',LOKI,'/loki/api/v1/labels'),
                           ('Prometheus',PROM,'/api/v1/targets')]:
        doc,err=fetch(base,path)
        if err:
            print('  ✗ API '+name+': '+err);failures+=1
        else:
            print('  ✓ API '+name+': accessible')
            if name=='Prometheus':
                active=doc.get('data',{}).get('activeTargets',[])
                for job in ('autokom-loki','autokom-alloy','autokom-logs-canary'):
                    states=[v.get('health') for v in active if v.get('labels',{}).get('job')==job]
                    print('    '+('✓' if 'up' in states else '!')+' '+job+': '+(', '.join(str(v) for v in states) if states else 'absent'))
    doc,err=fetch(LOKI,'/loki/api/v1/series',{'match[]':'{platform="autokom"}', 'start':t['start'],'end':t['end']})
    if err:
        print('  ✗ Loki series: '+err);failures+=1
    else:
        series=stream_labels(doc)
        print(f'  {"✓" if series else "!"} Séries Loki platform=autokom (24h) : {len(series)}')
        # Only print bounded low-cardinality source/unit information.
        for key in ('source','service_name'):
            found=sorted({str(s[key]) for s in series if key in s})
            print('    '+key+': '+(', '.join(found[:12]) + ('…' if len(found)>12 else '') if found else 'aucun label observé'))
        if not series:
            failures+=1
            print('    ✗ Aucun flux AUTOKOM sur 24h : vérifier journald → Alloy → Loki ; sonde canari et période.')
        elif not any(s.get('source')=='nginx' for s in series):
            print('    ! Pas de source nginx visible : vérifier droits de lecture, chemins /var/log/nginx et trafic récent.')
        if series and not any(s.get('source')=='docker' for s in series):
            print('    ! Pas de source docker visible : vérifier socket Docker, groupes Alloy et logs des applications.')
    docs=read_dashboards()
    print('  '+('✓' if docs else '!')+f' Dashboards AUTOKOM logs sur disque : {len(docs)}')
    for title,panel,state in lint_panels(docs):
        marker='✗' if state.startswith('HTTP ') or state.startswith('API returned') else '✓' if state.startswith('OK (data)') else '!'
        print(f'  {marker} {title} / {panel} : {state}')
        failures+=int(marker=='✗')
    # Authenticate only when operator deliberately supplied a token in their environment.
    token=os.environ.get('AUTOKOM_GRAFANA_TOKEN') or None
    for uid in ('autokom-loki','autokom-prometheus'):
        doc,err=fetch(GRAFANA,'/api/datasources/uid/'+uid,token=token)
        if err=='HTTP 401' or err=='HTTP 403':
            print('  › Grafana datasource '+uid+': vérification API nécessite un jeton (AUTOKOM_GRAFANA_TOKEN).')
        elif err:
            print('  ! Grafana datasource '+uid+': '+err)
        elif not isinstance(doc,dict) or doc.get('uid')!=uid:
            print('  ! Grafana datasource '+uid+': UID inattendu')
        else:
            print('  ✓ Grafana datasource '+uid+' : '+str(doc.get('type',''))+' (API)')
            if uid=='autokom-loki':
                if doc.get('type')!='loki' or doc.get('url')!=LOKI:
                    print('  ! Loki Grafana configuré sur un type/endpoint différent de celui d’AUTOKOM')
                    failures+=1
                health,eh=fetch(GRAFANA,'/api/datasources/uid/'+uid+'/health',token=token)
                if eh:
                    print('  ! Test Grafana → Loki : '+eh)
                else:
                    print('  '+('✓' if health.get('status')=='OK' else '!')+' Test Grafana → Loki : '+str(health.get('status','inconnu')))
    if token:
        for disk in docs:
            uid=disk.get('uid','')
            if not uid or not re.fullmatch(r'[a-zA-Z0-9_-]+',uid):continue
            live,err=fetch(GRAFANA,'/api/dashboards/uid/'+uid,token=token)
            if err:
                print('  ! Dashboard Grafana '+uid+': '+err)
                failures+=1
                continue
            content=live.get('dashboard',{}) if isinstance(live,dict) else {}
            if content.get('uid')!=uid or len(content.get('panels',[]))!=len(disk.get('panels',[])):
                print('  ✗ Dashboard Grafana '+uid+': provisionnement non synchronisé')
                failures+=1
            else:
                print('  ✓ Dashboard Grafana '+uid+': présent ('+str(len(content.get('panels',[])))+' panneaux)')
    print('  '+('✓' if LDS.is_file() else '!')+' Provisioning Loki : '+str(LDS))
    print('  '+('✓' if PDS.is_file() else '!')+' Provisioning Prometheus : '+str(PDS))
    # Do not confuse direct Loki query success with the effective Grafana
    # datasource (a common cause of red panel warnings).
    from grafana_bridge import local_state, drift, api_probe
    state=local_state()
    if state.get('available'):
        ds=state.get('datasource')
        if ds and ds.get('uid')=='autokom-loki' and ds.get('type')=='loki' and ds.get('url')==LOKI:
            print('  ✓ Grafana SQLite : datasource Loki chargée en base')
        else:
            print('  ✗ Grafana SQLite : datasource Loki absente ou incorrecte dans la base effective')
            failures+=1
        for issue in drift(state):
            print('  ✗ Grafana : '+issue)
            failures+=1
    else:
        print('  ! Grafana SQLite : '+state.get('reason','non vérifié'))
    if token:
        test=api_probe(token)
        if not test.get('ok'):
            print('  ✗ Grafana → Loki : '+test.get('error','inconnu'));failures+=1
        else:
            print(f"  ✓ Grafana → Loki : {test['frames']} frames, {test['samples']} échantillons (1 h)")
    print('  › Panneaux rouges : menu du panneau → Inspect → Query / Error ; vérifier le flux navigateur/Grafana.')
    print('  › Audit sans mutation ni exposition des lignes de journaux.')
    return 1 if failures else 0

if __name__=='__main__':raise SystemExit(run_audit())
