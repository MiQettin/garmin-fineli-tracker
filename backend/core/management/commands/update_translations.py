from django.core.management.base import BaseCommand

from core.i18n_tools import DEFAULT_HEADER, extract_messages, po_path, read_po, write_po


class Command(BaseCommand):
    help = (
        "Collect the translatable texts from templates and Python code into "
        "locale/fi/LC_MESSAGES/django.po, keeping existing translations "
        "(a makemessages replacement that needs no GNU gettext)."
    )

    def handle(self, *args, **options):
        path = po_path("fi")
        existing, header = read_po(path)
        messages = extract_messages()
        write_po(path, messages, existing, header or DEFAULT_HEADER)
        untranslated = [m.msgid for m in messages if not any(existing.get(m.key, {}).get("msgstr") or [])]
        removed = len(set(existing) - {m.key for m in messages})
        self.stdout.write(f"{path}: {len(messages)} texts, {len(untranslated)} untranslated, {removed} no longer used")
        for msgid in untranslated[:50]:
            self.stdout.write(f"  untranslated: {msgid}")
