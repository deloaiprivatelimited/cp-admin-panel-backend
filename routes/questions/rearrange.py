# routes/rearrange_routes.py

from flask import Blueprint, request
from functools import wraps
from mongoengine.errors import ValidationError, DoesNotExist
from models.questions.rearrange import Rearrange, Item, Image
from utils.jwt import verify_access_token
from utils.response import response

rearrange_bp = Blueprint("rearrange_bp", __name__)


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
# Helpers: normalize images & items
# ---------------------------
def _normalize_image(obj):
    """
    Accepts a dict or string.
    If string: treat as URL -> create Image with url.
    If dict: accept keys: image_id(optional), url(required), label(optional), alt_text(optional), metadata(optional dict)
    Returns an Image embedded doc instance or None if invalid.
    """
    if not obj:
        return None
    if isinstance(obj, str):
        return Image(url=obj)
    if not isinstance(obj, dict):
        return None
    # Accept url keys commonly used
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


def _normalize_item(it):
    """
    Accepts:
    - dict: { item_id?, value, images? } where images is list of image dicts/urls
    - string: treated as value
    Returns Item embedded doc.
    Raises ValueError for invalid inputs.
    """
    import uuid as _uuid

    if isinstance(it, str):
        return Item(item_id=str(_uuid.uuid4()), value=it.strip())
    if not isinstance(it, dict):
        raise ValueError("Item must be a string or dict")

    value = (it.get("value") or "").strip()
    if not value:
        raise ValueError("Item value cannot be empty")
    iid = it.get("item_id") or str(_uuid.uuid4())

    images_in = it.get("images") or []
    images = []
    for img in images_in:
        ni = _normalize_image(img)
        if ni:
            images.append(ni)

    return Item(item_id=iid, value=value, images=images)


# ---------------------------
# Create Rearrange
# ---------------------------
@rearrange_bp.route("/", methods=["POST"])
@token_required
def add_rearrange():
    try:
        data = request.get_json() or {}

        items_in = data.get("items", [])
        if not isinstance(items_in, list) or len(items_in) < 1:
            return response(False, "At least one item is required"), 400

        normalized_items = []
        try:
            for it in items_in:
                normalized_items.append(_normalize_item(it))
        except ValueError as ve:
            return response(False, f"Invalid item: {ve}"), 400

        # normalize question images
        question_images_in = data.get("question_images") or []
        question_images = []
        for img in question_images_in:
            ni = _normalize_image(img)
            if ni:
                question_images.append(ni)

        # normalize explanation images
        explanation_images_in = data.get("explanation_images") or []
        explanation_images = []
        for img in explanation_images_in:
            ni = _normalize_image(img)
            if ni:
                explanation_images.append(ni)

        # resolve correct_order: can be list of ids, values (correct_item_values), or indexes (correct_item_indexes)
        correct_order = data.get("correct_order") or []
        if not correct_order:
            by_values = data.get("correct_item_values") or []
            if by_values:
                map_by_val = {itm.value: itm.item_id for itm in normalized_items}
                correct_order = [map_by_val[v] for v in by_values if v in map_by_val]

        if not correct_order:
            by_indexes = data.get("correct_item_indexes") or []
            if by_indexes:
                try:
                    correct_order = [normalized_items[int(i)].item_id for i in by_indexes]
                except Exception:
                    correct_order = []

        if not correct_order:
            return response(False, "correct_order is required (ids, values or indexes)"), 400

        # basic checks will be enforced by model.clean() but do a quick check here
        item_ids_set = {it.item_id for it in normalized_items}
        if set(correct_order) != item_ids_set or len(correct_order) != len(normalized_items):
            return response(False, "correct_order must be a permutation of item ids (no missing/extra ids)"), 400

        rearr = Rearrange(
            title=data.get("title"),
            prompt=data.get("prompt", ""),
            question_images=question_images,
            items=normalized_items,
            correct_order=correct_order,
            is_drag_and_drop=bool(data.get("is_drag_and_drop", True)),
            marks=float(data.get("marks", 1.0)),
            negative_marks=float(data.get("negative_marks", 0.0)),
            difficulty_level=data.get("difficulty_level"),
            explanation=data.get("explanation", ""),
            explanation_images=explanation_images,
            tags=data.get("tags", []) or [],
            time_limit=int(data.get("time_limit")) if data.get("time_limit") is not None else None,
            topic=data.get("topic"),
            subtopic=data.get("subtopic"),
            created_by=request.admin if hasattr(request, "admin") else {"id": "system", "name": "System"}
        )

        rearr.save()
        return response(True, "Rearrange question added successfully", rearr.to_json()), 201

    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500


