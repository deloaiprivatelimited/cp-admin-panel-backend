# routes/coding.py
import json
import datetime
from flask import Blueprint, request, jsonify, current_app
from functools import wraps
from mongoengine.errors import DoesNotExist, ValidationError
from mongoengine.queryset.visitor import Q as MQ
from bson import ObjectId

from utils.jwt import verify_access_token
from utils.response import response

# Import models
from models.questions.coding import (
    Question,
    CourseQuestion,
    TestQuestion,
    CollegeQuestion,
)

coding_bp = Blueprint("generic_coding_bp", __name__, url_prefix="/v1/coding")

# ---------- token decorator ----------
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

# ---------- helpers ----------
def _get_admin_college_id():
    admin_payload = getattr(request, "admin", {}) or {}
    return admin_payload.get("college_id")

def _ensure_same_college_or_forbid(obj_college_id):
    admin_college_id = _get_admin_college_id()
    if admin_college_id is None:
        return response(False, "Forbidden: admin has no college_id"), 403
    if str(admin_college_id) != str(obj_college_id):
        return response(False, "Forbidden: resource does not belong to your college"), 403
    return None

def _model_for_collection(collection):
    """
    Return the concrete model class for coding questions.
    """
    if collection == "questions":
        return Question
    elif collection == "course_questions":
        return CourseQuestion
    elif collection == "test_questions":
        return TestQuestion
    elif collection == "college_questions":
        return CollegeQuestion
    else:
        return None

