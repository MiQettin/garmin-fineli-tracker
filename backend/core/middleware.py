from django.utils import translation

from nutrition.food_language import reset_food_language, set_food_language

from .models import UserSettings


class UserLanguageMiddleware:
    """Activate the language chosen in Settings for every request - the whole
    UI, Django's own texts (form errors, dates) and number formatting follow
    it. Settings are one shared row, so the language applies to everyone."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user_settings = UserSettings.get_settings()
        language = user_settings.language
        translation.activate(language)
        request.LANGUAGE_CODE = language
        # Fineli food names: Finnish in a Finnish app, the user's choice in English.
        token = set_food_language(user_settings.effective_food_language())
        try:
            response = self.get_response(request)
        finally:
            reset_food_language(token)
        response.headers.setdefault("Content-Language", language)
        return response
