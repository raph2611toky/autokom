"""Grafana dashboards for AUTOKOM dual-log architecture; stable UIDs, local files."""
import json

LOKI={'type':'loki','uid':'autokom-loki'}
PROM={'type':'prometheus','uid':'autokom-prometheus'}

def query_var(name,label,query):
    return {'name':name,'label':label,'type':'query','datasource':LOKI,
            'query':query,'refresh':2,'multi':True,'includeAll':True,'allValue':'.*',
            'current':{'text':'All','value':'$__all'}}

def variables():
    return [query_var('source','Source','label_values({platform="autokom"}, source)'),
            query_var('service_name','Service','label_values({platform="autokom"}, service_name)'),
            {'name':'search','label':'Contenu (regex)','type':'textbox','query':'',
             'current':{'text':'','value':''}}]

def panel(pid,title,typ,expr,x,y,w=12,h=8,*,metric=False,unit='short'):
    ds=PROM if metric else LOKI
    result={'id':pid,'title':title,'type':typ,'datasource':ds,
           'gridPos':{'x':x,'y':y,'w':w,'h':h},
           'targets':[{'refId':'A','expr':expr,'datasource':ds,
                      'queryType':'range','legendFormat':'{{source}} {{service_name}} {{job}}'}]}
    if typ=='timeseries':
        result['fieldConfig']={'defaults':{'unit':unit},'overrides':[]}
    if typ=='stat':
        result['fieldConfig']={'defaults':{'unit':unit},'overrides':[]}
    return result

def make(uid,title,description,panels,*,vars=True):
    return {'uid':uid,'title':'AUTOKOM · '+title,'description':description,
            'tags':['autokom-managed','autokom','logs'],'schemaVersion':39,
            'version':1,'refresh':'30s','editable':False,
            'time':{'from':'now-3h','to':'now'},'timezone':'browser',
            'templating':{'list':variables() if vars else []},'panels':panels}

def dashboards():
    stream='{platform="autokom",source=~"$source",service_name=~"$service_name"}'
    general=make('autokom-logs','Journaux',
         'Exploration centralisée des logs. Le diagnostic est dans le dashboard Santé des logs; filtrez par source, service et contenu.',[
         panel(1,'Journaux des services','logs',stream+' |~ "${search:regex}"',0,0,24,14),
         panel(2,'Débit de journaux (lignes/s)','timeseries','sum by (source) (rate('+stream+'[5m]))',0,14),
         panel(3,'Volume par service','timeseries','sum by (service_name) (rate('+stream+'[5m]))',12,14),
         panel(4,'Erreurs / 5 minutes','timeseries','sum by (source) (count_over_time('+stream+' |~ "(?i)(error|exception|fatal|panic)" [5m]))',0,22),
         panel(5,'Avertissements / 5 minutes','timeseries','sum by (source) (count_over_time('+stream+' |~ "(?i)(warn|warning|timeout)" [5m]))',12,22)
    ])
    health=make('autokom-logging-health','Santé des logs',
         'Disponibilité Loki/Alloy, qualité et fraîcheur de la chaîne journald → Alloy → Loki. Les alertes sont évaluées dans Prometheus et envoyées via Alertmanager.',[
         panel(1,'Alloy UP','stat','up{job="autokom-alloy"}',0,0,6,5,metric=True),
         panel(2,'Loki UP','stat','up{job="autokom-loki"}',6,0,6,5,metric=True),
         panel(3,'Canari E2E réussi','stat','autokom_logs_canary_success',12,0,6,5,metric=True),
         panel(4,'Âge du dernier canari','stat','time() - autokom_logs_canary_last_run_timestamp_seconds',18,0,6,5,metric=True,unit='s'),
         panel(5,'Durée du canari','timeseries','autokom_logs_canary_duration_seconds',0,5,12,8,metric=True,unit='s'),
         panel(6,'Erreurs de scrape','timeseries','up{job=~"autokom-(loki|alloy|logs-canary)"}',12,5,12,8,metric=True),
         panel(7,'Logs reçus / seconde','timeseries','sum(rate(loki_distributor_lines_received_total{job="autokom-loki"}[5m]))',0,13,12,8,metric=True),
         panel(8,'Logs rejetés / seconde','timeseries','sum by (reason) (rate(loki_discarded_samples_total{job="autokom-loki"}[5m]))',12,13,12,8,metric=True),
         panel(9,'Ressources RSS Alloy / Loki','timeseries','process_resident_memory_bytes{job=~"autokom-(alloy|loki)"}',0,21,12,8,metric=True,unit='bytes'),
         panel(10,'CPU Alloy / Loki','timeseries','rate(process_cpu_seconds_total{job=~"autokom-(alloy|loki)"}[5m])',12,21,12,8,metric=True,unit='cores')
        ],vars=False)
    security=make('autokom-logging-security','Sécurité des journaux',
         'Vue opérationnelle, sans exposition Internet; expressions heuristiques : vérifier les faux positifs avant toute remédiation.',[
         panel(1,'Échecs SSH et authentifications','timeseries',
               'sum(count_over_time({platform="autokom",source="systemd"} |~ "(?i)(failed password|authentication failure|invalid user)" [5m]))',0,0),
         panel(2,'Nginx HTTP 4xx/5xx','timeseries',
               'sum by (source) (count_over_time({platform="autokom",source="nginx"} |~ "HTTP/[0-9.]+\\\" (4[0-9]{2}|5[0-9]{2}) " [5m]))',12,0),
         panel(3,'Événements de sécurité','logs',
               '{platform="autokom"} |~ "(?i)(unauthorized|denied|authentication failure|permission denied|attack)"',0,8,24,12),
         panel(4,'Logs Nginx','logs','{platform="autokom",source="nginx"}',0,20,24,12)
        ],vars=False)
    pipeline=make('autokom-logging-cicd','CI/CD et services',
         'Logs de Jenkins et des services systemd; si Jenkins tourne dans Docker il est visible par son service_name.',[
         panel(1,'Jenkins systemd','logs','{platform="autokom",source="systemd",unit="jenkins.service"}',0,0,24,12),
         panel(2,'Jenkins Docker','logs','{platform="autokom",source="docker",service_name=~"(?i).*jenkins.*"}',0,12,24,12),
         panel(3,'Docker : erreurs et exceptions','logs','{platform="autokom",source="docker"} |~ "(?i)(error|fatal|exception|panic)"',0,24,24,12),
        ],vars=False)
    return general,health,security,pipeline

def serialized():
    return {d['uid']+'.json':json.dumps(d,ensure_ascii=False,indent=2)+'\n' for d in dashboards()}
