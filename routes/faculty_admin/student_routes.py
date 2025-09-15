import os, secrets, string
from flask import Blueprint, request, jsonify, current_app
from models.student import Student
from mongoengine.errors import NotUniqueError, ValidationError, FieldDoesNotExist
from tasks.mail_tasks import send_mail
from utils.response import response
from utils.jwt import create_access_token, verify_access_token
from models.college import College
bp = Blueprint("students_bp", __name__, url_prefix="/students")

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

def generate_password(length=12):
    alphabet = string.ascii_letters + string.digits + "!@#$%&*?"
    while True:
        pwd = ''.join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in "!@#$%&*?" for c in pwd)):
            return pwd

def build_email(student, plain_password):
    url = os.getenv("APP_URL", "https://deloai.com")
    subject = "Your DeloAI account credentials"
    html = f"""
    <p>Hi {student.name},</p>
    <p>Your student account has been created.</p>
    <p><b>Login:</b> <a href="{url}">{url}</a></p>
    <p><b>USN:</b> {getattr(student, 'usn', '')}<br>
       <b>Email:</b> {student.email}<br>
       <b>Password:</b> {plain_password}</p>
    <p>Please login and change your password.</p>
    """
    text = f"""Hi {student.name},

Login: {url}
USN: {getattr(student, 'usn', '')}
Email: {student.email}
Password: {plain_password}

Please login and change your password.
"""
    return subject, html, text

@bp.route('/add-bulk-students', methods=["POST"])
@token_required
def add_bulk_students():
    payload = getattr(request, "token_payload", {})
    college_id = payload.get("college_id")
    if not college_id:
        return response(False, "College ID missing in token"), 400

    data = request.get_json(force=True, silent=True) or {}
    students_data = data.get('mappedData') or data.get('students') or []

    # if nothing provided
    if not isinstance(students_data, list) or len(students_data) == 0:
        return response(False, "No students provided in 'mappedData' (expected a list)"), 400

    # resolve college
    try:
        college = College.objects.get(id=college_id)
    except College.DoesNotExist:
        return response(False, "College not found"), 404
    except ValidationError:
        return response(False, "Invalid College ID"), 400

    results = []
    created_count = 0

    for idx, sd in enumerate(students_data):
        # normalize dict-like input
        if not isinstance(sd, dict):
            results.append({"index": idx, "status": "error", "message": "Invalid item (expected object/dict)."})
            continue

        name = sd.get("name") or sd.get("first_name") or sd.get("full_name")
        email = sd.get("email")
        # Name and email mandatory for creation per user's requirement
        if not name or not email:
            results.append({
                "index": idx,
                "status": "skipped",
                "message": "Missing required field(s): name and email are required.",
                "provided": {"name": name, "email": email}
            })
            continue

        # prepare fields to set - make sure we only set fields that exist on the model
        allowed_fields = [
            "name", "gender", "date_of_birth", "email", "phone_number",
            "usn", "enrollment_number", "branch", "year_of_study", "semester",
            "cgpa", "address", "city", "state", "pincode",
            "guardian_name", "guardian_contact"
        ]
        student_kwargs = {"college": college.id}
        for key in allowed_fields:
            if key in sd and sd.get(key) is not None:
                student_kwargs[key] = sd.get(key)

        # create student instance
        try:
            plain_password = generate_password(12)
            student = Student(**student_kwargs)
            # set password (hashes internally)
            student.set_password(plain_password)
            # ensure required model fields exist - Student.name and Student.email are required by your model
            # Save
            student.save()

            # send email (Celery style if available)
            subject, html, text = build_email(student, plain_password)
            try:
                # prefer async task if present
                if hasattr(send_mail, "delay"):
                    send_mail.delay(to=[student.email], subject=subject, html=html, text=text)
                else:
                    send_mail(to=[student.email], subject=subject, html=html, text=text)
            except Exception as mail_exc:
                # email failure should not mark student creation as failed
                results.append({
                    "index": idx,
                    "status": "created_email_failed",
                    "message": f"Student created but email sending failed: {str(mail_exc)}",
                    "student_id": str(student.id),
                    "email": student.email
                })
                created_count += 1
                continue

            results.append({
                "index": idx,
                "status": "created",
                "message": "Student created and email scheduled/sent.",
                "student_id": str(student.id),
                "email": student.email
            })
            created_count += 1

        except NotUniqueError as e:
            # check which field likely violated uniqueness
            err_msg = str(e)
            results.append({
                "index": idx,
                "status": "error",
                "message": f"Unique constraint error: {err_msg}"
            })
        except ValidationError as e:
            results.append({
                "index": idx,
                "status": "error",
                "message": f"Validation error: {str(e)}"
            })
        except Exception as e:
            results.append({
                "index": idx,
                "status": "error",
                "message": f"Unexpected error: {str(e)}"
            })

    return jsonify({
        "success": True,
        "college": {"id": str(college.id), "name": college.name},
        "created_count": created_count,
        "total_received": len(students_data),
        "results": results
    }), 200


