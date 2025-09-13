# routes/coding_questions_routes.py

from flask import Blueprint, request
from functools import wraps
from datetime import datetime
from mongoengine.errors import ValidationError, DoesNotExist
from mongoengine.queryset.visitor import Q

from models.questions.coding import (
    Question, TestCaseGroup,
    SampleIO, AttemptPolicy,TestCase
)
from utils.jwt import verify_access_token
from utils.response import response

coding_q_bp = Blueprint("coding_q_bp", __name__)


# ---------------------------
# Token decorator
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
# Create new coding question (no testcases)
# ---------------------------

@coding_q_bp.route("/minimal", methods=["POST"])
@token_required
def add_question_minimal():
    """
    Create question with ONLY title and authors.
    'authors' is filled directly with request.admin (from token).
    short_description is optional.
    """
    try:
        data = request.get_json(force=True) or {}
        title = data.get("title")
        if not title:
            return response(False, "title is required"), 400

        # optional short_description
        short_description = data.get("short_description", "")
        topic = data.get("topic", "").strip()
        short_description = short_description.strip()

        print(topic)
        # store admin payload exactly as it is
        admin_payload = getattr(request, "admin", {}) or {}
                # subtopic: not in model by default — attach dynamically (no persistence unless model updated)
                # Persist subtopic (ensure model has `subtopic` StringField)
        subtopic = data.get("subtopic", "").strip()
        print(subtopic)


        q = Question(
            topic=topic,
            title=title,
            subtopic=subtopic,
            short_description=short_description,
            authors=[admin_payload],  # just store raw admin dict
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        print(q.topic)
        print(q.subtopic)
        q.save()

        return response(True, "Coding Question created", {"id": str(q.id), "title": q.title}), 201

    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500


# ---------------------------
# List minimal questions (public) - supports search, topic, tags, pagination
# GET /minimal
# ---------------------------
@coding_q_bp.route("/minimal", methods=["GET"])
def list_minimal_questions():
    try:
        # params
        search = (request.args.get("search") or "").strip()
        topic = (request.args.get("topic") or "").strip()
        # accept repeated tags or comma separated
        tags = request.args.getlist("tags")
        if not tags:
            tags_raw = (request.args.get("tags") or "").strip()
            if tags_raw:
                tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        try:
            per_page = max(1, int(request.args.get("per_page", 6)))
        except ValueError:
            per_page = 6

        sort = (request.args.get("sort") or "new").lower()

        # build query
        q_filters = Q()
        if search:
            q_filters &= Q(title__icontains=search)

        if topic:
            q_filters &= Q(topic=topic)

        if tags:
            # AND semantics (question must have all tags). Change to tags__in for OR.
            q_filters &= Q(tags__all=tags)

        # Base queryset for filtered results
        filtered_qs = Question.objects(q_filters)

        # Retrieve available tags for the filtered result set
        try:
            available_tags = filtered_qs.distinct('tags') or []
        except Exception:
            # fallback if distinct fails for some reason
            available_tags = []
        # normalize and sort
        available_tags = sorted({t.lower() for t in available_tags if t})

        # Also return global list of all tags in the collection
        try:
            all_tags = Question.objects.distinct('tags') or []
        except Exception:
            all_tags = []
        all_tags = sorted({t.lower() for t in all_tags if t})

        # Apply sorting
        if sort == "old":
            filtered_qs = filtered_qs.order_by("created_at")
        elif sort == "title":
            filtered_qs = filtered_qs.order_by("title")
        else:
            filtered_qs = filtered_qs.order_by("-created_at")

        total = filtered_qs.count()
        total_pages = max(1, (total + per_page - 1) // per_page)

        # clamp page
        if page > total_pages:
            page = total_pages

        skip = (page - 1) * per_page
        items = filtered_qs.skip(skip).limit(per_page)

        # shape response to match frontend expectations
        data = []
        for item in items:
            data.append({
                "id": str(item.id),
                "title": item.title,

                "shortDescription": item.short_description or "",
                "topic": item.topic or "",
                "subtopic":item.subtopic or "",
                "tags": list(item.tags or []),
                "difficulty": (item.difficulty.capitalize() if item.difficulty else "Easy")
            })

        meta = {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "available_tags": available_tags,
            "all_tags": all_tags
        }

        return response(True, "Questions fetched", {"items": data, "meta": meta}), 200

    except Exception as e:
        return response(False, f"Error: {str(e)}"), 500



@coding_q_bp.route("/form/<question_id>", methods=["POST", "PUT"])
@token_required
def save_question_form(question_id):
    """
    Update only the form fields of an existing question.
    Does NOT create or modify any testcases/testcase groups.
    Accepted JSON fields:
      - title, topic, subtopic (subtopic not persisted unless you add to model)
      - tags (string comma-separated or list)
      - timeLimit (seconds) OR timeLimitMs (milliseconds)
      - memoryLimit (MB) OR memoryLimitKb (KB)
      - shortDescription, fullDescription
      - sampleIO: [{ input, output, explanation }, ...]
      - allowedLanguages: list or comma-separated string
    """
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return response(False, "Invalid JSON"), 400

    try:
        q = Question.objects.get(id=question_id)
    except DoesNotExist:
        return response(False, "Question not found"), 404
    except Exception as e:
        return response(False, f"Error fetching question: {str(e)}"), 500

    try:
        # Basic fields
        if "title" in data and data.get("title") is not None:
            q.title = str(data.get("title")).strip()

        if "topic" in data:
            q.topic = (data.get("topic") or "").strip()

        # subtopic: not in model by default — attach dynamically (no persistence unless model updated)
        if "subtopic" in data:
            q.subtopic = (data.get("subtopic") or "").strip()
 # Difficulty: accept only allowed choices
        if "difficulty" in data:
            raw = data.get("difficulty")
            try:
                s = str(raw).strip().lower()
                if s in ("easy", "medium", "hard"):
                    q.difficulty = s
            except Exception:
                pass
        # Tags: accept string CSV or list
        if "tags" in data:
            tags = data.get("tags")
            if isinstance(tags, str):
                q.tags = [t.strip() for t in tags.split(",") if t.strip()]
            elif isinstance(tags, (list, tuple)):
                q.tags = [str(t).strip() for t in tags if str(t).strip()]
            else:
                q.tags = []

        # Time limit conversion: seconds -> ms
        if "timeLimit" in data and data.get("timeLimit") is not None:
            try:
                q.time_limit_ms = int(data.get("timeLimit")) * 1000
            except Exception:
                pass
        elif "timeLimitMs" in data and data.get("timeLimitMs") is not None:
            try:
                q.time_limit_ms = int(data.get("timeLimitMs"))
            except Exception:
                pass

        # Memory limit conversion: MB -> KB
        if "memoryLimit" in data and data.get("memoryLimit") is not None:
            try:
                q.memory_limit_kb = int(data.get("memoryLimit")) * 1024
            except Exception:
                pass
        elif "memoryLimitKb" in data and data.get("memoryLimitKb") is not None:
            try:
                q.memory_limit_kb = int(data.get("memoryLimitKb"))
            except Exception:
                pass

        # Descriptions
        if "shortDescription" in data:
            q.short_description = (data.get("shortDescription") or "").strip()
        if "fullDescription" in data:
            q.long_description_markdown = data.get("fullDescription") or ""

        # Sample IO: replace if provided
        if "sampleIO" in data:
            new_sample_io = []
            payload_list = data.get("sampleIO") or []
            if isinstance(payload_list, (list, tuple)):
                for s in payload_list:
                    if not isinstance(s, dict):
                        continue
                    inp = s.get("input", "") or ""
                    out = s.get("output", "") or ""
                    expl = s.get("explanation", "") or ""
                    new_sample_io.append(SampleIO(
                        input_text=str(inp),
                        output=str(out),
                        explanation=str(expl)
                    ))
            q.sample_io = new_sample_io
        else:
            q.sample_io = []

        # Allowed languages: validate against known set; update list
        if "allowedLanguages" in data or "allowed_languages" in data:
            raw = data.get("allowedLanguages", data.get("allowed_languages"))
            parsed = []
            if isinstance(raw, str):
                parsed = [p.strip() for p in raw.split(",") if p.strip()]
            elif isinstance(raw, (list, tuple)):
                parsed = [str(p).strip() for p in raw if str(p).strip()]
            else:
                parsed = []
            allowed_set = set(["python", "cpp", "java", "javascript", "c"])
            q.allowed_languages = [p for p in parsed if p in allowed_set]

        # ---- New: run_code_enabled boolean ----   
        if "runCodeEnabled" in data or "run_code_enabled" in data:
            raw = data.get("runCodeEnabled", data.get("run_code_enabled"))
            if isinstance(raw, bool):
                q.run_code_enabled = raw
            else:
                if raw is not None:
                    s = str(raw).strip().lower()
                    if s in ("true", "1", "yes"):
                        q.run_code_enabled = True
                    elif s in ("false", "0", "no"):
                        q.run_code_enabled = False
        # --NEw: submission_enabled boolean ----
        if "submissionEnabled" in data or "submission_enabled" in data:
            raw = data.get("submissionEnabled", data.get("submission_enabled"))
            if isinstance(raw, bool):
                q.submission_enabled = raw
            else:
                if raw is not None:
                    s = str(raw).strip().lower()
                    if s in ("true", "1", "yes"):
                        q.submission_enabled = True
                    elif s in ("false", "0", "no"):
                        q.submission_enabled = False

         # ---- New: show_solution / show_boilerplates booleans ----
        if "showSolution" in data or "show_solution" in data:
            raw = data.get("showSolution", data.get("show_solution"))
            if isinstance(raw, bool):
                q.show_solution = raw
            else:
                if raw is not None:
                    s = str(raw).strip().lower()
                    if s in ("true", "1", "yes"):
                        q.show_solution = True
                    elif s in ("false", "0", "no"):
                        q.show_solution = False

        if "showBoilerplates" in data or "show_boilerplates" in data:
            raw = data.get("showBoilerplates", data.get("show_boilerplates"))
            if isinstance(raw, bool):
                q.show_boilerplates = raw
            else:
                if raw is not None:
                    s = str(raw).strip().lower()
                    if s in ("true", "1", "yes"):
                        q.show_boilerplates = True
                    elif s in ("false", "0", "no"):
                        q.show_boilerplates = False

        # ---- New: published flag ----
        if "published" in data or "isPublished" in data:
            raw = data.get("published", data.get("isPublished"))
            if isinstance(raw, bool):
                q.published = raw
            else:
                if raw is not None:
                    s = str(raw).strip().lower()
                    if s in ("true", "1", "yes"):
                        q.published = True
                    elif s in ("false", "0", "no"):
                        q.published = False


        # authors: append admin payload (store exact admin dict if not duplicate)
        admin_payload = getattr(request, "admin", {}) or {}
        if admin_payload:
            existing_authors = q.authors or []
            if admin_payload not in existing_authors:
                existing_authors.append(admin_payload)
                q.authors = existing_authors

        # version bump and save
        try:
            q.version = (int(q.version) if getattr(q, "version", None) else 1) + 1
        except Exception:
            q.version = 1

        q.updated_at = datetime.utcnow()
        q.save()

        return response(True, "Form fields updated", {"id": str(q.id)}), 200

    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"Error saving form fields: {str(e)}"), 500


# ---------------------------
# Get form values only (no testcase info)
# ---------------------------
@coding_q_bp.route("/form/<question_id>", methods=["GET"])
def get_question_for_form(question_id):
    try:
        q = Question.objects.get(id=question_id)
    except DoesNotExist:
        return response(False, "Question not found"), 404
    except Exception as e:
        return response(False, f"Error fetching question: {str(e)}"), 500

    try:
        # convert stored ms/kb -> frontend seconds/MB
        time_limit_seconds = None
        if getattr(q, "time_limit_ms", None) is not None:
            try:
                time_limit_seconds = int(q.time_limit_ms) // 1000
            except Exception:
                time_limit_seconds = None

        memory_limit_mb = None
        if getattr(q, "memory_limit_kb", None) is not None:
            try:
                memory_limit_mb = int(q.memory_limit_kb) // 1024
            except Exception:
                memory_limit_mb = None

        # sampleIO mapping
        sample_io = []
        for s in (q.sample_io or []):
            try:
                sample_io.append({
                    "input": getattr(s, "input_text", "") or "",
                    "output": getattr(s, "output", "") or "",
                    "explanation": getattr(s, "explanation", "") or ""
                })
            except Exception:
                continue

        payload = {
            "id": str(q.id),
            "title": q.title or "",
            "topic": q.topic or "",
            "subtopic": q.subtopic or "",
            "tags": list(q.tags or []),
            "timeLimit": time_limit_seconds,
            "memoryLimit": memory_limit_mb,
                        "showBoilerplates": bool(getattr(q, "show_boilerplates", True)),
                        "showSolution": bool(getattr(q, "show_solution", False)),
                        "published": bool(getattr(q, "published", False)),
                        "runCodeEnabled": bool(getattr(q, "run_code_enabled", True)),
                        "submissionEnabled": bool(getattr(q, "submission_enabled", True)),
                                    "difficulty": (getattr(q, "difficulty", None) or "medium"),


            "shortDescription": q.short_description or "",
            "fullDescription": q.long_description_markdown or "",
            "sampleIO": sample_io,
            "allowedLanguages": list(q.allowed_languages or []),
            "authors": list(q.authors or [])
        }

        return response(True, "Question form data fetched", payload), 200

    except Exception as e:
        return response(False, f"Error building response: {str(e)}"), 500


# ---------------------------
# TestCaseGroup endpoints
# - GET  /<question_id>/testcase-groups         -> list groups for a question
# - POST /<question_id>/testcase-groups         -> create a new group (token_required)
# - PUT  /testcase-group/<group_id>             -> update an existing group (token_required)
# Notes:
#  - These endpoints only read/write TestCaseGroup and TestCase documents.
#  - They DO NOT modify Question fields (except optional denormalized question_id on group).
# ---------------------------

@coding_q_bp.route("/<question_id>/testcase-groups", methods=["GET"])
def list_testcase_groups(question_id):
    try:
        # ensure question exists (optional; if you prefer skip, remove this block)
        try:
            Question.objects.get(id=question_id)
        except DoesNotExist:
            return response(False, "Question not found"), 404

        groups = TestCaseGroup.objects(question_id=str(question_id)).order_by("name")
        out = []
        for g in groups:
            # expand minimal test case info for frontend
            cases = []
            for tc in (g.cases or []):
                try:
                    cases.append({
                        "id": str(tc.id),
                        "input": getattr(tc, "input_text", ""),
                        "expected_output": getattr(tc, "expected_output", ""),
                        "time_limit_ms": getattr(tc, "time_limit_ms", None),
                        "memory_limit_kb": getattr(tc, "memory_limit_kb", None)
                    })
                except Exception:
                    continue

            out.append({
                "id": str(g.id),
                "question_id": g.question_id,
                "name": g.name,
                "weight": g.weight,
                "visibility": g.visibility,
                "scoring_strategy": g.scoring_strategy,
                "cases": cases,
                "created_at": g.created_at.isoformat() if getattr(g, "created_at", None) else None,
                "updated_at": g.updated_at.isoformat() if getattr(g, "updated_at", None) else None
            })

        return response(True, "Test case groups fetched", {"items": out}), 200

    except Exception as e:
        return response(False, f"Error fetching testcase groups: {str(e)}"), 500


@coding_q_bp.route("/<question_id>/testcase-groups", methods=["POST", "PUT"])
@token_required
def upsert_testcase_group(question_id):
    """
    Create or update a TestCaseGroup.
    - If body has "groupId" → update that group.
    - If no "groupId" → create a new group for the given question.
    Accepted JSON:
      - groupId (optional, required for update)
      - name (required on create, optional on update)
      - weight (int)
      - visibility ("public"|"hidden")
      - scoring_strategy ("binary"|"partial")
      - cases: list of testcase ids or inline objects { input, expected_output, time_limit_ms?, memory_limit_kb? }
    """
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return response(False, "Invalid JSON"), 400

    # Common helper
    def _create_or_resolve_testcases(case_payloads):
        resolved = []
        for c in (case_payloads or []):
            try:
                if isinstance(c, str) and c.strip():
                    tc = TestCase.objects.get(id=c.strip())
                    resolved.append(tc)
                elif isinstance(c, dict):
                    inp = (c.get("input") or c.get("input_text") or "").strip()
                    outp = (c.get("expected_output") or c.get("output") or "").strip()
                    if not inp or not outp:
                        continue
                    tc = TestCase(
                        input_text=inp,
                        expected_output=outp,
                        time_limit_ms=int(c.get("time_limit_ms")) if c.get("time_limit_ms") else None,
                        memory_limit_kb=int(c.get("memory_limit_kb")) if c.get("memory_limit_kb") else None,
                    )
                    tc.save()
                    resolved.append(tc)
            except DoesNotExist:
                continue
            except Exception:
                continue
        return resolved

    try:
        # ensure question exists
        try:
            Question.objects.get(id=question_id)
        except DoesNotExist:
            return response(False, "Question not found"), 404

        group_id = data.get("groupId")

        if group_id:  # ---------- UPDATE ----------
            try:
                group = TestCaseGroup.objects.get(id=group_id)
            except DoesNotExist:
                return response(False, "Test case group not found"), 404

            if "name" in data:
                group.name = (data.get("name") or group.name).strip()
            if "weight" in data:
                try:
                    group.weight = int(data.get("weight"))
                except Exception:
                    pass
            if "visibility" in data and data["visibility"] in ("public", "hidden"):
                group.visibility = data["visibility"]
            if "scoring_strategy" in data and data["scoring_strategy"] in ("binary", "partial"):
                group.scoring_strategy = data["scoring_strategy"]

            if "cases" in data:
                resolved_cases = _create_or_resolve_testcases(data.get("cases", []))
                group.cases = resolved_cases

            group.updated_at = datetime.utcnow()
            group.save()
            try:
                q = Question.objects.get(id=group.question_id)
                if group not in q.testcase_groups:
                    q.testcase_groups.append(group)
                    q.save()
            except DoesNotExist:
                pass

            return response(True, "Test case group updated", {"id": str(group.id)}), 200

        else:  # ---------- CREATE ----------
            name = (data.get("name") or "").strip()
            if not name:
                return response(False, "name is required for new testcase group"), 400

            resolved_cases = _create_or_resolve_testcases(data.get("cases", []))

            group = TestCaseGroup(
                question_id=str(question_id),
                name=name,
                weight=int(data.get("weight") or 0),
                visibility=data.get("visibility", "hidden"),
                scoring_strategy=data.get("scoring_strategy", "binary"),
                cases=resolved_cases,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
          # after group.save() in CREATE branch
            group.save()

            # attach to question.document to keep the denormalized reference list in sync
            try:
                q = Question.objects.get(id=question_id)
                # avoid duplicates
                if group not in q.testcase_groups:
                    q.testcase_groups.append(group)
                    q.save()
            except DoesNotExist:
                # question already validated earlier, but handle defensively
                pass

            return response(True, "Test case group created", {"id": str(group.id)}), 201



    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"Error saving testcase group: {str(e)}"), 500

@coding_q_bp.route("/testcase-group/<group_id>", methods=["DELETE"])
@token_required
def delete_testcase_group(group_id):
    """
    Delete a TestCaseGroup by ID, along with all TestCase documents inside it.
    """
    try:
        try:
            group = TestCaseGroup.objects.get(id=group_id)
        except DoesNotExist:
            return response(False, "Test case group not found"), 404

        # delete all referenced TestCases
        for tc in (group.cases or []):
            try:
                tc.delete()
            except Exception:
                continue

        # finally delete the group itself
        group.delete()

        return response(True, "Test case group and its testcases deleted"), 200

    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"Error deleting testcase group: {str(e)}"), 500


