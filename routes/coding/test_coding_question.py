"""
Flask blueprint to return coding questions with strict exposure rules:
- Only return a question if `published` is True
- Include `solution_code` only if `show_solution` is True
- Include `predefined_boilerplates` only if `show_boilerplates` is True
- Never include test cases or testcase IDs

Assumes your existing mongoengine models are importable from `models`.
"""
import os
import base64
import logging
import requests
from bson import ObjectId

from flask import Blueprint, jsonify, abort, request, current_app
from mongoengine.errors import DoesNotExist, ValidationError

# Import the Document classes from your models module
# from models.questions.coding import Question, CourseQuestion

# from flask import Blueprint, jsonify, abort
# from bson import ObjectId
from mongoengine.errors import DoesNotExist, ValidationError

# Import the Document classes from your models module
from models.questions.coding import Question, CourseQuestion, CollegeQuestion,TestQuestion

bp = Blueprint('test_coding_questions', __name__)


def _serialize_question(q):
    """Return a dict representation of a question following the exposure rules.
    Never include any test cases or testcase_groups content/ids.
    """
    data = {
        "id": str(q.id),
        "title": q.title,
        "topic": q.topic,
        "subtopic": getattr(q, 'subtopic', None),
        "tags": list(q.tags or []),
        "short_description": q.short_description,
        "long_description_markdown": q.long_description_markdown,
        "difficulty": q.difficulty,
        "points": q.points,
        "time_limit_ms": q.time_limit_ms,
        "memory_limit_kb": q.memory_limit_kb,
        "allowed_languages": list(q.allowed_languages or []),
        "created_at": q.created_at.isoformat() if getattr(q, 'created_at', None) else None,
        "updated_at": q.updated_at.isoformat() if getattr(q, 'updated_at', None) else None,
        "version": q.version,
        "authors": list(q.authors or []),
        "sample_io": [
            {
                "input_text": s.input_text,
                "output": s.output,
                "explanation": s.explanation,
            }
            for s in (q.sample_io or [])
        ],
        # DO NOT include testcase_groups or any testcases
    }

    # Include boilerplates only if allowed by the question
    if getattr(q, 'show_boilerplates', False):
        data['predefined_boilerplates'] = q.predefined_boilerplates or {}

    # Include solution code only if allowed
    if getattr(q, 'show_solution', False):
        data['solution_code'] = q.solution_code or {}

    # Include run/submit flags
    data['run_code_enabled'] = q.run_code_enabled
    data['submission_enabled'] = q.submission_enabled

    # If this is a CollegeQuestion, optionally include college_id (non-sensitive)
    if hasattr(q, "college_id"):
        data["college_id"] = getattr(q, "college_id", None)

    return data


def _model_for_collection(collection):
    """
    Map the collection string to the model class.
    Adds support for 'college_questions' -> CollegeQuestion.
    """
    if collection == 'questions':
        return Question
    elif collection == 'course_questions':
        return CourseQuestion
    elif collection == 'college_questions':
        return CollegeQuestion
    elif collection == 'test_questions':
        return TestQuestion
    else:
        return None


@bp.route('/<collection>/<question_id>', methods=['GET'])
def get_question(collection, question_id):
    """Fetch a question by id from the given collection.

    URL structure: /api/questions/<collection>/<question_id>
    where <collection> is either "questions" or "course_questions".

    Rules enforced:
    - If the question is not published -> 404
    - Remove solution_code unless show_solution True
    - Remove predefined_boilerplates unless show_boilerplates True
    - Never return testcase_groups or test cases
    """
    # Basic ObjectId validation (works if you use ObjectId for ids)
    try:
        ObjectId(question_id)
    except Exception:
        # Not a valid ObjectId - still try to load by string id in case your setup uses plain strings
        pass

    Model = _model_for_collection(collection)
    print(collection)
    if Model is None:
        abort(404, description='Invalid collection')

    try:
        q = Model.objects.get(id=question_id)
    except (DoesNotExist, ValidationError):
        abort(404, description='Question not found')

    # Only return published questions
    if not getattr(q, 'published', False):
        abort(404, description='Question not available')

    response = _serialize_question(q)
    return jsonify(response)


# Example: register blueprint in your Flask app
# from flask import Flask
# app = Flask(__name__)
# app.register_blueprint(bp)

# Notes:
# - This blueprint deliberately avoids dereferencing or returning TestCase/TestCaseGroup data.
# - If you want to permit previewing drafts to admins, add an auth layer and a bypass for admin users.
# - If your IDs are not ObjectId, adjust the validation above.

