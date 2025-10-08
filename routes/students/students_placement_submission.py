# blueprints/students_placement_submission.py
from flask import Blueprint, request, current_app
from datetime import datetime
from mongoengine import DoesNotExist, ValidationError

from utils.response import response
from utils.jwt import verify_access_token  # token_required uses this
from models.college import College
from models.students_placement_profile import StudentsPlacementProfile
from models.students_placement_submission import (
    StudentsPlacementSubmission,
    SectionResponse,
    FieldResponse,
)

# You can reuse the token_required decorator from your other blueprint.
def token_required(f):
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


students_placement_submission = Blueprint("students_placement_submission", __name__, url_prefix="/submissions")


@students_placement_submission.route("/forms/<form_id>", methods=["GET"])
@token_required
def get_or_create_submission(form_id):
    """
    Return the student's submission for the given form if it exists.
    Otherwise create (and save) a blank submission snapshot based on the form,
    and return that empty submission. Token must include college_id and student_id.
    """
    token_payload = getattr(request, "token_payload", {})
    college_id = token_payload.get("college_id")
    student_id = token_payload.get("student_id")

    if not college_id:
        return response(False, "`college_id` is required in token"), 400
    if not student_id:
        return response(False, "`student_id` is required in token"), 400

    # load college + form
    try:
        college = College.objects.get(id=college_id)
    except (DoesNotExist, ValidationError):
        return response(False, "College not found"), 404

    try:
        form = StudentsPlacementProfile.objects.get(id=form_id)
    except DoesNotExist:
        return response(False, "Form not found"), 404

    # ensure form belongs to the college (defensive)
    if getattr(form, "college", None) and str(form.college.id) != str(college.id):
        return response(False, "Form does not belong to this college"), 403

    # Try to find an existing submission for this student for this form + college
    submission = StudentsPlacementSubmission.objects(
        college=college, form=form, student_id=student_id
    ).first()

    if submission:
        # print('sub',submission.to_dict())
        # return existing data (to_dict)
        return response(True, "Submission fetched", data=submission.to_dict()), 200

    # If not found: create a fresh blank submission snapshot using the form structure
    try:
        submission = StudentsPlacementSubmission.create_from_form(form=form, student_id=student_id, college=college)
    except Exception as e:
        current_app.logger.exception("Failed to create blank submission from form")
        return response(False, "Failed to create blank submission"), 500

    return response(True, "Blank submission created", data=submission.to_dict()), 201


@students_placement_submission.route("/forms/<form_id>", methods=["POST"])
@token_required
def create_or_update_submission(form_id):
    """
    Create or update the student's submission for `form_id`.
    - Token must include college_id and student_id.
    - Body expected: { "sections": [ { "section_id": "...", "fields": [ { "field_id": "...", "value": ... , "verified": true/false? } ] } ] }
    Behavior:
      - If a submission exists for this student+form+college => update that submission in-place (preserve other field values not present in payload).
      - If no submission exists => create a new one and populate provided values.
    Validation:
      - Reject unknown section_id or field_id (must match the current form snapshot).
      - Requires form to be open (form.form_open == True).
    Returns the saved submission.to_dict().
    """
    payload = request.get_json() or {}
    token_payload = getattr(request, "token_payload", {})
    college_id = token_payload.get("college_id")
    student_id = token_payload.get("student_id")

    if not college_id:
        return response(False, "`college_id` is required in token"), 400
    if not student_id:
        return response(False, "`student_id` is required in token"), 400

    # load college + form
    try:
        college = College.objects.get(id=college_id)
    except (DoesNotExist, ValidationError):
        return response(False, "College not found"), 404

    try:
        form = StudentsPlacementProfile.objects.get(id=form_id)
    except DoesNotExist:
        return response(False, "Form not found"), 404

    if getattr(form, "college", None) and str(form.college.id) != str(college.id):
        return response(False, "Form does not belong to this college"), 403

    # ensure form is open for submission
    if not getattr(form, "form_open", False):
        return response(False, "Form is currently closed"), 403

    incoming_sections = payload.get("sections", [])
    if not isinstance(incoming_sections, list):
        return response(False, "Invalid payload: 'sections' must be a list"), 400

    # build lookups from form snapshot: section_id -> set(field_id)
    form_lookup = {}
    for sec in form.sections or []:
        form_lookup[sec.id] = {f.id for f in (sec.fields or [])}

    # validate incoming ids
    for sec in incoming_sections:
        sec_id = sec.get("section_id")
        if sec_id not in form_lookup:
            return response(False, f"Unknown section_id: {sec_id}"), 400
        fields = sec.get("fields", [])
        if not isinstance(fields, list):
            return response(False, f"Invalid 'fields' for section {sec_id}, must be a list"), 400
        for fld in fields:
            fid = fld.get("field_id")
            if fid not in form_lookup[sec_id]:
                return response(False, f"Unknown field_id '{fid}' in section '{sec_id}'"), 400

    # Find existing submission if present
    submission = StudentsPlacementSubmission.objects(
        college=college, form=form, student_id=student_id
    ).first()

    try:
        if submission is None:
            # create new blank submission snapshot and then populate
            submission = StudentsPlacementSubmission.create_from_form(form=form, student_id=student_id, college=college)
        else:
            # ensure submission is synced to the latest form structure before updating
            submission.sync_with_form(save=True)
    except Exception:
        current_app.logger.exception("Failed to prepare submission")
        return response(False, "Failed to prepare submission"), 500

    # Map submission sections for quick lookup
    sub_map = {s.section_id: s for s in (submission.sections or [])}

    # Apply incoming values (only for provided fields). Preserve other values.
    for sec in incoming_sections:
        sec_id = sec.get("section_id")
        sub_sec = sub_map.get(sec_id)
        if not sub_sec:
            # defensive: should not happen
            continue
        # map fields for quick lookup
        field_map = {f.field_id: f for f in (sub_sec.fields or [])}
        for fld in sec.get("fields", []):
            fid = fld.get("field_id")
            val = fld.get("value")
            if fid in field_map:
                # set value; allow explicit setting to None
                field_map[fid].value = val
                # optionally accept verified flag in payload
                if "verified" in fld:
                    field_map[fid].verified = bool(fld.get("verified"))
            else:
                # defensive: if the field does not exist (shouldn't happen), skip
                current_app.logger.debug(f"Field {fid} not found in submission section {sec_id}, skipping")

    # update timestamp and save
    submission.updated_at = datetime.utcnow()
    try:
        submission.save()
    except ValidationError as e:
        current_app.logger.exception("Submission validation failed")
        return response(False, "Submission validation failed"), 400
    except Exception:
        current_app.logger.exception("Failed to save submission")
        return response(False, "Failed to save submission"), 500

    return response(True, "Submission saved", data=submission.to_dict()), 200
