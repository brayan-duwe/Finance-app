"""Read credit card statements (CSV or PDF) into a list of rows.

Banks format statements differently, so this module uses heuristics:
- CSV: find the header row (date / description / amount columns, in English,
  Portuguese or Spanish). Without a header, guess the columns per row.
  When there are several amount columns (e.g. US$ and R$), use the one in the
  user's currency, and ignore exchange-rate columns.
- PDF: read the text line by line and keep lines that start with a date and
  end with an amount.

Charges come out as positive amounts, and payments/refunds as negative ones.
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.utils.translation import gettext as _

from .currencies import CURRENCIES


class StatementError(Exception):
    """The file can't be read as a statement. The message is shown to the user."""


@dataclass
class Row:
    date: date
    description: str  # cleaned up for display
    amount: Decimal  # positive = charge, negative = payment or refund
    original: str = ""  # exactly as it appears on the statement
    bank_category: str = ""
    installment_number: int | None = None  # 3 in "3/4"
    installment_total: int | None = None  # 4 in "3/4"

    def __post_init__(self):
        self.original = self.original or self.description
        self.description = clean_description(self.description)


MONTHS = {
    "jan": 1, "feb": 2, "fev": 2, "ene": 1, "mar": 3, "apr": 4, "abr": 4, "may": 5, "mai": 5,
    "jun": 6, "jul": 7, "aug": 8, "ago": 8, "sep": 9, "set": 9, "oct": 10, "out": 10,
    "nov": 11, "dec": 12, "dez": 12, "dic": 12,
}

# Full-string date formats.
NUMERIC_DATE = re.compile(r"(\d{1,4})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?")
DAY_MONTH_NAME = re.compile(r"(\d{1,2})[ /\-]?([A-Za-z]{3})[A-Za-z]*\.?(?:[ /\-,]*(\d{4}))?")
MONTH_NAME_DAY = re.compile(r"([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?")

# A date at the start of a PDF line (any of the formats above).
DATE_TOKEN = (
    r"(?:\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[/.\-]\d{1,2}(?:[/.\-]\d{2,4})?"
    r"|\d{1,2}[ /\-]?[A-Za-z]{3}[A-Za-z]*\.?(?:[ /\-]\d{4})?"
    r"|[A-Za-z]{3}[A-Za-z]*\.?\s\d{1,2}(?:,\s?\d{4})?)"
)
LINE_START = re.compile(rf"^\s*(?P<date>{DATE_TOKEN})\s+(?P<rest>.+)$")
SECOND_DATE = re.compile(rf"^(?P<date>{DATE_TOKEN})\s+")

# An amount with 2 decimals at the end of a PDF line, e.g. "R$ 1.234,56", "-$12.00", "45.90 CR".
AMOUNT_END = re.compile(
    r"(?P<amount>(?:[-−]\s?)?(?:[A-Z]{1,3}\$|\$|€|£)?\s?[-−]?\(?"
    r"\d{1,3}(?:[.,]?\d{3})*[.,]\d{2}\)?(?:\s?-|\s?CR|\s?DR)?)\s*$",
    re.IGNORECASE,
)
# A whole CSV cell that is just an amount.
AMOUNT_CELL = re.compile(r"[-+(]?\s*(?:[A-Z]{1,3}\$|\$|€|£|[A-Z]{3})?\s*[-+(]?\s*[\d.,]+\s*\)?\s*-?")

# "3/4", "03 de 04", "3 of 4". "Única"/"-" means a single payment.
INSTALLMENT = re.compile(r"(\d{1,3})\s*(?:/|de|of|del)\s*(\d{1,3})", re.IGNORECASE)
# Installments written inside a PDF description: "PARC 03/04", "PARCELA 3 DE 4".
INSTALLMENT_IN_TEXT = re.compile(r"\s*\bPARC(?:ELA)?\.?\s*(\d{1,3})\s*(?:/|DE)\s*(\d{1,3})\b", re.IGNORECASE)

HEADER_KEYS = {
    "date": ("date", "data", "fecha", "posted", "transaction date"),
    "description": (
        "description", "descrição", "descricao", "descripción", "descripcion", "title", "merchant",
        "memo", "details", "estabelecimento", "histórico", "historico", "lançamento", "lancamento",
        "concepto", "name",
    ),
    "installment": ("parcela", "parcelas", "installment", "installments", "cuota", "cuotas", "prestação"),
    "category": ("categoria", "category", "categoría"),
    "debit": ("debit", "débito", "debito"),
    "credit": ("credit", "crédito", "credito"),
}
AMOUNT_WORDS = ("amount", "valor", "value", "total", "importe", "monto", "montant")
RATE_WORDS = ("cotação", "cotacao", "cotización", "cotizacion", "exchange", "rate", "tasa", "câmbio", "cambio")


