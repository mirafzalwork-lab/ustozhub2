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
    'video_presigned_url_register',
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
    # Юридические страницы — доступны всем
    'privacy',
    'terms',
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


# ---------------------------------------------------------------- #
# CSP (Content-Security-Policy) — Report-Only режим
# ---------------------------------------------------------------- #

class CSPReportOnlyMiddleware:
    """
    Добавляет Content-Security-Policy-Report-Only заголовок.

    Report-Only: браузер НЕ блокирует нарушения, только логирует их в консоль.
    Это безопасный первый шаг — собираем real-world нарушения, потом
    переключаем на enforcing.

    Политика разрешает наши доверенные источники:
      - 'self' для всего нашего
      - CDN: Font Awesome, FullCalendar, Google Fonts
      - meet.jit.si + meet.ustozhubedu.uz (для встроенной видео-комнаты)
      - R2/S3 для медиа
      - inline scripts/styles пока разрешены ('unsafe-inline') — мы их активно
        используем в шаблонах; чтобы отказаться, понадобится nonce/hash
        на каждом <script>/<style>.
    """
    POLICY = "; ".join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "https://cdnjs.cloudflare.com https://cdn.jsdelivr.net "
            "https://meet.jit.si https://meet.ustozhubedu.uz",
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com "
            "https://cdn.jsdelivr.net https://fonts.googleapis.com",
        "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com data:",
        "img-src 'self' data: blob: https:",
        "media-src 'self' blob: https:",
        "connect-src 'self' wss: https:",
        "frame-src 'self' https://meet.jit.si https://meet.ustozhubedu.uz "
            "https://accounts.google.com",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self' https://accounts.google.com",
    ])

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # На admin-страницы не вешаем (там reverse JS-тяжелее)
        if request.path.startswith('/admin/'):
            return response
        response['Content-Security-Policy-Report-Only'] = self.POLICY
        return response