# ---------------------------
# Predefined boilerplates endpoints
# GET  /form/<question_id>/boilerplates    -> read boilerplates for a question
# POST /form/<question_id>/boilerplates    -> create/update boilerplates (token_required)
# PUT  /form/<question_id>/boilerplates    -> alias to POST
# ---------------------------

@coding_q_bp.route("/form/<question_id>/boilerplates", methods=["GET"])
def get_predefined_boilerplates(question_id):
    """
    Return the predefined boilerplates for the given question.
    Response payload:
      { "id": "<question_id>", "predefined_boilerplates": { "python": "...", "cpp": "..." } }
    """
    try:
        try:
            q = Question.objects.get(id=question_id)
        except DoesNotExist:
            return response(False, "Question not found"), 404

        boilerplates = q.predefined_boilerplates or {}
        # Ensure it's always a dict
        if not isinstance(boilerplates, dict):
            boilerplates = {}

        return response(True, "Boilerplates fetched", {
            "id": str(q.id),
            "predefined_boilerplates": boilerplates
        }), 200

    except Exception as e:
        return response(False, f"Error fetching boilerplates: {str(e)}"), 500


@coding_q_bp.route("/form/<question_id>/boilerplates", methods=["POST", "PUT"])
@token_required
def upsert_predefined_boilerplates(question_id):
    """
    Create or update predefined boilerplates for a question.

    Accepts JSON in either of these forms:
      1) Full object:
         { "predefined_boilerplates": { "python": "def solve(): ...", "cpp": "..." } }
      2) Single language update:
         { "language": "python", "code": "def solve(): ..." }

    Behavior:
      - Merges updates into existing boilerplates (does not delete unspecified languages).
      - If an empty object is provided for predefined_boilerplates, it will clear them.
      - Bumps question.version and appends request.admin to authors if new.
    """
    try:
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            return response(False, "Invalid JSON"), 400

        try:
            q = Question.objects.get(id=question_id)
        except DoesNotExist:
            return response(False, "Question not found"), 404

        # current boilerplates (ensure dict)
        current = q.predefined_boilerplates or {}
        if not isinstance(current, dict):
            current = {}

        updated = dict(current)  # copy for merge

        # Option A: full object replacement/merge
        if "predefined_boilerplates" in data:
            pb = data.get("predefined_boilerplates") or {}
            if isinstance(pb, dict):
                # merge keys: overwrite only the provided languages
                for lang, code in pb.items():
                    if code is None or (isinstance(code, str) and code.strip() == ""):
                        # if explicitly empty string/null, remove that lang
                        if lang in updated:
                            updated.pop(lang, None)
                    else:
                        updated[str(lang)] = str(code)
            else:
                return response(False, "predefined_boilerplates must be an object/dict"), 400

        # Option B: single language update
        elif "language" in data and "code" in data:
            lang = (data.get("language") or "").strip()
            code = data.get("code") or ""
            if not lang:
                return response(False, "language is required"), 400
            if code is None or (isinstance(code, str) and code.strip() == ""):
                # remove language if empty
                updated.pop(lang, None)
            else:
                updated[lang] = str(code)

        else:
            return response(False, "No boilerplate payload provided"), 400

        # (Optional) validate languages against allowed set if needed
        # allowed_set = set(["python", "cpp", "java", "javascript", "c"])
        # updated = {k: v for k, v in updated.items() if k in allowed_set}

        q.predefined_boilerplates = updated

        # authors: append admin payload (store exact admin dict if not duplicate)
        admin_payload = getattr(request, "admin", {}) or {}
        if admin_payload:
            existing_authors = q.authors or []
            if admin_payload not in existing_authors:
                existing_authors.append(admin_payload)
                q.authors = existing_authors

        # version bump
        try:
            q.version = (int(q.version) if getattr(q, "version", None) else 1) + 1
        except Exception:
            q.version = 1

        q.updated_at = datetime.utcnow()
        q.save()

        return response(True, "Predefined boilerplates updated", {
            "id": str(q.id),
            "predefined_boilerplates": q.predefined_boilerplates
        }), 200

    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"Error saving boilerplates: {str(e)}"), 500


