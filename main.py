from contextlib import asynccontextmanager

from fastapi import FastAPI

from db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = init_db()  # упадёт при старте, если база не открылась
    yield
    app.state.db.close()


app = FastAPI(title="FastAPI Web Server", lifespan=lifespan)


@app.get("/healthcheck")
def health_check():
    return {
        "status": "ok",
        "message": "Сервер работает стабильно",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)