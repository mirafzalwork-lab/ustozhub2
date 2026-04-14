from django.core.management.base import BaseCommand
from django.utils import timezone
from teachers.models import TeacherProfile


class Command(BaseCommand):
    help = 'Approve and activate all teachers with pending moderation status'

    def handle(self, *args, **options):
        qs = TeacherProfile.objects.filter(moderation_status='pending')
        count = qs.count()
        self.stdout.write(f'Found pending teachers: {count}')

        if count == 0:
            self.stdout.write(self.style.WARNING('Nothing to update'))
            return

        updated = qs.update(
            moderation_status='approved',
            is_active=True,
            moderation_date=timezone.now(),
            moderation_comment='Bulk approval'
        )
        self.stdout.write(self.style.SUCCESS(f'Approved and activated: {updated}'))