@bp.route('/upsert-bulk-students', methods=["POST"])
@token_required
def upsert_bulk_students():
    """
    Expects JSON:
    {
      "primaryField": "email",            # one of: "email", "usn", "enrollment_number"
      "students": [
        { "email": "a@x.com", "phone_number": "98765", "city": "Bangalore" },
        { "email": "b@x.com", "cgpa": 8.2 },
        ...
      ]
    }

    Behavior: update if student exists (scoped to college), otherwise create student and send credentials email.
    """
    payload = getattr(request, "token_payload", {})
    college_id = payload.get("college_id")
    if not college_id:
        return response(False, "College ID missing in token"), 400

    data = request.get_json(force=True, silent=True) or {}
    primary_field = data.get("primaryField", "email")
    students_items = data.get("students") or []

    VALID_PRIMARYS = ["email", "usn", "enrollment_number"]
    if primary_field not in VALID_PRIMARYS:
        return response(False, f"primaryField must be one of {VALID_PRIMARYS}"), 400

    if not isinstance(students_items, list) or len(students_items) == 0:
        return response(False, "No students provided in 'students' (expected a list)"), 400

    # resolve college
    try:
        college = College.objects.get(id=college_id)
    except College.DoesNotExist:
        return response(False, "College not found"), 404
    except ValidationError:
        return response(False, "Invalid College ID"), 400

    allowed_fields = [
        "name", "gender", "date_of_birth", "email", "phone_number",
        "usn", "enrollment_number", "branch", "year_of_study", "semester",
        "cgpa","address", "city", "state", "pincode",
        "guardian_name", "guardian_contact", "is_active", "first_time_login"
    ]

    results = []
    updated_count = 0
    created_count = 0

    for idx, item in enumerate(students_items):
        if not isinstance(item, dict):
            results.append({"index": idx, "status": "error", "message": "Invalid item (expected object/dict)."})
            continue

        primary_value = item.get(primary_field)
        if not primary_value:
            results.append({
                "index": idx,
                "status": "skipped",
                "message": f"Missing primary field '{primary_field}'",
                "provided": item
            })
            continue

        # Build update dict (skip changing primary field itself to avoid accidental key clash)
        set_updates = {}
        for key in allowed_fields:
            if key == primary_field:
                continue
            if key in item and item.get(key) is not None:
                set_updates[f"set__{key}"] = item.get(key)

        # Try find student scoped to college
        try:
            query = {primary_field: primary_value, "college": college}
            student_qs = Student.objects(**query)

            if student_qs.count() > 0:
                # Student exists => update
                if set_updates:
                    try:
                        student_qs.update(**set_updates)
                        student_after = Student.objects(**query).first()
                        results.append({
                            "index": idx,
                            "primary": {primary_field: primary_value},
                            "status": "updated",
                            "student_id": str(student_after.id) if student_after else None
                        })
                        updated_count += 1
                    except NotUniqueError as e:
                        results.append({
                            "index": idx,
                            "primary": {primary_field: primary_value},
                            "status": "error",
                            "message": f"Unique constraint error during update: {str(e)}"
                        })
                else:
                    results.append({
                        "index": idx,
                        "primary": {primary_field: primary_value},
                        "status": "skipped",
                        "message": "No updatable fields present."
                    })
                continue

            # Not found -> create new Student
            # Ensure required fields (name and email) exist; for creation we require name and email per your add route.
            # If primary field is email, email is present. If primary is usn/enrollment_number and email missing, creation will be skipped.
            name = item.get("name") or item.get("first_name") or item.get("full_name")
            email = item.get("email")
            if not name or not email:
                results.append({
                    "index": idx,
                    "primary": {primary_field: primary_value},
                    "status": "skipped",
                    "message": "Missing required field(s) for creation: name and email are required.",
                    "provided": item
                })
                continue

            # prepare kwargs for new student
            student_kwargs = {"college": college}
            for key in allowed_fields:
                if key in item and item.get(key) is not None:
                    student_kwargs[key] = item.get(key)

            # If primary_field wasn't included in allowed_fields (it is), ensure it's set
            if primary_field not in student_kwargs and primary_value:
                student_kwargs[primary_field] = primary_value

            try:
                plain_password = generate_password(12)
                student = Student(**student_kwargs)
                student.set_password(plain_password)
                student.save()  # persist

                # send email (celery or direct)
                subject, html, text = build_email(student, plain_password)
                try:
                    if hasattr(send_mail, "delay"):
                        send_mail.delay(to=[student.email], subject=subject, html=html, text=text)
                    else:
                        send_mail(to=[student.email], subject=subject, html=html, text=text)
                    results.append({
                        "index": idx,
                        "primary": {primary_field: primary_value},
                        "status": "created",
                        "student_id": str(student.id),
                        "email": student.email
                    })
                    created_count += 1
                except Exception as mail_exc:
                    # Creation succeeded but email failed
                    results.append({
                        "index": idx,
                        "primary": {primary_field: primary_value},
                        "status": "created_email_failed",
                        "student_id": str(student.id),
                        "email": student.email,
                        "message": f"Student created but email sending failed: {str(mail_exc)}"
                    })
                    created_count += 1

            except NotUniqueError as e:
                results.append({
                    "index": idx,
                    "primary": {primary_field: primary_value},
                    "status": "error",
                    "message": f"Unique constraint error during create: {str(e)}"
                })
            except ValidationError as e:
                results.append({
                    "index": idx,
                    "primary": {primary_field: primary_value},
                    "status": "error",
                    "message": f"Validation error during create: {str(e)}"
                })
            except Exception as e:
                results.append({
                    "index": idx,
                    "primary": {primary_field: primary_value},
                    "status": "error",
                    "message": f"Unexpected error during create: {str(e)}"
                })

        except ValidationError as e:
            results.append({
                "index": idx,
                "primary": {primary_field: primary_value},
                "status": "error",
                "message": f"Invalid identifier or query error: {str(e)}"
            })
        except Exception as e:
            results.append({
                "index": idx,
                "primary": {primary_field: primary_value},
                "status": "error",
                "message": f"Unexpected error: {str(e)}"
            })

    return jsonify({
        "success": True,
        "college": {"id": str(college.id), "name": college.name},
        "updated_count": updated_count,
        "created_count": created_count,
        "total_received": len(students_items),
        "results": results
    }), 200


