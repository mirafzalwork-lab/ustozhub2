"""
Middleware для onboarding flow.
Пользователи без завершённого профиля не могут пользоваться платформой.
"""

from django.shortcuts import redirect
from django.urls import resolve, reverse


# URL-имена, доступные без завершённого профиля
ONBOARDING_ALLOWED_URL_NAMES = {
    # Onboarding flow
    'register_choose',
    'google_complete_student',
    'google_complete_teacher',
    'google_student_onboarding',
    'teacher_register',
    'teacher_register_complete',
    'register_student',
    # Auth
    'login',
    'logout',
    'google_login',
    # Allauth
    'google_callback',
    'account_login',
    'account_signup',
    'socialaccount_connections',
    # Служебное
    'set_language',
}

# URL-префиксы, доступные без профиля (static, media, api allauth)
ONBOARDING_ALLOWED_PREFIXES = (
    '/static/',
    '/media/',
    '/accounts/',
    '/admin/',
    '/i18n/',
    '/favicon.ico',
)


def _user_needs_onboarding(user):
    """Проверяет, нужен ли пользователю onboarding (нет профиля)."""
    if not user.is_authenticated:
        return False

    # Staff/superuser — пропускаем
    if user.is_staff or user.is_superuser:
        return False

    # Проверяем наличие хотя бы одного профиля
    try:
        _ = user.teacher_profile.pk
        return False
    except Exception:
        pass

    try:
        _ = user.student_profile.pk
        return False
    except Exception:
        pass

    return True


class OnboardingMiddleware:
    """
    Если авторизованный пользователь не завершил onboarding
    (нет TeacherProfile и нет StudentProfile),
    перенаправляет его на /register/choose/.

    Пропускает: auth-страницы, onboarding-страницы, static, admin.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if _user_needs_onboarding(request.user):
            path = request.path

            # Проверяем разрешённые префиксы
            if any(path.startswith(prefix) for prefix in ONBOARDING_ALLOWED_PREFIXES):
                return self.get_response(request)

            # Проверяем разрешённые URL по имени
            try:
                url_name = resolve(path).url_name
                if url_name in ONBOARDING_ALLOWED_URL_NAMES:
                    return self.get_response(request)
            except Exception:
                pass

            # Всё остальное → редирект на выбор роли
            return redirect('register_choose')

        return self.get_response(request)
