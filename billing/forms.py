from __future__ import annotations

from decimal import Decimal

from django import forms

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from teachers.models import TeacherSubject

from .models import Homework, HomeworkSubmission, Tariff, WithdrawalRequest
from .validators import validate_homework_file


class TariffForm(forms.ModelForm):
    """ModelForm для CRUD тарифа учителем.

    Поле `subject` ограничивается предметами, которые учитель уже преподаёт
    (есть в TeacherSubject) — нельзя создать тариф по предмету, который
    учитель не указал в своём профиле.
    """

    MIN_PRICE_PER_MONTH = Decimal('10000.00')

    class Meta:
        model = Tariff
        fields = (
            'subject', 'name', 'description',
            'lessons_per_week', 'lesson_duration_minutes', 'duration_months',
            'price_per_month',
            'is_recommended', 'is_active',
        )
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': _('Например: «Стандарт» или «Подготовка к IELTS»'),
            }),
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'lessons_per_week': forms.Select(attrs={'class': 'form-select'}),
            'lesson_duration_minutes': forms.Select(attrs={'class': 'form-select'}),
            'duration_months': forms.Select(attrs={'class': 'form-select'}),
            'price_per_month': forms.NumberInput(attrs={
                'class': 'form-input',
                'min': '10000',
                'step': '1000',
                'inputmode': 'numeric',
            }),
        }

    def __init__(self, *args, teacher=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.teacher = teacher
        if teacher is not None:
            # subject ограничен теми, что учитель уже преподаёт
            self.fields['subject'].queryset = (
                TeacherSubject.objects.filter(teacher=teacher)
                .select_related('subject')
                .values_list('subject', flat=True)
            )
            # ↑ values_list возвращает список id'ов — заменим на queryset Subject
            from teachers.models import Subject
            allowed_subject_ids = TeacherSubject.objects.filter(
                teacher=teacher
            ).values_list('subject_id', flat=True)
            self.fields['subject'].queryset = Subject.objects.filter(
                pk__in=allowed_subject_ids
            )
            # Локализованное имя предмета в выпадающем списке
            self.fields['subject'].label_from_instance = lambda obj: obj.get_display_name()

    def clean_price_per_month(self):
        price = self.cleaned_data.get('price_per_month')
        if price is None:
            return price
        if price < self.MIN_PRICE_PER_MONTH:
            raise forms.ValidationError(
                _('Минимальная цена за месяц — %(min)s сум.') % {'min': int(self.MIN_PRICE_PER_MONTH)}
            )
        return price

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.teacher is not None and not instance.pk:
            instance.teacher = self.teacher
        if commit:
            instance.save()
        return instance


class WithdrawalRequestForm(forms.ModelForm):
    """Заявка на вывод средств учителем."""

    class Meta:
        model = WithdrawalRequest
        fields = ('amount', 'payout_method', 'payout_details', 'comment')
        widgets = {
            'amount': forms.NumberInput(attrs={
                'class': 'form-input',
                'min': str(int(Decimal(settings.MIN_WITHDRAWAL_AMOUNT))),
                'step': '10000',
                'inputmode': 'numeric',
                'placeholder': _('Сумма в сумах'),
            }),
            'payout_method': forms.Select(attrs={'class': 'form-select'}),
            'payout_details': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': '8600 1234 5678 9012  или  +998 90 123 45 67',
                'autocomplete': 'off',
            }),
            'comment': forms.Textarea(attrs={'rows': 2, 'class': 'form-input',
                                              'placeholder': _('Опционально')}),
        }

    def __init__(self, *args, user=None, max_amount=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.max_amount = max_amount  # текущий баланс (для подсказки)

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is None:
            return amount
        min_amt = Decimal(settings.MIN_WITHDRAWAL_AMOUNT)
        if amount < min_amt:
            raise forms.ValidationError(
                _('Минимальная сумма вывода — %(min)s сум.') % {'min': int(min_amt)}
            )
        if self.max_amount is not None and amount > self.max_amount:
            raise forms.ValidationError(
                _('На балансе только %(amount)s сум.') % {'amount': int(self.max_amount)}
            )
        return amount


# ---------- Homework (Phase 8) -------------------------------------------


class HomeworkForm(forms.ModelForm):
    """Форма создания/редактирования задания учителем."""

    class Meta:
        model = Homework
        fields = ('title', 'description', 'due_at')
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': _('Например: Прочитать главу 3 и ответить на вопросы'),
                'maxlength': 200,
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-input', 'rows': 6,
                'placeholder': _('Подробное описание задания, что нужно сделать, на что обратить внимание...'),
            }),
            'due_at': forms.DateTimeInput(attrs={
                'class': 'form-input',
                'type': 'datetime-local',
            }, format='%Y-%m-%dT%H:%M'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['due_at'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M']
        self.fields['due_at'].required = False
        # Клиентский минимум — нельзя выбрать дедлайн в прошлом (datetime-local).
        from django.utils import timezone
        self.fields['due_at'].widget.attrs['min'] = timezone.localtime(
            timezone.now()).strftime('%Y-%m-%dT%H:%M')

    def clean_due_at(self):
        # Серверная защита: дедлайн в прошлом сделал бы задание сразу «просроченным».
        due_at = self.cleaned_data.get('due_at')
        if due_at:
            from django.utils import timezone
            if due_at < timezone.now():
                raise forms.ValidationError(_('Дедлайн не может быть в прошлом.'))
        return due_at


class HomeworkSubmissionForm(forms.ModelForm):
    """Форма сдачи задания учеником."""

    class Meta:
        model = HomeworkSubmission
        fields = ('text_response',)
        widgets = {
            'text_response': forms.Textarea(attrs={
                'class': 'form-input', 'rows': 6,
                'placeholder': _('Ваш ответ. Можете также прикрепить файлы внизу.'),
            }),
        }


class HomeworkGradeForm(forms.Form):
    """Форма оценки учителем."""
    DECISION_GRADE = 'grade'
    DECISION_RETURN = 'return'
    DECISIONS = (
        (DECISION_GRADE, _('Поставить оценку')),
        (DECISION_RETURN, _('Вернуть на доработку')),
    )

    decision = forms.ChoiceField(choices=DECISIONS, widget=forms.RadioSelect,
                                  initial=DECISION_GRADE, label=_('Действие'))
    grade = forms.IntegerField(
        min_value=0, max_value=100, required=False,
        widget=forms.NumberInput(attrs={'class': 'form-input',
                                         'min': 0, 'max': 100,
                                         'placeholder': _('0–100')}),
        label=_('Оценка (0–100)'),
    )
    feedback = forms.CharField(
        max_length=2000, required=False,
        widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 4,
                                      'placeholder': _('Комментарий к работе')}),
        label=_('Комментарий'),
    )

    def clean(self):
        cleaned = super().clean()
        decision = cleaned.get('decision')
        grade = cleaned.get('grade')
        feedback = cleaned.get('feedback') or ''
        if decision == self.DECISION_GRADE and grade is None:
            self.add_error('grade', _('Укажите оценку или выберите «Вернуть на доработку».'))
        if decision == self.DECISION_RETURN and not feedback.strip():
            self.add_error('feedback', _('При возврате на доработку нужен комментарий.'))
        return cleaned

