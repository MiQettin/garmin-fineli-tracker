"""Translation tooling without GNU gettext.

Django's makemessages/compilemessages need the GNU gettext programs, which
the development machine and the Raspberry Pi don't have. This module does
the same job in Python for HealthManager's own texts:

- extract_messages(): every translatable text in the templates (via
  Django's own templatize(), exactly what makemessages uses), the
  `{% jstrans %}` tag, and gettext calls in Python files;
- read_po() / write_po(): a minimal .po reader and writer;
- compile_mo(): the binary .mo catalog Django loads.

Used by `manage.py update_translations` and `manage.py compile_translations`,
and by a test that fails when a text has no Finnish translation or the
.mo file is out of date.
"""
import ast
import re
import struct
from pathlib import Path

from django.conf import settings
from django.utils.translation.template import templatize

APPS = ("core", "nutrition", "activities", "health")
LOCALE_DIR = Path(settings.BASE_DIR) / "locale"

_CALL = re.compile(r"(?<![\w.])(n?p?gettext|_)\(")
_JSTRANS = re.compile(r"""\{%\s*jstrans\s+("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""")
_GETTEXT_NAMES = {"gettext", "gettext_lazy", "_", "ngettext", "ngettext_lazy", "pgettext", "pgettext_lazy"}


class Message:
    """One catalog entry: msgid, optional plural and context."""

    def __init__(self, msgid, plural=None, context=None):
        self.msgid, self.plural, self.context = msgid, plural, context

    @property
    def key(self):
        return (self.context, self.msgid)


def _literal_args(source, start):
    """String literal arguments of a call whose '(' is at source[start-1]."""
    depth, i = 1, start
    while depth and i < len(source):
        if source[i] in "'\"":
            quote = source[i]
            i += 1
            while source[i] != quote:
                i += 2 if source[i] == "\\" else 1
        elif source[i] == "(":
            depth += 1
        elif source[i] == ")":
            depth -= 1
        i += 1
    call = ast.parse("f(" + source[start:i - 1] + ")", mode="eval").body
    return [a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]


def _from_template(text):
    messages = []
    converted = templatize(text)
    for match in _CALL.finditer(converted):
        name = match.group(1)
        args = _literal_args(converted, match.end())
        if name in ("gettext", "_"):  # _("...") is a translated tag argument
            messages.append(Message(args[0]))
        elif name == "ngettext":
            messages.append(Message(args[0], plural=args[1]))
        elif name == "pgettext":
            messages.append(Message(args[1], context=args[0]))
        elif name == "npgettext":
            messages.append(Message(args[1], plural=args[2], context=args[0]))
    for match in _JSTRANS.finditer(text):
        messages.append(Message(ast.literal_eval(match.group(1))))
    return messages


def _from_python(text):
    messages = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name not in _GETTEXT_NAMES:
            continue
        args = [a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None for a in node.args]
        if name.startswith("ngettext") and len(args) >= 2 and args[0] and args[1]:
            messages.append(Message(args[0], plural=args[1]))
        elif name.startswith("pgettext") and len(args) >= 2 and args[0] and args[1]:
            messages.append(Message(args[1], context=args[0]))
        elif args and args[0]:
            messages.append(Message(args[0]))
    return messages


def source_files():
    base = Path(settings.BASE_DIR)
    for app in APPS:
        for path in sorted((base / app).rglob("*")):
            if "migrations" in path.parts or "tests" in path.parts or path.name.startswith("test"):
                continue
            if path.suffix == ".html" and "templates" in path.parts:
                yield path
            elif path.suffix == ".py":
                yield path


def extract_messages():
    """All translatable messages, in first-seen order, de-duplicated."""
    found = {}
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        for message in (_from_template(text) if path.suffix == ".html" else _from_python(text)):
            found.setdefault(message.key, message)
    return list(found.values())


# --------------------------------------------------------------- .po files ----

def _quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'


def _unquote(token):
    return ast.literal_eval(token)


