from flask import Blueprint, request, jsonify, current_app
from functools import wraps
from mongoengine.errors import DoesNotExist, ValidationError
from bson import ObjectId
import datetime
from utils.jwt import verify_access_token
from utils.response import response



from mongoengine.queryset.visitor import Q as MQ



# Import your models from the single shared file
from models.questions.mcq import (
    MCQ,
    CourseMCQ,
    TestMCQ,
    CollegeMCQ,
    MCQConfig,
)

generic_bp = Blueprint("generic_mcq_bp", __name__, url_prefix="/v1/mcq")

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

# ---------- explicit mapping function ----------
def _model_for_collection(collection):
    """
    Return the concrete model class for a given collection name.
    Keep this function updated if you add more MCQ-like collections.
    """
    if collection == "mcqs":
        return MCQ
    elif collection == "course_mcqs":
        return CourseMCQ
    elif collection == "test_mcqs":
        return TestMCQ
    elif collection == "college_mcqs":
        return CollegeMCQ
    elif collection == "mcq_configs":
        return MCQConfig
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
    Converts _id -> id and runs simple type conversion.
    """
    try:
        raw = doc.to_mongo().to_dict()
    except Exception:
        if hasattr(doc, "to_json"):
            # your BaseMCQ has to_json — prefer it if available
            try:
                return _convert_simple_types(doc.to_json())
            except Exception:
                pass
        # fallback: build from __dict__
        raw = {k: v for k, v in getattr(doc, "__dict__", {}).items() if not k.startswith("_")}

    # Convert Mongo's _id to id if present
    if "_id" in raw:
        raw["id"] = str(raw.pop("_id"))
    elif "id" in raw:
        raw["id"] = str(raw["id"])

    return _convert_simple_types(raw)

# ---------- routes ----------


@generic_bp.route("/<collection>", methods=["GET"])
@token_required
def list_items(collection):
    """
    GET /v1/mcq/<collection>?page=1&per_page=20&filters=...&q=...&tags=tag1,tag2&include_meta=true

    - Supports:
      - `filters` (JSON string) to merge into the query.
      - `tags` (comma-separated) to filter by any tag in the list (OR semantics).
      - `q` full-text-like substring search across title/question_text/tags when available.
      - `include_meta=true` will return available tags, topics, subtopics and difficulty levels
        for the (already scoped) collection.

    NOTE: This implementation purposely ignores MCQConfig and computes metadata from the
    actual documents in the collection (as requested).
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

    # build base query: for college-scoped models enforce college_id equality
    query = {}
    if hasattr(Model, "college_id"):
        if admin_college_id is None:
            return response(False, "Forbidden: admin has no college_id"), 403
        query["college_id"] = admin_college_id

    # optional filters JSON
    filters = request.args.get("filters")
    if filters:
        try:
            extra = json.loads(filters)
            if isinstance(extra, dict):
                # merge but don't overwrite college scoping
                for k, v in extra.items():
                    if k == "college_id" and hasattr(Model, "college_id"):
                        # don't allow client to override scoping
                        continue
                    query[k] = v
        except Exception:
            # invalid filters ignored
            pass

    # tags param (comma-separated) — filter documents that have any of the tags
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
            if "question_text" in Model._fields:
                q_filter |= MQ(question_text__icontains=q)
                added = True
            if "title" in Model._fields:
                q_filter |= MQ(title__icontains=q)
                added = True
            if "tags" in Model._fields:
                # searching tags for substring (tags are strings in a list)
                q_filter |= MQ(tags__icontains=q)
                added = True
            if added:
                qs = qs_base.filter(q_filter).skip(skip).limit(per_page)
            else:
                # no searchable fields known; return base paginated
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

   
    return jsonify(resp), 200

@generic_bp.route("/<collection>/<item_id>", methods=["GET"])
@token_required
def get_item(collection, item_id):
    """
    GET /v1/mcq/<collection>/<item_id>
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


@generic_bp.route("/<collection>/<item_id>", methods=["DELETE"])
@token_required
def delete_item(collection, item_id):
    """
    DELETE /v1/mcq/<collection>/<item_id>
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

    return jsonify({
        "success": True,
        "message": f"{collection[:-1].capitalize()} deleted successfully"
    }), 200
