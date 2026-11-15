from django.contrib import admin
from django.urls import include, path

from transactions import views as transaction_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),  # language switcher (set_language)
    path("accounts/signup/", transaction_views.signup, name="signup"),
    path("accounts/", include("django.contrib.auth.urls")),  # login, logout, password reset
    path("", include("transactions.urls")),
]
