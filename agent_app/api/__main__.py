"""Run the travel planning HTTP API with Uvicorn."""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "agent_app.api.app:create_app",
        factory=True,
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips=os.getenv(
            "FORWARDED_ALLOW_IPS",
            "127.0.0.1",
        ),
    )


if __name__ == "__main__":
    main()
