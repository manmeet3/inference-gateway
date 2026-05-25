from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    errors: dict[str, str] = {}
    try:
        await request.app.state.redis.ping()
    except Exception as exc:
        errors["redis"] = str(exc)

    if errors:
        return JSONResponse(status_code=503, content={"status": "not_ready", "errors": errors})
    return {"status": "ready"}
