from fastapi import FastAPI

app = FastAPI(title="FastAPI Web Server")

@app.get("/healthcheck")
def health_check():
    return {
        "status": "ok",
        "message": "Сервер работает стабильно"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
