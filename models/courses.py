# models/course.py
from mongoengine import (
    Document, EmbeddedDocument,
    StringField, ReferenceField, ListField, BooleanField,
    EmbeddedDocumentField, ValidationError
)

# ---------------------------
# Payload models for Unit (Embedded)
# ---------------------------

class TextUnit(EmbeddedDocument):
    content = StringField(required=True)

    def to_json(self):
        return {
            "content": self.content
        }


class MCQOption(EmbeddedDocument):
    label = StringField(required=True)        # e.g., "A", "B", "C", "D"
    text = StringField(required=True)
    is_correct = BooleanField(default=False)

    def to_json(self):
        return {
            "label": self.label,
            "text": self.text,
            "is_correct": self.is_correct
        }


class MCQUnit(EmbeddedDocument):
    question = StringField(required=True)
    options = ListField(EmbeddedDocumentField(MCQOption), required=True)
    explanation = StringField()

    def to_json(self):
        return {
            "question": self.question,
            "options": [opt.to_json() for opt in self.options],
            "explanation": self.explanation
        }


# ---------------------------
# Unit (contains Embedded payloads)
# ---------------------------

class Unit(Document):
    name = StringField(required=True)
    unit_type = StringField(required=True, choices=["text", "mcq"])  # extend later (e.g., video)
    # Store embedded payloads directly (NOT references)
    text = EmbeddedDocumentField(TextUnit, default=None)
    mcq = EmbeddedDocumentField(MCQUnit, default=None)


    def to_json(self):
        base = {
            "id": str(self.id),
            "name": self.name,
            "unit_type": self.unit_type,
            "text": self.text.to_json() if self.text else None,
            "mcq": self.mcq.to_json() if self.mcq else None,
        }
        return base


# ---------------------------
# Lesson (optional grouping within a chapter)
# ---------------------------

class Lesson(Document):
    name = StringField(required=True)
    tagline = StringField()
    description = StringField()
    units = ListField(ReferenceField(Unit))

    def to_json(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "tagline": self.tagline,
            "description": self.description,
            "units": [u.to_json() for u in self.units],
        }


# ---------------------------
# Chapter (holds Units; can also reference Lessons)
# ---------------------------

class Chapter(Document):
    name = StringField(required=True)
    tagline = StringField()
    description = StringField()
    lessons = ListField(ReferenceField(Lesson))         # optional grouping

    def to_json(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "tagline": self.tagline,
            "description": self.description,
            "lessons": [l.to_json() for l in self.lessons],
        }


# ---------------------------
# Course (chapters list)
# ---------------------------

class Course(Document):
    name = StringField(required=True)
    tagline = StringField()
    description = StringField()
    chapters = ListField(ReferenceField(Chapter))
    thumbnail_url = StringField()  # 👈 New field for course thumbnail
    def to_json(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "tagline": self.tagline,
            "description": self.description,
            "thumbnail_url": self.thumbnail_url,  # 👈 Return thumbnail
            "chapters": [c.to_json() for c in self.chapters]
        }
