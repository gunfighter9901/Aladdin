"""Service errors that routers turn into HTTP responses. Messages are already safe."""


class AppError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "bad_request") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code

    def to_dict(self) -> dict:
        return {"detail": self.message, "code": self.code}


class GeminiUnavailable(AppError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "Add GEMINI_API_KEY to backend/.env to use the tutor, quizzes, podcasts, and diagrams.",
            status_code=503,
            code="gemini_unavailable",
        )
