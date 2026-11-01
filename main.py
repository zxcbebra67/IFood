from fastapi import FastAPI

<<<<<<< HEAD
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
=======
app = FastAPI()


@app.get("/")
def read_root():
  return {"Hello": "World"}
>>>>>>> 703466c4b87a3ef6959d33c23084feac08e3c4ec
