# routes/coding_questions_routes.py

from flask import Blueprint, request
from functools import wraps
from datetime import datetime
from mongoengine.errors import ValidationError, DoesNotExist
from mongoengine.queryset.visitor import Q

from models.questions.coding import (
    Question, TestCaseGroup,
    SampleIO, AttemptPolicy
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

        # store admin payload exactly as it is
        admin_payload = getattr(request, "admin", {}) or {}

        q = Question(
            topic=topic,
            title=title,
            short_description=short_description,
            authors=[admin_payload],  # just store raw admin dict
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        q.save()

        return response(True, "Coding Question created", {"id": str(q.id), "title": q.title}), 201

    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
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
            setattr(q, "subtopic", (data.get("subtopic") or "").strip())

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
            "subtopic": getattr(q, "subtopic", "") or "",
            "tags": list(q.tags or []),
            "timeLimit": time_limit_seconds,
            "memoryLimit": memory_limit_mb,
            "shortDescription": q.short_description or "",
            "fullDescription": q.long_description_markdown or "",
            "sampleIO": sample_io,
            "allowedLanguages": list(q.allowed_languages or []),
            "authors": list(q.authors or [])
        }

        return response(True, "Question form data fetched", payload), 200

    except Exception as e:
        return response(False, f"Error building response: {str(e)}"), 500