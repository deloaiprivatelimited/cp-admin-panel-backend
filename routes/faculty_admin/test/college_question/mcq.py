from flask import Blueprint, request
from math import ceil
from mongoengine.errors import ValidationError, DoesNotExist

from utils.response import response
# reuse token_required from your other routes (adjust import path if needed)
# from routes.f.test.tests import token_required
from routes.faculty_admin.test.tests import token_required
from models.questions.mcq import CollegeMCQ as MCQ, MCQConfig

mcq_bp = Blueprint("collge_mcq", __name__, url_prefix="/test/college-questions/mcqs")


def mcq_minimal_to_json(mcq: MCQ) -> dict:
    """
    Minimal representation used by list endpoints.
    NOTE: images are intentionally excluded from this minimal view.
    """
    options_json = []
    for o in mcq.options or []:
        options_json.append({
            "id": o.option_id,
            "text": o.value,
            "is_correct": o.option_id in (mcq.correct_options or [])
        })

    created_by = mcq.created_by or {}
    created_by_min = {
        "id": created_by.get("id"),
        "name": created_by.get("name")
    } if created_by else {}

    return {
        "id": str(mcq.id),
        "title": mcq.title,
        "question": mcq.question_text,
        "difficulty_level": mcq.difficulty_level,
        "topic": mcq.topic,
        "subtopic": mcq.subtopic,
        "tags": mcq.tags or [],
        "marks": mcq.marks,
        "negative_marks": getattr(mcq, "negative_marks", None),
        "time_limit": mcq.time_limit,
        "is_multiple": bool(mcq.is_multiple),
        "options": options_json,  # images omitted intentionally
        "created_by": created_by_min,
    }


@mcq_bp.route("/", methods=["GET"])
@token_required
def list_mcqs():
    print('yes')
    """
    GET /mcqs
    Query params:
      - page (int, default 1)
      - per_page (int, default 20)
      - tags (comma separated, matches ANY tag)
      - topic (exact match)
      - subtopic (exact match)
      - difficulty_level (exact match: Easy|Medium|Hard)
      - search (optional text search against title/question_text)
      - sort_by (optional field name, default: created at / id)
      - sort_dir (asc|desc, default desc)

    Response:
      {
        success: true,
        message: "...",
        data: {
          items: [... minimal mcq ...],
          meta: {
            total: int,
            page: int,
            per_page: int,
            total_pages: int,
            topics: [...],
            subtopics: [...],
            tags: [...],
            difficulty_levels: [...],
          }
        }
      }
    """
    params = request.args

    # pagination
    try:
        page = max(1, int(params.get("page", 1)))
    except ValueError:
        page = 1
    try:
        per_page = int(params.get("per_page", 20))
        if per_page <= 0:
            per_page = 20
        # cap per_page to prevent abuse
        per_page = min(per_page, 200)
    except ValueError:
        per_page = 20

    # filters
    tags_param = params.get("tags")
    tags = [t.strip() for t in tags_param.split(",") if t.strip()] if tags_param else []

    topic = params.get("topic")
    subtopic = params.get("subtopic")
    difficulty_level = params.get("difficulty_level")

    search = params.get("search", "").strip()

    # sort
    sort_by = params.get("sort_by", None)  # e.g., "marks" or "difficulty_level"
    sort_dir = params.get("sort_dir", "desc").lower()
    sort_prefix = "-" if sort_dir == "desc" else ""

    # build query
    query = {}
    # tags: match any provided tag in the mcq.tags list
    if tags:
        query["tags__in"] = tags
    if topic:
        query["topic"] = topic
    if subtopic:
        query["subtopic"] = subtopic
    if difficulty_level:
        query["difficulty_level"] = difficulty_level

    try:
        # base queryset
        qs = MCQ.objects(**query)

        # basic search (title or question_text) - case-insensitive contains
        if search:
            # MongoEngine Q for OR
            from mongoengine.queryset.visitor import Q as MQ
            qs = qs.filter(MQ(title__icontains=search) | MQ(question_text__icontains=search))

        total = qs.count()

        # sorting: default by id (descending)
        if sort_by:
            # ensure not allowing arbitrary injection; only allow a whitelist
            allowed_sort_fields = {
                "marks", "negative_marks", "difficulty_level", "time_limit", "title", "id"
            }
            if sort_by not in allowed_sort_fields:
                sort_by = "id"
            ordering = f"{sort_prefix}{sort_by}"
        else:
            ordering = "-id"  # newest first by default

        qs = qs.order_by(ordering)

        # pagination slicing (mongoengine supports skip/limit via [start:end])
        start = (page - 1) * per_page
        end = start + per_page
        items = list(qs[start:end])

        total_pages = ceil(total / per_page) if per_page else 1

        items_json = [m.to_json() for m in items]

        # meta: try to use MCQConfig document if available for canonical lists
        config = None
        try:
            config = MCQConfig.objects(collection_name="mcqs").first()
        except Exception:
            # fall back to first config if a query fails for some reason
            config = MCQConfig.objects.first()

       
            # fallback: aggregate from MCQ collection
            # NOTE: these queries are simple and may be slow on large collections;
            # consider maintaining MCQConfig or indexes for production.
        topics = MCQ.objects.distinct("topic") or []
        subtopics = MCQ.objects.distinct("subtopic") or []
        tags_list = MCQ.objects.distinct("tags") or []
        difficulty_levels = MCQ.objects.distinct("difficulty_level") or []
        # print(page)
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

        data = {"items": items_json, "meta": meta}
        print(len(data["items"]))
        return response(True, "MCQs fetched", data), 200

    except ValidationError as e:
        return response(False, f"Invalid query: {str(e)}"), 400
    except Exception as e:

        return response(False, f"Failed to fetch MCQs: {str(e)}"), 500


