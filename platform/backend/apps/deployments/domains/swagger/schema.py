from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

DOMAIN=openapi.Schema(type=openapi.TYPE_OBJECT,properties={
    'id':openapi.Schema(type=openapi.TYPE_STRING,format=openapi.FORMAT_UUID),
    'hostname':openapi.Schema(type=openapi.TYPE_STRING),
    'type':openapi.Schema(type=openapi.TYPE_STRING),
    'status':openapi.Schema(type=openapi.TYPE_STRING),
    'verified':openapi.Schema(type=openapi.TYPE_BOOLEAN),
    'https':openapi.Schema(type=openapi.TYPE_BOOLEAN)})

def envelope(data):
    return openapi.Schema(type=openapi.TYPE_OBJECT,properties={
       'success':openapi.Schema(type=openapi.TYPE_BOOLEAN),
       'message':openapi.Schema(type=openapi.TYPE_STRING),
       'data':data,
       'errors':openapi.Schema(type=openapi.TYPE_OBJECT,x_nullable=True)})
ERROR=envelope(openapi.Schema(type=openapi.TYPE_OBJECT,x_nullable=True))
