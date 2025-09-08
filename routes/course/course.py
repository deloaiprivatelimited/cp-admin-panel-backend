from flask import Blueprint, request
from functools import wraps
from mongoengine.queryset.visitor import Q
from mongoengine.errors import ValidationError,DoesNotExist,NotUniqueError
from bson import ObjectId
import re
from utils.jwt import verify_access_token
from utils.response import response
import math
from time import perf_counter
from datetime import datetime
# from models.course import Course, Chapter  # import others if you need them
from models.courses.courses import Course, Chapter,Lesson,Unit
course_bp = Blueprint('course_bp', __name__)

# ---------------------------
# Auth decorator (same style as admin)
# ---------------------------
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
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
# Helpers
# ---------------------------
@course_bp.route("/<course_id>", methods=["GET"])
@token_required
def get_course_by_id(course_id):
    try:
        # Validate ObjectId
        if not ObjectId.is_valid(course_id):
            return response(False, "Invalid course ID"), 400

        course = Course.objects.get(id=course_id)
        return response(True, "Course fetched successfully", course.to_json()), 200

    except DoesNotExist:
        return response(False, "Course not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

@course_bp.route("/<course_id>/chapters", methods=["POST"])
@token_required
def add_chapter_to_course(course_id):
    try:
        # Validate ObjectId
        if not ObjectId.is_valid(course_id):
            return response(False, "Invalid course ID"), 400

        course = Course.objects.get(id=course_id)

        data = request.get_json() or {}
        name = data.get("name")
        tagline = data.get("tagline")
        description = data.get("description")

        if not name:
            return response(False, "Chapter 'name' is required"), 400

        # Create new chapter
        new_chapter = Chapter(
            name=name,
            tagline=tagline,
            description=description
        )
        new_chapter.save()

        # Append to course
        course.chapters.append(new_chapter)
        course.save()

        return response(
            True,
            "Chapter added successfully",
         
                new_chapter.to_json()
          
        ), 201

    except DoesNotExist:
        return response(False, "Course not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

@course_bp.route("/<course_id>/chapters", methods=["GET"])
@token_required
def get_chapters_by_course(course_id):
    try:
        # Validate ObjectId
        if not ObjectId.is_valid(course_id):
            return response(False, "Invalid course ID"), 400

        course = Course.objects.get(id=course_id)

        chapters = [chapter.to_json() for chapter in course.chapters]

        return response(
            True,
            "Chapters fetched successfully",
            {"course_id": str(course.id), "chapters": chapters}
        ), 200

    except DoesNotExist:
        return response(False, "Course not found"), 404
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500



# ---------------------------
# Routes
# ---------------------------


@course_bp.route("/", methods=["GET"])
@token_required
def get_all_courses():
    try:
        q = (request.args.get("q") or "").strip()
        try:
            page = int(request.args.get("page", 1))
            per_page = int(request.args.get("per_page", 10))
        except ValueError:
            return response(False, "page and per_page must be integers"), 400

        page = 1 if page < 1 else page
        per_page = 1 if per_page < 1 else per_page
        per_page = 100 if per_page > 100 else per_page  

        qs = Course.objects

        if q:
            safe = re.escape(q)
            rx = re.compile(safe, re.IGNORECASE)
            qs = qs.filter(
                Q(name=rx) | Q(tagline=rx) | Q(description=rx)
            )

        total = qs.count()
        qs = qs.order_by('name')

        pages = max(1, math.ceil(total / per_page))
        if page > pages:
            page = pages

        offset = (page - 1) * per_page
        page_qs = qs.select_related()[offset: offset + per_page]
        items = list(page_qs)

        data = [c.to_json() for c in items]

        meta = {
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
            "has_next": page < pages,
            "has_prev": page > 1,
        }

        return response(True, "Courses fetched successfully", {"items": data, "meta": meta}), 200

    except ValidationError as ve:
        return response(False, f"Validation error: {str(ve)}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


@course_bp.route("/", methods=["POST"])
@token_required
def add_course():
    try:
        data = request.get_json() or {}

        name = data.get("name")
        tagline = data.get("tagline")
        description = data.get("description")
        thumbnail_url = data.get("thumbnail_url")  # ✅ new

        if not name:
            return response(False, "name is required"), 400

        new_course = Course(
            name=name,
            tagline=tagline,
            description=description,
            thumbnail_url=thumbnail_url  # ✅ save it
        )
        new_course.save()

        return response(True, "Course created successfully", new_course.to_json()), 201

    except NotUniqueError:
        return response(False, "Course with given unique field already exists"), 400
    except (ValidationError, ValueError) as ve:
        return response(False, f"Validation error: {ve}"), 400
    except DoesNotExist as dne:
        return response(False, str(dne)), 404
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


@course_bp.route("/<course_id>", methods=["PUT"])
@token_required
def update_course(course_id):
    try:
        data = request.get_json() or {}
        course = Course.objects.get(id=course_id)

        if "name" in data:
            if not data["name"]:
                return response(False, "name cannot be empty"), 400
            course.name = data["name"]

        if "tagline" in data:
            course.tagline = data["tagline"]

        if "description" in data:
            course.description = data["description"]

        if "thumbnail_url" in data:  # ✅ allow update
            course.thumbnail_url = data["thumbnail_url"]

        course.save()
        return response(True, "Course updated successfully", course.to_json()), 200

    except DoesNotExist:
        return response(False, "Course not found"), 404
    except NotUniqueError:
        return response(False, "Course with given unique field already exists"), 400
    except (ValidationError, ValueError) as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


@course_bp.route("/<course_id>/chapters/<chapter_id>", methods=["PUT"])
@token_required
def update_chapter_in_course(course_id, chapter_id):
    try:
        # Validate ObjectIds
        if not ObjectId.is_valid(course_id):
            return response(False, "Invalid course ID"), 400
        if not ObjectId.is_valid(chapter_id):
            return response(False, "Invalid chapter ID"), 400

        # Fetch course and chapter
        course = Course.objects.get(id=course_id)
        chapter = Chapter.objects.get(id=chapter_id)

        # Ensure chapter belongs to the course
        if not any(str(c.id) == str(chapter.id) for c in course.chapters):
            return response(False, "Chapter does not belong to this course"), 404

        data = request.get_json() or {}

        # Apply partial updates
        if "name" in data:
            if not data["name"]:
                return response(False, "Chapter 'name' cannot be empty"), 400
            chapter.name = data["name"]

        if "tagline" in data:
            chapter.tagline = data["tagline"]

        if "description" in data:
            chapter.description = data["description"]

        # Save changes
        chapter.save()

        return response(
            True,
            "Chapter updated successfully",
            chapter.to_json()
        ), 200

    except DoesNotExist:
        # Could be either Course or Chapter not found
        return response(False, "Course or Chapter not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

@course_bp.route("/<course_id>/chapters/<chapter_id>/lessons", methods=["POST"])
@token_required
def add_lesson_to_chapter(course_id, chapter_id):

    """
    Create a Lesson inside a Chapter of a Course.
    Body:
      {
        "name": "Pointers Basics",                # required
        "tagline": "Get comfy with pointers",     # optional
        "description": "Intro to pointers",       # optional
        "unit_ids": ["66e3...a2f", "66e3...b91"]  # optional list of Unit _ids to attach
      }
    """
    try:
        # Validate IDs
        if not ObjectId.is_valid(course_id):
            return response(False, "Invalid course ID"), 400
        if not ObjectId.is_valid(chapter_id):
            return response(False, "Invalid chapter ID"), 400

        # Fetch course and chapter
        course = Course.objects.get(id=course_id)
        chapter = Chapter.objects.get(id=chapter_id)

        # Ensure chapter belongs to the course
        if not any(str(c.id) == str(chapter.id) for c in course.chapters):
            return response(False, "Chapter does not belong to this course"), 404

        data = request.get_json() or {}
        name = data.get("name")
        tagline = data.get("tagline")
        description = data.get("description")

        if not name:
            return response(False, "Lesson 'name' is required"), 400

        # Collect units if provided
       
        # Create the lesson
        new_lesson = Lesson(
            name=name,
            tagline=tagline,
            description=description,
        )
        new_lesson.save()

        # Attach to chapter
        chapter.lessons.append(new_lesson)
        chapter.save()  # persist change to chapter

        # Success
        payload = {
            "course_id": str(course.id),
            "chapter_id": str(chapter.id),
            "lesson": new_lesson.to_json()
        }
        return response(True, "Lesson added successfully", payload), 201

    except DoesNotExist:
        return response(False, "Course or Chapter not found"), 404
    except (ValidationError, ValueError) as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500
    



@course_bp.route("/<course_id>/chapters/<chapter_id>/lessons/<lesson_id>", methods=["PUT"])
@token_required
def update_lesson_in_chapter(course_id, chapter_id, lesson_id):

    try:
        # Validate IDs
        for _id, label in [(course_id, "course"), (chapter_id, "chapter"), (lesson_id, "lesson")]:
            if not ObjectId.is_valid(_id):
                return response(False, f"Invalid {label} ID"), 400

        # Fetch documents
        course = Course.objects.get(id=course_id)
        chapter = Chapter.objects.get(id=chapter_id)
        lesson = Lesson.objects.get(id=lesson_id)

        # Ensure relationships
        if not any(str(c.id) == str(chapter.id) for c in course.chapters):
            return response(False, "Chapter does not belong to this course"), 404

        if not any(str(l.id) == str(lesson.id) for l in chapter.lessons):
            return response(False, "Lesson does not belong to this chapter"), 404

        data = request.get_json() or {}
        if not data:
            return response(False, "No fields provided to update"), 400

        # Apply partial updates
        if "name" in data:
            if not data["name"]:
                return response(False, "Lesson 'name' cannot be empty"), 400
            lesson.name = data["name"]

        if "tagline" in data:
            lesson.tagline = data["tagline"]

        if "description" in data:
            lesson.description = data["description"]

        # Full replacement of units if unit_ids provided
        lesson.save()

        return response(
            True,
            "Lesson updated successfully",
            {
                "course_id": str(course.id),
                "chapter_id": str(chapter.id),
                "lesson": lesson.to_json()
            }
        ), 200

    except DoesNotExist:
        return response(False, "Course, Chapter, or Lesson not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500
    
    
@course_bp.route("/<course_id>/chapters/<chapter_id>/lessons/<lesson_id>/units", methods=["GET"])
@token_required
def get_units_in_lesson(course_id, chapter_id, lesson_id):
    """
    Return ONLY the unit name and type for all units in the given lesson,
    after verifying the lesson belongs to the chapter and the chapter belongs to the course.

    Response data:
    {
      "course_id": "...",
      "chapter_id": "...",
      "lesson_id": "...",
      "units": [
        {"name": "Intro to Pointers", "unit_type": "text"},
        {"name": "Pointer Quiz 1", "unit_type": "mcq"}
      ]
    }
    """
    try:
        # Validate IDs
        for _id, label in [(course_id, "course"), (chapter_id, "chapter"), (lesson_id, "lesson")]:
            if not ObjectId.is_valid(_id):
                return response(False, f"Invalid {label} ID"), 400

        # Fetch the documents
        course = Course.objects.get(id=course_id)
        chapter = Chapter.objects.get(id=chapter_id)
        lesson = Lesson.objects.get(id=lesson_id)

        # Check relationships
        if not any(str(c.id) == str(chapter.id) for c in course.chapters):
            return response(False, "Chapter does not belong to this course"), 404

        if not any(str(l.id) == str(lesson.id) for l in chapter.lessons):
            return response(False, "Lesson does not belong to this chapter"), 404

        # Build minimal unit payload (only name and unit_type)
  # Build minimal unit payload (only id, name, unit_type, and mcq id or null)
        units_payload = []
        for u in lesson.units:
            mcq_id = None
            rearrange_id = None
            coding_id = None
            # safe check: u.mcq may be a DBRef / None
            if getattr(u, "mcq", None):
                # if it's a ReferenceField, store the referenced id as string
                mcq_id = str(u.mcq.id) if getattr(u.mcq, "id", None) else str(u.mcq)
            if getattr(u, "rearrange", None):
                rearrange_id = str(u.rearrange.id) if getattr(u.rearrange, "id", None) else str(u.rearrange)
            if getattr(u, "coding", None):
                coding_id = str(u.coding.id) if getattr(u.coding, "id", None) else str(u.coding)
            units_payload.append({
                "id": str(u.id),
                "name": u.name,
                "unit_type": u.unit_type,
                "mcq": mcq_id,
                "rearrange": rearrange_id,
                "coding": coding_id
            })
        return response(
            True,
            "Units fetched successfully",
            {
                "course_id": str(course.id),
                "chapter_id": str(chapter.id),
                "lesson_id": str(lesson.id),
                "units": units_payload
            }
        ), 200

    except DoesNotExist:
        return response(False, "Course, Chapter, or Lesson not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

@course_bp.route("/<course_id>/chapters/<chapter_id>/lessons/<lesson_id>/units", methods=["POST"])
@token_required
def add_unit_to_lesson(course_id, chapter_id, lesson_id):
    try:
        # Validate IDs
        for _id, label in [(course_id, "course"), (chapter_id, "chapter"), (lesson_id, "lesson")]:
            if not ObjectId.is_valid(_id):
                return response(False, f"Invalid {label} ID"), 400

        # Fetch documents
        course = Course.objects.get(id=course_id)
        chapter = Chapter.objects.get(id=chapter_id)
        lesson = Lesson.objects.get(id=lesson_id)

        # Verify relationships
        if chapter not in course.chapters:
            return response(False, "Chapter does not belong to this course"), 404
        if lesson not in chapter.lessons:
            return response(False, "Lesson does not belong to this chapter"), 404

        data = request.get_json() or {}
        name = data.get("name")
        unit_type = data.get("unit_type")

        if not name or not unit_type:
            return response(False, "'name' and 'unit_type' are required"), 400

        if unit_type not in ["text", "mcq","rearrange","coding"]:
            return response(False, "Invalid unit_type (must be 'text' or 'mcq')"), 400

        # Only minimal unit creation (text or mcq must be filled later)
        new_unit = Unit(name=name, unit_type=unit_type)

   
        new_unit.save()
        lesson.update(push__units=new_unit)

        return response(True, "Unit added successfully", new_unit.to_json()), 201

    except DoesNotExist:
        return response(False, "Course, Chapter, or Lesson not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

@course_bp.route("/<course_id>/chapters/<chapter_id>/lessons/<lesson_id>/units/<unit_id>", methods=["PUT"])
@token_required
def update_unit_name(course_id, chapter_id, lesson_id, unit_id):
    try:
        # Validate IDs
        for _id, label in [(course_id, "course"), (chapter_id, "chapter"), (lesson_id, "lesson"), (unit_id, "unit")]:
            if not ObjectId.is_valid(_id):
                return response(False, f"Invalid {label} ID"), 400

        # Fetch documents
        course = Course.objects.get(id=course_id)
        chapter = Chapter.objects.get(id=chapter_id)
        lesson = Lesson.objects.get(id=lesson_id)
        unit = Unit.objects.get(id=unit_id)

        # Verify relationships
        if chapter not in course.chapters:
            return response(False, "Chapter does not belong to this course"), 404
        if lesson not in chapter.lessons:
            return response(False, "Lesson does not belong to this chapter"), 404
        if unit not in lesson.units:
            return response(False, "Unit does not belong to this lesson"), 404

        data = request.get_json() or {}
        name = data.get("name")

        if not name:
            return response(False, "'name' is required to update"), 400

        unit.name = name
        unit.save()

        return response(True, "Unit name updated successfully", unit.to_json()), 200

    except DoesNotExist:
        return response(False, "Course, Chapter, Lesson, or Unit not found"), 404
    except ValidationError as ve:
        print(e  )
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

@course_bp.route("/<course_id>/chapters/<chapter_id>/lessons/<lesson_id>/units/reorder", methods=["PUT"])
@token_required
def reorder_units_in_lesson(course_id, chapter_id, lesson_id):
    """
    Reorder units in a lesson.
    Body: { "unit_ids": ["<unitId1>", "<unitId2>", ...] }  # full new order
    """
    try:
        # Validate IDs
        for _id, label in [(course_id, "course"), (chapter_id, "chapter"), (lesson_id, "lesson")]:
            if not ObjectId.is_valid(_id):
                return response(False, f"Invalid {label} ID"), 400

        course = Course.objects.get(id=course_id)
        chapter = Chapter.objects.get(id=chapter_id)
        lesson = Lesson.objects.get(id=lesson_id)

        # Relationship checks
        if chapter not in course.chapters:
            return response(False, "Chapter does not belong to this course"), 404
        if lesson not in chapter.lessons:
            return response(False, "Lesson does not belong to this chapter"), 404

        data = request.get_json() or {}
        unit_ids = data.get("unit_ids") or []
        if not isinstance(unit_ids, list) or not unit_ids:
            return response(False, "'unit_ids' (non-empty list) is required"), 400

        # Current set
        current_ids = [str(u.id) for u in lesson.units]

        # Must be a permutation of current_ids
        if set(unit_ids) != set(current_ids) or len(unit_ids) != len(current_ids):
            return response(False, "unit_ids must be a permutation of current lesson units"), 400

        # Build the new ordered list of Unit references
        id_to_unit = {str(u.id): u for u in lesson.units}
        new_units = [id_to_unit[uid] for uid in unit_ids]

        lesson.units = new_units
        lesson.save()

        # Return minimal payload (id,name,type) in new order
        payload = [{
            "id": str(u.id),
            "name": u.name,
            "unit_type": u.unit_type
        } for u in lesson.units]

        return response(True, "Units reordered successfully", {
            "course_id": str(course.id),
            "chapter_id": str(chapter.id),
            "lesson_id": str(lesson.id),
            "units": payload
        }), 200

    except DoesNotExist:
        return response(False, "Course, Chapter, or Lesson not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

@course_bp.route("/<course_id>/chapters/<chapter_id>/lessons/<lesson_id>/units/<unit_id>", methods=["PUT"])
@token_required
def update_unit_name_only(course_id, chapter_id, lesson_id, unit_id):
    """
    Update ONLY the unit's 'name'. Nothing else is modified.
    Body: { "name": "New Unit Name" }
    """
    try:
        # Validate IDs
        for _id, label in [(course_id, "course"), (chapter_id, "chapter"), (lesson_id, "lesson"), (unit_id, "unit")]:
            if not ObjectId.is_valid(_id):
                return response(False, f"Invalid {label} ID"), 400

        # Fetch docs
        course = Course.objects.get(id=course_id)
        chapter = Chapter.objects.get(id=chapter_id)
        lesson = Lesson.objects.get(id=lesson_id)
        unit = Unit.objects.get(id=unit_id)

        # Relationship checks (compare by id to avoid object identity issues)
        if not any(str(c.id) == str(chapter.id) for c in course.chapters):
            return response(False, "Chapter does not belong to this course"), 404
        if not any(str(l.id) == str(lesson.id) for l in chapter.lessons):
            return response(False, "Lesson does not belong to this chapter"), 404
        if not any(str(u.id) == str(unit.id) for u in lesson.units):
            return response(False, "Unit does not belong to this lesson"), 404

        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        if not name:
            return response(False, "'name' is required and cannot be empty"), 400

        # Update only the name
        unit.name = name
        unit.save()

        return response(True, "Unit name updated successfully", {
            "course_id": str(course.id),
            "chapter_id": str(chapter.id),
            "lesson_id": str(lesson.id),
            "unit": unit.to_json()
        }), 200

    except DoesNotExist:
        return response(False, "Course, Chapter, Lesson, or Unit not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

# GET unit text by unit id only (no course/chapter/lesson validation)
@course_bp.route("/units/<unit_id>/text", methods=["GET"])
@token_required
def get_unit_text_by_id(unit_id):
    try:
        if not ObjectId.is_valid(unit_id):
            return response(False, "Invalid unit ID"), 400

        unit = Unit.objects.get(id=unit_id)

        if getattr(unit, "unit_type", None) != "text":
            return response(False, "Unit is not of type 'text'"), 400

        content = getattr(unit, "content", None)
        if content is None:
            content = getattr(unit, "text", None)

        return response(True, "Unit text fetched successfully", {
            "unit_id": str(unit.id),
            "name": unit.name,
            "content": content
        }), 200

    except DoesNotExist:
        return response(False, "Unit not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

# UPDATE unit text by unit id only (no course/chapter/lesson validation)
@course_bp.route("/units/<unit_id>/text", methods=["PUT"])
@token_required
def update_unit_text_by_id(unit_id):
    try:
        if not ObjectId.is_valid(unit_id):
            return response(False, "Invalid unit ID"), 400

        unit = Unit.objects.get(id=unit_id)

        if getattr(unit, "unit_type", None) != "text":
            return response(False, "Unit is not of type 'text' and cannot be updated here"), 400

        data = request.get_json() or {}
        # accept either 'content' or 'text' in body
        new_content = (data.get("content") or data.get("text") or "").strip()
        if not new_content:
            return response(False, "'content' is required and cannot be empty"), 400

        # write to whichever field exists or to 'content' by default
        if hasattr(unit, "content"):
            unit.content = new_content
        else:
            unit.text = new_content

        unit.save()

        return response(True, "Unit text updated successfully", {
            "unit_id": str(unit.id),
            "name": unit.name,
            "content": new_content
        }), 200

    except DoesNotExist:
        return response(False, "Unit not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


from models.courses.courses import Unit, TextUnit

# ---------------------------
# GET unit content by unit id
# ---------------------------
@course_bp.route("/units/<unit_id>/content", methods=["GET"])
@token_required
def get_unit_content_by_id(unit_id):
    try:
        if not ObjectId.is_valid(unit_id):
            return response(False, "Invalid unit ID"), 400

        unit = Unit.objects.get(id=unit_id)

        if unit.unit_type != "text":
            return response(False, "Unit is not of type 'text'"), 400

        # Always return a string, default to ""
        content = unit.text.content if unit.text else ""

        return response(True, "Unit content fetched successfully", {
            "unit_id": str(unit.id),
            "name": unit.name,
            "content": content
        }), 200

    except DoesNotExist:
        return response(False, "Unit not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


# ---------------------------
# UPDATE unit content by unit id
# ---------------------------
@course_bp.route("/units/<unit_id>/content", methods=["PUT"])
@token_required
def update_unit_content_by_id(unit_id):
    try:
        if not ObjectId.is_valid(unit_id):
            return response(False, "Invalid unit ID"), 400

        unit = Unit.objects.get(id=unit_id)

        if unit.unit_type != "text":
            return response(False, "Unit is not of type 'text'"), 400

        data = request.get_json() or {}
        if "content" not in data:
            return response(False, "'content' field is required"), 400

        new_content = data.get("content")  # allow empty string

        # If unit.text doesn’t exist yet, create it
        if not unit.text:
            unit.text = TextUnit(content=new_content)
        else:
            unit.text.content = new_content

        unit.save()

        return response(True, "Unit content updated successfully", {
            "unit_id": str(unit.id),
            "name": unit.name,
            "content": unit.text.content if unit.text else ""
        }), 200

    except DoesNotExist:
        return response(False, "Unit not found"), 404
    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500





@course_bp.route("/<course_id>", methods=["DELETE"])
@token_required
def delete_course(course_id):
    try:
        course = Course.objects.get(id=course_id)
        course.delete()   # cascade handles everything
        return response(True, "Course and all related documents deleted successfully"), 200
    except DoesNotExist:
        return response(False, "Course not found"), 404
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

@course_bp.route("/<course_id>/chapters/<chapter_id>", methods=["DELETE"])
@token_required
def delete_chapter_from_course(course_id, chapter_id):
    try:
        course = Course.objects.get(id=course_id)
        chapter = Chapter.objects.get(id=chapter_id)

        # optional: check membership
        if chapter not in course.chapters:
            return response(False, "Chapter does not belong to this course"), 404

        # remove reference from course
        course.update(pull__chapters=chapter)

        # cascade handles lessons/units
        chapter.delete()

        return response(True, "Chapter deleted successfully"), 200
    except DoesNotExist:
        return response(False, "Course or Chapter not found"), 404
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500

@course_bp.route("/<course_id>/chapters/<chapter_id>/lessons/<lesson_id>/units/<unit_id>", methods=["DELETE"])
@token_required
def delete_unit_from_lesson(course_id, chapter_id, lesson_id, unit_id):
    try:
        course = Course.objects.get(id=course_id)
        chapter = Chapter.objects.get(id=chapter_id)
        lesson = Lesson.objects.get(id=lesson_id)
        unit = Unit.objects.get(id=unit_id)

        # Ensure relationships
        if chapter not in course.chapters:
            return response(False, "Chapter does not belong to this course"), 404
        if lesson not in chapter.lessons:
            return response(False, "Lesson does not belong to this chapter"), 404
        if unit not in lesson.units:
            return response(False, "Unit does not belong to this lesson"), 404

        # Remove reference from lesson
        lesson.update(pull__units=unit)

        # Cascade delete will remove mcq/rearrange/coding payloads
        unit.delete()

        return response(True, "Unit deleted successfully"), 200

    except DoesNotExist:
        return response(False, "Course, Chapter, Lesson, or Unit not found"), 404
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500


@course_bp.route("/<course_id>/chapters/<chapter_id>/lessons/<lesson_id>", methods=["DELETE"])
@token_required
def delete_lesson_from_chapter(course_id, chapter_id, lesson_id):
    try:
        course = Course.objects.get(id=course_id)
        chapter = Chapter.objects.get(id=chapter_id)
        lesson = Lesson.objects.get(id=lesson_id)

        # Ensure relationships
        if lesson not in chapter.lessons:
            return response(False, "Lesson does not belong to this chapter"), 404
        if chapter not in course.chapters:
            return response(False, "Chapter does not belong to this course"), 404

        # Remove reference from chapter
        chapter.update(pull__lessons=lesson)

        # Cascade delete will remove units inside this lesson
        lesson.delete()

        return response(True, "Lesson (and its units) deleted successfully"), 200

    except DoesNotExist:
        return response(False, "Course, Chapter, or Lesson not found"), 404
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500
