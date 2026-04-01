"""
Кастомный adapter для django-allauth.
Обрабатывает логику после входа через Google OAuth2.
"""

import uuid
import logging

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.shortcuts import resolve_url

logger = logging.getLogger(__name__)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Кастомный adapter для обработки social login (Google).

    Логика:
    - Если email уже существует в системе → привязать Google к существующему аккаунту
    - Если пользователь новый → создать User с is_verified=True, без профиля
    - После первого входа нового пользователя → редирект на /register/choose/
    """

    def save_user(self, request, sociallogin, form=None):
        """
        Сохраняет нового пользователя после Google login.
        Устанавливает is_verified=True и генерирует уникальный username.
        """
        user = super().save_user(request, sociallogin, form)

        # Данные из Google
        extra_data = sociallogin.account.extra_data

        # Устанавливаем verified, т.к. Google уже верифицировал email
        user.is_verified = True

        # Если username пустой или совпадает с email — генерируем уникальный
        if not user.username or '@' in user.username:
            base = extra_data.get('given_name', 'user').lower()
            user.username = f"{base}_{uuid.uuid4().hex[:8]}"

        # Имя и фамилия из Google (если не заполнены)
        if not user.first_name:
            user.first_name = extra_data.get('given_name', '')
        if not user.last_name:
            user.last_name = extra_data.get('family_name', '')

        user.save()

        logger.info(
            f"Новый пользователь через Google: {user.username} ({user.email})"
        )

        return user

    def get_login_redirect_url(self, request):
        """
        Определяет URL редиректа после login через Google.

        - Новый пользователь (нет TeacherProfile и нет StudentProfile) → /register/choose/
        - Существующий пользователь → стандартный редирект
        """
        user = request.user

        if not user.is_authenticated:
            return resolve_url('home')

        # Проверяем, есть ли у пользователя профиль
        has_teacher_profile = hasattr(user, 'teacher_profile')
        has_student_profile = hasattr(user, 'student_profile')

        # Дополнительная проверка на реальное существование (hasattr может вернуть True для related manager)
        try:
            if has_teacher_profile:
                _ = user.teacher_profile.pk
        except Exception:
            has_teacher_profile = False

        try:
            if has_student_profile:
                _ = user.student_profile.pk
        except Exception:
            has_student_profile = False

        if not has_teacher_profile and not has_student_profile:
            logger.info(
                f"Google-пользователь {user.username} без профиля → /register/choose/"
            )
            return resolve_url('register_choose')

        return resolve_url('profile')

    def is_auto_signup_allowed(self, request, sociallogin):
        """Разрешаем автоматическую регистрацию через Google."""
        return True

    def populate_user(self, request, sociallogin, data):
        """Заполняем поля пользователя из данных Google."""
        user = super().populate_user(request, sociallogin, data)

        # Гарантируем уникальный username
        if not user.username or '@' in user.username:
            base = (data.get('first_name') or 'user').lower()
            user.username = f"{base}_{uuid.uuid4().hex[:8]}"

        return user
