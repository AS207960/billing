import plaid
import zeep
import gocardless_pro
from django.conf import settings
from django.apps import AppConfig
import as207960_utils.rpc

# plaid_client = plaid.Client(
#     client_id=settings.PLAID_CLIENT_ID,
#     secret=settings.PLAID_SECRET,
#     environment=settings.PLAID_ENV,
#     api_version='2019-05-29'
# )
gocardless_client = gocardless_pro.Client(access_token=settings.GOCARDLESS_TOKEN, environment=settings.GOCARDLESS_ENV)
vies_client = zeep.Client("https://ec.europa.eu/taxation_customs/vies/checkVatService.wsdl")
rpc_client = as207960_utils.rpc.RpcClient()


class BillingConfig(AppConfig):
    name = 'billing'
