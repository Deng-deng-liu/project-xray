#!/usr/bin/env python3
"""Validate a project-xray Markdown report against its evidence packet."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set


def load_signals(path: Path) -> Dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "signals" not in data:
        raise ValueError("signals JSON must contain a top-level 'signals' list")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an evidence-linked Markdown report.")
    parser.add_argument("report", help="Markdown report path")
    parser.add_argument("signals", help="signals JSON produced by scan_repo.py")
    parser.add_argument("--allow-missing-summary", action="store_true", help="Do not require the fixed summary line")
    args = parser.parse_args()

    report_path = Path(args.report)
    signals_path = Path(args.signals)
    if not report_path.is_file():
        print(f"error: report not found: {report_path}", file=sys.stderr)
        return 2
    if not signals_path.is_file():
        print(f"error: signals JSON not found: {signals_path}", file=sys.stderr)
        return 2

    try:
        data = load_signals(signals_path)
    except Exception as exc:
        print(f"error: cannot load signals JSON: {exc}", file=sys.stderr)
        return 2

    text = report_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    signals = data.get("signals", [])
    if not isinstance(signals, list):
        print("error: signals must be a list", file=sys.stderr)
        return 2

    evidence: Dict[str, Dict[str, object]] = {}
    for item in signals:
        if isinstance(item, dict) and item.get("id"):
            evidence[str(item["id"])] = item

    errors: List[str] = []
    warnings: List[str] = []

    # 1. Evidence IDs must exist.
    refs = re.findall(r"\[(E\d{3,})\]", text)
    for ref in sorted(set(refs)):
        if ref not in evidence:
            errors.append(f"unknown evidence reference [{ref}]")
    if not refs:
        errors.append("report contains no evidence references")
    image_refs = re.findall(r"!\[[^\]]*\]\(([^)]+\.(?:png|jpg|jpeg|svg))\)", text, re.I)
    if not image_refs:
        errors.append("report contains no code evidence images")
    for image_ref in image_refs:
        if image_ref.startswith(("http://", "https://", "data:")):
            continue
        if not (report_path.parent / image_ref).is_file():
            errors.append(f"image file not found: {image_ref}")

    # 2. Finding/question sections need at least one evidence reference and image.
    heading_idx = [i for i, line in enumerate(lines) if re.match(r"^#{2,4}\s+", line)]
    heading_idx.append(len(lines))
    for pos in range(len(heading_idx) - 1):
        start, end = heading_idx[pos], heading_idx[pos + 1]
        heading = lines[start].strip()
        block = "\n".join(lines[start:end])
        if re.search(r"\b(?:Q|F)\d+\b", heading, re.I) or "现象：" in block:
            if not re.search(r"\[E\d{3,}\]", block):
                errors.append(f"section lacks evidence: {heading}")
            block_ids = set(re.findall(r"\[(E\d{3,})\]", block))
            block_images = set(re.findall(r"(E\d{3,})(?:-after)?\.(?:png|jpg|jpeg|svg)", block, re.I))
            for evidence_id in sorted(block_ids):
                if evidence_id not in block_images:
                    errors.append(f"section missing image for [{evidence_id}]: {heading}")
            if re.search(r"\bQ\d+\b", heading, re.I):
                tier_match = re.search(r"\[(重点|普通|延伸)\]", heading)
                if not tier_match:
                    errors.append(f"question has no tier marker [重点]/[普通]/[延伸]: {heading}")
                else:
                    tier = tier_match.group(1)
                    required = ["30 秒口述版", "完整解释"]
                    if tier in {"重点", "普通"}:
                        for field in required:
                            if field not in block:
                                errors.append(f"{tier} question missing {field}: {heading}")
                        followups = len(re.findall(r"追问\s*\d+", block))
                        if tier == "重点" and followups < 2:
                            errors.append(f"重点 question needs at least 2 follow-ups: {heading}")
                        if tier == "普通" and followups < 1:
                            errors.append(f"普通 question needs at least 1 follow-up: {heading}")
                        if "你需要补的基础" not in block:
                            errors.append(f"{tier} question missing 你需要补的基础: {heading}")
                    if tier == "重点" and "自测" not in block:
                        errors.append(f"重点 question missing 自测: {heading}")

    # 3. Fixed summary line and high-risk count.
    summary_re = re.compile(r"本次共发现\s*(\d+)\s*个可讲点[，,]\s*(\d+)\s*个高危坑")
    m = summary_re.search(text)
    verified_high = sum(
        1 for s in evidence.values()
        if s.get("severity") == "high" and s.get("confidence") == "verified"
    )
    if not m:
        if not args.allow_missing_summary:
            errors.append("missing fixed summary line: 本次共发现 X 个可讲点，Y 个高危坑，建议优先准备第 N 题")
    else:
        if int(m.group(2)) != verified_high:
            errors.append(
                f"summary says {m.group(2)} high-risk pitfalls, evidence packet has {verified_high} verified high-severity findings"
            )

    # 4. Reject unsupported metric claims. Numbers must appear in the evidence packet
    # or be explicitly marked as assumptions / to-be-verified.
    metric_words = re.compile(r"(?i)(qps|tps|ms\b|毫秒|秒|万|亿|用户|并发|请求数|次/秒|响应时间)")
    metric_with_unit = re.compile(r"(?i)(?<!\w)(\d[\d,.]*)\s*(?:qps|tps|ms|毫秒|秒|万|亿|用户|并发|请求|次/秒|响应时间)")
    metric_prefixed = re.compile(r"(?i)(?:qps|tps|并发|响应时间)\D{0,8}(\d[\d,.]*)")
    packet_text = json.dumps({"project": data.get("project"), "signals": data.get("signals")}, ensure_ascii=False)
    for i, line in enumerate(lines, start=1):
        if not metric_words.search(line):
            continue
        if any(x in line for x in ("假设", "待核实", "未提供", "无法确认", "示例")):
            continue
        scan_line = re.sub(r"^\s*\d+[.)]\s*", "", line)
        numbers = [m.group(1).strip() for m in metric_with_unit.finditer(scan_line)]
        numbers += [m.group(1).strip() for m in metric_prefixed.finditer(scan_line)]
        for number in numbers:
            if number and number not in packet_text:
                errors.append(f"line {i}: unsupported metric claim '{number}' - add evidence or mark as 假设/待核实")

    # 5. Warning for historical claims that the scanner cannot prove.
    historical = re.compile(r"(当时|曾经|线上发生|故障次数|用户反馈)", re.I)
    for i, line in enumerate(lines, start=1):
        if historical.search(line) and not any(x in line for x in ("可能", "待核实", "假设", "需本人确认")):
            warnings.append(f"line {i}: historical claim may need user confirmation")

    # 6. Obvious unredacted secret literals in the report.
    secret_like = re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)\b"
        r"\s*[:=]\s*[\"']?([^\s\"'，。;]{6,})"
    )
    for i, line in enumerate(lines, start=1):
        sm = secret_like.search(line)
        if sm and "REDACTED" not in sm.group(1).upper() and not any(x in sm.group(1).lower() for x in ("${", "#{", "changeme", "example")):
            errors.append(f"line {i}: possible unredacted secret literal")

    # 7. Placeholder or unfinished report markers.
    for i, line in enumerate(lines, start=1):
        if "[TODO" in line or "[待填写]" in line:
            errors.append(f"line {i}: unfinished placeholder remains")

    print(f"report: {report_path}")
    print(f"signals: {signals_path}")
    print(f"evidence ids: {len(evidence)}; referenced ids: {len(set(refs))}; verified high-risk: {verified_high}")
    if warnings:
        for w in warnings:
            print(f"warning: {w}")
    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        print(f"FAILED: {len(errors)} error(s)", file=sys.stderr)
        return 1
    print("OK: report passed evidence validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
