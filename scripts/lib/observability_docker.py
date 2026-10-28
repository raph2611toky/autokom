"""Opt-in cAdvisor; do not restart Docker or replace existing containers."""
import json
import subprocess
import time
from observability_extras import update_prom_config, local_http_ok

NAME='autokom-cadvisor'
IMAGE='ghcr.io/google/cadvisor:0.55.1'
LABEL='tech.autokom.managed'
PORT=8081


def inspect_existing():
    result=subprocess.run(['docker','container','ls','-a','--format','{{.Names}}'],
                          capture_output=True,text=True,timeout=15,check=True)
    if NAME not in result.stdout.splitlines():
        return None
    result=subprocess.run(['docker','inspect',NAME],capture_output=True,text=True,timeout=15,check=True)
    return json.loads(result.stdout)[0]


def validate_existing(data):
    if not isinstance(data,dict):
        raise ValueError("Inspection cAdvisor indisponible")
    if data.get('Config',{}).get('Labels',{}).get(LABEL) != 'true':
        raise ValueError('Conteneur autokom-cadvisor non géré : conservé, intervention manuelle requise')
    host=data.get('HostConfig',{})
    if host.get('NetworkMode')=='host' or host.get('PublishAllPorts'):
        raise ValueError('cAdvisor existant : réseau non privé')
    if host.get('PortBindings') != {'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':str(PORT)}]}:
        raise ValueError('cAdvisor existant : publication TCP non conforme')


def ensure_cadvisor(o,args):
    if not getattr(args,'setup_cadvisor',False):
        o.out('›','Docker : installation cAdvisor non demandée ; collecte existante conservée')
        return
    try:
        data=inspect_existing()
        if data:
            validate_existing(data)
            o.run(['docker','start',NAME],timeout=60)
        else:
            if o.listener(PORT):
                raise o.SetupError('Port 8081 occupé : aucun conteneur remplacé')
            o.run(['docker','info'],timeout=30)
            o.run(['docker','pull',IMAGE],timeout=420)
            o.run(['docker','run','-d','--name',NAME,'--label',LABEL+'=true',
                   '--restart','unless-stopped','--publish','127.0.0.1:8081:8080',
                   '--privileged','--device','/dev/kmsg',
                   '--volume','/:/rootfs:ro','--volume','/var/run:/var/run:ro',
                   '--volume','/sys:/sys:ro','--volume','/var/lib/docker:/var/lib/docker:ro',
                   '--volume','/dev/disk:/dev/disk:ro',IMAGE,
                   '--docker_only=true','--housekeeping_interval=30s'],timeout=120)
        # Docker may publish via NAT without a process visible in ss; inspect
        # PortBindings as well as local HTTP, not just listening sockets.
        validate_existing(inspect_existing())
        for attempt in range(30):
            if local_http_ok(PORT):
                break
            time.sleep(1)
        else:
            raise o.SetupError('cAdvisor non prêt : consulter docker logs autokom-cadvisor')
        update_prom_config(o,cadvisor=True)
        o.out('✓','cAdvisor : endpoint local prêt ; job Prometheus ajouté (audit requis pour les séries)')
    except (subprocess.SubprocessError,ValueError,OSError,TypeError) as exc:
        raise o.SetupError('Installation cAdvisor impossible : '+str(exc)) from exc
