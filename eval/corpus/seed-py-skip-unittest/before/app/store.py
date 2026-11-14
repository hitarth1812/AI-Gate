def load(path):
    try:
        with open(path) as fh:
            return fh.read()
    except FileNotFoundError:
        return None
    except PermissionError:
        raise
