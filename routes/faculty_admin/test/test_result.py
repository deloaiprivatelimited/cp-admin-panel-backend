from datetime import datetime
from bson import ObjectId
from mongoengine.errors import DoesNotExist
# routes/collegeadmin.py
from flask import Blueprint, request, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from mongoengine.errors import DoesNotExist
from utils.jwt import create_access_token, verify_access_token
from utils.response import response
from models.test.test import Test
from models.test.students_test_attempt import StudentTestAttempt

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

        request.token_payload = payload
        request.admin = payload
        return f(*args, **kwargs)

    return decorated


faculty_test_result_bp = Blueprint("faculty_test_result_bp", __name__, url_prefix="/api/faculty/test/results")

@faculty_test_result_bp.route("/results", methods=["GET"])
@token_required
def list_student_results_for_test():
    """
    GET /api/students/results?test_id=<id>&search=<name|email>&limit=&offset=&sort_by=&order=
    - test_id is MANDATORY.
    - Returns attempts for that test only. Optional search filters students by name/email.
    """
    from models.student import Student

    test_id = (request.args.get("test_id") or "").strip()
    print(test_id)
    if not test_id:
        return response(False, "test_id is required"), 400

    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return response(False, "limit and offset must be integers"), 400

    search = (request.args.get("search") or "").strip()
    sort_by = (request.args.get("sort_by") or "submitted_at").strip()
    allowed_sorts = {"submitted_at", "last_autosave", "total_marks"}
    if sort_by not in allowed_sorts:
        sort_by = "submitted_at"
    order = (request.args.get("order") or "desc").strip().lower()
    order_prefix = "-" if order == "desc" else "+"

    # Build base query: filter only by test_id
    query = Q(test_id=str(test_id))

    # If search provided, find student ids matching name/email, then restrict attempts to those students
    if search:
        try:
            students_qs = Student.objects(Q(name__icontains=search) | Q(email__icontains=search)).only("id")
            student_ids = [str(s.id) for s in students_qs]
        except Exception as e:
            current_app.logger.exception("Error searching students: %s", e)
            return response(False, "error searching students"), 500

        # if no students match search -> empty result set
        if not student_ids:
            return response(True, "results fetched", {"results": [], "total": 0, "limit": limit, "offset": offset}), 200

        query &= Q(student_id__in=student_ids)

    # fetch attempts
    try:
        total = StudentTestAttempt.objects(query).count()
        attempts_qs = (
            StudentTestAttempt.objects(query)
            .order_by(f"{order_prefix}{sort_by}")
            .skip(max(offset, 0))
            .limit(max(limit, 1))
        )
    except Exception as e:
        current_app.logger.exception("Error querying StudentTestAttempt: %s", e)
        return response(False, "error fetching results"), 500

    # fetch test meta (name + description)
    test_meta = None
    try:
        t = Test.objects(id=str(test_id)).first()
        if t:
            test_meta = {
                "id": str(t.id),
                "test_name": t.test_name,
                "description": t.description,
            }
    except Exception:
        test_meta = None

    # collect student_ids from attempts to fetch student details in one shot
    attempt_student_ids = {str(getattr(a, "student_id", "")) for a in attempts_qs if getattr(a, "student_id", None)}
    student_map = {}
    if attempt_student_ids:
        try:
            students_for_attempts = Student.objects(id__in=list(attempt_student_ids)).only("id", "name", "email")
            for s in students_for_attempts:
                student_map[str(s.id)] = {"id": str(s.id), "name": getattr(s, "name", ""), "email": getattr(s, "email", "")}
        except Exception as e:
            current_app.logger.exception("Error fetching students for attempts: %s", e)
            # Continue — we can still return attempts without detailed student info
            student_map = {}

    results = []
    # summary accumulators
    total_tab_switches = 0
    max_tab_switches = 0
    attempts_with_nonzero_tab_switches = 0
    total_violations = 0
    attempts_with_violations = 0

    for a in attempts_qs:
        sid = str(getattr(a, "student_id", "") or "")
        student_info = student_map.get(sid, None)

        # Best-effort extraction of full-screen / tab-switch / violations info (safe fallbacks)
        full_screen = bool(getattr(a, "full_screen", None) or getattr(a, "is_fullscreen", None) or False)

        # tab switch count: prefer integer field, else length of list-type fields if present
        tab_switch_count = 0
        ts_val = getattr(a, "tab_switch_count", None)
        if ts_val is None:
            # alternative candidate names
            ts_list = getattr(a, "tab_switches", None) or getattr(a, "tab_focus_events", None)
            if ts_list is None:
                ts_list = getattr(a, "tabs", None)  # fallback
            if isinstance(ts_list, (list, tuple)):
                tab_switch_count = len(ts_list)
            else:
                # try numeric fallback
                try:
                    tab_switch_count = int(getattr(a, "tab_switches_count", 0) or 0)
                except Exception:
                    tab_switch_count = 0
        else:
            try:
                tab_switch_count = int(ts_val or 0)
            except Exception:
                tab_switch_count = 0

        # violations: try multiple shapes
        violations_field = getattr(a, "violations", None)
        violation_count = 0
        violations_list = []
        if violations_field is None:
            # try alternate fields
            v_count = getattr(a, "violation_count", None) or getattr(a, "violations_count", None)
            if v_count:
                try:
                    violation_count = int(v_count)
                except Exception:
                    violation_count = 0
            else:
                # maybe a boolean flag
                if getattr(a, "cheating_detected", False):
                    violation_count = 1
                    violations_list = [{"type": "cheating_detected"}]
        else:
            # if it's a number
            if isinstance(violations_field, (int, float)):
                violation_count = int(violations_field)
            # if it's a list/seq then length
            elif isinstance(violations_field, (list, tuple)):
                violation_count = len(violations_field)
                violations_list = list(violations_field)
            # if some other structure, try to read .count or .get
            else:
                try:
                    violation_count = int(getattr(violations_field, "count", 0) or 0)
                except Exception:
                    violation_count = 0

        # update summary accumulators
        total_tab_switches += tab_switch_count
        if tab_switch_count > max_tab_switches:
            max_tab_switches = tab_switch_count
        if tab_switch_count > 0:
            attempts_with_nonzero_tab_switches += 1

        total_violations += violation_count
        if violation_count > 0:
            attempts_with_violations += 1

        results.append({
            "id": str(getattr(a, "id", "")),
            "student_id": sid,
            "student": student_info,

            "test_id": str(getattr(a, "test_id", "")),
            "total_marks": float(getattr(a, "total_marks", 0) or 0),
            "max_marks": float(getattr(a, "max_marks", 0) or 0),

            "submitted": bool(getattr(a, "submitted", False)),
            "submitted_at": getattr(a, "submitted_at", None),
            "last_autosave": getattr(a, "last_autosave", None),

            # UI / session telemetry (best-effort)
            "full_screen": full_screen,
            "tab_switch_count": tab_switch_count,
            "violations": violations_list,
            "violation_count": violation_count,
        })

    # Build summary objects for tabs + violations so frontend can display tabs with counts
    tabs_summary = {
        "total_tab_switches": total_tab_switches,
        "avg_tab_switches_per_attempt": (total_tab_switches / len(results)) if results else 0,
        "max_tab_switches": max_tab_switches,
        "attempts_with_tab_switches": attempts_with_nonzero_tab_switches,
        "attempts_with_tab_switches_percent": (attempts_with_nonzero_tab_switches / len(results) * 100) if results else 0,
    }

    violation_summary = {
        "total_violations": total_violations,
        "attempts_with_violations": attempts_with_violations,
        "attempts_with_violations_percent": (attempts_with_violations / len(results) * 100) if results else 0,
    }

    return response(
        True,
        "results fetched",
        {
            "test": test_meta,
            "results": results,
            "total": total,
            "limit": limit,
            "offset": offset,
            "tabs_summary": tabs_summary,
            "violation_summary": violation_summary,
        },
    ), 200

