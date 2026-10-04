"""Read-only DoorDash order-history ingestion with an explicit public-data allowlist.

Requires the authenticated DoorDash ``dd-cli``. No cart or checkout command is
exposed. Raw responses remain in memory; exported data contains meal names and
relative order sequence only. Review names before sharing an export.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


class DoorDashError(RuntimeError):
    """A safe error suitable for displaying without raw account data."""


DEFAULT_INTENT = (
    "Summary: Help the user compare models predicting food cravings from usual orders\n"
    'user prompt/purpose: "I want you to build a DoorDash benchmark for the models I have access to via the API, including every single one."'
)


def find_cli(executable: str | None = None) -> str:
    """Find the actual DoorDash executable; never use the unrelated Unix dd."""
    if executable:
        candidate = shutil.which(executable)
        if not candidate:
            raise DoorDashError("Specified DoorDash CLI executable was not found.")
        return candidate
    found = shutil.which("dd-cli")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "dd-cli"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    raise DoorDashError("DoorDash dd-cli is not installed or is not on PATH.")


def _payload(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise DoorDashError("DoorDash response must be a JSON object.")
    if response.get("isError"):
        raise DoorDashError("DoorDash returned an error; check CLI authentication.")
    result = response.get("structuredContent", response)
    if isinstance(result, dict) and "orders" in result:
        if result.get("success") is False:
            raise DoorDashError("DoorDash history request was unsuccessful.")
        return result
    # Some CLI versions expose JSON through the MCP text envelope only.
    for block in response.get("content", []):
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            try:
                embedded = json.loads(block["text"])
            except json.JSONDecodeError:
                continue
            if isinstance(embedded, dict) and "orders" in embedded:
                if embedded.get("success") is False:
                    raise DoorDashError("DoorDash history request was unsuccessful.")
                return embedded
    raise DoorDashError("DoorDash response did not contain order history.")


def _name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    # Remove newlines/control characters; names are data, never instructions.
    cleaned = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return cleaned[:300] or None


def sanitize_history(response: Any, max_orders: int = 10) -> dict[str, Any]:
    """Allowlist names and sequence, dropping every other account/order field."""
    if not 1 <= max_orders <= 100:
        raise DoorDashError("max_orders must be between 1 and 100.")
    raw_orders = _payload(response).get("orders")
    if not isinstance(raw_orders, list):
        raise DoorDashError("DoorDash orders field must be a list.")
    orders = []
    for raw in raw_orders[:max_orders]:
        if not isinstance(raw, dict):
            continue
        restaurant = _name(raw.get("store_name"))
        raw_items = raw.get("items", [])
        if not isinstance(raw_items, list):
            continue
        items = [_name(item.get("name")) for item in raw_items if isinstance(item, dict)]
        items = [item for item in items if item is not None]
        if restaurant and items:
            orders.append({"sequence": len(orders) + 1, "restaurant": restaurant, "items": items})
    return {"schema_version": 1, "source": "doordash_cli", "orders": orders}


def fetch_history(
    *, executable: str | None = None, max_orders: int = 10,
    days: int = 90, intent: str = DEFAULT_INTENT, timeout: float = 60,
) -> dict[str, Any]:
    """Invoke one verified read-only command using argv, without a shell."""
    if not 1 <= max_orders <= 100:
        raise DoorDashError("max_orders must be between 1 and 100.")
    if not 1 <= days <= 365:
        raise DoorDashError("days must be between 1 and 365.")
    argv = [find_cli(executable), "--json-output", "order", "history",
            "--max", str(max_orders), "--days", str(days), "--intent", intent]
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DoorDashError("DoorDash history command failed or timed out.") from exc
    if completed.returncode:
        # Do not echo subprocess stderr: it can contain authentication/account data.
        raise DoorDashError("DoorDash history command failed; check dd-cli login and connectivity.")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DoorDashError("DoorDash CLI returned invalid JSON.") from exc
    return sanitize_history(response, max_orders)


def write_fixture(data: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".scratch/doordash/orders.json"))
    parser.add_argument("--input-json", type=Path, help="Sanitize saved private CLI JSON instead of fetching")
    parser.add_argument("--cli", help="Explicit dd-cli executable path")
    parser.add_argument("--max-orders", type=int, default=10)
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()
    try:
        if args.input_json:
            data = sanitize_history(json.loads(args.input_json.read_text(encoding="utf-8")), args.max_orders)
        else:
            data = fetch_history(executable=args.cli, max_orders=args.max_orders, days=args.days)
        if not data["orders"]:
            raise DoorDashError("No usable orders found; increase --days or provide a manual fixture.")
        write_fixture(data, args.output)
    except (DoorDashError, OSError, json.JSONDecodeError) as exc:
        # File errors do not include file contents; API exceptions are sanitized above.
        parser.exit(1, f"Error: {exc}\n")
    print(f"Saved {len(data['orders'])} sanitized orders to {args.output}. Review meal names before sharing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
