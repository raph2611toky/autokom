#!/usr/bin/env python3
"""Apply a guarded AUTOKOM broker ingress patch in the Kali Git checkout.

No host commands, Docker, network or system services are touched by this tool.
Every source anchor is checked before any file is modified.
"""
import argparse
import ast
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
NEW_HELPER = HERE/'scripts/lib/autokom_internal_ingress.py'
NEW_TEST = HERE/'tests/test_autokom_internal_ingress.py'
BROKER = Path('scripts/lib/autokom_platform_broker.py')
ADMIN = Path('scripts/lib/autokom_runtime_admin.py')
WATCHDOG_SOURCE = "def _runtime_lock_call(action, params):\n    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)\n    fd=os.open(STATE/'.deploy.lock',os.O_WRONLY|os.O_CREAT,0o600)\n    try:\n        fcntl.flock(fd,fcntl.LOCK_EX)\n        return action(params)\n    finally:\n        fcntl.flock(fd,fcntl.LOCK_UN)\n        os.close(fd)\n\ndef _ingress_scan(_unused):\n    # A previously approved relay is updated ONLY when its Docker IP changed.\n    # The same lock as deploy/rollback/commit prevents lost state updates.\n    for folder in STATE.iterdir():\n        if not folder.is_dir() or not UUID.fullmatch(folder.name):\n            continue\n        for filename in ('current.json','previous.json'):\n            record=folder/filename\n            if not record.is_file() or record.is_symlink():\n                continue\n            try:\n                doc=json.loads(record.read_text(encoding='utf-8'))\n                if not isinstance(doc,dict) or doc.get('environment_id')!=folder.name:\n                    continue\n                if ingress.reconcile(doc,execute=run,available=PROXY,enabled=ENABLED):\n                    with tempfile.NamedTemporaryFile('w',dir=folder,delete=False,prefix='.ingress-state-',encoding='utf-8') as fd:\n                        os.chmod(fd.name,0o600)\n                        json.dump(doc,fd)\n                        fd.write('\\n')\n                        tmp=fd.name\n                    os.replace(tmp,record)\n                    logger.info('AUTOKOM_INGRESS_IP_RECONCILED')\n            except Exception:\n                logger.warning('AUTOKOM_INGRESS_RECONCILE_PENDING')\n\ndef _ingress_watchdog():\n    while True:\n        try:\n            _runtime_lock_call(_ingress_scan,None)\n        except Exception:\n            logger.warning('AUTOKOM_INGRESS_WATCHDOG_UNAVAILABLE')\n        time.sleep(90)\n\n"


def one(src, old, new):
    n = src.count(old)
    if n != 1:
        raise RuntimeError(f'Incompatible broker source: expected one anchor, got {n}: {old[:110]!r}')
    return src.replace(old, new, 1)


