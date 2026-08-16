from typing import Any, Optional


class APIError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class ProviderInvocationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        safe_details: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        # Provider responses and validation paths are untrusted model output.
        # Never expose their prose through SSE, /models, export, or provider
        # tests. The original exception string remains process-local only.
        public_messages = {
            "provider_refusal": "The provider declined to return a structured prediction",
            "provider_schema_mismatch": "The provider response did not match the required structured schema",
            "invalid_provider_json": "The provider response was not valid structured JSON",
            "invalid_provider_envelope": "The provider returned an invalid response envelope",
            "empty_provider_response": "The provider returned no usable structured prediction",
            "provider_output_truncated": "The provider response was truncated before the structured prediction completed",
        }
        self.safe_message = public_messages.get(code, message)
        self.retryable = retryable
        # Callers may persist or expose this field.  It must therefore contain
        # only adapter-generated metadata, never provider prose or values.
        self.safe_details = safe_details

    def safe_payload(self) -> dict[str, Any]:
        payload = {
            "code": self.code,
            "message": self.safe_message,
            "retryable": self.retryable,
        }
        if self.safe_details is not None:
            payload["details"] = self.safe_details
        return payload


class ProviderRefusal(ProviderInvocationError):
    def __init__(self, message: str = "The model refused to produce a prediction") -> None:
        super().__init__("provider_refusal", message, retryable=False)
