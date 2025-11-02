from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from .models import User, TeacherProfile, StudentProfile, Subject, City, Certificate, TeacherSubject, Message, Conversation


class TeacherRegistrationForm(UserCreationForm):
    """Форма регистрации пользователя как учителя"""
    
    # ========== ЛИЧНАЯ ИНФОРМАЦИЯ ==========
    first_name = forms.CharField(
        max_length=150,
        required=True,
        label=_('Имя'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': _('Введите ваше имя')
        })
    )
    
    last_name = forms.CharField(
        max_length=150,
        required=True,
        label=_('Фамилия'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': _('Введите вашу фамилию')
        })
    )
    
    email = forms.EmailField(
        required=True,
        label=_('Email'),
        widget=forms.EmailInput(attrs={
            'class': 'form-input',
            'placeholder': 'example@mail.com'
        })
    )
    
    phone = forms.CharField(
        max_length=20,
        required=False,
        label=_('Телефон'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '+998 90 123 45 67'
        })
    )
    
    age = forms.IntegerField(
        min_value=18,
        max_value=100,
        required=False,
        label=_('Возраст'),
        widget=forms.NumberInput(attrs={
            'class': 'form-input',
            'placeholder': '25'
        })
    )
    
    avatar = forms.ImageField(
        required=False,
        label=_('Фото профиля'),
        widget=forms.FileInput(attrs={
            'class': 'form-file',
            'accept': 'image/*'
        }),
        help_text=_('Рекомендуемый размер: 300x300px')
    )
    
    # ========== ПРОФЕССИОНАЛЬНАЯ ИНФОРМАЦИЯ ==========
    bio = forms.CharField(
        required=False,
        label=_('О себе'),
        widget=forms.Textarea(attrs={
            'class': 'form-textarea',
            'placeholder': _('Расскажите о своем опыте, методике преподавания, достижениях...'),
            'rows': 5
        }),
        help_text=_('Минимум 50 символов'),
        min_length=50,
        max_length=1000
    )
    
    education_level = forms.ChoiceField(
        choices=TeacherProfile.EDUCATION_LEVELS,
        required=False,
        label=_('Уровень образования'),
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    university = forms.CharField(
        max_length=200,
        required=False,
        label=_('Учебное заведение'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': _('Название университета/института')
        })
    )
    
    specialization = forms.CharField(
        max_length=200,
        required=False,
        label=_('Специализация'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': _('Ваша специальность')
        })
    )
    
    experience_years = forms.IntegerField(
        min_value=0,
        max_value=50,
        required=True,
        label=_('Опыт преподавания (лет)'),
        widget=forms.NumberInput(attrs={
            'class': 'form-input',
            'placeholder': '5'
        })
    )
    
    teaching_languages = forms.MultipleChoiceField(
        choices=TeacherProfile.TEACHING_LANGUAGES,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'language-checkbox'
        }),
        label=_('Языки преподавания'),
        help_text=_('Выберите один или несколько языков')
    )
    
    # ========== МЕСТОПОЛОЖЕНИЕ И ФОРМАТ ==========
    city = forms.ModelChoiceField(
        queryset=City.objects.filter(is_active=True),
        required=False,
        label=_('Город'),
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        empty_label=_('Выберите город')
    )
    
    teaching_format = forms.ChoiceField(
        choices=TeacherProfile.TEACHING_FORMATS,
        required=True,
        label=_('Формат обучения'),
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    # ========== КОНТАКТЫ ==========
    telegram = forms.CharField(
        max_length=100,
        required=False,
        label=_('Telegram'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': _('@username или номер телефона')
        })
    )
    
    whatsapp = forms.CharField(
        max_length=20,
        required=False,
        label=_('WhatsApp'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '+998 90 123 45 67'
        })
    )
    
    # ========== ВРЕМЯ РАБОТЫ ==========
    available_from = forms.TimeField(
        required=True,
        label=_('Доступен с'),
        widget=forms.TimeInput(attrs={
            'class': 'form-input',
            'type': 'time'
        }),
        initial='09:00'
    )
    
    available_to = forms.TimeField(
        required=True,
        label=_('Доступен до'),
        widget=forms.TimeInput(attrs={
            'class': 'form-input',
            'type': 'time'
        }),
        initial='21:00'
    )
    
    available_weekdays = forms.MultipleChoiceField(
        choices=[
            ('1', _('Понедельник')),
            ('2', _('Вторник')),
            ('3', _('Среда')),
            ('4', _('Четверг')),
            ('5', _('Пятница')),
            ('6', _('Суббота')),
            ('7', _('Воскресенье')),
        ],
        required=True,
        label=_('Рабочие дни'),
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'form-checkbox'
        })
    )
    
    # ========== СОГЛАСИЕ ==========
    terms_accepted = forms.BooleanField(
        required=True,
        label=_('Я принимаю условия использования и политику конфиденциальности'),
        widget=forms.CheckboxInput(attrs={
            'class': 'form-checkbox'
        })
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2', 'first_name', 
                'last_name', 'phone', 'age', 'avatar']
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': 'username'
        })
        self.fields['password1'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': _('Введите пароль')
        })
        self.fields['password2'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': _('Повторите пароль')
        })
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and User.objects.filter(email=email).exists():
            raise ValidationError(_('Этот email уже используется'))
        return email
    
    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if phone and User.objects.filter(phone=phone).exists():
            raise ValidationError(_('Этот номер телефона уже используется'))
        return phone
    
    def clean_available_weekdays(self):
        days = self.cleaned_data.get('available_weekdays')
        if not days:
            raise ValidationError(_('Выберите хотя бы один рабочий день'))
        return ','.join(days)
    
    def clean_teaching_languages(self):
        languages = self.cleaned_data.get('teaching_languages')
        if not languages:
            raise ValidationError(_('Выберите хотя бы один язык преподавания'))
        return languages
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.user_type = 'teacher'
        user.email = self.cleaned_data['email']
        user.phone = self.cleaned_data['phone']
        user.age = self.cleaned_data['age']
        
        if commit:
            user.save()
            
            languages_str = ','.join(self.cleaned_data['teaching_languages'])

            teacher_profile = TeacherProfile.objects.create(
                user=user,
                bio=self.cleaned_data['bio'],
                education_level=self.cleaned_data['education_level'],
                university=self.cleaned_data['university'],
                specialization=self.cleaned_data['specialization'],
                experience_years=self.cleaned_data['experience_years'],
                city=self.cleaned_data.get('city'),
                teaching_format=self.cleaned_data['teaching_format'],
                telegram=self.cleaned_data.get('telegram', ''),
                whatsapp=self.cleaned_data.get('whatsapp', ''),
                available_from=self.cleaned_data['available_from'],
                available_to=self.cleaned_data['available_to'],
                teaching_languages=languages_str,
                available_weekdays=self.cleaned_data['available_weekdays'],
                moderation_status='pending',
                is_active=False
            )
            
        return user


