"""APIView only; one HTTP verb per class; object-level authorization on every view."""
from rest_framework.views import APIView
from rest_framework.parsers import JSONParser
from rest_framework.throttling import ScopedRateThrottle
from apps.accounts.user.permissions import IsClientUser
from apps.projects.common import get_environment
from apps.deployments.domains.models import Domain
from apps.deployments.domains.serializers import DomainCreateSerializer
from apps.deployments.domains.services import claim_domain,domain_summary,verify_and_activate
from apps.deployments.domains.swagger.domain_list import schema as list_schema
from apps.deployments.domains.swagger.domain_create import schema as create_schema
from apps.deployments.domains.swagger.domain_verify import schema as verify_schema
from helpers.responses import api_success
from helpers.errors import handle_error


class DomainListAPIView(APIView):
    permission_classes=[IsClientUser]
    parser_classes=[JSONParser]
    throttle_classes=[ScopedRateThrottle]
    throttle_scope='domains'

    @list_schema
    def get(self,request,project_id,environment_id):
        try:
            _,env=get_environment(request.user,project_id,environment_id)
            results=[domain_summary(d,include_challenge=True) for d in Domain.objects.filter(environment=env).order_by('created_at')[:100]]
            return api_success({'domains':results},'Domaines liés à cet environnement.')
        except Exception as exc:
            return handle_error(exc,'domain_list')


class DomainCreateAPIView(APIView):
    permission_classes=[IsClientUser]
    parser_classes=[JSONParser]
    throttle_classes=[ScopedRateThrottle]
    throttle_scope='domains'

    @create_schema
    def post(self,request,project_id,environment_id):
        try:
            serializer=DomainCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            d=claim_domain(request.user,project_id,environment_id,**serializer.validated_data)
            return api_success({'domain':domain_summary(d,include_challenge=True)},
                               'Domaine réservé. Vérifier sa propriété et son pointage DNS.',201)
        except Exception as exc:
            return handle_error(exc,'domain_create')


class DomainVerifyAPIView(APIView):
    permission_classes=[IsClientUser]
    parser_classes=[JSONParser]
    throttle_classes=[ScopedRateThrottle]
    throttle_scope='domains'

    @verify_schema
    def post(self,request,project_id,environment_id,domain_id):
        try:
            d=verify_and_activate(request.user,project_id,environment_id,domain_id)
            message=('HTTPS installé et domaine actif.' if d.status==Domain.Status.ACTIVE else
                     'DNS vérifié. Activation HTTPS après le premier déploiement réussi.')
            return api_success({'domain':domain_summary(d)},message)
        except Exception as exc:
            return handle_error(exc,'domain_verify')
