# routes/assignments.py  (add this to your file)
from flask import Blueprint, request, current_app as app
from mongoengine.errors import ValidationError, NotUniqueError, DoesNotExist
from bson import ObjectId
from functools import wraps

from utils.response import response
from utils.jwt import verify_access_token

from models.student import Student
from models.test.test import Test
from models.test.students_test_attempt import StudentTestAttempt 

assign_bp = Blueprint("assignments", __name__, url_prefix="/test/assignments")
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

# ... keep your token_required decorator above ...

@assign_bp.route("/bulk_assign", methods=["POST"])
@token_required
def bulk_assign():
    """
    Request JSON:
    {
      "test_id": "<test id>",
      "student_ids": ["id1", "id2", ...]   # list of student ids (strings)
    }

    Response: summary with created/skipped/errors per student_id
    """
    data = request.get_json(force=True, silent=True)
    if not data:
        return response(False, "Invalid or missing JSON body"), 400

    test_id = data.get("test_id")
    student_ids = data.get("student_ids")

    if not test_id:
        return response(False, "Missing required field: test_id"), 400
    if not student_ids:
        return response(False, "Missing required field: student_ids"), 400
    if not isinstance(student_ids, (list, tuple)):
        return response(False, "student_ids must be a list"), 400

    # normalize ids to strings
    student_ids = [str(s).strip() for s in student_ids if str(s).strip()]

    # Validate test exists
    try:
        # Try ObjectId then fallback to string id
        try:
            test_obj = Test.objects.get(id=ObjectId(test_id))
        except Exception:
            test_obj = Test.objects.get(id=str(test_id))
    except DoesNotExist:
        return response(False, f"Test not found for id: {test_id}"), 404
    except Exception as e:
        app.logger.exception("Error fetching Test")
        return response(False, f"Error validating test id: {str(e)}"), 400

    # Validate which students actually exist (optional, but helpful)
    found_students = set()
    try:
        students_qs = Student.objects(id__in=student_ids).only("id")
        found_students = {str(s.id) for s in students_qs}
    except Exception:
        # If Student model uses non-ObjectId keys or errors, fall back to checking one-by-one below.
        found_students = set()

    results = []
    created_count = 0
    skipped_count = 0
    error_count = 0

    for sid in student_ids:
        # If we validated students and this one wasn't found, mark error and continue
        if found_students and sid not in found_students:
            results.append({"student_id": sid, "status": "error", "reason": "student_not_found"})
            error_count += 1
            continue

        # Skip if already assigned
        try:
            already = StudentTestAttempt.objects(student_id=sid, test_id=str(test_obj.id)).first()
        except Exception:
            # fallback try using raw sid and test_id
            already = StudentTestAttempt.objects(student_id=sid, test_id=test_id).first()

        if already:
            results.append({"student_id": sid, "status": "skipped", "reason": "already_assigned"})
            skipped_count += 1
            continue

        # create assignment
        try:
            assign = StudentTestAttempt(student_id=sid, test_id=str(test_obj.id))
            assign.save()
            created_count += 1
            results.append({"student_id": sid, "status": "created", "id": str(assign.id)})
        except NotUniqueError:
            # concurrent create may cause duplicates; treat as skipped
            skipped_count += 1
            results.append({"student_id": sid, "status": "skipped", "reason": "not_unique"})
        except ValidationError as ve:
            error_count += 1
            results.append({"student_id": sid, "status": "error", "reason": f"validation_error: {ve}"} )
        except Exception as e:
            app.logger.exception("Error creating StudentTestAttempt for %s", sid)
            error_count += 1
            results.append({"student_id": sid, "status": "error", "reason": str(e)} )

    summary = {
        "test_id": str(test_obj.id),
        "requested": len(student_ids),
        "created": created_count,
        "skipped": skipped_count,
        "errors": error_count,
        "details": results,
    }

    return response(True, "bulk assign complete", summary), 200


