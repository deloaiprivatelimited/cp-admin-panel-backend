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
student_test_bp = Blueprint("student_test_bp", __name__, url_prefix="/api/students")

@student_test_bp.route("/test/attempt/<test_id>", methods=["GET"])
@token_required
def get_test_by_id(test_id):
    """
    GET /api/students/tests/<test_id>
    Returns the student-facing test JSON ONLY if:
      - the calling student (from token) is assigned the test, and
      - the test is currently ongoing (start_datetime <= now <= end_datetime)

    Additional behavior:
      - If the student is retaking the test (either ?retake=true OR their attempt.submitted == True),
        clear previous answers and reset attempt fields so they start fresh.
    """
    payload = getattr(request, "token_payload", {}) or {}
    student_id = payload.get("student_id")
    if not student_id:
        return response(False, "token missing student_id"), 401

    # Normalize test id: try ObjectId then string fallback
    search_id = None
    try:
        search_id = ObjectId(str(test_id))
    except Exception:
        search_id = str(test_id)

    # Check assignment
    try:
        assigned = StudentTestAttempt.objects(
            student_id=str(student_id),
            test_id__in=[search_id, str(search_id)]
        ).first()
    except Exception as e:
        current_app.logger.exception("Error checking StudentTestAttempt: %s", e)
        return response(False, "error verifying assignment"), 500

    is_assigned = bool(assigned)

    # Fetch Test document
    try:
        try:
            test_doc = Test.objects.get(id=search_id)
        except (DoesNotExist, Exception):
            test_doc = Test.objects.get(id=str(test_id))
    except DoesNotExist:
        return response(False, "test not found"), 404
    except Exception as e:
        current_app.logger.exception("Error fetching test: %s", e)
        return response(False, "error fetching test"), 500

    now = datetime.utcnow()
    start = getattr(test_doc, "start_datetime", None)
    end = getattr(test_doc, "end_datetime", None)

    test_start_iso = start.isoformat() if start else None

    # Check ongoing
    is_ongoing = False
    if start and end:
        is_ongoing = (start <= now) and (now <= end)

    # If not assigned or not ongoing -> deny
    if not is_assigned or not is_ongoing:
        details = {
            "is_student_assigned": is_assigned,
            "is_test_ongoing": is_ongoing,
            "test_start_time": test_start_iso,
        }
        return response(False, "access to test denied", details), 403

    # --- RETAKE HANDLING ---
    # Conditions for retake:
    #  - explicit ?retake=true query param OR
    #  - existing attempt record was previously submitted (student completed earlier)
    # If retake, clear previous responses and reset attempt fields (but keep student_id & test_id)
    try:
        wants_retake = request.args.get("retake", "").lower() == "true"
        previously_submitted = bool(getattr(assigned, "submitted", False))
        if wants_retake or previously_submitted:
            # Clear previous answers / snapshots
            assigned.timed_section_answers = []
            assigned.open_section_answers = []
            # Reset timing/flags/marks so we treat this as a fresh attempt
            assigned.start_time = None
            assigned.last_autosave = None
            assigned.total_marks = 0.0
            assigned.max_marks = 0.0
            assigned.tab_switches_count = 0
            assigned.fullscreen_violated = False
            assigned.submitted = False
            assigned.submitted_at = None
            # persist
            assigned.save()
            current_app.logger.info("Cleared previous attempt for student %s test %s (retake=%s, prev_submitted=%s)",
                                    student_id, test_id, wants_retake, previously_submitted)
    except Exception as e:
        # If any error occurs while clearing, log but continue (so we don't block test fetch).
        current_app.logger.exception("Error clearing previous attempt for retake: %s", e)
        return response(False, "error resetting previous attempt"), 500

    # ✅ Update attempt start_time if empty (first fetch after reset or first ever)
    if assigned and not assigned.start_time:
        try:
            assigned.start_time = now
            # set last_autosave when starting
            assigned.last_autosave = now
            assigned.save()
        except Exception as e:
            current_app.logger.exception("Error updating attempt start_time: %s", e)
            # don't block test fetch — just continue

    # Serialize test for student
    try:
        test_json = test_doc.to_student_test_json(deterministic_shuffle=True)
    except Exception as e:
        current_app.logger.exception("Error serializing test for student: %s", e)
        return response(False, "error serializing test"), 500

    payload_out = {
        "test_assignment_id": str(assigned.id),
        "is_student_assigned": True,
        "test_start_time": test_start_iso,
        "attempt_start_time": assigned.start_time.isoformat() if assigned and assigned.start_time else None,
        "test": test_json,
    }

    return response(True, "test fetched", payload_out), 200



