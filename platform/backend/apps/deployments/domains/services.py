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
    if domain_type == Domain.Type.SUBDOMAIN:
        suffix = settings.AUTOKOM_MANAGED_DOMAIN_SUFFIX.lower().strip('.')
        if host.count('.') != suffix.count('.') + 1:
            raise ValidationError({'hostname': 'Saisissez un seul préfixe avant auto.ikom.mg.'})
    if host in ('backend.auto.ik0m.tech','auto.ik0m.tech','backend.auto.ikom.mg','auto.ikom.mg','ikom.mg','www.ikom.mg'):
        raise ValidationError({'hostname':'Nom de domaine réservé au service IKOM.'})
    with transaction.atomic():
        # Serialize all managed-host reservations for this project, across
        # environments. The user must rename the existing host, not reserve
        # another AUTOKOM hostname for the same project.
        from apps.projects.project.models import Project
        if domain_type == Domain.Type.SUBDOMAIN:
            Project.objects.select_for_update().get(pk=project.pk)
            if Domain.objects.filter(environment=env, domain_type=Domain.Type.SUBDOMAIN).exists():
                raise ValidationError({'hostname':
                    'Ce service possède déjà un sous-domaine AUTOKOM. Utilisez Modifier sur ce domaine.'})
            # For ordinary projects, the managed address belongs to the project.
            # A two-service stack is the one exception: the backend uses its
            # own environment and may need its own hostname. Do not regress
            # parallel frontend/backend deployments.
            from apps.projects.configurations.models import ServiceStack
            is_stack_backend = ServiceStack.objects.filter(
                project=project, backend_environment=env).exists()
            if not is_stack_backend and Domain.objects.filter(
                    environment__project=project, domain_type=Domain.Type.SUBDOMAIN).exists():
                raise ValidationError({'hostname':
                    'Ce projet possède déjà un sous-domaine AUTOKOM. Utilisez Modifier sur ce domaine.'})
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
             'activation_error_code': domain.activation_error_code,
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
                        'environment_id':str(env.id),'managed':managed,'token':token},timeout=720)
        domain.status=Domain.Status.ACTIVE
        domain.tls_status='ACTIVE'
        if not Domain.objects.filter(environment=env, is_primary=True).exclude(pk=domain.pk).exists():
            domain.is_primary=True
        domain.activation_error_code=''
        domain.save(update_fields=['status','tls_status','is_primary','activation_error_code'])
    return domain


def activate_managed_domains_for_release(environment):
    """After a healthy Docker RUN, publish reserved managed hosts before committing the release.

    Fail closed: an unpublished DNS/HTTPS domain must not be declared public by AUTOKOM.
    Custom domains remain user-verified via the dedicated endpoint.
    """
    from apps.deployments.domains.models import Domain
    for domain in Domain.objects.filter(environment=environment, domain_type=Domain.Type.SUBDOMAIN, is_disabled=False).order_by('created_at'):
        token = _challenge(domain)
        # Older projects reserved a managed name without a proof hash. Repair
        # only never-verified, pending AUTOKOM-owned reservations.
        if (not domain.verification_token_hash and domain.status == Domain.Status.PENDING
                and domain.verified_at is None):
            domain.verification_token_hash = hashlib.sha256(token.encode()).hexdigest()
            domain.save(update_fields=['verification_token_hash'])
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
        }, timeout=720)
        domain.verified_at=timezone.now()
        domain.status=Domain.Status.ACTIVE
        domain.tls_status='ACTIVE'
        if not Domain.objects.filter(environment=environment, is_primary=True).exclude(pk=domain.pk).exists():
            domain.is_primary=True
        domain.activation_error_code=''
        domain.save(update_fields=['verified_at','status','tls_status','is_primary','activation_error_code'])


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
    domain.activation_error_code=''
    domain.verified_at=None
    domain.save(update_fields=['status','tls_status','verified_at','is_disabled','activation_error_code'])
    return domain


