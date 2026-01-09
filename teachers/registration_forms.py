"""
Professional multi-step teacher registration forms
Clean, user-friendly forms with proper validation and helper texts
"""
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from .models import User, TeacherProfile, Subject, City, Certificate, TeacherSubject
import re


class Step1BasicProfileForm(forms.Form):
    """
    STEP 1: Basic Profile Information
    Fields: Profile photo, First name, Last name, Gender, Teaching languages, Phone
    """
    GENDER_CHOICES = [
        ('', 'Выберите пол'),
        ('male', 'Мужской'),
        ('female', 'Женский'),
    ]
    
    avatar = forms.ImageField(
        required=False,
        label='Фото профиля',
        widget=forms.FileInput(attrs={
            'class': 'form-file-input',
            'accept': 'image/jpeg,image/jpg,image/png',
            'id': 'id_avatar'
        }),
        help_text='JPG, PNG. Максимум 5 МБ. Рекомендуемый размер: 300×300 px'
    )
    
    first_name = forms.CharField(
        max_length=150,
        required=True,
        label='Имя',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Введите ваше имя',
            'autocomplete': 'given-name'
        }),
        help_text='Имя, которое увидят ученики'
    )
    
    last_name = forms.CharField(
        max_length=150,
        required=True,
        label='Фамилия',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Введите вашу фамилию',
            'autocomplete': 'family-name'
        }),
        help_text='Фамилия для профиля'
    )
    
    gender = forms.ChoiceField(
        choices=GENDER_CHOICES,
        required=True,
        label='Пол',
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    teaching_languages = forms.MultipleChoiceField(
        choices=TeacherProfile.TEACHING_LANGUAGES,
        required=True,
        label='Языки преподавания',
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'teaching-language-checkbox'
        }),
        help_text='Выберите языки, на которых вы проводите занятия'
    )
    
    phone = forms.CharField(
        max_length=20,
        required=True,
        label='Номер телефона',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '+998 90 123 45 67',
            'autocomplete': 'tel'
        }),
        help_text='Формат: +998 XX XXX XX XX'
    )
    
    def clean_avatar(self):
        avatar = self.cleaned_data.get('avatar')
        if avatar:
            # Проверка размера файла (5 МБ максимум)
            if avatar.size > 5 * 1024 * 1024:
                raise ValidationError('Размер файла не должен превышать 5 МБ')
            
            # Проверка типа файла
            valid_extensions = ['.jpg', '.jpeg', '.png']
            ext = avatar.name.lower().split('.')[-1]
            if f'.{ext}' not in valid_extensions:
                raise ValidationError('Разрешены только JPG, JPEG и PNG форматы')
        
        return avatar
    
    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        # Удаляем пробелы и дефисы для проверки
        phone_digits = re.sub(r'[\s\-]', '', phone)
        
        # Проверяем формат узбекского номера
        if not re.match(r'^\+998\d{9}$', phone_digits):
            raise ValidationError('Введите корректный номер телефона в формате +998 XX XXX XX XX')
        
        return phone
    
    def clean_teaching_languages(self):
        # Получаем значение (должно быть список от CheckboxSelectMultiple)
        languages = self.cleaned_data.get('teaching_languages')
        
        if not languages or len(languages) == 0:
            raise ValidationError('Выберите хотя бы один язык преподавания')
        
        return languages


