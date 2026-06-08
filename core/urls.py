"""
URL configuration for core project.
"""
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns
from django.views.i18n import set_language  # Добавлен импорт!

from teachers.sitemaps import SITEMAPS
from teachers.views import robots_txt, healthz
from billing.views import multicard_callback

urlpatterns = [
    path('i18n/setlang/', set_language, name='set_language'),
    path('accounts/', include('allauth.urls')),  # Google OAuth2 (вне i18n, чтобы callback URL был без /en/)
    # SEO: sitemap.xml + robots.txt — вне i18n, на корне домена
    path('sitemap.xml', sitemap, {'sitemaps': SITEMAPS}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', robots_txt, name='robots_txt'),
    path('healthz/', healthz, name='healthz'),  # мониторинг (DB + Redis)
    # Webhook Multicard — вне i18n, чтобы URL был без языкового префикса
    path('payments/multicard/callback/', multicard_callback, name='multicard_callback'),
]

urlpatterns += i18n_patterns(
    path('admin/', admin.site.urls),
    path('', include('billing.urls')),
    path('', include('teachers.urls')),
    prefix_default_language=True,  # Русский (default) без префикса: /
)

# Раздача media/static — ТОЛЬКО в dev. В production /media/ и /static/ отдаёт
# nginx (см. deploy/nginx.conf). Раньше Django отдавал /media/ безусловно, в т.ч.
# приватные сертификаты, без access-control и nosniff.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / 'static')