# --- Small parsers ---------------------------------------------------------


def clean_description(text):
    return re.sub(r"\s{2,}", " ", text).strip()


def parse_amount(text):
    """'R$ 1.234,56' -> Decimal('1234.56'), '(12.00)' -> Decimal('-12.00'). None if not an amount."""
    s = text.strip().replace("−", "-").upper()
    negative = False
    if s.endswith("CR"):
        negative, s = True, s[:-2].strip()
    elif s.endswith("DR"):
        s = s[:-2].strip()
    if not s or not AMOUNT_CELL.fullmatch(s):
        return None
    if "(" in s and ")" in s:  # accounting style: (12.00)
        negative = not negative
    if "-" in s:
        negative = not negative

    digits = re.sub(r"[^\d,.]", "", s)
    if not re.search(r"\d", digits):
        return None
    # The last separator is the decimal one: "1.234,56" or "1,234.56".
    if digits.rfind(",") > digits.rfind("."):
        digits = digits.replace(".", "").replace(",", ".")
    else:
        digits = digits.replace(",", "")
    try:
        value = Decimal(digits).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_date(text, day_first=True, today=None):
    """Parse the date formats statements use. Dates without a year get the most recent fitting year."""
    today = today or date.today()
    text = re.sub(r"[T\s]\d{1,2}:\d{2}(:\d{2})?.*$", "", text.strip())  # drop a time part

    if m := NUMERIC_DATE.fullmatch(text):
        a, b, c = m.groups()
        if len(a) == 4:  # 2026-11-14
            if c is None:
                return None
            return _build_date(int(a), int(b), int(c), today)
        if len(a) == 3:
            return None
        first, second = int(a), int(b)
        day, month = (first, second) if day_first else (second, first)
        return _build_date(_full_year(c), month, day, today)

    if m := DAY_MONTH_NAME.fullmatch(text):
        day, name, year = m.groups()
        if month := MONTHS.get(name.lower()):
            return _build_date(_full_year(year), month, int(day), today)

    if m := MONTH_NAME_DAY.fullmatch(text):
        name, day, year = m.groups()
        if month := MONTHS.get(name.lower()):
            return _build_date(_full_year(year), month, int(day), today)

    return None


def parse_installment(text):
    """'3/4' -> (3, 4). None for single payments ('Única', '1/1', '-', '')."""
    m = INSTALLMENT.search(text or "")
    if not m:
        return None
    number, total = int(m.group(1)), int(m.group(2))
    if total < 2 or not 1 <= number <= total:
        return None
    return number, total


def _full_year(text):
    if not text:
        return None
    year = int(text)
    return year + 2000 if year < 100 else year


def _build_date(year, month, day, today):
    try:
        if year is not None:
            return date(year, month, day)
        # No year: a December charge on a January statement belongs to last year.
        guess = date(today.year, month, day)
        if guess > today + timedelta(days=31):
            guess = date(today.year - 1, month, day)
        return guess
    except ValueError:
        return None


# --- CSV -------------------------------------------------------------------


def parse_csv(data, day_first=True, currency="BRL", today=None):
    text = _decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    columns = None
    rows = []
    for cells in csv.reader(io.StringIO(text), dialect):
        if not any(c.strip() for c in cells):
            continue
        if columns is None and (found := _match_header(cells, currency)):
            columns = found
            continue
        if columns:
            row = _row_from_columns(cells, columns, day_first, today)
        else:
            row = _row_by_guess(cells, day_first, today)
        if row and row.amount != 0:
            rows.append(row)
    return _charges_positive(rows)


def _decode(data):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _match_header(cells, currency):
    names = [c.strip().lower() for c in cells]
    columns = {}
    for exact in (True, False):  # exact names first, then partial ("Transaction Date")
        for index, name in enumerate(names):
            if not name or index in columns.values():
                continue
            for key, words in HEADER_KEYS.items():
                if key in columns:
                    continue
                if any(name == w if exact else w in name for w in words):
                    columns[key] = index
                    break

    amount_index = _pick_amount_column(names, set(columns.values()), currency)
    if amount_index is not None:
        columns["amount"] = amount_index
    if "date" in columns and ("amount" in columns or "debit" in columns):
        return columns
    return None


def _pick_amount_column(names, taken, currency):
    """The amount column in the user's currency.

    "Valor (em US$)" and "Valor (em R$)" are both amounts; a BRL user gets the R$ one.
    Exchange-rate columns ("Cotação") are never amounts.
    """
    mine = sorted(CURRENCIES.get(currency, {}).get("hints", ()), key=len, reverse=True)
    others = [h for code, c in CURRENCIES.items() if code != currency for h in c["hints"]]

    best_index, best_score = None, None
    for index, name in enumerate(names):
        if index in taken or not any(w in name for w in AMOUNT_WORDS):
            continue
        if any(w in name for w in RATE_WORDS):
            continue
        score = 0
        remaining = name
        for hint in mine:
            if hint in remaining:
                score = 2
                remaining = remaining.replace(hint, " ")
        if any(h in remaining for h in others):
            score -= 3  # mentions another currency
        if best_score is None or score > best_score:
            best_index, best_score = index, score
    return best_index


