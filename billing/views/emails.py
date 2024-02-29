from django.conf import settings
import requests
import django_keycloak_auth.clients

def send_email(data: dict, user = None, email = None):
    request = {
        "template_id": settings.LISTMONK_TEMPLATE_ID,
        "from_email": settings.DEFAULT_FROM_EMAIL,
        "data": data,
        "headers": [{
            "Reply-To": "Glauca Support <hello@glauca.digital>",
            "Bcc": "email-log@as207960.net"
        }]
    }
    request["data"]["service"] = "Glauca Billing"

    if user:
        if user.oidc_profile.id_data and user.oidc_profile.id_data.get("listmonk_user_id"):
            request["subscriber_id"] = user.oidc_profile.id_data["listmonk_user_id"]
        else:
            request["subscriber_email"] = user.email
    elif email:
        request["subscriber_email"] = email

    access_token = django_keycloak_auth.clients.get_access_token()
    r = requests.post(
        f"{settings.LISTMONK_URL}/api/tx",
        json=request,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
    )
    r.raise_for_status()