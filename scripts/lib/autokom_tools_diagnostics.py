#!/usr/bin/env python3
"""Read-only per-tool status and redacted journals for AUTOKOM.

Never guesses a service name from untrusted text and never calls a shell.
Systemd, Docker Compose labels and Nginx logs are handled separately.
"""
from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess
import sys
from autokom_ingress_health import probe_https


class DiagnosticsError(RuntimeError):
    pass


def _redact(line):
    # Best-effort masking for common credentials in accidental logs. Logs may
    # contain other sensitive application data; operators should review before sharing.
    line = re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)\S+', r'\1[MASQUÉ]', line)
    line = re.sub(r'(?i)\b((?:password|passwd|api[_-]?token|secret|smtp[_-]?password|access[_-]?key)\s*[=:]\s*)\S+',
                  r'\1[MASQUÉ]', line)
    return line


def _call(argv, *, follow=False):
    if follow:
        try:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, bufsize=1)
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    print(_redact(line), end='', flush=True)
            except KeyboardInterrupt:
                process.terminate()
            finally:
                process.stdout.close()
            return process.wait(timeout=6) if process.poll() is not None else 0
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DiagnosticsError('Lecture des journaux impossible') from exc
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DiagnosticsError('Lecture des journaux impossible') from exc
    if result.stdout:
        print(_redact(result.stdout), end='' if result.stdout.endswith('\n') else '\n')
    if result.returncode:
        raise DiagnosticsError(f'Journal indisponible : {result.stderr.strip()[:240]}')
    return 0


def _unit(name):
    from tools_registry import get
    obj=get(name)
    # UFW has its own status command but logs via journald usually absent.
    return obj.unit


def service_logs(name, *, lines=80, follow=False, since=None):
    from tools_registry import get, LOG_DOCKER_SERVICES
    get(name)
    try: number=int(lines)
    except (TypeError, ValueError) as exc: raise DiagnosticsError('--lines : entier attendu') from exc
    if not 1 <= number <= 1000:
        raise DiagnosticsError('--lines doit être compris entre 1 et 1000')
    if since is not None and (len(since)>100 or any(ord(c)<32 for c in since)):
        raise DiagnosticsError('--since invalide')
    if name == 'nginx':
        logfile=Path('/var/log/nginx/error.log')
        if logfile.is_symlink() or not logfile.is_file():
            raise DiagnosticsError('Journal Nginx absent ou non fiable')
        return _call(['tail','-n',str(number)]+(['-F'] if follow else [])+[str(logfile)],follow=follow)
    if name == 'certbot':
        logfile=Path('/var/log/letsencrypt/letsencrypt.log')
        if logfile.is_symlink() or not logfile.is_file():
            raise DiagnosticsError('Journal Certbot absent ou non fiable')
        return _call(['tail','-n',str(number)]+(['-F'] if follow else [])+[str(logfile)],follow=follow)
    if name in LOG_DOCKER_SERVICES:
        try:
            p=subprocess.run(['docker','ps','-q',
                '--filter','label=com.docker.compose.project=autokom-logs',
                '--filter',f'label=com.docker.compose.service={name}'],
                text=True,capture_output=True,timeout=6,check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DiagnosticsError('Docker indisponible') from exc
        if p.returncode or not p.stdout.strip():
            raise DiagnosticsError(f'Aucun conteneur AUTOKOM {name} trouvé')
        container=p.stdout.splitlines()[0].strip()
        if not re.fullmatch(r'[a-f0-9]{12,64}',container):
            raise DiagnosticsError('Identifiant du conteneur non fiable')
        argv=['docker','logs','--tail',str(number)]
        if since:argv.extend(['--since',since])
        if follow:argv.append('--follow')
        argv.append(container)
        return _call(argv,follow=follow)
    unit=_unit(name)
    if not unit:
        raise DiagnosticsError(f'{name} ne possède pas de journal systemd dédié')
    argv=['journalctl','-u',unit,'--no-pager','--output=cat','-n',str(number)]
    if since: argv.extend(['--since',since])
    if follow: argv.append('--follow')
    return _call(argv,follow=follow)


def status(name):
    from tools_registry import get, service_state, proc
    tool=get(name)
    print(f'  ◈ AUTOKOM / {name.upper()} · DIAGNOSTIC')
    state=service_state(tool)
    healthy=state in ('active', 'outil CLI')
    print('  › Service : '+state)
    if name == 'nginx':
        p=proc(['nginx','-t'])
        syntax_ok=bool(p and p.returncode==0)
        healthy &= syntax_ok
        print('  › Syntaxe Nginx : '+('valide' if syntax_ok else 'invalide / indisponible'))
        from autokom_ingress_health import catchall_ready
        catch_ok=catchall_ready()
        healthy &= catch_ok
        print('  › Hôtes inconnus : '+('catch-all AUTOKOM actif' if catch_ok else 'non protégés / à vérifier'))
    if name in ('nginx','jenkins','prometheus','grafana'):
        from observability_config import load
        from autokom_ingress_health import managed_domains
        try:
            config=load()
            selections=managed_domains(config)
            domains = ({'prometheus':[(selections['prometheus'], 'prometheus')],
                        'grafana':[(selections['grafana'],'grafana')],
                        'nginx':[(selections['prometheus'],'prometheus'),(selections['grafana'],'grafana')]}
                       .get(name, []))
            for domain, kind in domains:
                if not domain:continue
                code,local,errors=probe_https(domain)
                expected=(code==401) if kind=='prometheus' else code in (200,301,302,303,307,308,401)
                from autokom_ingress_health import probe_http
                redirect, _, _ = probe_http(domain)
                expected &= redirect in (301, 308)
                healthy &= expected
                print(f'  › {domain}: HTTPS local/SNI '+(f'OK ({code} via {local}); HTTP redirection {redirect}' if expected else f'ÉCHEC (HTTPS {code}; HTTP {redirect}; {", ".join(errors)})'))
        except (OSError,ValueError,RuntimeError) as exc:
            print('  ! Diagnostic HTTPS : '+type(exc).__name__)
    if name in ('nginx','prometheus','grafana'):
        ufw=proc(['ufw','status'])
        if ufw and ufw.returncode==0:
            lines=ufw.stdout.splitlines()
            relevant=[line.strip() for line in lines if re.search(r'\b(?:80|443)/tcp\b',line)]
            print('  › UFW : '+(lines[0].strip() if lines else 'inconnu'))
            for item in relevant[:6]: print('    '+item)
        print('  ! Pare-feu VPC GCP / accessibilité depuis Internet : NON VÉRIFIÉS depuis la VM')
        print('  › Tester depuis Kali : curl -4sSI --connect-timeout 5 https://'+
              ('prometheus.ikom.mg' if name=='prometheus' else 'grafana.ikom.mg' if name=='grafana' else 'jenkins.ikom.mg'))
    print(f'  › Journaux : autokom tools {name} logs --lines 80')
    return 0 if healthy else 1


def overview():
    from tools_registry import get,service_state
    print('  ◈ AUTOKOM / ÉTAT DES OUTILS (processus locaux uniquement)')
    for name in ('nginx','jenkins','prometheus','grafana','node-exporter','alertmanager','docker','mysql'):
        try:print(f'  {name:<18} {service_state(get(name))}')
        except Exception:print(f'  {name:<18} inconnu')
    print('  › Un processus actif ne garantit pas l’accès HTTPS public.')
    print('  › Pour inspecter : autokom tools prometheus status')
    return 0
