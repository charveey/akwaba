"""Export CSV (lot 11d) : séparateur « ; », UTF-8 avec BOM (Excel en français), téléchargement.

Neutralisation des formules (injection CSV) : toute cellule TEXTE qui, après d'éventuels espaces
ou caractères de contrôle initiaux, commence par = + - @ est préfixée d'une apostrophe. Les nombres
restent numériques (y compris négatifs) ; ils ne sont jamais interprétés comme des formules.
"""
import csv
import io
import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime

from fastapi import Response

BOM = "﻿"
_FORMULA = re.compile(r"^[\s\x00-\x1f\x7f]*[=+\-@]")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def neutralize(value) -> str | int | float:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "oui" if value else "non"
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    if _FORMULA.match(text) or text[:1] in ("\t", "\r"):
        return "'" + text
    return text


def render(header: Sequence[str], rows: Iterable[Sequence]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([neutralize(h) for h in header])
    for row in rows:
        writer.writerow([neutralize(v) for v in row])
    return BOM + buf.getvalue()


def csv_response(filename: str, header: Sequence[str], rows: Iterable[Sequence]) -> Response:
    name = _SAFE_NAME.sub("_", filename)
    if not name.endswith(".csv"):
        name += ".csv"
    return Response(
        content=render(header, rows).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
