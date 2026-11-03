"""Health/readiness of AUTOKOM Platform, independent from client accounts."""
from django.db import connection
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from config.swagger.health import health_schema


class PlatformHealthAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @health_schema
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
            return Response({
                'success': True,
                'message': 'AUTOKOM Platform opérationnelle.',
                'data': {'status': 'ok'},
                'errors': None,
            }, status=200)
        except Exception:
            # Deliberately do not expose database or internal service details.
            return Response({
                'success': False,
                'message': 'Service indisponible.',
                'data': None,
                'errors': None,
            }, status=503)
