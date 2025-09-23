# routes/test.py

from flask import Blueprint, request
from mongoengine.errors import ValidationError, NotUniqueError, DoesNotExist
from datetime import datetime, timedelta
from utils.response import response
from utils.jwt import verify_access_token
from models.test.test import Test
from math import ceil
from mongoengine import Q
import re
from bson import ObjectId

test_bp = Blueprint("test", __name__, url_prefix="/tests")


def _parse_iso8601_duration(s: str) -> int:
    """Basic ISO 8601 duration parser supporting hours/minutes/seconds. Returns seconds or raises ValueError.
       Examples: 'PT2H30M', 'PT45M', 'PT3600S'"""
    if not s or not isinstance(s, str):
        raise ValueError("Invalid ISO 8601 duration")
    s = s.upper()
    m = re.match(r"^P(T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)$", s)
    if not m:
        raise ValueError("Unsupported ISO 8601 duration format")
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    seconds = int(m.group(4) or 0)
    return hours * 3600 + minutes * 60 + seconds


def _parse_hh_mm_ss(s: str) -> int:
    """Parse H:M:S, M:S or S into seconds. Raises ValueError on bad format."""
    parts = [p.strip() for p in s.split(":")]
    if not 1 <= len(parts) <= 3:
        raise ValueError("Bad time format")
    try:
        parts = [int(p) for p in parts]
    except Exception:
        raise ValueError("Bad numeric values in time")
    if len(parts) == 1:
        return parts[0]
    elif len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    else:
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds


def parse_duration_to_seconds(raw) -> int:
    """Accept several duration formats and return total seconds. Raises ValueError on failure.
       Supported: int seconds (e.g., 3600), numeric string '3600', 'HH:MM:SS', 'MM:SS', ISO 8601 'PT2H'."""
    if raw is None:
        raise ValueError("No duration provided")
    if isinstance(raw, int):
        if raw < 0:
            raise ValueError("Duration must be non-negative")
        return raw
    if isinstance(raw, str):
        raw_strip = raw.strip()
        # plain integer seconds
        if re.fullmatch(r"^\d+$", raw_strip):
            return int(raw_strip)
        # HH:MM:SS or MM:SS or H:MM
        if ":" in raw_strip:
            return _parse_hh_mm_ss(raw_strip)
        # ISO 8601 PT... form
        if raw_strip.upper().startswith("P"):
            return _parse_iso8601_duration(raw_strip)
    raise ValueError("Unsupported duration format")


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

        request.token_payload = payload
        request.admin = payload
        return f(*args, **kwargs)

    return decorated


def _parse_pagination_args():
    """Helper to parse page/per_page query params. Returns (page, per_page, error_message)"""
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        if page < 1 or per_page < 1:
            return None, None, "page and per_page must be positive integers"
    except ValueError:
        return None, None, "page and per_page must be integers"
    return page, per_page, None