def activate_stack_domain_for_release(environment, hostname):
    """Activate one owned stack name; do not affect legacy domains or aliases."""
    if not hostname:
        raise ValidationError({'domain': 'Nom de domaine du service absent.'})
    domain = Domain.objects.filter(environment=environment, hostname=hostname, is_disabled=False).first()
    if domain is None:
        raise ValidationError({'domain': 'Domaine non réservé à ce service.'})
    token = _challenge(domain)
    managed = _managed(domain.hostname)
    if (domain.domain_type == Domain.Type.SUBDOMAIN) != managed:
        raise ValidationError({'domain': 'Type DNS non conforme.'})
    if managed:
        request_control('domain.dns.create', {'hostname': domain.hostname}, timeout=22)
    # DNS A + ownership TXT (external) are checked before HTTPS certificate issuance.
    request_control('domain.verify', {'hostname': domain.hostname,
        'managed': managed, 'token': token}, timeout=20)
    request_control('domain.activate', {'hostname': domain.hostname,
        'environment_id': str(environment.id), 'managed': managed, 'token': token}, timeout=720)
    domain.verified_at = timezone.now()
    domain.status = Domain.Status.ACTIVE
    domain.tls_status = 'ACTIVE'
    domain.activation_error_code = ''
    domain.save(update_fields=['verified_at', 'status', 'tls_status', 'activation_error_code'])
    return domain


def _reject_domain_mutation_during_deployment(environment):
    """Do not mutate domain ownership while workers may publish that hostname."""
    from django.db.models import Q
    from apps.deployments.runs.models import DeploymentRun, StackDeployment
    if DeploymentRun.objects.filter(environment=environment, status__in=['QUEUED', 'RUNNING', 'PREPARED']).exists():
        raise ValidationError({'domain': 'Publication en cours : réessayez une fois le déploiement terminé.'})
    if StackDeployment.objects.filter(
        Q(stack__frontend_environment=environment) | Q(stack__backend_environment=environment),
        status__in=['QUEUED', 'PREPARING', 'ACTIVATING', 'BLOCKED'],
    ).exists():
        raise ValidationError({'domain': 'Publication groupée en cours ou à réconcilier.'})


def remove_domain(user, project_id, environment_id, domain_id):
    """Permanently remove non-primary aliases, keeping at least one address.

    Do not delete the database reservation unless the privileged broker confirms
    Nginx teardown and (for managed hosts) owned DNS record cleanup.
    """
    _, env = get_environment(user, project_id, environment_id, write=True)
    with transaction.atomic():
        _reject_domain_mutation_during_deployment(env)
        aliases = Domain.objects.select_for_update().filter(environment=env)
        domain = aliases.filter(pk=domain_id).first()
        if domain is None:
            raise ValidationError({'domain': 'Domaine introuvable.'})
        if aliases.count() <= 1:
            raise ValidationError({'domain': 'Impossible de supprimer le dernier domaine : au moins une adresse doit rester associée à ce service.'})
        if domain.is_primary:
            raise ValidationError({'domain': 'Définissez un autre domaine principal avant de supprimer celui-ci.'})
        request_control('domain.remove', {
            'hostname': domain.hostname, 'environment_id': str(env.pk),
            'managed': domain.domain_type == Domain.Type.SUBDOMAIN,
        }, timeout=90)
        domain.delete()