import os
import logging
import requests

_JUDGE0_HTTP_TIMEOUT = float(os.getenv("JUDGE0_HTTP_TIMEOUT", "15.0"))
JUDGE0_BASE = os.getenv("JUDGE0_BASE_URL", "https://ce.judge0.com")
JUDGE0_API_KEY = os.getenv("JUDGE0_API_KEY")
JUDGE0_API_KEY_HEADER = os.getenv("JUDGE0_API_KEY_HEADER", "X-Auth-Token")
JUDGE0_API_HOST = os.getenv("JUDGE0_API_HOST")  # for RapidAPI

logger = logging.getLogger(__name__)

def _judge0_headers():
    headers = {"Content-Type": "application/json"}
    if JUDGE0_API_KEY and JUDGE0_API_HOST:
        # RapidAPI usage: requires key + host header
        headers[JUDGE0_API_KEY_HEADER] = JUDGE0_API_KEY
        headers["X-RapidAPI-Host"] = JUDGE0_API_HOST
    elif JUDGE0_API_KEY:
        headers[JUDGE0_API_KEY_HEADER] = JUDGE0_API_KEY
    return headers

# Local fallback mapping (best-effort only — may become stale across Judge0 versions)
_FALLBACK_LANG_MAP = {
    "python": 71,
    "python3": 71,
    "cpp": 54,
    "c++": 54,
    "java": 62,
    "javascript": 63,
    "js": 63,
    "c": 50,
    "go": 60,
    "ruby": 72,
    # add others you frequently use...
}

def _resolve_language_id(language_name):
    """
    Robust resolver:
      - Accepts numeric language ids (returns int)
      - Tries multiple Judge0 language endpoints (works for ce.judge0.com and RapidAPI proxy)
      - Falls back to _FALLBACK_LANG_MAP if remote fetch fails
    Returns: int language_id or None
    """
    if not language_name:
        return None

    want_raw = str(language_name).strip()
    # Accept numeric language id passed in (string or int)
    try:
        return int(want_raw)
    except Exception:
        pass

    want = want_raw.lower()

    # Candidate endpoints to try (order matters)
    endpoints = [
        f"{JUDGE0_BASE}/api/v1/languages",  # standard Judge0 CE
        f"{JUDGE0_BASE}/languages",         # RapidAPI or alternate proxies
        f"{JUDGE0_BASE}/api/languages",     # some proxies
    ]

    languages = None
    for url in endpoints:
        try:
            logger.debug("Trying Judge0 languages URL: %s", url)
            resp = requests.get(url, headers=_judge0_headers(), timeout=_JUDGE0_HTTP_TIMEOUT)
            # treat 2xx as success, 404/405/4xx skip to next
            if resp.status_code >= 200 and resp.status_code < 300:
                languages = resp.json() or []
                logger.info("Fetched languages from %s (count=%d)", url, len(languages))
                break
            else:
                logger.debug("Non-200 from %s: %s", url, resp.status_code)
        except Exception as exc:
            logger.debug("Error fetching %s: %s", url, exc)
            # try next candidate

    if languages:
        candidates = []
        for lang in languages:
            lang_id = lang.get("id") or lang.get("language_id") or lang.get("languageId")
            name = (lang.get("name") or "").strip().lower()
            language_field = (lang.get("language") or "").strip().lower() if lang.get("language") else ""
            aliases = [a.strip().lower() for a in (lang.get("aliases") or []) if a]
            version = (lang.get("version") or "").strip().lower()
            candidates.append({
                "id": lang_id,
                "name": name,
                "language": language_field,
                "aliases": aliases,
                "version": version,
                "raw": lang
            })

        # exact matches
        for c in candidates:
            if want == c["name"] or want == c["language"] or want in c["aliases"] or want == c["version"]:
                try:
                    return int(c["id"])
                except Exception:
                    continue

        # substring / tolerant matches
        for c in candidates:
            fields = " ".join(filter(None, [c["name"], c["language"], " ".join(c["aliases"]), c["version"]]))
            if want in fields:
                try:
                    return int(c["id"])
                except Exception:
                    continue

        # prefix match
        for c in candidates:
            if (c["name"] and c["name"].startswith(want)) or (c["language"] and c["language"].startswith(want)):
                try:
                    return int(c["id"])
                except Exception:
                    continue

        logger.info("Fetched languages but no match for '%s'. Sample names: %s", want, [c["name"] for c in candidates[:8]])

    # fallback local map
    fb = _FALLBACK_LANG_MAP.get(want)
    if fb:
        logger.info("Using fallback language id for '%s' -> %s", want, fb)
        return int(fb)

    logger.warning("Could not resolve language '%s' to a Judge0 language_id", want)
    return None