# ---------------------------
# Get All Rearrange (filters + pagination)
# ---------------------------
@rearrange_bp.route("/", methods=["GET"])
@token_required
def get_rearranges():
    try:
        topic = request.args.get("topic")
        subtopic = request.args.get("subtopic")
        difficulty_level = request.args.get("difficulty_level")
        search = request.args.get("search")

        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 10))

        admin_email = request.admin.get("email")
        # keep original behaviour: return only questions created by this admin
        query = Rearrange.objects(created_by__email=admin_email)

        if topic:
            query = query.filter(topic=topic)
        if subtopic:
            query = query.filter(subtopic=subtopic)
        if difficulty_level:
            query = query.filter(difficulty_level=difficulty_level)
        if search:
            # search in title or prompt
            query = query.filter(__raw__={"$or": [
                {"title": {"$regex": search, "$options": "i"}},
                {"prompt": {"$regex": search, "$options": "i"}}
            ]})

        total = query.count()
        items = query.skip((page - 1) * per_page).limit(per_page)

        return response(True, "Rearrange questions fetched successfully", {
            "rearranges": [r.to_json() for r in items],
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": (total + per_page - 1) // per_page
        }), 200

    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500


# ---------------------------
# Get single Rearrange by ID
# ---------------------------
@rearrange_bp.route("/<string:rearrange_id>", methods=["GET"])
@token_required
def get_rearrange(rearrange_id):
    try:
        admin_email = request.admin.get("email")
        rearr = Rearrange.objects.get(id=rearrange_id, created_by__email=admin_email)
        return response(True, "Rearrange fetched", rearr.to_json()), 200
    except DoesNotExist:
        return response(False, "Rearrange not found or not authorized"), 404
    except ValidationError:
        return response(False, "Invalid Rearrange ID"), 400
    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500


# ---------------------------
# Delete Rearrange by ID
# ---------------------------
@rearrange_bp.route("/<string:rearrange_id>", methods=["DELETE"])
@token_required
def delete_rearrange(rearrange_id):
    try:
        admin_email = request.admin.get("email")
        rearr = Rearrange.objects.get(id=rearrange_id, created_by__email=admin_email)
        rearr.delete()
        return response(True, "Rearrange deleted successfully"), 200
    except DoesNotExist:
        return response(False, "Rearrange not found or not authorized"), 404
    except ValidationError:
        return response(False, "Invalid Rearrange ID"), 400
    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500


