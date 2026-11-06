"""Domain ownership and publication state. Host DNS/proxy actions via AUTOKOM socket."""
import hashlib
import hmac
import re
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from apps.projects.common import get_environment, validate_hostname
from apps.deployments.domains.models import Domain
from apps.deployments.runs.autokom_socket import request_control


def _challenge(domain):
    # Deterministic, high-entropy DNS challenge, never sent to Jenkins or logs.
    return hmac.new(settings.SECRET_KEY.encode('utf-8'),
                    f'autokom-domain-proof:v1:{domain.pk}'.encode(),hashlib.sha256).hexdigest()


def _managed(name):
    suffix=settings.AUTOKOM_MANAGED_DOMAIN_SUFFIX.lower().strip('.')
    return bool(suffix and name.endswith('.'+suffix) and name!=suffix)


def claim_domain(user,project_id,environment_id,hostname,domain_type):
    project,env=get_environment(user,project_id,environment_id,write=True)
    host=validate_hostname(hostname)
    if domain_type not in (Domain.Type.SUBDOMAIN,Domain.Type.CUSTOM):
        raise ValidationError({'domain_type':'SUBDOMAIN ou CUSTOM attendu.'})
    if (domain_type==Domain.Type.SUBDOMAIN)!=_managed(host):
        raise ValidationError({'hostname':'Domaine géré AUTOKOM ou externe incohérent.'})
    if host in ('backend.auto.ik0m.tech','auto.ik0m.tech','backend.auto.ikom.mg','auto.ikom.mg','ikom.mg','www.ikom.mg'):
        raise ValidationError({'hostname':'Nom de domaine réservé au service IKOM.'})
    with transaction.atomic():
        if Domain.objects.filter(hostname=host).exists():
            raise ValidationError({'hostname':'Ce domaine est déjà enregistré.'})
        domain=Domain.objects.create(environment=env,organization=project.organization,
                 hostname=host,domain_type=domain_type,status=Domain.Status.PENDING)
        domain.verification_token_hash=hashlib.sha256(_challenge(domain).encode()).hexdigest()
        domain.save(update_fields=['verification_token_hash'])
    return domain


def domain_summary(domain,*,include_challenge=False):
    payload={'id':str(domain.id),'hostname':domain.hostname,'type':domain.domain_type,
             'status':domain.status,'verified':domain.verified_at is not None,
             'https':domain.tls_status=='ACTIVE' and domain.status==Domain.Status.ACTIVE,
             'tls_status':domain.tls_status, 'domain_type':domain.domain_type,
             'is_primary':domain.is_primary, 'is_disabled':domain.is_disabled,
             'url':('https://' + domain.hostname if domain.status==Domain.Status.ACTIVE and domain.tls_status=='ACTIVE' else None)}
    if include_challenge and domain.domain_type==Domain.Type.CUSTOM:
        payload['verification']={'type':'TXT','hostname':'_autokom-challenge.'+domain.hostname,
                                 'value':'autokom-verification='+_challenge(domain),
                                 'a_record_target':'IP publique de l’ingress AUTOKOM (voir paramètres de domaine)'}
    if include_challenge and domain.domain_type==Domain.Type.SUBDOMAIN:
        payload['verification']={'type':'MANAGED','provider':'Cloudflare (zone déjà déléguée et configurée)'}
    return payload


def verify_and_activate(user,project_id,environment_id,domain_id):
    _,env=get_environment(user,project_id,environment_id,write=True)
    domain=Domain.objects.filter(environment=env,id=domain_id).first()
    if not domain:raise ValidationError({'domain':'Domaine absent ou accès refusé.'})
    token=_challenge(domain)
    expected_hash=hashlib.sha256(token.encode()).hexdigest()
    # Repair legacy project-created domains that were never initialized.
    # Never repair domains that were already verified or activated.
    if not domain.verification_token_hash and domain.status==Domain.Status.PENDING and domain.verified_at is None:
        domain.verification_token_hash=expected_hash
        domain.save(update_fields=['verification_token_hash'])
    if not hmac.compare_digest(expected_hash,domain.verification_token_hash):
        raise PermissionDenied('Preuve DNS invalide ou clé serveur modifiée.')
    managed=domain.domain_type==Domain.Type.SUBDOMAIN
    if managed:
        # Refuse to overwrite another Cloudflare A record or activate before delegation.
        request_control('domain.dns.create',{'hostname':domain.hostname},timeout=22)
    request_control('domain.verify',{'hostname':domain.hostname,
                   'managed':managed,'token':token},timeout=20)
    domain.verified_at=timezone.now()
    domain.status=Domain.Status.VERIFYING
    domain.save(update_fields=['verified_at','status'])
    if domain.is_disabled:
        domain.is_disabled=False
        domain.save(update_fields=['is_disabled'])
    runtime=request_control('runtime.status',{'environment_id':str(env.id)},timeout=12).get('runtime')
    if runtime:
        request_control('domain.activate',{'hostname':domain.hostname,
                        'environment_id':str(env.id),'managed':managed,'token':token},timeout=175)
        domain.status=Domain.Status.ACTIVE
        domain.tls_status='ACTIVE'
        if not Domain.objects.filter(environment=env, is_primary=True).exclude(pk=domain.pk).exists():
            domain.is_primary=True
        domain.save(update_fields=['status','tls_status','is_primary'])
    return domain


