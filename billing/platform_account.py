"""Системный аккаунт платформы — кошелёк, куда сыпется комиссия.

Идентифицируется по `settings.PLATFORM_ACCOUNT_USERNAME` (default `__platform__`).
Не используется для логина — это служебный «бухгалтерский» аккаунт.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction


def get_or_create_platform_user():
    """Возвращает (создавая при необходимости) системного юзера платформы."""
    User = get_user_model()
    username = settings.PLATFORM_ACCOUNT_USERNAME

    with transaction.atomic():
        user = User.objects.filter(username=username).first()
        if user is not None:
            return user
        # set_unusable_password — нельзя залогиниться в этот аккаунт.
        user = User(
            username=username,
            email=f'{username}@platform.local',
            is_staff=False,
            is_active=False,
            user_type='admin' if hasattr(User, 'user_type') else None,
        )
        user.set_unusable_password()
        user.save()
        return user
