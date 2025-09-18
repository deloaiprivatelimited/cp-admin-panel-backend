# models/section.py
from datetime import datetime
from mongoengine import (
    Document, EmbeddedDocument,
    StringField, BooleanField, DateTimeField,
    ReferenceField, ListField, EmbeddedDocumentField, IntField
)

from models.questions.mcq import TestMCQ as MCQ
from models.questions.coding import TestQuestion as Question
from models.questions.rearrange import TestRearrange as Rearrange
# (later you’ll import CodingQuestion, RearrangeQuestion when models exist)
# from models.test.questions.coding import CodingQuestion
# from models.test.questions.rearrange import RearrangeQuestion


class SectionQuestion(EmbeddedDocument):
    """Wrapper for any question type inside a Section"""
    question_type = StringField(
        required=True,
        choices=["mcq", "coding", "rearrange"]
    )

    # Keep three separate references — only one is expected to be non-null
    mcq_ref = ReferenceField(MCQ, null=True)
    coding_ref = ReferenceField(Question, null=True)      # placeholder
    rearrange_ref = ReferenceField(Rearrange, null=True)  # placeholder


class Section(Document):
    """Model for a Test Section"""
    name = StringField(required=True)
    description = StringField(default="")
    instructions = StringField(default="")
    time_restricted = BooleanField(default=False, required=True)

    questions = ListField(EmbeddedDocumentField(SectionQuestion), default=list)
    duration = IntField(default=0, min_value=0)
 # New fields
    is_shuffle_question = BooleanField(default=False, required=True)
    is_shuffle_options = BooleanField(default=False, required=True)

    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)

    meta = {"collection": "sections", "indexes": ["time_restricted", "name"]}

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)

    def to_json(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description or "",
            "instructions": self.instructions or "",
                        "duration": int(self.duration) if self.duration is not None else 0,
"no_of_questions": len(self.questions),
            "time_restricted": self.time_restricted,
            "is_shuffle_question": self.is_shuffle_question,
            "is_shuffle_options": self.is_shuffle_options,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
