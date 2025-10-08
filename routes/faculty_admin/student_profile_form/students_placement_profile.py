from flask import Blueprint, request, current_app
from mongoengine import ValidationError, DoesNotExist
from models.students_placement_profile import (
    StudentsPlacementProfile,
    Section,
    Field,
    FieldOption,
    College,
)
from datetime import datetime
import re
from utils.jwt import create_access_token, verify_access_token
from models.students_placement_submission import StudentsPlacementSubmission

from utils.response import response  # <-- import your response utility

students_placement_profile = Blueprint("students_placement_profile", __name__)

_INVALID_FIELD_ID_RE = re.compile(r'[.$]')

def _is_safe_id(s: str) -> bool:
    return bool(s) and not _INVALID_FIELD_ID_RE.search(s)

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
        # print(payload)
        request.token_payload = payload
        return f(*args, **kwargs)

    return decorated

@students_placement_profile.route("/forms", methods=["POST"])
@token_required
def create_or_get_form():
    payload = request.get_json() or {}

    title = payload.get("title")
    token_payload = getattr(request, "token_payload", {})
    college_id = token_payload.get("college_id")

    if not college_id:
        return response(False, "`college_id` is required"), 400

    try:
        college = College.objects.get(id=college_id)
    except (DoesNotExist, ValidationError):
        return response(False, "College not found"), 404

    # --- Use existing form or create a new one ---
    form, created = StudentsPlacementProfile.get_or_create_for_college(college)

    # --- Update fields only if payload is provided ---
    if title:
        form.title = title
    if payload.get("description"):
        form.description = payload.get("description")

    sections_payload = payload.get("sections", [])
    if sections_payload:
        sections = []
        for sec in sections_payload:
            sec_id = sec.get("id")
            sec_title = sec.get("title") or ""
            sec_desc = sec.get("description")

            if not sec_id:
                return response(False, "Each section must have an `id`"), 400
            if not _is_safe_id(sec_id):
                return response(False, f"Unsafe section id: {sec_id}"), 400

            fields_payload = sec.get("fields", [])
            fields = []
            for f in fields_payload:
                fid = f.get("id")
                ftype = f.get("type")
                flabel = f.get("label") or ""

                if not fid:
                    return response(False, "Each field must have an `id`"), 400
                if not _is_safe_id(fid):
                    return response(False, f"Unsafe field id: {fid}"), 400
                if not ftype:
                    return response(False, f"Field '{fid}' missing `type`"), 400

                raw_opts = f.get("options") or []
                opts = [FieldOption(label=(opt.get("label") if isinstance(opt, dict) else str(opt)))
                        for opt in raw_opts if (opt.get("label") if isinstance(opt, dict) else str(opt))]

                fields.append(Field(
                    id=fid,
                    type=ftype,
                    label=flabel,
                    description=f.get("description"),
                    placeholder=f.get("placeholder"),
                    required=bool(f.get("required", False)),
                    verification_required=bool(
                        f.get("verificationRequired") or f.get("verification_required", False)
                    ),
                    options=opts,
                    min_scale=f.get("minScale") or f.get("min_scale"),
                    max_scale=f.get("maxScale") or f.get("max_scale"),
                    scale_min_label=f.get("scaleMinLabel") or f.get("scale_min_label"),
                    scale_max_label=f.get("scaleMaxLabel") or f.get("scale_max_label"),
                    rows=f.get("rows"),
                    min_value=f.get("min"),
                    max_value=f.get("max"),
                ))
            sections.append(Section(id=sec_id, title=sec_title, description=sec_desc, fields=fields))
        form.sections = sections

    if payload.get("settings"):
        form.settings.update(payload["settings"])

    form.updated_at = datetime.utcnow()

    try:
        form.save()
    except ValidationError as e:
        current_app.logger.exception("Form validation failed")
        return response(False, str(e)), 400
 # If this was an update (not new), sync all submissions for this form
    # if not created:
    #     try:
    #         from tasks import sync_form_submissions_task
    #         task = sync_form_submissions_task.delay(str(form.id))
    #         current_app.logger.info(f"Dispatched sync task {task.id} for form {form.id}")
    #     except Exception:
    #         current_app.logger.exception("Failed to dispatch sync task after form update")


    if created:
        msg = "Form created successfully"
        status = 201
    else:
        msg = "Existing form updated successfully"
        status = 200

    return response(True, msg, data={"id": str(form.id)}), status


@students_placement_profile.route("/forms", methods=["GET"])
@token_required
def get_form():
    """Fetch the placement form for the logged-in college."""
    token_payload = getattr(request, "token_payload", {})
    college_id = token_payload.get("college_id")

    if not college_id:
        return response(False, "`college_id` is required"), 400

    try:
        college = College.objects.get(id=college_id)
    except (DoesNotExist, ValidationError):
        return response(False, "College not found"), 404

    try:
        form = StudentsPlacementProfile.objects.get(college=college)
    except DoesNotExist:
        return response(False, "Form not found for this college"), 404

    # --- Serialize the form into a dictionary ---
    form_data = {
        "id": str(form.id),
        "title": form.title,
        "description": form.description,
        "sections": [
            {
                "id": sec.id,
                "title": sec.title,
                "description": sec.description,
                "fields": [
                    {
                        "id": f.id,
                        "type": f.type,
                        "label": f.label,
                        "description": f.description,
                        "placeholder": f.placeholder,
                        "required": f.required,
                        "verification_required": f.verification_required,
                        "options": [{"label": opt.label} for opt in (f.options or [])],
                        "min_scale": f.min_scale,
                        "max_scale": f.max_scale,
                        "scale_min_label": f.scale_min_label,
                        "scale_max_label": f.scale_max_label,
                        "rows": f.rows,
                        "min": f.min_value,
                        "max": f.max_value,
                    }
                    for f in sec.fields or []
                ],
            }
            for sec in form.sections or []
        ],
        "settings": form.settings or {},
            "open": bool(getattr(form, "form_open", False)),   # <-- ADDED: current open state

        "updated_at": form.updated_at.isoformat() if form.updated_at else None,
        "created_at": form.created_at.isoformat() if form.created_at else None,
    }

    return response(True, "Form fetched successfully", data=form_data), 200


@students_placement_profile.route("/forms/toggle_open", methods=["PATCH"])
@token_required
def toggle_open_form():
    """Toggle the 'open' status of the placement form for the logged-in college."""
    token_payload = getattr(request, "token_payload", {})
    college_id = token_payload.get("college_id")

    if not college_id:
        return response(False, "`college_id` is required"), 400

    try:
        college = College.objects.get(id=college_id)
    except (DoesNotExist, ValidationError):
        return response(False, "College not found"), 404

    try:
        form = StudentsPlacementProfile.objects.get(college=college)
    except DoesNotExist:
        return response(False, "Form not found for this college"), 404

    # Toggle the open status
    form.form_open = not form.form_open
    form.updated_at = datetime.utcnow()
    form.save()

    status = "opened" if form.form_open else "closed"
    return response(True, f"Form {status} successfully", data={"open": form.form_open}), 200
