# # forms.py
# from django import forms
# from django.contrib.auth.forms import UserCreationForm
# from django.core.exceptions import ValidationError
# from django.contrib.auth import get_user_model
# from .models import (
#     User, TeacherProfile, StudentProfile, Subject, City, 
#     TeacherSubject, Certificate, Message, Review
# )

# User = get_user_model()

# class CustomUserCreationForm(UserCreationForm):
#     """Форма регистрации пользователя"""
#     email = forms.EmailField(
#         required=True,
#         widget=forms.EmailInput(attrs={
#             'class': 'form-control',
#             'placeholder': 'example@mail.com'
#         })
#     )
#     user_type = forms.ChoiceField(
#         choices=User.USER_TYPES,
#         widget=forms.Select(attrs={'class': 'form-control'})
#     )
#     first_name = forms.CharField(
#         max_length=150,
#         widget=forms.TextInput(attrs={
#             'class': 'form-control',
#             'placeholder': 'Ваше имя'
#         })
#     )
#     last_name = forms.CharField(
#         max_length=150,
#         widget=forms.TextInput(attrs={
#             'class': 'form-control',
#             'placeholder': 'Ваша фамилия'
#         })
#     )
#     age = forms.IntegerField(
#         min_value=10,
#         max_value=100,
#         widget=forms.NumberInput(attrs={
#             'class': 'form-control',
#             'placeholder': 'Ваш возраст'
#         })
#     )
#     phone = forms.CharField(
#         max_length=20,
#         required=False,
#         widget=forms.TextInput(attrs={
#             'class': 'form-control',
#             'placeholder': '+998901234567'
#         })
#     )

#     class Meta:
#         model = User
#         fields = ('username', 'email', 'first_name', 'last_name', 
#                  'age', 'phone', 'user_type', 'password1', 'password2')

#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#         self.fields['username'].widget.attrs.update({
#             'class': 'form-control',
#             'placeholder': 'Имя пользователя'
#         })
#         self.fields['password1'].widget.attrs.update({
#             'class': 'form-control',
#             'placeholder': 'Пароль'
#         })
#         self.fields['password2'].widget.attrs.update({
#             'class': 'form-control',
#             'placeholder': 'Подтверждение пароля'
#         })

#     def clean_email(self):
#         email = self.cleaned_data.get('email')
#         if User.objects.filter(email=email).exists():
#             raise ValidationError("Пользователь с таким email уже существует.")
#         return email

#     def save(self, commit=True):
#         user = super().save(commit=False)
#         user.email = self.cleaned_data['email']
#         if commit:
#             user.save()
#         return user

# class UserProfileForm(forms.ModelForm):
#     """Форма редактирования основного профиля"""
#     class Meta:
#         model = User
#         fields = ['first_name', 'last_name', 'email', 'phone', 'age', 'avatar']
#         widgets = {
#             'first_name': forms.TextInput(attrs={'class': 'form-control'}),
#             'last_name': forms.TextInput(attrs={'class': 'form-control'}),
#             'email': forms.EmailInput(attrs={'class': 'form-control'}),
#             'phone': forms.TextInput(attrs={
#                 'class': 'form-control',
#                 'placeholder': '+998901234567'
#             }),
#             'age': forms.NumberInput(attrs={'class': 'form-control'}),
#             'avatar': forms.FileInput(attrs={
#                 'class': 'form-control',
#                 'accept': 'image/*'
#             }),
#         }

# class TeacherProfileForm(forms.ModelForm):
#     """Форма профиля учителя"""
    
