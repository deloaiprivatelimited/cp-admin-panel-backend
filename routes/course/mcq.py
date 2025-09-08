# routes/mcq_routes.py

from flask import Blueprint, request
from functools import wraps
from mongoengine.errors import ValidationError, NotUniqueError, DoesNotExist
# from models.mcq import MCQ
from  models.courses.mcq import CourseMCQ as MCQ
from utils.jwt import verify_access_token
from utils.response import response
from models.courses.courses import Unit

course_mcq_bp = Blueprint("course_mcq_bp", __name__)

# ---------------------------
# Decorator to check token
# ---------------------------
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


# ---------------------------
# Create new MCQ
# ---------------------------

# POST /units/<unit_id>/mcq
@course_mcq_bp.route("/units/<string:unit_id>/mcq", methods=["POST"])
@token_required
def add_mcq_to_unit(unit_id):
    try:
        data = request.get_json() or {}

        # find unit
        unit = Unit.objects(id=unit_id).first()
        if not unit:
            return response(False, f"Unit with id {unit_id} not found"), 404

        # build CourseMCQ instance (use field names from your CourseMCQ model)
        mcq = MCQ(
            title=data.get("title"),
            question_text=data.get("question_text"),
            options=data.get("options", []),
            correct_options=data.get("correct_options", []),
            is_multiple=data.get("is_multiple", False),
            marks=data.get("marks"),
            negative_marks=data.get("negative_marks"),
            difficulty_level=data.get("difficulty_level"),
            explanation=data.get("explanation", ""),
            tags=data.get("tags", []),
            time_limit=data.get("time_limit"),
            topic=data.get("topic"),
            subtopic=data.get("subtopic"),
            created_by=request.admin if hasattr(request, "admin") else {"id": "system", "name": "System"}
        )
        mcq.save()

        # attach to unit
        unit.mcq = mcq
        # set unit_type to mcq (optional, but keeps data consistent)
        unit.unit_type = "mcq"
        unit.save()

        return response(True, "MCQ added and linked to unit", {"unit": unit.to_json(), "mcq": mcq.to_json()}), 201

    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except DoesNotExist:
        return response(False, "Resource not found"), 404
    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500
# ---------------------------
# Delete MCQ by ID
# ---------------------------
@course_mcq_bp.route("/<string:mcq_id>", methods=["DELETE"])
@token_required
def delete_mcq(mcq_id):
    try:
        # Ensure only MCQs created by this admin can be deleted
        admin_email = request.admin.get("email")

        mcq = MCQ.objects.get(id=mcq_id, created_by__email=admin_email)
        mcq.delete()

        return response(True, "MCQ deleted successfully"), 200

    except DoesNotExist:
        return response(False, "MCQ not found or not authorized to delete"), 404
    except ValidationError:
        return response(False, "Invalid MCQ ID"), 400
    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500


# ... existing imports and setup remain the same
from mongoengine.queryset.visitor import Q

# ---------------------------
# Get single MCQ by ID (for editing)
# ---------------------------

# ... existing imports and setup remain the same
from mongoengine.queryset.visitor import Q

# ---------------------------
# Get single MCQ by ID (for editing)
# ---------------------------
@course_mcq_bp.route('/<string:mcq_id>', methods=['GET'])
@token_required
def get_mcq(mcq_id):
    try:
        admin_email = request.admin.get('email')
        mcq = MCQ.objects.get(id=mcq_id, created_by__email=admin_email)
        return response(True, 'MCQ fetched', mcq.to_json()), 200
    except DoesNotExist:
        return response(False, 'MCQ not found or not authorized'), 404
    except ValidationError:
        return response(False, 'Invalid MCQ ID'), 400
    except Exception as e:
        return response(False, f'Error: {str(e)}'), 500

# ---------------------------
# Update MCQ by ID (full replace)
# ---------------------------
@course_mcq_bp.route('/<string:mcq_id>', methods=['PUT'])
@token_required
def update_mcq(mcq_id):
    try:
        admin_email = request.admin.get('email')
        mcq = MCQ.objects.get(id=mcq_id, created_by__email=admin_email)
        data = request.get_json() or {}

        # --- normalize options into EmbeddedDocument Option objects ---
        options_in = data.get('options', [])
        print(options_in)
        if not isinstance(options_in, list) or len(options_in) < 2:
            return response(False, 'At least two options are required'), 400

        from models.courses.mcq import Option  # ensure correct import path
        import uuid as _uuid
        normalized_options = []
        for opt in options_in:
            # accept both {option_id, value} or {value}
            val = (opt.get('value') if isinstance(opt, dict) else str(opt)).strip()
            if not val:
                return response(False, 'Option values cannot be empty'), 400
            oid = (opt.get('option_id') if isinstance(opt, dict) else None) or str(_uuid.uuid4())
            normalized_options.append(Option(option_id=oid, value=val))
        print(normalized_options)
        # --- correct options ---
        is_multiple = bool(data.get('is_multiple', False))
        correct_ids = data.get('correct_options') or []
        # Fallbacks for create/edit clients that send by value or index
        if not correct_ids:
            by_values = data.get('correct_option_values') or []
            if by_values:
                map_by_val = {o.value: o.option_id for o in normalized_options}
                correct_ids = [map_by_val[v] for v in by_values if v in map_by_val]
        if not correct_ids:
            by_indexes = data.get('correct_option_indexes') or []
            if by_indexes:
                for i in by_indexes:
                    try:
                        correct_ids.append(normalized_options[int(i)].option_id)
                    except Exception:
                        pass

        option_ids_set = {o.option_id for o in normalized_options}
        if not correct_ids:
            return response(False, 'Select at least one correct option'), 400
        if not all(cid in option_ids_set for cid in correct_ids):
            return response(False, 'correct_options contain unknown IDs'), 400
        if not is_multiple and len(correct_ids) > 1:
            return response(False, 'Multiple correct not allowed when is_multiple is false'), 400

        # --- assign to document ---
        mcq.title = data.get('title', mcq.title)
        mcq.question_text = data.get('question_text', mcq.question_text)
        mcq.options = normalized_options
        mcq.correct_options = correct_ids
        mcq.is_multiple = is_multiple
        mcq.marks = float(data.get('marks', mcq.marks))
        mcq.negative_marks = float(data.get('negative_marks', mcq.negative_marks))
        mcq.difficulty_level = data.get('difficulty_level', mcq.difficulty_level)
        mcq.explanation = data.get('explanation', mcq.explanation)
        mcq.tags = data.get('tags', mcq.tags) or []
        mcq.time_limit = int(data.get('time_limit', mcq.time_limit or 60))
        mcq.topic = data.get('topic', mcq.topic)
        mcq.subtopic = data.get('subtopic', mcq.subtopic)

        mcq.save()
        return response(True, 'MCQ updated successfully', mcq.to_json()), 200

    except DoesNotExist:
        return response(False, 'MCQ not found or not authorized'), 404
    except ValidationError as ve:
        return response(False, f'Validation error: {ve}'), 400
    except Exception as e:
        return response(False, f'Error: {str(e)}'), 500

