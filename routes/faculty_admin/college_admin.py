# routes/collegeadmin.py
from flask import Blueprint, request, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from mongoengine.errors import DoesNotExist
from utils.jwt import create_access_token, verify_access_token
from utils.response import response
from models.college import CollegeAdmin, College

collegeadmin_bp = Blueprint("collegeadmin", __name__, url_prefix="/collegeadmin")


def token_required(f):
    """Decorator to protect routes using Authorization: Bearer <token>"""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", None)
        if not auth_header or not auth_header.startswith("Bearer "):
            return response(False, "Authorization header missing or malformed"), 401

        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = verify_access_token(token)
        except ValueError as e:
            return response(False, str(e)), 401

        # attach payload to request context for handler use
        request.token_payload = payload
        return f(*args, **kwargs)

    return decorated


@collegeadmin_bp.route("/login", methods=["POST"])
def login():
    """
    POST /collegeadmin/login
    body: { "email": "...", "password": "..." }
    Returns: { token: "<jwt>", admin: {...}, college: {...}, first_time_login: bool }
    """
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    print(password)

    if not email or not password:
        return response(False, "email and password are required"), 400

    try:
        admin = CollegeAdmin.objects.get(email=email)
    except DoesNotExist:
        return response(False, "invalid credentials"), 401

    # Password verification
    password_ok = False
    try:
        if check_password_hash(admin.password, password):
            password_ok = True
    except Exception:
        password_ok = False

    if not password_ok:
        if admin.password and admin.password == password:
            admin.password = generate_password_hash(password)
            admin.save()
            password_ok = True

    if not password_ok:
        return response(False, "invalid credentials"), 401

    college = College.objects(admins=admin).first()
    if not college:
        return response(False, "no college associated with this admin"), 400

    first_time = bool(getattr(admin, "is_first_login", False))
    if first_time:
        admin.is_first_login = False
        admin.save()

    payload = {
        "admin_id": str(admin.id),
        "college_id": str(college.id),
        "role": "college_admin"
    }

    token = create_access_token(payload)

    data = {
        "token": token,
        "admin": {
            "id": str(admin.id),
            "name": getattr(admin, "name", None),
            "email": admin.email,
            "phone": getattr(admin, "phone", None),
            "is_first_login": first_time
        },
        "college": {
            "id": str(college.id),
            "name": college.name,
            "college_id": college.college_id
        }
    }

    return response(True, "login successful", data), 200


@collegeadmin_bp.route("/me", methods=["GET"])
@token_required
def me():
    """
    GET /collegeadmin/me
    Protected route that returns token payload
    """
    payload = getattr(request, "token_payload", {})
    return response(True, "token payload fetched successfully", payload), 200


@collegeadmin_bp.route("/change-password", methods=["POST"])
@token_required
def change_password():
    """
    POST /collegeadmin/change-password
    Protected route. Authorization: Bearer <access-token>
    body: { "new_password": "..." }
    """
    data = request.get_json() or {}
    new_password = data.get("new_password", "")

    if not new_password:
        return response(False, "new_password is required"), 400

    payload = getattr(request, "token_payload", {})
    admin_id = payload.get("admin_id")
    if not admin_id:
        return response(False, "token missing admin_id"), 401

    try:
        admin = CollegeAdmin.objects.get(id=admin_id)
    except DoesNotExist:
        return response(False, "admin not found"), 404

    admin.password = generate_password_hash(new_password)
    admin.is_first_login = False
    admin.save()

    return response(True, "password changed successfully"), 200
