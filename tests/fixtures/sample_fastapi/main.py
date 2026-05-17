from fastapi import FastAPI
from sqlalchemy import create_engine

app = FastAPI()
engine = create_engine("postgresql://localhost/mydb")

@app.get("/")
def read_root():
    return {"hello": "world"}