def _cell(cells, index):
    if index is None or index >= len(cells):
        return ""
    return cells[index].strip()


def _row_from_columns(cells, columns, day_first, today):
    row_date = parse_date(_cell(cells, columns["date"]), day_first, today)
    if row_date is None:
        return None

    if "amount" in columns:
        amount = parse_amount(_cell(cells, columns["amount"]))
    else:
        debit = parse_amount(_cell(cells, columns.get("debit")))
        credit = parse_amount(_cell(cells, columns.get("credit")))
        if debit is None and credit is None:
            return None
        amount = abs(debit or 0) - abs(credit or 0)
    if amount is None:
        return None

    if "description" in columns:
        description = _cell(cells, columns["description"])
    else:
        used = set(columns.values())
        description = " ".join(c.strip() for i, c in enumerate(cells) if i not in used and c.strip())

    installment = parse_installment(_cell(cells, columns.get("installment")))
    bank_category = _cell(cells, columns.get("category"))
    return Row(
        row_date,
        description or "(no description)",
        amount,
        bank_category="" if bank_category == "-" else bank_category,
        installment_number=installment[0] if installment else None,
        installment_total=installment[1] if installment else None,
    )


def _row_by_guess(cells, day_first, today):
    """No header: the first date-like cell, the last amount-like cell, and the longest text cell."""
    date_index = row_date = None
    for index, cell in enumerate(cells):
        if row_date := parse_date(cell, day_first, today):
            date_index = index
            break
    if row_date is None:
        return None

    amount_index = amount = None
    for index in range(len(cells) - 1, -1, -1):
        if index != date_index and (amount := parse_amount(cells[index])) is not None:
            amount_index = index
            break
    if amount is None:
        return None

    texts = [
        c.strip() for i, c in enumerate(cells)
        if i not in (date_index, amount_index) and re.search(r"[^\W\d_]", c)
    ]
    description = max(texts, key=len) if texts else "(no description)"
    return Row(row_date, description, amount)


def _charges_positive(rows):
    """Some banks export charges as negative numbers. If most rows are negative, flip the signs."""
    negatives = sum(1 for r in rows if r.amount < 0)
    if negatives > len(rows) - negatives:
        for r in rows:
            r.amount = -r.amount
    return rows


# --- PDF -------------------------------------------------------------------


def parse_pdf(data, day_first=True, today=None):
    try:
        import pdfplumber
    except ImportError as exc:
        raise StatementError(_("PDF support needs pdfplumber. Run: pip install pdfplumber")) from exc

    rows = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                for line in (page.extract_text() or "").splitlines():
                    if (row := parse_line(line, day_first, today)) and row.amount != 0:
                        rows.append(row)
    except Exception as exc:
        raise StatementError(
            _("This PDF couldn't be read. If it's password-protected or a scanned image, "
              "try your bank's CSV export instead.")
        ) from exc
    return _charges_positive(rows)


def parse_line(line, day_first=True, today=None):
    """'14/11 UBER *TRIP 23,45' -> Row. None if the line isn't a transaction."""
    m = LINE_START.match(line)
    if not m:
        return None
    row_date = parse_date(m["date"], day_first, today)
    if row_date is None:
        return None

    rest = m["rest"]
    amount_match = AMOUNT_END.search(rest)
    if not amount_match:
        return None
    amount = parse_amount(amount_match["amount"])
    if amount is None:
        return None

    description = rest[: amount_match.start()].strip()
    # Many statements show two dates (transaction + posting). Drop the second one.
    if (second := SECOND_DATE.match(description)) and parse_date(second["date"], day_first, today):
        description = description[second.end():]

    installment = None
    if found := INSTALLMENT_IN_TEXT.search(description):
        installment = parse_installment(f"{found.group(1)}/{found.group(2)}")
        description = description[: found.start()] + description[found.end():]

    description = description.strip(" -–·*")
    if not re.search(r"[^\W\d_]", description):
        return None
    return Row(
        row_date,
        description,
        amount,
        installment_number=installment[0] if installment else None,
        installment_total=installment[1] if installment else None,
    )


# --- Entry point -----------------------------------------------------------


def parse_statement(filename, data, day_first=True, currency="BRL", today=None):
    name = filename.lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        return parse_pdf(data, day_first, today)
    if name.endswith((".csv", ".txt")):
        return parse_csv(data, day_first, currency, today)
    raise StatementError(_("Upload a CSV or PDF file."))
