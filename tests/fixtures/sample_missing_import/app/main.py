from fastapi import FastAPI
from app.settings import get_settings
from app.database import get_db

app = FastAPI()

@app.get("/")
def read_root():
    return {"hello": "world"}
