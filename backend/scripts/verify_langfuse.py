"""Send a small setup trace after Langfuse credentials have been configured."""

from __future__ import annotations

import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.observability.langfuse_tracing import (  # noqa: E402
    langfuse_is_configured,
    update_observation,
)
from langfuse import get_client, propagate_attributes  # noqa: E402


def main() -> int:
    if not langfuse_is_configured():
        print(
            "Langfuse credentials are not available in this terminal. "
            "Run configure-langfuse.ps1 with a new key pair in this terminal."
        )
        return 2

    client = get_client()
    if not client.auth_check():
        print("Langfuse authentication failed. Check the region and key pair.")
        return 3

    with client.start_as_current_observation(
        as_type="span",
        name="verify-observability",
        input={"check": "sdk-export"},
    ) as verification:
        with propagate_attributes(
            session_id="setup-verification",
            tags=["odoo-agent", "setup"],
            metadata={"access_mode": "read-only"},
            version="0.1.0",
        ):
            with client.start_as_current_observation(
                as_type="span",
                name="check-sdk-export",
                input={"region": "eu"},
            ) as export_check:
                update_observation(export_check, output={"status": "ready"})

            trace_id = client.get_current_trace_id()
            update_observation(
                verification,
                output={"status": "observability-ready"},
            )

    client.flush()
    trace_url = client.get_trace_url(trace_id=trace_id)
    print("Langfuse authentication and trace export succeeded.")
    print(f"Trace URL: {trace_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
