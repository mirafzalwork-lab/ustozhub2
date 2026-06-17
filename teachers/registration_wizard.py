"""
Teacher Registration Wizard View
Multi-step registration process using Django's SessionWizardView
"""
from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib import messages
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.core.files.storage import FileSystemStorage
from django.utils.translation import gettext as _
from django import forms as dj_forms
from formtools.wizard.views import SessionWizardView
import logging
import os
import re
import uuid

logger = logging.getLogger(__name__)

from .registration_forms import (
    Step1BasicProfileForm,
    Step2AccountSecurityForm,
    Step3EducationExperienceForm,
    Step4AvailabilityFormatForm,
    Step5SubjectsPricingForm,
    Step6CertificatesForm,
)
from .models import User, TeacherProfile, TeacherSubject, Certificate, WizardDraft, Notification


# Поля, которые НЕ имеет смысла сохранять в черновик: пароли (нельзя в plaintext)
# и загруженные файлы (FileField не сериализуется в JSON).
_DRAFT_BLACKLIST_KEYS = {'password1', 'password2', 'avatar', 'file', 'video'}


def _strip_unserializable(step_data):
    """Удаляет из dict шага значения, которые нельзя/нельзя безопасно сохранить."""
    if not isinstance(step_data, dict):
        return step_data
    cleaned = {}
    for k, v in step_data.items():
        # ключи в storage обычно имеют префикс step-name, поэтому проверяем по подстроке
        if any(bad in k for bad in _DRAFT_BLACKLIST_KEYS):
            continue
        # Файлы / объекты — пропускаем
        try:
            import json
            json.dumps(v)
        except (TypeError, ValueError):
            continue
        cleaned[k] = v
    return cleaned


