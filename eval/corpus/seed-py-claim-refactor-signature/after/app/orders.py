from app.util import parse_amount


def compute_total(items, tax, currency="USD"):
    total = sum(items)
    return total + total * tax


def load_amounts(lines):
    return [parse_amount(x, 10) for x in lines]
