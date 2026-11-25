"""Start VoiceGuard on the port supplied by local or cloud hosting."""

from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "3000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
