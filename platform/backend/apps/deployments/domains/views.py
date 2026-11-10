"""APIView only; one HTTP verb per class; object-level authorization on every view."""
from rest_framework.views import APIView
from rest_framework.parsers import JSONParser
from rest_framework.throttling import ScopedRateThrottle
from apps.accounts.user.permissions import IsClientUser
from apps.projects.common import get_environment
from apps.deployments.domains.models import Domain
from apps.deployments.runs.autokom_socket import request_control, AutokomControlUnavailable
from apps.deployments.domains.serializers import DomainCreateSerializer
from apps.deployments.domains.services import claim_domain,domain_summary,verify_and_activate,choose_primary_domain,deactivate_domain,remove_domain,rename_pending_managed_domain
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
            # DNS records are public, credentials never cross the Unix broker socket.
            # Missing runtime setup must not prevent a user viewing existing domains.
            setup = None
            try:
                config = request_control('domain.config.check', {}, timeout=10)
                setup = {key:config[key] for key in ('ingress_ipv4','managed_suffix','dns_zone')
                         if isinstance(config.get(key),str) and config[key]}
                setup['ready'] = config.get('ready') is True
            except AutokomControlUnavailable:
                pass
            return api_success({'domains':results,'dns_setup':setup},'Domaines liés à cet environnement.')
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


class DomainPrimaryAPIView(APIView):
    permission_classes=[IsClientUser]
    parser_classes=[JSONParser]
    throttle_classes=[ScopedRateThrottle]
    throttle_scope='domains'

    def post(self,request,project_id,environment_id,domain_id):
        try:
            d=choose_primary_domain(request.user,project_id,environment_id,domain_id)
            return api_success({'domain':domain_summary(d)}, 'Domaine principal mis à jour.')
        except Exception as exc:
            return handle_error(exc,'domain_primary')


class DomainDeactivateAPIView(APIView):
    permission_classes=[IsClientUser]
    parser_classes=[JSONParser]
    throttle_classes=[ScopedRateThrottle]
    throttle_scope='domains'

    def post(self,request,project_id,environment_id,domain_id):
        try:
            d=deactivate_domain(request.user,project_id,environment_id,domain_id)
            return api_success({'domain':domain_summary(d)}, 'Domaine désactivé. Son nom reste réservé.')
        except Exception as exc:
            return handle_error(exc,'domain_deactivate')


class DomainDeleteAPIView(APIView):
    permission_classes = [IsClientUser]
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'domains'

    def delete(self, request, project_id, environment_id, domain_id):
        try:
            remove_domain(request.user, project_id, environment_id, domain_id)
            return api_success(None, 'Domaine supprimé définitivement de ce projet.')
        except Exception as exc:
            return handle_error(exc, 'domain_delete')


class DomainRenameAPIView(APIView):
    permission_classes = [IsClientUser]
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'domains'

    def patch(self, request, project_id, environment_id, domain_id):
        try:
            from apps.deployments.domains.serializers import DomainRenameSerializer
            serializer = DomainRenameSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            domain = rename_pending_managed_domain(
                request.user, project_id, environment_id, domain_id,
                serializer.validated_data['hostname'])
            return api_success({'domain': domain_summary(domain, include_challenge=True)},
                               'Préfixe du domaine modifié.')
        except Exception as exc:
            return handle_error(exc, 'domain_rename')
