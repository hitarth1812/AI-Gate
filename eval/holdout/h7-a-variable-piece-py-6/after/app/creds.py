import os

parts = ["@@AKIA@@"]
parts.append(os.environ["AWS_TAIL"])
parts.append("@@AKT2@@")
AWS_KEY = "".join(parts)
