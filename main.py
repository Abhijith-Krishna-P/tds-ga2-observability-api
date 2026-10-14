import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from threading import Lock

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

EMAIL = "24f2005862@ds.study.iitm.ac.in"
START_TIME = time.monotonic()

# A Prometheus Counter named "http_requests" exposes
# the required metric name "http_requests_total".
REQUEST_COUNTER = Counter(
    "http_requests",
    "Total number of HTTP requests received by this service",
)

LOG_ENTRIES = deque(maxlen=2000)
LOG_LOCK = Lock()

app = FastAPI(title="TDS GA2 Production Observability")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        import json

        return json.dumps({
            "level": record.levelname.lower(),
            "ts": datetime.now(timezone.utc).isoformat(),
            "message": record.getMessage(),
        })


logger = logging.getLogger("observability")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    # Count every HTTP request, including metrics and health checks.
    REQUEST_COUNTER.inc()

    start = time.perf_counter()
    status_code = 500

    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 3)

        entry = {
            "level": "info" if status_code < 500 else "error",
            "ts": datetime.now(timezone.utc).isoformat(),
            "path": request.url.path,
            "request_id": request_id,
            "method": request.method,
            "status_code": status_code,
            "duration_ms": duration_ms,
        }

        with LOG_LOCK:
            LOG_ENTRIES.append(entry)

        logger.info(
            "request %s %s status=%s request_id=%s",
            request.method,
            request.url.path,
            status_code,
            request_id,
        )


@app.get("/work")
def work(n: int = Query(..., ge=0, le=1000000)):
    # Perform K units of simple, deterministic work.
    result = 0
    for i in range(n):
        result += i

    return {"email": EMAIL, "done": n}


@app.get("/metrics")
def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "uptime_s": max(0.0, time.monotonic() - START_TIME),
    }


@app.get("/logs/tail")
def logs_tail(limit: int = Query(default=100, ge=1, le=2000)):
    with LOG_LOCK:
        return list(LOG_ENTRIES)[-limit:][::-1]