from flask import Blueprint, request, current_app as app
from mongoengine.errors import ValidationError, DoesNotExist
from bson import ObjectId, errors as bson_errors
@assign_bp.route("/students/academic", methods=["GET"])
@token_required
def fetch_academic_students():
    """
    Query params:
      - search: string (matches name, usn, email, case-insensitive, partial)
      - semester: int or comma-separated ints
      - year_of_study: int or comma-separated ints
      - branch: string or comma-separated strings
      - page: int (default 1)
      - per_page: int (default 20)
      - sort_by: one of ('name','usn','year_of_study','semester','cgpa') default 'name'
      - sort_dir: 'asc' or 'desc' default 'asc'

    Only students from the admin's college (from request.admin['college_id']) are returned.
    Response contains:
      - items: list of academic objects (see below)
      - meta: distinct available_branches, available_years_of_study, available_semesters (for that college)
      - pagination: page, per_page, total, total_pages
    """
    # helper to get college id from admin payload
    def _get_admin_college_id():
        admin_payload = getattr(request, "admin", {}) or {}
        return admin_payload.get("college_id")

    try:
        # read query params
        q_search = request.args.get("search", "").strip()
        q_sem = request.args.get("semester")
        q_year = request.args.get("year_of_study")
        q_branch = request.args.get("branch")

        page = max(int(request.args.get("page", 1)), 1)
        per_page = min(max(int(request.args.get("per_page", 20)), 1), 200)
        sort_by = request.args.get("sort_by", "name")
        sort_dir = request.args.get("sort_dir", "asc").lower()
        sort_dir_prefix = "" if sort_dir == "asc" else "-"

    except Exception as e:
        return response(False, f"Invalid query parameters: {str(e)}"), 400

    # build base query: only students of admin's college
    college_id = _get_admin_college_id()
    if not college_id:
        return response(False, "Admin college_id not found in token payload"), 403

    query_filters = {}
    # try ObjectId or string for college reference
    try:
        query_filters["college"] = ObjectId(college_id)
    except (bson_errors.InvalidId, TypeError):
        # fallback to string id
        query_filters["college"] = str(college_id)

    # search: partial match on name, usn, email (case-insensitive)
    from mongoengine.queryset.visitor import Q
    q_obj = Q(**query_filters)
    if q_search:
        regex = r".*{}.*".format(q_search.replace(".", r"\."))
        q_obj &= (Q(name__icontains=q_search) | Q(usn__icontains=q_search) | Q(email__icontains=q_search))

    # filters: semester/year/branch (accept comma separated)
    def _split_vals(val):
        if val is None:
            return None
        parts = [v.strip() for v in str(val).split(",") if v.strip() != ""]
        return parts or None

    sem_vals = _split_vals(q_sem)
    if sem_vals:
        # convert to int if possible
        try:
            sem_ints = [int(x) for x in sem_vals]
            q_obj &= Q(semester__in=sem_ints)
        except ValueError:
            return response(False, "semester must be integer or comma-separated integers"), 400

    year_vals = _split_vals(q_year)
    if year_vals:
        try:
            year_ints = [int(x) for x in year_vals]
            q_obj &= Q(year_of_study__in=year_ints)
        except ValueError:
            return response(False, "year_of_study must be integer or comma-separated integers"), 400

    branch_vals = _split_vals(q_branch)
    if branch_vals:
        q_obj &= Q(branch__in=branch_vals)

    # determine sort field mapping to valid model fields
    allowed_sorts = {"name", "usn", "year_of_study", "semester", "cgpa"}
    if sort_by not in allowed_sorts:
        sort_by = "name"

    try:
        # total count for pagination
        total = Student.objects(q_obj).count()

        # pagination calculation
        skip = (page - 1) * per_page

        # fetch only academic fields to minimize payload
        students_qs = Student.objects(q_obj).only(
            "usn", "enrollment_number", "branch", "year_of_study",
            "semester", "cgpa", "college", "name", "email"
        ).order_by(f"{sort_dir_prefix}{sort_by}").skip(skip).limit(per_page)

        items = []
        for s in students_qs:
            # construct minimal academic dict
            items.append({
                "id": str(s.id),
                "name": s.name,
                "email": s.email,
                "usn": s.usn,
                "enrollment_number": s.enrollment_number,
                "branch": s.branch,
                "year_of_study": s.year_of_study,
                "semester": s.semester,
                "cgpa": s.cgpa,
                "college": str(s.college.id) if s.college else None
            })

        # meta: distinct values but only within this admin's college (and applied filters? user wanted distinct ones — we'll return distincts for the college scope, not further filtered subset)
        # Use the same college filter to fetch distincts
        distinct_base_filter = query_filters  # already filtered for college
        try:
            # Student.objects.distinct accepts field name; to scope by college, we filter first
            available_branches = Student.objects(**distinct_base_filter).distinct("branch") or []
            available_years = Student.objects(**distinct_base_filter).distinct("year_of_study") or []
            available_semesters = Student.objects(**distinct_base_filter).distinct("semester") or []
        except Exception:
            # fallback: try using string college id
            try:
                distinct_base_filter_alt = {"college": str(college_id)}
                available_branches = Student.objects(**distinct_base_filter_alt).distinct("branch") or []
                available_years = Student.objects(**distinct_base_filter_alt).distinct("year_of_study") or []
                available_semesters = Student.objects(**distinct_base_filter_alt).distinct("semester") or []
            except Exception:
                available_branches, available_years, available_semesters = [], [], []

        # sort distinct lists (years/semesters numerically, branches alphabetically)
        try:
            available_years = sorted([int(x) for x in available_years])
        except Exception:
            # if values are mixed or non-int, sort by natural order
            available_years = sorted(available_years)

        try:
            available_semesters = sorted([int(x) for x in available_semesters])
        except Exception:
            available_semesters = sorted(available_semesters)

        available_branches = sorted([b for b in available_branches if b is not None])

        total_pages = (total + per_page - 1) // per_page if per_page else 1

        payload = {
            "items": items,
            "meta": {
                "available_branches": available_branches,
                "available_years_of_study": available_years,
                "available_semesters": available_semesters
            },
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages
            }
        }

        return response(True, "students fetched", payload), 200

    except DoesNotExist:
        return response(False, "No students found"), 404
    except Exception as e:
        app.logger.exception("Error fetching academic students")
        return response(False, f"Error fetching students: {str(e)}"), 500
    
