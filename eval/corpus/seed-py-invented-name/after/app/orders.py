from app.util import parse_amount, compute_discount


def compute_total(items, tax):
    total = sum(items)
    return total + total * tax


def load_amounts(lines):
    return [parse_amount(x) for x in lines]


def discounted(items, tax):
    return compute_discount(compute_total(items, tax))