from flask import Blueprint, jsonify, abort, request, current_app
from flask import request
from utils.jwt import verify_access_token
@bp.route('/<collection>/<question_id>/run', methods=['POST'])
def run_submission(collection, question_id):
    """
    POST body JSON:
      {
        "source_code": "<code string>",
        "language": "<language friendly name, e.g. python, cpp, java>",
        "stdin": "<custom input for program>"   # optional
        "wait": true/false                     # optional, default true: get result synchronously
      }

    Rules applied:
    - Only published + run_code_enabled questions can be executed
    - Only languages listed in question.allowed_languages are permitted
    - We NEVER fetch or send question testcases to Judge0 here
    - We do not return testcases or testcase IDs in the response
    """
   
    body = request.get_json(force=True, silent=True) or {}
    source_code = body.get("source_code")
    language = (body.get("language") or "").strip().lower()
    stdin = body.get("stdin", "")
    wait = bool(body.get("wait", True))

    # Basic validation
    if not source_code or not language:
        return jsonify({"error": "source_code and language are required"}), 400

    # pick model
    Model = _model_for_collection(collection)
    if Model is None:
        abort(404, description='Invalid collection')

    try:
        q = Model.objects.get(id=question_id)
    except (DoesNotExist, ValidationError):
        abort(404, description='Question not found')

    # enforce published + run enabled
    if not getattr(q, "published", False) or not getattr(q, "run_code_enabled", False):
        abort(404, description="Question not available for running code")

    # enforce allowed languages if configured on question
    allowed = [l.lower() for l in (q.allowed_languages or [])]
    if allowed and language not in allowed:
        return jsonify({"error": "language not allowed for this question", "allowed_languages": allowed}), 400

    # resolve language id via Judge0 /languages endpoint
    print(language)
    language_id = _resolve_language_id(language)
    if language_id is None:
        # fall back: let the client pass numeric language_id directly
        # if they provided a numeric string
        try:
            language_id = int(language)
        except Exception:
            return jsonify({"error": "couldn't resolve language to Judge0 language_id; try a numeric language_id or use a different language string"}), 400

    # Prepare payload for Judge0.
    # We'll send Base64 encoded strings and set base64_encoded=true per Judge0 docs.
    payload = {
        "language_id": language_id,
        # base64-encoded source and stdin; Judge0 will decode when base64_encoded=true
        "source_code": base64.b64encode(source_code.encode("utf-8")).decode("ascii"),
        "stdin": base64.b64encode((stdin or "").encode("utf-8")).decode("ascii"),
        # do NOT send expected_output or any testcase data
    }

    params = {
        "base64_encoded": "true",
        # if wait is true we get a synchronous response (may be slower) — per Judge0 docs `wait=true`.
        "wait": "true" if wait else "false"
    }

    try:
        print( f"{JUDGE0_BASE}/api/v1/submissions")
        resp = requests.post(
    f"{JUDGE0_BASE}/submissions",   # <-- RapidAPI path, no /api/v1
    params=params,
    json=payload,
    headers=_judge0_headers(),
    timeout=_JUDGE0_HTTP_TIMEOUT
)

    except requests.RequestException as e:
        current_app.logger.exception("Judge0 request failed")
        return jsonify({"error": "cannot reach code execution service", "detail": str(e)}), 502

    # propagate non-200 from Judge0
    if resp.status_code >= 400:
        print(resp.text)
        return jsonify({"error": "execution service returned error", "status_code": resp.status_code, "detail": resp.text}), 502

    j = resp.json() or {}

    # Decode base64 response fields if base64 was used (Judge0 will respond with base64-encoded fields
    # when base64_encoded=true). The docs say stdout/stderr/compile_output may be base64-encoded.
    def _maybe_b64_decode(val):
        if val is None:
            return None
        # judge0 returns strings — try to decode if it looks base64y
        try:
            decoded = base64.b64decode(val).decode("utf-8")
            return decoded
        except Exception:
            return val

    safe_response = {
        "token": j.get("token"),
        "status": j.get("status"),              # status object/dict with id & description in many Judge0 versions
        "stdout": _maybe_b64_decode(j.get("stdout")),
        "stderr": _maybe_b64_decode(j.get("stderr")),
        "compile_output": _maybe_b64_decode(j.get("compile_output")),
        "message": _maybe_b64_decode(j.get("message")),
        "time": j.get("time"),
        "memory": j.get("memory"),
    }

    # Always avoid returning any Judge0 fields that could reveal internal testcases or judge internals.
    # The object above is intentionally minimal.

    return jsonify({
        "question_id": str(q.id),
        "language_id": language_id,
        "result": safe_response
    })


