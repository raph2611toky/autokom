from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from apps.deployments.domains.swagger.schema import DOMAIN,envelope,ERROR
schema=swagger_auto_schema(operation_summary='Réserver un domaine, sans activation DNS implicite',
  request_body=openapi.Schema(type=openapi.TYPE_OBJECT,required=['hostname','domain_type'],properties={
     'hostname':openapi.Schema(type=openapi.TYPE_STRING,example='monapp.apps.auto.ik0m.tech'),
     'domain_type':openapi.Schema(type=openapi.TYPE_STRING,enum=['SUBDOMAIN','CUSTOM'])}),
  responses={201:openapi.Response('Domaine enregistré',envelope(openapi.Schema(type=openapi.TYPE_OBJECT,properties={'domain':DOMAIN}))),
             400:ERROR,403:ERROR})
