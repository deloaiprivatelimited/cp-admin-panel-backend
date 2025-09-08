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

class Question(Document):
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

    meta = {
        "collection": "questions",
        "indexes": [
            ("published", "topic"),
            {"fields": ["tags"], "sparse": True},
            {"fields": ["allowed_languages"], "sparse": True},  # optional: query by language
        ]
    }

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)
