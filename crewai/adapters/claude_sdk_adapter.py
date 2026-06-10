"""
Claude SDK backend: uses the OAPE server (Claude Agent SDK) for implementation from an EP.

Requires the server to be running (or a server URL). Typically used for:
- api-implement: generate controller/reconciler code from an enhancement proposal PR.

Scope should include an EP URL (via extra_context, or set OAPE_EP_URL / --ep-url).
Operator repo path: OAPE_OPERATOR_CWD or server default (cwd).
"""

import json
import os
import re
from typing import Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import WorkflowAdapter, WorkflowResult

EP_URL_PATTERN = re.compile(
    r"https://github\.com/openshift/enhancements/pull/(\d+)",
    re.IGNORECASE,
)


def _extract_ep_url_from_scope(scope) -> Optional[str]:
    """Get EP URL from scope.extra_context or OAPE_EP_URL env."""
    ep_url = os.getenv("OAPE_EP_URL", "").strip()
    if ep_url:
        return ep_url
    extra = (scope.extra_context or "") or ""
    match = EP_URL_PATTERN.search(extra)
    if match:
        return f"https://github.com/openshift/enhancements/pull/{match.group(1)}"
    return None


class ClaudeSDKAdapter(WorkflowAdapter):
    """Execute the implementation workflow via the OAPE Claude SDK server (api-implement)."""

    def __init__(self, server_url: Optional[str] = None):
        """
        server_url: base URL of the OAPE server (e.g. http://localhost:8000).
                    Defaults to OAPE_CLAUDE_SDK_SERVER_URL env.
        """
        self.server_url = (server_url or os.getenv("OAPE_CLAUDE_SDK_SERVER_URL", "").strip()) or "http://localhost:8000"

    @property
    def backend_name(self) -> str:
        return "claude-sdk"

    def run(self, scope) -> WorkflowResult:
        from context import ProjectScope

        if not isinstance(scope, ProjectScope):
            return WorkflowResult(
                success=False,
                output_text="",
                backend=self.backend_name,
                error="scope must be a ProjectScope instance",
            )

        ep_url = _extract_ep_url_from_scope(scope)
        if not ep_url:
            return WorkflowResult(
                success=False,
                output_text="",
                backend=self.backend_name,
                error="Claude SDK backend requires an EP URL. Set OAPE_EP_URL or --ep-url, or include the URL in scope/context file.",
            )

        cwd = os.getenv("OAPE_OPERATOR_CWD", "").strip()
        try:
            url = f"{self.server_url.rstrip('/')}/api-implement?{urlencode({'ep_url': ep_url, 'cwd': cwd})}"
            with urlopen(Request(url), timeout=300) as resp:
                data = json.loads(resp.read().decode())
            if data.get("status") != "success":
                return WorkflowResult(
                    success=False,
                    output_text=data.get("output", ""),
                    backend=self.backend_name,
                    artifacts=data,
                    error=data.get("detail", "Server returned non-success"),
                )
            return WorkflowResult(
                success=True,
                output_text=data.get("output", ""),
                backend=self.backend_name,
                artifacts={
                    "ep_url": data.get("ep_url"),
                    "cwd": data.get("cwd"),
                    "cost_usd": data.get("cost_usd"),
                },
            )
        except HTTPError as e:
            try:
                body = e.fp.read().decode() if e.fp else ""
            except Exception:
                body = ""
            return WorkflowResult(
                success=False,
                output_text=body[:2000] if body else "",
                backend=self.backend_name,
                error=f"Server returned {e.code}: {e.reason}",
            )
        except Exception as e:
            return WorkflowResult(
                success=False,
                output_text="",
                backend=self.backend_name,
                error=str(e),
            )