# --- Add these routes to routes/assignments.py ---

from bson import ObjectId, errors as bson_errors
from mongoengine.errors import DoesNotExist

# GET assigned students for a test (with same filtering / pagination / sorting as fetch_academic_students)
@assign_bp.route("/students/assigned", methods=["GET"])
@token_required
def fetch_assigned_students():
    """
    Query params (same semantics as fetch_academic_students):
      - test_id: required
      - search, semester, year_of_study, branch
      - page, per_page, sort_by, sort_dir
    Returns students who already have a StudentTestAttempt for that test (scoped to admin's college).
    """
    # helper to get college id from admin payload
    def _get_admin_college_id():
        admin_payload = getattr(request, "admin", {}) or {}
        return admin_payload.get("college_id")

    try:
        test_id = request.args.get("test_id")
        if not test_id:
            return response(False, "Missing required query param: test_id"), 400

        q_search = request.args.get("search", "").strip()
        q_sem = request.args.get("semester")
        q_year = request.args.get("year_of_study")
        q_branch = request.args.get("branch")

        page = max(int(request.args.get("page", 1)), 1)
        per_page = min(max(int(request.args.get("per_page", 20)), 1), 200)
        sort_by = request.args.get("sort_by", "name")
        sort_dir = request.args.get("sort_dir", "asc").lower()
        sort_dir_prefix = "" if sort_dir == "asc" else "-"
    except Exception as e:
        return response(False, f"Invalid query parameters: {str(e)}"), 400

    # validate test exists
    try:
        try:
            test_obj = Test.objects.get(id=ObjectId(test_id))
        except Exception:
            test_obj = Test.objects.get(id=str(test_id))
    except DoesNotExist:
        return response(False, f"Test not found for id: {test_id}"), 404
    except Exception as e:
        app.logger.exception("Error fetching Test")
        return response(False, f"Error validating test id: {str(e)}"), 400

    college_id = _get_admin_college_id()
    if not college_id:
        return response(False, "Admin college_id not found in token payload"), 403

    # get all assigned student ids for the test
    try:
        assigned_qs = StudentTestAttempt.objects(test_id=str(test_obj.id)).only("student_id")
        assigned_student_ids = {str(a.student_id) for a in assigned_qs if getattr(a, "student_id", None) is not None}
    except Exception:
        # fallback: try raw test_id
        try:
            assigned_qs = StudentTestAttempt.objects(test_id=test_id).only("student_id")
            assigned_student_ids = {str(a.student_id) for a in assigned_qs if getattr(a, "student_id", None) is not None}
        except Exception as e:
            app.logger.exception("Error fetching assigned attempts")
            return response(False, f"Error fetching assigned students: {str(e)}"), 500

    if not assigned_student_ids:
        # still return paginated empty payload (so frontends get meta)
        payload = {
            "items": [],
            "meta": {"available_branches": [], "available_years_of_study": [], "available_semesters": []},
            "pagination": {"page": page, "per_page": per_page, "total": 0, "total_pages": 0}
        }
        return response(True, "no assigned students found", payload), 200

    # build base query: only students of admin's college AND whose id is in assigned_student_ids
    from mongoengine.queryset.visitor import Q
    query_filters = {}
    try:
        query_filters["college"] = ObjectId(college_id)
    except (bson_errors.InvalidId, TypeError):
        query_filters["college"] = str(college_id)

    q_obj = Q(**query_filters) & Q(id__in=list(assigned_student_ids))

    # apply search filter
    if q_search:
        q_obj &= (Q(name__icontains=q_search) | Q(usn__icontains=q_search) | Q(email__icontains=q_search))

    # helper to split comma separated values
    def _split_vals(val):
        if val is None:
            return None
        parts = [v.strip() for v in str(val).split(",") if v.strip() != ""]
        return parts or None

    sem_vals = _split_vals(q_sem)
    if sem_vals:
        try:
            sem_ints = [int(x) for x in sem_vals]
            q_obj &= Q(semester__in=sem_ints)
        except ValueError:
            return response(False, "semester must be integer or comma-separated integers"), 400

    year_vals = _split_vals(q_year)
    if year_vals:
        try:
            year_ints = [int(x) for x in year_vals]
            q_obj &= Q(year_of_study__in=year_ints)
        except ValueError:
            return response(False, "year_of_study must be integer or comma-separated integers"), 400

    branch_vals = _split_vals(q_branch)
    if branch_vals:
        q_obj &= Q(branch__in=branch_vals)

    allowed_sorts = {"name", "usn", "year_of_study", "semester", "cgpa"}
    if sort_by not in allowed_sorts:
        sort_by = "name"

    try:
        total = Student.objects(q_obj).count()
        skip = (page - 1) * per_page
        students_qs = Student.objects(q_obj).only(
            "usn", "enrollment_number", "branch", "year_of_study",
            "semester", "cgpa", "college", "name", "email"
        ).order_by(f"{sort_dir_prefix}{sort_by}").skip(skip).limit(per_page)

        items = []
        for s in students_qs:
            items.append({
                "id": str(s.id),
                "name": s.name,
                "email": s.email,
                "usn": s.usn,
                "enrollment_number": s.enrollment_number,
                "branch": s.branch,
                "year_of_study": s.year_of_study,
                "semester": s.semester,
                "cgpa": s.cgpa,
                "college": str(s.college.id) if s.college else None
            })

        # meta: distincts within admin's college (same approach as fetch_academic_students)
        distinct_base_filter = query_filters
        try:
            available_branches = Student.objects(**distinct_base_filter).distinct("branch") or []
            available_years = Student.objects(**distinct_base_filter).distinct("year_of_study") or []
            available_semesters = Student.objects(**distinct_base_filter).distinct("semester") or []
        except Exception:
            try:
                distinct_base_filter_alt = {"college": str(college_id)}
                available_branches = Student.objects(**distinct_base_filter_alt).distinct("branch") or []
                available_years = Student.objects(**distinct_base_filter_alt).distinct("year_of_study") or []
                available_semesters = Student.objects(**distinct_base_filter_alt).distinct("semester") or []
            except Exception:
                available_branches, available_years, available_semesters = [], [], []

        try:
            available_years = sorted([int(x) for x in available_years])
        except Exception:
            available_years = sorted(available_years)
        try:
            available_semesters = sorted([int(x) for x in available_semesters])
        except Exception:
            available_semesters = sorted(available_semesters)
        available_branches = sorted([b for b in available_branches if b is not None])

        total_pages = (total + per_page - 1) // per_page if per_page else 1

        payload = {
            "items": items,
            "meta": {
                "available_branches": available_branches,
                "available_years_of_study": available_years,
                "available_semesters": available_semesters
            },
            "pagination": {"page": page, "per_page": per_page, "total": total, "total_pages": total_pages}
        }
        return response(True, "assigned students fetched", payload), 200

    except Exception as e:
        app.logger.exception("Error fetching assigned students")
        return response(False, f"Error fetching students: {str(e)}"), 500


