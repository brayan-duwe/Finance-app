from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Category, Expense, Frequency, Income, Profile

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB
AMOUNT_ATTRS = {"step": "0.01", "min": "0.01", "inputmode": "decimal"}


class DateInput(forms.DateInput):
    """The browser's native date picker."""

    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class MonthInput(forms.DateInput):
    input_type = "month"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m", **kwargs)


def _check_installments(form, cleaned):
    if cleaned.get("frequency") == Frequency.INSTALLMENTS:
        if not cleaned.get("installment_total"):
            form.add_error("installment_total", _("How many installments in total?"))
    else:
        cleaned["installment_total"] = None
    return cleaned


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["description", "amount", "date", "category", "frequency", "installment_total"]
        widgets = {
            "description": forms.TextInput(attrs={"placeholder": _("e.g. Groceries"), "autofocus": True}),
            "amount": forms.NumberInput(attrs=AMOUNT_ATTRS),
            "date": DateInput(),
            "installment_total": forms.NumberInput(attrs={"min": 2, "max": 120}),
        }
        help_texts = {
            "frequency": _("Recurring expenses count every month (or year) from this date. "
                           "Installments count every month until the last one."),
            "installment_total": _("The amount above is the value of each installment."),
        }

    def clean(self):
        return _check_installments(self, super().clean())


class IncomeForm(forms.ModelForm):
    class Meta:
        model = Income
        fields = ["description", "amount", "date", "frequency"]
        widgets = {
            "description": forms.TextInput(attrs={"placeholder": _("e.g. Salary"), "autofocus": True}),
            "amount": forms.NumberInput(attrs=AMOUNT_ATTRS),
            "date": DateInput(),
        }
        help_texts = {"frequency": _("Add your salary as a monthly income.")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["frequency"].choices = [
            c for c in Frequency.choices if c[0] != Frequency.INSTALLMENTS
        ]


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["language", "currency"]
        help_texts = {
            "currency": _("Changes how amounts are shown, and which column is read from statements. "
                          "Amounts you already added are not converted."),
        }


class StatementUploadForm(forms.Form):
    file = forms.FileField(
        label=_("Statement file"),
        help_text=_("CSV or PDF, up to 10 MB. CSV exports are the most reliable."),
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.pdf,text/csv,application/pdf"}),
    )
    date_order = forms.ChoiceField(
        label=_("Dates in your statement look like"),
        choices=[("dmy", _("Day first: 31/12")), ("mdy", _("Month first: 12/31"))],
        initial="dmy",
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not upload.name.lower().endswith((".csv", ".pdf", ".txt")):
            raise forms.ValidationError(_("Upload a CSV or PDF file."))
        if upload.size > MAX_UPLOAD_SIZE:
            raise forms.ValidationError(_("This file is larger than 10 MB."))
        return upload


class BillMonthForm(forms.Form):
    bill_month = forms.DateField(
        label=_("Statement month"),
        input_formats=["%Y-%m"],
        widget=MonthInput(),
        help_text=_("The month you pay this bill. Installments are counted from here."),
    )


class ImportRowForm(forms.Form):
    """One transaction found in a statement, editable before saving."""

    include = forms.BooleanField(required=False)
    date = forms.DateField(label=_("date"), widget=DateInput())
    description = forms.CharField(label=_("description"), max_length=200)
    original_description = forms.CharField(max_length=200, required=False, widget=forms.HiddenInput)
    amount = forms.DecimalField(label=_("amount"), max_digits=12, decimal_places=2,
                                widget=forms.NumberInput(attrs={"step": "0.01"}))
    category = forms.ChoiceField(label=_("category"), choices=Category.choices)
    frequency = forms.ChoiceField(label=_("repeats"), choices=Frequency.choices)
    installment_number = forms.IntegerField(
        label=_("installment"), min_value=1, max_value=120, required=False,
        widget=forms.NumberInput(attrs={"min": 1, "max": 120}),
    )
    installment_total = forms.IntegerField(
        label=_("of"), min_value=2, max_value=120, required=False,
        widget=forms.NumberInput(attrs={"min": 2, "max": 120}),
    )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("include"):
            return cleaned
        if cleaned.get("frequency") == Frequency.INSTALLMENTS:
            number = cleaned.get("installment_number") or 1
            total = cleaned.get("installment_total")
            if not total:
                self.add_error("installment_total", _("How many installments in total?"))
            elif number > total:
                self.add_error("installment_number", _("Can't be more than the total."))
            cleaned["installment_number"] = number
        return cleaned


ImportRowFormSet = forms.formset_factory(ImportRowForm, extra=0)
