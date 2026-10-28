"""
TMS TCP client and record parsing.

The only place that talks to the TMS over its pipe-delimited TCP
protocol. MAX_BUY is an internal ceiling: it is stripped on every
path that hands a record to an adapter and never leaves the server.
"""

import socket
from typing import Optional

from app.core import config
from app.core.text import clean


def parse_tms_record(line: str) -> dict:
    """
    LOAD_ID:LD0001|ORIG_CITY:Atlanta|...
      -> {"load_id": "LD0001", "orig_city": "Atlanta", ...}
    """
    record = {}
    for part in line.split("|"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        record[key.strip().lower()] = value.strip()
    return record


def tms_request(command: str, fields: Optional[dict] = None) -> list[dict]:
    fields = fields or {}

    if not config.TMS_AUTH:
        raise RuntimeError("TMS_AUTH environment variable is not configured")

    # CMD must be first.
    parts = [f"CMD:{command}", f"AUTH:{config.TMS_AUTH}"]

    for key, value in fields.items():
        if value is None:
            continue
        # Protocol does not permit these characters.
        value = str(value).replace("|", "").replace("\r", "").replace("\n", "")
        parts.append(f"{key}:{value}")

    message = "|".join(parts) + "\r\n"

    # Avoid logging request fields, which may contain carrier or rate data.
    print("[TMS REQUEST]", command)

    response_lines = []

    try:
        with socket.create_connection((config.TMS_HOST, config.TMS_PORT), timeout=config.TMS_TIMEOUT) as sock:
            sock.sendall(message.encode("ascii", errors="ignore"))

            buffer = b""
            done = False

            while not done:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk

                while b"\r\n" in buffer:
                    raw_line, buffer = buffer.split(b"\r\n", 1)
                    line = raw_line.decode("ascii", errors="ignore").strip()
                    if not line:
                        continue
                    response_lines.append(line)
                    if line == "END":
                        done = True
                        break

    except socket.timeout:
        raise RuntimeError("TMS connection timed out")
    except OSError as exc:
        raise RuntimeError(f"TMS connection failed: {exc}")

    # LOAD_QUERY can legitimately return nothing.
    if not response_lines:
        if command == "LOAD_QUERY":
            print("[TMS RESPONSE] No matching loads")
            return []
        raise RuntimeError("TMS returned an empty response")

    if response_lines[0].startswith("ERR|"):
        error = parse_tms_record(response_lines[0])
        raise RuntimeError(
            f"TMS {error.get('code', 'UNKNOWN')}: {error.get('msg', 'TMS error')}"
        )

    records = []
    for line in response_lines:
        if line == "END":
            break
        records.append(parse_tms_record(line))

    return records


def tms_get_load_raw(load_id: str) -> Optional[dict]:
    """Full LOAD_GET record, including internal fields. Internal use only."""
    load_id = clean(load_id)
    if not load_id:
        return None
    try:
        records = tms_request("LOAD_GET", {"LOAD_ID": load_id})
    except Exception as exc:
        print(f"[TMS LOAD_GET ERROR] {exc}")
        return None
    return records[0] if records else None


def tms_get_load(load_id: str) -> Optional[dict]:
    """
    Load record safe to build agent-facing output from.
    MAX_BUY is removed and is NEVER returned to the agent.
    """
    record = tms_get_load_raw(load_id)
    if record is None:
        return None
    record = dict(record)
    record.pop("max_buy", None)
    return record


def strip_max_buy(records: list[dict]) -> list[dict]:
    """
    Remove MAX_BUY from records in place, so it never leaves the
    server — not even from the dev-only raw passthrough.
    """
    for record in records:
        record.pop("max_buy", None)
    return records
