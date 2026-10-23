"""Settings, keywords, spellings and session terms. Owned by S3."""
from fastapi import APIRouter

from routes import not_built


def router(live) -> APIRouter:
    r = APIRouter()
    for method, path in (("GET", "/api/settings"), ("PUT", "/api/settings"), ("GET", "/api/keywords"),
                         ("PUT", "/api/keywords"), ("GET", "/api/spellings"), ("PUT", "/api/spellings"),
                         ("POST", "/api/terms"), ("POST", "/api/import_config")):
        not_built(r, method, path, "S3")
    return r
