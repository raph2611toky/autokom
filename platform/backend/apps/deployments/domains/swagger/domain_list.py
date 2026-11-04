from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.deployments.domains.swagger.schema import DOMAIN,envelope,ERROR
schema=swagger_auto_schema(operation_summary='Liste des domaines vérifiés ou en attente',
  responses={200:openapi.Response('Liste',envelope(openapi.Schema(type=openapi.TYPE_OBJECT,properties={
     'domains':openapi.Schema(type=openapi.TYPE_ARRAY,items=DOMAIN)}))),403:ERROR})
