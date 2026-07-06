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

    # Комната урока: чат + мгновенная синхронизация материалов + presence.
    # booking_id — UUID. Паттерн специфичнее conversation, ставим выше.
    re_path(r'ws/lesson/(?P<booking_id>[0-9a-fA-F-]+)/$', consumers.LessonRoomConsumer.as_asgi()),

    # WebSocket для существующих конверсаций
    re_path(r'ws/conversation/(?P<conversation_id>[\w-]+)/$', consumers.ChatConsumer.as_asgi()),
]
