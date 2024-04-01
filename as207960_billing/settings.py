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
import sentry_sdk
from idempotency_key import status
from sentry_sdk.integrations.django import DjangoIntegration

logging.basicConfig(level=logging.INFO)

sentry_sdk.init(
    dsn="https://29216899113e486fa4a77116e2f633a0@o222429.ingest.sentry.io/5223211",
    environment=os.getenv("SENTRY_ENVIRONMENT", "dev"),
    release=os.getenv("RELEASE", None),
    integrations=[DjangoIntegration()],
    send_default_pii=True
)

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("SECRET_KEY", "")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

ALLOWED_HOSTS = os.getenv("HOST", "billing.as207960.net").split(",")

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
    'crispy_bootstrap5',
    'billing'
]

MIDDLEWARE = [
    'xff.middleware.XForwardedForMiddleware',
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
        "ENGINE": "django_cockroachdb",
        "HOST": os.getenv("DB_HOST", "localhost"),
        "NAME": os.getenv("DB_NAME", "billing"),
        "USER": os.getenv("DB_USER", "billing"),
        "PASSWORD": os.getenv("DB_PASS"),
        "PORT": '26257',
        "OPTIONS": {
            "application_name": os.getenv("APP_NAME", "billing")
        }
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

EXTERNAL_URL_BASE = os.getenv("EXTERNAL_URL", f"https://{ALLOWED_HOSTS[0]}")

STATIC_URL = os.getenv("STATIC_URL", f"{EXTERNAL_URL_BASE}/static/")
MEDIA_URL = os.getenv("MEDIA_URL", f"{EXTERNAL_URL_BASE}/media/")

AWS_S3_CUSTOM_DOMAIN = os.getenv("S3_CUSTOM_DOMAIN", "")
AWS_QUERYSTRING_AUTH = False
AWS_S3_REGION_NAME = os.getenv("S3_REGION", "")
AWS_S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT", "")
AWS_STORAGE_BUCKET_NAME = os.getenv("S3_BUCKET", "")
AWS_S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "")
AWS_S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")
AWS_S3_ADDRESSING_STYLE = "virtual"
AWS_S3_SIGNATURE_VERSION = "s3v4"

STORAGES = {
    "default": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
    "staticfiles": {"BACKEND": "storages.backends.s3boto3.S3ManifestStaticStorage"}
}

KEYCLOAK_SERVER_URL = os.getenv("KEYCLOAK_SERVER_URL")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM")
OIDC_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID")
OIDC_CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET")
OIDC_SCOPES = os.getenv("KEYCLOAK_SCOPES")

stripe.api_key = os.getenv("STRIPE_SERVER_KEY")
stripe.api_version = "2020-08-27; customer_balance_payment_method_beta=v2"
STRIPE_PUBLIC_KEY = os.getenv("STRIPE_PUBLIC_KEY")
STRIPE_ENDPOINT_SECRET = os.getenv("STRIPE_ENDPOINT_SECRET")

FLUX_CLIENT_ID = os.getenv("FLUX_CLIENT_ID")
FLUX_CLIENT_SECRET = os.getenv("FLUX_CLIENT_SECRET")

HMRC_CLIENT_ID = os.getenv("HMRC_CLIENT_ID")
HMRC_CLIENT_SECRET = os.getenv("HMRC_CLIENT_SECRET")

MONZO_WEBHOOK_SECRET_KEY = os.getenv("MONZO_WEBHOOK_SECRET")

GOCARDLESS_TOKEN = os.getenv("GOCARDLESS_TOKEN")
GOCARDLESS_ENV = os.getenv("GOCARDLESS_ENV")
GOCARDLESS_WEBHOOK_SECRET = os.getenv("GOCARDLESS_WEBHOOK_SECRET")

TRANSFERWISE_TOKEN = os.getenv("TRANSFERWISE_TOKEN")
TRANSFERWISE_ENV = os.getenv("TRANSFERWISE_ENV")
TRANSFERWISE_PRIV_KEY = os.getenv("TRANSFERWISE_PRIV_KEY")

