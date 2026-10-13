from django.utils.translation import gettext


def activity_type_label(code):
    """An Activity.activity_type in the app language. Translated here rather
    than in the model choices, which would need a (no-op) migration."""
    return {
        "running": gettext("Running"),
        "walking": gettext("Walking"),
        "cycling": gettext("Cycling"),
        "strength": gettext("Strength training"),
        "dancing": gettext("Dancing"),
        "other": gettext("Other"),
    }.get(code, code)


def step_type_label(code):
    """An ActivityLap.step_type in the app language."""
    return {
        "warmup": gettext("Warm-up"),
        "run": gettext("Run"),
        "cooldown": gettext("Cool-down"),
        "interval": gettext("Interval"),
        "recovery": gettext("Recovery"),
        "rest": gettext("Rest"),
        "other": gettext("Other"),
    }.get(code, code)
