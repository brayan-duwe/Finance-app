import json
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .categorize import guess_category
from .currencies import format_money
from .models import Category as CategoryModel
from .models import DefaultExpenseCategory as Category
from .models import Expense, Frequency, Income, add_months, ensure_default_categories, get_category, get_profile
from .parsers import parse_amount, parse_csv, parse_date, parse_installment, parse_line

TODAY = date(2026, 11, 14)


def cat(user, key, kind="expense"):
    """The user's category for a default key, e.g. cat(user, "food")."""
    ensure_default_categories(user)
    return get_category(user, kind, key)

# The layout of a real Brazilian card statement: two amount columns (US$ and R$),
# an exchange-rate column, the bank's category, and installments ("Parcela").
BRAZILIAN_STATEMENT = (
    "Data de Compra;Nome no Cartão;Final do Cartão;Categoria;Descrição;Parcela;"
    "Valor (em US$);Cotação (em R$);Valor (em R$)\n"
    "10/08/2026;CARD HOLDER;1111;Elétrico;WAZ HARDWARE;3/4;0;0;66.15\n"
    "17/10/2026;CARD HOLDER;1111;Restaurante / Lanchonete / Bar;CANOA ACORIANA;Única;0;0;23.10\n"
    "20/10/2026;CARD HOLDER;1111;Elétrico;ANTHROPIC* CLAUDE SUB  SA;Única;21.84;5.32;116.22\n"
    "11/11/2026;CARD HOLDER;1111;Serviços Profissionais;HTM*PREMIERSOFT-WIS;1/12;0;0;33.33\n"
    '20/10/2026;CARD HOLDER;2222;-;"Inclusao de Pagamento    ";Única;0;0;-88.10\n'
).encode()


class ParseAmountTests(SimpleTestCase):
    def test_formats(self):
        cases = {
            "45.90": "45.90",
            "R$ 1.234,56": "1234.56",
            "$1,234.56": "1234.56",
            "-12.00": "-12.00",
            "(12.00)": "-12.00",
            "12.00-": "-12.00",
            "30.00 CR": "-30.00",
            "€ 9,99": "9.99",
            "100": "100.00",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_amount(text), Decimal(expected))

    def test_not_amounts(self):
        for text in ["", "Uber", "2026-11-14", "14/11"]:
            with self.subTest(text=text):
                self.assertIsNone(parse_amount(text))


class ParseDateTests(SimpleTestCase):
    def test_formats(self):
        self.assertEqual(parse_date("2026-11-03", today=TODAY), date(2026, 11, 3))
        self.assertEqual(parse_date("03/11/2026", today=TODAY), date(2026, 11, 3))
        self.assertEqual(parse_date("11/03/2026", day_first=False, today=TODAY), date(2026, 11, 3))
        self.assertEqual(parse_date("03/11/26", today=TODAY), date(2026, 11, 3))
        self.assertEqual(parse_date("03 NOV", today=TODAY), date(2026, 11, 3))
        self.assertEqual(parse_date("Nov 3, 2026", today=TODAY), date(2026, 11, 3))
        self.assertEqual(parse_date("03 OUT", today=TODAY), date(2026, 10, 3))
        self.assertEqual(parse_date("2026-11-03 10:15:00", today=TODAY), date(2026, 11, 3))

    def test_missing_year_uses_last_year_for_future_dates(self):
        january = date(2027, 1, 10)
        self.assertEqual(parse_date("28/12", today=january), date(2026, 12, 28))

    def test_invalid(self):
        for text in ["", "hello", "32/01/2026", "Uber 12"]:
            with self.subTest(text=text):
                self.assertIsNone(parse_date(text, today=TODAY))


class ParseCsvTests(SimpleTestCase):
    def test_header_with_positive_charges(self):
        data = (
            "date,title,amount\n"
            "2026-11-01,Netflix,39.90\n"
            "2026-11-02,Uber *Trip,23.45\n"
            "2026-11-05,Pagamento recebido,-500.00\n"
        ).encode()
        rows = parse_csv(data, today=TODAY)
        self.assertEqual([r.description for r in rows], ["Netflix", "Uber *Trip", "Pagamento recebido"])
        self.assertEqual([r.amount for r in rows], [Decimal("39.90"), Decimal("23.45"), Decimal("-500.00")])

    def test_negative_charges_are_flipped(self):
        data = (
            "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
            "11/01/2026,11/02/2026,STARBUCKS,Food,Sale,-5.75,\n"
            "11/03/2026,11/04/2026,AMAZON,Shopping,Sale,-42.10,\n"
            "11/10/2026,11/10/2026,PAYMENT THANK YOU,,Payment,200.00,\n"
        ).encode()
        rows = parse_csv(data, day_first=False, today=TODAY)
        self.assertEqual(rows[0].date, date(2026, 11, 1))
        self.assertEqual([r.amount for r in rows], [Decimal("5.75"), Decimal("42.10"), Decimal("-200.00")])

    def test_semicolons_and_portuguese_header(self):
        data = "Data;Lançamento;Categoria;Valor\n03/11/2026;Farmácia São João;Saúde;R$ 54,90\n".encode("cp1252")
        rows = parse_csv(data, today=TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].description, "Farmácia São João")
        self.assertEqual(rows[0].amount, Decimal("54.90"))

    def test_debit_and_credit_columns(self):
        data = (
            "Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n"
            "2026-11-01,2026-11-02,1234,SHELL OIL,Gas,40.00,\n"
            "2026-11-03,2026-11-03,1234,REFUND,Other,,15.00\n"
        ).encode()
        rows = parse_csv(data, today=TODAY)
        self.assertEqual([r.amount for r in rows], [Decimal("40.00"), Decimal("-15.00")])

    def test_no_header(self):
        data = b"03/11/2026,Spotify,21.90\n04/11/2026,iFood,58.00\n"
        rows = parse_csv(data, today=TODAY)
        self.assertEqual([(r.description, r.amount) for r in rows], [("Spotify", Decimal("21.90")), ("iFood", Decimal("58.00"))])


class ParseLineTests(SimpleTestCase):
    def test_statement_lines(self):
        row = parse_line("14/11 UBER *TRIP 23,45", today=TODAY)
        self.assertEqual((row.date, row.description, row.amount), (date(2026, 11, 14), "UBER *TRIP", Decimal("23.45")))

        row = parse_line("03 NOV  NETFLIX.COM   R$ 39,90", today=TODAY)
        self.assertEqual((row.description, row.amount), ("NETFLIX.COM", Decimal("39.90")))

        row = parse_line("11/02 11/03 AMAZON MKTPLACE PMTS 1,234.56", day_first=False, today=TODAY)
        self.assertEqual((row.date, row.description, row.amount), (date(2026, 11, 2), "AMAZON MKTPLACE PMTS", Decimal("1234.56")))

        row = parse_line("Nov 10 PAYMENT - THANK YOU -500.00", today=TODAY)
        self.assertEqual(row.amount, Decimal("-500.00"))

    def test_other_lines_are_ignored(self):
        for line in ["Total da fatura R$ 1.234,56", "Página 1 de 3", "14/11/2026", "Vencimento 20/11 123,00 X"]:
            with self.subTest(line=line):
                self.assertIsNone(parse_line(line, today=TODAY))


class CategorizeTests(SimpleTestCase):
    def test_keywords(self):
        self.assertEqual(guess_category("NETFLIX.COM"), Category.SUBSCRIPTIONS)
        self.assertEqual(guess_category("Uber *Trip"), Category.TRANSPORT)
        self.assertEqual(guess_category("UBER EATS"), Category.FOOD)
        self.assertEqual(guess_category("Farmácia São João"), Category.HEALTH)
        self.assertEqual(guess_category("Mercado Livre"), Category.SHOPPING)
        self.assertEqual(guess_category("Something random"), Category.OTHER)


class DashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(self.user)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, f"{reverse('login')}?next=/")

    def test_empty_state_asks_for_salary(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Add your salary")

    def test_chart_with_salary_and_expenses(self):
        Income.objects.create(user=self.user, description="Salary", amount=Decimal("5000"), date=date(2026, 1, 5), frequency=Frequency.MONTHLY)
        Expense.objects.create(user=self.user, description="Rent", amount=Decimal("1500"), date=date(2026, 1, 1), frequency=Frequency.MONTHLY, category=cat(self.user, "housing"))
        Expense.objects.create(user=self.user, description="Dinner", amount=Decimal("100"), date=date(2026, 11, 3), category=cat(self.user, "food"))
        Expense.objects.create(user=self.user, description="Old dinner", amount=Decimal("80"), date=date(2026, 10, 3), category=cat(self.user, "food"))

        response = self.client.get(reverse("dashboard"), {"month": "2026-11"})
        chart = response.context["chart"]
        self.assertEqual(chart["income"], Decimal("5000"))
        self.assertEqual(chart["spent"], Decimal("1600"))
        self.assertEqual(chart["left"], Decimal("3400"))
        self.assertEqual([s["label"] for s in chart["segments"]], ["Housing & bills", "Food & groceries"])
        self.assertContains(response, "R$ 3.400,00")  # new users default to reais
        self.assertNotContains(response, "Old dinner")

    def test_yearly_expense_only_in_its_month(self):
        Expense.objects.create(user=self.user, description="Insurance", amount=Decimal("600"), date=date(2026, 3, 1), frequency=Frequency.YEARLY)
        self.assertContains(self.client.get(reverse("dashboard"), {"month": "2027-03"}), "Insurance")
        self.assertNotContains(self.client.get(reverse("dashboard"), {"month": "2027-04"}), "Insurance")

    def test_users_only_see_their_own_data(self):
        other = User.objects.create_user("sam", password="a-long-password-123")
        Expense.objects.create(user=other, description="Secret", amount=Decimal("10"), date=TODAY)
        self.assertNotContains(self.client.get(reverse("dashboard"), {"month": "2026-11"}), "Secret")

    def test_add_income_and_delete(self):
        response = self.client.post(reverse("add_income"), {
            "description": "Salary", "amount": "4200.00", "date": "2026-11-01", "frequency": "monthly",
            "category": cat(self.user, "salary", "income").pk,
        })
        self.assertEqual(response.status_code, 302)
        income = Income.objects.get(user=self.user)
        self.client.post(reverse("delete_entry", args=["income", income.pk]))
        self.assertFalse(Income.objects.exists())


class ImportFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(self.user)

    def test_upload_review_and_save(self):
        Expense.objects.create(user=self.user, description="Spotify", amount=Decimal("21.90"), date=date(2026, 1, 3), frequency=Frequency.MONTHLY)
        csv_file = SimpleUploadedFile("statement.csv", (
            "date,title,amount\n"
            "2026-11-01,Netflix,39.90\n"
            "2026-11-02,Uber *Trip,23.45\n"
            "2026-11-03,Spotify,21.90\n"
            "2026-11-05,Pagamento recebido,-500.00\n"
        ).encode(), content_type="text/csv")

        response = self.client.post(reverse("import_statement"), {"file": csv_file, "date_order": "dmy"})
        self.assertRedirects(response, reverse("import_review"))

        response = self.client.get(reverse("import_review"))
        forms = response.context["formset"].forms
        self.assertEqual([f.initial["include"] for f in forms], [True, True, False, False])
        self.assertEqual(forms[0].initial["frequency"], Frequency.MONTHLY)  # Netflix: subscription
        self.assertContains(response, "Already counted as recurring")

        post = review_post(response)
        post["form-1-category"] = str(cat(self.user, "other").pk)  # the user changed a category

        response = self.client.post(reverse("import_review"), post)
        self.assertEqual(response.status_code, 302)
        added = Expense.objects.filter(user=self.user, imported=True).order_by("date")
        self.assertEqual([(e.description, e.category.system_key) for e in added], [("Netflix", "subscriptions"), ("Uber *Trip", "other")])
        self.assertNotIn("statement_import", self.client.session)

    def test_unreadable_file_shows_an_error(self):
        csv_file = SimpleUploadedFile("statement.csv", b"hello,world\nfoo,bar\n", content_type="text/csv")
        response = self.client.post(reverse("import_statement"), {"file": csv_file, "date_order": "dmy"})
        self.assertContains(response, "No transactions were found")

    def test_wrong_file_type(self):
        upload = SimpleUploadedFile("photo.png", b"\x89PNG", content_type="image/png")
        response = self.client.post(reverse("import_statement"), {"file": upload, "date_order": "dmy"})
        self.assertContains(response, "Upload a CSV or PDF file.")


def review_post(response):
    """The POST data a browser would send for the review page, as rendered."""
    forms = response.context["formset"].forms
    post = {
        "form-TOTAL_FORMS": str(len(forms)),
        "form-INITIAL_FORMS": str(len(forms)),
        "bill_month": response.context["month_form"].initial["bill_month"].strftime("%Y-%m"),
    }
    fields = ["date", "description", "original_description", "amount", "category", "frequency",
              "installment_number", "installment_total", "recurring_id"]
    for i, f in enumerate(forms):
        for name in fields:
            value = f.initial.get(name)
            post[f"form-{i}-{name}"] = "" if value is None else str(value)
        if f.initial["include"]:
            post[f"form-{i}-include"] = "on"
        if f.initial.get("update_recurring"):
            post[f"form-{i}-update_recurring"] = "on"
    return post


class BrazilianStatementTests(SimpleTestCase):
    def test_uses_the_reais_column_not_dollars_or_exchange_rate(self):
        rows = parse_csv(BRAZILIAN_STATEMENT, currency="BRL", today=TODAY)
        self.assertEqual(
            [r.amount for r in rows],
            [Decimal("66.15"), Decimal("23.10"), Decimal("116.22"), Decimal("33.33"), Decimal("-88.10")],
        )

    def test_installments_bank_category_and_original_name(self):
        rows = parse_csv(BRAZILIAN_STATEMENT, currency="BRL", today=TODAY)
        self.assertEqual((rows[0].installment_number, rows[0].installment_total), (3, 4))
        self.assertIsNone(rows[1].installment_total)  # "Única"
        self.assertEqual(rows[1].bank_category, "Restaurante / Lanchonete / Bar")
        self.assertEqual(rows[2].description, "ANTHROPIC* CLAUDE SUB SA")  # spaces cleaned up
        self.assertEqual(rows[2].original, "ANTHROPIC* CLAUDE SUB  SA")  # kept exactly
        self.assertEqual(rows[4].bank_category, "")  # "-"

    def test_parse_installment(self):
        self.assertEqual(parse_installment("3/4"), (3, 4))
        self.assertEqual(parse_installment("03 de 12"), (3, 12))
        for text in ["Única", "-", "", "1/1", "5/4"]:
            with self.subTest(text=text):
                self.assertIsNone(parse_installment(text))

    def test_installment_in_pdf_line(self):
        row = parse_line("10/08 WAZ HARDWARE PARC 03/04 66,15", today=TODAY)
        self.assertEqual((row.description, row.installment_number, row.installment_total), ("WAZ HARDWARE", 3, 4))

    def test_bank_category_helps_when_the_name_is_unknown(self):
        self.assertEqual(guess_category("CANOA ACORIANA", "Restaurante / Lanchonete / Bar"), Category.FOOD)
        self.assertEqual(guess_category("ACADEMIAMOVIMENTO"), Category.HEALTH)
        self.assertEqual(guess_category("Anuidade Diferenciada"), Category.FEES)


class MoneyFormatTests(SimpleTestCase):
    def test_formats(self):
        self.assertEqual(format_money(Decimal("1234.5"), "BRL"), "R$ 1.234,50")
        self.assertEqual(format_money(Decimal("1234.5"), "USD"), "$1,234.50")
        self.assertEqual(format_money(Decimal("-12"), "EUR"), "-€ 12,00")


class InstallmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(self.user)

    def test_import_installments_count_until_the_last_one(self):
        upload = SimpleUploadedFile("Fatura.csv", BRAZILIAN_STATEMENT, content_type="text/csv")
        self.client.post(reverse("import_statement"), {"file": upload, "date_order": "dmy"})
        response = self.client.get(reverse("import_review"))
        self.assertEqual(response.context["month_form"].initial["bill_month"], date(2026, 11, 1))
        self.client.post(reverse("import_review"), review_post(response))

        waz = Expense.objects.get(description="WAZ HARDWARE")
        # 3rd of 4 on the November bill -> paid September to December.
        self.assertEqual(waz.frequency, Frequency.INSTALLMENTS)
        self.assertEqual((waz.first_installment_month, waz.last_installment_month), (date(2026, 9, 1), date(2026, 12, 1)))
        self.assertEqual(waz.installment_in_month(2026, 12), 4)
        self.assertEqual(waz.original_description, "WAZ HARDWARE")
        self.assertEqual(waz.source_file, "Fatura.csv")

        claude = Expense.objects.get(description="ANTHROPIC* CLAUDE SUB SA")
        self.assertEqual(claude.original_description, "ANTHROPIC* CLAUDE SUB  SA")

        self.assertContains(self.client.get(reverse("dashboard"), {"month": "2026-12"}), "4/4")
        self.assertNotContains(self.client.get(reverse("dashboard"), {"month": "2027-01"}), "WAZ HARDWARE")

    def test_next_months_statement_does_not_add_the_installment_again(self):
        Expense.objects.create(
            user=self.user, description="Hardware store", original_description="WAZ HARDWARE",
            amount=Decimal("66.15"), date=date(2026, 8, 10), frequency=Frequency.INSTALLMENTS,
            installment_total=4, first_installment_month=date(2026, 9, 1), imported=True,
        )
        upload = SimpleUploadedFile("Fatura.csv", BRAZILIAN_STATEMENT, content_type="text/csv")
        self.client.post(reverse("import_statement"), {"file": upload, "date_order": "dmy"})
        forms = self.client.get(reverse("import_review")).context["formset"].forms
        self.assertFalse(forms[0].initial["include"])
        self.assertTrue(forms[0].initial["duplicate"])

    def test_add_installments_by_hand(self):
        self.client.post(reverse("add_expense"), {
            "description": "Laptop", "amount": "500.00", "date": "2026-11-02",
            "category": cat(self.user, "shopping").pk, "frequency": "installments", "installment_total": "10",
        })
        laptop = Expense.objects.get(description="Laptop")
        self.assertEqual(laptop.last_installment_month, date(2027, 8, 1))

        response = self.client.post(reverse("add_expense"), {
            "description": "Phone", "amount": "100.00", "date": "2026-11-02",
            "category": cat(self.user, "shopping").pk, "frequency": "installments", "installment_total": "",
        })
        self.assertContains(response, "How many installments in total?")


class DetailAndSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(self.user)

    def test_detail_shows_original_name_after_renaming(self):
        expense = Expense.objects.create(
            user=self.user, description="ANTHROPIC* CLAUDE SUB SA", original_description="ANTHROPIC* CLAUDE SUB  SA",
            amount=Decimal("116.22"), date=date(2026, 10, 20), imported=True, source_file="Fatura.csv",
        )
        url = reverse("entry_detail", args=["expense", expense.pk])
        self.client.post(url, {
            "description": "Claude", "amount": "116.22", "date": "2026-10-20",
            "category": cat(self.user, "subscriptions").pk, "frequency": "monthly", "installment_total": "",
        })
        expense.refresh_from_db()
        self.assertEqual(expense.description, "Claude")
        response = self.client.get(url)
        self.assertContains(response, "ANTHROPIC* CLAUDE SUB  SA")
        self.assertContains(response, "Fatura.csv")

    def test_cannot_open_someone_elses_expense(self):
        other = User.objects.create_user("sam", password="a-long-password-123")
        expense = Expense.objects.create(user=other, description="Secret", amount=Decimal("1"), date=TODAY)
        response = self.client.get(reverse("entry_detail", args=["expense", expense.pk]))
        self.assertEqual(response.status_code, 404)

    def test_change_currency_and_language(self):
        Income.objects.create(user=self.user, description="Salary", amount=Decimal("1000"), date=TODAY, frequency=Frequency.MONTHLY)
        self.client.post(reverse("user_settings"), {"language": "pt-br", "currency": "USD"})
        self.assertEqual(get_profile(self.user).currency, "USD")
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "$1,000.00")
        self.assertContains(response, "Importar fatura")

        self.client.post(reverse("user_settings"), {"language": "es", "currency": "BRL"})
        self.assertContains(self.client.get(reverse("dashboard")), "Importar estado de cuenta")

    def test_visitors_see_english_by_default(self):
        self.client.logout()
        self.assertContains(self.client.get(reverse("login")), "Log in")


class RefundTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(self.user)

    def review(self, csv_text):
        upload = SimpleUploadedFile("Fatura.csv", csv_text.encode(), content_type="text/csv")
        self.client.post(reverse("import_statement"), {"file": upload, "date_order": "dmy"})
        return self.client.get(reverse("import_review"))

    def test_refund_cancels_the_fee(self):
        response = self.review(
            "Data de Compra;Categoria;Descrição;Parcela;Valor (em US$);Cotação (em R$);Valor (em R$)\n"
            "17/10/2026;Restaurante;CANOA ACORIANA;Única;0;0;23.10\n"
            "12/10/2026;Departamento;AMAZON BR   *AMAZON BR;2/2;0;0;88.10\n"
            '20/10/2026;-;"Inclusao de Pagamento    ";Única;0;0;-88.10\n'
            "08/11/2026;-;Anuidade Diferenciada;6/12;0;0;50.00\n"
            "08/11/2026;-;Estorno Tarifa;Única;0;0;-50.00\n"
        )
        rows = {f.initial["description"]: f.initial for f in response.context["formset"].forms}

        fee, refund = rows["Anuidade Diferenciada"], rows["Estorno Tarifa"]
        self.assertFalse(fee["include"])
        self.assertFalse(refund["include"])
        self.assertEqual(fee["refunded_by"], "Estorno Tarifa")
        self.assertEqual(refund["refund_for"], "Anuidade Diferenciada")
        self.assertContains(response, "Cancelled by the refund")

        # A bill payment is not a refund: the Amazon purchase with the same amount still counts.
        self.assertTrue(rows["Inclusao de Pagamento"]["is_payment"])
        self.assertTrue(rows["AMAZON BR *AMAZON BR"]["include"])
        self.assertNotIn("refunded_by", rows["AMAZON BR *AMAZON BR"])

        self.client.post(reverse("import_review"), review_post(response))
        saved = set(Expense.objects.values_list("description", flat=True))
        self.assertEqual(saved, {"CANOA ACORIANA", "AMAZON BR *AMAZON BR"})

    def test_refund_picks_the_matching_charge(self):
        response = self.review(
            "date,title,amount\n"
            "2026-11-01,Uber *Trip,30.00\n"
            "2026-11-02,Loja Renner,30.00\n"
            "2026-11-09,Estorno Loja Renner,-30.00\n"
        )
        rows = {f.initial["description"]: f.initial for f in response.context["formset"].forms}
        self.assertTrue(rows["Uber *Trip"]["include"])
        self.assertFalse(rows["Loja Renner"]["include"])


