"""
URL configuration for core project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns
from django.views.i18n import set_language  # Добавлен импорт!

urlpatterns = [
    path('i18n/setlang/', set_language, name='set_language'),
    path('accounts/', include('allauth.urls')),  # Google OAuth2 (вне i18n, чтобы callback URL был без /en/)
]

urlpatterns += i18n_patterns(
    path('admin/', admin.site.urls),
    path('', include('teachers.urls')),
    prefix_default_language=True,  # Русский (default) без префикса: /
)

# Serve media files (user uploads) - works in both DEBUG modes
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Serve static files in development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / 'static')