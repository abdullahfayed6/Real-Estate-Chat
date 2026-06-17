from fastapi import FastAPI

from app.core.config import OPENAI_MODEL, DB_USER, DB_HOST, DB_NAME, logger
from app.api.routes import router

app = FastAPI(title="Riyadh Real Estate Chat API")
app.include_router(router)


@app.on_event("startup")
def on_startup():
    logger.info("Startup complete. Model=%s DB=%s@%s/%s",
                OPENAI_MODEL, DB_USER, DB_HOST, DB_NAME)
