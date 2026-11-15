from django.conf import settings
from django.utils import translation

from .models import get_profile


class UserLanguageMiddleware:
    """Show the site in the user's language.

    Logged-in users: the language saved in their Settings.
    Visitors: the language they picked on the login page (a cookie), or English.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = None
        request.profile = None
        if request.user.is_authenticated:
            request.profile = get_profile(request.user)
            language = request.profile.language
        if not language:
            language = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        if language not in dict(settings.LANGUAGES):
            language = settings.LANGUAGE_CODE

        translation.activate(language)
        request.LANGUAGE_CODE = language
        response = self.get_response(request)
        response.headers.setdefault("Content-Language", language)
        return response
