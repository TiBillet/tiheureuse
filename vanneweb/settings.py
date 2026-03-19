import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Charge un fichier .env local si python-dotenv est disponible (dev)
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-w)cei*-fqfd#z&y7l)5n@zl=cp_=9o(nuo#c)4!h@tm8l1hy_m",
)

DEBUG = os.environ.get("DEBUG", "True").lower() == "true"

_hosts = os.environ.get("ALLOWED_HOSTS", "")
ALLOWED_HOSTS = [h.strip() for h in _hosts.split(",") if h.strip()] or ["*"]
# Application definition

INSTALLED_APPS = [
    'unfold',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',
    'controlvanne.apps.ControlvanneConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'vanneweb.urls'
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
#        'DIRS': [],
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

WSGI_APPLICATION = 'vanneweb.wsgi.application'

# ASGI / Channels
ASGI_APPLICATION = "vanneweb.asgi.application"
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AGENT_SHARED_KEY = os.environ.get("AGENT_SHARED_KEY", "changeme")

# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

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
LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = "Indian/Reunion"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / "staticfiles"
if DEBUG:
    # Dev : Django sert les fichiers statiques directement, pas besoin de collectstatic
    STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"
else:
    # Production : whitenoise avec manifest haché
    STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# Django Unfold — interface admin moderne
# ---------------------------------------------------------------------------
from django.urls import reverse_lazy

UNFOLD = {
    "SITE_TITLE": "TiHeure",
    "SITE_HEADER": "TiHeure",
    "SITE_URL": "/",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "SIDEBAR": {
        "show_search": False,
        "navigation": [
            {
                "title": "Exploitation",
                "collapsible": True,
                "items": [
                    {
                        "title": "Tireuses",
                        "icon": "sports_bar",
                        "link": reverse_lazy("admin:controlvanne_tireusebec_changelist"),
                    },
                    {
                        "title": "Fûts",
                        "icon": "inventory_2",
                        "link": reverse_lazy("admin:controlvanne_fut_changelist"),
                    },
                ],
            },
            {
                "title": "Cartes",
                "collapsible": True,
                "items": [
                    {
                        "title": "Cartes clients",
                        "icon": "credit_card",
                        "link": reverse_lazy("admin:controlvanne_card_changelist"),
                    },
                    {
                        "title": "Cartes maintenance",
                        "icon": "build",
                        "link": reverse_lazy("admin:controlvanne_cartemaintenance_changelist"),
                    },
                ],
            },
            {
                "title": "Historiques",
                "collapsible": True,
                "items": [
                    {
                        "title": "Par tireuse",
                        "icon": "bar_chart",
                        "link": reverse_lazy("admin:controlvanne_historiquetireuse_changelist"),
                    },
                    {
                        "title": "Par carte",
                        "icon": "receipt_long",
                        "link": reverse_lazy("admin:controlvanne_historiquecarte_changelist"),
                    },
                    {
                        "title": "Rotations fûts",
                        "icon": "swap_horiz",
                        "link": reverse_lazy("admin:controlvanne_historiquefut_changelist"),
                    },
                    {
                        "title": "Maintenance",
                        "icon": "cleaning_services",
                        "link": reverse_lazy("admin:controlvanne_historiquemaintenance_changelist"),
                    },
                ],
            },
            {
                "title": "Configuration",
                "collapsible": True,
                "items": [
                    {
                        "title": "Débitmètres",
                        "icon": "tune",
                        "link": reverse_lazy("admin:controlvanne_debimetre_changelist"),
                    },
                    {
                        "title": "Calibrations",
                        "icon": "straighten",
                        "link": reverse_lazy("admin:controlvanne_sessioncalibration_changelist"),
                    },
                    {
                        "title": "Sessions brutes",
                        "icon": "developer_mode",
                        "link": reverse_lazy("admin:controlvanne_rfidsession_changelist"),
                    },
                ],
            },
        ],
    },
}
