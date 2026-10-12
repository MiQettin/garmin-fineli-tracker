# Moving the Database to a Server

Last updated: 2026-10-12

How to give a server its own copy of the database, keep developing on this machine in Git, and later ship code changes to the server without touching its data.

## The model

- **Code** travels through Git: develop here, `git pull` on the server.
- **Data** lives in one SQLite file per machine. After the first copy, the server's database is the real one and your dev database is just a dev copy. The two diverge from then on.
- **Schema changes** travel as Django migrations in Git. Running `python manage.py migrate` on the server applies them to the server's data in place.

Don't copy the dev database over the server's database by accident after the first move - that would overwrite everything logged on the server since. If you want to do it on purpose (for example after fixing data on the dev machine), follow [Replacing the server database with a fresh copy](#replacing-the-server-database-with-a-fresh-copy).

## 1. Snapshot the database (dev machine)

```bash
cd backend
python manage.py migrate          # snapshot must match the committed code
python manage.py backup_db --output healthmanager-server.sqlite3
```

`backup_db` uses SQLite's online backup API, so it is safe even while `runserver` is running. It refuses to run with unapplied migrations, and it checks the snapshot's integrity. Without `--output` it writes `backend/backups/healthmanager-<timestamp>.sqlite3` (gitignored).

The snapshot contains everything: users and passwords (hashed), food diary, custom foods and recipes, the imported Fineli database, and all Garmin and health data. Copy it only over a channel you trust (e.g. `scp`, a USB stick).

## 2. First setup on the server

The server needs **Python 3.12 or newer**. Check it first:

```bash
python3 --version
```

Raspberry Pi OS / Debian 13 "Trixie" ships Python 3.13 and works. Debian 12 "Bookworm" ships 3.11, which is too old - upgrade the OS first. On Debian-based systems the venv module is a separate package: `sudo apt install python3-venv git`.

```bash
git clone https://github.com/MiQettin/garmin-fineli-tracker.git healthmanager
cd healthmanager
python3 -m venv venv
source venv/bin/activate
python --version                    # must show 3.12 or newer
pip install -r requirements.txt

sudo mkdir -p /srv/healthmanager
# copy healthmanager-server.sqlite3 to /srv/healthmanager/db.sqlite3
```

Create `.env` in the repo root (start from `.env.example`):

```ini
DATABASE_PATH=/srv/healthmanager/db.sqlite3
SECRET_KEY=<new long random string>
DEBUG=False
ALLOWED_HOSTS=<server hostname or IP>
GARMIN_EMAIL=...
GARMIN_PASSWD=...
```

Generate a secret key with:
```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Then:

```bash
cd backend
python manage.py migrate            # no-op on a fresh snapshot; safe to run
python manage.py collectstatic      # writes backend/staticfiles/
python manage.py check --deploy
```

`check --deploy` will still warn about four HTTPS settings (`SECURE_HSTS_SECONDS`, `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`). Leave them off while the server runs on plain HTTP in your home network - turning them on without HTTPS breaks login.

### Running the app

The app runs with gunicorn (installed from `requirements.txt` on Linux only). Static files are served by whitenoise from `backend/staticfiles/`, so no nginx is needed.

```bash
cd healthmanager/backend
../venv/bin/gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 360
```

`--timeout 360` gives the Garmin sync (up to 5 minutes, plus the CSV import) time to finish. Then open `http://<server-ip>:8000/`.

To start it automatically at boot, create `/etc/systemd/system/healthmanager.service` (replace `<user>` and paths):

```ini
[Unit]
Description=HealthManager
After=network.target

[Service]
User=<user>
WorkingDirectory=/home/<user>/healthmanager/backend
ExecStart=/home/<user>/healthmanager/venv/bin/gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 360
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now healthmanager
```

The database directory must be writable by that user (SQLite writes a journal file next to the database): `sudo chown <user> /srv/healthmanager`.

Keeping the database outside the checkout (`DATABASE_PATH`) means `git pull`, `git clean` or re-cloning can never touch it.

`.env` and `.garmintokens/` are not in Git. The server needs its own: the first Garmin sync logs in with the credentials and saves new tokens.

## 3. Shipping code changes later

On the server:

```bash
cd healthmanager/backend
python manage.py backup_db          # snapshot before every update
git pull
pip install -r ../requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart healthmanager
```

If a migration goes wrong, stop the app and copy the snapshot back over `DATABASE_PATH`.

Migrations that need to reach the server, newest last: `core/0006` (Garmin calorie adjustment), `core/0007` (custom fitness goal), `nutrition/0018` (Fineli portion sizes), `nutrition/0019` (package size, per-100 ml basis), `nutrition/0020` (product/recipe kind, servings, finished weight). `migrate` applies whichever are missing; `python manage.py showmigrations core nutrition` lists them.

After a release that changes the Fineli import (as on 2026-10-11), also run `python manage.py import_fineli_csv --data-folder <Fineli folder> --update-existing`. The Fineli folder is not in Git; copy it to the server.

## Replacing the server database with a fresh copy

Do this only on purpose - for example when the dev machine's copy has been cleaned up and the server has nothing newer that you want to keep. Anything logged on the server after the copy was taken is lost.

On the **dev machine**:

```bash
cd backend
python manage.py migrate                                   # the copy must match the code the server will run
python manage.py backup_db --output healthmanager-server.sqlite3
```

The code on the server must be the same version (or newer) as on the dev machine - push it first, so the server can `git pull` it.

On the **server**:

```bash
sudo systemctl stop healthmanager
cd healthmanager/backend
python manage.py backup_db --output ~/server-before-replace.sqlite3   # keep the old one, just in case
git pull
pip install -r ../requirements.txt
# copy healthmanager-server.sqlite3 over the DATABASE_PATH file, e.g. /srv/healthmanager/db.sqlite3
rm -f /srv/healthmanager/db.sqlite3-journal /srv/healthmanager/db.sqlite3-wal
python manage.py migrate                                   # no-op when the copy is current
python manage.py check
sudo systemctl start healthmanager
```

Then run the read-only [health checks](database_guide.md#health-checks) against the server file and open the app. The file must stay writable by the service user (`sudo chown <user> /srv/healthmanager/db.sqlite3`).

## Time zone

`TIME_ZONE` is `Europe/Helsinki` (since 2026-10-12): "today" in the diary and on the dashboard follows Finnish time on any server, whatever the server's own clock zone is. Datetimes are still stored in UTC.

## Rules while developing

These keep the server's database upgradeable with a plain `migrate`:

- Every model change gets a migration (`python manage.py makemigrations`), committed with the code that needs it.
- Never edit or delete a migration that has already run on the server. Fix mistakes with a new migration.
- Never squash or reset migrations, and never run `migrate <app> zero` against server data.
- A migration that needs to change existing data (not just the schema) must do it inside the migration (`RunPython`). Don't plan on editing the server's database by hand.
- Before pushing, `python manage.py makemigrations --check --dry-run` should print "No changes detected".

## Home network only

This setup is meant for a home network. Do not expose the app to the internet (port forwarding) without first adding HTTPS - e.g. a reverse proxy such as Caddy or nginx with a certificate, or a VPN such as Tailscale - and then enabling the four HTTPS settings above.
