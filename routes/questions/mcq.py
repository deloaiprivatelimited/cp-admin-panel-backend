# routes/mcq_routes.py

from flask import Blueprint, request
from functools import wraps
from mongoengine.errors import ValidationError, NotUniqueError, DoesNotExist
# from models.mcq import MCQ
from models.questions.mcq import MCQ
from utils.jwt import verify_access_token
from utils.response import response

mcq_bp = Blueprint("mcq_bp", __name__)

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
@mcq_bp.route("/", methods=["POST"])
@token_required
def add_mcq():
    try:
        data = request.get_json()

        mcq = MCQ(
            title=data.get("title"),
            question_text=data.get("question_text"),
            options=data.get("options"),
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

        return response(True, "MCQ added successfully", mcq.to_json()), 201
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500
    
# ---------------------------
# Get All MCQs (with filters + pagination)
# ---------------------------
@mcq_bp.route("/", methods=["GET"])
@token_required
def get_mcqs():
    try:
        # Get filters from query params
        topic = request.args.get("topic")
        subtopic = request.args.get("subtopic")
        difficulty_level = request.args.get("difficulty_level")
        search = request.args.get("search")

        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 10))

        # Ensure only questions by logged-in admin are fetched
        admin_email = request.admin.get("email")
        query = MCQ.objects(created_by__email=admin_email)

        # Apply filters
        if topic:
            query = query.filter(topic=topic)
        if subtopic:
            query = query.filter(subtopic=subtopic)
        if difficulty_level:
            query = query.filter(difficulty_level=difficulty_level)
        if search:
            query = query.filter(question_text__icontains=search)

        # Pagination
        total = query.count()
        mcqs = query.skip((page - 1) * per_page).limit(per_page)

        return response(True, "MCQs fetched successfully", {
            "mcqs": [m.to_json() for m in mcqs],
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": (total + per_page - 1) // per_page
        }), 200

    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500


# ---------------------------
# Delete MCQ by ID
# ---------------------------
@mcq_bp.route("/<string:mcq_id>", methods=["DELETE"])
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
@mcq_bp.route('/<string:mcq_id>', methods=['GET'])
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
@mcq_bp.route('/<string:mcq_id>', methods=['PUT'])
@token_required
def update_mcq(mcq_id):
    try:
        admin_email = request.admin.get('email')
        mcq = MCQ.objects.get(id=mcq_id, created_by__email=admin_email)
        data = request.get_json() or {}

        # --- normalize options into EmbeddedDocument Option objects ---
        options_in = data.get('options', [])
        if not isinstance(options_in, list) or len(options_in) < 2:
            return response(False, 'At least two options are required'), 400

        from models.questions.mcq import Option  # ensure correct import path
        import uuid as _uuid
        normalized_options = []
        for opt in options_in:
            # accept both {option_id, value} or {value}
            val = (opt.get('value') if isinstance(opt, dict) else str(opt)).strip()
            if not val:
                return response(False, 'Option values cannot be empty'), 400
            oid = (opt.get('option_id') if isinstance(opt, dict) else None) or str(_uuid.uuid4())
            normalized_options.append(Option(option_id=oid, value=val))

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

# ---------------------------
# Optional: Partial update (PATCH)
# ---------------------------
@mcq_bp.route('/<string:mcq_id>', methods=['PATCH'])
@token_required
def patch_mcq(mcq_id):
    try:
        admin_email = request.admin.get('email')
        mcq = MCQ.objects.get(id=mcq_id, created_by__email=admin_email)
        data = request.get_json() or {}

        from models.questions.mcq import Option
        import uuid as _uuid

        # Options update if provided
        if 'options' in data or 'correct_options' in data or 'is_multiple' in data or 'correct_option_values' in data or 'correct_option_indexes' in data:
            options_in = data.get('options', [ {'option_id': o.option_id, 'value': o.value} for o in mcq.options ])
            if not isinstance(options_in, list) or len(options_in) < 2:
                return response(False, 'At least two options are required'), 400
            normalized_options = []
            for opt in options_in:
                val = (opt.get('value') if isinstance(opt, dict) else str(opt)).strip()
                if not val:
                    return response(False, 'Option values cannot be empty'), 400
                oid = (opt.get('option_id') if isinstance(opt, dict) else None) or str(_uuid.uuid4())
                normalized_options.append(Option(option_id=oid, value=val))

            # corrects
            correct_ids = data.get('correct_options') or []
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
            if not correct_ids or not all(cid in option_ids_set for cid in correct_ids):
                return response(False, 'Invalid correct_options'), 400
            is_multiple = bool(data.get('is_multiple', mcq.is_multiple))
            if not is_multiple and len(correct_ids) > 1:
                return response(False, 'Multiple correct not allowed'), 400

            mcq.options = normalized_options
            mcq.correct_options = correct_ids
            mcq.is_multiple = is_multiple

        # Simple fields
        mapping = {
            'title': 'title', 'question_text': 'question_text',
            'difficulty_level': 'difficulty_level', 'explanation': 'explanation',
            'topic': 'topic', 'subtopic': 'subtopic'
        }
        for k, attr in mapping.items():
            if k in data:
                setattr(mcq, attr, data[k])
        if 'marks' in data: mcq.marks = float(data['marks'])
        if 'negative_marks' in data: mcq.negative_marks = float(data['negative_marks'])
        if 'tags' in data: mcq.tags = data.get('tags') or []
        if 'time_limit' in data: mcq.time_limit = int(data['time_limit'])

        mcq.save()
        return response(True, 'MCQ updated', mcq.to_json()), 200
    except DoesNotExist:
        return response(False, 'MCQ not found or not authorized'), 404
    except ValidationError as ve:
        return response(False, f'Validation error: {ve}'), 400
    except Exception as e:
        return response(False, f'Error: {str(e)}'), 500
