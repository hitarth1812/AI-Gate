import base64


def aws_key():
    return base64.urlsafe_b64decode("@@AWSB64@@").decode()
