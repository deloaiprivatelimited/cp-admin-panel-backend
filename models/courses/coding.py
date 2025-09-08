# models.py
from datetime import datetime
from mongoengine import (
    Document,
    EmbeddedDocument,
    EmbeddedDocumentField,
    StringField,
    IntField,
    ListField,
    DictField,
    BooleanField,
    DateTimeField,
    ReferenceField,
    FloatField,
    CASCADE,
    NULLIFY,
    signals,
    get_db,
)
from typing import List

# ---------------------------
# Embedded documents
# ---------------------------

class AttemptPolicy(EmbeddedDocument):
    max_attempts_per_minute = IntField(default=6, min_value=0)
    submission_cooldown_sec = IntField(default=2, min_value=0)


class SampleIO(EmbeddedDocument):
    input_text = StringField(required=True)   # renamed for clarity
    output = StringField(required=True)
    explanation = StringField()


# ---------------------------
# Base documents
# ---------------------------

class TestCase(Document):
    """
    Individual test case. Can be referenced from many TestCaseGroup documents.
    """
    input_text = StringField(required=True)
    expected_output = StringField(required=True)
    time_limit_ms = IntField(min_value=0)   # optional override
    memory_limit_kb = IntField(min_value=0) # optional override

    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)

    meta = {
        "collection": "testcases",
        "indexes": [
            "created_at",
        ]
    }

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)


class TestCaseGroup(Document):
    """
    A group of testcases (e.g., "basic", "edge", "performance").
    Note: cases references should not cascade-delete the group when a TestCase is deleted,
    because we will manage deletion manually from CourseQuestion.
    """
    question_id = StringField(required=True)            # denormalized link to parent CourseQuestion (optional)
    name = StringField(required=True)                   # "basic", "edge", "performance"
    weight = IntField(default=0)                        # weight points
    visibility = StringField(choices=("public", "hidden"), default="hidden")
    scoring_strategy = StringField(choices=("binary", "partial"), default="binary")

    # Reference to TestCase documents.
    # Use NULLIFY so that deleting a TestCase does not accidentally delete the group.
    cases = ListField(ReferenceField(TestCase, reverse_delete_rule=NULLIFY))

    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)

    meta = {
        "collection": "testcase_groups",
        "indexes": [
            ("question_id", "name"),
            "created_at",
        ]
    }

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)


class CourseQuestion(Document):
    """
    Main question document. The delete() override will delete
    associated TestCaseGroups and TestCases in a bulk, safer way.
    """
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

    show_boilerplates = BooleanField(default=True)

    # store ReferenceFields (rename to avoid confusing _ids suffix)
    # When a CourseQuestion deletes its groups, we expect the groups to be removed.
    testcase_groups = ListField(ReferenceField(TestCaseGroup, reverse_delete_rule=NULLIFY))

    published = BooleanField(default=False)
    version = IntField(default=1, min_value=1)
    authors = ListField(DictField(), default=list)  # list of authors/editors

    attempt_policy = EmbeddedDocumentField(AttemptPolicy, default=AttemptPolicy())

    sample_io = ListField(EmbeddedDocumentField(SampleIO), default=list)

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
            {"fields": ["allowed_languages"], "sparse": True},
        ]
    }

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)

    def delete(self, *args, use_transaction: bool = False, **kwargs):
        """
        Delete this CourseQuestion and its TestCaseGroups and TestCases.

        Args:
            use_transaction: If True, attempt a MongoDB multi-document transaction.
                             Requires MongoDB replica set and the driver configured for transactions.
        Behavior:
            - Collect group and testcase IDs first (stable snapshot).
            - Bulk delete testcases, then groups, then the question itself.
        """
        # 1) Snapshot group ids
        group_ids = [g.id for g in (self.testcase_groups or []) if getattr(g, "id", None)]
        if not group_ids:
            # No groups: just delete the question
            return super().delete(*args, **kwargs)

        # 2) Collect testcase ids from groups (iterate over DB to get canonical list)
        tc_ids = []
        # Query DB to get fresh group documents
        groups = TestCaseGroup.objects(id__in=group_ids)
        for g in groups:
            # g.cases may be list of DBRefs; collect ids carefully
            for tc in (g.cases or []):
                if getattr(tc, "id", None):
                    tc_ids.append(tc.id)
        # dedupe
        tc_ids = list(set(tc_ids))

        # 3) If using a transaction and DB supports it, perform transactional deletion
        if use_transaction:
            client = get_db().client
            # start a session and transaction
            with client.start_session() as session:
                with session.start_transaction():
                    if tc_ids:
                        TestCase.objects(id__in=tc_ids).delete()
                    TestCaseGroup.objects(id__in=group_ids).delete()
                    # finally delete the question
                    super(CourseQuestion, self).delete(*args, **kwargs)
            return

        # 4) Non-transactional bulk deletes (best-effort)
        if tc_ids:
            TestCase.objects(id__in=tc_ids).delete()
        TestCaseGroup.objects(id__in=group_ids).delete()
        return super().delete(*args, **kwargs)

    # Optional: if you want pre-delete hooks in the future
    # @classmethod
    # def pre_delete(cls, sender, document, **kwargs):
    #     # do something before deletion (audit log, notifications)
    #     pass

# Optionally connect signals if you plan to use them
# signals.pre_delete.connect(CourseQuestion.pre_delete, sender=CourseQuestion)