class TeacherSubjectsForm(forms.Form):
    """Форма для добавления предметов и цен"""
    
    def __init__(self, *args, **kwargs):
        teacher = kwargs.pop('teacher', None)
        super().__init__(*args, **kwargs)
        
        subjects = Subject.objects.filter(is_active=True)
        
        for i in range(1, 6):
            self.fields[f'subject_{i}'] = forms.ModelChoiceField(
                queryset=subjects,
                required=False,
                label=_('Предмет %(number)d') % {'number': i},
                widget=forms.Select(attrs={
                    'class': 'form-select',
                    'onchange': f'togglePriceField({i})'
                }),
                empty_label=_('Выберите предмет')
            )
            
            self.fields[f'hourly_rate_{i}'] = forms.DecimalField(
                max_digits=10,
                decimal_places=2,
                required=False,
                label=_('Цена за час (сум)'),
                min_value=0,
                widget=forms.NumberInput(attrs={
                    'class': 'form-input',
                    'placeholder': '50000',
                    'id': f'price_{i}'
                })
            )
            
            self.fields[f'is_free_trial_{i}'] = forms.BooleanField(
                required=False,
                label=_('Бесплатное пробное занятие'),
                widget=forms.CheckboxInput(attrs={
                    'class': 'form-checkbox'
                })
            )
            
            self.fields[f'description_{i}'] = forms.CharField(
                required=False,
                label=_('Описание'),
                widget=forms.Textarea(attrs={
                    'class': 'form-textarea',
                    'rows': 2,
                    'placeholder': _('Дополнительная информация о преподавании этого предмета')
                }),
                max_length=500
            )
    
    def clean(self):
        cleaned_data = super().clean()
        
        has_subject = False
        for i in range(1, 6):
            subject = cleaned_data.get(f'subject_{i}')
            hourly_rate = cleaned_data.get(f'hourly_rate_{i}')
            
            if subject:
                has_subject = True
                if not hourly_rate or hourly_rate <= 0:
                    raise ValidationError(_('Укажите цену для предмета %(number)d') % {'number': i})
        
        if not has_subject:
            raise ValidationError(_('Добавьте хотя бы один предмет'))
        
        return cleaned_data


