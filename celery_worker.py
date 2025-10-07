# celery_worker.py
from app import create_app
from celery_app import make_celery

flask_app = create_app()
celery = make_celery(flask_app)


import tasks  # <- add this line
