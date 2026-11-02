from django.contrib import admin
from apps.deployments.domains.models import Domain, DomainVerification, DomainBinding, TLSCertificate


admin.site.register([Domain, DomainVerification, DomainBinding, TLSCertificate])
