"""
Optional: CrewAI LLM using Claude via Google Vertex AI (no API key).
Use when OAPE_CREWAI_USE_VERTEX=1. Requires gcloud auth application-default login
or GOOGLE_APPLICATION_CREDENTIALS.
"""

from typing import Any, Dict, List, Optional, Union

from crewai import BaseLLM


class VertexClaudeLLM(BaseLLM):
    """Claude on Vertex AI via ADC."""

    def __init__(
        self,
        model: str,
        project_id: str,
        region: str,
        temperature: Optional[float] = None,
        max_tokens: int = 8192,
    ):
        super().__init__(model=model, temperature=temperature or 0.2)
        self._project_id = project_id
        self._region = region
        self._max_tokens = max_tokens
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from anthropic import AnthropicVertex
            self._client = AnthropicVertex(
                project_id=self._project_id,
                region=self._region,
            )
        return self._client

    def call(
        self,
        messages: Union[str, List[Dict[str, str]]],
        tools: Optional[List[dict]] = None,
        callbacks: Optional[List[Any]] = None,
        available_functions: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Union[str, Any]:
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        formatted = []
        for m in messages:
            role = (m.get("role") or "user").lower()
            if role == "human":
                role = "user"
            if role not in ("user", "assistant"):
                role = "user"
            content = m.get("content") or m.get("text") or ""
            if isinstance(content, list):
                content = next((c.get("text", "") for c in content if isinstance(c, dict)), str(content))
            formatted.append({"role": role, "content": str(content)})
        if not formatted:
            return ""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            messages=formatted,
            temperature=self.temperature,
        )
        if response.content and len(response.content) > 0:
            block = response.content[0]
            return getattr(block, "text", str(block)) if hasattr(block, "text") else str(block)
        return ""

    def supports_function_calling(self) -> bool:
        return True

    def get_context_window_size(self) -> int:
        return 200_000
