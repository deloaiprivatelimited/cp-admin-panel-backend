# routes/coding.py
from flask import Blueprint, request
from math import ceil
from mongoengine.errors import ValidationError, DoesNotExist

from routes.faculty_admin.test.tests import token_required
from utils.response import response

# Adjust import paths to your project layout if necessary
# Your provided models.py defined Question, TestQuestion, TestCaseGroup, TestCase, Submission etc.
from models.questions.coding import Question, TestQuestion, TestCaseGroup, TestCase  # if your module is models.py
# If your project uses a different module for question models, adapt the imports:
# from models.question import Question, TestQuestion
# from models.testcase import TestCaseGroup, TestCase

from models.test.section import Section, SectionQuestion

coding_bp = Blueprint("coding", __name__, url_prefix="/test/questions/coding")



def coding_minimal_to_json(q: Question) -> dict:
    """Full representation including all CodingData fields."""

    return {
        "id": str(q.id),
        "title": q.title,
        "topic": getattr(q, "topic", "") or "",
        "subtopic": getattr(q, "subtopic", "") or "",
        "tags": getattr(q, "tags", []) or [],
        "short_description": getattr(q, "short_description", "") or "",
        "long_description_markdown": getattr(q, "long_description_markdown", "") or "",
        "difficulty": getattr(q, "difficulty", "") or "",
        "points": getattr(q, "points", 0),
        "time_limit_ms": getattr(q, "time_limit_ms", 2000),
        "memory_limit_kb": getattr(q, "memory_limit_kb", 65536),
        "sample_io": [
            {
                "input_text": io.input_text,
                "output": io.output,
                "explanation": io.explanation or "",
            }
            for io in getattr(q, "sample_io", []) or []
        ],
        "allowed_languages": getattr(q, "allowed_languages", []) or [],
        "predefined_boilerplates": getattr(q, "predefined_boilerplates", {}) or {},
                "solution_code": getattr(q, "solution_code", {}) or {},

        "published": getattr(q, "published", False),
    }