from datetime import datetime, timedelta

import os
import base64
import math
import time as time_module
import requests
from flask import request, current_app, jsonify, abort
from models.questions.coding import Submission , SubmissionCaseResult,TestCaseGroup



@bp.route('/<collection>/<question_id>/submit', methods=['POST'])
def submit_question(collection, question_id):
    """
    Submit code to be judged against hidden testcases.

    Request JSON:
      { "source_code": "...", "language": "python" }

    Response (safe): {
      submission_id, question_id, verdict, total_score, max_score,
      groups: [{ name: "Group 1", group_max_points, group_points_awarded, cases: [{ name: "Testcase 1", passed, points_awarded, time, memory, judge_token? }] } ]
    }
    """
    # --- Auth: read JWT and extract user id ---
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Authorization required"}), 401
    token = auth_header.split(" ", 1)[1]
    try:
        payload = verify_access_token(token)
    except ValueError as e:
        return jsonify({"error": str(e)}), 401

    user_id = payload.get("sub") or payload.get("id") or payload.get("student_id")
    if not user_id:
        return jsonify({"error": "Invalid token payload"}), 401

    # --- Request body ---
    body = request.get_json(force=True, silent=True) or {}
    source_code = body.get("source_code")
    language = (body.get("language") or "").strip().lower()

    if not source_code or not language:
        return jsonify({"error": "source_code and language are required"}), 400

    # --- Model selection ---
    Model = _model_for_collection(collection)
    if Model is None:
        abort(404, description='Invalid collection')

    try:
        q = Model.objects.get(id=question_id)
    except (DoesNotExist, ValidationError):
        abort(404, description='Question not found')

    # enforce published/submission_enabled
    if not getattr(q, "published", False) or not getattr(q, "submission_enabled", False):
        abort(404, description="Question not available for submission")

    # enforce allowed languages
    allowed = [l.lower() for l in (q.allowed_languages or [])]
    if allowed and language not in allowed:
        return jsonify({"error": "language not allowed for this question", "allowed_languages": allowed}), 400

    # basic attempt policy enforcement (per-minute)
    policy = getattr(q, "attempt_policy", None)
    max_per_min = getattr(policy, "max_attempts_per_minute", None) if policy else None
    if max_per_min:
        since = datetime.utcnow() - timedelta(seconds=60)
        recent_count = Submission.objects(question_id=str(q.id), user_id=str(user_id), created_at__gte=since).count()
        if recent_count >= int(max_per_min):
            return jsonify({"error": "Too many attempts - try again later"}), 429

    # resolve judge0 language id
    language_id = _resolve_language_id(language)
    if language_id is None:
        return jsonify({"error": "couldn't resolve language id"}), 400

    # Load TestCaseGroups and their cases (dereference safely)
    testcase_groups_docs = []
    for tg_ref in (q.testcase_groups or []):
        try:
            # if already a document-like object
            if hasattr(tg_ref, 'id') and hasattr(tg_ref, 'cases'):
                tg = tg_ref
            else:
                tg = TestCaseGroup.objects.get(id=tg_ref)
        except Exception:
            current_app.logger.warning("Failed to load TestCaseGroup %s", tg_ref)
            continue
        # collect cases
        cases = []
        for c_ref in (tg.cases or []):
            try:
                case = c_ref if hasattr(c_ref, 'id') and hasattr(c_ref, 'input_text') else TestCase.objects.get(id=c_ref)
            except Exception:
                current_app.logger.warning("Failed to load TestCase %s", c_ref)
                continue
            cases.append(case)
        if cases:
            testcase_groups_docs.append({"tg": tg, "cases": cases})

    if not testcase_groups_docs:
        return jsonify({"error": "No testcases found for question"}), 500

    # --- Compute group scoring allocations normalized to q.points ---
    q_total_points = int(getattr(q, "points", 0) or 0)
    # sum of weights; if zero, fallback to equal weights
    sum_weights = sum(int(gw["tg"].weight or 0) for gw in testcase_groups_docs)
    if sum_weights == 0:
        for gw in testcase_groups_docs:
            gw["tg"].weight = 1
        sum_weights = len(testcase_groups_docs)

    # compute raw group points and convert to integer allocation (distribute remainder by fractional parts)
    raw_group_points = []
    for gw in testcase_groups_docs:
        w = int(gw["tg"].weight or 0)
        raw_group_points.append((w / sum_weights) * q_total_points)

    group_floor = [int(math.floor(x)) for x in raw_group_points]
    allocated = sum(group_floor)
    remainder = q_total_points - allocated
    frac_parts = [(i, raw_group_points[i] - group_floor[i]) for i in range(len(raw_group_points))]
    frac_parts.sort(key=lambda x: x[1], reverse=True)
    group_max_points = list(group_floor)
    i = 0
    while remainder > 0 and i < len(frac_parts):
        idx = frac_parts[i][0]
        group_max_points[idx] += 1
        remainder -= 1
        i += 1
    if remainder > 0:
        group_max_points[0] += remainder
        remainder = 0

    # attach allocations and per-case integer splits
    for idx, gw in enumerate(testcase_groups_docs):
        gw["group_max_points"] = group_max_points[idx]
        gw["group_points_awarded"] = 0
        num_cases = len(gw["cases"])
        base = gw["group_max_points"] // num_cases
        extra = gw["group_max_points"] - (base * num_cases)
        gw["case_points_allocation"] = [base + (1 if ci < extra else 0) for ci in range(num_cases)]

    # Create submission record now (store minimal fields)
    submission = Submission(
        question_id=str(q.id),
        collection=collection,
        user_id=str(user_id),
        language=str(language),
        source_code=source_code,
        case_results=[]
    )
    submission.save()

    total_awarded = 0

    # --- Run each testcase synchronously against Judge0 ---
    for gw in testcase_groups_docs:
        tg = gw["tg"]
        cases = gw["cases"]
        for ci, case in enumerate(cases):
            # compute limits
            cpu_limit = case.time_limit_ms / 1000.0 if getattr(case, "time_limit_ms", None) else (q.time_limit_ms / 1000.0 if getattr(q, "time_limit_ms", None) else None)
            memory_limit = case.memory_limit_kb or q.memory_limit_kb

            payload = {
                "language_id": int(language_id),
                "source_code": base64.b64encode(source_code.encode("utf-8")).decode("ascii"),
                "stdin": base64.b64encode((case.input_text or "").encode("utf-8")).decode("ascii"),
                "expected_output": base64.b64encode((case.expected_output or "").encode("utf-8")).decode("ascii")
            }
            if cpu_limit:
                payload["cpu_time_limit"] = cpu_limit
            if memory_limit:
                payload["memory_limit"] = memory_limit

            params = {"base64_encoded": "true", "wait": "true"}

            try:
                resp = requests.post(
                    f"{JUDGE0_BASE}/submissions",
                    params=params,
                    json=payload,
                    headers=_judge0_headers(),
                    timeout=_JUDGE0_HTTP_TIMEOUT
                )
                resp.raise_for_status()
                j = resp.json() or {}
            except Exception as e:
                current_app.logger.exception("Judge0 submission failed")
                status_obj = {"id": -1, "description": "Judge error"}
                stdout = stderr = compile_output = ""
                time_used = None
                memory_used = None
                judge_token = None
            else:
                status_obj = j.get("status") or {}
                def _decode_maybe(val):
                    if not val:
                        return ""
                    try:
                        return base64.b64decode(val).decode("utf-8")
                    except Exception:
                        return str(val)
                stdout = _decode_maybe(j.get("stdout"))
                stderr = _decode_maybe(j.get("stderr"))
                compile_output = _decode_maybe(j.get("compile_output"))
                judge_token = j.get("token")
                time_used = None
                memory_used = None
                if j.get("time") is not None:
                    try:
                        time_used = float(j.get("time"))
                    except Exception:
                        time_used = None
                if j.get("memory") is not None:
                    try:
                        memory_used = int(j.get("memory"))
                    except Exception:
                        memory_used = None

            # determine pass/fail
            passed = False
            try:
                if isinstance(status_obj, dict):
                    st_id = status_obj.get("id")
                    passed = (st_id == 3) or (str(status_obj.get("description","")).lower().startswith("accepted"))
                else:
                    passed = str(status_obj).lower().find("accepted") != -1
            except Exception:
                passed = False

            # allocate points for this case
            per_case_alloc = int(gw["case_points_allocation"][ci])
            awarded = per_case_alloc if passed else 0

            # save case result (we store testcase_id internally, but we won't return it)
            cr = SubmissionCaseResult(
                testcase_id=str(case.id),
                judge_token=judge_token,
                status=status_obj,
                stdout=stdout,
                stderr=stderr,
                compile_output=compile_output,
                time=time_used,
                memory=memory_used,
                points_awarded=int(awarded)
            )
            submission.case_results.append(cr)

            # accumulate
            gw["group_points_awarded"] += int(awarded)
            total_awarded += int(awarded)

            # small pause to avoid hammering judge
            time_module.sleep(0.03)

            submission.save()

    # finalize submission
    submission.total_score = int(total_awarded)
    submission.max_score = int(q_total_points)
    if submission.total_score >= submission.max_score and submission.max_score > 0:
        submission.verdict = "Accepted"
    elif submission.total_score > 0:
        submission.verdict = "Partial"
    else:
        submission.verdict = "Wrong Answer"
    submission.save()

    # Build safe response -> auto-named groups and testcases (no names/ids of original)
    resp_groups = []
    idx_pointer = 0
    for gidx, gw in enumerate(testcase_groups_docs):
        num_cases = len(gw["cases"])
        slice_crs = submission.case_results[idx_pointer: idx_pointer + num_cases]
        idx_pointer += num_cases

        case_summaries = []
        for ci, cr in enumerate(slice_crs):
            passed = False
            try:
                if isinstance(cr.status, dict):
                    st_id = cr.status.get("id")
                    passed = (st_id == 3) or (str(cr.status.get("description","")).lower().startswith("accepted"))
                else:
                    passed = str(cr.status).lower().find("accepted") != -1
            except Exception:
                passed = False

            case_summaries.append({
                "name": f"Testcase {ci + 1}",
                "passed": passed,
                "points_awarded": int(cr.points_awarded),
                "time": cr.time,
                "memory": cr.memory,
                "judge_token": cr.judge_token  # optional: remove if you don't want tokens in client response
            })

        resp_groups.append({
            "name": f"Test Case {gidx + 1}",
            "group_max_points": int(gw["group_max_points"]),
            "group_points_awarded": int(gw["group_points_awarded"]),
            "cases": case_summaries
        })

    response = {
        "submission_id": str(submission.id),
        "question_id": str(q.id),
        "verdict": submission.verdict,
        "total_score": submission.total_score,
        "max_score": submission.max_score,
        "groups": resp_groups,
        "created_at": submission.created_at.isoformat()
    }

    return jsonify(response), 200