def patch(src):
    if 'import autokom_internal_ingress as ingress' in src:
        raise RuntimeError('Ingress patch appears to be already applied. Inspect git diff.')
    src = one(src, 'import urllib.request\n',
              'import urllib.request\nimport threading\nimport autokom_internal_ingress as ingress\n')
    src = one(src, "        rid = valid_id(params.get('run_id'))\n        _isolated_agent_available()",
              "        rid = valid_id(params.get('run_id'))\n"
              "        ingress.preflight(run, PROXY, ENABLED)  # fail before an unnecessary Jenkins build\n"
              "        _isolated_agent_available()")
    src = one(src,
              "    if current and current.get('run_id')==rid:\n"
              "        return {'port':current['port'],'container':current['name'],'idempotent':True}",
              "    if current and current.get('run_id')==rid:\n"
              "        if current.get('ingress_mode') == ingress.MODE and not ingress.probe_relay(current['name'],current['port']):\n"
              "            raise BrokerError('AUTOKOM_INGRESS_NOT_READY: Relais local interrompu.')\n"
              "        return {'port':current['port'],'container':current['name'],'idempotent':True}")
    src = one(src, "    current=_load_state(environment)\n    if current and current.get('run_id')==rid:",
              "    ingress.preflight(run, PROXY, ENABLED)\n"
              "    current=_load_state(environment)\n    if current and current.get('run_id')==rid:")
    src = one(src, "    # Fixed trusted tag: only BuildKit-produced images in AUTOKOM's bounded directory.",
              "    # Also refuse builds without enough free space for image import.\n"
              "    if shutil.disk_usage(IMAGES).free < max(256*1024**2, artifact.stat().st_size*2):\n"
              "        raise BrokerError('AUTOKOM_DISK_LOW: Stockage insuffisant pour importer l’image.')\n"
              "    # Fixed trusted tag: only BuildKit-produced images in AUTOKOM's bounded directory.")
    src = one(src,
              "    argv=['docker','run','-d','--name',name,'--network',network,\n           '--publish',f'127.0.0.1::{port}',",
              "    argv=['docker','run','-d','--name',name,'--network',network,")
    src = one(src, "           '--env-file',str(envfile),'--restart','unless-stopped',tag]\n    try:",
              "           '--env-file',str(envfile),'--restart','unless-stopped',tag]\n"
              "    relay = None\n    try:")
    src = one(src,
              "        health_opener = build_opener(NoRedirect())",
              "        health_opener = build_opener(urllib.request.ProxyHandler({}), NoRedirect())")
    src = one(src, "        external_port = _docker_loopback_port(name, port)",
              "        # Nginx owns the trusted loopback listener. Customer has NO published ports.\n"
              "        relay = ingress.create(name, network, port, execute=run, available=PROXY, enabled=ENABLED)\n"
              "        external_port = relay['port']")
    src = one(src, "doc={'environment_id':environment,'run_id':rid,'port':external_port,'name':name,'network':network,'healthcheck':health}",
              "doc={'environment_id':environment,'run_id':rid,'port':external_port,'name':name,'network':network,'healthcheck':health,**relay}")
    src = one(src,
              "        _save_state(environment,doc)\n"
              "        # The previous container stays alive until all TLS proxies are installed.\n"
              "        if previous:\n"
              "            (STATE/environment/'previous.json').write_text(json.dumps(previous),encoding='utf-8')\n"
              "            os.chmod(STATE/environment/'previous.json',0o600)\n"
              "        return {'port':external_port,'container':name,'previous':previous.get('name') if previous else None}",
              "        # Persist previous BEFORE current so a failed snapshot cannot corrupt the active state.\n"
              "        prior_path=STATE/environment/'previous.json'\n"
              "        if previous:\n"
              "            if prior_path.exists():\n"
              "                raise BrokerError('AUTOKOM_PREVIOUS_PENDING: Ancienne publication non finalisée.')\n"
              "            with tempfile.NamedTemporaryFile('w',dir=prior_path.parent,delete=False,prefix='.previous-',encoding='utf-8') as fd:\n"
              "                os.chmod(fd.name,0o600);json.dump(previous,fd);fd.write('\\n');tmp=fd.name\n"
              "            os.replace(tmp,prior_path)\n"
              "        try:\n"
              "            _save_state(environment,doc)\n"
              "        except Exception:\n"
              "            if previous:prior_path.unlink(missing_ok=True)\n"
              "            raise\n"
              "        return {'port':external_port,'container':name,'previous':previous.get('name') if previous else None}")
    src = one(src,
              "    except Exception:\n        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=20)\n        raise\ndef _rollback(params):",
              "    except Exception:\n"
              "        if relay is not None:\n"
              "            try:\n"
              "                ingress.remove(name, execute=run, available=PROXY, enabled=ENABLED)\n"
              "            except Exception:\n"
              "                logger.error('AUTOKOM_INGRESS_CLEANUP_FAILED')\n"
              "        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=20)\n"
              "        raise\ndef _rollback(params):")
    src = one(src,
              "    old_path.unlink(missing_ok=True)\n    return {'rolled_back':True,'previous_available':previous is not None}",
              "    if state.get('ingress_mode') == ingress.MODE:\n"
              "        ingress.remove(state['name'], execute=run, available=PROXY, enabled=ENABLED)\n"
              "    old_path.unlink(missing_ok=True)\n    return {'rolled_back':True,'previous_available':previous is not None}")
    src = one(src,
              "        if previous.get('name') and previous['name'] != state['name']:\n"
              "            try:run(['docker','rm','-f',previous['name']],timeout=20)\n"
              "            except BrokerError:pass\n"
              "        previous_path.unlink(missing_ok=True)",
              "        if previous.get('name') and previous['name'] != state['name']:\n"
              "            old_name=valid_container_name(previous['name'])\n"
              "            present=subprocess.run(['docker','container','inspect',old_name],capture_output=True,timeout=12)\n"
              "            if present.returncode==0:\n"
              "                run(['docker','rm','-f',old_name],timeout=20)\n"
              "            if previous.get('ingress_mode') == ingress.MODE:\n"
              "                ingress.remove(old_name, execute=run, available=PROXY, enabled=ENABLED)\n"
              "        previous_path.unlink(missing_ok=True)")
    src = one(src,
              "    if action=='runtime.rollback':return _rollback(params)",
              "    if action=='runtime.rollback':return _runtime_lock_call(_rollback,params)")
    src = one(src,
              "    if action=='runtime.commit':return _commit(params)",
              "    if action=='runtime.commit':return _runtime_lock_call(_commit,params)")
    src = one(src,
              "            logger.warning('Broker request denied: %s', type(exc).__name__)\n"
              "            # No exception traceback, request content or secret values in response.\n"
              "            response={'ok':False,'error':str(exc) if isinstance(exc,BrokerError) else 'Erreur interne du service AUTOKOM.'}",
              "            safe = isinstance(exc,(BrokerError,ingress.IngressError))\n"
              "            match = re.match(r'^(AUTOKOM_[A-Z0-9_]{3,80}):',str(exc)) if safe else None\n"
              "            code = exc.code if isinstance(exc,ingress.IngressError) else (match.group(1) if match else 'AUTOKOM_BROKER_REFUSED')\n"
              "            logger.warning('Broker request denied: %s code=%s', type(exc).__name__,code)\n"
              "            # No exception traceback, request content or secret values in response.\n"
              "            response={'ok':False,'error':str(exc) if safe else 'Erreur interne du service AUTOKOM.', 'code':code}")
    src = one(src,
              "def main():\n    if os.geteuid()!=0:",
              WATCHDOG_SOURCE + "def main():\n    if os.geteuid()!=0:")
    src = one(src,
              "    IMAGES.mkdir(parents=True,exist_ok=True,mode=0o750)\n    with Server(str(SOCKET),Handler) as server:",
              "    IMAGES.mkdir(parents=True,exist_ok=True,mode=0o750)\n"
              "    threading.Thread(target=_ingress_watchdog,name='autokom-ingress-watchdog',daemon=True).start()\n"
              "    with Server(str(SOCKET),Handler) as server:")
    ast.parse(src)
    return src