class PanelAndCategoryTests(TestCase):
    """The '+ New' menu opens forms in a sliding panel (requests with the X-Panel header)."""

    def setUp(self):
        self.user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(self.user)

    def test_header_has_the_new_menu(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, 'id="new-menu"')
        for name in ["import_statement", "add_expense", "add_income"]:
            self.assertContains(response, f'href="{reverse(name)}" data-panel')

    def test_panel_gets_only_the_form(self):
        response = self.client.get(reverse("add_expense"), headers={"X-Panel": "1"})
        self.assertNotContains(response, "<html")
        self.assertContains(response, 'class="chips"')
        self.assertContains(response, "Food &amp; groceries")

        full_page = self.client.get(reverse("add_expense"))
        self.assertContains(full_page, "<html")

    def test_panel_save_answers_with_where_to_go(self):
        response = self.client.post(reverse("add_income"), {
            "description": "Freelance site", "amount": "800.00", "date": "2026-11-03",
            "frequency": "once", "category": cat(self.user, "freelance", "income").pk,
        }, headers={"X-Panel": "1"})
        self.assertEqual(response.json(), {"redirect": "/?month=2026-11"})
        self.assertEqual(Income.objects.get().category.system_key, "freelance")

    def test_panel_errors_come_back_as_the_form(self):
        response = self.client.post(reverse("add_expense"), {"description": ""}, headers={"X-Panel": "1"})
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        self.assertContains(response, 'class="error"')

    def test_income_form_has_income_categories(self):
        response = self.client.get(reverse("add_income"))
        self.assertContains(response, "Investments")
        self.assertContains(response, "Freelance")
        self.assertNotContains(response, "Food &amp; groceries")


class CategorySettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(self.user)
        ensure_default_categories(self.user)

    def test_settings_lists_both_kinds(self):
        response = self.client.get(reverse("user_settings"))
        self.assertContains(response, "Food &amp; groceries")
        self.assertContains(response, "Investments")
        self.assertContains(response, f'href="{reverse("category_new")}?kind=expense" data-panel')

    def test_create_category_and_use_it(self):
        self.client.post(f'{reverse("category_new")}?kind=expense', {"name": "Pets", "color": "#c9755b"})
        pets = CategoryModel.objects.get(user=self.user, name="Pets")
        self.assertEqual((pets.kind, pets.color), ("expense", "#c9755b"))

        response = self.client.get(reverse("add_expense"))
        self.assertContains(response, "Pets")
        self.assertContains(response, "Manage categories")

        self.client.post(reverse("add_expense"), {
            "description": "Vet", "amount": "150.00", "date": "2026-11-05",
            "category": pets.pk, "frequency": "once",
        })
        Income.objects.create(user=self.user, description="Salary", amount=Decimal("1000"), date=date(2026, 11, 1))
        chart = self.client.get(reverse("dashboard"), {"month": "2026-11"}).context["chart"]
        self.assertEqual([(s["label"], s["color"]) for s in chart["segments"]], [("Pets", "#c9755b")])

    def test_duplicate_names_are_rejected(self):
        response = self.client.post(f'{reverse("category_new")}?kind=expense', {"name": "health", "color": "#c9755b"})
        self.assertContains(response, "You already have a category with this name.")
        # The same name is fine for the other kind.
        self.client.post(f'{reverse("category_new")}?kind=income', {"name": "Health", "color": "#c9755b"})
        self.assertTrue(CategoryModel.objects.filter(user=self.user, kind="income", name="Health").exists())

    def test_rename_default_category(self):
        food = cat(self.user, "food")
        self.client.post(reverse("category_edit", args=[food.pk]), {"name": "Comida", "color": food.color})
        food.refresh_from_db()
        self.assertEqual(food.label, "Comida")
        self.assertEqual(food.system_key, "food")  # still used by the import's guesses

    def test_saving_a_default_without_renaming_keeps_it_translated(self):
        food = cat(self.user, "food")
        self.client.post(reverse("category_edit", args=[food.pk]), {"name": "Food & groceries", "color": "#c9755b"})
        food.refresh_from_db()
        self.assertEqual(food.name, "")
        self.assertEqual(food.color, "#c9755b")

    def test_delete_moves_items_to_other(self):
        food = cat(self.user, "food")
        dinner = Expense.objects.create(user=self.user, description="Dinner", amount=Decimal("80"), date=TODAY, category=food)
        response = self.client.get(reverse("category_edit", args=[food.pk]))
        self.assertContains(response, "moves its 1 item to")

        self.client.post(reverse("category_delete", args=[food.pk]))
        dinner.refresh_from_db()
        self.assertEqual(dinner.category.system_key, "other")
        self.assertFalse(CategoryModel.objects.filter(pk=food.pk).exists())

    def test_other_cannot_be_deleted(self):
        other = cat(self.user, "other")
        self.client.post(reverse("category_delete", args=[other.pk]))
        self.assertTrue(CategoryModel.objects.filter(pk=other.pk).exists())

    def test_cannot_touch_someone_elses_category(self):
        stranger = User.objects.create_user("sam", password="a-long-password-123")
        theirs = cat(stranger, "food")
        self.assertEqual(self.client.get(reverse("category_edit", args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("category_delete", args=[theirs.pk])).status_code, 404)
        response = self.client.post(reverse("add_expense"), {
            "description": "Sneaky", "amount": "1.00", "date": "2026-11-05", "category": theirs.pk, "frequency": "once",
        })
        self.assertContains(response, "Select a valid choice")

    def test_import_uses_other_when_the_guessed_category_was_deleted(self):
        self.client.post(reverse("category_delete", args=[cat(self.user, "subscriptions").pk]))
        upload = SimpleUploadedFile("s.csv", b"date,title,amount\n2026-11-01,Netflix,39.90\n", content_type="text/csv")
        self.client.post(reverse("import_statement"), {"file": upload, "date_order": "dmy"})
        row = self.client.get(reverse("import_review")).context["formset"].forms[0].initial
        self.assertEqual(row["category"], cat(self.user, "other").pk)
        self.assertEqual(row["frequency"], "once")

    def test_new_users_get_default_categories(self):
        newcomer = User.objects.create_user("new", password="a-long-password-123")
        self.client.force_login(newcomer)
        self.client.get(reverse("dashboard"))
        self.assertEqual(CategoryModel.objects.filter(user=newcomer).count(), 15)


class UploadPageTests(TestCase):
    def test_upload_field_is_a_drop_area(self):
        user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(user)
        response = self.client.get(reverse("import_statement"), headers={"X-Panel": "1"})
        self.assertContains(response, "data-dropzone")
        self.assertContains(response, 'class="dropzone-input"')
        self.assertContains(response, "Drag your statement here")


class DonutCenterAndFilterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(self.user)
        Income.objects.create(user=self.user, description="Salary", amount=Decimal("2200"), date=date(2026, 1, 5), frequency=Frequency.MONTHLY)
        Expense.objects.create(user=self.user, description="Rent", amount=Decimal("1245.96"), date=date(2026, 11, 3))

    def dashboard(self, **params):
        return self.client.get(reverse("dashboard"), {"month": "2026-11", **params})

    def test_center_shows_what_was_spent(self):
        response = self.dashboard()
        self.assertContains(response, '<span class="donut-amount">R$ 1.245,96</span>', html=True)
        self.assertContains(response, "of R$ 2.200,00")
        self.assertNotContains(response, "spent ·")
        self.assertContains(response, "R$ 954,04")  # "Left" is still in the legend

    def test_legend_rows_carry_the_details_for_the_centre(self):
        response = self.dashboard()
        self.assertContains(response, 'data-chart')
        self.assertContains(response, 'class="seg" data-index="0"')
        self.assertContains(response, 'data-detail="57% of income · 100% of spending"')
        self.assertContains(response, 'data-index="left"')
        self.assertContains(response, 'data-detail="43% of income"')

        self.client.post(reverse("user_settings"), {"language": "pt-br", "currency": "BRL"})
        self.assertContains(self.dashboard(), 'data-detail="57% da renda · 100% dos gastos"')

    def test_center_when_over_budget(self):
        Expense.objects.create(user=self.user, description="Trip", amount=Decimal("1254.04"), date=date(2026, 11, 9))
        response = self.dashboard()
        self.assertContains(response, '<span class="donut-amount negative">R$ 2.500,00</span>', html=True)
        self.assertContains(response, "R$ 300,00 over budget")

    def test_filter_counts_and_hidden_entries(self):
        response = self.dashboard(type="income")
        self.assertEqual(response.context["filters"], [("all", "All", 2), ("expense", "Expenses", 1), ("income", "Income", 1)])
        html = response.content.decode()
        self.assertRegex(html, r'<li class="entry" data-kind="expense" data-search="[^"]*" hidden>')
        self.assertRegex(html, r'<li class="entry" data-kind="income" data-search="[^"]*">')
        self.assertContains(response, 'data-filter="income" aria-current="true"')
        # Month arrows keep the filter.
        self.assertContains(response, "?month=2026-10&amp;type=income")

    def test_filter_empty_message(self):
        Income.objects.all().delete()
        response = self.dashboard(type="income")
        self.assertContains(response, '<p class="muted" data-empty-for="income">No income this month.</p>', html=True)

    def test_unknown_filter_means_all(self):
        response = self.dashboard(type="nonsense")
        self.assertEqual(response.context["active_filter"], "all")
        html = response.content.decode()
        self.assertRegex(html, r'<li class="entry" data-kind="expense" data-search="[^"]*">')
        self.assertRegex(html, r'<li class="entry" data-kind="income" data-search="[^"]*">')

    def test_entries_have_search_text(self):
        Expense.objects.create(user=self.user, description="Uber", original_description="UBER *TRIP HELP.UBER.COM",
                               amount=Decimal("20"), date=date(2026, 11, 5), category=cat(self.user, "transport"))
        response = self.dashboard()
        self.assertContains(response, 'data-search="Uber UBER *TRIP HELP.UBER.COM Transport"')
        self.assertContains(response, "data-transactions-search")
        self.assertContains(response, "data-empty-search")


class LegendListTests(TestCase):
    def test_each_category_lists_its_expenses_biggest_first(self):
        user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(user)
        Income.objects.create(user=user, description="Salary", amount=Decimal("3000"), date=date(2026, 11, 1))
        food = cat(user, "food")
        Expense.objects.create(user=user, description="Bakery", amount=Decimal("12.50"), date=date(2026, 11, 2), category=food)
        Expense.objects.create(user=user, description="Supermarket", amount=Decimal("230.00"), date=date(2026, 11, 4), category=food)
        Expense.objects.create(user=user, description="Gym", amount=Decimal("99.00"), date=date(2026, 11, 5), category=cat(user, "health"))

        response = self.client.get(reverse("dashboard"), {"month": "2026-11"})
        segments = response.context["chart"]["segments"]
        self.assertEqual([s["label"] for s in segments], ["Food & groceries", "Health"])
        self.assertEqual([e.description for e in segments[0]["items"]], ["Supermarket", "Bakery"])
        self.assertEqual([e.description for e in segments[1]["items"]], ["Gym"])
        self.assertContains(response, 'aria-controls="legend-items-0"')
        self.assertContains(response, 'data-details-for="0" inert')


class SeeAllTests(TestCase):
    def test_only_five_shown_until_see_all(self):
        user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(user)
        Income.objects.create(user=user, description="Salary", amount=Decimal("3000"), date=date(2026, 11, 1))
        food = cat(user, "food")
        for i in range(7):
            Expense.objects.create(user=user, description=f"Meal {i}", amount=Decimal(10 + i), date=date(2026, 11, 2), category=food)

        response = self.client.get(reverse("dashboard"), {"month": "2026-11"})
        self.assertContains(response, 'class="legend-entries is-collapsed"')
        self.assertContains(response, 'class="legend-entry-extra"', count=2)
        self.assertContains(response, "See all (7)")

        self.client.post(reverse("user_settings"), {"language": "pt-br", "currency": "BRL"})
        self.assertContains(self.client.get(reverse("dashboard"), {"month": "2026-11"}), "Ver todas (7)")


class ConfirmDialogAndToastTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(self.user)

    def test_delete_buttons_use_our_dialog(self):
        Income.objects.create(user=self.user, description="Vale-refeição", amount=Decimal("550"), date=TODAY, frequency=Frequency.MONTHLY)
        response = self.client.get(reverse("dashboard"), {"month": "2026-11"})
        self.assertContains(response, 'id="confirm-dialog"')
        self.assertContains(response, 'data-confirm="Delete this entry from every month?" data-confirm-detail="Vale-refeição" data-next-current')
        self.assertNotContains(response, "confirm(")

    def test_messages_appear_as_toasts(self):
        income = Income.objects.create(user=self.user, description="Vale-refeição", amount=Decimal("550"), date=TODAY)
        response = self.client.post(reverse("delete_entry", args=["income", income.pk]),
                                    {"next": "/?month=2026-11&type=income"}, follow=True)
        self.assertRedirects(response, "/?month=2026-11&type=income")
        self.assertContains(response, '<div class="toast success" role="status">')
        self.assertContains(response, "Deleted “Vale-refeição”.")
        self.assertContains(response, "data-toast-close")


class GearAndSettingsTabsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(self.user)

    def test_header_has_a_gear_link_with_a_label(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, 'class="icon-link" aria-label="Settings" data-tooltip="Settings"')

    def test_general_tab_is_the_default(self):
        response = self.client.get(reverse("user_settings"))
        self.assertEqual(response.context["tab"], "general")
        self.assertContains(response, '<section class="card" data-tab-panel="general">')
        self.assertContains(response, 'data-tab-panel="account" hidden')
        self.assertContains(response, 'data-tab="general" aria-current="true"')

    def test_open_a_tab_from_the_address(self):
        response = self.client.get(reverse("user_settings"), {"tab": "account"})
        self.assertContains(response, '<section class="card account" data-tab-panel="account">')
        self.assertContains(response, "Log out")
        self.assertContains(response, 'data-tab-panel="general" hidden')
        self.assertEqual(self.client.get(reverse("user_settings"), {"tab": "nope"}).context["tab"], "general")

    def test_saving_returns_to_the_right_tab(self):
        response = self.client.post(reverse("user_settings"), {"language": "en", "currency": "BRL"})
        self.assertRedirects(response, "/settings/?tab=general")
        response = self.client.post(f'{reverse("category_new")}?kind=expense', {"name": "Pets", "color": "#c9755b"})
        self.assertRedirects(response, "/settings/?tab=categories")

    def test_manage_categories_link_opens_the_categories_tab(self):
        self.assertContains(self.client.get(reverse("add_expense")), "/settings/?tab=categories")



class SubscriptionMatchingTests(TestCase):
    """Next month's statement: subscriptions are recognised even when codes or prices change."""

    def setUp(self):
        self.user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(self.user)
        subs = cat(self.user, "subscriptions")
        self.spotify = Expense.objects.create(
            user=self.user, description="Spotify", original_description="DL*SPOTIFY P48A3",
            amount=Decimal("23.90"), date=date(2026, 10, 30), frequency=Frequency.MONTHLY, category=subs)
        self.claude = Expense.objects.create(
            user=self.user, description="Claude", original_description="ANTHROPIC* CLAUDE SUB  SA",
            amount=Decimal("116.22"), date=date(2026, 10, 20), frequency=Frequency.MONTHLY, category=subs)

    def review(self, csv_text):
        upload = SimpleUploadedFile("Fatura_dez.csv", csv_text.encode(), content_type="text/csv")
        self.client.post(reverse("import_statement"), {"file": upload, "date_order": "dmy"})
        return self.client.get(reverse("import_review"))

    DECEMBER = (
        "date,title,amount\n"
        "2026-11-30,DL*SPOTIFY Q91B2,23.90\n"          # new bank code, same price
        "2026-11-20,ANTHROPIC* CLAUDE SUB SA,119.08\n"  # dollar went up
        "2026-11-20,ANTHROPIC* CLAUDE SUB SA,4.17\n"    # its IOF: not the subscription
        "2026-12-02,Padaria Real,12.50\n"
        "2026-12-03,Netflix,55.90\n"                    # a subscription the app doesn't know yet
    )

    def test_name_key_ignores_codes(self):
        from .matching import name_key
        self.assertEqual(name_key("DL*SPOTIFY P48A3"), "spotify")
        self.assertEqual(name_key("ANTHROPIC* CLAUDE SUB  SA"), "anthropic claude sub")
        self.assertEqual(name_key("JIM.COM* 62519589 APO"), "jim com apo")

    def test_rows_are_grouped(self):
        response = self.review(self.DECEMBER)
        new = [f.initial["description"] for f in response.context["new_rows"]]
        registered = [f.initial["description"] for f in response.context["registered_rows"]]
        self.assertEqual(sorted(registered), ["ANTHROPIC* CLAUDE SUB SA", "DL*SPOTIFY Q91B2"])
        self.assertEqual(sorted(new), ["ANTHROPIC* CLAUDE SUB SA", "Netflix", "Padaria Real"])  # the IOF is new
        self.assertTrue(response.context["has_price_changes"])
        self.assertContains(response, "price changed R$ 116,22 → R$ 119,08")

    def test_price_change_keeps_past_months(self):
        response = self.review(self.DECEMBER)
        self.client.post(reverse("import_review"), review_post(response))

        self.claude.refresh_from_db()
        self.assertEqual(self.claude.ends_on, date(2026, 11, 1))  # old price ends in November
        new_claude = Expense.objects.get(description="Claude", ends_on__isnull=True)
        self.assertEqual((new_claude.amount, new_claude.date), (Decimal("119.08"), date(2026, 12, 20)))

        def claude_in(month):
            return [e.amount for e in Expense.objects.filter(user=self.user, description="Claude").for_month(2026, month)]
        self.assertEqual(claude_in(11), [Decimal("116.22")])
        self.assertEqual(claude_in(12), [Decimal("119.08")])

        # Spotify (same price) wasn't touched or duplicated, and the new rows were added.
        self.assertEqual(Expense.objects.filter(original_description__icontains="spotify").count(), 1)
        self.assertTrue(Expense.objects.filter(description="Netflix").exists())

    def test_unchecking_update_keeps_the_old_price(self):
        response = self.review(self.DECEMBER)
        post = review_post(response)
        for key in [k for k in post if k.endswith("-update_recurring")]:
            del post[key]
        self.client.post(reverse("import_review"), post)
        self.claude.refresh_from_db()
        self.assertIsNone(self.claude.ends_on)
        self.assertEqual(Expense.objects.filter(description="Claude").count(), 1)

    def test_big_price_difference_is_not_the_same_subscription(self):
        response = self.review("date,title,amount\n2026-12-01,DL*SPOTIFY X1,59.90\n")
        self.assertEqual(len(response.context["registered_rows"]), 0)

    def test_ended_subscriptions_are_not_matched(self):
        self.spotify.ends_on = date(2026, 10, 1)
        self.spotify.save()
        response = self.review("date,title,amount\n2026-11-30,DL*SPOTIFY Q91B2,23.90\n")
        self.assertEqual(len(response.context["new_rows"]), 1)


class TrendTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(self.user)

    def test_monthly_totals_for_the_line_chart(self):
        from .charts import build_trend
        food = cat(self.user, "food")
        Income.objects.create(user=self.user, description="Salary", amount=Decimal("2000"), date=date(2026, 9, 5), frequency=Frequency.MONTHLY)
        Expense.objects.create(user=self.user, description="Rent", amount=Decimal("800"), date=date(2026, 10, 1), frequency=Frequency.MONTHLY)
        Expense.objects.create(user=self.user, description="Dinner", amount=Decimal("120"), date=date(2026, 11, 3), category=food)

        months = build_trend(self.user, date(2026, 11, 1), months=3)["months"]
        self.assertEqual([m["month"] for m in months], ["2026-09", "2026-10", "2026-11"])
        self.assertEqual([m["income"] for m in months], [2000.0, 2000.0, 2000.0])
        self.assertEqual([m["expenses"] for m in months], [0.0, 800.0, 920.0])
        self.assertEqual(months[2]["categories"][str(food.pk)], 120.0)

    def test_dashboard_has_the_trend_view(self):
        Income.objects.create(user=self.user, description="Salary", amount=Decimal("2000"), date=date(2026, 9, 5), frequency=Frequency.MONTHLY)
        Expense.objects.create(user=self.user, description="Dinner", amount=Decimal("120"), date=date(2026, 11, 3))
        response = self.client.get(reverse("dashboard"), {"month": "2026-11"})
        self.assertContains(response, 'data-view="trend"')
        self.assertContains(response, '<script id="trend-data" type="application/json">')
        self.assertEqual(len(response.context["trend"]["months"]), 12)
        self.assertEqual(response.context["trend"]["currency"]["symbol"], "R$")
        self.assertContains(response, 'data-category-id="')


class FakeResponse:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self._content = content

    def to_dict(self):
        return {"content": self._content}


class FakeClaude:
    """Stands in for anthropic.Anthropic(): returns prepared responses and records the requests."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = self
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))  # a snapshot, like sending it
        return self.responses.pop(0)


class AssistantTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="a-long-password-123")
        self.client.force_login(self.user)
        today = date.today()
        self.month = today.replace(day=1)
        Income.objects.create(user=self.user, description="Salary", amount=Decimal("3000"), date=self.month, frequency=Frequency.MONTHLY)
        Expense.objects.create(user=self.user, description="Uber *Trip", amount=Decimal("25.50"), date=self.month, category=cat(self.user, "transport"))
        Expense.objects.create(user=self.user, description="Spotify", amount=Decimal("23.90"), date=self.month, frequency=Frequency.MONTHLY, category=cat(self.user, "subscriptions"))

    def ask(self, fake, question="Where am I spending the most?"):
        with mock.patch("transactions.assistant.is_configured", return_value=True), \
             mock.patch("transactions.assistant.get_client", return_value=fake):
            return self.client.post(reverse("assistant_ask"), {"message": question})

    def test_without_api_key_it_explains_how_to_turn_it_on(self):
        with mock.patch("transactions.assistant.is_configured", return_value=False):
            response = self.client.post(reverse("assistant_ask"), {"message": "Hi"})
        self.assertFalse(response.json()["configured"])
        self.assertIn("ANTHROPIC_API_KEY", response.json()["reply"])

    def test_answer_with_a_tool_call(self):
        fake = FakeClaude([
            FakeResponse("tool_use", [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "toolu_1", "name": "month_summary", "input": {"month": None}},
            ]),
            FakeResponse("end_turn", [{"type": "text", "text": "Your biggest category is **Transport**."}]),
        ])
        with self.settings(AI_MODEL="claude-opus-5-5", AI_EFFORT="low"):  # not whatever is in your .env
            response = self.ask(fake)
        self.assertEqual(response.json(), {"configured": True, "reply": "Your biggest category is **Transport**.", "drafts": []})

        # The tool result Claude received has the real numbers for this user.
        tool_result = fake.requests[1]["messages"][-1]["content"][0]
        self.assertEqual(tool_result["tool_use_id"], "toolu_1")
        summary = json.loads(tool_result["content"])
        self.assertEqual((summary["income"], summary["expenses"]), (3000.0, 49.4))
        self.assertEqual(summary["expenses_by_category"][0]["category"], "Transport")

        # How Claude was called.
        first = fake.requests[0]
        self.assertEqual(first["model"], "claude-opus-5-5")
        self.assertEqual(first["fallbacks"], "default")
        self.assertEqual(first["output_config"], {"effort": "low"})
        self.assertTrue(all(tool["strict"] for tool in first["tools"]))
        self.assertIn("Reply in English", first["system"])

        # The conversation is kept for follow-up questions, and can be shown again.
        history = self.client.get(reverse("assistant_history")).json()["messages"]
        self.assertEqual([m["role"] for m in history], ["user", "assistant"])

    def test_haiku_is_called_without_the_refusal_fallback(self):
        fake = FakeClaude([FakeResponse("end_turn", [{"type": "text", "text": "Hi!"}])])
        with self.settings(AI_MODEL="claude-haiku-5-5"):
            response = self.ask(fake, "Hi")
        self.assertEqual(response.json()["reply"], "Hi!")
        self.assertEqual(fake.requests[0]["model"], "claude-haiku-5-5")
        self.assertNotIn("fallbacks", fake.requests[0])
        self.assertNotIn("betas", fake.requests[0])

    def test_follow_up_questions_send_the_whole_conversation(self):
        self.ask(FakeClaude([FakeResponse("end_turn", [{"type": "text", "text": "Hello!"}])]), "Hi")
        fake = FakeClaude([FakeResponse("end_turn", [{"type": "text", "text": "Sure."}])])
        self.ask(fake, "And now?")
        roles = [m["role"] for m in fake.requests[0]["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user"])

    def test_reset_starts_a_new_conversation(self):
        self.ask(FakeClaude([FakeResponse("end_turn", [{"type": "text", "text": "Hello!"}])]), "Hi")
        self.client.post(reverse("assistant_reset"))
        self.assertEqual(self.client.get(reverse("assistant_history")).json()["messages"], [])

    def test_refusal(self):
        response = self.ask(FakeClaude([FakeResponse("refusal", [])]))
        self.assertEqual(response.json()["reply"], "Sorry, I can't help with that one.")

    def test_replies_in_the_users_language(self):
        self.client.post(reverse("user_settings"), {"language": "pt-br", "currency": "BRL"})
        fake = FakeClaude([FakeResponse("end_turn", [{"type": "text", "text": "Oi!"}])])
        self.ask(fake, "Oi")
        self.assertIn("Reply in Brazilian Portuguese", fake.requests[0]["system"])
        self.assertIn("R$ 1.234,56", fake.requests[0]["system"])

    def test_search_includes_recurring_items_in_each_month(self):
        from .assistant import Tools
        result = Tools(self.user).tool_search_transactions(
            text="spotify", category=None, kind="expense",
            month_from=f"{add_months(self.month, -2):%Y-%m}", month_to=f"{add_months(self.month, 1):%Y-%m}", limit=10)
        self.assertEqual(result["count"], 2)  # this month and next month (it started this month)
        self.assertEqual(result["total_expenses"], 47.8)

    def test_upcoming_commitments(self):
        from .assistant import Tools
        Expense.objects.create(user=self.user, description="Laptop", amount=Decimal("500"), date=self.month,
                               frequency=Frequency.INSTALLMENTS, installment_total=3, category=cat(self.user, "shopping"))
        months = Tools(self.user).tool_upcoming_commitments(months_ahead=3)["months"]
        self.assertEqual(months[0]["installments"][0]["installment"], "2/3")
        self.assertEqual(months[0]["committed_total"], 523.9)  # laptop + Spotify
        self.assertEqual(months[0]["free_after_commitments"], 2476.1)
        self.assertEqual(months[2]["installments"], [])  # the laptop is paid off by then

    def test_tools_only_see_the_users_own_data(self):
        from .assistant import Tools
        stranger = User.objects.create_user("sam", password="a-long-password-123")
        Expense.objects.create(user=stranger, description="Secret Uber", amount=Decimal("99"), date=self.month)
        result = Tools(self.user).tool_search_transactions(
            text="uber", category=None, kind="any", month_from=None, month_to=None, limit=10)
        self.assertEqual([i["description"] for i in result["items"]], ["Uber *Trip"])

    # --- Adding by chat: drafts that the user confirms ---

    def draft_reply(self, tool, tool_input, text="Check the card and click Add."):
        return FakeClaude([
            FakeResponse("tool_use", [{"type": "tool_use", "id": "toolu_d", "name": tool, "input": tool_input}]),
            FakeResponse("end_turn", [{"type": "text", "text": text}]),
        ])

    def test_draft_is_not_saved_until_confirmed(self):
        yesterday = (date.today().toordinal() - 1)
        yesterday = date.fromordinal(yesterday).isoformat()
        fake = self.draft_reply("draft_expense", {
            "description": "Pizza", "amount": 45, "date": yesterday, "category": None,
            "repeats": "once", "installments": None,
        })
        response = self.ask(fake, "add 45 reais of pizza yesterday")
        draft = response.json()["drafts"][0]
        self.assertEqual((draft["description"], draft["amount_display"], draft["date"]), ("Pizza", "R$ 45,00", yesterday))
        self.assertEqual(draft["category_label"], "Food & groceries")  # guessed from "pizza"
        self.assertFalse(Expense.objects.filter(description="Pizza").exists())  # not saved yet

        # Claude was told it's only a draft.
        tool_result = json.loads(fake.requests[1]["messages"][-1]["content"][0]["content"])
        self.assertIn("not saved yet", tool_result["status"])

        url = reverse("assistant_draft_action", args=[draft["id"], "confirm"])
        result = self.client.post(url).json()
        self.assertEqual(result["status"], "confirmed")
        pizza = Expense.objects.get(description="Pizza")
        self.assertEqual((pizza.amount, pizza.date.isoformat(), pizza.category.system_key), (Decimal("45.00"), yesterday, "food"))

        # Clicking twice doesn't add it twice.
        self.assertEqual(self.client.post(url).status_code, 400)
        self.assertEqual(Expense.objects.filter(description="Pizza").count(), 1)

        # The chat shows it as added after a reload, and Claude hears about it with the next question.
        history = self.client.get(reverse("assistant_history")).json()["messages"]
        self.assertEqual(history[-1]["drafts"][0]["status"], "confirmed")
        next_question = FakeClaude([FakeResponse("end_turn", [{"type": "text", "text": "Great."}])])
        self.ask(next_question, "thanks")
        self.assertIn("The user saved draft", next_question.requests[0]["messages"][-1]["content"])

    def test_cancel_saves_nothing(self):
        response = self.ask(self.draft_reply("draft_expense", {
            "description": "Shoes", "amount": 200, "date": None, "category": None, "repeats": "once", "installments": None,
        }))
        draft_id = response.json()["drafts"][0]["id"]
        result = self.client.post(reverse("assistant_draft_action", args=[draft_id, "cancel"])).json()
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(Expense.objects.filter(description="Shoes").exists())

    def test_installments_and_income_drafts(self):
        response = self.ask(self.draft_reply("draft_expense", {
            "description": "Phone", "amount": 200, "date": None, "category": "Shopping",
            "repeats": "installments", "installments": 4,
        }))
        draft_id = response.json()["drafts"][0]["id"]
        self.client.post(reverse("assistant_draft_action", args=[draft_id, "confirm"]))
        phone = Expense.objects.get(description="Phone")
        self.assertEqual((phone.installment_total, phone.category.system_key), (4, "shopping"))
        self.assertEqual(phone.last_installment_month, add_months(self.month, 3))

        response = self.ask(self.draft_reply("draft_income", {
            "description": "Website job", "amount": 800, "date": None, "category": "Freelance", "repeats": "once",
        }))
        draft_id = response.json()["drafts"][0]["id"]
        self.client.post(reverse("assistant_draft_action", args=[draft_id, "confirm"]))
        self.assertEqual(Income.objects.get(description="Website job").category.system_key, "freelance")

    def test_invalid_draft_goes_back_to_claude_as_an_error(self):
        fake = self.draft_reply("draft_expense", {
            "description": "Gum", "amount": 0, "date": None, "category": None, "repeats": "once", "installments": None,
        }, text="How much was it?")
        response = self.ask(fake)
        self.assertEqual(response.json()["drafts"], [])
        tool_result = fake.requests[1]["messages"][-1]["content"][0]
        self.assertTrue(tool_result["is_error"])
        self.assertIn("greater than zero", tool_result["content"])

    def test_drafts_belong_to_the_session(self):
        response = self.ask(self.draft_reply("draft_expense", {
            "description": "Taxi", "amount": 30, "date": None, "category": None, "repeats": "once", "installments": None,
        }))
        draft_id = response.json()["drafts"][0]["id"]
        stranger = User.objects.create_user("sam", password="a-long-password-123")
        self.client.force_login(stranger)  # a different session
        result = self.client.post(reverse("assistant_draft_action", args=[draft_id, "confirm"]))
        self.assertEqual(result.status_code, 400)
        self.assertFalse(Expense.objects.filter(description="Taxi").exists())

    # --- Finding, editing and deleting by chat ---

    def uber(self):
        return Expense.objects.get(description="Uber *Trip")

    def test_search_results_carry_ids(self):
        from .assistant import Tools
        result = Tools(self.user).tool_search_transactions(
            text="uber", category=None, kind="expense", month_from=None, month_to=None, limit=5)
        self.assertEqual(result["items"][0]["id"], self.uber().pk)

    def test_show_transactions_as_cards(self):
        response = self.ask(self.draft_reply("show_transactions", {"items": [
            {"kind": "expense", "id": self.uber().pk}, {"kind": "expense", "id": 999999},
        ]}, text="Here it is."))
        card = response.json()["drafts"][0]
        self.assertEqual(card["type"], "found")
        self.assertEqual([i["description"] for i in card["items"]], ["Uber *Trip"])
        self.assertEqual(card["items"][0]["url"], reverse("entry_detail", args=["expense", self.uber().pk]))

    def test_propose_and_apply_an_edit(self):
        fake = self.draft_reply("propose_change", {
            "kind": "expense", "id": self.uber().pk, "action": "edit", "description": None,
            "amount": 52, "date": None, "category": "Food & groceries", "repeats": None,
        })
        card = self.ask(fake, "change the uber to 52, it was food").json()["drafts"][0]
        self.assertEqual(card["type"], "change")
        self.assertEqual([(c["before"], c["after"]) for c in card["changes"]],
                         [("R$ 25,50", "R$ 52,00"), ("Transport", "Food & groceries")])
        self.assertEqual(self.uber().amount, Decimal("25.50"))  # nothing changed yet

        result = self.client.post(reverse("assistant_draft_action", args=[card["id"], "confirm"])).json()
        self.assertEqual(result["status"], "confirmed")
        uber = self.uber()
        self.assertEqual((uber.amount, uber.category.system_key), (Decimal("52.00"), "food"))

    def test_propose_and_confirm_a_delete(self):
        card = self.ask(self.draft_reply("propose_change", {
            "kind": "expense", "id": self.uber().pk, "action": "delete", "description": None,
            "amount": None, "date": None, "category": None, "repeats": None,
        })).json()["drafts"][0]
        self.assertTrue(Expense.objects.filter(description="Uber *Trip").exists())
        result = self.client.post(reverse("assistant_draft_action", args=[card["id"], "confirm"])).json()
        self.assertIn("Deleted", result["message"])
        self.assertFalse(Expense.objects.filter(description="Uber *Trip").exists())

    def test_cancelled_change_changes_nothing(self):
        card = self.ask(self.draft_reply("propose_change", {
            "kind": "expense", "id": self.uber().pk, "action": "delete", "description": None,
            "amount": None, "date": None, "category": None, "repeats": None,
        })).json()["drafts"][0]
        self.client.post(reverse("assistant_draft_action", args=[card["id"], "cancel"]))
        self.assertTrue(Expense.objects.filter(description="Uber *Trip").exists())

    def test_recurring_changes_are_flagged(self):
        spotify = Expense.objects.get(description="Spotify")
        card = self.ask(self.draft_reply("propose_change", {
            "kind": "expense", "id": spotify.pk, "action": "edit", "description": None,
            "amount": 25.9, "date": None, "category": None, "repeats": None,
        })).json()["drafts"][0]
        self.assertTrue(card["recurring"])

    def test_cannot_change_someone_elses_transaction(self):
        stranger = User.objects.create_user("sam", password="a-long-password-123")
        theirs = Expense.objects.create(user=stranger, description="Theirs", amount=Decimal("10"), date=self.month)
        fake = self.draft_reply("propose_change", {
            "kind": "expense", "id": theirs.pk, "action": "delete", "description": None,
            "amount": None, "date": None, "category": None, "repeats": None,
        })
        response = self.ask(fake)
        self.assertEqual(response.json()["drafts"], [])
        self.assertTrue(fake.requests[1]["messages"][-1]["content"][0]["is_error"])
        self.assertTrue(Expense.objects.filter(pk=theirs.pk).exists())

    def test_edit_with_nothing_new_is_an_error_for_claude(self):
        fake = self.draft_reply("propose_change", {
            "kind": "expense", "id": self.uber().pk, "action": "edit", "description": "Uber *Trip",
            "amount": 25.5, "date": None, "category": None, "repeats": None,
        })
        self.ask(fake)
        result = fake.requests[1]["messages"][-1]["content"][0]
        self.assertTrue(result["is_error"])
        self.assertIn("Nothing would change", result["content"])

    def test_delete_from_a_found_card_answers_with_json(self):
        response = self.client.post(reverse("delete_entry", args=["expense", self.uber().pk]),
                                    headers={"X-Requested-With": "fetch"})
        self.assertEqual(response.json(), {"message": "Deleted “Uber *Trip”."})

    def test_tool_schemas_follow_the_api_rules(self):
        """The fake Claude in these tests doesn't check schemas, so check the rules the real API enforces."""
        from .assistant import TOOLS

        def check(schema, path):
            if "enum" in schema:
                self.assertIsInstance(schema.get("type"), str, f"{path}: an enum needs one plain type")
                self.assertTrue(all(isinstance(v, str) for v in schema["enum"]), f"{path}: enum values must match the type")
            if schema.get("type") == "object" or "properties" in schema:
                self.assertEqual(schema.get("additionalProperties"), False, f"{path}: strict needs additionalProperties: false")
                self.assertEqual(sorted(schema.get("required", [])), sorted(schema.get("properties", {})), f"{path}: strict needs every field required")
                for name, sub in schema.get("properties", {}).items():
                    check(sub, f"{path}.{name}")
            if "items" in schema:
                check(schema["items"], f"{path}[]")

        for tool in TOOLS:
            self.assertTrue(tool["strict"])
            check(tool["input_schema"], tool["name"])

    def test_javascript_has_no_syntax_errors(self):
        """A syntax error in app.js would break every button, so check it (needs Node.js)."""
        import shutil
        import subprocess
        from pathlib import Path
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        script = Path(__file__).parent / "static" / "transactions" / "app.js"
        result = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dashboard_has_the_ask_bar(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "data-assistant-form")
        self.assertContains(response, "Ask about your finances…")
