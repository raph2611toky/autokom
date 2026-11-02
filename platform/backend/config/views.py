"""Pages système ; les API métier restent des APIView à méthode HTTP unique."""
from django.contrib.auth import authenticate, login, logout
from django.conf import settings
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from rest_framework.permissions import AllowAny
from rest_framework.authentication import SessionAuthentication
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.renderers import TemplateHTMLRenderer
from rest_framework.views import APIView


def safe_redirect_target(request):
    """Prevent open-redirects via the next parameter."""
    value = request.data.get('next', '') if request.method == 'POST' else request.query_params.get('next', '')
    if value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ) and value.startswith('/') and not value.startswith('//'):
        return value
    return '/api/docs/'


@method_decorator(never_cache, name='dispatch')
class SwaggerLoginPageAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    renderer_classes = [TemplateHTMLRenderer]

    def get(self, request):
        get_token(request._request)
        if request._request.user.is_authenticated and request._request.user.is_staff:
            return redirect(safe_redirect_target(request))
        return render(request._request, 'swagger_login.html', {
            'next': safe_redirect_target(request),
        })


@method_decorator(csrf_protect, name='dispatch')
@method_decorator(never_cache, name='dispatch')
class SwaggerLoginSubmitAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    renderer_classes = [TemplateHTMLRenderer]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        email = str(request.data.get('email', '')).strip()
        password = request.data.get('password', '')
        user = authenticate(request._request, username=email, password=password)
        if user is None or not user.is_active or not user.is_staff:
            return render(request._request, 'swagger_login.html', {
                'error': 'Identifiants administrateur incorrects.',
                'next': safe_redirect_target(request),
            }, status=401)
        login(request._request, user)
        return redirect(safe_redirect_target(request))


@method_decorator(csrf_protect, name='dispatch')
class SwaggerLogoutAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    renderer_classes = [TemplateHTMLRenderer]

    def post(self, request):
        logout(request._request)
        return redirect('/docs/login/')


def handle_404(request, exception):
    """Django's required URL handler: JSON for missing APIs, HTML elsewhere."""
    if request.path.startswith('/api/'):
        return JsonResponse({
            'success': False,
            'message': 'Route API introuvable.',
            'data': None,
            'errors': None,
        }, status=404)
    return render(request, '404.html', {'frontend_url': settings.FRONTEND_URL}, status=404)
