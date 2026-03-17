from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    fatal: bool = True

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


ERROR_EXIT_CODE = {
    "E120": 120,
    "E121": 121,
    "E122": 122,
    "E201": 201,
    "E202": 202,
    "E203": 203,
    "E301": 301,
    "E302": 302,
    "E303": 303,
    "E401": 401,
    "E402": 402,
    "E501": 501,
    "E502": 502,
    "E601": 601,
    "E699": 699,
    "E999": 999,
}


def to_exit_code(code: str) -> int:
    return ERROR_EXIT_CODE.get(code, 1)
