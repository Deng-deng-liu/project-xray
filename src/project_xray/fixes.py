#!/usr/bin/env python3
"""Render suggested-fix code cards from fixes.json."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .rendering import render_signal


def main() -> int:
    parser = argparse.ArgumentParser(description="Render after-fix code cards from fixes.json.")
    parser.add_argument("signals", help="signals.json produced by scan_repo.py")
    parser.add_argument("fixes", help="fixes.json containing suggested after snippets")
    parser.add_argument("--out-dir", default="assets", help="Image output directory (default: assets)")
    parser.add_argument("--theme", choices=["dark", "light"], default="dark", help="Color theme")
    parser.add_argument("--max-chars", type=int, default=160, help="Maximum characters per code line")
    parser.add_argument("--scale", type=int, default=1, choices=[1, 2, 3], help="Render scale")
    args = parser.parse_args()

    signals_path = Path(args.signals)
    fixes_path = Path(args.fixes)
    if not signals_path.is_file():
        print(f"error: signals JSON not found: {signals_path}", file=sys.stderr)
        return 2
    if not fixes_path.is_file():
        print(f"error: fixes JSON not found: {fixes_path}", file=sys.stderr)
        return 2
    try:
        signals_data = json.loads(signals_path.read_text(encoding="utf-8"))
        fixes_data = json.loads(fixes_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"error: cannot parse JSON: {exc}", file=sys.stderr)
        return 2

    signal_by_id = {
        str(item.get("id")): item
        for item in signals_data.get("signals", [])
        if isinstance(item, dict) and item.get("id")
    }
    fixes = fixes_data.get("fixes", [])
    if not isinstance(fixes, list):
        print("error: fixes must be a list", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    for fix in fixes:
        if not isinstance(fix, dict):
            continue
        evidence_id = str(fix.get("evidence_id", ""))
        base = signal_by_id.get(evidence_id)
        if base is None:
            print(f"warning: evidence {evidence_id} not found; skipping", file=sys.stderr)
            continue
        after = str(fix.get("after_snippet", "")).rstrip("\n")
        if not after.strip():
            print(f"warning: evidence {evidence_id} has no after_snippet; skipping", file=sys.stderr)
            continue
        after_lines = after.splitlines() or [""]
        line_start = int(fix.get("line_start", 1))
        line_end = int(fix.get("line_end", line_start + len(after_lines) - 1))
        pseudo = {
            "id": f"{evidence_id}-after",
            "rule_id": base.get("rule_id", ""),
            "title": fix.get("title") or f"{evidence_id} 建议修改",
            "path": fix.get("path") or base.get("path", ""),
            "line_start": line_start,
            "line_end": line_end,
            "snippet": after,
            "severity": fix.get("severity", "suggested"),
            "confidence": fix.get("confidence", "suggested"),
            "badge": fix.get("badge", "after fix"),
        }
        out_path = out_dir / f"{evidence_id}-after.png"
        rendered.append(render_signal(pseudo, out_path, args.theme, args.max_chars, args.scale))
        print(f"rendered {out_path}")

    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "theme": args.theme,
        "images": rendered,
    }
    (out_dir / "fix-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"rendered {len(rendered)} fix images into {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
