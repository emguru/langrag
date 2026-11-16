"""Lambda entrypoint wrapping the ingestion FastAPI app."""

from mangum import Mangum

from ingestion.main import app

_asgi = Mangum(app, lifespan="off")


def handler(event, context):
    if isinstance(event, dict) and event.get("job") == "eval":
        from ingestion.eval_job import run_eval

        return run_eval()
    return _asgi(event, context)
