from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("settings/", views.user_settings, name="user_settings"),
    path("settings/categories/new/", views.category_form, name="category_new"),
    path("settings/categories/<int:pk>/", views.category_form, name="category_edit"),
    path("settings/categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
    path("expenses/new/", views.add_expense, name="add_expense"),
    path("income/new/", views.add_income, name="add_income"),
    path("<str:kind>/<int:pk>/", views.entry_detail, name="entry_detail"),
    path("<str:kind>/<int:pk>/delete/", views.delete_entry, name="delete_entry"),
    path("import/", views.import_statement, name="import_statement"),
    path("import/review/", views.import_review, name="import_review"),
    path("import/cancel/", views.import_cancel, name="import_cancel"),
]
