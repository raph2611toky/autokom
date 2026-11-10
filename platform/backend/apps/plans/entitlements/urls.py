from django.urls import path
from apps.plans.entitlements.views import SandboxAvailabilityAPIView, SandboxCheckoutAPIView, SandboxSubscriptionAPIView, SandboxAccountSubscriptionsAPIView, AccountSubscriptionAPIView, AccountCheckoutAPIView

urlpatterns = [
    path('billing/account/', AccountSubscriptionAPIView.as_view(), name='billing-account'),
    path('billing/account/checkout/', AccountCheckoutAPIView.as_view(), name='billing-account-checkout'),
    path('billing/sandbox/account/subscriptions/', SandboxAccountSubscriptionsAPIView.as_view(), name='billing-sandbox-account-subscriptions'),
    path('billing/sandbox/availability/', SandboxAvailabilityAPIView.as_view(), name='billing-sandbox-availability'),
    path('projects/<uuid:project_id>/environments/<uuid:environment_id>/billing/sandbox/checkout/',
         SandboxCheckoutAPIView.as_view(), name='billing-sandbox-checkout'),
    path('projects/<uuid:project_id>/environments/<uuid:environment_id>/billing/sandbox/subscription/',
         SandboxSubscriptionAPIView.as_view(), name='billing-sandbox-subscription'),
]