class TeacherRegistrationWizard(SessionWizardView):
    """
    Multi-step teacher registration wizard
    
    Steps:
    1. Basic Profile (photo, name, gender, languages, phone)
    2. Account Security (username, password, email)
    3. Education & Experience
    4. Availability & Format (telegram, location, schedule)
    5. Subjects & Pricing
    6. Certificates (optional)
    """
    
    # Define form list (6 шагов: сертификаты и видео объединены в один)
    form_list = [
        ('basic_profile', Step1BasicProfileForm),
        ('account_security', Step2AccountSecurityForm),
        ('education', Step3EducationExperienceForm),
        ('availability', Step4AvailabilityFormatForm),
        ('subjects', Step5SubjectsPricingForm),
        ('certificates', Step6CertificatesForm),
    ]

    # Templates for each step
    templates = {
        'basic_profile': 'registration/step1_basic_profile.html',
        'account_security': 'registration/step2_account_security.html',
        'education': 'registration/step3_education.html',
        'availability': 'registration/step4_availability.html',
        'subjects': 'registration/step5_subjects.html',
        'certificates': 'registration/step6_certificates.html',
    }
    
    # File storage for uploaded files during the wizard
    file_storage = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'temp_uploads'))

    # =========================================================================
    # DRAFT PERSISTENCE
    # Сохранение состояния wizard в БД (WizardDraft), привязка по session_key.
    # Это позволяет пользователю продолжить регистрацию после закрытия браузера
    # или истечения сессии. Восстановление происходит при первом GET-запросе,
    # сохранение — после каждого валидного перехода между шагами.
    # =========================================================================

    def _get_or_create_session_key(self):
        """Гарантирует наличие session_key. Без него мы не можем привязать draft."""
        session = self.request.session
        if not session.session_key:
            session.save()  # создаёт session_key
        return session.session_key

    def _save_draft(self):
        """Сохраняет текущее storage.data в БД как WizardDraft."""
        try:
            session_key = self._get_or_create_session_key()
            data = self.storage.data or {}
            # storage.data содержит вложенный step_data (по шагам) — чистим каждый
            step_data = data.get(self.storage.step_data_key, {}) or {}
            cleaned_step_data = {
                step: _strip_unserializable(payload)
                for step, payload in step_data.items()
            }
            safe_data = {
                self.storage.step_key: data.get(self.storage.step_key, ''),
                self.storage.step_data_key: cleaned_step_data,
                self.storage.extra_data_key: _strip_unserializable(
                    data.get(self.storage.extra_data_key, {}) or {}
                ),
            }
            WizardDraft.objects.update_or_create(
                session_key=session_key,
                defaults={
                    'wizard_name': 'teacher_registration',
                    'current_step': self.steps.current or '',
                    'data': safe_data,
                },
            )
        except Exception as e:
            logger.warning(f"Не удалось сохранить черновик wizard: {e}")

    def _restore_draft(self):
        """Восстанавливает данные из БД в storage, если в сессии пусто, а draft есть.

        Вызывается из get(), когда self.storage уже инициализирован.
        Возвращает True, если что-то восстановили (для get() это сигнал не
        вызывать super().get(), который зачистит storage.reset()).
        """
        try:
            session = self.request.session
            session_key = session.session_key
            if not session_key:
                return False
            # Восстанавливаем ТОЛЬКО если в текущей сессии нет своих данных wizard
            if self.storage.data and self.storage.data.get(self.storage.step_data_key):
                return False
            draft = WizardDraft.objects.filter(
                session_key=session_key,
                wizard_name='teacher_registration',
            ).first()
            if not draft or not draft.data:
                return False
            # Полностью перезаписываем storage.data из draft.data
            new_data = dict(self.storage.data or {})
            for key, value in draft.data.items():
                new_data[key] = value
            self.storage.data = new_data
            if draft.current_step:
                self.storage.current_step = draft.current_step
            return True
        except Exception as e:
            logger.warning(f"Не удалось восстановить черновик wizard: {e}")
            return False

    def _delete_draft(self):
        """Удаляет draft после успешного завершения регистрации."""
        try:
            session_key = self.request.session.session_key
            if session_key:
                WizardDraft.objects.filter(session_key=session_key).delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить черновик wizard: {e}")

    def dispatch(self, request, *args, **kwargs):
        """Гарантируем session_key, чтобы потом привязать к нему draft."""
        if request.method == 'GET':
            self._get_or_create_session_key()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        """Переопределяем get() чтобы сохранить прогресс на GET.

        Базовый SessionWizardView.get() вызывает storage.reset() — это удаляет
        весь прогресс при любом GET (даже простой refresh страницы). Здесь мы:
        1. Если в БД есть draft → восстанавливаем его в storage и рендерим
           текущий шаг (минуя reset).
        2. Если в session уже есть промежуточные данные шагов → рендерим
           текущий шаг (минуя reset). Это покрывает обычный refresh.
        3. Иначе — fallback к стандартному поведению (start from step 1).
        """
        # 1) попытка восстановить из БД
        restored = self._restore_draft()

        # 2) если сейчас в storage уже что-то есть (либо из БД, либо из session)
        step_data = (self.storage.data or {}).get(self.storage.step_data_key) or {}
        if restored or step_data:
            # Выставим текущий шаг и срендерим, не сбрасывая storage.
            return self.render(self.get_form())

        # 3) стандартное поведение для свежего входа
        return super().get(request, *args, **kwargs)

    def render_next_step(self, form, **kwargs):
        """Сохраняем draft после успешного шага."""
        response = super().render_next_step(form, **kwargs)
        self._save_draft()
        return response

    def _is_google_registration(self):
        """Check if this wizard is being used by a Google OAuth user."""
        return self.request.session.get('is_google_teacher', False)

    def get_form_initial(self, step):
        """Pre-fill form data. Для Google — поля из сессии.
        Для всех — placeholder-username (мы его всё равно перепишем в _create_user)."""
        initial = super().get_form_initial(step)
        session = self.request.session

        if step == 'account_security':
            # Username скрыт для всех. Кладём уникальный placeholder, чтобы пройти
            # уникальность/regex в clean_username; финальный username сгенерим из
            # first_name в _create_user(). Прификс _g_ отличает от пользовательских.
            if not initial.get('username'):
                initial['username'] = f"_g_{uuid.uuid4().hex[:16]}"

        if not self._is_google_registration():
            return initial

        if step == 'basic_profile':
            if session.get('google_first_name'):
                initial['first_name'] = session['google_first_name']
            if session.get('google_last_name'):
                initial['last_name'] = session['google_last_name']
        elif step == 'account_security':
            if session.get('google_email'):
                initial['email'] = session['google_email']

        return initial

    @staticmethod
    def _generate_unique_username(seed):
        """Делает уникальный username вида '<имя>_<8hex>'. Не больше 150 символов."""
        base = re.sub(r'[^\w]+', '', (seed or 'user').lower())[:20] or 'user'
        for _ in range(10):
            candidate = f"{base}_{uuid.uuid4().hex[:8]}"
            if not User.objects.filter(username__iexact=candidate).exists():
                return candidate
        # Запасной вариант: гарантированно уникальный длинный UUID.
        return f"user_{uuid.uuid4().hex}"

    def get_form(self, step=None, data=None, files=None):
        """Кастомизация формы.

        Общая логика (для всех):
        - username скрыт всегда — генерируем в _create_user() из first_name.

        Google-flow дополнительно скрывает то, что уже знаем из Google-сессии:
        - first_name, last_name, gender → hidden (значения из сессии)
        - email → hidden (из Google)
        - password1, password2 → hidden + optional (вход через Google)
        """
        form = super().get_form(step, data, files)
        current_step = step or self.steps.current
        is_google = self._is_google_registration()

        # Universal: username скрываем всегда, генерим в done().
        if current_step == 'account_security':
            form.fields['username'].required = False
            form.fields['username'].widget = dj_forms.HiddenInput()

        if not is_google:
            return form

        if current_step == 'basic_profile':
            form.fields['first_name'].widget = dj_forms.HiddenInput()
            form.fields['last_name'].widget = dj_forms.HiddenInput()
            form.fields['gender'].widget = dj_forms.HiddenInput()

        elif current_step == 'account_security':
            form.fields['email'].widget = dj_forms.HiddenInput()
            form.fields['password1'].required = False
            form.fields['password2'].required = False
            form.fields['password1'].widget = dj_forms.HiddenInput()
            form.fields['password2'].widget = dj_forms.HiddenInput()

        return form

    def get_template_names(self):
        """Return template for current step"""
        return [self.templates[self.steps.current]]

    def get_context_data(self, form, **kwargs):
        """Add context data for templates"""
        context = super().get_context_data(form=form, **kwargs)
        
        # Progress — учитываем condition_dict (skip-шаги для Google), поэтому
        # берём steps.count/step1 у formtools, а НЕ len(self.form_list).
        current_step = self.steps.step1            # 1-based номер текущего шага
        total_steps = self.steps.count             # реальное число шагов (с учётом skip)
        progress_percentage = (current_step / total_steps) * 100 if total_steps else 0
        
        # Step information
        step_titles = {
            'basic_profile': _('Основная информация'),
            'account_security': _('Безопасность аккаунта'),
            'education': _('Образование и опыт'),
            'availability': _('Доступность и формат'),
            'subjects': _('Предметы и цены'),
            'certificates': _('Сертификаты и видео'),
        }

        step_descriptions = {
            'basic_profile': _('Расскажите о себе. Эта информация будет видна ученикам.'),
            'account_security': _('Создайте учетные данные для входа в систему.'),
            'education': _('Укажите ваше образование и опыт преподавания.'),
            'availability': _('Когда и как ученики могут с вами связаться?'),
            'subjects': _('Какие предметы вы преподаёте и по какой цене?'),
            'certificates': _('Загрузите сертификаты и короткое видео о себе (всё необязательно).'),
        }
        
        session_key = self.request.session.session_key
        has_draft = False
        if session_key:
            has_draft = WizardDraft.objects.filter(
                session_key=session_key,
                wizard_name='teacher_registration',
            ).exists()

        context.update({
            'current_step': current_step,
            'total_steps': total_steps,
            'progress_percentage': progress_percentage,
            'step_title': step_titles.get(self.steps.current, ''),
            'step_description': step_descriptions.get(self.steps.current, ''),
            'is_last_step': self.steps.current == self.steps.last,
            'is_google': self._is_google_registration(),
            'has_draft': has_draft,
        })

        return context
    
    def done(self, form_list, **kwargs):
        """
        Process all forms and create user + teacher profile
        Called when all steps are completed
        """
        # Collect all form data
        form_data = {}
        for form in form_list:
            form_data.update(form.cleaned_data)
        
        try:
            # ВСЁ создание — в одной транзакции. Если упадёт любой шаг после
            # создания User, откатывается всё, и осиротевших аккаунтов не остаётся.
            with transaction.atomic():
                # Create User
                user = self._create_user(form_data)

                # Create Teacher Profile
                teacher_profile = self._create_teacher_profile(user, form_data)

                # Add Subjects
                self._add_subjects(teacher_profile, form_data)

                # Add Certificates (if any were uploaded during the wizard)
                self._add_certificates(teacher_profile, form_data)

                # Save video URL (if uploaded via presigned URL)
                video_url = form_data.get('video_url', '').strip()
                if video_url and video_url.startswith(settings.S3_PUBLIC_URL.rstrip('/')):
                    teacher_profile.video_url = video_url
                    teacher_profile.save(update_fields=['video_url'])

                # Сразу нарезаем TimeSlot из шаблона расписания на 4 недели вперёд,
                # чтобы календарь учителя не был пустым после регистрации.
                # Если в расписании нет ни одного интервала — метод вернёт нули.
                try:
                    result = teacher_profile.generate_slots_from_template(weeks=4, slot_minutes=60)
                    logger.info(
                        f"Auto-generated slots after registration: teacher={teacher_profile.pk} "
                        f"created={result['created']} skipped={result['skipped']}"
                    )
                except Exception as e:
                    # Сгенерировать слоты не критично для регистрации — учитель сможет
                    # сделать это вручную из календаря. Логируем и идём дальше.
                    logger.warning(f"Slot auto-generation failed: {e}", exc_info=True)

            # Уведомляем модераторов о новой заявке (вне транзакции — не критично)
            self._notify_moderators(teacher_profile)

            # Log the user in
            login(self.request, user, backend='django.contrib.auth.backends.ModelBackend')

            # Clean up Google session data
            for key in ['google_first_name', 'google_last_name', 'google_email',
                        'google_user_id', 'is_google_teacher']:
                self.request.session.pop(key, None)

            # Удаляем черновик — регистрация завершена
            self._delete_draft()

            # Success message
            messages.success(
                self.request,
                _('Регистрация завершена! Ваш профиль отправлен на модерацию.')
            )
            
            # Redirect to completion page
            return redirect('teacher_register_complete')
            
        except Exception as e:
            messages.error(
                self.request,
                _('Произошла ошибка при сохранении данных: %(err)s') % {'err': str(e)}
            )
            return redirect('teacher_register')
    
    def _create_user(self, form_data):
        """Create User object"""
        is_google = self._is_google_registration()
        password = form_data.get('password1') or None

        # У Google-регистрации шаг account_security скрыт (condition_dict), поэтому
        # email берём из сессии, если его нет в данных формы.
        email = form_data.get('email') or self.request.session.get('google_email')
        form_data = {**form_data, 'email': email}

        # Username мы больше не спрашиваем у пользователя — генерим из first_name.
        # На форме лежит placeholder вида "_g_<hex>", который заменяем на стабильный.
        username = (form_data.get('username') or '').strip()
        if not username or username.startswith('_g_'):
            username = self._generate_unique_username(form_data.get('first_name', ''))

        # gender может быть пустым для Google-учителей (мы не запрашиваем его)
        gender = form_data.get('gender') or None

        user = User.objects.create_user(
            username=username,
            email=form_data['email'],
            password=password,
            first_name=form_data['first_name'],
            last_name=form_data['last_name'],
            phone=form_data['phone'],
            gender=gender,
            user_type='teacher',
            is_verified=is_google,  # Google email already verified
        )

        # Google user without password — set unusable password
        if is_google and not password:
            user.set_unusable_password()
            user.save(update_fields=['password'])

        # Set avatar if uploaded
        if form_data.get('avatar'):
            user.avatar = form_data['avatar']
            user.save()

        return user
    
    def _create_teacher_profile(self, user, form_data):
        """Create TeacherProfile object"""
        # Convert teaching languages list to comma-separated string
        teaching_languages = ','.join(form_data['teaching_languages'])
        
        # Новый формат расписания (мультиинтервалы) приходит уже собранным в clean()
        # формы шага 4 как form_data['weekly_schedule'] = {"monday": [{"from","to"},...], ...}
        weekly_schedule = form_data.get('weekly_schedule') or {}

        # available_weekdays — список номеров дней ('1'..'7'), собран в clean()
        available_weekdays = ','.join(form_data.get('available_weekdays') or [])

        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

        # available_from / available_to — берём из самого раннего/позднего интервала
        # (для обратной совместимости со старыми полями модели).
        from datetime import time as _time
        first_available_from = None
        first_available_to = None
        for day in days:
            intervals = weekly_schedule.get(day) or []
            for itv in intervals:
                try:
                    f = _time.fromisoformat(itv['from'])
                    t = _time.fromisoformat(itv['to'])
                except (ValueError, KeyError, TypeError):
                    continue
                if first_available_from is None or f < first_available_from:
                    first_available_from = f
                if first_available_to is None or t > first_available_to:
                    first_available_to = t
        
        # Поля available_from/available_to/available_weekdays — не nullable, у них
        # есть БД-дефолты. Если расписание не задано, оставляем дефолты модели,
        # не передавая None — учитель заполнит позже в календаре.
        profile_kwargs = dict(
            user=user,
            bio=form_data['bio'],
            education_level=form_data.get('education_level'),
            university=form_data.get('university'),
            specialization=form_data.get('specialization', ''),
            experience_years=form_data['experience_years'],
            city=form_data.get('city'),
            teaching_format=form_data['teaching_format'],
            telegram=form_data['telegram'],
            whatsapp=form_data.get('whatsapp', ''),
            teaching_languages=teaching_languages,
            weekly_schedule=weekly_schedule,
            moderation_status='pending',
            is_active=False,  # Will be activated after moderation
        )
        if first_available_from is not None:
            profile_kwargs['available_from'] = first_available_from
        if first_available_to is not None:
            profile_kwargs['available_to'] = first_available_to
        if available_weekdays:
            profile_kwargs['available_weekdays'] = available_weekdays

        teacher_profile = TeacherProfile.objects.create(**profile_kwargs)

        return teacher_profile
    
    def _add_subjects(self, teacher_profile, form_data):
        """Add teacher subjects with pricing + trial lesson settings."""
        for i in range(1, 5):
            subject = form_data.get(f'subject_{i}')
            hourly_rate = form_data.get(f'hourly_rate_{i}')

            if subject and hourly_rate:
                is_free_trial = bool(form_data.get(f'is_free_trial_{i}', True))
                # Длительность приходит строкой ('30'/'60'), приводим к int.
                try:
                    trial_duration = int(form_data.get(f'trial_duration_{i}') or 60)
                except (TypeError, ValueError):
                    trial_duration = 60
                if trial_duration not in (30, 60):
                    trial_duration = 60

                trial_price = form_data.get(f'trial_price_{i}') if not is_free_trial else None

                TeacherSubject.objects.create(
                    teacher=teacher_profile,
                    subject=subject,
                    hourly_rate=hourly_rate,
                    is_free_trial=is_free_trial,
                    trial_duration_minutes=trial_duration,
                    trial_price=trial_price,
                    description=form_data.get(f'description_{i}', ''),
                )
    
    def _add_certificates(self, teacher_profile, form_data):
        """Сохраняем все сертификаты из шага 6 (до 4 шт.).
        Все поля опциональны; пара (name+issuer+file) либо целиком заполнена, либо
        целиком пустая (валидируется формой). Ошибка одного сертификата не валит
        регистрацию — мы её логируем и продолжаем."""
        for i in range(1, 5):
            name = (form_data.get(f'cert_name_{i}') or '').strip()
            issuer = (form_data.get(f'cert_issuer_{i}') or '').strip()
            file = form_data.get(f'cert_file_{i}')
            if not (name and issuer and file):
                continue
            try:
                certificate = Certificate.objects.create(
                    name=name, issuer=issuer, file=file,
                )
                teacher_profile.certificates.add(certificate)
            except Exception as e:
                logger.error(f"Error adding certificate #{i}: {e}", exc_info=True)

    def _notify_moderators(self, teacher_profile):
        """Создаёт уведомление для администраторов о новой заявке на модерацию."""
        try:
            user = teacher_profile.user
            teacher_name = user.get_full_name() or user.username
            try:
                action_url = self.request.build_absolute_uri(reverse('admin_dashboard'))
            except Exception:
                action_url = ''
            Notification.objects.create(
                title="Новая заявка учителя на модерацию",
                short_text=f"{teacher_name} зарегистрировался и ожидает проверки профиля.",
                full_text=(
                    f"Новый преподаватель {teacher_name} (@{user.username}) "
                    f"завершил регистрацию и ожидает модерации.\n\n"
                    f"Проверьте профиль в панели администратора и одобрите либо отклоните заявку."
                ),
                target='admins',
                is_active=True,
                priority=5,
                category=Notification.Category.MODERATION,
                action_url=action_url or None,
            )
            logger.info(f"Moderator notification created for new teacher: {user.username}")
        except Exception as e:
            logger.error(f"Failed to create moderator notification: {e}", exc_info=True)


def teacher_register_complete(request):
    """
    Completion page after successful registration
    Shows moderation pending message
    """
    # Check if user just completed registration
    if not request.user.is_authenticated:
        messages.warning(request, _('Пожалуйста, завершите регистрацию'))
        return redirect('teacher_register')
    
    if not hasattr(request.user, 'teacher_profile'):
        messages.warning(request, _('Профиль учителя не найден'))
        return redirect('home')
    
    teacher_profile = request.user.teacher_profile
    
    context = {
        'teacher_profile': teacher_profile,
        'user': request.user,
    }
    
    return render(request, 'registration/complete.html', context)
