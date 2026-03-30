from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field

from src.gui.i18n import build_translator, detect_system_language


ERROR_LANGUAGE: ContextVar[str] = ContextVar("error_language", default=detect_system_language())


def set_error_language(language: str) -> None:
    ERROR_LANGUAGE.set(language)


def get_error_language() -> str:
    return ERROR_LANGUAGE.get()


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str = ""
    fatal: bool = True
    message_key: str | None = None
    message_params: dict[str, object] = field(default_factory=dict)

    @classmethod
    def translated(
        cls,
        code: str,
        message_key: str,
        *,
        fatal: bool = True,
        **message_params: object,
    ) -> "AppError":
        return cls(code=code, fatal=fatal, message_key=message_key, message_params=message_params)

    def localized_message(self, language: str | None = None) -> str:
        if self.message_key is None:
            return self.message
        translator = build_translator(language or get_error_language())
        return translator(self.message_key, **self.message_params)

    def __str__(self) -> str:
        return f"{self.code}: {self.localized_message()}"


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
