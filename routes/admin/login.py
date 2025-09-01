# routes/login.py
from flask import Blueprint, request
from werkzeug.security import check_password_hash
from models.admin import Admin
from utils.jwt import create_access_token
from utils.response import response

login_bp = Blueprint("login", __name__)


@login_bp.route("/login", methods=["POST"])
def login():
    """
    Login route for admin.
    Expects JSON: { "email": "test@example.com", "password": "secret" }
    """
    data = request.get_json()
    if not data or "email" not in data or "password" not in data:
        return response(False, "Email and password are required")

    admin = Admin.objects(email=data["email"]).first()
    if not admin:
        return response(False, "Invalid email or password")

    if not admin.is_active:
        return response(False, "Admin account is not active. Please contact support.")

    if not check_password_hash(admin.password, data["password"]):
        return response(False, "Invalid email or password")

    token = create_access_token({"id": str(admin.id), "email": admin.email})

    return response(
        True,
        "Login successful",
        {
            "access_token": token,
            "admin": admin.to_json()
        }
    )
