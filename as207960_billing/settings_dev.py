"""
Django settings for as207960_billing project.

Generated by 'django-admin startproject' using Django 3.0.5.

For more information on this file, see
https://docs.djangoproject.com/en/3.0/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/3.0/ref/settings/
"""

import os
import json
import stripe
import logging

logging.basicConfig(level=logging.INFO)

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = '#^k4a)pwd3$eet415_g%wehz2ytgzcl8p9kqbd!i3-av1f28k9'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ["*"]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django_keycloak_auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'crispy_forms',
    'mathfilters',
    'django_countries',
    'phonenumber_field',
    'billing'
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    "django_keycloak_auth.middleware.OIDCMiddleware",
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'idempotency_key.middleware.ExemptIdempotencyKeyMiddleware',
]

ROOT_URLCONF = 'as207960_billing.urls'

AUTHENTICATION_BACKENDS = ["django_keycloak_auth.auth.KeycloakAuthorization"]

LOGIN_URL = "oidc_login"
LOGOUT_REDIRECT_URL = "oidc_login"

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'as207960_billing.wsgi.application'


# Database
# https://docs.djangoproject.com/en/3.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
    }
}


# Password validation
# https://docs.djangoproject.com/en/3.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/3.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.0/howto/static-files/

EXTERNAL_URL_BASE = "http://localhost:8001"
STATIC_URL = '/static/'

with open(os.path.join(BASE_DIR, "secrets/keycloak.json")) as f:
    keycloak_conf = json.load(f)
with open(os.path.join(BASE_DIR, "secrets/stripe.json")) as f:
    stripe_conf = json.load(f)
with open(os.path.join(BASE_DIR, "secrets/open_exchange.json")) as f:
    open_exchange_conf = json.load(f)
with open(os.path.join(BASE_DIR, "secrets/plaid.json")) as f:
    plaid_conf = json.load(f)
with open(os.path.join(BASE_DIR, "secrets/flux.json")) as f:
    flux_conf = json.load(f)
with open(os.path.join(BASE_DIR, "secrets/hmrc.json")) as f:
    hmrc_conf = json.load(f)
with open(os.path.join(BASE_DIR, "secrets/gocardless.json")) as f:
    gocardless_conf = json.load(f)
with open(os.path.join(BASE_DIR, "secrets/transferwise.json")) as f:
    transferwise_conf = json.load(f)
with open(os.path.join(BASE_DIR, "secrets/freeagent.json")) as f:
    freeagent_conf = json.load(f)

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "AS207960 Billing <billing@as207960.net>"

KEYCLOAK_SERVER_URL = keycloak_conf["server_url"]
KEYCLOAK_REALM = keycloak_conf["realm"]
OIDC_CLIENT_ID = keycloak_conf["client_id"]
OIDC_CLIENT_SECRET = keycloak_conf["client_secret"]
OIDC_SCOPES = keycloak_conf["scopes"]

stripe.api_key = stripe_conf["server_key"]
stripe.api_version = "2020-08-27; identity_beta=v3; customer_balance_payment_method_beta=v1"
STRIPE_PUBLIC_KEY = stripe_conf["public_key"]
STRIPE_ENDPOINT_SECRET = stripe_conf["endpoint_secret"]

PLAID_CLIENT_ID = plaid_conf["client_id"]
PLAID_PUBLIC_KEY = plaid_conf["public_key"]
PLAID_SECRET = plaid_conf["client_secret"]
PLAID_ENV = plaid_conf["env"]

FLUX_CLIENT_ID = flux_conf["client_id"]
FLUX_CLIENT_SECRET = flux_conf["client_secret"]

HMRC_CLIENT_ID = hmrc_conf["client_id"]
HMRC_CLIENT_SECRET = hmrc_conf["client_secret"]

GOCARDLESS_TOKEN = gocardless_conf["token"]
GOCARDLESS_ENV = gocardless_conf["env"]
GOCARDLESS_WEBHOOK_SECRET = gocardless_conf["webhook_secret"]

TRANSFERWISE_TOKEN = transferwise_conf["token"]
TRANSFERWISE_ENV = transferwise_conf["env"]

OPEN_EXCHANGE_API_KEY = open_exchange_conf["key"]

MONZO_WEBHOOK_SECRET_KEY = "test"

PHONENUMBER_DEFAULT_REGION = "GB"
CRISPY_TEMPLATE_PACK = "bootstrap4"
CRISPY_FAIL_SILENTLY = not DEBUG

with open(os.path.join(BASE_DIR, "secrets/vapid_private.der")) as f:
    PUSH_PRIV_KEY = f.read()

IS_TEST = True

OWN_EU_VAT_COUNTRY = "EU"
OWN_EU_VAT_ID = "372013983"
OWN_UK_VAT_ID = None

RABBITMQ_RPC_URL = "amqp://guest:guest@localhost:5672/rpc"

STRIPE_CLIMATE = True
STRIPE_CLIMATE_RATE = "0.01"

FREEAGENT_BASE_URL = "https://api.sandbox.freeagent.com"
FREEAGENT_CLIENT_ID = freeagent_conf["client_id"]
FREEAGENT_CLIENT_SECRET = freeagent_conf["client_secret"]

COUNTRIES_COMMON_NAMES = True
# COUNTRIES_OVERRIDE = {
#     "BS": "the Bahamas",
#     "IO": "the British Indian Ocean Territory",
#     "KY": "the Cayman Islands",
#     "CF": "the Central African Republic",
#     "CC": "the Cocos Islands",
#     "KM": "the Comoros",
#     "CD": "the Democratic Republic of the Congo",
#     "CG": "the Congo",
#     "DO": "the Dominican Republic",
#     "FK": "the Falkland Islands",
#     "FO": "the Faroe Islands",
#     "TF": "the French Southern Territories",
#     "VA": "the Holy See",
#     "KP": "the Democratic People's Republic of Korea",
#     "KR": "the Republic of Korea",
#     "LA": "the Lao People's Democratic Republic",
#     "MH": "the Marshall Islands",
#     "MD": "the Republic of Moldova",
#     "NL": "the Netherlands",
#     "NE": "the Niger",
#     "MP": "the Northen Mariana Islands",
#     "PH": "the Philippines",
#     "RU": "the Russian Federation",
#     "CK": "the Cook Islands",
#     "AE": "the United Arab Emirates",
#     "GB": "the United Kingdom",
#     "US": "the United States of America",
#     "UM": "the United States Minor Outlying Islands",
# }