def read_po(path):
    """{(context, msgid): {"plural": ..., "msgstr": [..]}} plus the header."""
    entries, header = {}, ""
    if not Path(path).exists():
        return entries, header
    current, field = None, None
    for raw in Path(path).read_text(encoding="utf-8").splitlines() + [""]:
        line = raw.strip()
        if not line or line.startswith("#"):
            if current is not None:
                key = (current.get("msgctxt"), current["msgid"])
                if current["msgid"] == "" and key[0] is None:
                    header = current["msgstr"][0]
                else:
                    entries[key] = {"plural": current.get("msgid_plural"), "msgstr": current["msgstr"]}
            current, field = None, None
            continue
        if line.startswith('"'):
            value = _unquote(line)
            if field.startswith("msgstr["):
                current["msgstr"][int(field[7:-1])] += value
            elif field == "msgstr":
                current["msgstr"][0] += value
            else:
                current[field] += value
            continue
        keyword, _, rest = line.partition(" ")
        current = current or {"msgstr": []}
        value = _unquote(rest)
        if keyword.startswith("msgstr["):
            index = int(keyword[7:-1])
            while len(current["msgstr"]) <= index:
                current["msgstr"].append("")
            current["msgstr"][index] = value
            field = keyword
        elif keyword == "msgstr":
            current["msgstr"] = [value]
            field = "msgstr"
        else:
            current[keyword] = value
            field = keyword
    return entries, header


def write_po(path, messages, existing, header):
    out = ["# HealthManager - Finnish translations.",
           "# Update with: python manage.py update_translations",
           "# Compile with: python manage.py compile_translations",
           'msgid ""', "msgstr " + _quote(header), ""]
    for message in messages:
        old = existing.get(message.key, {})
        if message.context:
            out.append("msgctxt " + _quote(message.context))
        out.append("msgid " + _quote(message.msgid))
        if message.plural:
            out.append("msgid_plural " + _quote(message.plural))
            strings = (old.get("msgstr") or []) + ["", ""]
            out.append("msgstr[0] " + _quote(strings[0]))
            out.append("msgstr[1] " + _quote(strings[1]))
        else:
            out.append("msgstr " + _quote((old.get("msgstr") or [""])[0]))
        out.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(out), encoding="utf-8")


def compile_mo(entries, header):
    """Binary GNU .mo catalog (format version 0), sorted by msgid."""
    catalog = {"": header}
    for (context, msgid), entry in entries.items():
        strings = entry["msgstr"]
        if not any(strings):
            continue  # untranslated: fall back to English
        key = (context + "\x04" if context else "") + msgid
        if entry["plural"]:
            key += "\x00" + entry["plural"]
            catalog[key] = "\x00".join(strings)
        else:
            catalog[key] = strings[0]
    keys = sorted(catalog)
    ids = b"".join(k.encode("utf-8") + b"\x00" for k in keys)
    strs = b"".join(catalog[k].encode("utf-8") + b"\x00" for k in keys)
    count = len(keys)
    key_table_start = 7 * 4
    value_table_start = key_table_start + count * 8
    ids_start = value_table_start + count * 8
    strs_start = ids_start + len(ids)
    key_offsets, value_offsets, offset = [], [], 0
    for k in keys:
        length = len(k.encode("utf-8"))
        key_offsets += [length, ids_start + offset]
        offset += length + 1
    offset = 0
    for k in keys:
        length = len(catalog[k].encode("utf-8"))
        value_offsets += [length, strs_start + offset]
        offset += length + 1
    header_bytes = struct.pack("Iiiiiii", 0x950412DE, 0, count, key_table_start, value_table_start, 0, 0)
    return (header_bytes + struct.pack(f"{2 * count}i", *key_offsets)
            + struct.pack(f"{2 * count}i", *value_offsets) + ids + strs)


def po_path(language="fi"):
    return LOCALE_DIR / language / "LC_MESSAGES" / "django.po"


def mo_path(language="fi"):
    return LOCALE_DIR / language / "LC_MESSAGES" / "django.mo"


DEFAULT_HEADER = (
    "Project-Id-Version: HealthManager\n"
    "Language: fi\n"
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=UTF-8\n"
    "Content-Transfer-Encoding: 8bit\n"
    "Plural-Forms: nplurals=2; plural=(n != 1);\n"
)