#     class Meta:
#         model = TeacherProfile
#         fields = [
#             'bio', 'education_level', 'university', 'specialization',
#             'experience_years', 'city', 'teaching_format',
#             'telegram', 'whatsapp', 'available_from', 'available_to',
#             'available_weekdays'
#         ]
#         widgets = {
#             'bio': forms.Textarea(attrs={
#                 'class': 'form-control',
#                 'rows': 4,
#                 'placeholder': 'Расскажите о себе, вашем опыте и методах обучения...'
#             }),
#             'education_level': forms.Select(attrs={'class': 'form-control'}),
#             'university': forms.TextInput(attrs={
#                 'class': 'form-control',
#                 'placeholder': 'Название университета'
#             }),
#             'specialization': forms.TextInput(attrs={
#                 'class': 'form-control',
#                 'placeholder': 'Ваша специальность'
#             }),
#             'experience_years': forms.NumberInput(attrs={
#                 'class': 'form-control',
#                 'min': 0,
#                 'max': 50
#             }),
#             'city': forms.Select(attrs={'class': 'form-control'}),
#             'teaching_format': forms.Select(attrs={'class': 'form-control'}),
#             'telegram': forms.TextInput(attrs={
#                 'class': 'form-control',
#                 'placeholder': '@username'
#             }),
#             'whatsapp': forms.TextInput(attrs={
#                 'class': 'form-control',
#                 'placeholder': '+998901234567'
#             }),
#             'available_from': forms.TimeInput(attrs={
#                 'class': 'form-control',
#                 'type': 'time'
#             }),
#             'available_to': forms.TimeInput(attrs={
#                 'class': 'form-control',
#                 'type': 'time'
#             }),
#             'available_weekdays': forms.TextInput(attrs={
#                 'class': 'form-control',
#                 'placeholder': '1,2,3,4,5,6,7',
#                 'help_text': 'Дни недели через запятую (1-Пн, 2-Вт, ..., 7-Вс)'
#             }),
#         }

#     def clean_available_weekdays(self):
#         weekdays = self.cleaned_data.get('available_weekdays', '')
#         try:
#             days = [int(day.strip()) for day in weekdays.split(',')]
#             if not all(1 <= day <= 7 for day in days):
#                 raise ValidationError("Дни недели должны быть от 1 до 7")
#             return ','.join(map(str, sorted(set(days))))
#         except ValueError:
#             raise ValidationError("Введите дни недели числами через запятую")

# class TeacherSubjectFormSet(forms.BaseInlineFormSet):
#     """Формсет для предметов учителя"""
    
#     def clean(self):
#         super().clean()
#         if not any(self.cleaned_data):
#             raise ValidationError('Необходимо указать хотя бы один предмет.')
        
#         subjects = []
#         for form in self.forms:
#             if form.cleaned_data and not form.cleaned_data.get('DELETE'):
#                 subject = form.cleaned_data.get('subject')
#                 if subject in subjects:
#                     raise ValidationError('Нельзя добавлять один и тот же предмет дважды.')
#                 subjects.append(subject)

# class TeacherSubjectForm(forms.ModelForm):
#     """Форма для добавления предмета учителем"""
    
#     class Meta:
#         model = TeacherSubject
#         fields = ['subject', 'hourly_rate', 'is_free_trial', 'description']
#         widgets = {
#             'subject': forms.Select(attrs={'class': 'form-control'}),
#             'hourly_rate': forms.NumberInput(attrs={
#                 'class': 'form-control',
#                 'min': 0,
#                 'step': 1000,
#                 'placeholder': 'Цена за час в сумах'
#             }),
#             'is_free_trial': forms.CheckboxInput(attrs={
#                 'class': 'form-check-input'
#             }),
#             'description': forms.Textarea(attrs={
#                 'class': 'form-control',
#                 'rows': 3,
#                 'placeholder': 'Дополнительная информация о преподавании этого предмета...'
#             }),
#         }

#     def clean_hourly_rate(self):
#         rate = self.cleaned_data.get('hourly_rate')
#         if rate is not None and rate < 0:
#             raise ValidationError("Цена не может быть отрицательной")
#         return rate

# class StudentProfileForm(forms.ModelForm):
#     """Форма профиля ученика"""
    
#     interests = forms.ModelMultipleChoiceField(
#         queryset=Subject.objects.filter(is_active=True),
#         widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
#         required=False
#     )
    