@coding_bp.route("/", methods=["GET"])
@token_required
def list_coding_questions():
    """
    GET /test/questions/coding/
    Query params:
      - page (int, default 1)
      - per_page (int, default 20, max 200)
      - tags (comma separated)
      - topic (exact)
      - subtopic (exact)
      - difficulty (easy|medium|hard)
      - search (text on title/short_description/long_description_markdown)
      - sort_by (points|time_limit_ms|difficulty|title|id)
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
    difficulty = params.get("difficulty")
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
    if difficulty:
        query["difficulty"] = difficulty

    try:
        qs = Question.objects(**query)

        if search:
            from mongoengine.queryset.visitor import Q as MQ
            qs = qs.filter(
                MQ(title__icontains=search) |
                MQ(short_description__icontains=search) |
                MQ(long_description_markdown__icontains=search)
            )

        total = qs.count()

        allowed_sort_fields = {"points", "time_limit_ms", "difficulty", "title", "id"}
        if sort_by and sort_by in allowed_sort_fields:
            ordering = f"{sort_prefix}{sort_by}"
        else:
            ordering = "-id"

        qs = qs.order_by(ordering)

        start = (page - 1) * per_page
        end = start + per_page
        items = list(qs[start:end])

        total_pages = ceil(total / per_page) if per_page else 1
        items_json = [q.to_safe_json() for q in items]

        # derive meta (topics/tags/difficulties) by simple distinct queries; consider keeping a config doc
        topics = Question.objects.distinct("topic") or []
        subtopics = Question.objects.distinct("subtopic") or []
        tags_list = Question.objects.distinct("tags") or []
        difficulty_levels = Question.objects.distinct("difficulty") or []

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

        return response(True, "Coding questions fetched", {"items": items_json, "meta": meta}), 200

    except ValidationError as e:
        return response(False, f"Invalid query: {str(e)}"), 400
    except Exception as e:
        return response(False, f"Failed to fetch coding questions: {str(e)}"), 500


@coding_bp.route("/<string:question_id>/duplicate-to-section", methods=["POST"])
@token_required
def duplicate_coding_to_section(question_id):
    """
    POST /test/questions/coding/<question_id>/duplicate-to-section
    Body JSON:
      { "section_id": "<section id to attach to>" }

    Behavior:
      - Finds Question by id
      - Duplicates it into TestQuestion
      - Duplicates TestCaseGroup documents (but reuses TestCase documents)
      - Appends an embedded SectionQuestion with question_type="coding" and coding_ref pointing to the new test question
      - Returns new test_question_id and section_id
    """
    data = request.get_json(force=True, silent=True) or {}
    section_id = data.get("section_id")
    if not section_id:
        return response(False, "Missing required field: section_id"), 400

    try:
        original = Question.objects.get(id=question_id)

        # duplicate TestCaseGroup docs but reuse TestCase references
        new_group_refs = []
        for group_ref in (getattr(original, "testcase_groups", []) or []):
            try:
                # group_ref may be an object id or TestCaseGroup instance
                # attempt to load the group document if needed
                if isinstance(group_ref, str):
                    orig_group = TestCaseGroup.objects.get(id=group_ref)
                else:
                    orig_group = group_ref
            except Exception:
                # skip invalid group references
                continue

            new_group = TestCaseGroup(
                question_id=str(original.id),  # denormalized link to parent; keep original id or change if you prefer
                name=orig_group.name,
                weight=getattr(orig_group, "weight", 0),
                visibility=getattr(orig_group, "visibility", "hidden"),
                scoring_strategy=getattr(orig_group, "scoring_strategy", "binary"),
                # reuse TestCase references (do NOT duplicate TestCase docs). If you want to copy testcases,
                # change this to create new TestCase documents and reference them instead.
                cases=list(getattr(orig_group, "cases", []) or [])
            )
            new_group.save()
            new_group_refs.append(new_group)  # store the saved doc reference

        # create the TestQuestion copy (copy most fields; recreate embedded docs as necessary)
        test_q = TestQuestion(
            title=original.title,
            topic=original.topic,
            subtopic=original.subtopic,
            tags=list(original.tags or []),
            short_description=original.short_description,
            long_description_markdown=original.long_description_markdown,
            difficulty=original.difficulty,
            points=original.points,
            time_limit_ms=original.time_limit_ms,
            memory_limit_kb=original.memory_limit_kb,
            predefined_boilerplates=dict(original.predefined_boilerplates or {}),
            solution_code=dict(original.solution_code or {}),
            show_solution=original.show_solution,
            run_code_enabled=original.run_code_enabled,
            submission_enabled=original.submission_enabled,
            show_boilerplates=original.show_boilerplates,
            testcase_groups=[g for g in new_group_refs],
            published=original.published,
            version=original.version,
            authors=list(original.authors or []),
            attempt_policy=getattr(original, "attempt_policy", None),
            sample_io=list(getattr(original, "sample_io", []) or []),
            allowed_languages=list(getattr(original, "allowed_languages", []) or []),
            # created_by preserved; you may prefer g.current_user if token_required sets it
            created_at=original.created_at,
            # created_by=getattr(original, "created_by", {"id": "system", "name": "System"}),
        )

        test_q.save()

        # attach to section
        try:
            section = Section.objects.get(id=section_id)
        except DoesNotExist:
            # optional: rollback created groups & test_q here if you want strict behavior
            # for now, leave them and inform client that section was not found
            return response(False, f"Section not found: {section_id}"), 404

        sq = SectionQuestion(question_type="coding", coding_ref=test_q)
        section.questions = section.questions or []
        section.questions.append(sq)
        section.save()

        return response(True, "Coding question duplicated into TestQuestion and added to section", {
            "original_question_id": str(original.id),
            "test_question_id": str(test_q.id),
            "section_id": str(section.id)
        }), 201

    except DoesNotExist:
        return response(False, f"Question not found: {question_id}"), 404
    except ValidationError as e:
        return response(False, f"Validation error: {str(e)}"), 400
    except Exception as e:
        return response(False, f"Failed to duplicate coding question: {str(e)}"), 500
