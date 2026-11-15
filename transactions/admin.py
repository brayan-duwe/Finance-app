from django.contrib import admin

from .models import Expense, Income, Profile


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ["description", "amount", "date", "category", "frequency", "installment_total", "user", "imported"]
    list_filter = ["category", "frequency", "imported"]
    search_fields = ["description", "original_description"]


@admin.register(Income)
class IncomeAdmin(admin.ModelAdmin):
    list_display = ["description", "amount", "date", "frequency", "user"]
    list_filter = ["frequency"]
    search_fields = ["description"]


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "language", "currency"]
