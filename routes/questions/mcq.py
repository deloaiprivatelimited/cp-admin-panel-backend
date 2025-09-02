# routes/mcq_routes.py
from flask import Blueprint, request
from mongoengine.errors import ValidationError, NotUniqueError
from models.questions.mcq import QuestionMCQ, Option
from utils.jwt import verify_access_token
from utils.response import response
from functools import wraps

mcq_bp = Blueprint("mcq_bp", __name__)


# Decorator to check token validity
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


# Add new MCQ
@mcq_bp.route("/", methods=["POST"])
@token_required
def add_mcq():
    try:
        data = request.get_json()
        if not data:
            return response(False, "Request body is required"), 400

        title = data.get("title")
        question_text = data.get("question_text")
        options_data = data.get("options", [])
        correct_options = data.get("correct_options", [])
        is_multiple = data.get("is_multiple", False)
        marks = data.get("marks", 1.0)
        negative_marks = data.get("negative_marks", 0.0)
        difficulty_level = data.get("difficulty_level", "medium")
        explanation = data.get("explanation", "")
        tags = data.get("tags", [])
        time_limit = data.get("time_limit", 60)
        topic = data.get("topic")
        subtopic = data.get("subtopic")

        # Validation
        if not title or not question_text or not options_data or not correct_options:
            return response(False, "Title, question_text, options, and correct_options are required"), 400

        # Build options
        options = []
        option_ids = set()
        for opt in options_data:
            if "id" not in opt or "value" not in opt:
                return response(False, "Each option must have 'id' and 'value'"), 400
            if opt["id"] in option_ids:
                return response(False, f"Duplicate option id '{opt['id']}'"), 400
            option_ids.add(opt["id"])
            options.append(Option(id=opt["id"], value=opt["value"]))

        # Ensure correct options exist in options list
        for co in correct_options:
            if co not in option_ids:
                return response(False, f"Correct option '{co}' not found in options"), 400

        # Create and save MCQ
        mcq = QuestionMCQ(
            title=title,
            question_text=question_text,
            options=options,
            correct_options=correct_options,
            is_multiple=is_multiple,
            marks=marks,
            negative_marks=negative_marks,
            difficulty_level=difficulty_level,
            explanation=explanation,
            tags=tags,
            time_limit=time_limit,
            topic=topic,
            subtopic=subtopic,
        )
        mcq.save()

        return response(True, "MCQ created successfully", mcq.to_json()), 201

    except ValidationError as ve:
        return response(False, f"Validation error: {ve}"), 400
    except NotUniqueError as ne:
        return response(False, f"Duplicate entry: {ne}"), 400
    except Exception as e:
        return response(False, f"An error occurred: {str(e)}"), 500
