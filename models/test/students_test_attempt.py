# models/student_attempt.py
from datetime import datetime
from mongoengine import (
    Document, EmbeddedDocument, EmbeddedDocumentField, StringField, IntField,
    ListField, DictField, BooleanField, DateTimeField, FloatField
)
from typing import Optional

# Snapshots --------------------------------------------------------
class MCQSnapshot(EmbeddedDocument):
    """Snapshot of an MCQ when student answered (what they saw)."""
    question_id = StringField(required=True)
    title = StringField()
    question_text = StringField()
    options = ListField(DictField(), default=list)  # [{'option_id':..., 'value':...}, ...]
    is_multiple = BooleanField(default=False)
    marks = FloatField(default=0.0)
    negative_marks = FloatField(default=0.0)

class CodingSnapshot(EmbeddedDocument):
    """Snapshot for coding question."""
    question_id = StringField(required=True)
    title = StringField()
    short_description = StringField()
    long_description_markdown = StringField()
    sample_io = ListField(DictField(), default=list)
    allowed_languages = ListField(StringField(), default=list)
    predefined_boilerplates = DictField(default=dict)
    run_code_enabled = BooleanField(default=True)
    submission_enabled = BooleanField(default=True)
    marks = FloatField(default=0.0)
    negative_marks = FloatField(default=0.0)

class RearrangeSnapshot(EmbeddedDocument):
    """Snapshot for rearrange question."""
    question_id = StringField(required=True)
    title = StringField()
    prompt = StringField()
    items = ListField(DictField(), default=list)  # [{'item_id':..., 'value':...}, ...]
    is_drag_and_drop = BooleanField(default=True)
    marks = FloatField(default=0.0)
    negative_marks = FloatField(default=0.0)

# Student answer with snapshot and marks_obtained -------------------
class StudentAnswer(EmbeddedDocument):
    question_id = StringField(required=True)
    question_type = StringField(required=True, choices=["mcq", "coding", "rearrange"])
    # value semantics:
    #  - mcq: list of option_ids (strings)
    #  - coding: dict with {'language':..., 'source_code':..., 'submission_id':...} (optional)
    #  - rearrange: list of item_ids in student order
    value = DictField()  # flexible container; for mcq we store {'value': [...]} for compatibility
    snapshot_mcq = EmbeddedDocumentField(MCQSnapshot, null=True)
    snapshot_coding = EmbeddedDocumentField(CodingSnapshot, null=True)
    snapshot_rearrange = EmbeddedDocumentField(RearrangeSnapshot, null=True)

    # marks obtained for this answer (None if not graded / not applicable yet)
    marks_obtained = FloatField(null=True)

# Section answers grouping ----------------------------------------
class SectionAnswers(EmbeddedDocument):
    section_id = StringField(required=True)
    answers = ListField(EmbeddedDocumentField(StudentAnswer), default=list)