class CertificateUploadForm(forms.ModelForm):
    """Форма для загрузки сертификатов"""
    
    class Meta:
        model = Certificate
        fields = ['name', 'issuer', 'file']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': _('Название сертификата')
            }),
            'issuer': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': _('Кто выдал')
            }),
            'file': forms.FileInput(attrs={
                'class': 'form-file',
                'accept': '.pdf,.jpg,.jpeg,.png'
            })
        }
        labels = {
            'name': _('Название сертификата'),
            'issuer': _('Организация/учреждение'),
            'file': _('Файл сертификата')
        }


class LoginForm(AuthenticationForm):
    """Форма входа"""
    username = forms.CharField(
        label=_('Имя пользователя или Email'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': _('Введите имя пользователя или email'),
            'autofocus': True
        })
    )
    
    password = forms.CharField(
        label=_('Пароль'),
        widget=forms.PasswordInput(attrs={
            'class': 'form-input',
            'placeholder': _('Введите пароль')
        })
    )
    
    remember_me = forms.BooleanField(
        required=False,
        initial=True,
        label=_('Запомнить меня'),
        widget=forms.CheckboxInput(attrs={
            'class': 'form-checkbox'
        })
    )


class StudentRegistrationForm(UserCreationForm):
    """Форма регистрации ученика"""
    
    first_name = forms.CharField(
        max_length=150,
        required=True,
        label=_('Имя'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': _('Введите ваше имя')
        })
    )
    
    last_name = forms.CharField(
        max_length=150,
        required=True,
        label=_('Фамилия'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': _('Введите вашу фамилию')
        })
    )
    
    email = forms.EmailField(
        required=False,
        label=_('Email'),
        widget=forms.EmailInput(attrs={
            'class': 'form-input',
            'placeholder': 'example@mail.com'
        })
    )
    
    phone = forms.CharField(
        max_length=20,
        required=True,
        label=_('Телефон'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '+998 90 123 45 67'
        })
    )
    
    age = forms.IntegerField(
        min_value=10,
        max_value=100,
        required=False,
        label=_('Возраст'),
        widget=forms.NumberInput(attrs={
            'class': 'form-input',
            'placeholder': '18'
        })
    )
    
    city = forms.ModelChoiceField(
        queryset=City.objects.filter(is_active=True),
        required=False,
        label=_('Город'),
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        empty_label=_('Выберите город')
    )
    
    interests = forms.ModelMultipleChoiceField(
        queryset=Subject.objects.filter(is_active=True),
        required=True,
        label=_('Предметы, которые хочу изучать'),
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'subject-checkbox'
        }),
        help_text=_('Выберите один или несколько предметов')
    )
    
    bio = forms.CharField(
        required=False,
        label=_('Расскажите о ваших целях'),
        widget=forms.Textarea(attrs={
            'class': 'form-textarea',
            'placeholder': _('Например: Хочу подготовиться к экзаменам, улучшить знания по математике, изучить английский с нуля...'),
            'rows': 4
        }),
        help_text=_('Минимум 20 символов - это поможет учителям лучше понять ваши потребности'),
        min_length=20,
        max_length=1000
    )
    
    education_level = forms.ChoiceField(
        choices=StudentProfile.EDUCATION_LEVELS,
        required=False,
        label=_('Уровень образования'),
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    school_university = forms.CharField(
        max_length=200,
        required=False,
        label=_('Школа/Университет'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': _('Название вашей школы или университета')
        })
    )
    
    learning_format = forms.ChoiceField(
        choices=StudentProfile.LEARNING_FORMATS,
        required=True,
        label=_('Предпочитаемый формат обучения'),
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        initial='both'
    )
    
    budget_min = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        label=_('Минимальный бюджет (сум/час)'),
        min_value=0,
        widget=forms.NumberInput(attrs={
            'class': 'form-input',
            'placeholder': '30000',
            'step': '1000'
        }),
        help_text=_('Минимальная цена, которую готовы платить')
    )
    
    budget_max = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        label=_('Максимальный бюджет (сум/час)'),
        min_value=0,
        widget=forms.NumberInput(attrs={
            'class': 'form-input',
            'placeholder': '100000',
            'step': '1000'
        }),
        help_text=_('Максимальная цена, которую готовы платить')
    )
    
    telegram = forms.CharField(
        max_length=100,
        required=False,
        label=_('Telegram'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': _('@username или +998901234567')
        }),
        help_text=_('Укажите ваш Telegram для удобной связи с учителями')
    )
    
    whatsapp = forms.CharField(
        max_length=20,
        required=False,
        label=_('WhatsApp'),
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '+998 90 123 45 67'
        }),
        help_text=_('Укажите номер WhatsApp для связи')
    )
    
    available_weekdays = forms.MultipleChoiceField(
        choices=[
            ('1', _('Понедельник')),
            ('2', _('Вторник')),
            ('3', _('Среда')),
            ('4', _('Четверг')),
            ('5', _('Пятница')),
            ('6', _('Суббота')),
            ('7', _('Воскресенье')),
        ],
        required=False,
        label=_('Доступные дни для занятий'),
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'weekday-checkbox'
        }),
        help_text=_('Выберите удобные дни для занятий')
    )
    
    terms_accepted = forms.BooleanField(
        required=True,
        label=_('Я принимаю условия использования и политику конфиденциальности'),
        widget=forms.CheckboxInput(attrs={
            'class': 'form-checkbox'
        })
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2', 'first_name', 
                'last_name', 'phone', 'age']
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': 'username'
        })
        self.fields['password1'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': _('Введите пароль')
        })
        self.fields['password2'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': _('Повторите пароль')
        })
        
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and User.objects.filter(email=email).exists():
            raise ValidationError(_('Этот email уже используется'))
        return email
    
    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if phone and User.objects.filter(phone=phone).exists():
            raise ValidationError(_('Этот номер телефона уже используется'))
        return phone
    
    def clean_interests(self):
        interests = self.cleaned_data.get('interests')
        if not interests:
            raise ValidationError(_('Выберите хотя бы один предмет'))
        if interests.count() > 10:
            raise ValidationError(_('Можно выбрать максимум 10 предметов'))
        return interests
    
    def clean(self):
        cleaned_data = super().clean()
        budget_min = cleaned_data.get('budget_min')
        budget_max = cleaned_data.get('budget_max')
        
        if budget_min and budget_max and budget_min > budget_max:
            raise ValidationError(_('Максимальный бюджет должен быть больше минимального'))
        
        return cleaned_data
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.user_type = 'student'
        user.email = self.cleaned_data['email']
        user.phone = self.cleaned_data['phone']
        user.age = self.cleaned_data.get('age')
        
        if commit:
            user.save()
            
            student_profile = StudentProfile.objects.create(
                user=user,
                bio=self.cleaned_data.get('bio', ''),
                description=self.cleaned_data.get('bio', ''),
                education_level=self.cleaned_data.get('education_level', ''),
                school_university=self.cleaned_data.get('school_university', ''),
                city=self.cleaned_data.get('city'),
                learning_format=self.cleaned_data.get('learning_format', 'both'),
                budget_min=self.cleaned_data.get('budget_min'),
                budget_max=self.cleaned_data.get('budget_max'),
                telegram=self.cleaned_data.get('telegram', ''),
                whatsapp=self.cleaned_data.get('whatsapp', ''),
                available_weekdays=','.join(self.cleaned_data.get('available_weekdays', [])) if self.cleaned_data.get('available_weekdays') else '1,2,3,4,5,6,7',
                is_active=True
            )
            
            interests = self.cleaned_data['interests']
            student_profile.interests.set(interests)
            student_profile.desired_subjects.set(interests)
            
        return user


