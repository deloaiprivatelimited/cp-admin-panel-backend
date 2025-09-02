# models/questions/mcq.py
from mongoengine import (
    Document, EmbeddedDocument, StringField, ListField,
    BooleanField, IntField, EmbeddedDocumentField, ReferenceField,
    FloatField
)
from mongoengine.queryset import QuerySet
import datetime


class Option(EmbeddedDocument):
    id = StringField(required=True)   # unique id for option (e.g., "A", "B", "C", or UUID)
    value = StringField(required=True)

from mongoengine import Document, StringField, SetField

class MCQConfig(Document):
    topics = SetField(StringField(), default=set)
    subtopics = SetField(StringField(), default=set)
    tags = SetField(StringField(), default=set)
    difficulties = SetField(StringField(), default=set)

    meta = {"collection": "mcq_config"}

    def update_from_mcq(self, mcq):
        updated = False

        if mcq.topic:
            if mcq.topic not in self.topics:
                self.topics.add(mcq.topic)
                updated = True

        if mcq.subtopic:
            if mcq.subtopic not in self.subtopics:
                self.subtopics.add(mcq.subtopic)
                updated = True

        for tag in mcq.tags:
            if tag not in self.tags:
                self.tags.add(tag)
                updated = True

        if mcq.difficulty_level:
            if mcq.difficulty_level not in self.difficulties:
                self.difficulties.add(mcq.difficulty_level)
                updated = True

        if updated:
            self.save()

class QuestionMCQ(Document):
    title = StringField(required=True)
    question_text = StringField(required=True)
    options = ListField(EmbeddedDocumentField(Option), required=True)
    correct_options = ListField(StringField(), required=True)  # stores option ids
    is_multiple = BooleanField(default=False)
    marks = FloatField(default=1.0)
    negative_marks = FloatField(default=0.0)
    difficulty_level = StringField(choices=["easy", "medium", "hard"], default="medium")
    explanation = StringField()
    tags = ListField(StringField(), default=list)
    time_limit = IntField(default=60)  # seconds
    topic = StringField()
    subtopic = StringField()
    created_at = StringField(default=lambda: datetime.datetime.utcnow().isoformat())

    meta = {"collection": "questions_mcq"}

    def to_json(self):
        return {
            "id": str(self.id),
            "title": self.title,
            "question_text": self.question_text,
            "options": [{"id": o.id, "value": o.value} for o in self.options],
            "correct_options": self.correct_options,
            "is_multiple": self.is_multiple,
            "marks": self.marks,
            "negative_marks": self.negative_marks,
            "difficulty_level": self.difficulty_level,
            "explanation": self.explanation,
            "tags": self.tags,
            "time_limit": self.time_limit,
            "topic": self.topic,
            "subtopic": self.subtopic,
            "created_at": self.created_at,
        }

    def save(self, *args, **kwargs):
        """Override save to auto-update MCQConfig"""
        result = super().save(*args, **kwargs)
        config = MCQConfig.objects.first()
        if not config:
            config = MCQConfig()
        config.update_from_mcq(self)
        return result
