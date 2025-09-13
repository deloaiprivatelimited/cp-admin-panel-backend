# routes/mcq_routes.py

from flask import Blueprint, request
from functools import wraps
from mongoengine.errors import ValidationError, NotUniqueError, DoesNotExist
from mongoengine.queryset.visitor import Q

from models.questions.mcq import MCQ, Option, Image
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
# Helpers: normalize images & options
# ---------------------------
def _normalize_image(obj):
    """
    Accepts a dict or string.
    If string: treat as URL -> create Image with url.
    If dict: accept keys: image_id(optional), url(required), label(optional), alt_text(optional), metadata(optional dict)
    Returns an Image embedded doc instance.
    """
    if not obj:
        return None
    if isinstance(obj, str):
        return Image(url=obj)
    if not isinstance(obj, dict):
        return None
    # Ensure url exists
    url = obj.get("url") or obj.get("uri") or obj.get("src")
    if not url:
        return None
    img_kwargs = {
        "image_id": obj.get("image_id"),
        "url": url,
        "label": obj.get("label"),
        "alt_text": obj.get("alt_text"),
        "metadata": obj.get("metadata") or {}
    }
    # remove None values (mongoengine will fill defaults)
    return Image(**{k: v for k, v in img_kwargs.items() if v is not None})


def _normalize_option(opt):
    """
    Accepts:
    - dict: { option_id?, value, images? } where images is list of image dicts/urls
    - string: treated as value
    Returns Option embedded doc.
    """
    import uuid as _uuid

    if isinstance(opt, str):
        return Option(option_id=str(_uuid.uuid4()), value=opt.strip())
    if not isinstance(opt, dict):
        raise ValueError("Option must be string or dict")
    val = (opt.get("value") or "").strip()
    if not val:
        raise ValueError("Option value cannot be empty")
    oid = opt.get("option_id") or str(_uuid.uuid4())
    images_in = opt.get("images") or []
    images = []
    for img in images_in:
        normalized = _normalize_image(img)
        if normalized:
            images.append(normalized)
    return Option(option_id=oid, value=val, images=images)


