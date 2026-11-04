"""HTTP RSS service, compatible with the original Docker and reverse proxy setup."""

from contextlib import asynccontextmanager
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from dblp import normalize_stream
from local_dblp import FeedPending, LocalDBLP


def create_app(service=None):
    service = service or LocalDBLP()

    @asynccontextmanager
    async def lifespan(app):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        service.settings.cache_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            service.settings.cache_dir / "request.log", maxBytes=5_000_000,
            backupCount=3, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        request_logger = logging.getLogger("dblp.requests")
        request_logger.setLevel(logging.INFO)
        request_logger.propagate = False
        request_logger.addHandler(handler)
        service.start()
        try:
            yield
        finally:
            service.stop()
            request_logger.removeHandler(handler)
            handler.close()

    app = FastAPI(lifespan=lifespan)
    app.state.dblp = service

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        response = await call_next(request)
        logging.getLogger("dblp.requests").info(
            "IP=%s %s %s status=%s", request.client.host if request.client else "-",
            request.method, request.url.path, response.status_code,
        )
        return response

    @app.get("/health")
    def health():
        return service.status()

    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/search")

    @app.get("/search", include_in_schema=False)
    def search_page():
        return FileResponse(Path(__file__).parent / "templates" / "search.html",
                            media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/venues")
    def search_venues(q: str = Query("", max_length=200),
                      kind: Literal["all", "journals", "conf"] = "all",
                      limit: int = Query(30, ge=1, le=100),
                      offset: int = Query(0, ge=0)):
        return service.store.search_venues(q, kind, limit, offset)

    @app.get("/dblp/{keyword:path}")
    async def get_dblp_rss(keyword: str):
        try:
            stream = normalize_stream(keyword)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            rss = await run_in_threadpool(service.feed, stream)
        except FeedPending as exc:
            raise HTTPException(status_code=503, detail=str(exc),
                                headers={"Retry-After": "60"}) from exc
        return Response(rss, media_type="application/rss+xml",
                        headers={"Cache-Control": "no-cache"})

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)
