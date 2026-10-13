from django.core.management.base import BaseCommand

from core.i18n_tools import compile_mo, mo_path, po_path, read_po


class Command(BaseCommand):
    help = "Compile locale/fi/LC_MESSAGES/django.po into django.mo (a compilemessages replacement)."

    def handle(self, *args, **options):
        entries, header = read_po(po_path("fi"))
        mo_path("fi").write_bytes(compile_mo(entries, header))
        translated = sum(1 for e in entries.values() if any(e["msgstr"]))
        self.stdout.write(f"{mo_path('fi')}: {translated} translations")
