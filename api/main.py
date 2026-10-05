from fastapi import FastAPI

app = FastAPI(title="FirmFactor API", version="0.1.0")


@app.get("/health")
def health():
    """Vérifie que l'API tourne."""
    return {"status": "ok"}
