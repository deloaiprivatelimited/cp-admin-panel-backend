# routes/rearrange_routes.py

from flask import Blueprint, request
from functools import wraps
from mongoengine.errors import ValidationError, DoesNotExist
from models.courses.courses import Unit
from models.questions.rearrange import Item
from  models.questions.rearrange import CourseRearrange as Rearrange
from utils.jwt import verify_access_token
from utils.response import response

course_rearrange_bp = Blueprint("course_rearrange_bp", __name__)

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
@course_rearrange_bp.route("/units/<string:unit_id>/rearrange", methods=["POST"])
@token_required
def add_rearrange(unit_id):
    try:
        data = request.get_json() or {}

        unit = Unit.objects(id=unit_id).first()
        if not unit:
            return response(False, f"Unit with id {unit_id} not found"), 404

     

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

        unit.rearrange = rearr
        unit.save()
        return response(True, "Rearrange question added successfully", rearr.to_json()), 201

    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500

