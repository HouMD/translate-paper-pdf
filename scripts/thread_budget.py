#!/usr/bin/env python3
"""Estimate how close the current Codex thread is to the DeepSeek request-body limit.

Usage:
    python thread_budget.py [--cwd DIR | --file SESSION.jsonl] [--codex-home DIR]
                            [--max-images 25] [--max-mb 25] [--json]

Without --file, the newest rollout under <codex-home>/sessions whose session_meta
cwd (or workspace roots) matches --cwd is analyzed. Only the current compaction
window is counted, so the report reflects what Codex would resend next turn.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

IMAGE_MARK = '"input_image"'
COMPACTED_TYPES = {"compacted", "compaction"}
VIEW_TOOL = "view_image"


def normalize(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.replace("\\\\?\\", "")))


def resolve_codex_home(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env else Path.home() / ".codex"


def read_meta(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(5):
                line = handle.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "session_meta":
                    payload = obj.get("payload")
                    return payload if isinstance(payload, dict) else None
    except OSError:
        return None
    return None


def find_sessions(root: Path, cwd: str | None, limit: int = 5) -> list[Path]:
    if not root.is_dir():
        return []
    target = normalize(cwd) if cwd else None
    matches: list[tuple[float, Path]] = []
    for path in root.rglob("*.jsonl"):
        meta = read_meta(path)
        if not meta:
            continue
        if target:
            candidates = [meta.get("cwd")] + list(meta.get("runtime_workspace_roots") or [])
            if not any(isinstance(c, str) and normalize(c) == target for c in candidates):
                continue
        try:
            matches.append((path.stat().st_mtime, path))
        except OSError:
            continue
    matches.sort(reverse=True)
    return [path for _, path in matches[:limit]]


def measure_items(items: object) -> tuple[int, int, int]:
    """Return (image_count, image_bytes, json_bytes) for a list of response items."""
    if not isinstance(items, list):
        return 0, 0, 0
    images = 0
    image_bytes = 0
    stack: list[object] = list(items)
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if current.get("type") == "input_image":
                images += 1
                url = current.get("image_url")
                if isinstance(url, str) and url.startswith("data:"):
                    image_bytes += len(url)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    text = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return images, image_bytes, len(text.encode("utf-8"))


def analyze(path: Path) -> dict:
    window_bytes = 0
    images = 0
    image_bytes = 0
    view_calls = 0
    last_usage: dict | None = None
    context_window: int | None = None
    window_number: int | None = None
    started: str | None = None
    lines = 0

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            lines += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = obj.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            ptype = payload.get("type")

            if obj.get("type") == "session_meta" and started is None:
                started = payload.get("timestamp")

            if ptype in COMPACTED_TYPES:
                replacement = payload.get("replacement_history") or []
                images, image_bytes, window_bytes = measure_items(replacement)
                view_calls = sum(
                    1
                    for item in replacement
                    if isinstance(item, dict)
                    and item.get("type") == "function_call"
                    and item.get("name") == VIEW_TOOL
                )
                window_number = payload.get("window_number")
                continue

            window_bytes += len(line.encode("utf-8"))
            if IMAGE_MARK in line:
                found_images, found_bytes, _ = measure_items([payload])
                images += found_images
                image_bytes += found_bytes
            if ptype == "function_call" and payload.get("name") == VIEW_TOOL:
                view_calls += 1
            if ptype == "token_count" and isinstance(payload.get("info"), dict):
                info = payload["info"]
                last_usage = info.get("last_token_usage") or info.get("total_token_usage")
                context_window = info.get("model_context_window") or context_window

    input_tokens = (last_usage or {}).get("input_tokens")
    return {
        "session": str(path),
        "started": started,
        "lines": lines,
        "window_number": window_number,
        "view_image_calls": view_calls,
        "inline_images": images,
        "image_mb": round(image_bytes / 1048576, 2),
        "est_body_mb": round(window_bytes / 1048576, 2),
        "last_request_tokens": input_tokens,
        "context_window": context_window,
    }


def verdict(report: dict, max_images: int, max_mb: float) -> tuple[str, str]:
    image_ratio = report["view_image_calls"] / max_images if max_images else 0.0
    body_ratio = report["est_body_mb"] / max_mb if max_mb else 0.0
    worst = max(image_ratio, body_ratio)
    if worst >= 1.0:
        return (
            "red",
            "预算已用完：写检查点并开新线程；继续看图大概率报 413 或 No tool output。",
        )
    if worst >= 0.6:
        return (
            "yellow",
            "接近预算：完成当前小单元后写检查点，准备轮换线程。",
        )
    return ("green", "继续，但只用预览图，且每完成一个小单元就更新检查点。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--cwd", default=None, help="match sessions by workspace cwd (default: current dir)")
    parser.add_argument("--file", default=None, help="analyze one rollout jsonl directly")
    parser.add_argument("--codex-home", default=None, help="CODEX_HOME override")
    parser.add_argument("--max-images", type=int, default=25, help="view_image budget per thread (default 25)")
    parser.add_argument("--max-mb", type=float, default=25.0, help="estimated body budget in MB (default 25)")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    if args.file:
        path = Path(args.file)
        if not path.is_file():
            print(f"session file not found: {path}", file=sys.stderr)
            return 1
    else:
        target = args.cwd or os.getcwd()
        home = resolve_codex_home(args.codex_home)
        matches = find_sessions(home / "sessions", target)
        if not matches:
            print(f"no session found for cwd: {target} (under {home / 'sessions'})", file=sys.stderr)
            return 1
        path = matches[0]
        if len(matches) > 1 and not args.json:
            print(f"note: {len(matches)} sessions match cwd; analyzing newest: {path.name}", file=sys.stderr)

    report = analyze(path)
    level, advice = verdict(report, args.max_images, args.max_mb)
    report["verdict"] = level
    report["advice"] = advice
    report["budgets"] = {"max_images": args.max_images, "max_mb": args.max_mb}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    tokens = report["last_request_tokens"]
    window = report["context_window"]
    token_text = (
        f"{tokens} / {window}" if tokens is not None and window else (str(tokens) if tokens else "unknown")
    )
    print(f"session      : {report['session']}")
    print(f"started      : {report['started']}  (compaction window: {report['window_number']})")
    print(f"view_image   : {report['view_image_calls']} calls / budget {args.max_images}")
    print(f"inline images: {report['inline_images']} ({report['image_mb']} MB)")
    print(f"est. body    : {report['est_body_mb']} MB / budget {args.max_mb} MB (estimate)")
    print(f"last request : {token_text} input tokens")
    print(f"verdict      : {level.upper()} - {advice}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
