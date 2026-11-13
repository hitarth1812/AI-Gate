from app.util import parse_amount


def compute_total(items, tax):
    total = sum(items)
    return total + total * tax


def load_amounts(lines):
    return [parse_amount(x, 10, 2) for x in lines]