def activate_managed_domains_for_release(environment):
    """After a healthy Docker RUN, publish reserved managed hosts before committing the release.

    Fail closed: an unpublished DNS/HTTPS domain must not be declared public by AUTOKOM.
    Custom domains remain user-verified via the dedicated endpoint.
    """
    from apps.deployments.domains.models import Domain
    for domain in Domain.objects.filter(environment=environment, domain_type=Domain.Type.SUBDOMAIN, is_disabled=False).order_by('created_at'):
        token = _challenge(domain)
        if not hmac.compare_digest(domain.verification_token_hash or '', hashlib.sha256(token.encode()).hexdigest()):
            raise ValidationError({'domain':'Empreinte de propriété AUTOKOM non conforme.'})
        request_control('domain.dns.create', {'hostname':domain.hostname}, timeout=22)
        # DNS propagation is eventually consistent: retry verification, never bypass it.
        import time
        from apps.deployments.runs.autokom_socket import AutokomControlUnavailable
        for attempt in range(4):
            try:
                request_control('domain.verify', {'hostname':domain.hostname, 'managed':True, 'token':token}, timeout=20)
                break
            except AutokomControlUnavailable:
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
        request_control('domain.activate', {
            'hostname':domain.hostname, 'environment_id':str(environment.id),
            'managed':True, 'token':token,
        }, timeout=175)
        domain.verified_at=timezone.now()
        domain.status=Domain.Status.ACTIVE
        domain.tls_status='ACTIVE'
        if not Domain.objects.filter(environment=environment, is_primary=True).exclude(pk=domain.pk).exists():
            domain.is_primary=True
        domain.save(update_fields=['verified_at','status','tls_status','is_primary'])


def choose_primary_domain(user, project_id, environment_id, domain_id):
    _, env = get_environment(user, project_id, environment_id, write=True)
    with transaction.atomic():
        domains = Domain.objects.select_for_update().filter(environment=env)
        chosen = domains.filter(pk=domain_id).first()
        if not chosen:
            raise ValidationError({'domain':'Domaine introuvable.'})
        if chosen.status != Domain.Status.ACTIVE or chosen.tls_status != 'ACTIVE':
            raise ValidationError({'domain':'Le domaine doit être accessible en HTTPS avant de devenir principal.'})
        domains.filter(is_primary=True).exclude(pk=chosen.pk).update(is_primary=False)
        if not chosen.is_primary:
            chosen.is_primary=True
            chosen.save(update_fields=['is_primary'])
    return chosen


def deactivate_domain(user, project_id, environment_id, domain_id):
    _, env = get_environment(user, project_id, environment_id, write=True)
    domain = Domain.objects.filter(environment=env, pk=domain_id).first()
    if not domain:
        raise ValidationError({'domain':'Domaine introuvable.'})
    if domain.is_primary:
        raise ValidationError({'domain':'Choisissez d’abord un autre domaine principal.'})
    # Keep reservation and ownership proof: a DNS entry may still point to AUTOKOM.
    # The broker will not remove any vhost without a matching ownership marker.
    request_control('domain.deactivate', {
        'hostname':domain.hostname, 'environment_id':str(env.pk),
    }, timeout=30)
    domain.status=Domain.Status.PENDING
    domain.is_disabled=True
    domain.tls_status=''
    domain.verified_at=None
    domain.save(update_fields=['status','tls_status','verified_at','is_disabled'])
    return domain
