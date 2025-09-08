# models/mcq.py

import uuid
from mongoengine import (
    Document, EmbeddedDocument,
    StringField, ListField, EmbeddedDocumentField,
    IntField, BooleanField, FloatField, BooleanField, DictField
)


class Option(EmbeddedDocument):
    """Options for MCQ"""
    option_id = StringField(required=True, default=lambda: str(uuid.uuid4()))
    value = StringField(required=True)


class CourseMCQConfig(Document):
    """Stores Config data auto-created from MCQ"""
    difficulty_levels = ListField(StringField())
    topics = ListField(StringField())
    subtopics = ListField(StringField())
    tags = ListField(StringField())

    meta = {"collection": "mcq_configs"}


class CourseMCQ(Document):
    """Main MCQ Model"""
    title = StringField(required=True)
    question_text = StringField(required=True)

    options = ListField(EmbeddedDocumentField(Option), required=True)
    correct_options = ListField(StringField(), required=True)  # list of option_ids

    is_multiple = BooleanField(default=False)

    marks = FloatField(required=True, min_value=0)
    negative_marks = FloatField(required=True, min_value=0)

    difficulty_level = StringField(choices=["Easy", "Medium", "Hard"], required=True)

    explanation = StringField()
    tags = ListField(StringField())
    time_limit = IntField()  # in seconds

    topic = StringField(required=True)
    subtopic = StringField()
    created_by = DictField(required=True,default=lambda: {"id": "system", "name": "System"})


    meta = {"collection": "mcqs"}

    def clean(self):
        """Validation before saving"""
        if not self.is_multiple and len(self.correct_options) > 1:
            raise ValueError("Multiple correct options not allowed unless is_multiple=True")

    def save(self, *args, **kwargs):
        """Override save to auto-update/create CourseMCQConfig"""
        self.clean()  # run validation

        # Save MCQ first
        result = super(CourseMCQ, self).save(*args, **kwargs)

        # Update CourseMCQConfig
        config = CourseMCQConfig.objects.first()
        if not config:
            config = CourseMCQConfig()

        if self.difficulty_level not in config.difficulty_levels:
            config.difficulty_levels.append(self.difficulty_level)

        if self.topic and self.topic not in config.topics:
            config.topics.append(self.topic)

        if self.subtopic and self.subtopic not in config.subtopics:
            config.subtopics.append(self.subtopic)

        for tag in self.tags or []:
            if tag not in config.tags:
                config.tags.append(tag)

        config.save()

        return result
    def to_json(self):
        """Convert MCQ document to dict/JSON"""
        return {
            "id": str(self.id),
            "title": self.title,
            "question_text": self.question_text,
            "options": [{"option_id": o.option_id, "value": o.value} for o in self.options],
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
            "created_by": self.created_by,
        }