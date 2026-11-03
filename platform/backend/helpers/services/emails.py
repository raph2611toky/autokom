import logging
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

def send_template_email(*, subject, to, template, context):
    html = render_to_string(template, context)
    send_mail(subject, '', settings.DEFAULT_FROM_EMAIL, [to], html_message=html, fail_silently=False)
