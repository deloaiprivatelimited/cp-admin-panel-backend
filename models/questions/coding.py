# models.py (suggested updates)
from datetime import datetime
from mongoengine import (
    Document, EmbeddedDocument, EmbeddedDocumentField, StringField, IntField,
    ListField, DictField, BooleanField, DateTimeField, ReferenceField, FloatField,
    CASCADE, NULLIFY
)


class AttemptPolicy(EmbeddedDocument):
    max_attempts_per_minute = IntField(default=6, min_value=0)
    submission_cooldown_sec = IntField(default=2, min_value=0)


class SampleIO(EmbeddedDocument):
    input_text = StringField(required=True)   # renamed for clarity
    output = StringField(required=True)
    explanation = StringField()


class TestCase(Document):
    input_text = StringField(required=True)
    expected_output = StringField(required=True)
    time_limit_ms = IntField(min_value=0)   # optional override
    memory_limit_kb = IntField(min_value=0) # optional override

    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)

    meta = {
        "collection": "testcases",
        # index the default 'id' is present; remove nonexistent 'testcase_id' index
        "indexes": [
            # Add any real fields you query frequently, e.g. created_at
            "created_at",
        ]
    }

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)


class TestCaseGroup(Document):
    question_id = StringField(required=True)            # denormalized link to parent Question (optional)
    name = StringField(required=True)                   # "basic", "edge", "performance"
    weight = IntField(default=0)                        # weight points
    visibility = StringField(choices=("public", "hidden"), default="hidden")
    scoring_strategy = StringField(choices=("binary", "partial"), default="binary")

    # List of references to TestCase documents
    cases = ListField(ReferenceField(TestCase, reverse_delete_rule=CASCADE))

    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)

    meta = {
        "collection": "testcase_groups",
        # removed "group_id" index (non-existent); add a compound index if you query by question_id + name
        "indexes": [
            ("question_id", "name"),  # keep if you actually query by question_id+name
            "created_at",
        ]
    }

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)

