# routes/collegeadmin.py
from flask import Blueprint, request, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from mongoengine.errors import DoesNotExist
from utils.jwt import create_access_token, verify_access_token
from utils.response import response
from models.student import Student
from models.college import College
from models.test.students_test_attempt import StudentTestAttempt
from models.test.test import Test
from mongoengine.queryset.visitor import Q

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
        # request.admin = payload

        return f(*args, **kwargs)

    return decorated


student_bp = Blueprint("student_basic", __name__, url_prefix="/api/students")

@student_bp.route("/login", methods=["POST"])
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
        student = Student.objects.get(email=email)
    except DoesNotExist:
        return response(False, "Student Not found"), 401

    # Password verification
    password_ok = False
    try:
        if student.check_password(password):
            password_ok = True
    except Exception as e:
        print('erer')
        print(e)
        password_ok = False

 

    if not password_ok:
        return response(False, "Invalid Password"), 401
    college = student.college

    if not college:
        return response(False, "no college associated with this "), 400
    print(student.first_time_login)

    first_time = bool(getattr(student, "first_time_login", False))
    

    payload = {
        "student_id": str(student.id),
        "college_id": str(college.id),
    }

    token = create_access_token(payload,expires_delta=timedelta(hours=12))
    print(first_time)

    data = {
        "token": token,
        "admin": {
            "id": str(student.id),
            "name": getattr(student, "name", None),
            "email": student.email,
            "is_first_login": first_time
        },
        "college": {
            "id": str(college.id),
            "name": college.name,
            "college_id": college.college_id
        }
    }

    return response(True, "login successful", data), 200


@student_bp.route("/change-password", methods=["POST"])
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
    student_id = payload.get("student_id")
    if not student_id:
        return response(False, "token missing student_id"), 401

    try:
        student = Student.objects.get(id=student_id)
    except DoesNotExist:
        return response(False, "student not found"), 404

    student.password = generate_password_hash(new_password)
    student.first_time_login= False
    student.save()
    

    return response(True, "password changed successfully"), 200

from datetime import datetime, timedelta

from bson import ObjectId

@student_bp.route("/tests", methods=["GET"])
@token_required
def list_tests():
    """
    GET /api/students/tests
    Query params:
      - status: all | upcoming | ongoing | past   (default: all)
      - college_id: <id>                          (optional; defaults to student's college)
      - limit: int                                (default: 50)
      - offset: int                               (default: 0)
    """
    status = (request.args.get("status") or "all").strip().lower()
    college_id = request.args.get("college_id")
    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return response(False, "limit and offset must be integers"), 400

    payload = getattr(request, "token_payload", {}) or {}
    student_id = payload.get("student_id")
    college_from_token = payload.get("college_id")

    if not student_id:
        return response(False, "token missing student_id"), 401

    # default college_id to student's college if not provided
    if not college_id:
        college_id = college_from_token

    now = datetime.utcnow()
    query = Q()

    # NOTE: Use the correct field name from your Test model: 'college'
    if college_id:
        # If Test.college stores ObjectId strings or plain strings, ensure matching type.
        query &= Q(college=str(college_id))

    # status filters: use start_datetime / end_datetime (correct model fields)
    if status == "upcoming":
        query &= Q(start_datetime__gt=now)
    elif status == "ongoing":
        query &= Q(start_datetime__lte=now) & Q(end_datetime__gte=now)
    elif status == "past":
        query &= Q(end_datetime__lt=now)
    elif status == "all":
        pass
    else:
        return response(False, "invalid status. allowed: all, upcoming, ongoing, past"), 400

    # fetch assigned tests for student
    try:
        assigned_qs = StudentTestAttempt.objects(student_id=str(student_id)).only("test_id")
    except Exception as e:
        current_app.logger.exception("Error querying StudentTestAttempt: %s", e)
        return response(False, "error fetching assigned tests"), 500

    # normalize test ids to ObjectId when possible, else keep strings
    test_ids = []
    for a in assigned_qs:
        t = getattr(a, "test_id", None)
        if t is None:
            continue
        # If it's already an ObjectId, use it. If it's a string that looks like an ObjectId, convert.
        if isinstance(t, ObjectId):
            test_ids.append(t)
        else:
            try:
                test_ids.append(ObjectId(str(t)))
            except Exception:
                # fallback: use string (maybe Test.id is stored/compared as string)
                test_ids.append(str(t))

    if not test_ids:
        return response(True, "no tests found", {"tests": [], "total": 0, "limit": limit, "offset": offset}), 200

    query &= Q(id__in=test_ids)

    # run query with pagination and correct order_by field name
    try:
        total = Test.objects(query).count()
        tests_qs = (
            Test.objects(query)
            .order_by("+start_datetime")
            .skip(max(offset, 0))
            .limit(max(limit, 1))
        )
    except Exception as e:
        current_app.logger.exception("Error querying Test model: %s", e)
        return response(False, "error fetching tests"), 500

    tests = [t.to_minimal_json() for t in tests_qs]
    return response(True, "tests fetched", {"tests": tests, "total": total, "limit": limit, "offset": offset}), 200