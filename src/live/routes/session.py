"""Before and after the sermon: preflight, rate, export, diagnostics, rehearsal. Owned by S9."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import paths
import preflight
import report
from routes import body, run


def _err(status: int, error: str, detail: str, **extra) -> JSONResponse:
    return JSONResponse({"error": error, "detail": detail, **extra}, status_code=status)


def router(live) -> APIRouter:
    r = APIRouter()
    report.attach(live)                       # elapsed_s, first_word_ok_pct, preflight in the state

    def services():
        return paths.services_dir(live.lc["transcript_log_dir"], live.root)

    def the_log(b: dict):
        return report.find_log(services(), b.get("log"), live.log_path)

    async def save(write):
        """write(folder) -> path. The Desktop first; macOS may refuse it (Files & Folders) or the
        disk may be full, and then the app's own log folder. None when neither can be written."""
        for folder in (paths.desktop(), paths.log_dir(live.root) / "exports"):
            try:
                return await run(write, folder)
            except OSError:
                continue
        return None

    def unsaved():
        return _err(507, "write_failed", "The file could not be saved. Free some disk space, then try again.")

    @r.post("/api/preflight")
    async def do_preflight():
        res = await run(preflight.run, live)
        live._broadcast()
        return res

    @r.post("/api/rate")
    async def rate(request: Request):
        b = await body(request)
        log = the_log(b)
        if log is None:
            return _err(404, "no_session", "There is no service to rate yet.")
        try:
            s = await run(report.rate, log, paths.services_index(live.lc["transcript_log_dir"], live.root),
                          b.get("score"), b.get("tags"), b.get("note"))
        except ValueError as e:
            return _err(400, "bad_request", str(e))
        return {"ok": True, "log": log.name, "summary": s}

    @r.post("/api/export")
    async def export(request: Request):
        b = await body(request)
        fmt = b.get("format", "txt")
        if fmt not in ("txt", "srt", "docx"):
            return _err(400, "bad_request", "format must be txt, srt or docx")
        log = the_log(b)
        if log is None:
            return _err(404, "no_session", "There is no transcript to export yet.")
        path = await save(lambda d: report.export(log, fmt, d))
        if path is None:
            return unsaved()
        return {"ok": True, "format": fmt, "path": str(path), "log": log.name}

    @r.post("/api/diagnostics")
    async def diagnostics():
        settings = [live.root / "config.yaml", paths.settings_file(live.root), paths.resource("defaults.yaml", live.root)]
        try:
            st = live.state()
        except Exception as e:                # noqa: BLE001 - the zip is most needed when things break
            st = {"error": f"state failed: {type(e).__name__}: {e}"}
        path = await save(lambda d: report.diagnostics(d, paths.log_dir(live.root), services(),
                                                       list(dict.fromkeys(settings)), st,
                                                       {"profile": live.profile, "translator": live.lc.get("translator"),
                                                        "stt": live.lc.get("stt_model")}))
        if path is None:
            return unsaved()
        return {"ok": True, "path": str(path)}

    @r.post("/api/rehearsal")
    async def rehearsal(request: Request):
        b = await body(request)
        if live.running:
            return _err(409, "busy", "Stop captioning before starting a rehearsal.")
        sample = preflight.find_sample(live.root, b.get("sample"))
        if sample is None:
            return _err(404, "no_sample", "No such rehearsal recording.",
                        samples=[p.name for p in preflight.samples(live.root)])
        try:
            await run(preflight.rehearse, live, sample)
        except Exception as e:                # noqa: BLE001
            return _err(500, "start_failed", f"{type(e).__name__}: {e}")
        return live.state()

    return r