# ---------- serialization helpers ----------
def _convert_simple_types(obj):
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, (list, tuple)):
        return [_convert_simple_types(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _convert_simple_types(v) for k, v in obj.items()}
    return obj

def serialize_doc(doc):
    """
    Use the model's safe JSON if available,
    otherwise fallback to .to_mongo().
    """
    if hasattr(doc, "to_safe_json"):
        return _convert_simple_types(doc.to_safe_json())

    try:
        raw = doc.to_mongo().to_dict()
    except Exception:
        raw = {k: v for k, v in getattr(doc, "__dict__", {}).items() if not k.startswith("_")}

    if "_id" in raw:
        raw["id"] = str(raw.pop("_id"))
    elif "id" in raw:
        raw["id"] = str(raw["id"])

    return _convert_simple_types(raw)

# ---------- routes ----------

@coding_bp.route("/<collection>", methods=["GET"])
@token_required
def list_items(collection):
    """
    GET /v1/coding/<collection>?page=1&per_page=20&q=sort&filters=...&tags=dp,array&include_meta=true
    """
    Model = _model_for_collection(collection)
    if Model is None:
        return response(False, f"Unknown collection: {collection}"), 400

    admin_college_id = _get_admin_college_id()

    # pagination
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    try:
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    except ValueError:
        per_page = 20
    skip = (page - 1) * per_page

    # base query
    query = {}
    if hasattr(Model, "college_id"):  # enforce scoping
        if admin_college_id is None:
            return response(False, "Forbidden: admin has no college_id"), 403
        query["college_id"] = admin_college_id

    # filters
    filters = request.args.get("filters")
    if filters:
        try:
            extra = json.loads(filters)
            if isinstance(extra, dict):
                for k, v in extra.items():
                    if k == "college_id" and hasattr(Model, "college_id"):
                        continue
                    query[k] = v
        except Exception:
            pass

    # tags
    tags_param = request.args.get("tags")
    if tags_param:
        tags_list = [t.strip() for t in tags_param.split(",") if t.strip()]
        if tags_list:
            query["tags__in"] = tags_list

    # search (q)
    q = request.args.get("q")
    try:
        qs_base = Model.objects(__raw__=query)
        if q:
            q_filter = MQ()
            added = False
            if "title" in Model._fields:
                q_filter |= MQ(title__icontains=q)
                added = True
            if "topic" in Model._fields:
                q_filter |= MQ(topic__icontains=q)
                added = True
            if "subtopic" in Model._fields:
                q_filter |= MQ(subtopic__icontains=q)
                added = True
            if "tags" in Model._fields:
                q_filter |= MQ(tags__icontains=q)
                added = True
            if added:
                qs = qs_base.filter(q_filter).skip(skip).limit(per_page)
            else:
                qs = qs_base.skip(skip).limit(per_page)
        else:
            qs = qs_base.skip(skip).limit(per_page)
    except Exception as e:
        current_app.logger.exception(e)
        return response(False, "Error building query"), 500

    try:
        items = [serialize_doc(i) for i in qs]
        total = Model.objects(__raw__=query).count()
    except Exception as e:
        current_app.logger.exception(e)
        return response(False, "Error querying collection"), 500

    resp = {
        "success": True,
        "collection": collection,
        "page": page,
        "per_page": per_page,
        "total": total,
        "items": items,
    }

    # include_meta (tags, topics, subtopics, difficulties, langs)
    include_meta = request.args.get("include_meta")
    if include_meta and include_meta.lower() in ("1", "true", "yes"):
        try:
            meta_agg = {
                "difficulty_levels": set(),
                "topics": set(),
                "subtopics": set(),
                "tags": set(),
                "allowed_languages": set(),
            }
            for doc in Model.objects(__raw__=query):
                if getattr(doc, "difficulty", None):
                    meta_agg["difficulty_levels"].add(doc.difficulty)
                if getattr(doc, "topic", None):
                    meta_agg["topics"].add(doc.topic)
                if getattr(doc, "subtopic", None):
                    meta_agg["subtopics"].add(doc.subtopic)
                for t in getattr(doc, "tags", []) or []:
                    if t:
                        meta_agg["tags"].add(t)
                for lang in getattr(doc, "allowed_languages", []) or []:
                    meta_agg["allowed_languages"].add(lang)
            resp["meta"] = {k: sorted(list(v)) for k, v in meta_agg.items()}
        except Exception:
            current_app.logger.exception("Failed to compute meta for coding collection")

    return jsonify(resp), 200


@coding_bp.route("/<collection>/<item_id>", methods=["GET"])
@token_required
def get_item(collection, item_id):
    """
    GET /v1/coding/<collection>/<item_id>
    """
    Model = _model_for_collection(collection)
    if Model is None:
        return response(False, f"Unknown collection: {collection}"), 400

    try:
        obj = Model.objects.get(id=item_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Not found"), 404
    except Exception as e:
        current_app.logger.exception(e)
        return response(False, "Error fetching item"), 500

    obj_college_id = getattr(obj, "college_id", None)
    if obj_college_id is not None:
        forbidden = _ensure_same_college_or_forbid(obj_college_id)
        if forbidden:
            return forbidden

    return jsonify({"success": True, "item": serialize_doc(obj)}), 200


@coding_bp.route("/<collection>/<item_id>", methods=["DELETE"])
@token_required
def delete_item(collection, item_id):
    """
    DELETE /v1/coding/<collection>/<item_id>
    """
    Model = _model_for_collection(collection)
    if Model is None:
        return response(False, f"Unknown collection: {collection}"), 400

    try:
        obj = Model.objects.get(id=item_id)
    except (DoesNotExist, ValidationError):
        return response(False, "Not found"), 404
    except Exception as e:
        current_app.logger.exception(e)
        return response(False, "Error fetching item"), 500

    # enforce same-college restriction
    obj_college_id = getattr(obj, "college_id", None)
    if obj_college_id is not None:
        forbidden = _ensure_same_college_or_forbid(obj_college_id)
        if forbidden:
            return forbidden

    try:
        obj.delete()
    except Exception as e:
        current_app.logger.exception(e)
        return response(False, "Error deleting item"), 500

    return jsonify({"success": True, "message": f"{collection[:-1].capitalize()} deleted successfully"}), 200