# ---------------------------
# Solution code endpoints
# GET  /form/<question_id>/solution    -> read solution_code for a question
# POST /form/<question_id>/solution    -> create/update solution_code (token_required)
# PUT  /form/<question_id>/solution    -> alias to POST
# ---------------------------

@coding_q_bp.route("/form/<question_id>/solution", methods=["GET"])
def get_solution_code(question_id):
    """
    Return the stored solution_code for the given question.
    Response payload:
      { "id": "<question_id>", "solution_code": { "python": "...", "cpp": "..." } }
    """
    try:
        try:
            q = Question.objects.get(id=question_id)
        except DoesNotExist:
            return response(False, "Question not found"), 404

        solution = q.solution_code or {}
        # Ensure it's always a dict
        if not isinstance(solution, dict):
            solution = {}

        return response(True, "Solution code fetched", {
            "id": str(q.id),
            "solution_code": solution
        }), 200

    except Exception as e:
        return response(False, f"Error fetching solution code: {str(e)}"), 500


@coding_q_bp.route("/form/<question_id>/solution", methods=["POST", "PUT"])
@token_required
def upsert_solution_code(question_id):
    """
    Create or update solution_code for a question.

    Accepts JSON in either of these forms:
      1) Full object:
         { "solution_code": { "python": "def solve(): ...", "cpp": "..." } }
      2) Single language update:
         { "language": "python", "code": "def solve(): ..." }

    Behavior:
      - Merges updates into existing solution_code (does not delete unspecified languages).
      - If an empty object is provided for solution_code, it will clear them.
      - If a specific language is provided with an empty/null code it will remove that language.
      - Bumps question.version and appends request.admin to authors if new.
    """
    try:
        try:
            data = request.get_json(force=True) or {}
            print(data)
        except Exception:
            print('sds')
            return response(False, "Invalid JSON"), 400

        try:
            q = Question.objects.get(id=question_id)
        except DoesNotExist:
            return response(False, "Question not found"), 404

        # current solution code (ensure dict)
        current = q.solution_code or {}
        if not isinstance(current, dict):
            current = {}

        updated = dict(current)  # copy for merge

        # Option A: full object replacement/merge
        if "solution_code" in data:
            sc = data.get("solution_code") or {}
            if isinstance(sc, dict):
                # merge keys: overwrite only the provided languages
                for lang, code in sc.items():
                    if code is None or (isinstance(code, str) and code.strip() == ""):
                        # if explicitly empty string/null, remove that lang
                        updated.pop(lang, None)
                    else:
                        updated[str(lang)] = str(code)
            else:
                return response(False, "solution_code must be an object/dict"), 400

        # Option B: single language update
        elif "language" in data and "code" in data:
            lang = (data.get("language") or "").strip()
            code = data.get("code") or ""
            if not lang:
                return response(False, "language is required"), 400
            if code is None or (isinstance(code, str) and code.strip() == ""):
                # remove language if empty
                updated.pop(lang, None)
            else:
                updated[lang] = str(code)

        else:
            return response(False, "No solution payload provided"), 400

        # (Optional) validate languages against allowed set if needed
        # allowed_set = set(["python", "cpp", "java", "javascript", "c"])
        # updated = {k: v for k, v in updated.items() if k in allowed_set}

        q.solution_code = updated

        # authors: append admin payload (store exact admin dict if not duplicate)
        admin_payload = getattr(request, "admin", {}) or {}
        if admin_payload:
            existing_authors = q.authors or []
            if admin_payload not in existing_authors:
                existing_authors.append(admin_payload)
                q.authors = existing_authors

        # version bump
        try:
            q.version = (int(q.version) if getattr(q, "version", None) else 1) + 1
        except Exception:
            q.version = 1

        q.updated_at = datetime.utcnow()
        q.save()

        return response(True, "Solution code updated", {
            "id": str(q.id),
            "solution_code": q.solution_code
        }), 200

    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"Error saving solution code: {str(e)}"), 500

# ---------------------------
# Delete coding question (and all references)
# DELETE /<question_id>
# ---------------------------
@coding_q_bp.route("/<question_id>", methods=["DELETE"])
@token_required
def delete_coding_question(question_id):
    """
    Delete a coding question by ID along with all related references:
      - TestCaseGroups
      - TestCases inside those groups
      - Predefined boilerplates
      - Solution code
    """
    try:
        try:
            q = Question.objects.get(id=question_id)
        except DoesNotExist:
            return response(False, "Question not found"), 404

        # Delete all TestCaseGroups and their TestCases
        groups = TestCaseGroup.objects(question_id=str(question_id))
        for g in groups:
            for tc in (g.cases or []):
                try:
                    tc.delete()
                except Exception:
                    continue
            g.delete()

        # Optionally: clean up other embedded/dict fields in Question (boilerplates, solution_code, etc.)
        q.predefined_boilerplates = {}
        q.solution_code = {}
        q.sample_io = []

        # Finally delete the question itself
        q.delete()

        return response(True, "Question and all its references deleted"), 200

    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except Exception as e:
        return response(False, f"Error deleting question: {str(e)}"), 500