# ---------------------------
# Update Rearrange by ID (full replace)
# ---------------------------
@rearrange_bp.route("/<string:rearrange_id>", methods=["PUT"])
@token_required
def update_rearrange(rearrange_id):
    try:
        admin_email = request.admin.get("email")
        rearr = Rearrange.objects.get(id=rearrange_id, created_by__email=admin_email)
        data = request.get_json() or {}

        items_in = data.get("items", [])
        if not isinstance(items_in, list) or len(items_in) < 1:
            return response(False, "At least one item is required"), 400

        normalized_items = []
        try:
            for it in items_in:
                normalized_items.append(_normalize_item(it))
        except ValueError as ve:
            return response(False, f"Invalid item: {ve}"), 400

        # normalize question images
        question_images_in = data.get("question_images") or []
        q_images = []
        for img in question_images_in:
            ni = _normalize_image(img)
            if ni:
                q_images.append(ni)

        # normalize explanation images
        explanation_images_in = data.get("explanation_images") or []
        e_images = []
        for img in explanation_images_in:
            ni = _normalize_image(img)
            if ni:
                e_images.append(ni)

        correct_order = data.get("correct_order") or []
        if not correct_order:
            by_values = data.get("correct_item_values") or []
            if by_values:
                map_by_val = {itm.value: itm.item_id for itm in normalized_items}
                correct_order = [map_by_val[v] for v in by_values if v in map_by_val]

        if not correct_order:
            by_indexes = data.get("correct_item_indexes") or []
            if by_indexes:
                try:
                    correct_order = [normalized_items[int(i)].item_id for i in by_indexes]
                except Exception:
                    correct_order = []

        if not correct_order:
            return response(False, "correct_order is required (ids, values or indexes)"), 400

        item_ids_set = {it.item_id for it in normalized_items}
        if set(correct_order) != item_ids_set or len(correct_order) != len(normalized_items):
            return response(False, "correct_order must be a permutation of item ids (no missing/extra ids)"), 400

        # assign fields
        rearr.title = data.get("title", rearr.title)
        rearr.prompt = data.get("prompt", rearr.prompt)
        rearr.question_images = q_images
        rearr.items = normalized_items
        rearr.correct_order = correct_order
        rearr.is_drag_and_drop = bool(data.get("is_drag_and_drop", rearr.is_drag_and_drop))
        rearr.marks = float(data.get("marks", rearr.marks))
        rearr.negative_marks = float(data.get("negative_marks", rearr.negative_marks))
        rearr.difficulty_level = data.get("difficulty_level", rearr.difficulty_level)
        rearr.explanation = data.get("explanation", rearr.explanation)
        rearr.explanation_images = e_images
        rearr.tags = data.get("tags", rearr.tags) or []
        rearr.time_limit = int(data.get("time_limit", rearr.time_limit or 60)) if data.get("time_limit") is not None else rearr.time_limit
        rearr.topic = data.get("topic", rearr.topic)
        rearr.subtopic = data.get("subtopic", rearr.subtopic)

        rearr.save()
        return response(True, "Rearrange updated successfully", rearr.to_json()), 200

    except DoesNotExist:
        return response(False, "Rearrange not found or not authorized"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500


# ---------------------------
# Partial update (PATCH)
# ---------------------------
@rearrange_bp.route("/<string:rearrange_id>", methods=["PATCH"])
@token_required
def patch_rearrange(rearrange_id):
    try:
        admin_email = request.admin.get("email")
        rearr = Rearrange.objects.get(id=rearrange_id, created_by__email=admin_email)
        data = request.get_json() or {}

        # items/correct_order handling if any of related keys provided
        if any(k in data for k in ("items", "correct_order", "correct_item_values", "correct_item_indexes")):
            items_in = data.get("items", [{"item_id": i.item_id, "value": i.value, "images": [ { "image_id": ii.image_id, "url": ii.url, "label": ii.label, "alt_text": ii.alt_text, "metadata": ii.metadata } for ii in (i.images or []) ] } for i in rearr.items])
            if not isinstance(items_in, list) or len(items_in) < 1:
                return response(False, "At least one item is required"), 400

            normalized_items = []
            try:
                for it in items_in:
                    normalized_items.append(_normalize_item(it))
            except ValueError as ve:
                return response(False, f"Invalid item: {ve}"), 400

            correct_order = data.get("correct_order") or []
            if not correct_order:
                by_values = data.get("correct_item_values") or []
                if by_values:
                    map_by_val = {itm.value: itm.item_id for itm in normalized_items}
                    correct_order = [map_by_val[v] for v in by_values if v in map_by_val]

            if not correct_order:
                by_indexes = data.get("correct_item_indexes") or []
                if by_indexes:
                    try:
                        correct_order = [normalized_items[int(i)].item_id for i in by_indexes]
                    except Exception:
                        correct_order = []

            if not correct_order or set(correct_order) != {it.item_id for it in normalized_items}:
                return response(False, "Invalid correct_order for provided items"), 400

            rearr.items = normalized_items
            rearr.correct_order = correct_order

        # question_images update if provided
        if "question_images" in data:
            q_images = []
            for img in data.get("question_images", []):
                ni = _normalize_image(img)
                if ni:
                    q_images.append(ni)
            rearr.question_images = q_images

        # explanation_images update if provided
        if "explanation_images" in data:
            e_images = []
            for img in data.get("explanation_images", []):
                ni = _normalize_image(img)
                if ni:
                    e_images.append(ni)
            rearr.explanation_images = e_images

        # simple fields
        mapping = {
            "title": "title", "prompt": "prompt",
            "difficulty_level": "difficulty_level", "explanation": "explanation",
            "topic": "topic", "subtopic": "subtopic"
        }
        for k, attr in mapping.items():
            if k in data:
                setattr(rearr, attr, data[k])

        if "marks" in data: rearr.marks = float(data["marks"])
        if "negative_marks" in data: rearr.negative_marks = float(data["negative_marks"])
        if "tags" in data: rearr.tags = data.get("tags") or []
        if "time_limit" in data: rearr.time_limit = int(data["time_limit"])
        if "is_drag_and_drop" in data: rearr.is_drag_and_drop = bool(data["is_drag_and_drop"])

        rearr.save()
        return response(True, "Rearrange updated", rearr.to_json()), 200

    except DoesNotExist:
        return response(False, "Rearrange not found or not authorized"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500
