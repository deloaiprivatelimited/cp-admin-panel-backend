from datetime import datetime
from mongoengine import (
    Document,
    StringField,
    DateTimeField,
    ListField,
    DictField,
    ReferenceField,
    PULL,
    IntField,
)
from models.test.section import Section


class Test(Document):
    """Model for Tests

    This model already maintains two separate lists of sections:
      - sections_time_restricted: sections that are time-restricted (e.g. each section has its own timer)
      - sections_open: sections that are "open" (no per-section timer)

    The changes below add convenience serializers that return full section data
    for both admin-facing JSON and student-facing JSON. Student JSON uses the
    Section.to_student_test_json method (so shuffling/option shuffling rules are
    respected and can be made deterministic using the section id as seed).
    """

    test_name = StringField(required=True)
    description = StringField()

    start_datetime = DateTimeField(required=True)
    end_datetime = DateTimeField(required=True)
    college = StringField(required=True)  # ✅ Add this

    instructions = StringField()  # rich text
    notes = StringField()  # ✅ Separate notes field

    duration_seconds = IntField(required=True, default=3 * 60 * 60)  # 3 hours = 10800 seconds

    tags = ListField(StringField())
    created_by = DictField(required=True, default=lambda: {"id": "system", "name": "System"})
    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)

    # Two separate lists for sections
    sections_time_restricted = ListField(ReferenceField("Section", reverse_delete_rule=PULL))
    sections_open = ListField(ReferenceField("Section", reverse_delete_rule=PULL))

    meta = {"collection": "tests", "indexes": ["start_datetime", "end_datetime", "test_name"]}

    def clean(self):
        """Validation before saving"""
        if self.start_datetime and self.end_datetime:
            if self.start_datetime >= self.end_datetime:
                raise ValueError("start_datetime must be earlier than end_datetime")

    def save(self, *args, **kwargs):
        """Auto-update timestamps and run validation"""
        self.clean()
        self.updated_at = datetime.utcnow()
        if not self.created_at:
            self.created_at = datetime.utcnow()
        return super(Test, self).save(*args, **kwargs)

    # ----------------------
    # Helper: serialize lists of section references
    # ----------------------
    def _serialize_sections(self, section_refs, student_view: bool = False, deterministic_shuffle: bool = True):
        """Return list of serialized sections.

        - If student_view is False, returns Section.to_json() for each section (admin/teacher view).
        - If student_view is True, returns Section.to_student_test_json(deterministic_shuffle) for each section.

        If a reference is missing or raises, we include a placeholder with an error key so client can handle it.
        """
        out = []
        for s in (section_refs or []):
            try:
                if s is None:
                    out.append({"id": None, "error": "reference_missing"})
                    continue
                # choose serializer
                if student_view:
                    # Section.to_student_test_json accepts deterministic_shuffle boolean
                    out.append(s.to_student_test_json(deterministic_shuffle=deterministic_shuffle))
                else:
                    out.append(s.to_json())
            except Exception:
                out.append({"id": str(getattr(s, "id", None)), "error": "serialize_error"})
        return out

    # ----------------------
    # JSON serializers
    # ----------------------
    def to_json(self, include_section_details: bool = True, deterministic_shuffle: bool = True):
        """Convert Test document to dict/JSON for admin/teacher use.

        By default includes detailed section JSON for both time-restricted and open sections.
        Set include_section_details=False to return only the section ids.
        """
        base = {
            "id": str(self.id),
            "test_name": self.test_name,
            "description": self.description,
            "start_datetime": self.start_datetime.isoformat() if self.start_datetime else None,
            "end_datetime": self.end_datetime.isoformat() if self.end_datetime else None,
            "instructions": self.instructions,
            "notes": self.notes,
            "tags": self.tags,
            "duration_seconds": int(self.duration_seconds) if self.duration_seconds is not None else None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_section_details:
            base["sections_time_restricted"] = self._serialize_sections(self.sections_time_restricted, student_view=False, deterministic_shuffle=deterministic_shuffle)
            base["sections_open"] = self._serialize_sections(self.sections_open, student_view=False, deterministic_shuffle=deterministic_shuffle)
        else:
            base["sections_time_restricted"] = [str(s.id) for s in (self.sections_time_restricted or [])]
            base["sections_open"] = [str(s.id) for s in (self.sections_open or [])]

        return base

    def to_minimal_json(self):
        return {
            "id": str(self.id),
            "test_name": self.test_name,
            "tags": self.tags,
            "description": self.description,
            "instructions": self.instructions,
            "notes": self.notes,
            "duration_seconds": int(self.duration_seconds) if self.duration_seconds is not None else None,
            "start_datetime": self.start_datetime.isoformat() if self.start_datetime else None,
            "end_datetime": self.end_datetime.isoformat() if self.end_datetime else None,
            "total_sections": len(self.sections_time_restricted or []) + len(self.sections_open or []),
            "no_of_students": 0,
        }

    def to_student_test_json(self, deterministic_shuffle: bool = True):
        """Student-facing JSON for the Test.

        - sections_time_restricted and sections_open will contain student-facing section JSON
          produced by Section.to_student_test_json. That ensures section-level shuffling and
          option shuffling behavior is respected.
        - deterministic_shuffle parameter is forwarded to each section so clients can request
          deterministic (reproducible) shuffles based on section id.
        """
        return {
            "id": str(self.id),
            "test_name": self.test_name,
            "description": self.description,
            "instructions": self.instructions,
            "duration_seconds": int(self.duration_seconds) if self.duration_seconds is not None else None,
            "start_datetime": self.start_datetime.isoformat() if self.start_datetime else None,
            "end_datetime": self.end_datetime.isoformat() if self.end_datetime else None,
            "total_sections": len(self.sections_time_restricted or []) + len(self.sections_open or []),
            "sections_time_restricted": self._serialize_sections(self.sections_time_restricted, student_view=True, deterministic_shuffle=deterministic_shuffle),
            "sections_open": self._serialize_sections(self.sections_open, student_view=True, deterministic_shuffle=deterministic_shuffle),
            "no_of_students": 0,
        }
