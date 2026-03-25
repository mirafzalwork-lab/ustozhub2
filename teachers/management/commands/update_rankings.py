from django.core.management.base import BaseCommand
from teachers.models import TeacherProfile


class Command(BaseCommand):
    help = 'Recalculate ranking_score for all active teachers'

    def handle(self, *args, **options):
        teachers = TeacherProfile.objects.filter(
            is_active=True, moderation_status='approved'
        )
        count = 0
        for teacher in teachers:
            teacher.update_ranking_score()
            count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Updated ranking for {count} teachers'
        ))