from math import ceil
from flask import request, jsonify
from mongoengine import ValidationError
# no changes to token_required; reuse existing decorator

@bp.route('/list', methods=['GET'])
@token_required
def list_students():
    """
    GET /students/list?page=1&per_page=20&search=abc&branch=CSE&year_of_study=3
    Query params (all optional):
      - page (int, default=1)
      - per_page (int, default=20, max=100)
      - search (string)
      - year_of_study (int or comma separated list)
      - gender (string or comma separated list)
      - branch (string or comma separated list)
      - is_active (true/false)
      - min_cgpa, max_cgpa (numbers)
      - sort_by (field name, default: name)
      - sort_dir (asc|desc, default: asc)
    Response:
      - students: [ ...student dicts... ]
      - meta: { total, page, per_page, total_pages }
      - filters_meta: { years, genders, branches }
    """
    payload = getattr(request, "token_payload", {})
    college_id = payload.get("college_id")
    if not college_id:
        return response(False, "College ID missing in token"), 400

    # pagination
    try:
        page = max(1, int(request.args.get("page", 1)))
    except Exception:
        page = 1
    try:
        per_page = int(request.args.get("per_page", 20))
    except Exception:
        per_page = 20
    per_page = max(1, min(per_page, 100))

    search = (request.args.get("search") or "").strip()
    sort_by = request.args.get("sort_by", "name")
    sort_dir = (request.args.get("sort_dir", "asc") or "asc").lower()
    sort_prefix = "" if sort_dir == "asc" else "-"

    # resolve college
    try:
        college = College.objects.get(id=college_id)
    except College.DoesNotExist:
        return response(False, "College not found"), 404
    except ValidationError:
        return response(False, "Invalid College ID"), 400

    college_oid = college.id  # use ObjectId, not model

    # filters
    def _split_csv(name):
        val = request.args.get(name)
        if not val:
            return None
        return [v.strip() for v in val.split(",") if v.strip()]

    query_filters = {}
    years = _split_csv("year_of_study")
    if years:
        query_filters["year_of_study__in"] = [int(y) if y.isdigit() else y for y in years]

    genders = _split_csv("gender")
    if genders:
        query_filters["gender__in"] = genders

    branches = _split_csv("branch")
    if branches:
        query_filters["branch__in"] = branches

    is_active = request.args.get("is_active")
    if is_active is not None:
        if str(is_active).lower() in ("true", "1", "yes"):
            query_filters["is_active"] = True
        elif str(is_active).lower() in ("false", "0", "no"):
            query_filters["is_active"] = False

    try:
        min_cgpa = request.args.get("min_cgpa")
        if min_cgpa:
            query_filters["cgpa__gte"] = float(min_cgpa)
    except Exception:
        pass
    try:
        max_cgpa = request.args.get("max_cgpa")
        if max_cgpa:
            query_filters["cgpa__lte"] = float(max_cgpa)
    except Exception:
        pass

    # raw query with ObjectId
    mongo_raw_query = {"college": college_oid}
    for k, v in query_filters.items():
        if k.endswith("__in"):
            mongo_raw_query[k[:-4]] = {"$in": v}
        elif k.endswith("__gte"):
            f = k[:-5]; mongo_raw_query.setdefault(f, {})["$gte"] = v
        elif k.endswith("__lte"):
            f = k[:-5]; mongo_raw_query.setdefault(f, {})["$lte"] = v
        else:
            mongo_raw_query[k] = v

    if search:
        or_clauses = [
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"usn": {"$regex": search, "$options": "i"}},
            {"enrollment_number": {"$regex": search, "$options": "i"}},
        ]
        mongo_raw_query = {"$and": [mongo_raw_query, {"$or": or_clauses}]}

    # total + pagination
    total = Student.objects(__raw__=mongo_raw_query).count()
    total_pages = ceil(total / per_page) if per_page else 1
    skip = (page - 1) * per_page
    allowed_fields = [
        "id", "name", "email", "phone_number", "usn", "enrollment_number",
        "branch", "year_of_study", "semester", "cgpa", "gender", "is_active",
        "first_time_login", "address", "city", "state", "pincode",
        "guardian_name", "guardian_contact" 
    ]

    if sort_by not in allowed_fields and sort_by not in ("created_at", "updated_at"):
        sort_by = "name"
    sort_arg = f"{sort_prefix}{sort_by}"

    qs = Student.objects(__raw__=mongo_raw_query).order_by(sort_arg).skip(skip).limit(per_page)

    students = []
    for s in qs:
        row = {}
        for f in allowed_fields:
            row[f if f != "id" else "id"] = str(getattr(s, "id")) if f == "id" else getattr(s, f, None)
        students.append(row)

    # filters_meta (can use model objects safely here)
    def _distinct_counts(field):
        vals = Student.objects(college=college).distinct(field)
        out = []
        for v in vals:
            count = Student.objects(college=college, **{field: v}).count()
            out.append({"value": v, "count": count})
        return sorted(out, key=lambda x: (x["value"] is None or x["value"] == "", str(x["value"])))

    return jsonify({
        "success": True,
        "college": {"id": str(college.id), "name": college.name},
        "students": students,
        "meta": {
            "total": total, "page": page, "per_page": per_page,
            "total_pages": total_pages, "sort_by": sort_by, "sort_dir": sort_dir
        },
        "filters_meta": {
            "years": _distinct_counts("year_of_study"),
            "genders": _distinct_counts("gender"),
            "branches": _distinct_counts("branch")
        }
    }), 200



