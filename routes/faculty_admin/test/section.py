# routes/test.py

from flask import Blueprint, request
from mongoengine.errors import ValidationError, NotUniqueError
from datetime import datetime

from utils.response import response
from utils.jwt import verify_access_token
from models.test.test import Test
from math import ceil
from mongoengine import Q
from datetime import datetime

test_bp = Blueprint("section", __name__, url_prefix="/tests")

# add these imports near top of your routes file
from models.test.section import Section
from models.test.test import Test
from mongoengine.errors import DoesNotExist, ValidationError


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
        return f(*args, **kwargs)

    return decorated


def parse_bool(val, default=False):
    """Utility to parse boolean-ish values from JSON payloads.
    Accepts actual bools, numbers (0/1), and strings "true"/"false","1","0","yes","no".
    """
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()
    if s in ("true", "1", "yes", "y", "t"):
        return True
    if s in ("false", "0", "no", "n", "f", ""):
        return False
    # fallback
    return default

# POST /tests/<test_id>/sections
# POST /<test_id>/sections
@test_bp.route("/<test_id>/sections", methods=["POST"])
@token_required
def add_section_to_test(test_id):
    """
    Create a Section and attach it to a Test.
    Body: {
      "name": "...",
      "description": "...",
      "instructions": "...",
      "time_restricted": true|false,
      "duration": 30   # minutes, required when time_restricted=true
    }
    """
    data = request.get_json() or {}
    name = data.get("name")
    if not name:
        return response(False, "Section 'name' is required"), 400

    time_restricted = bool(data.get("time_restricted", False))
    description = data.get("description", "")
    instructions = data.get("instructions", "")