class Step2AccountSecurityForm(UserCreationForm):
    """
    STEP 1.5: Account Security
    Fields: Username, Password, Confirm Password
    """
    username = forms.CharField(
        max_length=150,
        required=True,
        label='Имя пользователя',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'john_teacher',
            'autocomplete': 'username'
        }),
        help_text='Используйте буквы, цифры и символы _ . -'
    )
    
    password1 = forms.CharField(
        label='Пароль',
        widget=forms.PasswordInput(attrs={
            'class': 'form-input',
            'placeholder': 'Введите надежный пароль',
            'autocomplete': 'new-password'
        }),
        help_text='Минимум 8 символов. Используйте буквы и цифры.'
    )
    
    password2 = forms.CharField(
        label='Подтвердите пароль',
        widget=forms.PasswordInput(attrs={
            'class': 'form-input',
            'placeholder': 'Повторите пароль',
            'autocomplete': 'new-password'
        }),
        help_text='Введите тот же пароль для подтверждения'
    )
    
    email = forms.EmailField(
        required=True,
        label='Email',
        widget=forms.EmailInput(attrs={
            'class': 'form-input',
            'placeholder': 'your.email@example.com',
            'autocomplete': 'email'
        }),
        help_text='Мы отправим подтверждение на этот адрес'
    )
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']
    
    def clean_username(self):
        username = self.cleaned_data.get('username')
        
        # Проверяем, что username содержит только разрешенные символы
        if not re.match(r'^[\w.-]+$', username):
            raise ValidationError('Используйте только буквы, цифры и символы _ . -')
        
        # Проверяем уникальность
        if User.objects.filter(username=username).exists():
            raise ValidationError('Это имя пользователя уже занято')
        
        return username
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise ValidationError('Этот email уже используется')
        return email


class Step3EducationExperienceForm(forms.Form):
    """
    STEP 2: Education & Experience
    Fields: Education level, Institution, Work experience, About me
    """
    education_level = forms.ChoiceField(
        choices=[('', 'Выберите уровень образования')] + list(TeacherProfile.EDUCATION_LEVELS),
        required=False,
        label='Уровень образования',
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        help_text='Выберите ваш наивысший уровень образования (желательно)'
    )
    
    university = forms.CharField(
        max_length=200,
        required=False,
        label='Учебное заведение',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Например: НУУз, ТГЭУ, Вестминстер...'
        }),
        help_text='Полное или краткое название вашего университета/института (желательно)'
    )
    
    specialization = forms.CharField(
        max_length=200,
        required=False,
        label='Специализация',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Например: Математическая физика, Филология...'
        }),
        help_text='Ваша академическая специализация (желательно)'
    )
    
    experience_years = forms.IntegerField(
        min_value=0,
        max_value=50,
        required=True,
        label='Опыт преподавания (лет)',
        widget=forms.NumberInput(attrs={
            'class': 'form-input',
            'placeholder': '0',
            'min': '0',
            'max': '50'
        }),
        help_text='Сколько лет вы преподаете? Укажите 0, если только начинаете'
    )
    
    bio = forms.CharField(
        required=True,
        label='О себе',
        widget=forms.Textarea(attrs={
            'class': 'form-textarea',
            'placeholder': 'Расскажите о себе как о преподавателе:\n• Ваш подход к обучению\n• Что вам нравится в преподавании\n• Чего достигли ваши ученики\n• Ваши профессиональные интересы',
            'rows': 6,
            'maxlength': '1000'
        }),
        help_text='От 100 до 1000 символов. Это первое, что увидят ученики.',
        min_length=100,
        max_length=1000
    )
    
    def clean_bio(self):
        bio = self.cleaned_data.get('bio')
        if bio:
            bio = bio.strip()
            if len(bio) < 100:
                raise ValidationError(f'Описание слишком короткое. Минимум 100 символов (сейчас: {len(bio)})')
            if len(bio) > 1000:
                raise ValidationError(f'Описание слишком длинное. Максимум 1000 символов (сейчас: {len(bio)})')
        return bio


