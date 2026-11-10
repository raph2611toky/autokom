from django.urls import path
from apps.plans.entitlements.views import SandboxAvailabilityAPIView, SandboxCheckoutAPIView, SandboxSubscriptionAPIView, SandboxAccountSubscriptionsAPIView

urlpatterns = [
    path('billing/sandbox/account/subscriptions/', SandboxAccountSubscriptionsAPIView.as_view(), name='billing-sandbox-account-subscriptions'),
    path('billing/sandbox/availability/', SandboxAvailabilityAPIView.as_view(), name='billing-sandbox-availability'),
    path('projects/<uuid:project_id>/environments/<uuid:environment_id>/billing/sandbox/checkout/',
         SandboxCheckoutAPIView.as_view(), name='billing-sandbox-checkout'),
    path('projects/<uuid:project_id>/environments/<uuid:environment_id>/billing/sandbox/subscription/',
         SandboxSubscriptionAPIView.as_view(), name='billing-sandbox-subscription'),
]
