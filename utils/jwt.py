# utils/jwt.py
import jwt
from datetime import datetime, timedelta
from flask import current_app


def create_access_token(data: dict, expires_delta: timedelta = timedelta(hours=1)) -> str:
    """
    Create a JWT access token.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    secret_key = current_app.config["SECRET_KEY"]
    algorithm = "HS256"

    return jwt.encode(to_encode, secret_key, algorithm=algorithm)


def verify_access_token(token: str) -> dict:
    """
    Verify a JWT token and return decoded data.
    """
    secret_key = current_app.config["SECRET_KEY"]
    algorithm = "HS256"
    try:
        payload = jwt.decode(token, secret_key, algorithms=[algorithm])
        return payload
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token")
 