PHONENUMBER_DEFAULT_REGION = "GB"
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"
CRISPY_FAIL_SILENTLY = True

OWN_EU_VAT_COUNTRY = "EU"
OWN_EU_VAT_ID = "372013983"
OWN_UK_VAT_ID = "378323867"
OWN_TR_VAT_ID = "0861333524"

OPEN_EXCHANGE_API_KEY = os.getenv("OPEN_EXCHANGE_API_KEY")

DEFAULT_FROM_EMAIL = os.getenv("EMAIL_FROM", "AS207960 Billing <billing@as207960.net>")

PUSH_PRIV_KEY = os.getenv("PUSH_PRIV_KEY")

IS_TEST = bool(os.getenv("IS_TEST", False))

XFF_TRUSTED_PROXY_DEPTH = 2
XFF_NO_SPOOFING = True
XFF_HEADER_REQUIRED = True

RABBITMQ_RPC_URL = os.getenv("RABBITMQ_RPC_URL")

STRIPE_CLIMATE = bool(os.getenv("STRIPE_CLIMATE"))
STRIPE_CLIMATE_RATE = "0.01"

IDEMPOTENCY_KEY = {
    'ENCODER_CLASS': 'idempotency_key.encoders.BasicKeyEncoder',
    'CONFLICT_STATUS_CODE': status.HTTP_409_CONFLICT,
    'HEADER': 'HTTP_IDEMPOTENCY_KEY',
    'STORAGE': {
        'CLASS': 'idempotency_key.storage.MemoryKeyStorage',
        'CACHE_NAME': 'default',
        'STORE_ON_STATUSES': [
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_202_ACCEPTED,
            status.HTTP_203_NON_AUTHORITATIVE_INFORMATION,
            status.HTTP_204_NO_CONTENT,
            status.HTTP_205_RESET_CONTENT,
            status.HTTP_206_PARTIAL_CONTENT,
            status.HTTP_207_MULTI_STATUS,
            status.HTTP_302_FOUND,
        ]
    },

    'LOCK': {
        'CLASS': 'idempotency_key.locks.basic.ThreadLock',
        'LOCATION': 'localhost:6379',
        'NAME': 'BillingLock',
        'TTL': None,
        'ENABLE': False,
        'TIMEOUT': 0.1,
    },

}

FREEAGENT_BASE_URL = os.getenv("FREEAGENT_BASE_URL")
FREEAGENT_CLIENT_ID = os.getenv("FREEAGENT_CLIENT_ID")
FREEAGENT_CLIENT_SECRET = os.getenv("FREEAGENT_CLIENT_SECRET")

LISTMONK_TEMPLATE_ID = int(os.getenv("LISTMONK_TEMPLATE_ID"))
LISTMONK_URL = os.getenv("LISTMONK_URL")

COUNTRIES_COMMON_NAMES = True
COUNTRIES_OVERRIDE = {
    "BS": "the Bahamas",
    "IO": "the British Indian Ocean Territory",
    "KY": "the Cayman Islands",
    "CF": "the Central African Republic",
    "CC": "the Cocos Islands",
    "KM": "the Comoros",
    "CD": "the Democratic Republic of the Congo",
    "CG": "the Congo",
    "DO": "the Dominican Republic",
    "FK": "the Falkland Islands",
    "FO": "the Faroe Islands",
    "TF": "the French Southern Territories",
    "VA": "the Holy See",
    "KP": "the Democratic People's Republic of Korea",
    "KR": "the Republic of Korea",
    "LA": "the Lao People's Democratic Republic",
    "MH": "the Marshall Islands",
    "MD": "the Republic of Moldova",
    "NL": "the Netherlands",
    "NE": "the Niger",
    "MP": "the Northen Mariana Islands",
    "PH": "the Philippines",
    "RU": "the Russian Federation",
    "CK": "the Cook Islands",
    "AE": "the United Arab Emirates",
    "GB": "the United Kingdom",
    "US": "the United States of America",
    "UM": "the United States Minor Outlying Islands",
}

DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'