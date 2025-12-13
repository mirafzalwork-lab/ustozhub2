# teachers/routing.py
"""
WebSocket URL routing для real-time чата
Интегрируется с существующей системой Conversation
"""

from django.urls import re_path
from . import consumers

# WebSocket URL patterns - работают с существующими conversation UUID
websocket_urlpatterns = [
    # WebSocket для существующих конверсаций
    # Паттерн: ws/conversation/<conversation_id>/
    # Упрощенный UUID паттерн для совместимости с Channels
    re_path(r'ws/conversation/(?P<conversation_id>[\w-]+)/$', consumers.ChatConsumer.as_asgi()),
]