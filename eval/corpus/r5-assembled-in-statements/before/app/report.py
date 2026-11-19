from app.orders import compute_total
from app.util import slugify


def summary(items):
    return compute_total(items, 0.1)


def headline(title):
    return slugify(title, lower=False)
