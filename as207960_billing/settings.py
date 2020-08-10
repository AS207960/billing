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

STATIC_URL = f"{EXTERNAL_URL_BASE}/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "static")

MEDIA_URL = f"{EXTERNAL_URL_BASE}/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

KEYCLOAK_SERVER_URL = os.getenv("KEYCLOAK_SERVER_URL")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM")
OIDC_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID")
OIDC_CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET")
OIDC_SCOPES = os.getenv("KEYCLOAK_SCOPES")

stripe.api_key = os.getenv("STRIPE_SERVER_KEY")
stripe.api_version = "2020-03-02; identity_beta=v3"
STRIPE_PUBLIC_KEY = os.getenv("STRIPE_PUBLIC_KEY")
STRIPE_ENDPOINT_SECRET = os.getenv("STRIPE_ENDPOINT_SECRET")

FLUX_CLIENT_ID = os.getenv("FLUX_CLIENT_ID")
FLUX_CLIENT_SECRET = os.getenv("FLUX_CLIENT_SECRET")

MONZO_WEBHOOK_SECRET_KEY = os.getenv("MONZO_WEBHOOK_SECRET")

PHONENUMBER_DEFAULT_REGION = "GB"
CRISPY_TEMPLATE_PACK = "bootstrap4"
CRISPY_FAIL_SILENTLY = True

OPEN_EXCHANGE_API_KEY = os.getenv("OPEN_EXCHANGE_API_KEY")

EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 25))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", False)
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", False)
DEFAULT_FROM_EMAIL = os.getenv("EMAIL_FROM", "AS207960 Billing <billing@as207960.net>")

PUSH_PRIV_KEY = os.getenv("PUSH_PRIV_KEY")

IS_TEST = bool(os.getenv("IS_TEST", False))

XFF_TRUSTED_PROXY_DEPTH = 2
XFF_NO_SPOOFING = True
XFF_HEADER_REQUIRED = True

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

