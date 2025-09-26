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
    # New fields requested:
    correct_options = ListField(StringField(), default=list)  # store correct option ids
    explanation = StringField()  # explanation for the MCQ (if present)

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
    # New fields requested:
    correct_order = ListField(StringField(), default=list)  # store correct order of item_ids
    explanation = StringField()  # explanation for rearrange question (if present)

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
    # New snapshot fields on section wrapper:
    section_name = StringField()            # store section name at autosave time
    section_duration = IntField(default=0)  # store section duration (seconds or minutes as your app uses)
    answers = ListField(EmbeddedDocumentField(StudentAnswer), default=list)

    # NEW: section-level marks snapshot
    section_max_marks = FloatField(default=0.0)    # maximum possible marks for this section (from question snapshots)
    section_total_marks = FloatField(default=0.0)  # marks obtained so far in this section (sum of marks_obtained)

# StudentTestAttempt with timed/open lists -------------------------

class StudentTestAttempt(Document):
    student_id = StringField(required=True)
    test_id = StringField(required=True)
    start_time = DateTimeField(null=True)

    timed_section_answers = ListField(EmbeddedDocumentField(SectionAnswers), default=list)
    open_section_answers = ListField(EmbeddedDocumentField(SectionAnswers), default=list)

    # total marks obtained (kept from before)
    total_marks = FloatField(default=0.0)
     # ---------------- NEW FIELDS ----------------
    tab_switches_count = IntField(default=0)          # number of times student switched tab
    fullscreen_violated = BooleanField(default=False) # true if fullscreen violation detected
    # --------------------------------------------


    # NEW: maximum marks for the entire test (sum of section_max_marks)
    max_marks = FloatField(default=0.0)

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

    def max_marks_possible(self) -> float:
        """
        Compute the maximum possible marks for this attempt,
        based on the question snapshots in all sections.

        Returns:
            float: sum of marks from each question snapshot (mcq/coding/rearrange).
        """
        total_max = 0.0

        def section_max(sec_list):
            nonlocal total_max
            for sec in (sec_list or []):
                for ans in (sec.answers or []):
                    snap_marks = 0.0
                    if getattr(ans, "snapshot_mcq", None):
                        snap_marks = float(getattr(ans.snapshot_mcq, "marks", 0.0) or 0.0)
                    elif getattr(ans, "snapshot_coding", None):
                        snap_marks = float(getattr(ans.snapshot_coding, "marks", 0.0) or 0.0)
                    elif getattr(ans, "snapshot_rearrange", None):
                        snap_marks = float(getattr(ans.snapshot_rearrange, "marks", 0.0) or 0.0)
                    total_max += snap_marks

        section_max(self.timed_section_answers)
        section_max(self.open_section_answers)

        return total_max

    # ------------------------
    # Autosave: populate snapshots, compute MCQ marks, upsert answers
    # answers_dict format: { section_id: { question_id: {'value': [...], 'qwell': 'mcq' }, ... }, ... }
    # test_obj not strictly required but passing it helps avoid fetching sections repeatedly.
    # ------------------------
    def save_autosave(self, answers_dict: dict, test_obj=None):
        """
        Autosave that ensures the full test structure is stored section-wise:
          - If test_obj provided, use it; otherwise fetch Test by self.test_id.
          - Iterate every section in the test and ensure every question has a StudentAnswer
            (snapshot + normalized value). Merge any incoming answers from answers_dict.
        answers_dict format: { section_id: { question_id: {'value': [...], 'qwell': 'mcq' }, ... }, ... }
        """
        from models.test.section import Section
        from models.questions.mcq import TestMCQ as MCQModel
        from models.questions.coding import TestQuestion as CodingModel
        from models.questions.rearrange import TestRearrange as RearrangeModel
        from models.test.test import Test

        # Resolve the test object (prefer provided test_obj)
        try:
            if test_obj is None and getattr(self, "test_id", None):
                test_obj = Test.objects(id=str(self.test_id)).first()
        except Exception:
            test_obj = None

        # Build a mapping of section_id (string) -> Section document
        section_map = {}

        # 1) first include sections from the resolved test_obj (preferred source of truth)
        if test_obj:
            try:
                for s in (test_obj.sections_time_restricted or []):
                    if s is not None:
                        section_map[str(s.id)] = s
            except Exception:
                pass
            try:
                for s in (test_obj.sections_open or []):
                    if s is not None:
                        section_map[str(s.id)] = s
            except Exception:
                pass

        # 2) also include any section ids sent by frontend in answers_dict (in case client has cached/extra)
        for sent_section_id in (answers_dict or {}).keys():
            if str(sent_section_id) not in section_map:
                try:
                    sec = Section.objects(id=sent_section_id).first()
                    if sec:
                        section_map[str(sec.id)] = sec
                    else:
                        # keep None placeholder to still store client-sent questions in that wrapper
                        section_map[str(sent_section_id)] = None
                except Exception:
                    section_map[str(sent_section_id)] = None

        # Now iterate over every section key we gathered (union of test's sections + client sections)
        for section_id, section in section_map.items():
            # incoming payload map for that section (may be missing or empty)
            incoming_map = (answers_dict or {}).get(section_id, {}) or {}

            # choose correct target list (timed or open). If we don't have section doc, default to open list.
            target_list = None
            try:
                if section and getattr(section, "time_restricted", False):
                    target_list = self.timed_section_answers
                else:
                    target_list = self.open_section_answers
            except Exception:
                target_list = self.open_section_answers

            # find or create SectionAnswers wrapper
            sec_ans = next((s for s in target_list if s.section_id == str(section_id)), None)
            if not sec_ans:
                # include section name & duration in the wrapper (new snapshot fields)
                sec_ans = SectionAnswers(
                    section_id=str(section_id),
                    section_name=getattr(section, "name", None) if section else None,
                    section_duration=int(getattr(section, "duration", 0) or 0),
                    answers=[]
                )
                target_list.append(sec_ans)
            else:
                # update name/duration if we have a section doc (keep snapshot current)
                try:
                    if section:
                        sec_ans.section_name = getattr(section, "name", sec_ans.section_name)
                        sec_ans.section_duration = int(getattr(section, "duration", sec_ans.section_duration) or 0)
                except Exception:
                    pass

            # Build the question list to ensure full coverage:
            # - If we have a Section doc, use its questions (preferred).
            # - Otherwise fallback to keys present in incoming_map only.
            section_question_ids = []
            if section:
                for sq in (section.questions or []):
                    qid = None
                    try:
                        if sq.question_type == "mcq" and getattr(sq, "mcq_ref", None):
                            qid = str(sq.mcq_ref.id)
                        elif sq.question_type == "coding" and getattr(sq, "coding_ref", None):
                            qid = str(sq.coding_ref.id)
                        elif sq.question_type == "rearrange" and getattr(sq, "rearrange_ref", None):
                            qid = str(sq.rearrange_ref.id)
                    except Exception:
                        qid = None
                    if qid:
                        section_question_ids.append((qid, sq.question_type))
            else:
                # fallback: use whatever question ids the client sent under this section
                for qid, payload in (incoming_map or {}).items():
                    qwell = payload.get("qwell") or payload.get("question_type") or "unknown"
                    section_question_ids.append((str(qid), qwell))

            # Also include any client-sent questions that weren't present in the section doc
            for qid, payload in (incoming_map or {}).items():
                if str(qid) not in [x[0] for x in section_question_ids]:
                    qwell = payload.get("qwell") or payload.get("question_type") or "unknown"
                    section_question_ids.append((str(qid), qwell))

            # Iterate each question id and upsert StudentAnswer with snapshot (if possible)
            for qid, qwell in section_question_ids:
                payload = incoming_map.get(qid, {}) or {}
                raw_value = payload.get("value", None)

                # find existing answer
                existing = next((a for a in sec_ans.answers if a.question_id == str(qid)), None)

                snapshot = None
                marks_awarded = None
                store_value = {}

                # --- MCQ ---
                if qwell == "mcq":
                    mcq_ref = None
                    if section:
                        try:
                            for sq in (section.questions or []):
                                if sq.question_type == "mcq" and sq.mcq_ref and str(sq.mcq_ref.id) == str(qid):
                                    mcq_ref = sq.mcq_ref
                                    break
                        except Exception:
                            mcq_ref = None
                    # if no mcq_ref from section, try to fetch MCQ directly
                    if not mcq_ref:
                        try:
                            mcq_ref = MCQModel.objects(id=str(qid)).first()
                        except Exception:
                            mcq_ref = None

                    if mcq_ref:
                        snapshot = MCQSnapshot(
                            question_id=str(mcq_ref.id),
                            title=getattr(mcq_ref, "title", None),
                            question_text=getattr(mcq_ref, "question_text", None),
                            options=[{"option_id": o.option_id, "value": o.value} for o in (mcq_ref.options or [])],
                            is_multiple=bool(mcq_ref.is_multiple),
                            marks=float(mcq_ref.marks or 0.0),
                            negative_marks=float(mcq_ref.negative_marks or 0.0),
                            correct_options=list(mcq_ref.correct_options or []),
                            explanation=getattr(mcq_ref, "explanation", None),
                        )

                    # normalize value
                    if isinstance(raw_value, list):
                        store_value["value"] = raw_value
                    elif isinstance(raw_value, dict) and "value" in raw_value:
                        store_value["value"] = raw_value.get("value") or []
                    elif isinstance(raw_value, str):
                        store_value["value"] = [raw_value]
                    else:
                        store_value["value"] = raw_value if raw_value is not None else []

                    # grade only if client gave an answer
                    if mcq_ref and raw_value is not None:
                        selected = []
                        if isinstance(raw_value, list):
                            selected = raw_value
                        elif isinstance(raw_value, dict) and "value" in raw_value:
                            selected = raw_value.get("value") or []
                        elif isinstance(raw_value, str):
                            selected = [raw_value]
                        marks_awarded = float(self._grade_mcq(mcq_ref, selected))

                    if existing:
                        existing.value = store_value
                        existing.snapshot_mcq = snapshot
                        if marks_awarded is not None:
                            existing.marks_obtained = marks_awarded
                    else:
                        ans = StudentAnswer(
                            question_id=str(qid),
                            question_type="mcq",
                            value=store_value,
                            snapshot_mcq=snapshot,
                            marks_obtained=marks_awarded,
                        )
                        sec_ans.answers.append(ans)

                # --- Coding ---
                elif qwell == "coding":
                    coding_ref = None
                    if section:
                        try:
                            for sq in (section.questions or []):
                                if sq.question_type == "coding" and sq.coding_ref and str(sq.coding_ref.id) == str(qid):
                                    coding_ref = sq.coding_ref
                                    break
                        except Exception:
                            coding_ref = None
                    if not coding_ref:
                        try:
                            coding_ref = CodingModel.objects(id=str(qid)).first()
                        except Exception:
                            coding_ref = None

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
                            marks=float(getattr(coding_ref, "points", 0.0)),
                            negative_marks=float(getattr(coding_ref, "negative_marks", 0.0))
                        )

                    # normalize
                    if isinstance(raw_value, list):
                        store_value["value"] = raw_value
                    elif isinstance(raw_value, dict):
                        if "value" in raw_value:
                            store_value["value"] = raw_value.get("value") or []
                        elif "submission_ids" in raw_value:
                            store_value["value"] = raw_value.get("submission_ids") or []
                        else:
                            store_value["value"] = raw_value
                    elif isinstance(raw_value, str):
                        store_value["value"] = [raw_value]
                    else:
                        store_value["value"] = []

                    # try autosave marks if submissions present
                    marks_awarded = None
                    try:
                        sub_ids = store_value.get("value") or []
                        sub_ids = [str(x) for x in sub_ids if x]
                        if coding_ref and sub_ids:
                            from models.questions.coding import Submission
                            subs = list(Submission.objects(id__in=sub_ids))
                            if subs:
                                best = max(subs, key=lambda s: (float(getattr(s, "total_score", 0)), getattr(s, "updated_at", datetime.utcnow())))
                                best_total = float(getattr(best, "total_score", 0) or 0.0)
                                marks_awarded = float(best_total)
                    except Exception:
                        marks_awarded = None

                    if existing:
                        existing.value = store_value
                        existing.snapshot_coding = snapshot
                        if marks_awarded is not None:
                            existing.marks_obtained = marks_awarded
                    else:
                        ans = StudentAnswer(
                            question_id=str(qid),
                            question_type="coding",
                            value=store_value,
                            snapshot_coding=snapshot,
                            marks_obtained=marks_awarded,
                        )
                        sec_ans.answers.append(ans)

                # --- Rearrange ---
                elif qwell == "rearrange":
                    rearr_ref = None
                    if section:
                        try:
                            for sq in (section.questions or []):
                                if sq.question_type == "rearrange" and sq.rearrange_ref and str(sq.rearrange_ref.id) == str(qid):
                                    rearr_ref = sq.rearrange_ref
                                    break
                        except Exception:
                            rearr_ref = None
                    if not rearr_ref:
                        try:
                            rearr_ref = RearrangeModel.objects(id=str(qid)).first()
                        except Exception:
                            rearr_ref = None

                    if rearr_ref:
                        snapshot = RearrangeSnapshot(
                            question_id=str(rearr_ref.id),
                            title=getattr(rearr_ref, "title", None),
                            prompt=getattr(rearr_ref, "prompt", None),
                            items=[{"item_id": it.item_id, "value": it.value} for it in (rearr_ref.items or [])],
                            is_drag_and_drop=bool(getattr(rearr_ref, "is_drag_and_drop", True)),
                            marks=float(getattr(rearr_ref, "marks", 0.0)),
                            negative_marks=float(getattr(rearr_ref, "negative_marks", 0.0)),
                            correct_order=list(getattr(rearr_ref, "correct_order", []) or []),
                            explanation=getattr(rearr_ref, "explanation", None),
                        )

                    # normalize
                    if isinstance(raw_value, list):
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

                    marks_awarded = None
                    try:
                        if rearr_ref and raw_value is not None:
                            student_order = store_value.get("value") or []
                            marks_awarded = float(self._grade_rearrange(rearr_ref, student_order))
                    except Exception:
                        marks_awarded = None

                    if existing:
                        existing.value = store_value
                        if snapshot is not None:
                            existing.snapshot_rearrange = snapshot
                        if marks_awarded is not None:
                            existing.marks_obtained = marks_awarded
                    else:
                        ans = StudentAnswer(
                            question_id=str(qid),
                            question_type="rearrange",
                            value=store_value,
                            snapshot_rearrange=snapshot,
                            marks_obtained=marks_awarded,
                        )
                        sec_ans.answers.append(ans)

                # --- fallback unknown question type ---
                else:
                    store_value = {"value": raw_value} if raw_value is not None else {"value": None}
                    if existing:
                        existing.value = store_value
                    else:
                        ans = StudentAnswer(
                            question_id=str(qid),
                            question_type=qwell or "unknown",
                            value=store_value,
                            marks_obtained=None,
                        )
                        sec_ans.answers.append(ans)
            try:
                sec_max = 0.0
                sec_total = 0.0
                for a in (sec_ans.answers or []):
                    if a.snapshot_mcq:
                        sec_max += float(getattr(a.snapshot_mcq, "marks", 0.0) or 0.0)
                    elif a.snapshot_coding:
                        sec_max += float(getattr(a.snapshot_coding, "marks", 0.0) or 0.0)
                    elif a.snapshot_rearrange:
                        sec_max += float(getattr(a.snapshot_rearrange, "marks", 0.0) or 0.0)

                    if a.marks_obtained is not None:
                        sec_total += float(a.marks_obtained or 0.0)

                sec_ans.section_max_marks = float(sec_max)
                sec_ans.section_total_marks = float(sec_total)
            except Exception:
                # be safe: don't crash autosave on aggregation error
                pass
        # finished processing payload -> update timestamp and persist
        self.last_autosave = datetime.utcnow()
        self.total_marks = self.total_marks_obtained()
        self.max_marks = self.max_marks_possible()
        self.save()
        return True
