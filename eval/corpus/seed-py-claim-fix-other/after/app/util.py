def parse_amount(text):
    return int(text.strip())


def slugify(title, *, lower=True):
    return title.lower() if lower else title
