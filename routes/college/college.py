# routes/college_routes.py
from flask import Blueprint, request
from functools import wraps
from mongoengine.errors import ValidationError, NotUniqueError
from models.college import College
from utils.jwt import verify_access_token
from utils.response import response
from models.college import Address
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
@token_required
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
