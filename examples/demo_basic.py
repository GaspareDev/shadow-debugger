"""Basic example of Shadow Debugger with @watch and manual capture_exception()."""

import os
import shadow_debugger

# Load .env if present
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

shadow_debugger.init(
    provider="gemini",
    model="gemini-3.8-flash",
    output_dir="./shadow_reports",
    print_to_console=True,
)


@shadow_debugger.watch(reraise=False)
def process_user_order(order_payload: dict) -> float:
    return order_payload["items"]["price"] / order_payload["items"]["quantity"]


if __name__ == "__main__":
    sample_order = {
        "user": "Alice",
        "auth_token": "super_secret_token_12345",  # Automatically redacted
        "items": {"price": 100, "quantity": 0},  # Triggers ZeroDivisionError
    }
    process_user_order(sample_order)
    print("Main thread continued immediately. Waiting for background diagnosis...")
    shadow_debugger.flush(timeout=15.0)
