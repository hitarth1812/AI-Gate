import random


def make_secret():
    secret = random.randint(0, 10 ** 12)
    return secret
