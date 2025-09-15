# routes/rearrange.py
from flask import Blueprint, request
from math import ceil
from mongoengine.errors import ValidationError, DoesNotExist

# reuse your project's auth + response helpers
from routes.faculty_admin.test.tests import token_required
from utils.response import response

# models (adjust import paths if needed)
from models.questions.rearrange import Rearrange, TestRearrange, Item, Image
from models.test.section import Section, SectionQuestion

rearrange_bp = Blueprint("rearrange", __name__, url_prefix="/test/questions/rearranges")


def rearrange_minimal_to_json(r: Rearrange) -> dict:
    """
    Minimal representation used by list endpoints.
    images omitted intentionally for compactness.
    """
    items_json = []
    for it in r.items or []:
        items_json.append({
            "id": it.item_id,
            "value_preview": (it.value[:120] + "…") if it.value and len(it.value) > 120 else it.value,
            "has_images": bool(it.images)
        })

    created_by = r.created_by or {}
    created_by_min = {"id": created_by.get("id"), "name": created_by.get("name")} if created_by else {}

    return {
        "id": str(r.id),
        "title": r.title,
        "prompt": r.prompt,
        "difficulty_level": r.difficulty_level,
        "topic": r.topic,
        "subtopic": r.subtopic,
        "tags": r.tags or [],
        "correct_order" : r.correct_order,
        "marks": r.marks,
        "negative_marks": getattr(r, "negative_marks", None),
        "time_limit": r.time_limit,
        "is_drag_and_drop": bool(r.is_drag_and_drop),
        "items": items_json,
        "created_by": created_by_min,
    }


@rearrange_bp.route("/", methods=["GET"])
@token_required
def list_rearranges():
    """
    GET /test/questions/rearranges
    Query params:
      - page (int, default 1)
      - per_page (int, default 20, max 200)
      - tags (comma separated, matches ANY tag)
      - topic (exact match)
      - subtopic (exact match)
      - difficulty_level (Easy|Medium|Hard)
      - search (text search against title/prompt)
      - sort_by (marks|difficulty_level|time_limit|title|id)
      - sort_dir (asc|desc, default desc)
    """
    params = request.args

    # pagination
    try:
        page = max(1, int(params.get("page", 1)))
    except Exception:
        page = 1
    try:
        per_page = int(params.get("per_page", 20))
        if per_page <= 0:
            per_page = 20
        per_page = min(per_page, 200)
    except Exception:
        per_page = 20

    # filters
    tags_param = params.get("tags")
    tags = [t.strip() for t in tags_param.split(",") if t.strip()] if tags_param else []
    topic = params.get("topic")
    subtopic = params.get("subtopic")
    difficulty_level = params.get("difficulty_level")
    search = params.get("search", "").strip()

    # sort
    sort_by = params.get("sort_by", None)
    sort_dir = params.get("sort_dir", "desc").lower()
    sort_prefix = "-" if sort_dir == "desc" else ""

    query = {}
    if tags:
        query["tags__in"] = tags
    if topic:
        query["topic"] = topic
    if subtopic:
        query["subtopic"] = subtopic
    if difficulty_level:
        query["difficulty_level"] = difficulty_level

    try:
        qs = Rearrange.objects(**query)

        if search:
            from mongoengine.queryset.visitor import Q as MQ
            qs = qs.filter(MQ(title__icontains=search) | MQ(prompt__icontains=search))

        total = qs.count()

        allowed_sort_fields = {"marks", "negative_marks", "difficulty_level", "time_limit", "title", "id"}
        if sort_by and sort_by in allowed_sort_fields:
            ordering = f"{sort_prefix}{sort_by}"
        else:
            ordering = "-id"

        qs = qs.order_by(ordering)

        start = (page - 1) * per_page
        end = start + per_page
        items = list(qs[start:end])

        total_pages = ceil(total / per_page) if per_page else 1
        items_json = [rearrange_minimal_to_json(r) for r in items]

        # attempt to read a config-like doc if you create one for rearranges; fallback to distinct queries
        try:
            # if you have a RearrangeConfig doc, prefer it. If not, fallback:
            from models.questions.rearrange import RearrangeConfig
            config = RearrangeConfig.objects.first()
        except Exception:
            config = None

        
        topics = Rearrange.objects.distinct("topic") or []
        subtopics = Rearrange.objects.distinct("subtopic") or []
        tags_list = Rearrange.objects.distinct("tags") or []
        difficulty_levels = Rearrange.objects.distinct("difficulty_level") or []

        meta = {
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "topics": sorted([t for t in topics if t]),
            "subtopics": sorted([s for s in subtopics if s]),
            "tags": sorted([t for t in tags_list if t]),
            "difficulty_levels": sorted([d for d in difficulty_levels if d]),
        }

        return response(True, "Rearrange questions fetched", {"items": items_json, "meta": meta}), 200

    except ValidationError as e:
        return response(False, f"Invalid query: {str(e)}"), 400
    except Exception as e:
        return response(False, f"Failed to fetch rearrange questions: {str(e)}"), 500


