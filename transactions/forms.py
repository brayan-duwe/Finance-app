from django import forms
from django.utils.translation import gettext_lazy as _

from .models import PALETTE, Category, Expense, Frequency, Income, Kind, Profile

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


class UserCategoriesMixin:
    """Limit the "category" field to the user's own categories of the right kind."""

    category_kind = None

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["category"]
        field.queryset = Category.objects.filter(user=user, kind=self.category_kind)
        field.empty_label = None


class ExpenseForm(UserCategoriesMixin, forms.ModelForm):
    category_kind = Kind.EXPENSE

    class Meta:
        model = Expense
        fields = ["description", "amount", "date", "category", "frequency", "installment_total"]
        widgets = {
            "description": forms.TextInput(attrs={"placeholder": _("e.g. Groceries"), "autofocus": True}),
            "amount": forms.NumberInput(attrs=AMOUNT_ATTRS),
            "date": DateInput(),
            "category": forms.RadioSelect,  # shown as coloured chips
            "installment_total": forms.NumberInput(attrs={"min": 2, "max": 120}),
        }
        help_texts = {
            "frequency": _("Recurring expenses count every month (or year) from this date. "
                           "Installments count every month until the last one."),
            "installment_total": _("The amount above is the value of each installment."),
        }

    def clean(self):
        return _check_installments(self, super().clean())


class IncomeForm(UserCategoriesMixin, forms.ModelForm):
    category_kind = Kind.INCOME

    class Meta:
        model = Income
        fields = ["description", "amount", "date", "category", "frequency"]
        widgets = {
            "description": forms.TextInput(attrs={"placeholder": _("e.g. Salary"), "autofocus": True}),
            "amount": forms.NumberInput(attrs=AMOUNT_ATTRS),
            "date": DateInput(),
            "category": forms.RadioSelect,  # shown as coloured chips
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


class CategoryForm(forms.ModelForm):
    name = forms.CharField(label=_("Name"), max_length=40,
                           widget=forms.TextInput(attrs={"autofocus": True, "placeholder": _("e.g. Pets")}))
    color = forms.ChoiceField(label=_("Colour"), choices=[(c, c) for c in PALETTE], widget=forms.RadioSelect)

    class Meta:
        model = Category
        fields = ["name", "color"]

    def __init__(self, *args, user, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self.user, self.kind = user, kind
        if self.instance.pk:
            self.initial["name"] = self.instance.label  # default categories have no stored name
        if self.instance.color not in PALETTE:
            self.fields["color"].choices.append((self.instance.color, self.instance.color))

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        others = Category.objects.filter(user=self.user, kind=self.kind).exclude(pk=self.instance.pk)
        if any(str(c.label).casefold() == name.casefold() for c in others):
            raise forms.ValidationError(_("You already have a category with this name."))
        return name

    def save(self, commit=True):
        category = super().save(commit=False)
        category.user, category.kind = self.user, self.kind
        # A default category keeps following the language until it's actually renamed.
        if category.system_key and category.name == str(category.label_default):
            category.name = ""
        if commit:
            category.save()
        return category


class StatementUploadForm(forms.Form):
    file = forms.FileField(
        label=_("Statement file"),
        help_text=_("CSV or PDF, up to 10 MB. CSV exports are the most reliable."),
        widget=forms.FileInput(attrs={
            "accept": ".csv,.pdf,text/csv,application/pdf",
            "class": "dropzone-input",
            "aria-labelledby": "id_file-label",
        }),
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
    category = forms.ModelChoiceField(label=_("category"), queryset=Category.objects.none(), empty_label=None)
    frequency = forms.ChoiceField(label=_("repeats"), choices=Frequency.choices)
    installment_number = forms.IntegerField(
        label=_("installment"), min_value=1, max_value=120, required=False,
        widget=forms.NumberInput(attrs={"min": 1, "max": 120}),
    )
    installment_total = forms.IntegerField(
        label=_("of"), min_value=2, max_value=120, required=False,
        widget=forms.NumberInput(attrs={"min": 2, "max": 120}),
    )

    def __init__(self, *args, categories, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = categories

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