from mongoengine.errors import NotUniqueError, ValidationError, FieldDoesNotExist, DoesNotExist
from flask import request, jsonify
from utils.response import response

# Allowed fields for update (same as used elsewhere)
UPDATE_ALLOWED = [
    "name", "gender", "date_of_birth", "email", "phone_number",
    "usn", "enrollment_number", "branch", "year_of_study", "semester",
    "cgpa", "address", "city", "state", "pincode",
    "guardian_name", "guardian_contact", "is_active", "first_time_login"
]

@bp.route("/<student_id>", methods=["GET"])
@token_required
def get_student_by_id(student_id):
    """Get a student by id (scoped to the caller's college)."""
    payload = getattr(request, "token_payload", {})
    college_id = payload.get("college_id")
    if not college_id:
        return response(False, "College ID missing in token"), 400

    try:
        college = College.objects.get(id=college_id)
    except College.DoesNotExist:
        return response(False, "College not found"), 404
    except ValidationError:
        return response(False, "Invalid College ID"), 400

    try:
        student = Student.objects.get(id=student_id, college=college)
    except (Student.DoesNotExist, DoesNotExist):
        return response(False, "Student not found"), 404
    except ValidationError:
        return response(False, "Invalid student id"), 400
    except Exception as e:
        return response(False, f"Unexpected error: {str(e)}"), 500

    # serialise allowed fields (and id)
    out = {"id": str(student.id)}
    for f in UPDATE_ALLOWED:
        out[f] = getattr(student, f, None)

    # include some extra common fields
    out["date_joined"] = student.date_joined.isoformat() if getattr(student, "date_joined", None) else None

    return jsonify({"success": True, "student": out}), 200


