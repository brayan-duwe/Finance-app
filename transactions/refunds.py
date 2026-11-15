"""Match refunds to the charges they cancel.

A statement can have a charge and a refund for the same amount, e.g.
"Anuidade Diferenciada  50,00" and "Estorno Tarifa  -50,00". Together they
cost nothing, so neither should count as an expense.

Bill payments ("Inclusão de Pagamento", "Payment - thank you") are not refunds:
they pay the card bill, so they never cancel a charge.
"""

import re
import unicodedata

BILL_PAYMENT = re.compile(
    r"\b(pagamento|pagto|pgto|payment|pago de (la )?tarjeta|pago recibido|autopay)\b"
)


def _normalize(text):
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def is_bill_payment(description):
    return bool(BILL_PAYMENT.search(_normalize(description)))


def _words(text):
    return {w for w in re.findall(r"[a-z]{3,}", _normalize(text))}


def pair_refunds(rows):
    """Mark refund/charge pairs in the review rows (dicts with date, description, amount, category).

    Each refund is matched to one charge with exactly the same amount. If several
    charges match, the one with the same category, a shared word, or the closest
    date wins. Both rows are unchecked and get a note pointing to each other.
    """
    for row in rows:
        row.setdefault("is_payment", row["amount"] < 0 and is_bill_payment(row["description"]))

    paired = set()
    for refund_index, refund in enumerate(rows):
        if refund["amount"] >= 0 or refund["is_payment"]:
            continue
        candidates = [
            i for i, row in enumerate(rows)
            if i not in paired and row["amount"] == -refund["amount"]
        ]
        if not candidates:
            continue

        refund_words = _words(refund["description"])

        def closeness(i):
            charge = rows[i]
            return (
                charge["category"] != refund["category"],  # same category first
                not (refund_words & _words(charge["description"])),  # then a shared word
                abs((charge["date"] - refund["date"]).days),  # then the closest date
            )

        charge_index = min(candidates, key=closeness)
        paired.add(charge_index)
        charge = rows[charge_index]
        charge["include"] = False
        charge["refunded_by"] = refund["description"]
        refund["include"] = False
        refund["refund_for"] = charge["description"]
    return rows
