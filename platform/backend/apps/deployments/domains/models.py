import uuid
from django.db import models

class Domain(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    organization = models.ForeignKey('organizations.Organization', on_delete=models.PROTECT, related_name='domains')
    hostname = models.CharField(max_length=253, unique=True, help_text='Normalisé IDNA, en minuscules')
    kind = models.CharField(max_length=12, default='SUBDOMAIN')
    status = models.CharField(max_length=20, default='PENDING')
    verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'Domain : {self.kind}'

    class Meta:
        db_table = 'domains_domain'

class DomainVerification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    domain = models.ForeignKey('domains.Domain', on_delete=models.CASCADE, related_name='verifications')
    method = models.CharField(max_length=15, default='DNS_TXT')
    challenge_ref = models.CharField(max_length=400)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default='PENDING')

    def __str__(self):
        return f'DomainVerification : {self.status}'

    class Meta:
        db_table = 'domains_domainverification'

class DomainBinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    domain = models.ForeignKey('domains.Domain', on_delete=models.PROTECT, related_name='bindings')
    environment = models.ForeignKey('environments.Environment', on_delete=models.PROTECT, related_name='domain_bindings')
    target = models.ForeignKey('sites.HostedApplication', on_delete=models.PROTECT, null=True, blank=True)
    status = models.CharField(max_length=24, default='PENDING')
    activated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'DomainBinding : {self.status}'

    class Meta:
        db_table = 'domains_domainbinding'

class TLSCertificate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    domain = models.ForeignKey('domains.Domain', on_delete=models.CASCADE, related_name='certificates')
    issuer = models.CharField(max_length=180, blank=True)
    serial_fingerprint = models.CharField(max_length=128, blank=True)
    certificate_ref = models.CharField(max_length=700, help_text='Référence, jamais la clé privée')
    not_before = models.DateTimeField(null=True, blank=True)
    not_after = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=24, default='PENDING')

    def __str__(self):
        return f'TLSCertificate : {self.status}'

    class Meta:
        db_table = 'domains_tlscertificate'