# new boolean fields (default False)
    is_shuffle_question = parse_bool(data.get("is_shuffle_question", False))
    is_shuffle_options = parse_bool(data.get("is_shuffle_options", False))

    # parse duration if provided
    duration = data.get("duration", None)
    if duration is not None:
        try:
            duration = int(duration)
        except (ValueError, TypeError):
            return response(False, "Field 'duration' must be an integer (minutes)"), 400
        if duration < 0:
            return response(False, "Field 'duration' must be >= 0"), 400

    # if time_restricted, duration must be positive
    if time_restricted and (duration is None or duration <= 0):
        return response(False, "A positive 'duration' (minutes) is required when time_restricted is true"), 400

    # ensure test exists
    try:
        test = Test.objects.get(id=test_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Test not found"), 404

    # create section
    section = Section(
        name=name,
        description=description,
        instructions=instructions,
        time_restricted=time_restricted,
        duration=(duration or 0),
        is_shuffle_question=is_shuffle_question,
        is_shuffle_options=is_shuffle_options
    )
    try:
        section.save()
    except (ValidationError, ValueError) as e:
        return response(False, f"Error creating section: {str(e)}"), 400

    # attach reference to appropriate list on Test
    if time_restricted:
        test.sections_time_restricted = (test.sections_time_restricted or []) + [section]
    else:
        test.sections_open = (test.sections_open or []) + [section]

    try:
        test.save()
    except Exception as e:
        # rollback created section if attaching fails (best-effort)
        try:
            section.delete()
        except Exception:
            pass
        return response(False, f"Error attaching section to test: {str(e)}"), 500

    return response(True, "Section created and attached", section.to_json()), 201


# PUT /sections/<section_id>
@test_bp.route("/sections/<section_id>", methods=["PUT"])
@token_required
def update_section(section_id):
    """
    Update a Section. Body may include: name, description, instructions, time_restricted, duration.
    If time_restricted flips, move references on Tests accordingly.
    """
    data = request.get_json() or {}
    try:
        section = Section.objects.get(id=section_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Section not found"), 404

    updated = False
    old_time_restricted = bool(section.time_restricted)

    if "name" in data and data["name"] is not None:
        section.name = data["name"]
        updated = True
    if "description" in data:
        section.description = data["description"] or ""
        updated = True
    if "instructions" in data:
        section.instructions = data["instructions"] or ""
        updated = True
    if "time_restricted" in data:
        section.time_restricted = bool(data["time_restricted"])
        updated = True
 # new shuffle flags
    if "is_shuffle_question" in data:
        section.is_shuffle_question = parse_bool(data.get("is_shuffle_question"), default=bool(section.is_shuffle_question))
        updated = True
    if "is_shuffle_options" in data:
        section.is_shuffle_options = parse_bool(data.get("is_shuffle_options"), default=bool(section.is_shuffle_options))
        updated = True
    # duration handling: validate if provided
    if "duration" in data:
        dur_raw = data["duration"]
        # allow null/empty to mean 0
        if dur_raw is None or (isinstance(dur_raw, str) and dur_raw.strip() == ""):
            duration = 0
        else:
            try:
                duration = int(dur_raw)
            except (ValueError, TypeError):
                return response(False, "Field 'duration' must be an integer (minutes)"), 400
            if duration < 0:
                return response(False, "Field 'duration' must be >= 0"), 400
        section.duration = duration
        updated = True

    if not updated:
        return response(False, "No valid fields provided to update"), 400

    # If changing to time_restricted=True, ensure a positive duration exists (either provided just now or existing)
    new_time_restricted = bool(section.time_restricted)
    if new_time_restricted and (section.duration is None or int(section.duration) <= 0):
        # If the request included duration but it was invalid, we'd already have returned. Here check if missing.
        return response(False, "Cannot enable time_restricted without a positive 'duration' (minutes)"), 400

    try:
        section.save()
    except (ValidationError, ValueError) as e:
        return response(False, f"Error updating section: {str(e)}"), 400

    # If time_restricted changed, move references in Tests
    if old_time_restricted != new_time_restricted:
        try:
            if old_time_restricted:
                # was in time_restricted list, move to open
                tests_with_old = Test.objects(sections_time_restricted=section)
                for t in tests_with_old:
                    t.update(pull__sections_time_restricted=section)
                    t.update(push__sections_open=section)
            else:
                # was in open list, move to time_restricted
                tests_with_old = Test.objects(sections_open=section)
                for t in tests_with_old:
                    t.update(pull__sections_open=section)
                    t.update(push__sections_time_restricted=section)
        except Exception as e:
            # log and return partial success (section updated but moving refs failed)
            return response(False, f"Section updated but failed to move references: {str(e)}"), 500

    return response(True, "Section updated", section.to_json()), 200

# GET /tests/<test_id>/sections
@test_bp.route("/<test_id>/sections", methods=["GET"])
@token_required
def get_sections_by_test(test_id):
    """
    Return sections attached to a test, separated into time_restricted and open lists.
    Response data: { "sections_time_restricted": [...], "sections_open": [...] }
    """
    try:
        test = Test.objects.get(id=test_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Test not found"), 404

    # gather ids from test (may be empty)
    time_ids = [s.id for s in (test.sections_time_restricted or [])]
    open_ids = [s.id for s in (test.sections_open or [])]

    # fetch Section documents in two queries
    sections_time = list(Section.objects(id__in=time_ids)) if time_ids else []
    sections_open = list(Section.objects(id__in=open_ids)) if open_ids else []

    # convert to json
    data = {
        "test" : test.to_minimal_json(),
        "sections_time_restricted": [s.to_json() for s in sections_time],
        "sections_open": [s.to_json() for s in sections_open],
    }
    return response(True, "Sections fetched", data), 200


# Add these imports near top of your routes/test.py
from flask import jsonify
from mongoengine.errors import DoesNotExist, ValidationError
from models.test.section import Section, SectionQuestion  # Section & embedded wrapper
# Source MCQ: the 'questions' folder model (question bank)
from models.questions.mcq import MCQ as SourceMCQ
# Target MCQ: the test-specific MCQ model where duplicates should be stored
from models.test.questions.mcq import MCQ as TestMCQ, Option as TestOption



@test_bp.route("/sections/<section_id>/questions", methods=["GET"])
@token_required
def get_questions_by_section(section_id):
    """
    Fetch all questions for a given section.
    Returns a list of questions with their type and details.
    """
    try:
        section = Section.objects.get(id=section_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Section not found"), 404
    questions_data = []
    for sq in section.questions or []:
        q_type = sq.question_type
        q_obj = None

        if q_type == "mcq":
            try:
                # attempt to dereference (may raise DoesNotExist)
                if sq.mcq_ref:
                    q_obj = sq.mcq_ref.to_json()
            except DoesNotExist:
                # missing referenced TestMCQ — produce a placeholder instead of crashing
                # try to read the raw DBRef id if available
                raw = getattr(sq, "_data", {}).get("mcq_ref")
                ref_id = getattr(raw, "id", None) or str(raw) if raw else None
                q_obj = {"id": str(ref_id), "missing": True, "note": "Referenced test_mcq not found"}
        elif q_type == "coding":
            try:
                if sq.coding_ref:
                    q_obj = sq.coding_ref.to_safe_json()
            except DoesNotExist:
                q_obj = {"missing": True, "note": "Referenced coding question not found"}
        elif q_type == "rearrange":
            try:
                if sq.rearrange_ref:
                    q_obj = sq.rearrange_ref.to_json()
            except DoesNotExist:
                q_obj = {"missing": True, "note": "Referenced rearrange question not found"}

        if q_obj:
            questions_data.append({
                "type": q_type,
                "data": q_obj
            })

    return response(True, "Questions fetched", questions_data), 200