def check_managed_domain_availability(user, project_id, environment_id, domain_id, hostname):
    """Check application ownership AND the authoritative DNS provider, without writes.

    This is advisory only. The mutating endpoint checks again, because another
    request can reserve the name after an availability lookup.
    """
    _, env = get_environment(user, project_id, environment_id, write=True)
    host = _validate_managed_rename_host(hostname)
    original = Domain.objects.filter(environment=env, pk=domain_id).first()
    if original is None or original.domain_type != Domain.Type.SUBDOMAIN:
        raise ValidationError({'domain': 'Sous-domaine AUTOKOM introuvable.'})
    if original.hostname == host:
        return {'available': False, 'reason': 'unchanged'}
    if Domain.objects.filter(hostname=host).exists():
        return {'available': False, 'reason': 'reserved'}
    # The broker knows about Cloudflare A/CNAME records and protected Nginx vhosts.
    result = request_control('domain.available', {'hostname': host}, timeout=18)
    return {'available': result.get('available') is True,
            'reason': str(result.get('reason', 'unavailable'))[:40]}


def _validate_managed_rename_host(hostname):
    host = validate_hostname(hostname)
    suffix = settings.AUTOKOM_MANAGED_DOMAIN_SUFFIX.lower().strip('.')
    if (not _managed(host) or host.count('.') != suffix.count('.') + 1):
        raise ValidationError({'hostname': 'Seul le préfixe avant auto.ikom.mg peut être modifié.'})
    if host in ('backend.auto.ikom.mg', 'auto.ikom.mg'):
        raise ValidationError({'hostname': 'Nom réservé au service IKOM.'})
    return host


