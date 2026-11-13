import random
import string


def new_token():
    chars = string.ascii_letters
    token = "".join(random.choice(chars) for _ in range(32))
    return token
