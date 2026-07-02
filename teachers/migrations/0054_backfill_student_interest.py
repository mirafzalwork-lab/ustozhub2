from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    """Наполняет StudentInterest из уже существующих источников:
    избранное (warm), активные пробные брони (hot), просмотры профиля учителя
    авторизованными учениками (cold), плюс opt-out. Идемпотентно за счёт
    ignore_conflicts (уникальность teacher+student)."""
    StudentInterest = apps.get_model('teachers', 'StudentInterest')
    Favorite = apps.get_model('teachers', 'Favorite')
    Booking = apps.get_model('teachers', 'Booking')
    ProfileView = apps.get_model('teachers', 'ProfileView')
    LeadOptOut = apps.get_model('teachers', 'LeadOptOut')

    data = {}

    def rec(teacher_id, student_id):
        key = (teacher_id, student_id)
        d = data.get(key)
        if d is None:
            d = data[key] = {
                'has_trial': False, 'trial_at': None,
                'has_favorite': False, 'favorited_at': None,
                'view_count': 0, 'first_viewed_at': None, 'last_viewed_at': None,
                'opted_out_at': None,
            }
        return d

    # Избранное (student — User; учитываем только учеников)
    for f in Favorite.objects.select_related('student').all():
        if getattr(f.student, 'user_type', None) != 'student':
            continue
        d = rec(f.teacher_id, f.student_id)
        d['has_favorite'] = True
        if d['favorited_at'] is None or f.created_at < d['favorited_at']:
            d['favorited_at'] = f.created_at

    # Активные пробные брони (кроме отменённых учеником)
    for b in (Booking.objects.filter(is_trial=True)
              .exclude(status='cancelled_by_student')
              .select_related('slot', 'student')):
        student = b.student
        if student is None or getattr(student, 'user_type', None) != 'student':
            continue
        d = rec(b.slot.teacher_id, student.id)
        d['has_trial'] = True
        if d['trial_at'] is None or b.created_at < d['trial_at']:
            d['trial_at'] = b.created_at

    # Просмотры профиля учителя авторизованными учениками
    for pv in (ProfileView.objects.filter(
            profile_type='teacher', teacher_profile__isnull=False,
            viewer_user__isnull=False).select_related('viewer_user').iterator()):
        viewer = pv.viewer_user
        if getattr(viewer, 'user_type', None) != 'student':
            continue
        d = rec(pv.teacher_profile_id, viewer.id)
        d['view_count'] += 1
        lv = pv.last_viewed_at or pv.viewed_at
        if lv and (d['last_viewed_at'] is None or lv > d['last_viewed_at']):
            d['last_viewed_at'] = lv
        fv = pv.viewed_at
        if fv and (d['first_viewed_at'] is None or fv < d['first_viewed_at']):
            d['first_viewed_at'] = fv

    # Opt-out — только для уже собранных пар
    for o in LeadOptOut.objects.all():
        key = (o.teacher_id, o.student_id)
        if key in data:
            data[key]['opted_out_at'] = o.created_at

    objs = []
    for (teacher_id, student_id), d in data.items():
        if d['has_trial']:
            temp = 'hot'
        elif d['has_favorite']:
            temp = 'warm'
        else:
            temp = 'cold'
        stamps = [t for t in (d['trial_at'], d['favorited_at'], d['last_viewed_at']) if t]
        last_activity = max(stamps) if stamps else timezone.now()
        objs.append(StudentInterest(
            teacher_id=teacher_id, student_id=student_id,
            has_trial=d['has_trial'], trial_at=d['trial_at'],
            has_favorite=d['has_favorite'], favorited_at=d['favorited_at'],
            view_count=d['view_count'], first_viewed_at=d['first_viewed_at'],
            last_viewed_at=d['last_viewed_at'],
            temperature=temp, last_activity_at=last_activity,
            opted_out_at=d['opted_out_at'],
        ))
    StudentInterest.objects.bulk_create(objs, batch_size=500, ignore_conflicts=True)


def reverse(apps, schema_editor):
    StudentInterest = apps.get_model('teachers', 'StudentInterest')
    StudentInterest.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('teachers', '0053_studentinterest'),
    ]

    operations = [
        migrations.RunPython(backfill, reverse),
    ]