# StudentTestAttempt with timed/open lists -------------------------
class StudentTestAttempt(Document):
    student_id = StringField(required=True)
    test_id = StringField(required=True)
    start_time = DateTimeField(null=True)

    timed_section_answers = ListField(EmbeddedDocumentField(SectionAnswers), default=list)
    open_section_answers = ListField(EmbeddedDocumentField(SectionAnswers), default=list)
    total_marks = FloatField(default=0.0)

    last_autosave = DateTimeField(default=datetime.utcnow)
    submitted = BooleanField(default=False)
    submitted_at = DateTimeField(null=True)
    meta = {
        "collection": "student_test_assignments",
        "indexes": [("student_id", "test_id"), "student_id", "test_id"],
    }

    # ------------------------
    # Helper: grade MCQ using simple rule
    # ------------------------
    def _grade_mcq(self, mcq, selected_option_ids):
        """
        mcq: TestMCQ document instance (has correct_options (list of option_ids), is_multiple, marks, negative_marks)
        selected_option_ids: list of strings
        Returns: float marks (clamped between 0 and mcq.marks)
        Rules implemented:
          - single-choice: if selected == single correct -> full marks, else 0 (no negative on autosave).
            (You can change to allow negative by removing clamp).
          - multiple-choice: score = marks * (num_correct_selected / num_correct) - negative_marks * num_incorrect_selected
            then clamped to [0, marks].
        """
        try:
            selected = set(selected_option_ids or [])
            correct = set(mcq.correct_options or [])
            max_marks = float(mcq.marks or 0.0)
            neg = float(mcq.negative_marks or 0.0)

            if not mcq.is_multiple:
                # expect single select; if correct, full. otherwise zero on autosave
                if len(selected) == 1 and next(iter(selected)) in correct:
                    return max_marks
                return 0.0
            else:
                if not correct:
                    return 0.0
                correct_selected = selected.intersection(correct)
                incorrect_selected = selected.difference(correct)
                ratio = len(correct_selected) / len(correct)
                score = max_marks * ratio - neg * len(incorrect_selected)
                # clamp
                if score < 0:
                    score = 0.0
                if score > max_marks:
                    score = max_marks
                return float(score)
        except Exception:
            return 0.0


    def _grade_rearrange(self, rearr_obj, student_order):
        """
        Exact-match grader: full marks only when student's order == correct order.
        Otherwise returns 0.0.
        """
        try:
            if not rearr_obj:
                return 0.0
            print(rearr_obj.correct_order)
            correct_order = rearr_obj.correct_order
            print(correct_order)
            if not correct_order:
                return 0.0

            student = list(student_order or [])
            # consider only the first N items where N = len(correct_order)
            student = student[:len(correct_order)]

            max_marks = float(getattr(rearr_obj, "marks", 0.0) or 0.0)
            print(student)
            # exact match required for full marks
            if student == correct_order:
                return float(max_marks)
            return 0.0
        except Exception:
            return 0.0
    def total_marks_obtained(self) -> float:
        """Sum of all marks_obtained across all answers."""
        total = 0.0
        for section in (self.timed_section_answers or []):
            for ans in (section.answers or []):
                if ans.marks_obtained is not None:
                    total += ans.marks_obtained
        for section in (self.open_section_answers or []):
            for ans in (section.answers or []):
                if ans.marks_obtained is not None:
                    total += ans.marks_obtained
        return total

    # ------------------------
    # Autosave: populate snapshots, compute MCQ marks, upsert answers
    # answers_dict format: { section_id: { question_id: {'value': [...], 'qwell': 'mcq' }, ... }, ... }
    # test_obj not strictly required but passing it helps avoid fetching sections repeatedly.
    # ------------------------
    def save_autosave(self, answers_dict: dict, test_obj=None):
        from models.test.section import Section
        from models.questions.mcq import TestMCQ as MCQModel
        from models.questions.coding import TestQuestion as CodingModel
        from models.questions.rearrange import TestRearrange as RearrangeModel

        for section_id, qmap in (answers_dict or {}).items():
            # fetch section to determine time_restricted flag and to access question refs
            section = Section.objects(id=section_id).first()
            if not section:
                # If section not found, skip (could log)
                continue

            target_list = (self.timed_section_answers if section.time_restricted else self.open_section_answers)

            # find or create SectionAnswers wrapper
            sec_ans = next((s for s in target_list if s.section_id == str(section_id)), None)
            if not sec_ans:
                sec_ans = SectionAnswers(section_id=str(section_id), answers=[])
                target_list.append(sec_ans)

            # iterate questions in that section payload
            for qid, payload in (qmap or {}).items():
                qwell = payload.get("qwell")
                raw_value = payload.get("value", None)

                # find existing answer
                existing = next((a for a in sec_ans.answers if a.question_id == str(qid)), None)

                # --- MCQ handling (autosave + grading)
                if qwell == "mcq":
                    # find mcq reference inside the section (if available)
                    mcq_ref = None
                    try:
                        for sq in (section.questions or []):
                            if sq.question_type == "mcq" and sq.mcq_ref and str(sq.mcq_ref.id) == str(qid):
                                mcq_ref = sq.mcq_ref
                                break
                    except Exception:
                        mcq_ref = None

                    snapshot = None
                    marks_awarded = None
                    if mcq_ref:
                        print('mcq')
                        # build snapshot
                        snapshot = MCQSnapshot(
                            question_id=str(mcq_ref.id),
                            title=getattr(mcq_ref, "title", None),
                            question_text=getattr(mcq_ref, "question_text", None),
                            options=[{"option_id": o.option_id, "value": o.value} for o in (mcq_ref.options or [])],
                            is_multiple=bool(mcq_ref.is_multiple),
                            marks=float(mcq_ref.marks or 0.0),
                            negative_marks=float(mcq_ref.negative_marks or 0.0),
                        )
                        # grade using the function above; raw_value might be list or dict depending on client
                        selected = []
                        if isinstance(raw_value, list):
                            selected = raw_value
                        elif isinstance(raw_value, dict) and "value" in raw_value:
                            selected = raw_value.get("value") or []
                        elif isinstance(raw_value, str):
                            selected = [raw_value]
                        marks_awarded = float(self._grade_mcq(mcq_ref, selected))
                        print(marks_awarded)

                    # prepare value container for storage (normalize to {'value': [...]} for mcq)
                    store_value = {}
                    if isinstance(raw_value, list):
                        store_value["value"] = raw_value
                    elif isinstance(raw_value, dict) and "value" in raw_value:
                        store_value["value"] = raw_value.get("value")
                    else:
                        store_value["value"] = raw_value if raw_value is not None else []

                    if existing:
                        existing.value = store_value
                        existing.snapshot_mcq = snapshot
                        existing.marks_obtained = marks_awarded
                    else:
                        ans = StudentAnswer(
                            question_id=str(qid),
                            question_type="mcq",
                            value=store_value,
                            snapshot_mcq=snapshot,
                            marks_obtained=marks_awarded
                        )
                        sec_ans.answers.append(ans)

                # --- Coding (snapshot only on autosave; grading deferred)
                elif qwell == "coding":
                    # locate coding question ref if present on section
                    coding_ref = None
                    try:
                        for sq in (section.questions or []):
                            if sq.question_type == "coding" and sq.coding_ref and str(sq.coding_ref.id) == str(qid):
                                coding_ref = sq.coding_ref
                                break
                    except Exception:
                        coding_ref = None

                    snapshot = None
                    if coding_ref:
                        snapshot = CodingSnapshot(
                            question_id=str(coding_ref.id),
                            title=getattr(coding_ref, "title", None),
                            short_description=getattr(coding_ref, "short_description", None),
                            long_description_markdown=getattr(coding_ref, "long_description_markdown", None),
                            sample_io=[{"input_text": s.input_text, "output": s.output, "explanation": s.explanation} for s in (coding_ref.sample_io or [])],
                            allowed_languages=coding_ref.allowed_languages or [],
                            predefined_boilerplates=coding_ref.predefined_boilerplates or {},
                            run_code_enabled=bool(getattr(coding_ref, "run_code_enabled", True)),
                            submission_enabled=bool(getattr(coding_ref, "submission_enabled", True)),
                            marks=float(getattr(coding_ref, "marks", 0.0)),
                            negative_marks=float(getattr(coding_ref, "negative_marks", 0.0))
                        )

                    # --- Normalize store_value:
                    # Client may send:
                    #   - a list of submission ids: ['id1','id2']
                    #   - a single submission id string: 'id1'
                    #   - a dict already: {'value': [...]} or {'submission_ids': [...]}
                    store_value = {}
                    if isinstance(raw_value, list):
                        store_value["value"] = raw_value
                    elif isinstance(raw_value, dict):
                        # keep "value" if present, else accept "submission_ids"
                        if "value" in raw_value:
                            store_value["value"] = raw_value.get("value") or []
                        elif "submission_ids" in raw_value:
                            store_value["value"] = raw_value.get("submission_ids") or []
                        else:
                            # generic dict — store as-is under value key
                            store_value["value"] = raw_value
                    elif isinstance(raw_value, str):
                        store_value["value"] = [raw_value]
                    else:
                        store_value["value"] = []

                    # --- Attempt to compute marks_awarded from submissions (autosave grade)
                    marks_awarded = None
                    try:
                        # only try if we have a coding_ref and at least one submission id
                        sub_ids = store_value.get("value") or []
                        # normalize to strings
                        sub_ids = [str(x) for x in sub_ids if x]
                        if coding_ref and sub_ids:
                            from models.questions.coding import Submission  # import here to avoid circulars
                            # fetch submissions that match these ids and belong to this user/question
                            subs = list(Submission.objects(id__in=sub_ids))
                            if subs:
                                # choose best run by total_score (tie-breaker: latest updated_at)
                                best = max(subs, key=lambda s: (float(getattr(s, "total_score", 0)), getattr(s, "updated_at", datetime.utcnow())))
                                # Convert submission's numeric score to question marks:
                                # - if submission.max_score present and >0, compute proportion
                                # - otherwise, take min(best.total_score, coding_ref.marks)
                                best_total = float(getattr(best, "total_score", 0) or 0.0)
                                raw_marks = best_total

                               

                                print(raw_marks)
                                marks_awarded = float(raw_marks)
                    except Exception:
                        marks_awarded = None

                    # upsert answer, keep existing.marks_obtained only if we couldn't compute a new one
                    if existing:
                        existing.value = store_value
                        existing.snapshot_coding = snapshot
                        if marks_awarded is not None:
                            existing.marks_obtained = marks_awarded
                        # otherwise preserve previous marks (likely None)
                    else:
                        ans = StudentAnswer(
                            question_id=str(qid),
                            question_type="coding",
                            value=store_value,
                            snapshot_coding=snapshot,
                            marks_obtained=marks_awarded
                        )
                        sec_ans.answers.append(ans)   # --- Rearrange (snapshot only, marks left None)
                                # --- Rearrange (snapshot + autosave grading)
                elif qwell == "rearrange":
                    print('rearrange')
                    # locate rearrange question ref if present on section
                    rearr_ref = None
                    try:
                        for sq in (section.questions or []):
                            if sq.question_type == "rearrange" and sq.rearrange_ref and str(sq.rearrange_ref.id) == str(qid):
                                rearr_ref = sq.rearrange_ref
                                break
                    except Exception:
                        rearr_ref = None

                    snapshot = None
                    if rearr_ref:
                        snapshot = RearrangeSnapshot(
                            question_id=str(rearr_ref.id),
                            title=getattr(rearr_ref, "title", None),
                            prompt=getattr(rearr_ref, "prompt", None),
                            items=[{"item_id": it.item_id, "value": it.value} for it in (rearr_ref.items or [])],
                            is_drag_and_drop=bool(getattr(rearr_ref, "is_drag_and_drop", True)),
                            marks=float(getattr(rearr_ref, "marks", 0.0)),
                            negative_marks=float(getattr(rearr_ref, "negative_marks", 0.0))
                        )

                    # normalize value (list of item_ids) to {"value": [...]}
                    store_value = {}
                    if isinstance(raw_value, list):
                        # ensure all entries are strings (or as-is)
                        store_value["value"] = raw_value
                    elif isinstance(raw_value, dict) and "value" in raw_value:
                        val = raw_value.get("value")
                        if isinstance(val, list):
                            store_value["value"] = val
                        elif val is None:
                            store_value["value"] = []
                        else:
                            store_value["value"] = [val]
                    elif isinstance(raw_value, str):
                        store_value["value"] = [raw_value]
                    else:
                        store_value["value"] = []

                    # compute marks_awarded if we have a rearr_ref and a student value
                    marks_awarded = None
                    try:
                        if rearr_ref:
                            student_order = store_value.get("value") or []
                            marks_awarded = float(self._grade_rearrange(rearr_ref, student_order))
                            print(marks_awarded)
                    except Exception:
                        marks_awarded = None

                    # upsert answer, but only overwrite previous marks if we computed a mark
                    if existing:
                        existing.value = store_value
                        if snapshot is not None:
                            existing.snapshot_rearrange = snapshot
                        if marks_awarded is not None:
                            existing.marks_obtained = marks_awarded
                        # otherwise preserve existing.marks_obtained
                    else:
                        ans = StudentAnswer(
                            question_id=str(qid),
                            question_type="rearrange",
                            value=store_value,
                            snapshot_rearrange=snapshot,
                            marks_obtained=marks_awarded
                        )
                        sec_ans.answers.append(ans)

                else:
                    # unsupported qwell/type: store generically but no snapshot/grade
                    store_value = {"value": raw_value}
                    if existing:
                        existing.value = store_value
                    else:
                        ans = StudentAnswer(
                            question_id=str(qid),
                            question_type=qwell or "unknown",
                            value=store_value,
                            marks_obtained=None
                        )
                        sec_ans.answers.append(ans)

        # finished processing payload -> update timestamp and persist
        self.last_autosave = datetime.utcnow()
        self.total_marks = self.total_marks_obtained()

        self.save()
        return True