def rename_pending_managed_domain(user, project_id, environment_id, domain_id, hostname):
    """Safely rename pending domains, or migrate an ACTIVE domain over HTTPS.

    For active domains publish/verify a new alias first, switch the primary
    only after its HTTPS probe succeeds, then remove the old alias. On failure
    the existing public address remains online; any uncleared alias remains
    visible in the database for administrator reconciliation.
    """
    project, env = get_environment(user, project_id, environment_id, write=True)
    host = _validate_managed_rename_host(hostname)
    with transaction.atomic():
        from apps.projects.project.models import Project
        Project.objects.select_for_update().get(pk=project.pk)
        _reject_domain_mutation_during_deployment(env)
        domain = Domain.objects.select_for_update().filter(environment=env, pk=domain_id).first()
        if domain is None or domain.domain_type != Domain.Type.SUBDOMAIN:
            raise ValidationError({'domain': 'Sous-domaine AUTOKOM introuvable.'})
        if host == domain.hostname:
            return {'domain': domain, 'previous_retained': False, 'activated': domain.status == Domain.Status.ACTIVE}
        if Domain.objects.filter(hostname=host).exists():
            raise ValidationError({'hostname': 'Ce sous-domaine est déjà réservé.'})
        active = (domain.status == Domain.Status.ACTIVE and
                  domain.tls_status == 'ACTIVE' and domain.verified_at is not None and
                  not domain.is_disabled)
        pending = (domain.status == Domain.Status.PENDING and
                   domain.verified_at is None and not domain.tls_status and
                   not domain.is_disabled)
        if not (active or pending):
            raise ValidationError({'domain': 'Adresse dans un état incompatible avec une modification.'})
        available = request_control('domain.available', {'hostname': host}, timeout=18)
        if available.get('available') is not True:
            raise ValidationError({'hostname': 'Ce préfixe est déjà utilisé. Choisissez-en un autre.'})
        if pending:
            # No active DNS/HTTPS will be taken offline. Remove only an owned
            # pending record, and only then rename this reservation.
            request_control('domain.remove', {
                'hostname': domain.hostname, 'environment_id': str(env.pk), 'managed': True,
            }, timeout=90)
            domain.hostname = host
            domain.activation_error_code = ''
            domain.save(update_fields=['hostname', 'activation_error_code', 'updated_at'])
            return {'domain': domain, 'previous_retained': False, 'activated': False}

        # Reserve in DB before the slow Cloudflare + ACME operations to stop
        # concurrent projects from taking the hostname. No existing site is changed.
        staged = Domain.objects.create(
            environment=env, organization=project.organization,
            hostname=host, domain_type=Domain.Type.SUBDOMAIN,
            status=Domain.Status.VERIFYING, is_primary=False, is_disabled=True,
        )
        staged.verification_token_hash = hashlib.sha256(_challenge(staged).encode()).hexdigest()
        staged.save(update_fields=['verification_token_hash'])
        old_id = domain.pk
        old_host = domain.hostname

    token = _challenge(staged)
    try:
        request_control('domain.dns.create', {'hostname': host}, timeout=22)
        # DNS takes time to propagate; retry only the known propagation failure.
        from apps.deployments.runs.autokom_socket import AutokomControlUnavailable
        import time
        for attempt in range(3):
            try:
                request_control('domain.verify', {'hostname': host, 'managed': True, 'token': token}, timeout=20)
                break
            except AutokomControlUnavailable as exc:
                if getattr(exc, 'safe_code', None) != 'AUTOKOM_DNS_A_MISMATCH' or attempt == 2:
                    raise
                time.sleep((attempt + 1) * 3)
        # Broker verifies real HTTPS against the existing healthy environment.
        request_control('domain.activate', {
            'hostname': host, 'environment_id': str(env.pk),
            'managed': True, 'token': token,
        }, timeout=720)
    except Exception:
        # Cleanup only our just-staged name, NEVER the original live address.
        try:
            request_control('domain.remove', {
                'hostname': host, 'environment_id': str(env.pk), 'managed': True,
            }, timeout=90)
        except Exception:
            # Preserve the reservation so it cannot be claimed by another user.
            staged.status = Domain.Status.ERROR
            staged.activation_error_code = 'AUTOKOM_DOMAIN_RENAME_RECONCILE_REQUIRED'
            staged.save(update_fields=['status', 'activation_error_code'])
        else:
            staged.delete()
        raise

    # The new HTTPS endpoint is live: atomically switch primary ownership.
    try:
        with transaction.atomic():
            _reject_domain_mutation_during_deployment(env)
            original = Domain.objects.select_for_update().get(pk=old_id, environment=env)
            replacement = Domain.objects.select_for_update().get(pk=staged.pk, environment=env)
            was_primary = original.is_primary
            # Prevent scheduled domain reconciliation from republishing the old
            # alias while its owned DNS and vhost are being retired.
            original.is_primary = False
            original.is_disabled = True
            original.save(update_fields=['is_primary', 'is_disabled'])
            if was_primary:
                replacement.is_primary = True
            replacement.verified_at = timezone.now()
            replacement.status = Domain.Status.ACTIVE
            replacement.tls_status = 'ACTIVE'
            replacement.is_disabled = False
            replacement.activation_error_code = ''
            replacement.save(update_fields=[
                'is_primary', 'verified_at', 'status', 'tls_status', 'is_disabled', 'activation_error_code',
            ])
    except Exception:
        # A deployment may have started while ACME was running. If the database
        # cannot switch, the original domain remains primary and reachable.
        try:
            request_control('domain.remove', {
                'hostname': host, 'environment_id': str(env.pk), 'managed': True,
            }, timeout=90)
        except Exception:
            staged.status = Domain.Status.ERROR
            staged.activation_error_code = 'AUTOKOM_DOMAIN_RENAME_RECONCILE_REQUIRED'
            staged.save(update_fields=['status', 'activation_error_code'])
        else:
            staged.delete()
        raise

    # Remove the old domain only after the new public site is demonstrably live.
    # Failure here must never roll back the new working primary or erase old DNS.
    try:
        request_control('domain.remove', {
            'hostname': old_host, 'environment_id': str(env.pk), 'managed': True,
        }, timeout=90)
    except Exception:
        original.activation_error_code = 'AUTOKOM_DOMAIN_OLD_ALIAS_CLEANUP_PENDING'
        original.save(update_fields=['activation_error_code'])
        return {'domain': replacement, 'previous_retained': True, 'activated': True}
    original.delete()
    return {'domain': replacement, 'previous_retained': False, 'activated': True}
