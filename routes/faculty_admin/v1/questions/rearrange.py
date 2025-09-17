# routes/rearrange.py
import json
from flask import Blueprint, request, jsonify, current_app
from functools import wraps
from mongoengine.errors import DoesNotExist, ValidationError
from mongoengine.queryset.visitor import Q as MQ
from bson import ObjectId
import datetime

from utils.jwt import verify_access_token
from utils.response import response

# import models
from models.questions.rearrange import (
    Rearrange,
    CourseRearrange,
    CollegeRearrange,
    TestRearrange,
    RearrangeConfig,
)

rearrange_bp = Blueprint("generic_rearrange_bp", __name__, url_prefix="/v1/rearrange")

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

# explicit mapping function
def _model_for_collection(collection):
    """
    Return the concrete model class for a given collection name.
    Keep in sync with your models/rearrange.py.
    """
    if collection == "rearranges":
        return Rearrange
    elif collection == "course_rearrange":
        return CourseRearrange
    elif collection == "college_rearrange":
        return CollegeRearrange
    elif collection == "test_rearrange":
        return TestRearrange
    elif collection == "rearrange_configs":
        return RearrangeConfig
    else:
        return None

# ---------- serialization helpers ----------
def _convert_simple_types(obj):
    # convert ObjectId, datetime, etc to JSON-friendly
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
    Turn a MongoEngine document into a JSON-friendly dict.
    Prefer a document's custom to_json() when available.
    """
    try:
        raw = doc.to_mongo().to_dict()
    except Exception:
        if hasattr(doc, "to_json"):
            try:
                return _convert_simple_types(doc.to_json())
            except Exception:
                pass
        raw = {k: v for k, v in getattr(doc, "__dict__", {}).items() if not k.startswith("_")}

    if "_id" in raw:
        raw["id"] = str(raw.pop("_id"))
    elif "id" in raw:
        raw["id"] = str(raw["id"])

    return _convert_simple_types(raw)

# ---------- routes ----------

@rearrange_bp.route("/<collection>", methods=["GET"])
@token_required
def list_items(collection):
    """
    GET /v1/rearrange/<collection>?page=1&per_page=20&filters=...&q=...&tags=tag1,tag2&include_meta=true

    - filters: JSON string to merge into the query (client cannot override college scoping)
    - tags: comma-separated list (OR semantics)
    - q: substring search across title/prompt/tags/items.value
    - include_meta=true will return aggregated tags/topics/subtopics/difficulties computed from the documents
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

    # base query: enforce college scoping for college-scoped models
    query = {}
    if hasattr(Model, "college_id"):
        if admin_college_id is None:
            return response(False, "Forbidden: admin has no college_id"), 403
        query["college_id"] = admin_college_id

    # optional filters (JSON)
    filters = request.args.get("filters")
    if filters:
        try:
            extra = json.loads(filters)
            if isinstance(extra, dict):
                for k, v in extra.items():
                    if k == "college_id" and hasattr(Model, "college_id"):
                        # don't allow override of college scoping
                        continue
                    query[k] = v
        except Exception:
            # ignore invalid filters
            pass

    # tags param (comma-separated) — match any
    tags_param = request.args.get("tags")
    if tags_param:
        tags_list = [t.strip() for t in tags_param.split(",") if t.strip()]
        if tags_list:
            query["tags__in"] = tags_list

    # Build queryset and apply `q` fuzzy search if requested
    q = request.args.get("q")
    try:
        qs_base = Model.objects(__raw__=query)
        if q:
            q_filter = MQ()
            added = False
            if "prompt" in Model._fields:
                q_filter |= MQ(prompt__icontains=q)
                added = True
            if "title" in Model._fields:
                q_filter |= MQ(title__icontains=q)
                added = True
            if "tags" in Model._fields:
                q_filter |= MQ(tags__icontains=q)
                added = True
            # items is a list of embedded docs with `value` — search items.value substring
            if "items" in Model._fields:
                # __raw__ with $elemMatch is another option, but MQ supports nested lookups:
                q_filter |= MQ(items__value__icontains=q)
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

    # include_meta: compute aggregated metadata from documents (topics/tags/difficulties/subtopics)
    include_meta = request.args.get("include_meta")
    if include_meta and include_meta.lower() in ("1", "true", "yes"):
        try:
            meta_agg = {
                "difficulty_levels": set(),
                "topics": set(),
                "subtopics": set(),
                "tags": set(),
            }
            for doc in Model.objects(__raw__=query):
                if getattr(doc, "difficulty_level", None):
                    meta_agg["difficulty_levels"].add(doc.difficulty_level)
                if getattr(doc, "topic", None):
                    meta_agg["topics"].add(doc.topic)
                if getattr(doc, "subtopic", None):
                    meta_agg["subtopics"].add(doc.subtopic)
                for t in getattr(doc, "tags", []) or []:
                    if t:
                        meta_agg["tags"].add(t)
            resp["meta"] = {k: sorted(list(v)) for k, v in meta_agg.items()}
        except Exception:
            # don't block main response if meta computation fails
            current_app.logger.exception("Failed to compute meta for rearrange collection")

    return jsonify(resp), 200

@rearrange_bp.route("/<collection>/<item_id>", methods=["GET"])
@token_required
def get_item(collection, item_id):
    """
    GET /v1/rearrange/<collection>/<item_id>
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


@rearrange_bp.route("/<collection>/<item_id>", methods=["DELETE"])
@token_required
def delete_item(collection, item_id):
    """
    DELETE /v1/rearrange/<collection>/<item_id>
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

    # enforce same-college restriction (if model has college_id)
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

    return jsonify({
        "success": True,
        "message": f"{collection[:-1].replace('_', ' ').capitalize()} deleted successfully"
    }), 200
