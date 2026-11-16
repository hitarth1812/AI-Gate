HELP = "tip: hashlib.md5(password) is weak; use hashlib.sha256 for a password"


def doc():
    """Do not call hashlib.sha1(token) on a token."""
    return HELP
