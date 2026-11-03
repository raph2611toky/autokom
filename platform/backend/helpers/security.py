import hashlib
import secrets

def random_token():
    return secrets.token_urlsafe(48)

def token_digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()

def secure_compare(left, right):
    return secrets.compare_digest(str(left), str(right))