# POST bulk unassign (remove assignments)
@assign_bp.route("/bulk_unassign", methods=["POST"])
@token_required
def bulk_unassign():
    """
    Request JSON:
    {
      "test_id": "<test id>",
      "student_ids": ["id1", "id2", ...]   # list of student ids (strings)
    }

    Response: summary with removed/skipped/errors per student_id
    """
    data = request.get_json(force=True, silent=True)
    if not data:
        return response(False, "Invalid or missing JSON body"), 400

    test_id = data.get("test_id")
    student_ids = data.get("student_ids")

    if not test_id:
        return response(False, "Missing required field: test_id"), 400
    if not student_ids:
        return response(False, "Missing required field: student_ids"), 400
    if not isinstance(student_ids, (list, tuple)):
        return response(False, "student_ids must be a list"), 400

    student_ids = [str(s).strip() for s in student_ids if str(s).strip()]

    # Validate test exists
    try:
        try:
            test_obj = Test.objects.get(id=ObjectId(test_id))
        except Exception:
            test_obj = Test.objects.get(id=str(test_id))
    except DoesNotExist:
        return response(False, f"Test not found for id: {test_id}"), 404
    except Exception as e:
        app.logger.exception("Error fetching Test")
        return response(False, f"Error validating test id: {str(e)}"), 400

    results = []
    removed_count = 0
    skipped_count = 0
    error_count = 0

    for sid in student_ids:
        try:
            # try delete by normalized test id
            deleted = StudentTestAttempt.objects(student_id=sid, test_id=str(test_obj.id)).delete()
        except Exception:
            # fallback try using raw test_id
            try:
                deleted = StudentTestAttempt.objects(student_id=sid, test_id=test_id).delete()
            except Exception as e:
                app.logger.exception("Error deleting attempt for %s", sid)
                error_count += 1
                results.append({"student_id": sid, "status": "error", "reason": str(e)})
                continue

        if deleted and deleted > 0:
            removed_count += 1
            results.append({"student_id": sid, "status": "removed", "deleted_count": deleted})
        else:
            skipped_count += 1
            results.append({"student_id": sid, "status": "skipped", "reason": "not_assigned"})

    summary = {
        "test_id": str(test_obj.id),
        "requested": len(student_ids),
        "removed": removed_count,
        "skipped": skipped_count,
        "errors": error_count,
        "details": results,
    }
    return response(True, "bulk unassign complete", summary), 200
