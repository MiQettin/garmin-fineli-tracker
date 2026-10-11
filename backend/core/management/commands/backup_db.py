"""
Write a consistent, self-contained snapshot of the SQLite database.

Uses SQLite's online backup API, so it is safe to run while the server is
running (unlike copying db.sqlite3 by hand mid-write). The snapshot is a
single file that can be moved to another machine and used directly as
DATABASE_PATH there.

Usage:
    python manage.py backup_db
    python manage.py backup_db --output D:/backups/healthmanager.sqlite3
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Write a consistent snapshot of the SQLite database to a single file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Destination file (default: backups/healthmanager-<timestamp>.sqlite3 next to the database)",
        )

    def handle(self, *args, **options):
        db_path = Path(settings.DATABASES["default"]["NAME"])
        if not db_path.exists():
            raise CommandError(f"Database not found: {db_path}")

        # A snapshot with unapplied migrations would not match the code that
        # gets deployed next to it.
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            names = ", ".join(f"{m.app_label}.{m.name}" for m, _ in pending)
            raise CommandError(f"Unapplied migrations - run `python manage.py migrate` first: {names}")

        if options["output"]:
            out_path = Path(options["output"])
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            out_path = db_path.parent / "backups" / f"healthmanager-{stamp}.sqlite3"
        if out_path.exists():
            raise CommandError(f"Refusing to overwrite existing file: {out_path}")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(out_path)
        try:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
            src.close()

        if result != "ok":
            raise CommandError(f"Snapshot failed integrity check ({result}): {out_path}")

        size_mb = out_path.stat().st_size / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(f"Snapshot written: {out_path} ({size_mb:.1f} MB, integrity ok)"))
