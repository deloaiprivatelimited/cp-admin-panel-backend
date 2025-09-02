# routes/college_routes.py
from flask import Blueprint, request
from functools import wraps
from mongoengine.errors import ValidationError, NotUniqueError
from models.college import College
from utils.jwt import verify_access_token
from utils.response import response
from werkzeug.security import generate_password_hash
from utils.admin_helper import get_current_admin_id
from models.admin import Admin
from models.college import Address ,Contact,CollegeAdmin
college_bp = Blueprint("college_bp", __name__)

# Decorator to check token validity
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization")
        if not token:
            return response(False, "Token is missing"), 401

        if token.startswith("Bearer "):
            token = token[7:]

        try:
            payload = verify_access_token(token)
        except ValueError as e:
            return response(False, str(e)), 401

        request.admin = payload
        return f(*args, **kwargs)

    return decorated

@college_bp.route("/", methods=["POST"])
# @token_required
def add_college():
    try:
        data = request.get_json()
        name = data.get("name")
        college_id = data.get("college_id")
        address_data = data.get("address", {})
        notes = data.get("notes", "")
        status = data.get("status", "active")

        if not name or not college_id or not address_data:
            return response(False, "Name, college_id, and address are required"), 400

        # Create Address object field by field
        address = Address(
            line1=address_data.get("line1"),
            line2=address_data.get("line2"),
            city=address_data.get("city"),
            state=address_data.get("state"),
            country=address_data.get("country"),
            zip_code=address_data.get("zip_code")
        )

        # Create college
        new_college = College(
            name=name,
            college_id=college_id,
            address=address,
            notes=notes,
            status=status
        )
        new_college.save()

        return response(True, "College added successfully", new_college.to_json()), 201

    except NotUniqueError:
        return response(False, "College with this ID already exists"), 400
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

# routes/college_routes.py

@college_bp.route("/<college_id>", methods=["PUT"])
# @token_required
def edit_college(college_id):
    try:
        data = request.get_json()
        college = College.objects(college_id=college_id).first()

        if not college:
            return response(False, "College not found"), 404

        # Update fields if provided
        if "name" in data:
            college.name = data["name"]

        if "notes" in data:
            college.notes = data["notes"]

        if "address" in data:
            addr = data["address"]
            college.address.line1 = addr.get("line1", college.address.line1)
            college.address.line2 = addr.get("line2", college.address.line2)
            college.address.city = addr.get("city", college.address.city)
            college.address.state = addr.get("state", college.address.state)
            college.address.country = addr.get("country", college.address.country)
            college.address.zip_code = addr.get("zip_code", college.address.zip_code)

        college.save()

        return response(True, "College updated successfully", college.to_json()), 200

    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


@college_bp.route("/<college_id>/status", methods=["PATCH"])
# @token_required
def update_college_status(college_id):
    try:
        data = request.get_json()
        status = data.get("status")

        if not status:
            return response(False, "Status is required"), 400

        college = College.objects(college_id=college_id).first()
        if not college:
            return response(False, "College not found"), 404

        college.status = status
        college.save()

        return response(True, "College status updated successfully", {"college_id": college.college_id, "status": college.status}), 200

    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

from mongoengine.queryset import Q

@college_bp.route("/", methods=["GET"])
@token_required
def get_colleges():
    try:
        search = request.args.get("search")  # single search parameter

        if search:
            # Search both name and college_id using OR
            query = Q(name__icontains=search) | Q(college_id__icontains=search)
            colleges = College.objects(query)
        else:
            colleges = College.objects()  # fetch all if no search

        # Only include selected fields in the response
        college_list = []
        for college in colleges:
            college_list.append({
                "id": str(college.id),
                "name": college.name,
                "college_id": college.college_id,
                "address": {
                    "line1": college.address.line1 if college.address else None,
                    "line2": college.address.line2 if college.address else None,
                    "city": college.address.city if college.address else None,
                    "state": college.address.state if college.address else None,
                    "country": college.address.country if college.address else None,
                    "zip_code": college.address.zip_code if college.address else None,
                },
                "status": college.status,
                "notes": college.notes
            })

        return response(True, "Colleges fetched successfully", college_list), 200

    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