@rearrange_bp.route("/<string:rearrange_id>/duplicate-to-section", methods=["POST"])
@token_required
def duplicate_rearrange_to_section(rearrange_id):
    """
    POST /test/questions/rearranges/<rearrange_id>/duplicate-to-section
    Body JSON:
      { "section_id": "<section id to attach to>" }

    Behavior:
      - Find the Rearrange by id
      - Duplicate it to TestRearrange (recreate Items & Images; new item_ids generated)
      - Remap correct_order to the new item_ids
      - Append a SectionQuestion with rearrange_ref pointing to new TestRearrange
      - Returns new test_rearrange_id and section_id on success
    """
    data = request.get_json(force=True, silent=True) or {}
    section_id = data.get("section_id")
    if not section_id:
        return response(False, "Missing required field: section_id"), 400

    try:
        original = Rearrange.objects.get(id=rearrange_id)

        # helper to duplicate an Image embedded doc (generate new image_id by default)
        def dup_image(orig_img):
            return Image(
                label=getattr(orig_img, "label", None),
                url=getattr(orig_img, "url", None),
                alt_text=getattr(orig_img, "alt_text", None),
                metadata=getattr(orig_img, "metadata", None) or {}
            )

        # duplicate items and build mapping old_item_id -> new_item_id
        new_items = []
        old_to_new_item_id = {}
        for orig_item in (original.items or []):
            copied_images = [dup_image(img) for img in (orig_item.images or [])]
            new_item = Item(value=orig_item.value, images=copied_images)
            # new_item.item_id will be created by default lambda on instantiation
            new_items.append(new_item)
            old_to_new_item_id[orig_item.item_id] = new_item.item_id

        # remap correct_order using mapping
        new_correct_order = []
        for old_id in (original.correct_order or []):
            new_id = old_to_new_item_id.get(old_id)
            if new_id:
                new_correct_order.append(new_id)
            else:
                # skip unknown ids; alternatively raise an error if you want strict behavior
                pass

        # duplicate question-level and explanation images
        new_question_images = [dup_image(i) for i in (original.question_images or [])]
        new_explanation_images = [dup_image(i) for i in (original.explanation_images or [])]

        test_rearrange = TestRearrange(
            title=original.title,
            prompt=original.prompt,
            question_images=new_question_images,
            items=new_items,
            correct_order=new_correct_order,
            is_drag_and_drop=original.is_drag_and_drop,
            marks=original.marks,
            negative_marks=original.negative_marks,
            difficulty_level=original.difficulty_level,
            explanation=original.explanation,
            explanation_images=new_explanation_images,
            tags=list(original.tags or []),
            time_limit=original.time_limit,
            topic=original.topic,
            subtopic=original.subtopic,
            created_by=getattr(original, "created_by", {"id": "system", "name": "System"})
        )

        test_rearrange.save()

        # attach to section
        try:
            section = Section.objects.get(id=section_id)
        except DoesNotExist:
            # Do NOT attempt a DB transaction here; simply inform client.
            # If you want rollback behavior, uncomment the deletion below.
            # test_rearrange.delete()
            return response(False, f"Section not found: {section_id}"), 404

        sq = SectionQuestion(question_type="rearrange", rearrange_ref=test_rearrange)
        section.questions = section.questions or []
        section.questions.append(sq)
        section.save()

        return response(True, "Rearrange duplicated into TestRearrange and added to section", {
            "original_rearrange_id": str(original.id),
            "test_rearrange_id": str(test_rearrange.id),
            "section_id": str(section.id)
        }), 201

    except DoesNotExist:
        return response(False, f"Rearrange not found: {rearrange_id}"), 404
    except ValidationError as e:
        return response(False, f"Validation error: {str(e)}"), 400
    except Exception as e:
        return response(False, f"Failed to duplicate Rearrange: {str(e)}"), 500