from flask import request, jsonify


@student_test_bp.route("/test/auto-save", methods=["POST"])
@token_required
def auto_save_test():
    payload = getattr(request, "token_payload", {}) or {}
    student_id = payload.get("student_id")

    data = request.get_json(silent=True) or {}
    # print(data)
    test_id = data.get("test_id")
    answers = data.get("answers", {})

    if not student_id or not test_id:
        return jsonify({"error": "missing_student_or_test"}), 400

    # get or create attempt
    attempt = StudentTestAttempt.objects(student_id=student_id, test_id=test_id).first()
    if not attempt:
        attempt = StudentTestAttempt(student_id=student_id, test_id=test_id, start_time=None)
        attempt.save()

    test_obj = Test.objects(id=test_id).first()
    if not test_obj:
        return jsonify({"error": "test_not_found"}), 404

    attempt.save_autosave(answers, test_obj)

    return jsonify({
        "status": "autosaved",
        "last_autosave": attempt.last_autosave.isoformat()
    }), 200

# routes/collegeadmin.py

from datetime import datetime

@student_test_bp.route("/test/submit", methods=["POST"])
@token_required
def submit_test():
    """
    POST /api/students/test/submit
    Body: {
      "test_id": "<test_id>",
      "answers": { ... }   # same structure as autosave
    }

    Marks the test attempt as submitted, saves answers, computes total marks.
    """
    payload = getattr(request, "token_payload", {}) or {}
    student_id = payload.get("student_id")

    data = request.get_json(silent=True) or {}
    test_id = data.get("test_id")
    answers = data.get("answers", {})

    if not student_id or not test_id:
        return response(False, "missing student_id or test_id"), 400

    attempt = StudentTestAttempt.objects(student_id=student_id, test_id=test_id).first()
    if not attempt:
        return response(False, "attempt not found"), 404

    # if attempt.submitted:
    #     return response(False, "test already submitted", {
    #         "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
    #         "total_marks": attempt.total_marks,
    #     }), 400

    test_obj = Test.objects(id=test_id).first()
    if not test_obj:
        return response(False, "test not found"), 404

    # Save latest answers
    attempt.save_autosave(answers, test_obj)

    # Mark as submitted
    attempt.submitted = True
    attempt.submitted_at = datetime.utcnow()
    attempt.total_marks = attempt.total_marks_obtained()
    attempt.save()

    result_payload = {
        "submitted_at": attempt.submitted_at.isoformat(),
        "total_marks": attempt.total_marks,
        "test_id": test_id,  
        "student_id": student_id,
    }

    return response(True, "test submitted successfully", result_payload), 200