@bp.route('/<collection>/<question_id>/mock-submit', methods=['POST', 'GET'])
def mock_submit(collection, question_id):
    """
    Dev-only: return a static submission payload for testing UI / client.
    URL: /<collection>/<question_id>/mock-submit
    Accepts POST or GET. Ignores body and auth.
    """
    sample = {
        "submission_id": "68c57d2da747cccd981a5051",
        "question_id": "68c559a2592c0d9977b08b8b",
        "verdict": "Accepted",
        "total_score": 100,
        "max_score": 100,
        "groups": [
            {
                "cases": [
                    {
                        "judge_token": "81ec8efc-2c93-4472-85cc-fc18e5ad21e6",
                        "memory": 3300,
                        "name": "Testcase 1",
                        "passed": True,
                        "points_awarded": 5,
                        "time": 0.008
                    },
                    {
                        "judge_token": "6f3c1b50-9f77-4da5-8736-ff5c6e7c71e7",
                        "memory": 3600,
                        "name": "Testcase 2",
                        "passed": True,
                        "points_awarded": 4,
                        "time": 0.008
                    },
                    {
                        "judge_token": "e5b195f3-b7f2-4c5b-9f0a-d7c85d75c9ea",
                        "memory": 3520,
                        "name": "Testcase 3",
                        "passed": True,
                        "points_awarded": 4,
                        "time": 0.008
                    },
                    {
                        "judge_token": "0eeeaba2-b606-4251-aeef-1ebd9b1bedec",
                        "memory": 3304,
                        "name": "Testcase 4",
                        "passed": True,
                        "points_awarded": 4,
                        "time": 0.008
                    }
                ],
                "group_max_points": 17,
                "group_points_awarded": 17,
                "name": "Test Case 1"
            },
            {
                "cases": [
                    {
                        "judge_token": "142d1110-bd1f-4376-8413-c8a96c8285f7",
                        "memory": 3300,
                        "name": "Testcase 1",
                        "passed": True,
                        "points_awarded": 11,
                        "time": 0.008
                    },
                    {
                        "judge_token": "7afb558d-d3f2-4348-9ee3-c1bf24d5110e",
                        "memory": 3412,
                        "name": "Testcase 2",
                        "passed": True,
                        "points_awarded": 11,
                        "time": 0.008
                    },
                    {
                        "judge_token": "73ffb7d0-b07d-4e35-a97b-33625cc4246e",
                        "memory": 3396,
                        "name": "Testcase 3",
                        "passed": True,
                        "points_awarded": 11,
                        "time": 0.008
                    }
                ],
                "group_max_points": 33,
                "group_points_awarded": 33,
                "name": "Test Case 2"
            },
            {
                "cases": [
                    {
                        "judge_token": "257596c5-c651-44e7-b325-0f5512a43813",
                        "memory": 3216,
                        "name": "Testcase 1",
                        "passed": True,
                        "points_awarded": 25,
                        "time": 0.008
                    },
                    {
                        "judge_token": "7dee0870-633c-4226-9cfa-9898b46243b3",
                        "memory": 3444,
                        "name": "Testcase 2",
                        "passed": True,
                        "points_awarded": 25,
                        "time": 0.008
                    }
                ],
                "group_max_points": 50,
                "group_points_awarded": 50,
                "name": "Test Case 3"
            }
        ],
        "created_at": "2025-09-13T14:18:21.405310"
    }

    # optionally log the incoming request for debugging
    current_app.logger.debug("Mock submit called for %s/%s; method=%s", collection, question_id, request.method)

    return jsonify(sample), 200

