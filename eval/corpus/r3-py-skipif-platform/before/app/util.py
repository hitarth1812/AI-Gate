def parse_amount(text):
    return int(text)


def slugify(title, *, lower=True):
    return title.lower() if lower else title