class BaseQuestion(Document):
    title = StringField(required=True)
    topic = StringField()
    subtopic = StringField()                 # <-- new persisted field

    tags = ListField(StringField())
    short_description = StringField()
    long_description_markdown = StringField()  # render safely on frontend
    difficulty = StringField(choices=("easy", "medium", "hard"), default="medium")
    points = IntField(default=100, min_value=0)

    time_limit_ms = IntField(default=2000, min_value=0)
    memory_limit_kb = IntField(default=65536, min_value=0)

    predefined_boilerplates = DictField()  # e.g. {"python": "def solve():\n  ...", "cpp": "..."}
    solution_code = DictField()        # e.g. {"python": "def solve():\n  ...", "cpp": "..."}
    show_solution = BooleanField(default=False)
    run_code_enabled = BooleanField(default=True)
    submission_enabled = BooleanField(default=True)

    # controls whether boilerplates are visible to public users
    show_boilerplates = BooleanField(default=True)
    # store ReferenceFields (rename to avoid confusing _ids suffix)
    testcase_groups = ListField(
        ReferenceField(TestCaseGroup, reverse_delete_rule=NULLIFY)
    )

    published = BooleanField(default=False)
    version = IntField(default=1, min_value=1)
    authors = ListField(DictField(), default=list)  # list of authors/editors

    attempt_policy = EmbeddedDocumentField(AttemptPolicy, default=AttemptPolicy())  # instance

    sample_io = ListField(EmbeddedDocumentField(SampleIO), default=list)

    # ✅ new field: allowed languages
    allowed_languages = ListField(
        StringField(choices=("python", "cpp", "java", "javascript", "c")),
        default=list
    )

    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)
    meta = {"abstract": True}


    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)

    def to_safe_json(self):
        """
        Returns a safe JSON-like dict representation of the question,
        excluding sensitive/internal fields such as solution_code and
        referenced testcases.
        """
        return {
            "id": str(self.id),
            "title": self.title,
            "topic": self.topic,
            "subtopic": self.subtopic,
            "tags": self.tags,
            "short_description": self.short_description,
            "long_description_markdown": self.long_description_markdown,
            "difficulty": self.difficulty,
            "points": self.points,
            "time_limit_ms": self.time_limit_ms,
            "memory_limit_kb": self.memory_limit_kb,
            "solution_code" : self.solution_code,
            "predefined_boilerplates": self.predefined_boilerplates,
            "show_boilerplates": self.show_boilerplates,
            "run_code_enabled": self.run_code_enabled,
            "submission_enabled": self.submission_enabled,
            "sample_io": [
                {
                    "input_text": s.input_text,
                    "output": s.output,
                    "explanation": s.explanation,
                }
                for s in self.sample_io
            ],
            "allowed_languages": self.allowed_languages,
            "published": self.published,
            "version": self.version,
            "authors": self.authors,
            "attempt_policy": {
                "max_attempts_per_minute": self.attempt_policy.max_attempts_per_minute,
                "submission_cooldown_sec": self.attempt_policy.submission_cooldown_sec,
            } if self.attempt_policy else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

class Question(BaseQuestion):
     meta = {
        "collection": "questions",
        "indexes": [
            ("published", "topic"),
            {"fields": ["tags"], "sparse": True},
            {"fields": ["allowed_languages"], "sparse": True},  # optional: query by language
        ]
    }

class CourseQuestion(BaseQuestion):
     meta = {
        "collection": "course_questions",
        "indexes": [
            ("published", "topic"),
            {"fields": ["tags"], "sparse": True},
            {"fields": ["allowed_languages"], "sparse": True},  # optional: query by language
        ]
    }


class TestQuestion(BaseQuestion):
     meta = {
        "collection": "test_questions",
        "indexes": [
            ("published", "topic"),
            {"fields": ["tags"], "sparse": True},
            {"fields": ["allowed_languages"], "sparse": True},  # optional: query by language
        ]
    }
     

class CollegeQuestion(BaseQuestion):
    college_id = StringField(required=True)  # ✅ mandatory field for college linkage

    meta = {
        "collection": "college_questions",
        "indexes": [
            ("published", "topic"),
            {"fields": ["tags"], "sparse": True},
            {"fields": ["allowed_languages"], "sparse": True},
            "college_id",  # ✅ useful index if you query by college_id often
        ]
    }
    def to_json(self):
        base_json = super().to_json()
        base_json["college_id"] = self.college_id
        return base_json

# Add imports near top of models.py
from mongoengine import EmbeddedDocumentField

# New embedded doc for per-case results
class SubmissionCaseResult(EmbeddedDocument):
    testcase_id = StringField(required=False)  # internal id reference (NOT exposed in API responses)
    judge_token = StringField()                # token returned by Judge0 for this run
    status = DictField()                       # minimal status object returned by Judge0 (id + description)
    stdout = StringField()                     # may be large, but useful for debugging; consider truncation
    stderr = StringField()
    compile_output = StringField()
    time = FloatField()                        # runtime seconds/milliseconds depending on Judge0 response
    memory = IntField()
    points_awarded = IntField(default=0)       # points for this testcase (after applying group weight/rule)
    created_at = DateTimeField(default=datetime.utcnow)

# Submission document
class Submission(Document):
    question_id = StringField(required=True)
    collection = StringField(choices=("questions", "course_questions"), default="questions")
    user_id = StringField(required=True)  # store only the user id from JWT
    language = StringField(required=True)
    source_code = StringField(required=True)
    total_score = IntField(default=0)
    max_score = IntField(default=0)
    verdict = StringField(default="Pending")  # "Accepted", "Wrong Answer", "Runtime Error", etc.
    case_results = ListField(EmbeddedDocumentField(SubmissionCaseResult), default=list)
    attempt_number = IntField(default=1)      # for tracking attempts (optional)
    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)

    meta = {
        "collection": "submissions",
        "indexes": [
            ("question_id", "created_at"),
            "user_id",
        ]
    }

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)
