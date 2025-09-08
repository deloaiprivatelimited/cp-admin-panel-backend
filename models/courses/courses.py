# models/course.py
from mongoengine import (
    Document, EmbeddedDocument,
    StringField, ReferenceField, ListField, BooleanField,
    EmbeddedDocumentField, ValidationError
)

from models.courses.mcq import CourseMCQ
from models.courses.rearrange import CourseRearrange
from models.courses.coding import CourseQuestion
# ---------------------------
# Payload models for Unit (Embedded)
# ---------------------------

class TextUnit(EmbeddedDocument):
    content = StringField(required=True)

    def to_json(self):
        return {
            "content": self.content
        }


# ---------------------------
# Unit (contains Embedded payloads)
# ---------------------------

class Unit(Document):
    name = StringField(required=True)
    unit_type = StringField(required=True, choices=["text", "mcq","rearrange","coding"])  # extend later (e.g., video)
    # Store embedded payloads directly (NOT references)
    text = EmbeddedDocumentField(TextUnit, default=None)
    mcq = ReferenceField(CourseMCQ, default=None)
    rearrange = ReferenceField(CourseRearrange, default=None)
    coding = ReferenceField(CourseQuestion, default=None)


    def to_json(self):
        base = {
            "id": str(self.id),
            "name": self.name,
            "unit_type": self.unit_type,
            "text": self.text.to_json() if self.text else None,
            "mcq": self.mcq.to_json() if self.mcq else None,
        }
        return base
# Add to Unit class
    def delete(self, *args, **kwargs):
        """
        Delete a Unit and any referenced payload documents (mcq, rearrange, coding).
        Assumes those payload docs are not shared elsewhere.
        """
        # delete referenced payload documents if present
        try:
            if self.mcq:
                # mcq is a ReferenceField -> call its delete
                self.mcq.delete()
        except Exception:
            # best-effort: ignore failures but you may want to log
            pass

        try:
            if self.rearrange:
                self.rearrange.delete()
        except Exception:
            pass

        try:
            if self.coding:
                # CourseQuestion has its own delete() that will remove testcase groups etc.
                self.coding.delete()
        except Exception:
            pass

        # finally delete this Unit document
        return super(Unit, self).delete(*args, **kwargs)




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


# Add to Lesson class
    def delete(self, *args, **kwargs):
        """
        Delete a Lesson and all Units referenced by it.
        """
        # iterate units and delete each one (per-document to trigger signals)
        for u in (self.units or []):
            try:
                u.delete()
            except Exception:
                # ignore errors per-unit; optionally log
                pass

        # now delete the lesson itself
        return super(Lesson, self).delete(*args, **kwargs)


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


# Add to Chapter class
    def delete(self, *args, **kwargs):
        """
        Delete a Chapter and all its Lessons (and recursively their Units / payloads).
        """
        # delete lessons referenced by this chapter
        for lesson in (self.lessons or []):
            try:
                lesson.delete()
            except Exception:
                pass

        # delete the chapter doc
        return super(Chapter, self).delete(*args, **kwargs)

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

# Add to Course class
    def delete(self, *args, **kwargs):
        """
        Delete a Course and all Chapters (which will cascade to Lessons -> Units -> payloads).
        """
        for ch in (self.chapters or []):
            try:
                ch.delete()
            except Exception:
                pass

        return super(Course, self).delete(*args, **kwargs)