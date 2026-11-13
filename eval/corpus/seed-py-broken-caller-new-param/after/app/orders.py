from app.util import parse_amount


def compute_total(items, tax, currency):
    total = sum(items)
    return total + total * tax


def load_amounts(lines):
    return [parse_amount(x) for x in lines]