@bp.route('/<collection>/<question_id>/mock-run', methods=['POST', 'GET'])
def mock_run(collection, question_id):
    """
    Dev-only: return a static run payload for testing UI / client.
    URL: /<collection>/<question_id>/mock-run
    Accepts POST or GET. Ignores body and auth.
    """
    sample = {
        "question_id": question_id,
        "language_id": 71,   # Python 3
        "result": {
            "token": "mock-token-12345",
            "status": {"id": 3, "description": "Accepted"},
            "stdout": "Hello World\n",
            "stderr": "",
            "compile_output": None,
            "message": None,
            "time": "0.005",
            "memory": 3456
        }
    }

    current_app.logger.debug("Mock run called for %s/%s; method=%s", collection, question_id, request.method)

    return jsonify(sample), 200


# GET /<collection>/<question_id>/my-submissions
@bp.route('/<collection>/<question_id>/my-submissions', methods=['GET'])
def my_submissions(collection, question_id):
    """
    Return only the authenticated user's submissions for a given question.
    - Auth: Authorization: Bearer <token>
    - Query params:
        page (default=1), per_page (default=20, max=200)
        include_case_details (true/false, default=true)
    - Never returns judge_token or testcase IDs.
    """
    # pagination
    try:
        page = max(1, int(request.args.get('page', 1)))
    except Exception:
        page = 1
    try:
        per_page = int(request.args.get('per_page', 20))
    except Exception:
        per_page = 20
    per_page = min(max(1, per_page), 200)
    include_case_details = request.args.get('include_case_details', 'true').lower() not in ('0', 'false', 'no')

    # auth
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Authorization required"}), 401
    token = auth_header.split(" ", 1)[1]
    try:
        payload = verify_access_token(token)
    except ValueError as e:
        return jsonify({"error": str(e)}), 401

    user_id = payload.get("sub") or payload.get("id") or payload.get("student_id")
    if not user_id:
        return jsonify({"error": "Invalid token payload"}), 401

    # pick question model (mirror your existing logic)
    Model = _model_for_collection(collection)
    if Model is None:
        abort(404, description='Invalid collection')

    # ensure question exists (optional: require published)
    try:
        q = Model.objects.get(id=question_id)
    except (DoesNotExist, ValidationError):
        abort(404, description='Question not found')

    # If you want to restrict to published questions uncomment below:
    # if not getattr(q, "published", False):
    #     abort(404, description="Question not available")

    # Query only this user's submissions for the given question & collection
    query = {
        "question_id": str(q.id),
        "collection": collection,
        "user_id": str(user_id)
    }

    total = Submission.objects(__raw__=query).count()
    skip = (page - 1) * per_page
    submissions = Submission.objects(__raw__=query).order_by("-created_at").skip(skip).limit(per_page)

    def _case_summary_from_cr(cr, idx):
        # safe per-case summary -> no judge_token or testcase ids
        passed = False
        try:
            if isinstance(cr.status, dict):
                st_id = cr.status.get("id")
                passed = (st_id == 3) or (str(cr.status.get("description", "")).lower().startswith("accepted"))
            else:
                passed = str(cr.status).lower().find("accepted") != -1
        except Exception:
            passed = False

        return {
            "name": f"Testcase {idx + 1}",
            "passed": bool(passed),
            "points_awarded": int(getattr(cr, "points_awarded", 0) or 0),
            "time": getattr(cr, "time", None),
            "memory": getattr(cr, "memory", None),
        }

    items = []
    for sub in submissions:
        case_results = getattr(sub, "case_results", []) or []
        cases = []
        if include_case_details:
            for idx, cr in enumerate(case_results):
                cases.append(_case_summary_from_cr(cr, idx))
        print(sub)

        item = {
            "submission_id": str(sub.id),
            "question_id": str(sub.question_id),
            "language" : sub.language,
            "source_code" : sub.source_code,
            "verdict": getattr(sub, "verdict", None),
            "total_score": int(getattr(sub, "total_score", 0) or 0),
            "max_score": int(getattr(sub, "max_score", 0) or 0),
            "created_at": sub.created_at.isoformat() if getattr(sub, "created_at", None) else None,
        }
        if include_case_details:
            item["cases"] = cases

        items.append(item)

    return jsonify({
        "page": page,
        "per_page": per_page,
        "total": total,
        "items": items
    }), 200


