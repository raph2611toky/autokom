from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.deployments.domains.swagger.schema import DOMAIN,envelope,ERROR
schema=swagger_auto_schema(operation_summary='Prouver propriété DNS et activer TLS si une release saine existe',
  request_body=openapi.Schema(type=openapi.TYPE_OBJECT,properties={}),
  responses={200:openapi.Response('Statut DNS/TLS',envelope(openapi.Schema(type=openapi.TYPE_OBJECT,properties={'domain':DOMAIN}))),
             400:ERROR,403:ERROR,503:ERROR})