class Step4AvailabilityFormatForm(forms.Form):
    """
    STEP 3: Availability & Format
    Fields: Telegram, Location, Teaching format, Working hours
    """
    telegram = forms.CharField(
        max_length=100,
        required=True,
        label='Telegram',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '@username или +998901234567'
        }),
        help_text='Ученики свяжутся с вами через Telegram'
    )
    
    city = forms.ModelChoiceField(
        queryset=City.objects.filter(is_active=True),
        required=False,
        label='Город',
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        empty_label='Не указан / Онлайн',
        help_text='Выберите город, если преподаете офлайн'
    )
    
    teaching_format = forms.ChoiceField(
        choices=TeacherProfile.TEACHING_FORMATS,
        required=True,
        label='Формат обучения',
        widget=forms.RadioSelect(attrs={
            'class': 'format-radio'
        }),
        help_text='Как вы проводите занятия?'
    )
    
    whatsapp = forms.CharField(
        max_length=20,
        required=False,
        label='WhatsApp (желательно)',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '+998 90 123 45 67'
        }),
        help_text='Дополнительный способ связи'
    )
    
    # Расписание для каждого дня недели (отдельные поля)
    # Понедельник
    monday_enabled = forms.BooleanField(required=False, label='Понедельник')
    monday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    monday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Вторник
    tuesday_enabled = forms.BooleanField(required=False, label='Вторник')
    tuesday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    tuesday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Среда
    wednesday_enabled = forms.BooleanField(required=False, label='Среда')
    wednesday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    wednesday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Четверг
    thursday_enabled = forms.BooleanField(required=False, label='Четверг')
    thursday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    thursday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Пятница
    friday_enabled = forms.BooleanField(required=False, label='Пятница')
    friday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    friday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Суббота
    saturday_enabled = forms.BooleanField(required=False, label='Суббота')
    saturday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    saturday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Воскресенье
    sunday_enabled = forms.BooleanField(required=False, label='Воскресенье')
    sunday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    sunday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Скрытое поле для хранения всех дней (для обратной совместимости с моделью)
    available_weekdays = forms.CharField(required=False, widget=forms.HiddenInput())
    available_from = forms.TimeField(required=False, widget=forms.HiddenInput())
    available_to = forms.TimeField(required=False, widget=forms.HiddenInput())
    
    def clean_telegram(self):
        telegram = self.cleaned_data.get('telegram')
        if telegram:
            telegram = telegram.strip()
            # Проверяем, что это либо @username, либо номер телефона
            if not (telegram.startswith('@') or telegram.startswith('+')):
                raise ValidationError('Введите Telegram в формате @username или +998...')
        return telegram
    
    def clean(self):
        cleaned_data = super().clean()
        
        # Проверяем, что выбран хотя бы один день
        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        has_any_day = False
        
        for day in days:
            enabled = cleaned_data.get(f'{day}_enabled')
            time_from = cleaned_data.get(f'{day}_from')
            time_to = cleaned_data.get(f'{day}_to')
            
            if enabled:
                has_any_day = True
                
                # Проверяем, что если день выбран, то указаны оба времени
                if not time_from or not time_to:
                    day_name_ru = {
                        'monday': 'Понедельник',
                        'tuesday': 'Вторник',
                        'wednesday': 'Среда',
                        'thursday': 'Четверг',
                        'friday': 'Пятница',
                        'saturday': 'Суббота',
                        'sunday': 'Воскресенье'
                    }[day]
                    raise ValidationError(f'{day_name_ru}: укажите время начала и окончания')
                
                # Проверяем, что время начала раньше времени окончания
                if time_from >= time_to:
                    day_name_ru = {
                        'monday': 'Понедельник',
                        'tuesday': 'Вторник',
                        'wednesday': 'Среда',
                        'thursday': 'Четверг',
                        'friday': 'Пятница',
                        'saturday': 'Суббота',
                        'sunday': 'Воскресенье'
                    }[day]
                    raise ValidationError(f'{day_name_ru}: время начала должно быть раньше времени окончания')
        
        if not has_any_day:
            raise ValidationError('Выберите хотя бы один рабочий день')
        
        # Формируем данные для обратной совместимости
        enabled_days = []
        day_mapping = {
            'monday': '1',
            'tuesday': '2',
            'wednesday': '3',
            'thursday': '4',
            'friday': '5',
            'saturday': '6',
            'sunday': '7'
        }
        
        for day in days:
            if cleaned_data.get(f'{day}_enabled'):
                enabled_days.append(day_mapping[day])
        
        cleaned_data['available_weekdays'] = enabled_days
        
        return cleaned_data


