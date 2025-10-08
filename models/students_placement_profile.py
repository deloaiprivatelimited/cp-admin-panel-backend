# models/students_placement_profile.py
from datetime import datetime
from mongoengine import (
    Document,
    EmbeddedDocument,
    StringField,
    IntField,
    BooleanField,
    EmbeddedDocumentField,
    ListField,
    ReferenceField,
    DateTimeField,
    DictField,
    EmailField,
)

from models.college import College
# ---------- Embedded documents ----------

class FieldOption(EmbeddedDocument):
    """Option used by choice fields (multiple-choice / checkboxes / dropdown)."""
    label = StringField(required=True)


class Field(EmbeddedDocument):
    """
    Single question/field in a section.
    Keep `id` to match frontend-generated field ids for easy mapping.
    Type should match your frontend naming (e.g. 'short-text', 'number', 'email', 'multiple-choice', ...).
    """
    id = StringField(required=True)      # frontend id (string)
    type = StringField(required=True)
    label = StringField(required=True)
    description = StringField()
    placeholder = StringField()
    required = BooleanField(default=False)
    verification_required = BooleanField(default=False)
    options = ListField(EmbeddedDocumentField(FieldOption))  # for choice fields
    min_scale = IntField()
    max_scale = IntField()
    scale_min_label = StringField()
    scale_max_label = StringField()
    rows = IntField()   # for long-text / paragraph
    min_value = IntField()
    max_value = IntField()


class Section(EmbeddedDocument):
    """A section grouping multiple fields."""
    id = StringField(required=True)     # frontend section id
    title = StringField(required=True)
    description = StringField()
    fields = ListField(EmbeddedDocumentField(Field))




class StudentsPlacementProfile(Document):
    """
    Stores a generated form (snapshot) belonging to a College.
    - sections: embedded snapshot of the form structure at creation/update time
    - college: reference to the owning college
    - settings: free-form dict for form settings (e.g., allowMultipleSubmissions)
    """
    meta = {
        'collection': 'students_placement_profiles',
        'indexes': [
            'college',  # index to query forms by college quickly
        ],
    }

    title = StringField(required=True)
    description = StringField()
    college = ReferenceField(College, required=True)  # reference to the College owning this form
    sections = ListField(EmbeddedDocumentField(Section))
    settings = DictField()  # e.g., {"allowMultipleSubmissions": True, "confirmationMessage": "..."}
    form_open = BooleanField(default = False)
    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)

    @classmethod
    def get_or_create_for_college(cls, college: College):
        """
        Get or create a StudentsPlacementProfile for a given college.
        Returns (profile, created)
        """
        profile = cls.objects(college=college).first()
        if profile:
            return profile, False

        # Create a new profile
        profile = cls(
            title=f"{college.name} - Placement Profile",
            description="Default placement profile form for student data collection.",
            college=college,
            sections=[],
            settings={
                "allowMultipleSubmissions": True,
                "confirmationMessage": "Your profile has been submitted successfully.",
            },
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        profile.save()
        return profile, True