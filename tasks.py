# tasks.py
from celery_worker import celery

@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def sync_form_submissions_task(self, form_id: str):
    try:
        from models.students_placement_profile import StudentsPlacementProfile
        from models.students_placement_submission import StudentsPlacementSubmission

        form = StudentsPlacementProfile.objects.get(id=form_id)
        StudentsPlacementSubmission.sync_all_for_form(form)
        print('synced')
        return {"status": "ok", "form_id": str(form_id)}
    except Exception as exc:
        try:
            self.retry(exc=exc)
        except Exception:
            raise