@faculty_test_result_bp.route("/<student_id>/results", methods=["GET"])
@token_required
def get_results_by_student_for_test(student_id):
    """
    GET /api/students/<student_id>/results?test_id=<id>&limit=&offset=&include_snapshots=(true|false)
    - test_id is MANDATORY.
    - Returns attempts for that student for the given test (paginated).
    - include_snapshots: whether to include section snapshots and question snapshots (defaults to true).
    """
    test_id = (request.args.get("test_id") or "").strip()
    print(test_id)
    if not test_id:
        return response(False, "test_id is required"), 400

    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return response(False, "limit and offset must be integers"), 400

    # boolean flag (default true). Faculty/admin can request snapshots.
    include_snapshots = str(request.args.get("include_snapshots", "true")).lower() not in ("0", "false", "no")

    # Validate student exists
    from models.student import Student
    try:
        student = Student.objects.get(id=str(student_id))
    except DoesNotExist:
        return response(False, "student not found"), 404
    except Exception as e:
        current_app.logger.exception("error fetching student: %s", e)
        return response(False, "error fetching student"), 500

    # Filter only by student_id + test_id
    query = Q(student_id=str(student_id)) & Q(test_id=str(test_id))

    try:
        total = StudentTestAttempt.objects(query).count()
        attempts_qs = (
            StudentTestAttempt.objects(query)
            .order_by("-submitted_at")
            .skip(max(offset, 0))
            .limit(max(min(limit, 500), 1))  # hard cap of 500 by default to avoid huge responses
        )
    except Exception as e:
        current_app.logger.exception("Error querying StudentTestAttempt: %s", e)
        return response(False, "error fetching results"), 500

    # Optionally include test meta
    from models.test.test import Test
    test_meta = None
    try:
        t = Test.objects(id=str(test_id)).first()
        if t:
            test_meta = t.to_minimal_json()
    except Exception:
        test_meta = None

    # Helper: serialize embedded snapshot objects safely to plain dicts
    def _mcq_snapshot_to_dict(snap):
        if not snap:
            return None
        return {
            "question_id": getattr(snap, "question_id", None),
            "title": getattr(snap, "title", None),
            "question_text": getattr(snap, "question_text", None),
            "options": getattr(snap, "options", []) or [],
            "is_multiple": bool(getattr(snap, "is_multiple", False)),
            "marks": float(getattr(snap, "marks", 0) or 0.0),
            "negative_marks": float(getattr(snap, "negative_marks", 0) or 0.0),
            "correct_options": getattr(snap, "correct_options", []) or [],    # faculty view includes corrects
            "explanation": getattr(snap, "explanation", None),
        }

    def _rearrange_snapshot_to_dict(snap):
        if not snap:
            return None
        return {
            "question_id": getattr(snap, "question_id", None),
            "title": getattr(snap, "title", None),
            "prompt": getattr(snap, "prompt", None),
            "items": getattr(snap, "items", []) or [],
            "is_drag_and_drop": bool(getattr(snap, "is_drag_and_drop", True)),
            "marks": float(getattr(snap, "marks", 0) or 0.0),
            "negative_marks": float(getattr(snap, "negative_marks", 0) or 0.0),
            "correct_order": getattr(snap, "correct_order", []) or [],      # faculty view includes correct order
            "explanation": getattr(snap, "explanation", None),
        }

    def _coding_snapshot_to_dict(snap):
        if not snap:
            return None
        return {
            "question_id": getattr(snap, "question_id", None),
            "title": getattr(snap, "title", None),
            "short_description": getattr(snap, "short_description", None),
            "long_description_markdown": getattr(snap, "long_description_markdown", None),
            "sample_io": getattr(snap, "sample_io", []) or [],
            "allowed_languages": getattr(snap, "allowed_languages", []) or [],
            "predefined_boilerplates": getattr(snap, "predefined_boilerplates", {}) or {},
            "run_code_enabled": bool(getattr(snap, "run_code_enabled", True)),
            "submission_enabled": bool(getattr(snap, "submission_enabled", True)),
            "marks": float(getattr(snap, "marks", 0) or 0.0),
            "negative_marks": float(getattr(snap, "negative_marks", 0) or 0.0),
        }

    def _student_answer_to_dict(ans):
        if not ans:
            return None
        base = {
            "question_id": getattr(ans, "question_id", None),
            "question_type": getattr(ans, "question_type", None),
            "value": getattr(ans, "value", None),
            "marks_obtained": None if getattr(ans, "marks_obtained", None) is None else float(ans.marks_obtained),
        }
        # include snapshots only when requested
        if include_snapshots:
            if getattr(ans, "snapshot_mcq", None):
                base["snapshot_mcq"] = _mcq_snapshot_to_dict(ans.snapshot_mcq)
            if getattr(ans, "snapshot_rearrange", None):
                base["snapshot_rearrange"] = _rearrange_snapshot_to_dict(ans.snapshot_rearrange)
            if getattr(ans, "snapshot_coding", None):
                base["snapshot_coding"] = _coding_snapshot_to_dict(ans.snapshot_coding)
        return base

    def _section_answers_to_dict(sec):
        if not sec:
            return None
        return {
            "section_id": getattr(sec, "section_id", None),
            "section_name": getattr(sec, "section_name", None),
            "section_duration": int(getattr(sec, "section_duration", 0) or 0),
            "answers": [ _student_answer_to_dict(a) for a in (getattr(sec, "answers", []) or []) ],
        }

    results = []
    # summary accumulators
    total_tab_switches = 0
    max_tab_switches = 0
    attempts_with_nonzero_tab_switches = 0
    total_violations = 0
    attempts_with_violations = 0

    for a in attempts_qs:
        item = {
            "id": str(getattr(a, "id", "")),
            "student_id": str(getattr(a, "student_id", "")),
            "test_id": str(getattr(a, "test_id", "")),
            "test": test_meta,
            "total_marks": float(getattr(a, "total_marks", 0) or 0),
            "max_marks": float(getattr(a, "max_marks", 0) or 0),

            "submitted": bool(getattr(a, "submitted", False)),
            "submitted_at": getattr(a, "submitted_at", None),
            "last_autosave": getattr(a, "last_autosave", None),
        }
        print(item['max_marks'])

        # Best-effort extraction of full-screen / tab-switch / violations info (safe fallbacks)
        full_screen = bool(getattr(a, "full_screen", None) or getattr(a, "is_fullscreen", None) or False)

        tab_switch_count = 0
        ts_val = getattr(a, "tab_switch_count", None)
        if ts_val is None:
            ts_list = getattr(a, "tab_switches", None) or getattr(a, "tab_focus_events", None)
            if ts_list is None:
                ts_list = getattr(a, "tabs", None)
            if isinstance(ts_list, (list, tuple)):
                tab_switch_count = len(ts_list)
            else:
                try:
                    tab_switch_count = int(getattr(a, "tab_switches_count", 0) or 0)
                except Exception:
                    tab_switch_count = 0
        else:
            try:
                tab_switch_count = int(ts_val or 0)
            except Exception:
                tab_switch_count = 0

        violations_field = getattr(a, "violations", None)
        violation_count = 0
        violations_list = []
        if violations_field is None:
            v_count = getattr(a, "violation_count", None) or getattr(a, "violations_count", None)
            if v_count:
                try:
                    violation_count = int(v_count)
                except Exception:
                    violation_count = 0
            else:
                if getattr(a, "cheating_detected", False):
                    violation_count = 1
                    violations_list = [{"type": "cheating_detected"}]
        else:
            if isinstance(violations_field, (int, float)):
                violation_count = int(violations_field)
            elif isinstance(violations_field, (list, tuple)):
                violation_count = len(violations_field)
                violations_list = list(violations_field)
            else:
                try:
                    violation_count = int(getattr(violations_field, "count", 0) or 0)
                except Exception:
                    violation_count = 0

        # include snapshots when requested
        if include_snapshots:
            try:
                item["timed_section_answers"] = [
                    _section_answers_to_dict(s) for s in (getattr(a, "timed_section_answers", []) or [])
                ]
                item["open_section_answers"] = [
                    _section_answers_to_dict(s) for s in (getattr(a, "open_section_answers", []) or [])
                ]
            except Exception as e:
                # avoid total failure if unexpected structure; log and continue
                current_app.logger.exception("error serializing snapshots for attempt %s: %s", getattr(a, "id", None), e)
                item["timed_section_answers"] = []
                item["open_section_answers"] = []

        # attach ui/telemetry fields
        item["full_screen"] = full_screen
        item["tab_switch_count"] = tab_switch_count
        item["violations"] = violations_list
        item["violation_count"] = violation_count

        # update summary accumulators
        total_tab_switches += tab_switch_count
        if tab_switch_count > max_tab_switches:
            max_tab_switches = tab_switch_count
        if tab_switch_count > 0:
            attempts_with_nonzero_tab_switches += 1

        total_violations += violation_count
        if violation_count > 0:
            attempts_with_violations += 1

        results.append(item)

    tabs_summary = {
        "total_tab_switches": total_tab_switches,
        "avg_tab_switches_per_attempt": (total_tab_switches / len(results)) if results else 0,
        "max_tab_switches": max_tab_switches,
        "attempts_with_tab_switches": attempts_with_nonzero_tab_switches,
        "attempts_with_tab_switches_percent": (attempts_with_nonzero_tab_switches / len(results) * 100) if results else 0,
    }

    violation_summary = {
        "total_violations": total_violations,
        "attempts_with_violations": attempts_with_violations,
        "attempts_with_violations_percent": (attempts_with_violations / len(results) * 100) if results else 0,
    }

    return response(True, "student results fetched", {
        "student": {"id": str(student.id), "name": getattr(student, "name", None), "email": getattr(student, "email", None)},
        "test_id": str(test_id),
        "results": results,
        "total": total,
        "limit": limit,
        "offset": offset,
        "tabs_summary": tabs_summary,
        "violation_summary": violation_summary
    }), 200
