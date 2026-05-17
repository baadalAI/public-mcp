from fastapi import FastAPI
from celery import Celery

app = FastAPI()
celery_app = Celery("tasks", broker="redis://localhost:6379")
