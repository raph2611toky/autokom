"""Contrat landing -> POST /api/contact/ -> EMAIL_SUPPORT sans fuite de secrets."""
import json
from unittest.mock import patch

from django.core import mail
from django.test import Client, SimpleTestCase, override_settings
from django.core.cache import cache

URL = '/api/contact/'
CSRF = '/api/auth/browser/csrf/'
ORIGIN = 'https://auto.ikom.mg'
VALID = {'name': 'Exemple Client', 'email': 'client@example.com',
         'subject': 'Problème technique', 'message': 'Bonjour, mon projet rencontre un problème.'}


@override_settings(
    EMAIL_SUPPORT='support@example.com', DEFAULT_FROM_EMAIL='noreply@autokom.example',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    CSRF_TRUSTED_ORIGINS=[ORIGIN],
)
class ContactEndpointTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.browser = Client(enforce_csrf_checks=True)
        self.browser.get(CSRF, secure=True)

    def post(self, payload=None, client=None, token=True):
        client = client or self.browser
        kwargs = {'HTTP_ORIGIN': ORIGIN}
        if token and 'csrftoken' in client.cookies:
            kwargs['HTTP_X_CSRFTOKEN'] = client.cookies['csrftoken'].value
        return client.post(URL, data=json.dumps(VALID if payload is None else payload),
                           content_type='application/json', secure=True, **kwargs)

    def test_envoi_smtp_et_reply_to_sans_imitation_de_l_expediteur(self):
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['support@example.com'])
        self.assertEqual(mail.outbox[0].from_email, 'noreply@autokom.example')
        self.assertEqual(mail.outbox[0].reply_to, ['client@example.com'])
        self.assertIn('Bonjour, mon projet', mail.outbox[0].body)
        self.assertNotIn('client@example.com', response.content.decode())

    def test_csrf_obligatoire_meme_anonyme(self):
        response = self.post(client=Client(enforce_csrf_checks=True), token=False)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_envoi_sans_compte_et_piege_antibot(self):
        response = self.post({**VALID, 'website': 'https://spam.example'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_validation_email_sujet_taille_et_injection(self):
        for payload in [
            {**VALID, 'email': 'bad-address'},
            {**VALID, 'subject': 'urgent\nBcc: example@evil.test'},
            {**VALID, 'message': 'x'*4001},
            {**VALID, 'name': 'a\nb'},
        ]:
            response = self.post(payload)
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_SUPPORT='')
    def test_destinataire_absent_fail_closed(self):
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(mail.outbox), 0)

    def test_echec_smtp_ne_confirme_pas_un_envoi(self):
        with patch('config.contact.EmailMessage.send', side_effect=OSError('smtp password should remain private')):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('password', response.content.decode())

    def test_get_refuse(self):
        self.assertEqual(self.browser.get(URL, secure=True).status_code, 405)

    def test_limite_des_demandes(self):
        for _ in range(4):
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post().status_code, 429)
        self.assertEqual(len(mail.outbox), 4)

    def test_ip_proxy_local_ne_fait_pas_confiance_au_xff_arbitraire(self):
        from config.contact import ContactRateThrottle
        throttle = ContactRateThrottle()
        class Request:
            META = {'REMOTE_ADDR': '127.0.0.1', 'HTTP_X_FORWARDED_FOR': '203.0.113.3, 127.0.0.1', 'HTTP_X_REAL_IP': '198.51.100.8'}
        self.assertEqual(throttle.get_ident(Request()), '198.51.100.8')
        Request.META = {'REMOTE_ADDR': '192.0.2.5', 'HTTP_X_FORWARDED_FOR': '9.9.9.9', 'HTTP_X_REAL_IP': '8.8.8.8'}
        self.assertEqual(throttle.get_ident(Request()), '192.0.2.5')
