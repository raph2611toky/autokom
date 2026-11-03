"""Versioned Grafana dashboards; no network template dependency."""
import json
from pathlib import Path

PROVIDER = Path('/etc/grafana/provisioning/dashboards/autokom.yaml')
DIRECTORY = Path('/var/lib/grafana/dashboards/autokom')
MARKER = 'autokom-managed'


def dashboards(uid, jenkins_job):
    ds={'type':'prometheus','uid':uid}
    job=json.dumps(jenkins_job)
    node='job="node",instance=~"$instance"'
    container='job="cadvisor",image!="",name=~"$container"'
    def make(slug,title,specs,variables=(),description=''):
        panels=[]
        for i,(label,expr,unit) in enumerate(specs):
            panels.append({'id':i+1,'title':label,'type':'timeseries','datasource':ds,
                'gridPos':{'x':12*(i%2),'y':8*(i//2),'w':12,'h':8},
                'fieldConfig':{'defaults':{'unit':unit},'overrides':[]},
                'targets':[{'refId':'A','expr':expr,'legendFormat':'{{instance}} {{name}} {{mountpoint}} {{__name__}}','datasource':ds}],
                'options':{'legend':{'displayMode':'table','placement':'bottom'},'tooltip':{'mode':'multi'}}})
        return {'uid':'autokom-'+slug,'title':'AUTOKOM · '+title,'tags':[MARKER,'autokom'],
                'schemaVersion':39,'version':1,'editable':False,'refresh':'30s',
                'time':{'from':'now-1h','to':'now'},'timezone':'browser','description':description,
                'templating':{'list':[{'name':name,'label':name,'type':'query','datasource':ds,
                    'query':query,'refresh':1,'multi':True,'includeAll':True,'allValue':'.*',
                    'current':{'text':'All','value':'$__all'}} for name,query in variables]},'panels':panels}
    return [
        make('host','Serveur',[
            ('CPU utilisé',f'100 * (1 - avg by(instance) (rate(node_cpu_seconds_total{{{node},mode="idle"}}[5m])))','percent'),
            ('RAM utilisée',f'100 * (1 - node_memory_MemAvailable_bytes{{{node}}}/node_memory_MemTotal_bytes{{{node}}})','percent'),
            ('Disque disponible',f'node_filesystem_avail_bytes{{{node},fstype!~"tmpfs|overlay|squashfs|ramfs"}}','bytes'),
            ('Disque utilisé',f'100 * (1-node_filesystem_avail_bytes{{{node},fstype!~"tmpfs|overlay|squashfs|ramfs"}}/node_filesystem_size_bytes{{{node},fstype!~"tmpfs|overlay|squashfs|ramfs"}})','percent'),
            ('Charge système 1 / 5 / 15 min',f'{{__name__=~"node_load1|node_load5|node_load15",{node}}}','short'),
            ('Réseau reçu',f'rate(node_network_receive_bytes_total{{{node},device!="lo"}}[5m])','Bps'),
            ('Réseau envoyé',f'rate(node_network_transmit_bytes_total{{{node},device!="lo"}}[5m])','Bps'),
            ('Uptime',f'time()-node_boot_time_seconds{{{node}}}','s'),
            ('Inodes disponibles',f'100*node_filesystem_files_free{{{node}}}/node_filesystem_files{{{node}}}','percent'),
            ('Disque lectures / écritures',f'rate({{__name__=~"node_disk_read_bytes_total|node_disk_written_bytes_total",{node}}}[5m])','Bps'),
        ], [('instance','label_values(node_uname_info{job="node"}, instance)')]),
        make('jenkins','Jenkins',[
            ('Collecte Jenkins',f'up{{job={job}}}','short'),
            ('Durée du scrape',f'scrape_duration_seconds{{job={job}}}','s'),
            ('Échantillons collectés',f'scrape_samples_scraped{{job={job}}}','short'),
            ('File des exécuteurs',f'{{__name__=~".*_jenkins_executors_queue_length",job={job}}}','short'),
            ('Exécuteurs disponibles',f'{{__name__=~".*_jenkins_executors_available",job={job}}}','short'),
            ('Dernier résultat par job (0 = succès)',f'{{__name__=~".*_jenkins_builds_last_build_result_ordinal",job={job}}}','short'),
            ('Durée du dernier build',f'{{__name__=~".*_jenkins_builds_last_build_duration_milliseconds",job={job}}}','ms'),
            ('Builds échoués sur une heure',f'increase({{__name__=~".*_jenkins_builds_failed_build_count",job={job}}}[1h])','short'),
            ('Exécuteurs occupés',f'{{__name__=~".*_jenkins_executors_busy",job={job}}}','short'),
        ], description='Les métriques de file et exécuteurs dépendent du plugin Prometheus Jenkins. Une série absente reste sans données, jamais convertie en zéro.'),
        make('docker','Docker',[
            ('Collecte cAdvisor','up{job="cadvisor"}','short'),
            ('CPU par conteneur (100 % = un cœur)',f'100*sum by(instance,name)(rate(container_cpu_usage_seconds_total{{{container}}}[5m]))','percent'),
            ('Mémoire active',f'container_memory_working_set_bytes{{{container}}}','bytes'),
            ('Limite mémoire déclarée',f'container_spec_memory_limit_bytes{{{container}}} > 0','bytes'),
            ('Réseau reçu',f'sum by(instance,name)(rate(container_network_receive_bytes_total{{{container}}}[5m]))','Bps'),
            ('Réseau envoyé',f'sum by(instance,name)(rate(container_network_transmit_bytes_total{{{container}}}[5m]))','Bps'),
            ('Disque du conteneur',f'container_fs_usage_bytes{{{container}}}','bytes'),
        ], [('container','label_values(container_last_seen{job="cadvisor",image!=""}, name)')],
             'Nécessite cAdvisor : activer la collecte Docker pendant configure observability. Certaines métriques dépendent du runtime et du stockage.'),
        make('collectors','Collecte et alertes',[
            ('Disponibilité des cibles','up','short'),
            ('Durée des scrapes','scrape_duration_seconds','s'),
            ('Alertes en attente / déclenchées','ALERTS','short'),
            ('Échecs de transmission des alertes','rate(prometheus_notifications_errors_total[5m])','ops'),
        ],description='Alertes évaluées par Prometheus et routées par Alertmanager, pas par Grafana Alerting. Aucun résultat ALERTS signifie généralement aucune alerte active.')
    ]


def planned_files(uid, job):
    import yaml
    provider={'apiVersion':1,'providers':[{'name':'autokom','orgId':1,'folder':'AUTOKOM',
        'type':'file','disableDeletion':True,'allowUiUpdates':False,'updateIntervalSeconds':30,
        'options':{'path':str(DIRECTORY)}}]}
    result={PROVIDER:'# AUTOKOM MANAGED dashboards\n'+yaml.safe_dump(provider,sort_keys=False)}
    for dash in dashboards(uid,job):
        result[DIRECTORY/(dash['uid']+'.json')]=json.dumps(dash,ensure_ascii=False,indent=2)+'\n'
    return result


def needs_update(uid, job):
    try:
        return any(not p.is_file() or p.read_text()!=text for p,text in planned_files(uid,job).items())
    except (ImportError,OSError,UnicodeError):
        return True


def provision(o):
    files=planned_files(o.cfg().datasource_uid,o.cfg().jenkins_job)
    # Preflight the complete set before writing any file.
    for path in files:
        if path.is_symlink():
            raise o.SetupError(f'Dashboard : lien symbolique refusé : {path}')
        if path.exists():
            old=path.read_text()
            try:
                managed=(old.startswith('# AUTOKOM MANAGED') if path==PROVIDER
                         else MARKER in json.loads(old).get('tags',[]))
            except (ValueError,AttributeError):
                managed=False
            if not managed:
                raise o.SetupError(f'Dashboard non géré : refus de remplacer {path}')
    changed=False
    for path,text in files.items():
        changed=o.owned_write(path,text) or changed
    return changed


def verify(o, auth):
    for dash in dashboards(o.cfg().datasource_uid,o.cfg().jenkins_job):
        code, raw=o.grafana_api(*auth,'/api/dashboards/uid/'+dash['uid'])
        if code != 200:
            raise o.SetupError('Dashboard Grafana non chargé : '+dash['uid'])
        try:
            if json.loads(raw)['dashboard']['uid'] != dash['uid']:
                raise ValueError('UID différent')
        except (ValueError,KeyError,TypeError) as exc:
            raise o.SetupError('Dashboard Grafana invalide : '+dash['uid']) from exc
    o.out('✓','Grafana API : quatre dashboards AUTOKOM chargés')
