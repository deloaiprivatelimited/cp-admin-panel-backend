# models/students_placement_submission.py

from datetime import datetime
from mongoengine import (
    Document,
    EmbeddedDocument,
    StringField,
    ListField,
    EmbeddedDocumentField,
    ReferenceField,
    DateTimeField,
    BooleanField,
    DynamicField,
    DictField,
    ValidationError,
    signals,
)
from models.students_placement_profile import StudentsPlacementProfile, Section, Field
from models.college import College


class FieldResponse(EmbeddedDocument):
    """
    Stores a single field's response.
    `value` is DynamicField so it can store string, list, dict, number etc.
    `verified` mirrors verification status per-field (if your app uses it).
    """
    field_id = StringField(required=True)
    value = DynamicField()   # Accept any JSON-serializable value (string, list, dict, number)
    verified = BooleanField(default=False)


class SectionResponse(EmbeddedDocument):
    section_id = StringField(required=True)
    fields = ListField(EmbeddedDocumentField(FieldResponse))


class StudentsPlacementSubmission(Document):
    """
    A student's submission for a placement profile form.
    - `form` references the StudentsPlacementProfile (the form snapshot)
    - `college` references the College owning the form (for fast queries & integrity)
    - `student_id` is an identifier for the student (user id, roll no. etc.)
    - `sections` stores the student's answers in the same structure as the form
    """
    meta = {
        "collection": "students_placement_submissions",
        "indexes": [
            "college",
            "student_id",
            "form",
            ("college", "form"),
        ],
    }

    college = ReferenceField(College, required=True)
    form = ReferenceField(StudentsPlacementProfile, required=True)
    student_id = StringField(required=True)
    sections = ListField(EmbeddedDocumentField(SectionResponse))
    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)

    def clean(self):
        # Ensure the form belongs to the same college
        # (safer to reload to ensure up-to-date reference equality)
        if self.form and self.college:
            self.form.reload()
            if getattr(self.form, "college", None) and self.form.college.id != self.college.id:
                raise ValidationError("Form does not belong to the same college as the submission.")

    @classmethod
    def create_from_form(cls, form: StudentsPlacementProfile, student_id: str, college=None):
        """
        Create a blank submission for `student_id` based on the given form snapshot.
        If `college` is not provided, form.college is used.
        """
        if not college:
            college = form.college

        sections = []
        for sec in form.sections or []:
            fields = [FieldResponse(field_id=f.id, value=None, verified=False) for f in sec.fields or []]
            sections.append(SectionResponse(section_id=sec.id, fields=fields))

        submission = cls(
            college=college,
            form=form,
            student_id=student_id,
            sections=sections,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        submission.save()
        return submission

    def _map_sections(self):
        """Return a dict mapping section_id -> SectionResponse"""
        return {s.section_id: s for s in (self.sections or [])}

    def get_field(self, section_id: str, field_id: str):
        """Return FieldResponse or None."""
        sec_map = self._map_sections()
        sec = sec_map.get(section_id)
        if not sec:
            return None
        for f in sec.fields or []:
            if f.field_id == field_id:
                return f
        return None

    def set_field(self, section_id: str, field_id: str, value, verified: bool = None):
        """
        Set a value for an existing field. Raises ValueError if field doesn't exist.
        (If you'd prefer auto-creating missing fields, change behavior.)
        """
        f = self.get_field(section_id, field_id)
        if not f:
            raise ValueError(f"Field '{field_id}' not found in section '{section_id}' for this submission.")
        f.value = value
        if verified is not None:
            f.verified = bool(verified)
        self.updated_at = datetime.utcnow()
        self.save()

    def to_dict(self):
        """Serialize submission to a plain dict (useful for API responses)."""
        return {
            "id": str(self.id),
            "college": str(self.college.id) if self.college else None,
            "form": str(self.form.id) if self.form else None,
            "student_id": self.student_id,
            "sections": [
                {
                    "section_id": s.section_id,
                    "fields": [
                        {"field_id": f.field_id, "value": f.value, "verified": f.verified}
                        for f in (s.fields or [])
                    ],
                }
                for s in (self.sections or [])
            ],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def sync_with_form(self, save=True):
        """
        Sync this submission to the current structure of the referenced form.
        - Remove sections/fields that no longer exist on the form.
        - Add new sections/fields that the form now contains (with value=None).
        - Preserve existing values for unchanged fields.
        After calling, the submission is saved (unless save=False).
        """
        # reload form to get latest snapshot
        form = StudentsPlacementProfile.objects.get(id=self.form.id)

        # Map current submission sections and fields
        sub_section_map = {s.section_id: s for s in (self.sections or [])}

        new_sections = []
        for form_sec in (form.sections or []):
            sub_sec = sub_section_map.get(form_sec.id)
            if sub_sec:
                # Map existing fields for quick lookup
                sub_field_map = {f.field_id: f for f in (sub_sec.fields or [])}
                updated_fields = []
                for form_field in (form_sec.fields or []):
                    existing_fr = sub_field_map.get(form_field.id)
                    if existing_fr:
                        # keep existing value & verified
                        updated_fields.append(existing_fr)
                    else:
                        # new field -> add empty value
                        updated_fields.append(FieldResponse(field_id=form_field.id, value=None, verified=False))
                # assign cleaned/ordered fields back
                sub_sec.fields = updated_fields
                new_sections.append(sub_sec)
            else:
                # New section -> add with empty fields
                new_fields = [FieldResponse(field_id=f.id, value=None, verified=False) for f in (form_sec.fields or [])]
                new_sections.append(SectionResponse(section_id=form_sec.id, fields=new_fields))

        # replace with the synced version
        self.sections = new_sections
        self.updated_at = datetime.utcnow()
        if save:
            self.save()
        return self

    @classmethod
    def sync_all_for_form(cls, form: StudentsPlacementProfile, batch_size: int = 200):
        """
        Sync all submissions for the given form snapshot.
        - Iterates over submissions and calls sync_with_form().
        - Use `batch_size` to limit memory usage.
        Note: This may be an expensive operation for large data sets — run asynchronously or in background job.
        """
        qs = cls.objects(form=form)
        # iterate in batches. Using .limit / .skip is not ideal for large collections,
        # but for clarity we iterate using Django-style pagination. For production you may use cursors.
        count = qs.count()
        offset = 0
        while offset < count:
            batch = qs.skip(offset).limit(batch_size)
            for sub in batch:
                try:
                    sub.sync_with_form()
                except Exception:
                    # Optionally log errors, but continue syncing others
                    continue
            offset += batch_size

# # Optional: auto-sync a submission when its form is updated.
# # WARNING: calling a heavy sync in a signal can slow form save. Use with care.
# def _post_save_profile(sender, document, **kwargs):
#     """
#     WARNING: This will attempt to sync all submissions whenever a profile is saved.
#     If your form updates are frequent or submissions are many, prefer to run StudentsPlacementSubmission.sync_all_for_form
#     in a background job (Celery / a queue). Keep this commented out if you do not want automatic syncing.
#     """
#     # Uncomment the line below to enable automatic sync when form is saved.
#     # StudentsPlacementSubmission.sync_all_for_form(document)

# # signals.post_save.connect(_post_save_profile, sender=StudentsPlacementProfile)