#     class Meta:
#         model = StudentProfile
#         fields = ['education_level', 'school_university', 'city', 'interests', 'bio']
#         widgets = {
#             'education_level': forms.Select(attrs={'class': 'form-control'}),
#             'school_university': forms.TextInput(attrs={
#                 'class': 'form-control',
#                 'placeholder': 'Название учебного заведения'
#             }),
#             'city': forms.Select(attrs={'class': 'form-control'}),
#             'bio': forms.Textarea(attrs={
#                 'class': 'form-control',
#                 'rows': 3,
#                 'placeholder': 'Расскажите о себе и ваших целях обучения...'
#             }),
#         }

# class CertificateForm(forms.ModelForm):
#     """Форма для добавления сертификата"""
    
#     class Meta:
#         model = Certificate
#         fields = ['name', 'issuer', 'issue_date', 'file', 'verification_url']
#         widgets = {
#             'name': forms.TextInput(attrs={
#                 'class': 'form-control',
#                 'placeholder': 'Название сертификата'
#             }),
#             'issuer': forms.TextInput(attrs={
#                 'class': 'form-control',
#                 'placeholder': 'Организация, выдавшая сертификат'
#             }),
#             'issue_date': forms.DateInput(attrs={
#                 'class': 'form-control',
#                 'type': 'date'
#             }),
#             'file': forms.FileInput(attrs={
#                 'class': 'form-control',
#                 'accept': '.pdf,.jpg,.jpeg,.png'
#             }),
#             'verification_url': forms.URLInput(attrs={
#                 'class': 'form-control',
#                 'placeholder': 'https://example.com/verify'
#             }),
#         }

# class MessageForm(forms.ModelForm):
#     """Форма для отправки сообщения"""
    
#     class Meta:
#         model = Message
#         fields = ['content']
#         widgets = {
#             'content': forms.Textarea(attrs={
#                 'class': 'form-control',
#                 'rows': 3,
#                 'placeholder': 'Напишите ваше сообщение...',
#                 'maxlength': 2000
#             }),
#         }

# class ReviewForm(forms.ModelForm):
#     """Форма для написания отзыва"""
    
#     class Meta:
#         model = Review
#         fields = [
#             'subject', 'rating', 'knowledge_rating', 
#             'communication_rating', 'punctuality_rating', 'comment'
#         ]
#         widgets = {
#             'subject': forms.Select(attrs={'class': 'form-control'}),
#             'rating': forms.Select(
#                 choices=[(i, f'{i} звезд{"а" if i in [2,3,4] else ""}') for i in range(1, 6)],
#                 attrs={'class': 'form-control'}
#             ),
#             'knowledge_rating': forms.Select(
#                 choices=[(i, f'{i} звезд{"а" if i in [2,3,4] else ""}') for i in range(1, 6)],
#                 attrs={'class': 'form-control'}
#             ),
#             'communication_rating': forms.Select(
#                 choices=[(i, f'{i} звезд{"а" if i in [2,3,4] else ""}') for i in range(1, 6)],
#                 attrs={'class': 'form-control'}
#             ),
#             'punctuality_rating': forms.Select(
#                 choices=[(i, f'{i} звезд{"а" if i in [2,3,4] else ""}') for i in range(1, 6)],
#                 attrs={'class': 'form-control'}
#             ),
#             'comment': forms.Textarea(attrs={
#                 'class': 'form-control',
#                 'rows': 4,
#                 'placeholder': 'Поделитесь своим опытом обучения с этим учителем...'
#             }),
#         }

# class TeacherSearchForm(forms.Form):
#     """Форма поиска учителей"""
    
#     PRICE_RANGES = [
#         ('', 'Любая цена'),
#         ('0-50000', 'До 50,000 сум'),
#         ('50000-100000', '50,000 - 100,000 сум'),
#         ('100000-200000', '100,000 - 200,000 сум'),
#         ('200000-500000', '200,000 - 500,000 сум'),
#         ('500000+', 'От 500,000 сум'),
#     ]
    
