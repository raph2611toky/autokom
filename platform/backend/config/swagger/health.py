"""Exact OpenAPI schemas for PlatformHealthAPIView GET responses."""
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema


health_schema = swagger_auto_schema(
    operation_summary='État de santé du backend AUTOKOM',
    operation_description='Vérifie la disponibilité du backend et de sa base MySQL.',
    security=[],
    responses={
        200: openapi.Response(description='Service disponible', schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['success', 'message', 'data', 'errors'],
            properties={
                'success': openapi.Schema(type=openapi.TYPE_BOOLEAN, enum=[True]),
                'message': openapi.Schema(type=openapi.TYPE_STRING, enum=['AUTOKOM Platform opérationnelle.']),
                'data': openapi.Schema(type=openapi.TYPE_OBJECT, required=['status'], properties={
                    'status': openapi.Schema(type=openapi.TYPE_STRING, enum=['ok']),
                }),
                'errors': openapi.Schema(type=openapi.TYPE_STRING, x_nullable=True),
            },
        )),
        503: openapi.Response(description='Service indisponible', schema=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['success', 'message', 'data', 'errors'],
            properties={
                'success': openapi.Schema(type=openapi.TYPE_BOOLEAN, enum=[False]),
                'message': openapi.Schema(type=openapi.TYPE_STRING, enum=['Service indisponible.']),
                'data': openapi.Schema(type=openapi.TYPE_OBJECT, x_nullable=True),
                'errors': openapi.Schema(type=openapi.TYPE_STRING, x_nullable=True),
            },
        )),
    },
)
