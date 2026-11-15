from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .categorize import guess_category
from .currencies import format_money
from .models import Category, Expense, Frequency, Income, get_profile
from .parsers import parse_amount, parse_csv, parse_date, parse_installment, parse_line

TODAY = date(2026, 11, 14)

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
        Expense.objects.create(user=self.user, description="Rent", amount=Decimal("1500"), date=date(2026, 1, 1), frequency=Frequency.MONTHLY, category=Category.HOUSING)
        Expense.objects.create(user=self.user, description="Dinner", amount=Decimal("100"), date=date(2026, 11, 3), category=Category.FOOD)
        Expense.objects.create(user=self.user, description="Old dinner", amount=Decimal("80"), date=date(2026, 10, 3), category=Category.FOOD)

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
        post["form-1-category"] = "other"  # the user changed a category

        response = self.client.post(reverse("import_review"), post)
        self.assertEqual(response.status_code, 302)
        added = Expense.objects.filter(user=self.user, imported=True).order_by("date")
        self.assertEqual([(e.description, e.category) for e in added], [("Netflix", "subscriptions"), ("Uber *Trip", "other")])
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
              "installment_number", "installment_total"]
    for i, f in enumerate(forms):
        for name in fields:
            value = f.initial.get(name)
            post[f"form-{i}-{name}"] = "" if value is None else str(value)
        if f.initial["include"]:
            post[f"form-{i}-include"] = "on"
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
            "category": "shopping", "frequency": "installments", "installment_total": "10",
        })
        laptop = Expense.objects.get(description="Laptop")
        self.assertEqual(laptop.last_installment_month, date(2027, 8, 1))

        response = self.client.post(reverse("add_expense"), {
            "description": "Phone", "amount": "100.00", "date": "2026-11-02",
            "category": "shopping", "frequency": "installments", "installment_total": "",
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
            "category": "subscriptions", "frequency": "monthly", "installment_total": "",
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