from bson import ObjectId  # optional, for validating ObjectId if needed

@college_bp.route("/<college_id>", methods=["GET"])
@token_required
def get_college_by_id(college_id):
    try:
        # Fetch college by MongoDB _id or college_id
        college = College.objects(id=college_id).first()  # if using ObjectId
        # Alternatively, if you want to fetch by your custom college_id field:
        # college = College.objects(college_id=college_id).first()

        if not college:
            return response(False, "College not found"), 404

        # Build response
        college_data = {
            "id": str(college.id),
            "name": college.name,
            "college_id": college.college_id,
            "address": {
                "line1": college.address.line1 if college.address else None,
                "line2": college.address.line2 if college.address else None,
                "city": college.address.city if college.address else None,
                "state": college.address.state if college.address else None,
                "country": college.address.country if college.address else None,
                "zip_code": college.address.zip_code if college.address else None,
            },
            "status": college.status,
            "notes": college.notes,
            "contacts": [c.to_json() for c in college.contacts],
            "admins": [admin.to_json() for admin in college.admins],
            "tokens":[t.to_json() for t in college.token_logs] if college.token_logs else [],
            "token_config": college.token.to_json() if college.token else None
        }

        return response(True, "College fetched successfully", college_data), 200

    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

@college_bp.route("/<college_id>/contacts", methods=["POST"])
@token_required
def add_college_contact(college_id):
    try:
        data = request.get_json()
        name = data.get("name")
        phone = data.get("phone")
        email = data.get("email")
        designation = data.get("designation", "")
        status = data.get("status", "active")

        if not name or not phone or not email:
            return response(False, "Name, phone, and email are required"), 400

        # Fetch the college by ID
        college = College.objects(id=college_id).first()
        if not college:
            return response(False, "College not found"), 404

        # Create a new Contact
        new_contact = Contact(
            name=name,
            phone=phone,
            email=email,
            designation=designation,
            status=status
        )

        # Append to the contacts list
        college.contacts.append(new_contact)
        college.save()

        return response(True, "Contact added successfully", [c.to_json() for c in college.contacts]), 201

    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


# Edit a contact by index
@college_bp.route("/<college_id>/contacts/<int:index>", methods=["PUT"])
@token_required
def edit_college_contact(college_id, index):
    try:
        data = request.get_json()
        college = College.objects(id=college_id).first()
        if not college:
            return response(False, "College not found"), 404

        if index < 0 or index >= len(college.contacts):
            return response(False, "Contact index out of range"), 400

        contact = college.contacts[index]

        # Update fields if provided
        contact.name = data.get("name", contact.name)
        contact.phone = data.get("phone", contact.phone)
        contact.email = data.get("email", contact.email)
        contact.designation = data.get("designation", contact.designation)
        contact.status = data.get("status", contact.status)

        college.save()
        return response(True, "Contact updated successfully", [c.to_json() for c in college.contacts]), 200

    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


# Toggle contact status (active/inactive) by index
@college_bp.route("/<college_id>/contacts/<int:index>/toggle-status", methods=["PATCH"])
@token_required
def toggle_contact_status(college_id, index):
    try:
        college = College.objects(id=college_id).first()
        if not college:
            return response(False, "College not found"), 404

        if index < 0 or index >= len(college.contacts):
            return response(False, "Contact index out of range"), 400

        contact = college.contacts[index]
        contact.status = "inactive" if contact.status == "active" else "active"
        college.save()

        return response(True, "Contact status toggled", [c.to_json() for c in college.contacts]), 200

    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


# Delete a contact by index
@college_bp.route("/<college_id>/contacts/<int:index>", methods=["DELETE"])
@token_required
def delete_college_contact(college_id, index):
    try:
        college = College.objects(id=college_id).first()
        if not college:
            return response(False, "College not found"), 404

        if index < 0 or index >= len(college.contacts):
            return response(False, "Contact index out of range"), 400

        # Remove the contact at the given index
        removed_contact = college.contacts.pop(index)
        college.save()

        return response(True, "Contact deleted successfully", [c.to_json() for c in college.contacts]), 200

    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

