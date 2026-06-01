"""
ASGI config for core project.
Настройка для поддержки как HTTP, так и WebSocket соединений
"""

import os
import django
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

# Инициализация Django до импорта Channels
django.setup()

# Импортируем после setup
from channels.routing import ProtocolTypeRouter, URLRouter

from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator
from teachers.routing import websocket_urlpatterns

# Получаем Django ASGI приложение
django_asgi_app = get_asgi_application()

# ASGI приложение с поддержкой WebSocket
application = ProtocolTypeRouter({
    # HTTP запросы обрабатываются обычным Django
    "http": django_asgi_app,

    # WebSocket соединения обрабатываются через Channels.
    # AllowedHostsOriginValidator проверяет Origin по ALLOWED_HOSTS — защита от
    # Cross-Site WebSocket Hijacking (сторонний сайт не откроет ws от имени юзера).
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(
                websocket_urlpatterns  # WebSocket URL patterns из teachers.routing
            )
        )
    ),
})
