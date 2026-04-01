"""
Teacher Registration Wizard View
Multi-step registration process using Django's SessionWizardView
"""
from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib import messages
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from formtools.wizard.views import SessionWizardView
import logging
import os

logger = logging.getLogger(__name__)

from .registration_forms import (
    Step1BasicProfileForm,
    Step2AccountSecurityForm,
    Step3EducationExperienceForm,
    Step4AvailabilityFormatForm,
    Step5SubjectsPricingForm,
    Step6CertificatesForm,
    Step7VideoForm,
)
from .models import User, TeacherProfile, TeacherSubject, Certificate


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
    
    # Define form list
    form_list = [
        ('basic_profile', Step1BasicProfileForm),
        ('account_security', Step2AccountSecurityForm),
        ('education', Step3EducationExperienceForm),
        ('availability', Step4AvailabilityFormatForm),
        ('subjects', Step5SubjectsPricingForm),
        ('certificates', Step6CertificatesForm),
        ('video', Step7VideoForm),
    ]

    # Templates for each step
    templates = {
        'basic_profile': 'registration/step1_basic_profile.html',
        'account_security': 'registration/step2_account_security.html',
        'education': 'registration/step3_education.html',
        'availability': 'registration/step4_availability.html',
        'subjects': 'registration/step5_subjects.html',
        'certificates': 'registration/step6_certificates.html',
        'video': 'registration/step7_video.html',
    }
    
    # File storage for uploaded files during the wizard
    file_storage = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'temp_uploads'))
    
    def _is_google_registration(self):
        """Check if this wizard is being used by a Google OAuth user."""
        return self.request.session.get('is_google_teacher', False)

    def get_form_initial(self, step):
        """Pre-fill form data from Google session for Google users."""
        initial = super().get_form_initial(step)
        session = self.request.session

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

    def get_form(self, step=None, data=None, files=None):
        """Customize form for Google users (make password optional)."""
        form = super().get_form(step, data, files)

        if self._is_google_registration() and (step or self.steps.current) == 'account_security':
            form.fields['password1'].required = False
            form.fields['password2'].required = False
            form.fields['password1'].help_text = 'Необязательно для Google-аккаунта. Оставьте пустым, если хотите входить только через Google.'
            form.fields['email'].widget.attrs['readonly'] = True

        return form

    def get_template_names(self):
        """Return template for current step"""
        return [self.templates[self.steps.current]]

    def get_context_data(self, form, **kwargs):
        """Add context data for templates"""
        context = super().get_context_data(form=form, **kwargs)
        
        # Calculate progress
        current_step = self.steps.step1 + 1
        total_steps = len(self.form_list)
        progress_percentage = (current_step / total_steps) * 100
        
        # Step information
        step_titles = {
            'basic_profile': 'Основная информация',
            'account_security': 'Безопасность аккаунта',
            'education': 'Образование и опыт',
            'availability': 'Доступность и формат',
            'subjects': 'Предметы и цены',
            'certificates': 'Сертификаты',
            'video': 'Видео-визитка',
        }

        step_descriptions = {
            'basic_profile': 'Расскажите о себе. Эта информация будет видна ученикам.',
            'account_security': 'Создайте учетные данные для входа в систему.',
            'education': 'Укажите ваше образование и опыт преподавания.',
            'availability': 'Когда и как ученики могут с вами связаться?',
            'subjects': 'Какие предметы вы преподаете и по какой цене?',
            'certificates': 'Загрузите ваши сертификаты и дипломы (желательно).',
            'video': 'Загрузите короткое видео о себе (необязательно).',
        }
        
        context.update({
            'current_step': current_step,
            'total_steps': total_steps,
            'progress_percentage': progress_percentage,
            'step_title': step_titles.get(self.steps.current, ''),
            'step_description': step_descriptions.get(self.steps.current, ''),
            'is_last_step': current_step == total_steps,
            'is_google': self._is_google_registration(),
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
            
            # Log the user in
            login(self.request, user, backend='django.contrib.auth.backends.ModelBackend')

            # Clean up Google session data
            for key in ['google_first_name', 'google_last_name', 'google_email',
                        'google_user_id', 'is_google_teacher']:
                self.request.session.pop(key, None)

            # Success message
            messages.success(
                self.request,
                'Регистрация завершена! Ваш профиль отправлен на модерацию.'
            )
            
            # Redirect to completion page
            return redirect('teacher_register_complete')
            
        except Exception as e:
            messages.error(
                self.request,
                f'Произошла ошибка при сохранении данных: {str(e)}'
            )
            return redirect('teacher_register')
    
    def _create_user(self, form_data):
        """Create User object"""
        is_google = self._is_google_registration()
        password = form_data.get('password1') or None

        user = User.objects.create_user(
            username=form_data['username'],
            email=form_data['email'],
            password=password,
            first_name=form_data['first_name'],
            last_name=form_data['last_name'],
            phone=form_data['phone'],
            gender=form_data.get('gender'),
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
        
        # Convert weekdays list to comma-separated string (для обратной совместимости)
        available_weekdays = ','.join(form_data['available_weekdays'])
        
        # Построить индивидуальное расписание из данных формы
        weekly_schedule = {}
        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        
        for day in days:
            if form_data.get(f'{day}_enabled'):
                time_from = form_data.get(f'{day}_from')
                time_to = form_data.get(f'{day}_to')
                
                if time_from and time_to:
                    weekly_schedule[day] = {
                        'from': time_from.strftime('%H:%M'),
                        'to': time_to.strftime('%H:%M')
                    }
        
        # Определить общие available_from и available_to (берем из первого дня или значения по умолчанию)
        first_available_from = form_data.get('available_from')
        first_available_to = form_data.get('available_to')
        
        # Если не установлены, ищем первый день с временем
        if not first_available_from or not first_available_to:
            for day in days:
                if form_data.get(f'{day}_enabled'):
                    time_from = form_data.get(f'{day}_from')
                    time_to = form_data.get(f'{day}_to')
                    if time_from and time_to:
                        first_available_from = time_from
                        first_available_to = time_to
                        break
        
        teacher_profile = TeacherProfile.objects.create(
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
            available_from=first_available_from,
            available_to=first_available_to,
            available_weekdays=available_weekdays,
            weekly_schedule=weekly_schedule,  # Добавляем индивидуальное расписание
            moderation_status='pending',
            is_active=False  # Will be activated after moderation
        )
        
        return teacher_profile
    
    def _add_subjects(self, teacher_profile, form_data):
        """Add teacher subjects with pricing"""
        for i in range(1, 5):
            subject = form_data.get(f'subject_{i}')
            hourly_rate = form_data.get(f'hourly_rate_{i}')
            
            if subject and hourly_rate:
                TeacherSubject.objects.create(
                    teacher=teacher_profile,
                    subject=subject,
                    hourly_rate=hourly_rate,
                    is_free_trial=form_data.get(f'is_free_trial_{i}', False),
                    description=form_data.get(f'description_{i}', '')
                )
    
    def _add_certificates(self, teacher_profile, form_data):
        """
        Add certificates uploaded during the wizard
        Note: Certificates are optional. Only add if all required fields are filled.
        """
        # Only create certificate if file is provided
        if form_data.get('file'):
            # Проверяем, что заполнены name и issuer
            name = form_data.get('name')
            issuer = form_data.get('issuer')
            file = form_data.get('file')
            
            if name and issuer and file:
                try:
                    certificate = Certificate.objects.create(
                        name=name,
                        issuer=issuer,
                        file=file
                    )
                    teacher_profile.certificates.add(certificate)
                except Exception as e:
                    # Логируем ошибку, но не прерываем регистрацию
                    logger.error(f"Error adding certificate: {e}", exc_info=True)


def teacher_register_complete(request):
    """
    Completion page after successful registration
    Shows moderation pending message
    """
    # Check if user just completed registration
    if not request.user.is_authenticated:
        messages.warning(request, 'Пожалуйста, завершите регистрацию')
        return redirect('teacher_register')
    
    if not hasattr(request.user, 'teacher_profile'):
        messages.warning(request, 'Профиль учителя не найден')
        return redirect('home')
    
    teacher_profile = request.user.teacher_profile
    
    context = {
        'teacher_profile': teacher_profile,
        'user': request.user,
    }
    
    return render(request, 'registration/complete.html', context)