# College Admin Routes
@college_bp.route("/<college_id>/admins", methods=["POST"])
@token_required
def add_college_admin(college_id):
    try:
        data = request.get_json()
        name = data.get("name")
        email = data.get("email")
        password = data.get("password")  # make sure to hash in real use
        designation = data.get("designation", "")
        phone = data.get("phone", "")

        status = data.get("status", "active")

        if not name or not email or not password:
            return response(False, "Name, email, and password are required"), 400

        college = College.objects(id=college_id).first()
        if not college:
            return response(False, "College not found"), 404
        hashed_password = generate_password_hash(password)
        # Create admin
        new_admin = CollegeAdmin(
            name=name,
            email=email,
            password=hashed_password,
            designation=designation,
            status=status,
            phone=phone
        )
        new_admin.save()

        college.admins.append(new_admin)
        college.save()

        return response(True, "Admin added successfully", [a.to_json() for a in college.admins]), 201

    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


# Edit admin by index
@college_bp.route("/<college_id>/admins/<admin_id>", methods=["PUT"])
@token_required
def edit_college_admin(college_id, admin_id):
    try:
        data = request.get_json()
        college = College.objects(id=college_id).first()
        if not college:
            return response(False, "College not found"), 404

        admin = next((a for a in college.admins if str(a.id) == admin_id), None)
        if not admin:
            return response(False, "Admin not found"), 404

        admin.name = data.get("name", admin.name)
        admin.email = data.get("email", admin.email)
        admin.designation = data.get("designation", admin.designation)
        admin.status = data.get("status", admin.status)
        admin.phone = data.get("phone", admin.phone)
        admin.save()
        college.save()

        return response(True, "Admin updated successfully", [a.to_json() for a in college.admins]), 200
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

# Toggle admin status by index
@college_bp.route("/<college_id>/admins/<admin_id>/toggle-status", methods=["PATCH"])
@token_required
def toggle_admin_status(college_id, admin_id):
    try:
        college = College.objects(id=college_id).first()
        if not college:
            return response(False, "College not found"), 404

        admin = next((a for a in college.admins if str(a.id) == admin_id), None)
        if not admin:
            return response(False, "Admin not found"), 404

        admin.status = "inactive" if admin.status == "active" else "active"
        admin.save()
        college.save()

        return response(True, "Admin status toggled", [a.to_json() for a in college.admins]), 200
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


# Update admin password by index
@college_bp.route("/<college_id>/admins/<admin_id>/update-password", methods=["PATCH"])
@token_required
def update_admin_password(college_id, admin_id):
    try:
        data = request.get_json()
        print(data)
        new_password = data.get("newPassword")
        if not new_password:
            return response(False, "New password is required"), 400

        college = College.objects(id=college_id).first()
        if not college:
            return response(False, "College not found"), 404

        admin = next((a for a in college.admins if str(a.id) == admin_id), None)
        if not admin:
            return response(False, "Admin not found"), 404

        admin.password = generate_password_hash(new_password)
        admin.is_first_login = False
        admin.save()
        college.save()

        return response(True, "Password updated successfully", [a.to_json() for a in college.admins]), 200
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

@college_bp.route("/<college_id>/admins/<admin_id>", methods=["DELETE"])
@token_required
def delete_college_admin(college_id, admin_id):
    try:
        college = College.objects(id=college_id).first()
        if not college:
            return response(False, "College not found"), 404

        admin = next((a for a in college.admins if str(a.id) == admin_id), None)
        if not admin:
            return response(False, "Admin not found"), 404

        college.admins = [a for a in college.admins if str(a.id) != admin_id]
        admin.delete()
        college.save()

        return response(True, "Admin deleted successfully", [a.to_json() for a in college.admins]), 200
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

from models.college import TokenLog, TokenConfig, TokenStatus, CollegeAdmin

