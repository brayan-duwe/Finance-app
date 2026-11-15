from django.contrib import admin

from .models import Category, Expense, Income, Profile


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ["description", "amount", "date", "category", "frequency", "installment_total", "user", "imported"]
    list_filter = ["category", "frequency", "imported"]
    search_fields = ["description", "original_description"]


@admin.register(Income)
class IncomeAdmin(admin.ModelAdmin):
    list_display = ["description", "amount", "date", "category", "frequency", "user"]
    list_filter = ["category", "frequency"]
    search_fields = ["description"]


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "language", "currency"]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["__str__", "kind", "color", "system_key", "user"]
    list_filter = ["kind"]