#     SORT_OPTIONS = [
#         ('-rating', 'По рейтингу'),
#         ('hourly_rate', 'По цене (возрастанию)'),
#         ('-hourly_rate', 'По цене (убыванию)'),
#         ('-created_at', 'Новые'),
#         ('-total_students', 'По популярности'),
#     ]

#     query = forms.CharField(
#         required=False,
#         widget=forms.TextInput(attrs={
#             'class': 'form-control',
#             'placeholder': 'Поиск по имени или предмету...'
#         })
#     )
    
#     subject = forms.ModelChoiceField(
#         queryset=Subject.objects.filter(is_active=True),
#         required=False,
#         empty_label="Все предметы",
#         widget=forms.Select(attrs={'class': 'form-control'})
#     )
    
#     city = forms.ModelChoiceField(
#         queryset=City.objects.filter(is_active=True),
#         required=False,
#         empty_label="Все города",
#         widget=forms.Select(attrs={'class': 'form-control'})
#     )
    
#     teaching_format = forms.ChoiceField(
#         choices=[('', 'Любой формат')] + TeacherProfile.TEACHING_FORMATS,
#         required=False,
#         widget=forms.Select(attrs={'class': 'form-control'})
#     )
    
#     price_range = forms.ChoiceField(
#         choices=PRICE_RANGES,
#         required=False,
#         widget=forms.Select(attrs={'class': 'form-control'})
#     )
    
#     has_free_trial = forms.BooleanField(
#         required=False,
#         widget=forms.CheckboxInput(attrs={
#             'class': 'form-check-input'
#         })
#     )
    
#     min_rating = forms.ChoiceField(
#         choices=[('', 'Любой рейтинг')] + [(i, f'От {i} звезд') for i in range(1, 6)],
#         required=False,
#         widget=forms.Select(attrs={'class': 'form-control'})
#     )
    
#     sort_by = forms.ChoiceField(
#         choices=SORT_OPTIONS,
#         required=False,
#         initial='-rating',
#         widget=forms.Select(attrs={'class': 'form-control'})
#     )

# class ContactTeacherForm(forms.Form):
#     """Форма для первого контакта с учителем"""
    
#     subject = forms.ModelChoiceField(
#         queryset=Subject.objects.filter(is_active=True),
#         widget=forms.Select(attrs={'class': 'form-control'}),
#         help_text="По какому предмету вас интересуют занятия?"
#     )
    
#     message = forms.CharField(
#         widget=forms.Textarea(attrs={
#             'class': 'form-control',
#             'rows': 4,
#             'placeholder': 'Здравствуйте! Меня интересуют занятия по ... Расскажите о ваших методах обучения и когда можем начать?'
#         }),
#         max_length=1000,
#         help_text="Расскажите учителю о ваших целях и пожеланиях"
#     )
    
#     preferred_format = forms.ChoiceField(
#         choices=TeacherProfile.TEACHING_FORMATS,
#         widget=forms.Select(attrs={'class': 'form-control'}),
#         help_text="Предпочитаемый формат занятий"
#     )
    
#     available_time = forms.CharField(
#         required=False,
#         widget=forms.TextInput(attrs={
#             'class': 'form-control',
#             'placeholder': 'Например: будние дни с 18:00 до 20:00'
#         }),
#         help_text="Когда вам удобно заниматься?"
#     )

# # Дополнительные формы для админки и управления

# class BulkSubjectForm(forms.Form):
#     """Форма для массового добавления предметов"""
#     subjects = forms.CharField(
#         widget=forms.Textarea(attrs={
#             'class': 'form-control',
#             'rows': 10,
#             'placeholder': 'Введите предметы по одному на строку:\nМатематика\nФизика\nХимия\n...'
#         }),
#         help_text="Введите названия предметов по одному на строку"
#     )

#     def clean_subjects(self):
#         subjects_text = self.cleaned_data.get('subjects', '')
#         subjects_list = [s.strip() for s in subjects_text.split('\n') if s.strip()]
        
