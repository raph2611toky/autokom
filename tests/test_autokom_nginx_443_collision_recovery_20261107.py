"""Regression for GCP 2026-11-07: nginx reload returned success but 443 stayed unavailable."""
import unittest
from unittest import mock
from types import SimpleNamespace

import autokom_nginx_runtime as runtime
import autokom_ingress_health as ingress


def scan(port80=('nginx', 123), port443=None):
    result={80:[],443:[]}
    if port80 is not None:
        result[80]=[runtime.Listener(80, port80[0], port80[1], '0.0.0.0:80')]
    if port443 is not None:
        result[443]=[runtime.Listener(443, port443[0], port443[1], '0.0.0.0:443')]
    return result


class CollisionRegression(unittest.TestCase):
    def test_ss_detects_ipv4_ipv6_process_and_docker_conflict(self):
        output=('LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=151,fd=4))\n'
                'LISTEN 0 128 [::]:443 [::]:* users:(("docker-proxy",pid=332,fd=5))\n')
        with mock.patch.object(runtime,'_call',return_value=SimpleNamespace(returncode=0,stdout=output)):
            ports=runtime.listeners()
        self.assertEqual(ports[80][0].name,'nginx')
        self.assertEqual(ports[443][0].name,'docker-proxy')
        self.assertIn('docker-proxy PID 332',runtime.summary(ports))

    def test_ss_without_process_details_must_not_appear_free(self):
        output='LISTEN 0 128 0.0.0.0:443 0.0.0.0:*\n'
        with mock.patch.object(runtime,'_call',return_value=SimpleNamespace(returncode=0,stdout=output)):
            ports=runtime.listeners()
        self.assertEqual(ports[443][0].name,'propriétaire non visible')

    def test_refuse_docker_443_without_running_any_command(self):
        calls=[]
        with self.assertRaisesRegex(runtime.NginxRuntimeError,'docker-proxy'):
            runtime.guarded_recover_nginx(socket_scan=lambda:scan(port443=('docker-proxy',4096)),
                command=lambda argv,description:calls.append(argv))
        self.assertFalse(calls)

    def test_refuse_orphan_nginx_443_without_killing_it(self):
        with self.assertRaisesRegex(runtime.NginxRuntimeError,'TCP/443 déjà occupé'):
            runtime.guarded_recover_nginx(socket_scan=lambda:scan(port443=('nginx',333)),
                command=lambda argv,description: self.fail('should not intervene'))

    def test_refuse_foreign_http_port(self):
        with self.assertRaisesRegex(runtime.NginxRuntimeError,'TCP/80 occupé'):
            runtime.guarded_recover_nginx(socket_scan=lambda:scan(port80=('apache2',45)),
                command=lambda argv,description:self.fail('no change'))

    def test_reload_then_restart_if_443_still_absent(self):
        calls=[]
        samples=iter([scan(),scan(),scan(),scan(port443=('nginx',901))])
        with mock.patch.object(runtime,'_call',return_value=SimpleNamespace(returncode=0)):
            answer=runtime.guarded_recover_nginx(socket_scan=lambda:next(samples),
                command=lambda argv,description:calls.append(argv))
        self.assertIn('redémarré',answer)
        self.assertEqual(calls,[['nginx','-t'],['systemctl','reload','nginx'],['systemctl','restart','nginx']])

    def test_reload_that_fixes_listener_does_not_restart(self):
        calls=[]
        samples=iter([scan(),scan(port443=('nginx',901))])
        with mock.patch.object(runtime,'_call',return_value=SimpleNamespace(returncode=0)):
            answer=runtime.guarded_recover_nginx(socket_scan=lambda:next(samples),
                command=lambda argv,description:calls.append(argv))
        self.assertIn('rechargé',answer)
        self.assertEqual(calls,[['nginx','-t'],['systemctl','reload','nginx']])

    def test_restart_with_no_443_does_not_claim_success(self):
        calls=[]
        with mock.patch.object(runtime,'_call',return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(runtime.NginxRuntimeError,'aucun listener TCP/443'):
                runtime.guarded_recover_nginx(socket_scan=lambda:scan(),
                    command=lambda argv,description:calls.append(argv))
        self.assertEqual(calls[-1],['systemctl','restart','nginx'])

    def test_nginx_syntax_failure_stops_before_reload(self):
        def fail(argv,description):
            raise runtime.NginxRuntimeError('syntaxe invalide')
        with self.assertRaisesRegex(runtime.NginxRuntimeError,'syntaxe invalide'):
            runtime.guarded_recover_nginx(socket_scan=lambda:scan(),command=fail)

    def test_foreign_listener_created_during_reload_refused(self):
        samples=iter([scan(),scan(port443=('docker-proxy',17))])
        with mock.patch.object(runtime,'_call',return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(runtime.NginxRuntimeError,'service étranger après reload'):
                runtime.guarded_recover_nginx(socket_scan=lambda:next(samples),command=lambda argv,description:None)

    def test_foreign_listener_after_restart_refused(self):
        samples=iter([scan(),scan(),scan(),scan(port443=('docker-proxy',17))])
        with mock.patch.object(runtime,'_call',return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(runtime.NginxRuntimeError,'pas par Nginx'):
                runtime.guarded_recover_nginx(socket_scan=lambda:next(samples),command=lambda argv,description:None)

    def test_post_reload_foreign_443_rejected(self):
        samples=iter([scan(),scan(),scan(port443=('docker-proxy',17))])
        with mock.patch.object(runtime,'_call',return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(runtime.NginxRuntimeError,'Conflit détecté après reload'):
                runtime.guarded_recover_nginx(socket_scan=lambda:next(samples),command=lambda argv,description:None)

    def test_completed_ingress_does_not_restart_nginx(self):
        validated=[]
        result=runtime.ensure_validated_ingress([('prometheus','prometheus.ikom.mg')],
            verifier=lambda domains:validated.append(domains),
            recovery=lambda:self.fail('must not restart'))
        self.assertEqual(result,'conforme')
        self.assertEqual(len(validated),1)

    def test_recovery_runs_once_and_rechecks_https(self):
        from autokom_ingress_health import IngressError
        calls=[]
        def verify(domains):
            calls.append('probe')
            if len(calls)==1:raise IngressError('443 refusé')
        result=runtime.ensure_validated_ingress([('grafana','grafana.ikom.mg')],
            verifier=verify,recovery=lambda:(calls.append('recover') or 'redémarré'))
        self.assertEqual(result,'redémarré')
        self.assertEqual(calls,['probe','recover','probe'])

    def test_failed_post_recovery_check_stays_failed(self):
        from autokom_ingress_health import IngressError
        with self.assertRaisesRegex(runtime.NginxRuntimeError,'non validée'):
            runtime.ensure_validated_ingress([('grafana','grafana.ikom.mg')],
                verifier=lambda domains:(_ for _ in ()).throw(IngressError('502')),
                recovery=lambda:'rechargé')

    def test_conflict_stops_repeat_verification(self):
        from autokom_ingress_health import IngressError
        with self.assertRaisesRegex(runtime.NginxRuntimeError,'docker-proxy'):
            runtime.ensure_validated_ingress([('prometheus','prometheus.ikom.mg')],
                verifier=lambda _:(_ for _ in ()).throw(IngressError('443')),
                recovery=lambda:(_ for _ in ()).throw(runtime.NginxRuntimeError('docker-proxy')))

    def test_tls_probes_still_require_real_https_and_redirect(self):
        with self.assertRaisesRegex(ingress.IngressError,'HTTPS/SNI'):
            ingress.verify_observability([('prometheus','prometheus.ikom.mg')],
                probe=lambda _: (0,'',('127.0.0.1: ConnectionRefusedError',)),
                http_probe=lambda _: (200,'127.0.0.1',()))


if __name__=='__main__':unittest.main()
