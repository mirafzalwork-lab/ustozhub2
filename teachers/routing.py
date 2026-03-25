# teachers/routing.py
"""
WebSocket URL routing:
  - ws/notifications/  — per-user push-уведомления
  - ws/conversation/<id>/  — real-time чат
"""

from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    # Per-user notification channel (должен быть ПЕРЕД conversation, т.к. более специфичный)
    re_path(r'ws/notifications/$', consumers.NotificationConsumer.as_asgi()),

    # WebSocket для существующих конверсаций
    re_path(r'ws/conversation/(?P<conversation_id>[\w-]+)/$', consumers.ChatConsumer.as_asgi()),
]
