import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

celery = Celery("deloai")

if os.getenv("FLASK_ENV") == "development":
    # In dev → run tasks immediately, no Redis
    celery.conf.update(task_always_eager=True, task_eager_propagates=True)
else:
    # In prod → use Redis
    celery.conf.update(
        broker_url=os.getenv("CELERY_BROKER_URL"),
        result_backend=os.getenv("CELERY_RESULT_BACKEND"),
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="Asia/Kolkata",
    )
