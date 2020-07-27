import plaid
from django.conf import settings
from django.apps import AppConfig

# plaid_client = plaid.Client(
#     client_id=settings.PLAID_CLIENT_ID,
#     secret=settings.PLAID_SECRET,
#     environment=settings.PLAID_ENV,
#     api_version='2019-05-29'
# )


class BillingConfig(AppConfig):
    name = 'billing'
