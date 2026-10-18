"""Weight page: the body weight used to scale nutrition targets (g/kg),
from Garmin weigh-ins and manual entries. Deliberately no weight-trend
features - this is not a weight-loss app."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext
from django.views.decorators.http import require_POST

from .forms import WeightEntryForm
from .models import WeightEntry
from .weight import entry_on, record_manual_weight, sync_settings_weight

HISTORY_ROWS = 100


@login_required
def weight_page_view(request):
    """List and add weight entries."""
    today = timezone.localdate()
    if request.method == "POST":
        form = WeightEntryForm(request.POST)
        if form.is_valid():
            record_manual_weight(form.cleaned_data["date"], form.cleaned_data["weight_kg"])
            messages.success(request, gettext("Weight saved."))
            return redirect("weight_page")
    else:
        form = WeightEntryForm(initial={"date": today})

    entries = WeightEntry.objects.filter(ignored=False)
    current = entry_on(today)
    return render(request, "health/weight_page.html", {
        "form": form,
        "entries": entries[:HISTORY_ROWS],
        "entry_count": entries.count(),
        "history_rows": HISTORY_ROWS,
        "current": current,
    })


@login_required
def weight_edit_view(request, entry_id):
    """Correct an entry. A corrected Garmin entry becomes a manual one; it
    keeps its Garmin id, so a later sync doesn't import the original again."""
    entry = get_object_or_404(WeightEntry, pk=entry_id, ignored=False)
    if request.method == "POST":
        form = WeightEntryForm(request.POST)
        if form.is_valid():
            entry.date = form.cleaned_data["date"]
            entry.weight_kg = form.cleaned_data["weight_kg"]
            entry.source = WeightEntry.SOURCE_MANUAL
            entry.save()
            sync_settings_weight()
            messages.success(request, gettext("Weight saved."))
            return redirect("weight_page")
    else:
        form = WeightEntryForm(initial={"date": entry.date, "weight_kg": entry.weight_kg})
    return render(request, "health/weight_edit.html", {"form": form, "entry": entry})


@login_required
@require_POST
def weight_delete_view(request, entry_id):
    """Remove an entry. Garmin entries are only marked ignored, so the next
    sync doesn't bring them back."""
    entry = get_object_or_404(WeightEntry, pk=entry_id, ignored=False)
    if entry.garmin_sample_id is not None:
        entry.ignored = True
        entry.save(update_fields=["ignored", "updated_at"])
    else:
        entry.delete()
    sync_settings_weight()
    messages.success(request, gettext("Weight entry removed."))
    return redirect("weight_page")