# ---------------------------
# Create new MCQ
# ---------------------------
@mcq_bp.route("/", methods=["POST"])
@token_required
def add_mcq():
    try:
        data = request.get_json() or {}

        # Normalize options (must be at least 2)
        options_in = data.get("options") or []
        if not isinstance(options_in, list) or len(options_in) < 2:
            return response(False, "At least two options are required"), 400

        normalized_options = []
        for raw_opt in options_in:
            try:
                normalized_options.append(_normalize_option(raw_opt))
            except ValueError as ve:
                return response(False, f"Invalid option: {ve}"), 400

        # question images
        question_images_in = data.get("question_images") or []
        question_images = []
        for img in question_images_in:
            ni = _normalize_image(img)
            if ni:
                question_images.append(ni)

        # explanation images
        explanation_images_in = data.get("explanation_images") or []
        explanation_images = []
        for img in explanation_images_in:
            ni = _normalize_image(img)
            if ni:
                explanation_images.append(ni)

        # correct options: accept option_ids, values, or indexes (list)
        is_multiple = bool(data.get("is_multiple", False))
        correct_ids = data.get("correct_options") or []

        # fallback: by values
        if not correct_ids:
            by_values = data.get("correct_option_values") or []
            if by_values:
                map_by_val = {o.value: o.option_id for o in normalized_options}
                correct_ids = [map_by_val[v] for v in by_values if v in map_by_val]

        # fallback: by indexes (0-based)
        if not correct_ids:
            by_indexes = data.get("correct_option_indexes") or []
            if by_indexes:
                for idx in by_indexes:
                    try:
                        correct_ids.append(normalized_options[int(idx)].option_id)
                    except Exception:
                        pass

        if not correct_ids:
            return response(False, "Select at least one correct option"), 400

        option_ids_set = {o.option_id for o in normalized_options}
        if not all(cid in option_ids_set for cid in correct_ids):
            return response(False, "correct_options contain unknown IDs"), 400

        if not is_multiple and len(correct_ids) > 1:
            return response(False, "Multiple correct not allowed when is_multiple is false"), 400

        mcq = MCQ(
            title=data.get("title"),
            question_text=data.get("question_text"),
            question_images=question_images,
            options=normalized_options,
            correct_options=correct_ids,
            is_multiple=is_multiple,
            marks=float(data.get("marks", 0)),
            negative_marks=float(data.get("negative_marks", 0)),
            difficulty_level=data.get("difficulty_level"),
            explanation=data.get("explanation", ""),
            explanation_images=explanation_images,
            tags=data.get("tags", []),
            time_limit=int(data.get("time_limit")) if data.get("time_limit") is not None else None,
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
# - By default returns ALL MCQs irrespective of created_by
# - If ?mine=true passed, restrict to current admin's email (if available)
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

        mine = request.args.get("mine", "false").lower() == "true"
        query = MCQ.objects

        if mine:
            admin_email = request.admin.get("email")
            if admin_email:
                query = query.filter(created_by__email=admin_email)
            else:
                # if token doesn't have email, return empty
                query = query.filter(id__exists=False)

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
# Get single MCQ by ID (for editing)
# - By default fetches irrespective of created_by (authorized via token only)
# - Use ?mine=true to require created_by match
# ---------------------------
@mcq_bp.route('/<string:mcq_id>', methods=['GET'])
@token_required
def get_mcq(mcq_id):
    try:
        mine = request.args.get("mine", "false").lower() == "true"
        if mine:
            admin_email = request.admin.get("email")
            mcq = MCQ.objects.get(id=mcq_id, created_by__email=admin_email)
        else:
            mcq = MCQ.objects.get(id=mcq_id)
        return response(True, 'MCQ fetched', mcq.to_json()), 200
    except DoesNotExist:
        return response(False, 'MCQ not found or not authorized'), 404
    except ValidationError:
        return response(False, 'Invalid MCQ ID'), 400
    except Exception as e:
        return response(False, f'Error: {str(e)}'), 500


# ---------------------------
# Update MCQ by ID (full replace)
# - By default updates irrespective of created_by
# - Use ?mine=true to enforce created_by match
# ---------------------------
@mcq_bp.route('/<string:mcq_id>', methods=['PUT'])
@token_required
def update_mcq(mcq_id):
    try:
        mine = request.args.get("mine", "false").lower() == "true"
        if mine:
            admin_email = request.admin.get("email")
            mcq = MCQ.objects.get(id=mcq_id, created_by__email=admin_email)
        else:
            mcq = MCQ.objects.get(id=mcq_id)

        data = request.get_json() or {}

        # --- Normalize incoming options (required at least 2) ---
        options_in = data.get('options', [])
        if not isinstance(options_in, list) or len(options_in) < 2:
            return response(False, 'At least two options are required'), 400

        normalized_options = []
        for opt in options_in:
            try:
                normalized_options.append(_normalize_option(opt))
            except ValueError as ve:
                return response(False, f'Invalid option: {ve}'), 400

        # --- question images ---
        question_images_in = data.get("question_images", [])
        q_images = []
        for img in question_images_in:
            ni = _normalize_image(img)
            if ni:
                q_images.append(ni)

        # --- explanation images ---
        explanation_images_in = data.get("explanation_images", [])
        e_images = []
        for img in explanation_images_in:
            ni = _normalize_image(img)
            if ni:
                e_images.append(ni)

        # --- correct options resolution ---
        is_multiple = bool(data.get('is_multiple', False))
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
        if not correct_ids:
            return response(False, 'Select at least one correct option'), 400
        if not all(cid in option_ids_set for cid in correct_ids):
            return response(False, 'correct_options contain unknown IDs'), 400
        if not is_multiple and len(correct_ids) > 1:
            return response(False, 'Multiple correct not allowed when is_multiple is false'), 400

        # --- assign fields ---
        mcq.title = data.get('title', mcq.title)
        mcq.question_text = data.get('question_text', mcq.question_text)
        mcq.question_images = q_images
        mcq.options = normalized_options
        mcq.correct_options = correct_ids
        mcq.is_multiple = is_multiple
        mcq.marks = float(data.get('marks', mcq.marks))
        mcq.negative_marks = float(data.get('negative_marks', mcq.negative_marks))
        mcq.difficulty_level = data.get('difficulty_level', mcq.difficulty_level)
        mcq.explanation = data.get('explanation', mcq.explanation)
        mcq.explanation_images = e_images
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
# - Allows partial updates; options block is handled similarly as PUT
# ---------------------------
@mcq_bp.route('/<string:mcq_id>', methods=['PATCH'])
@token_required
def patch_mcq(mcq_id):
    try:
        mine = request.args.get("mine", "false").lower() == "true"
        if mine:
            admin_email = request.admin.get("email")
            mcq = MCQ.objects.get(id=mcq_id, created_by__email=admin_email)
        else:
            mcq = MCQ.objects.get(id=mcq_id)

        data = request.get_json() or {}

        # If options are provided, normalize and validate them
        if 'options' in data or 'correct_options' in data or 'is_multiple' in data \
           or 'correct_option_values' in data or 'correct_option_indexes' in data:
            options_in = data.get('options', [ {'option_id': o.option_id, 'value': o.value, 'images': [ { "image_id": ii.image_id, "url": ii.url, "label": ii.label, "alt_text": ii.alt_text } for ii in (o.images or []) ] } for o in mcq.options ])
            if not isinstance(options_in, list) or len(options_in) < 2:
                return response(False, 'At least two options are required'), 400
            normalized_options = []
            for opt in options_in:
                try:
                    normalized_options.append(_normalize_option(opt))
                except ValueError as ve:
                    return response(False, f'Invalid option: {ve}'), 400

            # resolve correct ids
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

        # question_images update if provided
        if 'question_images' in data:
            q_images = []
            for img in data.get('question_images', []):
                ni = _normalize_image(img)
                if ni:
                    q_images.append(ni)
            mcq.question_images = q_images

        # explanation_images update if provided
        if 'explanation_images' in data:
            e_images = []
            for img in data.get('explanation_images', []):
                ni = _normalize_image(img)
                if ni:
                    e_images.append(ni)
            mcq.explanation_images = e_images

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


# ---------------------------
# Delete MCQ by ID
# - By default deletes irrespective of created_by
# - Use ?mine=true to enforce created_by match
# ---------------------------
@mcq_bp.route("/<string:mcq_id>", methods=["DELETE"])
@token_required
def delete_mcq(mcq_id):
    try:
        mine = request.args.get("mine", "false").lower() == "true"
        if mine:
            admin_email = request.admin.get("email")
            mcq = MCQ.objects.get(id=mcq_id, created_by__email=admin_email)
        else:
            mcq = MCQ.objects.get(id=mcq_id)
        mcq.delete()
        return response(True, "MCQ deleted successfully"), 200

    except DoesNotExist:
        return response(False, "MCQ not found or not authorized to delete"), 404
    except ValidationError:
        return response(False, "Invalid MCQ ID"), 400
    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500
