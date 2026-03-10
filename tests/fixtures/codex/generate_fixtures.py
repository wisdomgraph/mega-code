"""Generate variant fixtures from the golden Codex session."""

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent
GOLDEN = FIXTURES_DIR / "golden_session.jsonl"


def load_golden() -> list[dict]:
    entries = []
    with open(GOLDEN) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def write_jsonl(path: Path, entries: list[dict]) -> None:
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def generate() -> None:
    golden = load_golden()

    # no_session_meta.jsonl — golden minus session_meta line
    write_jsonl(
        FIXTURES_DIR / "no_session_meta.jsonl",
        [e for e in golden if e.get("type") != "session_meta"],
    )

    # no_payload_in_turn_context.jsonl — turn_context with payload removed
    entries = []
    for e in golden:
        if e.get("type") == "turn_context":
            e2 = dict(e)
            del e2["payload"]
            entries.append(e2)
        else:
            entries.append(e)
    write_jsonl(FIXTURES_DIR / "no_payload_in_turn_context.jsonl", entries)

    # no_call_id.jsonl — 2 function_call/output entries with call_id removed
    entries = []
    removed = 0
    for e in golden:
        if e.get("type") == "response_item" and e["payload"].get("type") in (
            "function_call",
            "function_call_output",
        ):
            if removed < 2:
                e2 = {"type": e["type"], "timestamp": e.get("timestamp", ""), "payload": dict(e["payload"])}
                e2["payload"].pop("call_id", None)
                entries.append(e2)
                removed += 1
                continue
        entries.append(e)
    write_jsonl(FIXTURES_DIR / "no_call_id.jsonl", entries)

    # no_payload_in_session_meta.jsonl — session_meta with payload removed
    entries = []
    for e in golden:
        if e.get("type") == "session_meta":
            e2 = dict(e)
            del e2["payload"]
            entries.append(e2)
        else:
            entries.append(e)
    write_jsonl(FIXTURES_DIR / "no_payload_in_session_meta.jsonl", entries)

    # no_timestamps.jsonl — all timestamp fields removed
    entries = []
    for e in golden:
        e2 = dict(e)
        e2.pop("timestamp", None)
        if "payload" in e2 and isinstance(e2["payload"], dict):
            e2["payload"] = dict(e2["payload"])
            e2["payload"].pop("timestamp", None)
        entries.append(e2)
    write_jsonl(FIXTURES_DIR / "no_timestamps.jsonl", entries)

    # missing_token_counts.jsonl — 2 of 4 token_count entries removed
    token_count_idx = 0
    entries = []
    for e in golden:
        if (
            e.get("type") == "event_msg"
            and isinstance(e.get("payload"), dict)
            and e["payload"].get("type") == "token_count"
        ):
            if token_count_idx in (1, 3):
                token_count_idx += 1
                continue
            token_count_idx += 1
        entries.append(e)
    write_jsonl(FIXTURES_DIR / "missing_token_counts.jsonl", entries)

    # orphaned_tool_output.jsonl — extra function_call_output with unmatched call_id
    entries = list(golden)
    entries.append(
        {
            "type": "response_item",
            "timestamp": "2026-03-10T10:01:00Z",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-orphan-999",
                "output": "orphaned output",
            },
        }
    )
    write_jsonl(FIXTURES_DIR / "orphaned_tool_output.jsonl", entries)

    # tool_call_no_output.jsonl — one function_call_output removed (call-001)
    entries = []
    for e in golden:
        if (
            e.get("type") == "response_item"
            and isinstance(e.get("payload"), dict)
            and e["payload"].get("type") == "function_call_output"
            and e["payload"].get("call_id") == "call-001"
        ):
            continue
        entries.append(e)
    write_jsonl(FIXTURES_DIR / "tool_call_no_output.jsonl", entries)

    # truncated_last_line.jsonl — last line cut at byte 20
    lines = []
    with open(GOLDEN) as f:
        lines = f.readlines()
    with open(FIXTURES_DIR / "truncated_last_line.jsonl", "w") as f:
        for line in lines[:-1]:
            f.write(line)
        f.write(lines[-1][:20])

    # empty_file.jsonl — zero bytes
    (FIXTURES_DIR / "empty_file.jsonl").write_text("")

    # multi_project.jsonl — cwd changed to different project
    entries = []
    for e in golden:
        if e.get("type") == "session_meta":
            e2 = json.loads(json.dumps(e))
            e2["payload"]["cwd"] = "/home/user/projects/other-project"
            e2["payload"]["id"] = "fixture-session-002"
            entries.append(e2)
        else:
            entries.append(e)
    write_jsonl(FIXTURES_DIR / "multi_project.jsonl", entries)

    print("Generated all variant fixtures.")


if __name__ == "__main__":
    generate()
