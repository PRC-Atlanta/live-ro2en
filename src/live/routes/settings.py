"""Settings, keywords, spellings and session terms. Owned by S3 (logic in live/settings.py).
A rejected change answers 400 {error: rejected|invalid, keys?, detail} and changes nothing."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routes import body, run


def _reply(result):
    code, payload = result
    return JSONResponse(payload, status_code=code)


def router(live) -> APIRouter:
    r = APIRouter()

    @r.get("/api/settings")
    async def get_settings():
        return live.settings_view()

    @r.put("/api/settings")
    async def put_settings(request: Request):
        return _reply(await run(live.put_settings, await body(request)))

    @r.get("/api/keywords")
    async def get_keywords():
        return await run(live.keywords_view)

    @r.put("/api/keywords")
    async def put_keywords(request: Request):
        return _reply(await run(live.put_keywords, await body(request)))

    @r.get("/api/blocklist")
    async def get_blocklist():
        return await run(live.blocklist_view)

    @r.put("/api/blocklist")
    async def put_blocklist(request: Request):
        return _reply(await run(live.put_blocklist, await body(request)))

    @r.get("/api/spellings")
    async def get_spellings():
        return live.spellings_view()

    @r.put("/api/spellings")
    async def put_spellings(request: Request):
        return _reply(await run(live.put_spellings, await body(request)))

    @r.get("/api/terms")
    async def get_terms():
        return live.terms_view()

    @r.post("/api/terms")
    async def post_terms(request: Request):
        return _reply(await run(live.set_terms, await body(request)))

    @r.post("/api/import_config")
    async def import_config(request: Request):
        return _reply(await run(live.import_config, await body(request)))

    return r
