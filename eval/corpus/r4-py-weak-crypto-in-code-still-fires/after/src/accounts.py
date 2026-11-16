import hashlib


def store(password):
    return hashlib.md5(password.encode()).hexdigest()
