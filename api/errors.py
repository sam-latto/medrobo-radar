"""One error shape for every failure: {"error": {"code", "message", ...}}."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def not_found(what: str = "Not found") -> ApiError:
    return ApiError(404, "not_found", what)


def _body(code: str, message: str, **extra) -> dict:
    return {"error": {"code": code, "message": message, **extra}}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(_body(exc.code, exc.message, **exc.extra), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                   for e in exc.errors()]
        return JSONResponse(_body("invalid_request", "Some fields are missing or invalid.",
                                  details=details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        return JSONResponse(_body("server_error", "Something went wrong. Please try again."),
                            status_code=500)
