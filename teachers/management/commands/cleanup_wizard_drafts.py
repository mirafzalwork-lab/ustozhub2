"""
Удаляет устаревшие черновики мастера регистрации.

Запуск из cron раз в сутки:
    0 3 * * * cd /path/to/project && venv/bin/python manage.py cleanup_wizard_drafts
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from teachers.models import WizardDraft


class Command(BaseCommand):
    help = 'Удаляет черновики регистрации старше WIZARD_DRAFT_TTL_DAYS (default 14).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=None,
            help='Перекрыть TTL из settings.WIZARD_DRAFT_TTL_DAYS',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Не удалять, только посчитать.',
        )

    def handle(self, *args, **options):
        days = options['days'] if options['days'] is not None else getattr(
            settings, 'WIZARD_DRAFT_TTL_DAYS', 14,
        )
        cutoff = timezone.now() - timedelta(days=days)
        qs = WizardDraft.objects.filter(updated_at__lt=cutoff)
        count = qs.count()

        if options['dry_run']:
            self.stdout.write(self.style.WARNING(
                f"[dry-run] Под удаление: {count} черновиков старше {days} дней."
            ))
            return

        qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f"Удалено {count} черновиков старше {days} дней."
        ))
