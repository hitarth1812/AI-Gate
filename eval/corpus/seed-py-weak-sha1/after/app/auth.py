import hashlib


def sign_token(token):
    return hashlib.sha1(token.encode()).hexdigest()
