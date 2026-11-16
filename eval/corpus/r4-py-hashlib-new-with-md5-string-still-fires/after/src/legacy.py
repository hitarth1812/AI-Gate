import hashlib


def store(password):
    return hashlib.new("md5", password.encode()).hexdigest()