# GET /<collection>/<question_id>/my-submissions
@bp.route('/<collection>/<question_id>/my-test-submissions', methods=['GET'])
def my_submissions_test(collection, question_id):
    """
    Return only the authenticated user's submissions for a given question.
    - Auth: Authorization: Bearer <token>
    - Query params:
        submission_ids (comma-separated list of submission IDs to fetch; required)
        include_case_details (true/false, default=true)
    - Never returns judge_token or testcase IDs.
    - If submission_ids is missing or empty, returns an empty list.
    """
    # query params
    submission_ids_param = request.args.get("submission_ids")
    submission_ids = []
    if submission_ids_param:
        submission_ids = [sid.strip() for sid in submission_ids_param.split(",") if sid.strip()]

    include_case_details = request.args.get('include_case_details', 'true').lower() not in ('0', 'false', 'no')

    # auth
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Authorization required"}), 401
    token = auth_header.split(" ", 1)[1]
    try:
        payload = verify_access_token(token)
    except ValueError as e:
        return jsonify({"error": str(e)}), 401

    user_id = payload.get("sub") or payload.get("id") or payload.get("student_id")
    if not user_id:
        return jsonify({"error": "Invalid token payload"}), 401

    # pick question model
    Model = _model_for_collection(collection)
    if Model is None:
        abort(404, description='Invalid collection')

    # ensure question exists
    try:
        q = Model.objects.get(id=question_id)
    except (DoesNotExist, ValidationError):
        abort(404, description='Question not found')

    # if no submission_ids → always return empty
    if not submission_ids:
        return jsonify({
            "page": None,
            "per_page": None,
            "total": 0,
            "items": []
        }), 200

    # base query
    query = {
        "question_id": str(q.id),
        "collection": collection,
        "user_id": str(user_id),
    }

    # filter only requested submissions
    submissions_qs = Submission.objects(__raw__=query).filter(id__in=submission_ids)
    total = submissions_qs.count()
    submissions = submissions_qs.order_by("-created_at")

    def _case_summary_from_cr(cr, idx):
        passed = False
        try:
            if isinstance(cr.status, dict):
                st_id = cr.status.get("id")
                passed = (st_id == 3) or (str(cr.status.get("description", "")).lower().startswith("accepted"))
            else:
                passed = str(cr.status).lower().find("accepted") != -1
        except Exception:
            passed = False

        return {
            "name": f"Testcase {idx + 1}",
            "passed": bool(passed),
            "points_awarded": int(getattr(cr, "points_awarded", 0) or 0),
            "time": getattr(cr, "time", None),
            "memory": getattr(cr, "memory", None),
        }

    items = []
    for sub in submissions:
        case_results = getattr(sub, "case_results", []) or []
        cases = []
        if include_case_details:
            for idx, cr in enumerate(case_results):
                cases.append(_case_summary_from_cr(cr, idx))

        item = {
            "submission_id": str(sub.id),
            "question_id": str(sub.question_id),
            "language": sub.language,
            "source_code": sub.source_code,
            "verdict": getattr(sub, "verdict", None),
            "total_score": int(getattr(sub, "total_score", 0) or 0),
            "max_score": int(getattr(sub, "max_score", 0) or 0),
            "created_at": sub.created_at.isoformat() if getattr(sub, "created_at", None) else None,
        }
        if include_case_details:
            item["cases"] = cases

        items.append(item)

    return jsonify({
        "page": None,
        "per_page": None,
        "total": total,
        "items": items
    }), 200