def _apply_search(qs):
    """Apply search query 'q' filtering across test_name, description, instructions, notes."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return qs
    # Case-insensitive partial match on name/description/instructions/notes
    search_filter = (
        Q(test_name__icontains=q)
        | Q(description__icontains=q)
        | Q(instructions__icontains=q)
        | Q(notes__icontains=q)
    )
    return qs.filter(search_filter)


def _paginate_and_respond(qs, page, per_page, sort=None):
    """Apply sorting, pagination and return the response dict."""
    total = qs.count()
    if sort:
        qs = qs.order_by(sort)
    else:
        qs = qs.order_by("start_datetime")

    total_pages = ceil(total / per_page) if per_page else 1
    skip = (page - 1) * per_page
    qs = qs.skip(skip).limit(per_page)

    tests = [t.to_minimal_json() for t in qs]
    meta = {"total": total, "page": page, "per_page": per_page, "total_pages": total_pages}
    return response(True, "OK", data={"tests": tests, "meta": meta}), 200
# ---------- helpers ----------
def _get_admin_college_id():
    # print(admin_payload)
    admin_payload = getattr(request, "admin", {}) or {}
    print(admin_payload)
    return admin_payload.get("college_id")

def _ensure_same_college_or_forbid(obj_college_id):
    admin_college_id = _get_admin_college_id()
    if admin_college_id is None:
        return response(False, "Forbidden: admin has no college_id"), 403
    if str(admin_college_id) != str(obj_college_id):
        return response(False, "Forbidden: resource does not belong to your college"), 403
    return None


@test_bp.route("/add", methods=["POST"])
@token_required
def add_test():
    """
    POST /tests/add
    Protected route.

    Body (examples):
      {
        "test_name": "Algo test",
        "description": "...",
        "start_datetime": "2025-09-09T00:00:00",
        "end_datetime": "2025-09-15T23:59:59",
        "duration": "01:30:00",         # optional
        "instructions": "<p>...</p>",   # optional
        "notes": "Bring calculator",    # optional (NEW)
        "tags": ["python"]
      }

    Notes:
      - duration_seconds is required on the model and has a model-level default (3 hours).
        If the client does NOT provide a duration, we purposefully OMIT the field when
        constructing the Test so MongoEngine applies the model's default.
      - Because duration_seconds is required, do NOT pass duration_seconds=None to the model.
    """
    data = request.get_json() or {}

    test_name = data.get("test_name") or data.get("name")
    start_datetime = data.get("start_datetime") or data.get("startDateTime")
    end_datetime = data.get("end_datetime") or data.get("endDateTime")
    raw_duration = data.get("duration") or data.get("duration_seconds") or data.get("duration_hms")

    if not test_name or not start_datetime or not end_datetime:
        return response(False, "test_name, start_datetime and end_datetime are required"), 400

    # parse start/end (availability window)
    try:
        start = datetime.fromisoformat(start_datetime)
    except Exception:
        return response(False, "Invalid start_datetime format, use ISO 8601"), 400

    try:
        end = datetime.fromisoformat(end_datetime)
    except Exception:
        return response(False, "Invalid end_datetime format, use ISO 8601"), 400

    # Window sanity
    if start >= end:
        return response(False, "start_datetime must be earlier than end_datetime"), 400

    # parse optional duration if provided by frontend
    duration_seconds = None
    if raw_duration is not None:
        try:
            duration_seconds = parse_duration_to_seconds(raw_duration)
        except ValueError as e:
            return response(False, f"Invalid duration: {str(e)}"), 400
        if duration_seconds <= 0:
            return response(False, "duration must be greater than zero"), 400

    payload = getattr(request, "token_payload", {})
    created_by = {
        "id": payload.get("admin_id", "system"),
        "name": payload.get("role", "college_admin"),
    }
    # ✅ Fetch college_id from admin token or request
    college_id = payload.get("college_id") or _get_admin_college_id()
    if not college_id:
        return response(False, "college_id is required"), 400

    # Build kwargs to pass to Test - omit duration_seconds if frontend didn't provide it so model uses default
    test_kwargs = dict(
        test_name=test_name,
        description=data.get("description"),
        start_datetime=start,
        end_datetime=end,
        instructions=data.get("instructions"),
        notes=data.get("notes"),  # NEW: store separate notes
        tags=data.get("tags", []),
        created_by=created_by,
                college=str(college_id),   # ✅ Added

    )
    if duration_seconds is not None:
        test_kwargs["duration_seconds"] = duration_seconds

    test = Test(**test_kwargs)

    # validation & save (clean enforces start < end; model will fill default duration if omitted)
    try:
        test.clean()
    except ValueError as e:
        return response(False, f"Validation error: {str(e)}"), 400
    except ValidationError as e:
        return response(False, f"Validation error: {str(e)}"), 400

    try:
        test.save()
    except (ValidationError, NotUniqueError, ValueError) as e:
        return response(False, f"Error saving test: {str(e)}"), 400
    except Exception as e:
        return response(False, f"Unexpected error saving test: {str(e)}"), 500

    # Build response JSON and include exactly what was stored
    try:
        test_json = test.to_json()
    except Exception:
        test_json = {
            "id": str(test.id),
            "test_name": test.test_name,
            "start_datetime": test.start_datetime.isoformat() if test.start_datetime else None,
            "end_datetime": test.end_datetime.isoformat() if test.end_datetime else None,
            "duration_seconds": int(test.duration_seconds) if test.duration_seconds is not None else None,
            "notes": test.notes if hasattr(test, "notes") else None,
        }

    # Attach human readable duration
    test_json["duration_hms"] = (
        str(timedelta(seconds=int(test.duration_seconds))) if test.duration_seconds is not None else None
    )

    return response(True, "Test created successfully", data=test_json), 201



# bind same function to multiple routes
@test_bp.route("", methods=["GET"])
@test_bp.route("/past", methods=["GET"])
@test_bp.route("/ongoing", methods=["GET"])
@test_bp.route("/upcoming", methods=["GET"])
@token_required
def get_tests_merged():
    """
    GET /tests, /tests/past, /tests/ongoing, /tests/upcoming
    Same query params as before (q, page, per_page, sort). If a `when` query param
    is present it overrides the path-derived mode.

    This version enforces college scoping: admins only see tests that belong to their college.
    """
    page, per_page, err = _parse_pagination_args()
    if err:
        return response(False, err), 400

    # fetch admin college and require it
    admin_college_id = _get_admin_college_id()
    if admin_college_id is None:
        return response(False, "Forbidden: admin has no college_id"), 403
    college_filter = {"college": str(admin_college_id)}

    # mode precedence: explicit ?when=... else derived from the path
    when = request.args.get("when")
    if when:
        when = when.lower()
    else:
        path = request.path.rstrip("/")  # e.g. "/tests/past" or "/tests"
        if path.endswith("/past"):
            when = "past"
        elif path.endswith("/ongoing"):
            when = "ongoing"
        elif path.endswith("/upcoming"):
            when = "upcoming"
        else:
            when = "all"

    now = datetime.utcnow()
    if when == "all":
        qs = Test.objects(**college_filter)
        default_sort = None
    elif when == "past":
        qs = Test.objects(end_datetime__lt=now, **college_filter)
        default_sort = "-end_datetime"
    elif when == "ongoing":
        qs = Test.objects(start_datetime__lte=now, end_datetime__gte=now, **college_filter)
        default_sort = "start_datetime"
    elif when == "upcoming":
        qs = Test.objects(start_datetime__gt=now, **college_filter)
        default_sort = "start_datetime"
    else:
        return response(False, f"invalid 'when' value: {when}"), 400

    qs = _apply_search(qs)
    sort = request.args.get("sort") or default_sort
    return _paginate_and_respond(qs, page, per_page, sort)
# GET /tests/<id>
@test_bp.route("/<test_id>", methods=["GET"])
@token_required
def get_test(test_id):
    """
    GET /tests/<test_id>
    Validates ID, enforces college scoping, returns test.to_json()
    """
    # validate ObjectId-ish string early to avoid weird mongoengine errors
    try:
        # allow both ObjectId and string ids; ObjectId() will raise if invalid format
        ObjectId(str(test_id))
    except Exception:
        return response(False, "Test not found"), 404

    try:
        test = Test.objects.get(id=test_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Test not found"), 404

    # enforce that admin belongs to same college as the test
    ensure_err = _ensure_same_college_or_forbid(getattr(test, "college", None))
    if ensure_err is not None:
        return ensure_err

    # include human-readable duration_hms like other endpoints expect
    result = test.to_json()
    try:
        result["duration_hms"] = (
            str(timedelta(seconds=int(test.duration_seconds))) if getattr(test, "duration_seconds", None) is not None else None
        )
    except Exception:
        result["duration_hms"] = None

    return response(True, "Test fetched", result), 200


# PUT /tests/<id>
@test_bp.route("/<test_id>", methods=["PUT"])
@token_required
def update_test(test_id):
    """
    PUT /tests/<test_id>
    Body may include any of:
      "test_name", "description", "start_datetime", "end_datetime",
      "instructions", "notes", "tags", "duration" (or duration_seconds / duration_hms)

    Behavior:
      - duration is independent of start/end. If provided it will be stored as duration_seconds.
      - Because duration_seconds is required at the model level, the API DOES NOT allow removing duration
        by sending null. To remove/changing that requirement you'd need to change the model.
    """
    data = request.get_json() or {}
    try:
        test = Test.objects.get(id=test_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Test not found"), 404

    # Only allow updating safe fields
    allowed_fields = {
        "test_name",
        "description",
        "start_datetime",
        "end_datetime",
        "instructions",
        "notes",
        "tags",
        "duration",
        "duration_seconds",
        "duration_hms",
    }
    updated = False

    # handle datetimes if provided
    if "start_datetime" in data and data["start_datetime"] is not None:
        try:
            test.start_datetime = datetime.fromisoformat(data["start_datetime"])
            updated = True
        except Exception:
            return response(False, "Invalid start_datetime format, use ISO 8601"), 400

    if "end_datetime" in data and data["end_datetime"] is not None:
        try:
            test.end_datetime = datetime.fromisoformat(data["end_datetime"])
            updated = True
        except Exception:
            return response(False, "Invalid end_datetime format, use ISO 8601"), 400

    # update simple string/list fields including the new 'notes'
    for key in ("test_name", "description", "instructions", "notes", "tags"):
        if key in data:
            # prevent unallowed removal of required duration, but notes can be set to None/empty if client wants
            setattr(test, key, data.get(key))
            updated = True

    # handle duration separately
    if "duration" in data or "duration_seconds" in data or "duration_hms" in data:
        # prefer explicit keys in this order: duration -> duration_seconds -> duration_hms
        if "duration" in data:
            raw_duration = data.get("duration")
        elif "duration_seconds" in data:
            raw_duration = data.get("duration_seconds")
        else:
            raw_duration = data.get("duration_hms")

        # Because duration_seconds is required at the model level, we DO NOT allow the client to remove it by setting null.
        if raw_duration is None:
            return response(False, "Removing duration is not allowed (duration_seconds is required)"), 400

        # parse and store seconds
        try:
            parsed = parse_duration_to_seconds(raw_duration)
        except ValueError as e:
            return response(False, f"Invalid duration: {str(e)}"), 400
        if parsed <= 0:
            return response(False, "duration must be greater than zero"), 400
        test.duration_seconds = parsed
        updated = True

    if not updated:
        return response(False, "No valid fields provided to update"), 400

    # Optionally record who updated it (not overwriting created_by)
    payload = getattr(request, "token_payload", {})
    updated_by = {"id": payload.get("admin_id", "system"), "name": payload.get("role", "college_admin")}
    # (You can store/update an audit trail here if desired)

    try:
        test.save()
    except (ValidationError, NotUniqueError, ValueError) as e:
        return response(False, f"Error updating test: {str(e)}"), 400
    except Exception as e:
        return response(False, f"Unexpected error updating test: {str(e)}"), 500

    # ensure response includes human readable duration if present
    result = test.to_json()
    result["duration_hms"] = (
        str(timedelta(seconds=int(test.duration_seconds))) if getattr(test, "duration_seconds", None) is not None else None
    )

    return response(True, "Test updated", result), 200


# DELETE /tests/<id>
@test_bp.route("/<test_id>", methods=["DELETE"])
@token_required
def delete_test(test_id):
    """
    DELETE /tests/<test_id>
    """
    try:
        test = Test.objects.get(id=test_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Test not found"), 404

    try:
        test.delete()
    except Exception as e:
        return response(False, f"Error deleting test: {str(e)}"), 400

    return response(True, "Test deleted"), 200