@college_bp.route("/<college_id>/token-log", methods=["POST"])
@token_required
def add_token_log(college_id):
    try:
        admin_id = get_current_admin_id()

        data = request.get_json()
        number_of_tokens = data.get("number_of_tokens")
        notes = data.get("notes", "")
        if number_of_tokens is None:
            return response(False, "number_of_tokens is required"), 400

        # Fetch the college
        college = College.objects(id=college_id).first()
        if not college:
            return response(False, "College not found"), 404

        # Check or create TokenConfig
        token_config = TokenConfig.objects(college=college).first()
        if not token_config:
            token_config = TokenConfig(
                college=college,
                total_tokens=TokenStatus(count=number_of_tokens, status="active"),
                consumed_tokens=TokenStatus(count=0, status="active"),
                pending_tokens=TokenStatus(count=0, status="active"),
                unused_tokens=TokenStatus(count=number_of_tokens, status="active")
            )
            token_config.save()
            college.token = token_config
        else:
            # Add new tokens to total
            token_config.total_tokens.count += number_of_tokens
            token_config.unused_tokens.count += number_of_tokens
            token_config.save()
        print(admin_id)
        assigned_admin = Admin.objects(id=admin_id).first() if admin_id else None
        print("Assigned Admin:", assigned_admin)


        # Create TokenLog with active status
        token_log = TokenLog(
            number_of_tokens=TokenStatus(count=number_of_tokens, status="active"),
                assigned_by=assigned_admin,
                unused_tokens=TokenStatus(count=number_of_tokens, status="active"),

            notes=notes,
        )
        token_log.save()

        # Append to college logs
        college.token_logs.append(token_log)
        college.save()

        return response(True, "Token log added successfully", token_log.to_json()), 201

    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

# Update unused_tokens status
@college_bp.route("/<college_id>/token-log/<token_log_id>/unused-tokens/status", methods=["PATCH"])
@token_required
def update_unused_tokens_status(college_id, token_log_id):
    try:
        data = request.get_json()
        new_status = data.get("status")
        if new_status not in ["active", "inactive"]:
            return response(False, "Status must be 'active' or 'inactive'"), 400

        token_log = TokenLog.objects(id=token_log_id).first()
        if not token_log:
            return response(False, "Token log not found"), 404

        token_log.unused_tokens.status = new_status
        token_log.save()
        print(token_log.to_json())
        return response(True, "Unused tokens status updated", token_log.to_json()), 200
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


# Update consumed_tokens status
@college_bp.route("/<college_id>/token-log/<token_log_id>/consumed-tokens/status", methods=["PATCH"])
@token_required
def update_consumed_tokens_status(college_id, token_log_id):
    try:
        data = request.get_json()
        new_status = data.get("status")
        if new_status not in ["active", "inactive"]:
            return response(False, "Status must be 'active' or 'inactive'"), 400

        token_log = TokenLog.objects(id=token_log_id).first()
        if not token_log:
            return response(False, "Token log not found"), 404

        token_log.consumed_tokens.status = new_status
        token_log.save()
        return response(True, "Consumed tokens status updated", token_log.to_json()), 200
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


# Update pending_initiation tokens status
@college_bp.route("/<college_id>/token-log/<token_log_id>/pending-tokens/status", methods=["PATCH"])
@token_required
def update_pending_tokens_status(college_id, token_log_id):
    try:
        data = request.get_json()
        new_status = data.get("status")
        if new_status not in ["active", "inactive"]:
            return response(False, "Status must be 'active' or 'inactive'"), 400

        token_log = TokenLog.objects(id=token_log_id).first()
        if not token_log:
            return response(False, "Token log not found"), 404

        token_log.pending_initiation.status = new_status
        token_log.save()
        return response(True, "Pending tokens status updated", token_log.to_json()), 200
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


# Edit notes of a token log
@college_bp.route("/<college_id>/token-log/<token_log_id>/edit-notes", methods=["PATCH"])
@token_required
def edit_token_log_notes(college_id, token_log_id):
    try:
        data = request.get_json()
        new_notes = data.get("notes", "")
        if new_notes is None:
            return response(False, "Notes are required"), 400

        # Fetch the token log
        token_log = TokenLog.objects(id=token_log_id).first()
        if not token_log:
            return response(False, "Token log not found"), 404

        # Update notes
        token_log.notes = new_notes
        token_log.save()

        return response(True, "Token log notes updated successfully", token_log.to_json()), 200

    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500
