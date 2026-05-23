"""
Sitemaps для UstozHub — для Google/Yandex indexing.
"""
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import TeacherProfile


class StaticSitemap(Sitemap):
    """Главные публичные страницы."""
    priority = 0.8
    changefreq = 'weekly'
    protocol = 'https'

    def items(self):
        return ['home', 'register_choose', 'login', 'privacy', 'terms']

    def location(self, item):
        return reverse(item)


class TeacherSitemap(Sitemap):
    """Все опубликованные профили учителей — главный SEO-актив."""
    changefreq = 'weekly'
    priority = 0.9
    protocol = 'https'

    def items(self):
        return TeacherProfile.objects.filter(
            is_active=True, moderation_status='approved'
        ).select_related('user')

    def location(self, obj):
        return reverse('teacher_detail', args=[obj.pk])

    def lastmod(self, obj):
        # Не у всех моделей есть updated_at — используем user.date_joined как fallback
        return getattr(obj, 'updated_at', None) or obj.user.date_joined


SITEMAPS = {
    'static': StaticSitemap,
    'teachers': TeacherSitemap,
}
