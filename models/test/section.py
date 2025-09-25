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
import hashlib
import random

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
# ------------------------
    # Student-facing Section JSON (with optional deterministic shuffling)
    # ------------------------
    def to_student_test_json(self, deterministic_shuffle: bool = True):
        """
        Minimal, student-facing representation of the section.
        - If self.is_shuffle_question is True, questions are returned in shuffled order.
        - If self.is_shuffle_options is True, MCQ options are returned in shuffled order.
        - deterministic_shuffle=True makes shuffles reproducible using section.id as seed.
        """
        # prepare deterministic RNG seeded from section id (if requested)
        if deterministic_shuffle and getattr(self, "id", None) is not None:
            # use sha256(self.id) as seed (stable across processes)
            seed_bytes = hashlib.sha256(str(self.id).encode("utf-8")).digest()
            seed = int.from_bytes(seed_bytes[:8], "big")
            rng = random.Random(seed)
        else:
            rng = random.Random()

        result = {
            "id": str(self.id),
            "name": self.name,
            "description": self.description or "",
            "instructions": self.instructions or "",
            "duration": int(self.duration) if self.duration is not None else 0,
            "no_of_questions": len(self.questions or []),
            "time_restricted": bool(self.time_restricted),
            "is_shuffle_question": bool(self.is_shuffle_question),
            "is_shuffle_options": bool(self.is_shuffle_options),
            "questions": [],
        }

        def safe_img_list(img_list):
            if not img_list:
                return []
            out = []
            for img in img_list:
                try:
                    out.append({
                        "image_id": getattr(img, "image_id", None),
                        "label": getattr(img, "label", None),
                        "url": getattr(img, "url", None),
                        "alt_text": getattr(img, "alt_text", None),
                        "metadata": getattr(img, "metadata", None),
                    })
                except Exception:
                    continue
            return out

        # Build question wrappers first (in original order)
        q_wrappers = []
        for sq in (self.questions or []):
            q_wrapper = {"question_type": sq.question_type}
            try:
                if sq.question_type == "mcq" and sq.mcq_ref:
                    mcq = sq.mcq_ref
                    # build options list
                    options = [
                        {
                            "option_id": opt.option_id,
                            "value": opt.value,
                            "images": safe_img_list(opt.images),
                        }
                        for opt in (mcq.options or [])
                    ]
                    # shuffle options if section flag set
                    if self.is_shuffle_options and options:
                        rng.shuffle(options)

                    q_wrapper["question"] = {
                        "id": str(mcq.id),
                        "title": mcq.title,
                        "question_text": mcq.question_text,
                        "question_images": safe_img_list(mcq.question_images),
                        "options": options,
                        "is_multiple": bool(mcq.is_multiple),
                    }

                elif sq.question_type == "coding" and sq.coding_ref:
                    q = sq.coding_ref
                    q_wrapper["question"] = {
                        "id": str(q.id),
                        "title": q.title,
                        "short_description": q.short_description,
                        "long_description_markdown": q.long_description_markdown,
                        "sample_io": [
                            {
                                "input_text": s.input_text,
                                "output": s.output,
                                "explanation": s.explanation,
                            }
                            for s in (q.sample_io or [])
                        ],
                        "allowed_languages": q.allowed_languages or [],
                        "predefined_boilerplates": q.predefined_boilerplates or {},
                        "run_code_enabled": bool(q.run_code_enabled),
                        "submission_enabled": bool(q.submission_enabled),
                    }

                elif sq.question_type == "rearrange" and sq.rearrange_ref:
                    r = sq.rearrange_ref
                    q_wrapper["question"] = {
                        "id": str(r.id),
                        "title": r.title,
                        "prompt": r.prompt,
                        "question_images": safe_img_list(r.question_images),
                        "items": [
                            {
                                "item_id": it.item_id,
                                "value": it.value,
                                "images": safe_img_list(it.images),
                            }
                            for it in (r.items or [])
                        ],
                        "is_drag_and_drop": bool(r.is_drag_and_drop),
                    }

                else:
                    q_wrapper["question"] = {
                        "id": None,
                        "title": None,
                        "error": "reference_missing_or_invalid"
                    }

            except Exception:
                q_wrapper["question"] = {"id": None, "title": None, "error": "serialize_error"}

            q_wrappers.append(q_wrapper)

        # Shuffle question order if requested
        if self.is_shuffle_question and q_wrappers:
            rng.shuffle(q_wrappers)

        result["questions"] = q_wrappers
        return result
    
    def delete(self, cascade: bool = False, *args, **kwargs):
        # If you know questions are unique to this section, just delete them.
        try:
            mcq_ids = []
            coding_ids = []
            rearrange_ids = []

            for sq in (self.questions or []):
                if sq.question_type == "mcq" and getattr(sq, "mcq_ref", None):
                    mcq_ids.append(sq.mcq_ref.id)
                elif sq.question_type == "coding" and getattr(sq, "coding_ref", None):
                    coding_ids.append(sq.coding_ref.id)
                elif sq.question_type == "rearrange" and getattr(sq, "rearrange_ref", None):
                    rearrange_ids.append(sq.rearrange_ref.id)

            # Bulk delete by id (faster)
            if mcq_ids:
                MCQ.objects(id__in=mcq_ids).delete()
            if coding_ids:
                Question.objects(id__in=coding_ids).delete()
            if rearrange_ids:
                Rearrange.objects(id__in=rearrange_ids).delete()

        except Exception as e:
            print(f"[Section.delete] error deleting referenced questions: {e}")

        return super(Section, self).delete(*args, **kwargs)
