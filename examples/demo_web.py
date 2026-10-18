"""FastAPI integration demo with Shadow Debugger."""

import os
import shadow_debugger

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def run_fastapi_example() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    shadow_debugger.init(
        provider="gemini",
        model="gemini-3.8-flash",
        output_dir="./shadow_reports",
        print_to_console=True,
    )

    app = FastAPI(title="Demo API")
    app.add_middleware(shadow_debugger.FastAPIWatcherMiddleware)

    @app.get("/items/{item_id}")
    async def get_item(item_id: int) -> dict:
        if item_id == 0:
            raise ZeroDivisionError("Failed calculating stock ratio for item 0")
        return {"item_id": item_id, "status": "available"}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/items/0")
    print(f"FastAPI HTTP status: {response.status_code} (returned immediately)")
    shadow_debugger.flush(timeout=15.0)


if __name__ == "__main__":
    run_fastapi_example()