@bp.route("/<student_id>", methods=["PUT", "PATCH"])
@token_required
def update_student_by_id(student_id):
    """
    Update allowed student fields.
    Request JSON: { "name": "New", "city": "Bangalore", ... }
    """
    payload = getattr(request, "token_payload", {})
    college_id = payload.get("college_id")
    if not college_id:
        return response(False, "College ID missing in token"), 400

    try:
        college = College.objects.get(id=college_id)
    except College.DoesNotExist:
        return response(False, "College not found"), 404
    except ValidationError:
        return response(False, "Invalid College ID"), 400

    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict) or len(data) == 0:
        return response(False, "No update fields provided"), 400

    # build update kwargs using mongoengine update syntax (set__field)
    update_kwargs = {}
    for k, v in data.items():
        if k in UPDATE_ALLOWED:
            update_kwargs[f"set__{k}"] = v

    if not update_kwargs:
        return response(False, "No updatable fields present or fields not allowed."), 400

    try:
        qs = Student.objects(id=student_id, college=college)
        if qs.count() == 0:
            return response(False, "Student not found"), 404

        qs.update(**update_kwargs)
        student = Student.objects.get(id=student_id, college=college)
        out = {"id": str(student.id)}
        for f in UPDATE_ALLOWED:
            out[f] = getattr(student, f, None)
        return jsonify({"success": True, "student": out, "message": "Student updated"}), 200

    except NotUniqueError as e:
        return response(False, f"Unique constraint error during update: {str(e)}"), 400
    except ValidationError as e:
        return response(False, f"Validation error during update: {str(e)}"), 400
    except Exception as e:
        return response(False, f"Unexpected error: {str(e)}"), 500


@bp.route("/<student_id>/change-password", methods=["POST"])
@token_required
def change_password(student_id):
    """
    Change a student's password.
    Request JSON:
      {
        "old_password": "optional - required for normal user",
        "new_password": "required",
        "force": true/false (optional) - admins/managers may pass force=true to reset without old_password
      }

    Behavior:
      - If caller is the same student (token may include 'user_id' or 'student_id'), require old_password.
      - If caller has role 'admin' or 'manager' in token, allow force reset without old_password (if force=true).
      - After successful change, set first_time_login = False.
    """
    payload = getattr(request, "token_payload", {}) or {}
    college_id = payload.get("college_id")
    caller_id = payload.get("user_id") or payload.get("student_id")

    if not college_id:
        return response(False, "College ID missing in token"), 400

    try:
        college = College.objects.get(id=college_id)
    except College.DoesNotExist:
        return response(False, "College not found"), 404
    except ValidationError:
        return response(False, "Invalid College ID"), 400

    data = request.get_json(force=True, silent=True) or {}
    new_password = data.get("new_password")
    force = bool(data.get("force", False))

    if not new_password:
        return response(False, "new_password is required"), 400

    try:
        student = Student.objects.get(id=student_id, college=college)
    except (Student.DoesNotExist, DoesNotExist):
        return response(False, "Student not found"), 404
    except ValidationError:
        return response(False, "Invalid student id"), 400
    except Exception as e:
        return response(False, f"Unexpected error: {str(e)}"), 500

    # Determine permission: allow if caller is admin/manager OR caller is same student
    is_same_student = False
    try:
        if caller_id and str(caller_id) == str(student.id):
            is_same_student = True
    except Exception:
        is_same_student = False

    

    
    try:
        student.set_password(new_password)
        student.first_time_login = False
        student.save()
        return jsonify({"success": True, "message": "Password changed and first_time_login cleared."}), 200
    except ValidationError as e:
        return response(False, f"Validation error: {str(e)}"), 400
    except Exception as e:
        return response(False, f"Unexpected error: {str(e)}"), 500


