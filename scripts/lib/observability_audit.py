"""Read-only semantic checks: files, runtime rules and metric series are distinct."""
import json
from pathlib import Path
from urllib.parse import urlencode


def audit(r,cfg,http):
    def data(path):
        code,raw=http(cfg.prometheus_port,path)
        try:
            obj=json.loads(raw)
            if code!=200 or obj.get('status')!='success':
                raise ValueError()
            return obj['data']
        except (ValueError,TypeError,KeyError,AttributeError):
            r.missing('Prometheus '+path.split('?')[0],'réponse API absente ou invalide')
            return None
    targets=data('/api/v1/targets')
    if isinstance(targets,dict):
        for job in ('node','cadvisor'):
            found=[t for t in targets.get('activeTargets',[]) if t.get('labels',{}).get('job')==job]
            if not found:
                r.add('WARN',job,'job absent'+(' (Docker optionnel)' if job=='cadvisor' else ''))
            else:
                r.add('OK' if all(t.get('health')=='up' for t in found) else 'FAIL',job,'état des cibles Prometheus')
        for job,metric in [('node','node_memory_MemAvailable_bytes'),('cadvisor','container_memory_working_set_bytes{image!=""}')]:
            if any(t.get('labels',{}).get('job')==job for t in targets.get('activeTargets',[])):
                query=metric if '{' in metric else metric+'{job="'+job+'"}'
                if job=='cadvisor': query='container_memory_working_set_bytes{job="cadvisor",image!=""}'
                result=data('/api/v1/query?'+urlencode({'query':query}))
                if isinstance(result,dict):
                    r.add('OK' if result.get('result') else 'FAIL',job+' données','séries présentes' if result.get('result') else 'aucune série exploitable')
    rules=data('/api/v1/rules?type=alert')
    if isinstance(rules,dict):
        loaded=[rule for group in rules.get('groups',[]) for rule in group.get('rules',[]) if rule.get('name','').startswith('AUTOKOM')]
        from observability_extras import alert_rules
        import yaml
        expected={rule['alert'] for group in yaml.safe_load(alert_rules(cfg.jenkins_job))['groups'] for rule in group['rules']}
        missing=expected-{rule.get('name') for rule in loaded}
        bad=[rule for rule in loaded if rule.get('health')!='ok' or rule.get('lastError')]
        r.add('FAIL' if missing or bad else 'OK','Règles AUTOKOM chargées',
              ('absentes : '+', '.join(sorted(missing))) if missing else ('erreur d’évaluation' if bad else str(len(loaded))+' règles évaluées'))
    managers=data('/api/v1/alertmanagers')
    if isinstance(managers,dict):
        r.add('OK' if managers.get('activeAlertmanagers') else 'FAIL','Prometheus → Alertmanager','cible découverte (ne prouve pas la remise des e-mails)')
    code,_=http(9093,'/-/ready')
    r.add('OK' if code==200 else 'WARN','Alertmanager readiness',str(code))
    from observability_dashboards import planned_files
    files=planned_files(cfg.datasource_uid,cfg.jenkins_job)
    missing=[str(p) for p,text in files.items() if not p.is_file() or p.read_text()!=text]
    r.add('WARN' if missing else 'OK','Dashboards sur disque','absents ou à actualiser' if missing else 'quatre dashboards provisionnés ; API Grafana vérifiée pendant configure')
    r.add('WARN','Notification SMTP','remise non prouvée par cet audit ; effectuer le test contrôlé documenté')