def patch_admin(src):
    if 'private ingress doctor' in src:
        raise RuntimeError('Runtime ingress doctor already patched')
    src = one(src,
              "    print('  ✓ BuildKit rootless accessible par Jenkins : socket, worker et RUN validés.')\n"
              "    if failures:",
              "    print('  ✓ BuildKit rootless accessible par Jenkins : socket, worker et RUN validés.')\n"
              "    # private ingress doctor: read-only preflight and active HTTP healthcheck\n"
              "    from autokom_internal_ingress import diagnose, IngressError\n"
              "    from autokom_platform_broker import run as broker_run, PROXY, ENABLED, STATE\n"
              "    try:\n"
              "        ingress_report = diagnose(broker_run, PROXY, ENABLED, STATE)\n"
              "    except IngressError as exc:\n"
              "        raise SetupError(str(exc)) from exc\n"
              "    print('  ✓ Ingress privé : '+str(ingress_report['ingresses_checked'])+' publication(s) inspectée(s).')\n"
              "    if failures:")
    ast.parse(src)
    return src


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check',action='store_true')
    group.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    root=args.repository.expanduser().resolve()
    broker=root/BROKER
    if not broker.is_file():
        parser.error(f'Not an AUTOKOM checkout: {broker}')
    old=broker.read_text(encoding='utf-8')
    new=patch(old)
    admin=root/ADMIN
    if not admin.is_file():
        parser.error(f'Not a matching AUTOKOM runtime admin checkout: {admin}')
    updated_admin=patch_admin(admin.read_text(encoding='utf-8'))
    files=[]
    for src,dest in ((NEW_HELPER,root/'scripts/lib/autokom_internal_ingress.py'),
                     (NEW_TEST,root/'tests/test_autokom_internal_ingress.py')):
        if dest.exists():
            raise RuntimeError(f'Existing file would be overwritten: {dest}')
        files.append((dest,src.read_bytes()))
    # Publish the broker LAST: it must never import an absent module.
    files.append((broker,new.encode('utf-8')))
    files.append((admin,updated_admin.encode('utf-8')))
    if args.check:
        print('CHECK OK: broker/doctor anchors and new modules validated; no files modified.')
        for path,content in files:
            print(f'  {path.relative_to(root)}: {len(content)} bytes')
        return
    # Entire transformed set has already passed validation; write changes atomically.
    for path,content in files:
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix(path.suffix+'.autokom-write-tmp')
        if temp.exists():
            raise RuntimeError(f'Temporary target exists: {temp}')
        try:
            temp.write_bytes(content)
            temp.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    print('APPLY OK: AUTOKOM broker, runtime doctor, ingress module, and tests integrated.')
    print('Run PYTHONPATH=scripts/lib python3 -m unittest -v tests.test_autokom_internal_ingress')

if __name__ == '__main__':
    try:main()
    except Exception as exc:
        print(f'INSTALLER ABORTED: {exc}',file=sys.stderr)
        raise SystemExit(2)
