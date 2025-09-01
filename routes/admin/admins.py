# routes/admin_routes.py
from flask import Blueprint, request
from functools import wraps
from mongoengine.errors import DoesNotExist, ValidationError, NotUniqueError
from werkzeug.security import generate_password_hash

from models.admin import Admin
from utils.jwt import verify_access_token
from utils.response import response

admin_bp = Blueprint('admin_bp', __name__)


# Decorator to check token validity
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return response(False, "Token is missing"), 401
        
        # Remove "Bearer " prefix if present
        if token.startswith("Bearer "):
            token = token[7:]
        
        try:
            payload = verify_access_token(token)
        except ValueError as e:
            return response(False, str(e)), 401
        
        # Attach admin info to request
        request.admin = payload
        return f(*args, **kwargs)
    
    return decorated


# Fetch all admins
@admin_bp.route("/", methods=["GET"])
@token_required
def get_all_admins():
    try:
        admins = Admin.objects()
        admin_list = [admin.to_json() for admin in admins]
        print(admin_list[0]['is_active'])
        return response(True, "Admins fetched successfully", admin_list), 200
    except Exception as e:
        print(e)
        return response(False, f"An error occurred: {str(e)}"), 500


# Update admin password
@admin_bp.route("/<admin_id>/password", methods=["PUT"])
@token_required
def update_password(admin_id):
    try:
        data = request.get_json()
        new_password = data.get("password")
        if not new_password:
            return response(False, "Password is required"), 400

        admin = Admin.objects.get(id=admin_id)
        admin.password = generate_password_hash(new_password)
        admin.save()
        return response(True, "Password updated successfully"), 200

    except DoesNotExist:
        return response(False, "Admin not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


# Update admin permissions
@admin_bp.route("/<admin_id>/permissions", methods=["PUT"])
@token_required
def update_permissions(admin_id):
    try:
        data = request.get_json()
        permissions = data.get("permissions")
        if not isinstance(permissions, dict):
            return response(False, "Permissions must be a dictionary"), 400

        admin = Admin.objects.get(id=admin_id)
        admin.permissions = permissions
        admin.save()
        return response(True, "Permissions updated successfully"), 200

    except DoesNotExist:
        return response(False, "Admin not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


# Delete admin
@admin_bp.route("/<admin_id>", methods=["DELETE"])
@token_required
def delete_admin(admin_id):
    try:
        admin = Admin.objects.get(id=admin_id)
        admin.delete()
        return response(True, "Admin deleted successfully"), 200

    except DoesNotExist:
        return response(False, "Admin not found"), 404
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


# Add new admin
@admin_bp.route("/", methods=["POST"])
@token_required
def add_admin():
    try:
        data = request.get_json()
        print(data)
        name = data.get("name")
        email = data.get("email")
        password = data.get("password")
        permissions = data.get("permissions", {})  # Optional, default empty dict

        # Basic validation
        if not name or not email or not password:
            return response(False, "Name, email, and password are required"), 400

        # Hash password
        hashed_password = generate_password_hash(password)

        # Create and save admin
        new_admin = Admin(
            name=name,
            email=email,
            password=hashed_password,
            permissions=permissions
        )
        new_admin.save()

        return response(True, "Admin created successfully", new_admin.to_json()), 201

    except NotUniqueError as e:
        print(e)
        return response(False, "Email already exists"), 400
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

@admin_bp.route("/<admin_id>/status", methods=["PUT"])
@token_required
def update_status(admin_id):
    try:
        data = request.get_json()
        status = data.get("status")  # True / False expected
        if status is None:
            return response(False, "Status is required"), 400
        if not isinstance(status, bool):
            return response(False, "Status must be a boolean"), 400

        admin = Admin.objects.get(id=admin_id)
        admin.is_active = status  # <-- use correct field
        admin.save()
        return response(True, "Admin status updated successfully", {"status": status}), 200

    except DoesNotExist:
        return response(False, "Admin not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500