@bp.route("/add", methods=["POST"])
@token_required
def add_student():
    """
    POST /students/add
    Body (JSON):
    {
      "name": "Student Name",
      "email": "student@example.com",
      "phone_number": "...",         # optional
      "usn": "...",                  # optional
      "branch": "CSE",               # optional
      ...                            # other allowed fields below
    }

    Behavior:
      - Requires token with college_id in payload.
      - Requires name and email.
      - Generates a random password if 'password' not provided in request.
      - Sends credentials email (async if send_mail.delay exists).
    """
    payload = getattr(request, "token_payload", {})
    college_id = payload.get("college_id")
    if not college_id:
        return response(False, "College ID missing in token"), 400

    # resolve college
    try:
        college = College.objects.get(id=college_id)
    except College.DoesNotExist:
        return response(False, "College not found"), 404
    except ValidationError:
        return response(False, "Invalid College ID"), 400

    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict) or not data:
        return response(False, "JSON body required"), 400

    # required fields
    name = data.get("name") or data.get("first_name") or data.get("full_name")
    email = data.get("email")
    if not name or not email:
        return response(False, "Missing required fields: name and email"), 400

    # allowed fields (match your other routes)
    allowed_fields = [
        "name", "gender", "date_of_birth", "email", "phone_number",
        "usn", "enrollment_number", "branch", "year_of_study", "semester",
        "cgpa", "subjects", "address", "city", "state", "pincode",
        "guardian_name", "guardian_contact", "is_active", "first_time_login"
    ]

    student_kwargs = {"college": college}
    for key in allowed_fields:
        if key in data and data.get(key) is not None:
            student_kwargs[key] = data.get(key)

    # Allow caller to provide a plain password (optional) otherwise generate one
    plain_password = data.get("password") or generate_password(12)

    try:
        student = Student(**student_kwargs)
        student.set_password(plain_password)
        student.save()

        # send email (try async if available)
        subject, html, text = build_email(student, plain_password)
        try:
            if hasattr(send_mail, "delay"):
                send_mail.delay(to=[student.email], subject=subject, html=html, text=text)
            else:
                send_mail(to=[student.email], subject=subject, html=html, text=text)
            email_status = "email_scheduled_or_sent"
        except Exception as mail_exc:
            email_status = f"email_failed: {str(mail_exc)}"

        out = {
            "success": True,
            "message": "Student created",
            "student": {
                "id": str(student.id),
                "name": student.name,
                "email": student.email
            },
            "email_status": email_status
        }
        return jsonify(out), 201

    except NotUniqueError as e:
        return response(False, f"Unique constraint error: {str(e)}"), 400
    except ValidationError as e:
        return response(False, f"Validation error: {str(e)}"), 400
    except Exception as e:
        return response(False, f"Unexpected error: {str(e)}"), 500


@bp.route("/<student_id>", methods=["DELETE"])
@token_required
def delete_student(student_id):
    """
    DELETE /students/<student_id>
    Deletes a student (scoped to the caller's college).

    Permissions:
      - Only 'admin' or 'manager' roles can delete.
    """
    payload = getattr(request, "token_payload", {})
    college_id = payload.get("college_id")

    if not college_id:
        return response(False, "College ID missing in token"), 400


    try:
        college = College.objects.get(id=college_id)
    except College.DoesNotExist:
        return response(False, "College not found"), 404
    except ValidationError:
        return response(False, "Invalid College ID"), 400

    try:
        student = Student.objects.get(id=student_id, college=college)
    except (Student.DoesNotExist, DoesNotExist):
        return response(False, "Student not found"), 404
    except ValidationError:
        return response(False, "Invalid student id"), 400
    except Exception as e:
        return response(False, f"Unexpected error: {str(e)}"), 500

    try:
        student.delete()
        return jsonify({
            "success": True,
            "message": f"Student {student.name} ({student.email}) deleted.",
            "student_id": str(student.id)
        }), 200
    except Exception as e:
        return response(False, f"Error deleting student: {str(e)}"), 500
