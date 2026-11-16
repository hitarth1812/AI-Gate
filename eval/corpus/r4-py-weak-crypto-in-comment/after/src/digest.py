import hashlib

# hashlib.md5(password.encode()) was removed in 2023, see the security notes


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
