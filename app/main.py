from fastapi import FastAPI
app = FastAPI()

@app.get("/test")
def test():
    return {"message": "테스트"}