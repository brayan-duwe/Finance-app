from django.conf import settings


def preferences(request):
    """Make the user's currency and the language list available in every template."""
    profile = getattr(request, "profile", None)
    return {
        "currency": profile.currency if profile else settings.DEFAULT_CURRENCY,
        "available_languages": settings.LANGUAGES,
    }