# add these imports at top of your mcq routes file (adjust path if needed)
from mongoengine.errors import ValidationError, DoesNotExist
from flask import jsonify
from mongoengine.errors import DoesNotExist

# import classes needed for duplication
from models.questions.mcq import CollegeMCQ as MCQ, MCQConfig, TestMCQ, Option, Image  # Option/Image are embedded docs
from models.test.section import Section, SectionQuestion

# new endpoint: duplicate mcq into test_mcqs and attach to section
@mcq_bp.route("/<string:mcq_id>/duplicate-to-section", methods=["POST"])
@token_required
def duplicate_mcq_to_section(mcq_id):
    """
    POST /test/questions/mcqs/<mcq_id>/duplicate-to-section
    Body JSON:
      {
        "section_id": "<section id to attach to>"
      }
    Behavior:
      - Finds the MCQ by id
      - Creates a new TestMCQ document by copying fields (options/images are re-created)
      - Ensures new option_ids are generated and maps correct_options to those new ids
      - Appends an embedded SectionQuestion to the Section.questions list with mcq_ref referencing the new TestMCQ
      - Returns new test_mcq id and section id on success
    """
    data = request.get_json(force=True, silent=True) or {}
    section_id = data.get("section_id")
    if not section_id:
        return response(False, "Missing required field: section_id"), 400

    try:
        # load original MCQ
        original = MCQ.objects.get(id=mcq_id)

        # -- duplicate images helper --
        def dup_image(orig_img):
            # create a new Image embedded document (new image_id will be generated by default)
            return Image(
                label=getattr(orig_img, "label", None),
                url=getattr(orig_img, "url", None),
                alt_text=getattr(orig_img, "alt_text", None),
                metadata=getattr(orig_img, "metadata", None) or {}
            )

        # -- duplicate options and keep mapping for correct options --
        new_options = []
        old_to_new_option_id = {}
        for orig_opt in (original.options or []):
            # create a new Option (option_id will be autogenerated if not specified)
            # copy images (create new Image embedded docs)
            copied_images = [dup_image(img) for img in (orig_opt.images or [])]
            new_opt = Option(value=orig_opt.value, images=copied_images)
            # new_opt.option_id will be populated by default factory when saved/serialized
            # but to access it right away we ensure it has an id attribute (mongoengine EmbeddedDocument allows this)
            new_options.append(new_opt)
            # store mapping from old to new - new_opt.option_id should exist because Default lambda sets it on creation
            old_to_new_option_id[orig_opt.option_id] = new_opt.option_id

        # remap correct_options to new option ids
        new_correct_options = []
        for old_id in (original.correct_options or []):
            new_id = old_to_new_option_id.get(old_id)
            # if mapping not found (weird), skip it
            if new_id:
                new_correct_options.append(new_id)

        # duplicate question-level images & explanation images
        new_question_images = [dup_image(i) for i in (original.question_images or [])]
        new_explanation_images = [dup_image(i) for i in (original.explanation_images or [])]

        # create TestMCQ with copied fields
        test_mcq = TestMCQ(
            title=original.title,
            question_text=original.question_text,
            question_images=new_question_images,
            options=new_options,
            correct_options=new_correct_options,
            is_multiple=original.is_multiple,
            marks=original.marks,
            negative_marks=original.negative_marks,
            difficulty_level=original.difficulty_level,
            explanation=original.explanation,
            explanation_images=new_explanation_images,
            tags=list(original.tags or []),
            time_limit=original.time_limit,
            topic=original.topic,
            subtopic=original.subtopic,
            # for created_by: you may want to set this to the current user; preserve for now
            created_by=getattr(original, "created_by", {"id": "system", "name": "System"}),
        )

        # save the duplicated test mcq
        test_mcq.save()

        # attach to section
        try:
            section = Section.objects.get(id=section_id)
        except DoesNotExist:
            # rollback the created test_mcq? For simplicity we keep it but inform client
            return response(False, f"Section not found: {section_id}"), 404

        # create embedded SectionQuestion and append
        sq = SectionQuestion(question_type="mcq", mcq_ref=test_mcq)
        section.questions = section.questions or []
        section.questions.append(sq)
        section.save()

        return response(True, "MCQ duplicated into TestMCQ and added to section", {
            "original_mcq_id": str(original.id),
            "test_mcq_id": str(test_mcq.id),
            "section_id": str(section.id)
        }), 201

    except DoesNotExist:
        return response(False, f"MCQ not found: {mcq_id}"), 404
    except ValidationError as e:
        return response(False, f"Validation error: {str(e)}"), 400
    except Exception as e:
        # log exception in real app
        return response(False, f"Failed to duplicate MCQ: {str(e)}"), 500