class Step5SubjectsPricingForm(forms.Form):
    """
    STEP 4: Subjects & Pricing
    Dynamic form for up to 4 subjects with pricing
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Создаем поля для 4 предметов
        for i in range(1, 5):
            self.fields[f'subject_{i}'] = forms.ModelChoiceField(
                queryset=Subject.objects.filter(is_active=True).order_by('name'),
                required=False,
                label=f'Предмет {i}',
                widget=forms.Select(attrs={
                    'class': 'form-select subject-select',
                    'data-subject-num': i
                }),
                empty_label='Выберите предмет'
            )
            
            self.fields[f'hourly_rate_{i}'] = forms.DecimalField(
                max_digits=10,
                decimal_places=2,
                required=False,
                label='Цена за час (сум)',
                widget=forms.NumberInput(attrs={
                    'class': 'form-input',
                    'placeholder': '100000',
                    'min': '0',
                    'step': '1000',
                    'data-subject-num': i
                })
            )
            
            self.fields[f'is_free_trial_{i}'] = forms.BooleanField(
                required=False,
                label='Пробный урок бесплатно',
                widget=forms.CheckboxInput(attrs={
                    'class': 'form-checkbox',
                    'data-subject-num': i
                })
            )
            
            self.fields[f'description_{i}'] = forms.CharField(
                required=False,
                label='Описание (желательно)',
                widget=forms.Textarea(attrs={
                    'class': 'form-textarea',
                    'placeholder': 'Особенности преподавания этого предмета...',
                    'rows': 2,
                    'maxlength': '200',
                    'data-subject-num': i
                }),
                max_length=200
            )
    
    def clean(self):
        cleaned_data = super().clean()
        subjects_added = 0
        selected_subjects = []
        
        for i in range(1, 5):
            subject = cleaned_data.get(f'subject_{i}')
            hourly_rate = cleaned_data.get(f'hourly_rate_{i}')
            
            if subject:
                # Проверяем, что не выбран дубликат
                if subject in selected_subjects:
                    raise ValidationError(f'Предмет "{subject}" выбран несколько раз. Выберите разные предметы.')
                
                # Проверяем, что указана цена
                if not hourly_rate or hourly_rate <= 0:
                    raise ValidationError(f'Укажите цену для предмета "{subject}"')
                
                selected_subjects.append(subject)
                subjects_added += 1
            elif hourly_rate and hourly_rate > 0:
                # Указана цена, но не выбран предмет
                raise ValidationError(f'Выберите предмет для строки {i}')
        
        # Проверяем, что добавлен хотя бы один предмет
        if subjects_added == 0:
            raise ValidationError('Добавьте хотя бы один предмет с ценой')
        
        cleaned_data['subjects_count'] = subjects_added
        return cleaned_data


class Step6CertificatesForm(forms.ModelForm):
    """
    STEP 5: Certificates (Optional)
    Upload certificates/diplomas
    """
    name = forms.CharField(
        max_length=200,
        required=False,
        label='Название сертификата',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Например: Сертификат IELTS, Диплом о высшем образовании'
        }),
        help_text='Что это за сертификат? (желательно)'
    )
    
    issuer = forms.CharField(
        max_length=200,
        required=False,
        label='Кто выдал',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Например: British Council, НУУз'
        }),
        help_text='Организация или учреждение (желательно)'
    )
    
    file = forms.FileField(
        required=False,
        label='Файл сертификата',
        widget=forms.FileInput(attrs={
            'class': 'form-file-input',
            'accept': 'image/*,application/pdf'
        }),
        help_text='PDF, JPG или PNG. Максимум 10 МБ (желательно)'
    )
    
    class Meta:
        model = Certificate
        fields = ['name', 'issuer', 'file']
    
    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get('name')
        issuer = cleaned_data.get('issuer')
        file = cleaned_data.get('file')
        
        # Если загружен файл, то name и issuer тоже обязательны
        if file:
            if not name:
                raise ValidationError('Укажите название сертификата для загруженного файла')
            if not issuer:
                raise ValidationError('Укажите, кто выдал сертификат')
        
        # Если указаны name и issuer, то файл обязателен
        if (name or issuer) and not file:
            raise ValidationError('Загрузите файл сертификата')
        
        return cleaned_data
    
    def clean_file(self):
        file = self.cleaned_data.get('file')
        if file:
            # Проверка размера файла (10 МБ максимум)
            if file.size > 10 * 1024 * 1024:
                raise ValidationError('Размер файла не должен превышать 10 МБ')
            
            # Проверка типа файла
            valid_extensions = ['.jpg', '.jpeg', '.png', '.pdf']
            ext = file.name.lower().split('.')[-1]
            if f'.{ext}' not in valid_extensions:
                raise ValidationError('Разрешены только JPG, PNG и PDF форматы')
        
        return file
