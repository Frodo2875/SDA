"""FastAPI application entry point."""

from fastapi import FastAPI


app = FastAPI(title="Student Document Agent", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    """Return the current service health status."""
    return {
        "status": "ok",
        "project": "Student Document Agent",
        "version": "0.1.0",
    }
