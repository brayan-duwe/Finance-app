"""Move categories from fixed lists into a Category table (one set per user).

Existing expenses and incomes keep their category: "food" becomes the user's
own "Food & groceries" category, and so on.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

# A copy of the defaults at the time of this migration (migrations must not import app code
# that may change later).
DEFAULTS = {
    "expense": [
        ("housing", "#6c8ebf"), ("food", "#e0a458"), ("transport", "#6fb39b"),
        ("subscriptions", "#a08bd6"), ("shopping", "#e07a7a"), ("health", "#5aaec4"),
        ("entertainment", "#d48bb0"), ("fees", "#b59a6d"), ("other", "#9a9ea6"),
    ],
    "income": [
        ("salary", "#5cae7e"), ("freelance", "#6c8ebf"), ("investments", "#a08bd6"),
        ("gifts", "#d48bb0"), ("refunds", "#5aaec4"), ("other", "#9a9ea6"),
    ],
}


def create_categories_and_link(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    Category = apps.get_model("transactions", "Category")
    Expense = apps.get_model("transactions", "Expense")
    Income = apps.get_model("transactions", "Income")

    for user in User.objects.all():
        by_key = {}
        for kind, defaults in DEFAULTS.items():
            for index, (key, color) in enumerate(defaults):
                category = Category.objects.create(
                    user=user, kind=kind, system_key=key, color=color,
                    position=999 if key == "other" else index * 10,
                )
                by_key[(kind, key)] = category

        for model, kind in ((Expense, "expense"), (Income, "income")):
            for entry in model.objects.filter(user=user):
                entry.new_category = by_key.get((kind, entry.category)) or by_key[(kind, "other")]
                entry.save(update_fields=["new_category"])


class Migration(migrations.Migration):
    dependencies = [
        ("transactions", "0003_income_category"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Category",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("expense", "Expenses"), ("income", "Income")], max_length=10)),
                ("name", models.CharField(blank=True, max_length=40, verbose_name="name")),
                ("color", models.CharField(default="#6c8ebf", max_length=7, verbose_name="colour")),
                ("system_key", models.CharField(blank=True, editable=False, max_length=20)),
                ("position", models.PositiveSmallIntegerField(default=500, editable=False)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name="categories", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["position", "id"], "verbose_name": "category", "verbose_name_plural": "categories"},
        ),
        migrations.AddField(
            model_name="expense",
            name="new_category",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="+", to="transactions.category"),
        ),
        migrations.AddField(
            model_name="income",
            name="new_category",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="+", to="transactions.category"),
        ),
        migrations.RunPython(create_categories_and_link, migrations.RunPython.noop),
        migrations.RemoveField(model_name="expense", name="category"),
        migrations.RemoveField(model_name="income", name="category"),
        migrations.RenameField(model_name="expense", old_name="new_category", new_name="category"),
        migrations.RenameField(model_name="income", old_name="new_category", new_name="category"),
        migrations.AlterField(
            model_name="expense",
            name="category",
            field=models.ForeignKey(limit_choices_to={"kind": "expense"}, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="expenses", to="transactions.category", verbose_name="category"),
        ),
        migrations.AlterField(
            model_name="income",
            name="category",
            field=models.ForeignKey(limit_choices_to={"kind": "income"}, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="incomes", to="transactions.category", verbose_name="category"),
        ),
    ]