# ------------------------------------------------------------------
# POST /api/students/test/tab-switch
# Increment tab switch counter; if threshold reached -> autosave + auto-submit
# ------------------------------------------------------------------
@student_test_bp.route("/test/tab-switch", methods=["POST"])
@token_required
def route_tab_switch():
    payload = getattr(request, "token_payload", {}) or {}
    student_id = payload.get("student_id")
    data = request.get_json(silent=True) or {}
    test_id = data.get("test_id")
    answers = data.get("answers", None)  # optional payload for autosave

    if not student_id or not test_id:
        return response(False, "missing student_id or test_id"), 400

    try:
        attempt = StudentTestAttempt.objects(student_id=str(student_id), test_id=str(test_id)).first()
    except Exception as e:
        current_app.logger.exception("Error fetching attempt for tab-switch: %s", e)
        return response(False, "error fetching attempt"), 500

    if not attempt:
        return response(False, "attempt not found"), 404

    # increment safely
    try:
        attempt.tab_switches_count = (int(getattr(attempt, "tab_switches_count", 0)) or 0) + 1
        attempt.last_autosave = datetime.utcnow()
        attempt.save()
    except Exception as e:
        current_app.logger.exception("Failed updating tab_switches_count: %s", e)
        return response(False, "error updating tab switch count"), 500

    # threshold: 5 (match frontend MAX_TAB_SWITCHES)
    THRESHOLD = 5
    auto_submitted = False
    try:
        print(attempt.tab_switches_count)
        if attempt.tab_switches_count >= THRESHOLD and not getattr(attempt, "submitted", False):
            # mark fullscreen_violated too, since excessive switching implies violation of proctoring rules
            attempt.fullscreen_violated = True

            # Try to autosave provided answers if present and Test exists (best-effort)
            if answers:
                try:
                    test_obj = Test.objects(id=str(test_id)).first()
                    if test_obj:
                        attempt.save_autosave(answers, test_obj)
                    else:
                        # fallback: call save_autosave without test_obj (it will try to resolve)
                        attempt.save_autosave(answers, None)
                except Exception:
                    current_app.logger.exception("Autosave during tab-switch auto-submit failed")

            # finalize submission
            attempt.submitted = True
            attempt.submitted_at = datetime.utcnow()
            try:
                attempt.total_marks = float(attempt.total_marks_obtained())
            except Exception:
                attempt.total_marks = getattr(attempt, "total_marks", 0.0) or 0.0
            attempt.save()
            auto_submitted = True
    except Exception as e:
        current_app.logger.exception("Error while handling threshold behaviour for tab-switch: %s", e)

    out = {
        "tab_switches_count": attempt.tab_switches_count,
        "fullscreen_violated": bool(attempt.fullscreen_violated),
        "last_autosave": attempt.last_autosave.isoformat() if attempt.last_autosave else None,
        "submitted": bool(attempt.submitted),
        "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        "auto_submitted": auto_submitted,
    }
    return response(True, "tab switch recorded", out), 200


# ------------------------------------------------------------------
# POST /api/students/test/fullscreen-violation
# Mark fullscreen_violated and autosave + auto-submit immediately (best-effort).
# Body: { test_id: "...", answers: { ... } }  (answers optional)
# ------------------------------------------------------------------
@student_test_bp.route("/test/fullscreen-violation", methods=["POST"])
@token_required
def route_fullscreen_violation():
    payload = getattr(request, "token_payload", {}) or {}
    student_id = payload.get("student_id")
    data = request.get_json(silent=True) or {}
    test_id = data.get("test_id")
    answers = data.get("answers", None)

    if not student_id or not test_id:
        return response(False, "missing student_id or test_id"), 400

    try:
        attempt = StudentTestAttempt.objects(student_id=str(student_id), test_id=str(test_id)).first()
    except Exception as e:
        current_app.logger.exception("Error fetching attempt for fullscreen-violation: %s", e)
        return response(False, "error fetching attempt"), 500

    if not attempt:
        return response(False, "attempt not found"), 404

    try:
        attempt.fullscreen_violated = True
        attempt.last_autosave = datetime.utcnow()

        # Autosave answers if provided
        if answers:
            try:
                test_obj = Test.objects(id=str(test_id)).first()
                if test_obj:
                    attempt.save_autosave(answers, test_obj)
                else:
                    attempt.save_autosave(answers, None)
            except Exception:
                current_app.logger.exception("Autosave during fullscreen-violation failed")

        # Immediately auto-submit (since fullscreen violation ends the test)
        if not getattr(attempt, "submitted", False):
            attempt.submitted = True
            attempt.submitted_at = datetime.utcnow()
            try:
                attempt.total_marks = float(attempt.total_marks_obtained())
            except Exception:
                attempt.total_marks = getattr(attempt, "total_marks", 0.0) or 0.0

        attempt.save()
    except Exception as e:
        current_app.logger.exception("Error processing fullscreen-violation: %s", e)
        return response(False, "error recording fullscreen violation"), 500

    out = {
        "fullscreen_violated": True,
        "last_autosave": attempt.last_autosave.isoformat() if attempt.last_autosave else None,
        "submitted": bool(attempt.submitted),
        "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        "total_marks": attempt.total_marks,
    }
    return response(True, "fullscreen violation recorded and attempt submitted", out), 200
