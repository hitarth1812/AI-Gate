from app.orders import compute_total
from app.util import slugify


def summary(items):
    return compute_total(items)


def headline(title):
    return slugify(title, lower=False)