class TeacherProfileEditForm(forms.ModelForm):
    """Форма редактирования профиля учителя"""
    
    teaching_languages = forms.MultipleChoiceField(
        choices=TeacherProfile.TEACHING_LANGUAGES,
        required=True,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'language-checkbox'
        }),
        label=_('Языки преподавания')
    )
    
    available_weekdays = forms.MultipleChoiceField(
        choices=[
            ('1', _('Понедельник')),
            ('2', _('Вторник')),
            ('3', _('Среда')),
            ('4', _('Четверг')),
            ('5', _('Пятница')),
            ('6', _('Суббота')),
            ('7', _('Воскресенье')),
        ],
        required=True,
        label=_('Рабочие дни'),
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'weekday-checkbox'
        })
    )
    
    class Meta:
        model = TeacherProfile
        fields = [
            'bio', 'education_level', 'university', 'specialization',
            'experience_years', 'city', 'teaching_format', 'telegram',
            'whatsapp', 'available_from', 'available_to', 'is_active'
        ]
        widgets = {
            'bio': forms.Textarea(attrs={
                'class': 'form-textarea',
                'rows': 5,
                'placeholder': _('Расскажите о своем опыте преподавания...')
            }),
            'education_level': forms.Select(attrs={'class': 'form-select'}),
            'university': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': _('Название университета')
            }),
            'specialization': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': _('Ваша специальность')
            }),
            'experience_years': forms.NumberInput(attrs={
                'class': 'form-input',
                'min': 0,
                'max': 50
            }),
            'city': forms.Select(attrs={'class': 'form-select'}),
            'teaching_format': forms.Select(attrs={'class': 'form-select'}),
            'telegram': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': '@username'
            }),
            'whatsapp': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': '+998 90 123 45 67'
            }),
            'available_from': forms.TimeInput(attrs={
                'class': 'form-input',
                'type': 'time'
            }),
            'available_to': forms.TimeInput(attrs={
                'class': 'form-input',
                'type': 'time'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-checkbox-large'
            })
        }
        labels = {
            'bio': _('О себе'),
            'education_level': _('Уровень образования'),
            'university': _('Учебное заведение'),
            'specialization': _('Специализация'),
            'experience_years': _('Опыт преподавания (лет)'),
            'city': _('Город'),
            'teaching_format': _('Формат обучения'),
            'telegram': _('Telegram'),
            'whatsapp': _('WhatsApp'),
            'available_from': _('Доступен с'),
            'available_to': _('Доступен до'),
            'is_active': _('Отображать в поиске учителей')
        }
        help_texts = {
            'is_active': _('Если отключено, ваш профиль не будет показываться в поиске')
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        if self.instance and self.instance.pk:
            if self.instance.teaching_languages:
                self.initial['teaching_languages'] = self.instance.teaching_languages.split(',')
            
            if self.instance.available_weekdays:
                self.initial['available_weekdays'] = self.instance.available_weekdays.split(',')
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        
        instance.teaching_languages = ','.join(self.cleaned_data['teaching_languages'])
        instance.available_weekdays = ','.join(self.cleaned_data['available_weekdays'])
        
        if commit:
            instance.save()
        
        return instance


class StudentProfileEditForm(forms.ModelForm):
    """Форма редактирования профиля ученика"""
    
    interests = forms.ModelMultipleChoiceField(
        queryset=Subject.objects.filter(is_active=True),
        required=False,
        label=_('Интересующие предметы'),
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'subject-checkbox'
        })
    )
    
    desired_subjects = forms.ModelMultipleChoiceField(
        queryset=Subject.objects.filter(is_active=True),
        required=True,
        label=_('Предметы для изучения'),
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'subject-checkbox'
        }),
        help_text=_('Выберите предметы, которые хотите изучать')
    )
    
    available_weekdays = forms.MultipleChoiceField(
        choices=[
            ('1', _('Понедельник')),
            ('2', _('Вторник')),
            ('3', _('Среда')),
            ('4', _('Четверг')),
            ('5', _('Пятница')),
            ('6', _('Суббота')),
            ('7', _('Воскресенье')),
        ],
        required=False,
        label=_('Доступные дни для занятий'),
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'weekday-checkbox'
        })
    )
    
    class Meta:
        model = StudentProfile
        fields = [
            'bio', 'description', 'education_level', 'school_university',
            'city', 'learning_format', 'budget_min', 'budget_max',
            'telegram', 'whatsapp',
            'is_active'
        ]
        widgets = {
            'bio': forms.Textarea(attrs={
                'class': 'form-textarea',
                'rows': 3,
                'placeholder': _('Краткое описание...')
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-textarea',
                'rows': 5,
                'placeholder': _('Расскажите о своих целях обучения...')
            }),
            'education_level': forms.Select(attrs={'class': 'form-select'}),
            'school_university': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': _('Название школы/университета')
            }),
            'city': forms.Select(attrs={'class': 'form-select'}),
            'learning_format': forms.Select(attrs={'class': 'form-select'}),
            'budget_min': forms.NumberInput(attrs={
                'class': 'form-input',
                'placeholder': '30000',
                'step': '1000'
            }),
            'budget_max': forms.NumberInput(attrs={
                'class': 'form-input',
                'placeholder': '100000',
                'step': '1000'
            }),
            'telegram': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': _('@username или +998901234567')
            }),
            'whatsapp': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': '+998 90 123 45 67'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-checkbox-large'
            })
        }
        labels = {
            'bio': _('Краткое описание'),
            'description': _('Подробное описание целей'),
            'education_level': _('Уровень образования'),
            'school_university': _('Школа/Университет'),
            'city': _('Город'),
            'learning_format': _('Предпочитаемый формат'),
            'budget_min': _('Минимальный бюджет (сум/час)'),
            'budget_max': _('Максимальный бюджет (сум/час)'),
            'telegram': _('Telegram'),
            'whatsapp': _('WhatsApp'),
            'is_active': _('Отображать в поиске учеников')
        }
        help_texts = {
            'is_active': _('Если отключено, ваш профиль не будет показываться в поиске'),
            'budget_min': _('Минимальная цена, которую готовы платить'),
            'budget_max': _('Максимальная цена, которую готовы платить'),
            'telegram': _('Ваш Telegram username или номер телефона'),
            'whatsapp': _('Номер WhatsApp для связи'),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        if self.instance and self.instance.pk:
            self.initial['interests'] = self.instance.interests.all()
            self.initial['desired_subjects'] = self.instance.desired_subjects.all()
            
            if self.instance.available_weekdays:
                self.initial['available_weekdays'] = self.instance.available_weekdays.split(',')
    
    def clean(self):
        cleaned_data = super().clean()
        budget_min = cleaned_data.get('budget_min')
        budget_max = cleaned_data.get('budget_max')
        
        if budget_min and budget_max and budget_min > budget_max:
            raise forms.ValidationError(
                _('Максимальный бюджет должен быть больше минимального')
            )
        
        return cleaned_data
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        
        weekdays = self.cleaned_data.get('available_weekdays', [])
        instance.available_weekdays = ','.join(weekdays) if weekdays else '1,2,3,4,5,6,7'
        
        if commit:
            instance.save()
            
            self.save_m2m()
            
            interests = self.cleaned_data.get('interests', [])
            desired_subjects = self.cleaned_data.get('desired_subjects', [])
            
            instance.interests.set(interests)
            instance.desired_subjects.set(desired_subjects)
        
        return instance


class UserProfileEditForm(forms.ModelForm):
    """Форма редактирования базовой информации пользователя"""
    
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone', 'age', 'avatar']
        widgets = {
            'first_name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': _('Ваше имя')
            }),
            'last_name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': _('Ваша фамилия')
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-input',
                'placeholder': 'email@example.com'
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': '+998 90 123 45 67'
            }),
            'age': forms.NumberInput(attrs={
                'class': 'form-input',
                'min': 10,
                'max': 100
            }),
            'avatar': forms.FileInput(attrs={
                'class': 'form-file',
                'accept': 'image/*'
            })
        }
        labels = {
            'first_name': _('Имя'),
            'last_name': _('Фамилия'),
            'email': _('Email'),
            'phone': _('Телефон'),
            'age': _('Возраст'),
            'avatar': _('Фото профиля')
        }
        help_texts = {
            'avatar': _('Рекомендуемый размер: 300x300px')
        }
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            existing = User.objects.filter(email=email).exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError(_('Этот email уже используется'))
        return email
    
    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if phone:
            existing = User.objects.filter(phone=phone).exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError(_('Этот номер телефона уже используется'))
        return phone


class MessageForm(forms.ModelForm):
    """Форма для отправки сообщения"""
    
    class Meta:
        model = Message
        fields = ['content']
        widgets = {
            'content': forms.Textarea(attrs={
                'class': 'form-textarea message-input',
                'placeholder': _('Введите ваше сообщение...'),
                'rows': 4,
                'maxlength': 2000,
                'required': True
            })
        }
        labels = {
            'content': _('Сообщение')
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content'].required = True
        self.fields['content'].widget.attrs.update({
            'id': 'message-content'
        })
    
    def clean_content(self):
        content = self.cleaned_data.get('content', '').strip()
        if not content:
            raise forms.ValidationError(_('Сообщение не может быть пустым'))
        if len(content) < 1:
            raise forms.ValidationError(_('Сообщение слишком короткое'))
        if len(content) > 2000:
            raise forms.ValidationError(_('Сообщение слишком длинное (максимум 2000 символов)'))
        return content