#         if not subjects_list:
#             raise ValidationError("Введите хотя бы один предмет")
        
#         # Проверяем, что предметы не дублируются
#         if len(subjects_list) != len(set(subjects_list)):
#             raise ValidationError("Найдены дублирующиеся предметы")
        
#         return subjects_list

# class BulkCityForm(forms.Form):
#     """Форма для массового добавления городов"""
#     cities = forms.CharField(
#         widget=forms.Textarea(attrs={
#             'class': 'form-control',
#             'rows': 10,
#             'placeholder': 'Введите города по одному на строку:\nТашкент\nСамарканд\nБухара\n...'
#         }),
#         help_text="Введите названия городов по одному на строку"
#     )
    
#     country = forms.CharField(
#         initial='Узбекистан',
#         widget=forms.TextInput(attrs={
#             'class': 'form-control'
#         })
#     )

#     def clean_cities(self):
#         cities_text = self.cleaned_data.get('cities', '')
#         cities_list = [c.strip() for c in cities_text.split('\n') if c.strip()]
        
#         if not cities_list:
#             raise ValidationError("Введите хотя бы один город")
        
#         # Проверяем, что города не дублируются
#         if len(cities_list) != len(set(cities_list)):
#             raise ValidationError("Найдены дублирующиеся города")
        
#         return cities_list

# class AdvancedTeacherSearchForm(TeacherSearchForm):
#     """Расширенная форма поиска учителей с дополнительными фильтрами"""
    
#     EXPERIENCE_RANGES = [
#         ('', 'Любой опыт'),
#         ('0-2', 'До 2 лет'),
#         ('2-5', '2-5 лет'),
#         ('5-10', '5-10 лет'),
#         ('10+', 'Более 10 лет'),
#     ]
    
#     experience_range = forms.ChoiceField(
#         choices=EXPERIENCE_RANGES,
#         required=False,
#         widget=forms.Select(attrs={'class': 'form-control'})
#     )
    
#     has_certificates = forms.BooleanField(
#         required=False,
#         widget=forms.CheckboxInput(attrs={
#             'class': 'form-check-input'
#         })
#     )
    
#     is_verified = forms.BooleanField(
#         required=False,
#         widget=forms.CheckboxInput(attrs={
#             'class': 'form-check-input'
#         })
#     )
    
#     education_level = forms.ChoiceField(
#         choices=[('', 'Любое образование')] + TeacherProfile.EDUCATION_LEVELS,
#         required=False,
#         widget=forms.Select(attrs={'class': 'form-control'})
#     )

# class TeacherStatisticsForm(forms.Form):
#     """Форма для просмотра статистики учителя"""
    
#     PERIOD_CHOICES = [
#         ('week', 'Неделя'),
#         ('month', 'Месяц'),
#         ('quarter', 'Квартал'),
#         ('year', 'Год'),
#     ]
    
#     period = forms.ChoiceField(
#         choices=PERIOD_CHOICES,
#         initial='month',
#         widget=forms.Select(attrs={'class': 'form-control'})
#     )
    
#     date_from = forms.DateField(
#         required=False,
#         widget=forms.DateInput(attrs={
#             'class': 'form-control',
#             'type': 'date'
#         })
#     )
    
#     date_to = forms.DateField(
#         required=False,
#         widget=forms.DateInput(attrs={
#             'class': 'form-control',
#             'type': 'date'
#         })
#     )

# # Inline formsets для админки
# from django.forms import inlineformset_factory

# TeacherSubjectFormSet = inlineformset_factory(
#     TeacherProfile,
#     TeacherSubject,
#     form=TeacherSubjectForm,
#     extra=1,
#     can_delete=True,
#     min_num=1,
#     validate_min=True
# )

# CertificateFormSet = inlineformset_factory(
#     TeacherProfile,
#     Certificate,
#     form=CertificateForm,
#     extra=1,
#     can_delete=True,
#     fk_name=None  # Будет использоваться ManyToMany через промежуточную таблицу
# )