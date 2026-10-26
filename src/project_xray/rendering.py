#!/usr/bin/env python3
"""Render IDE-style code evidence cards from a project-xray signals.json."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception as exc:  # pragma: no cover
    print("error: Pillow is required to render evidence images. Install it with: python -m pip install pillow", file=sys.stderr)
    raise SystemExit(2) from exc

try:
    from pygments import lex
    from pygments.lexers import IniLexer, JavaLexer, PythonLexer, SqlLexer, TextLexer, XmlLexer, YamlLexer
    from pygments.token import Token
except Exception as exc:  # pragma: no cover
    print("error: Pygments is required to render evidence images. Install it with: python -m pip install pygments", file=sys.stderr)
    raise SystemExit(2) from exc


THEMES: Dict[str, Dict[str, str]] = {
    "dark": {
        "bg": "#1e1e1e", "panel": "#252526", "border": "#3c3c3c", "text": "#d4d4d4",
        "muted": "#858585", "highlight": "#264f78", "highlight_border": "#569cd6",
        "keyword": "#569cd6", "string": "#ce9178", "comment": "#6a9955", "number": "#b5cea8",
        "function": "#dcdcaa", "class": "#4ec9b0", "operator": "#d4d4d4",
        "badge": "#0e639c", "badge_text": "#ffffff",
    },
    "light": {
        "bg": "#ffffff", "panel": "#f3f3f3", "border": "#d4d4d4", "text": "#1f1f1f",
        "muted": "#6e6e6e", "highlight": "#fff3cd", "highlight_border": "#d6a700",
        "keyword": "#0000ff", "string": "#a31515", "comment": "#008000", "number": "#098658",
        "function": "#795e26", "class": "#267f99", "operator": "#1f1f1f",
        "badge": "#0b6fb3", "badge_text": "#ffffff",
    },
}


def font_candidates(bold: bool = False) -> List[str]:
    if bold:
        return [r"C:\Windows\Fonts\simsunb.ttf", r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\consolab.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"]
    return [r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\consola.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for candidate in font_candidates(bold):
        p = Path(candidate)
        if not p.exists():
            continue
        try:
            return ImageFont.truetype(str(p), size, index=0)
        except Exception:
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                continue
    return ImageFont.load_default()


def lexer_for(path: str):
    ext = Path(path).suffix.lower()
    if ext == ".java": return JavaLexer()
    if ext in {".xml", ".html", ".htm"}: return XmlLexer()
    if ext in {".yml", ".yaml"}: return YamlLexer()
    if ext in {".properties", ".ini", ".conf"}: return IniLexer()
    if ext == ".sql": return SqlLexer()
    if ext == ".py": return PythonLexer()
    return TextLexer()


def token_lines(code: str, path: str) -> List[List[Tuple[object, str]]]:
    result: List[List[Tuple[object, str]]] = [[]]
    for ttype, value in lex(code, lexer_for(path)):
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        parts = value.split("\n")
        for idx, part in enumerate(parts):
            if part:
                result[-1].append((ttype, part.replace("\t", "    ")))
            if idx < len(parts) - 1:
                result.append([])
    return result


def color_for(ttype, theme: Dict[str, str]) -> str:
    if ttype in Token.Comment: return theme["comment"]
    if ttype in Token.Literal.String: return theme["string"]
    if ttype in Token.Literal.Number: return theme["number"]
    if ttype in Token.Keyword: return theme["keyword"]
    if ttype in Token.Name.Function: return theme["function"]
    if ttype in Token.Name.Class: return theme["class"]
    if ttype in Token.Operator: return theme["operator"]
    return theme["text"]


def ellipsize(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def render_signal(signal: Dict[str, object], out_path: Path, theme_name: str, max_chars: int, scale: int) -> Dict[str, object]:
    theme = THEMES[theme_name]
    snippet = str(signal.get("snippet", "")).rstrip("\n")
    raw_lines = snippet.splitlines() or [""]
    raw_lines = [ellipsize(line.replace("\t", "    "), max_chars) for line in raw_lines]
    code = "\n".join(raw_lines)
    tokens_by_line = token_lines(code, str(signal.get("path", "")))
    while len(tokens_by_line) < len(raw_lines):
        tokens_by_line.append([])

    line_start = int(signal.get("line_start", 1))
    line_end = int(signal.get("line_end", line_start))
    snippet_start = max(1, line_start - 2)
    hi_start = max(0, line_start - snippet_start)
    hi_end = min(len(raw_lines) - 1, line_end - snippet_start)

    font_code = load_font(18 * scale)
    font_line = load_font(15 * scale)
    font_title = load_font(19 * scale, bold=True)
    font_small = load_font(13 * scale)
    font_badge = load_font(12 * scale, bold=True)
    line_height = 27 * scale
    margin = 22 * scale
    header_h = 76 * scale
    footer_h = 44 * scale
    code_width = max([font_code.getlength(line) for line in raw_lines] + [0])
    width = max(980 * scale, min(1700 * scale, int(code_width) + 180 * scale))
    height = header_h + len(raw_lines) * line_height + footer_h + 20 * scale
    img = Image.new("RGB", (width, height), theme["bg"])
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=14 * scale, outline=theme["border"], width=max(1, scale))
    draw.rounded_rectangle([0, 0, width, header_h], radius=14 * scale, fill=theme["panel"])

    filename = Path(str(signal.get("path", ""))).name
    title = f"{signal.get('id', 'E???')}  {filename}:{line_start}"
    if line_end != line_start: title += f"-{line_end}"
    draw.text((margin, 14 * scale), ellipsize(title, 110), font=font_title, fill=theme["text"])
    draw.text((margin, 46 * scale), ellipsize(str(signal.get("path", "")), 150), font=font_small, fill=theme["muted"])

    badge = str(signal.get("badge") or f"{signal.get('severity', '?')} | {signal.get('confidence', '?')}")
    badge_w = int(font_badge.getlength(badge)) + 22 * scale
    badge_h = 24 * scale
    bx = width - margin - badge_w
    by = 20 * scale
    draw.rounded_rectangle([bx, by, bx + badge_w, by + badge_h], radius=10 * scale, fill=theme["badge"])
    draw.text((bx + 11 * scale, by + 4 * scale), badge, font=font_badge, fill=theme["badge_text"])

    y = header_h + 10 * scale
    line_no_x = margin + 40 * scale
    code_x = margin + 64 * scale
    for idx, (raw_line, tokens) in enumerate(zip(raw_lines, tokens_by_line)):
        if hi_start <= idx <= hi_end:
            draw.rounded_rectangle([margin - 6 * scale, y - 2 * scale, width - margin + 6 * scale, y + line_height - 4 * scale], radius=4 * scale, fill=theme["highlight"])
            draw.rectangle([margin - 6 * scale, y - 2 * scale, margin - 1 * scale, y + line_height - 4 * scale], fill=theme["highlight_border"])
        actual_line = snippet_start + idx
        draw.text((line_no_x, y), str(actual_line), font=font_line, fill=theme["muted"], anchor="ra")
        x = code_x
        for ttype, value in tokens:
            draw.text((x, y), value, font=font_code, fill=color_for(ttype, theme))
            x += font_code.getlength(value)
        y += line_height

    footer_y = height - footer_h + 10 * scale
    rule_text = f"{signal.get('rule_id', '')}  {signal.get('title', '')}"
    draw.text((margin, footer_y), ellipsize(rule_text, 130), font=font_small, fill=theme["muted"])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    return {"id": signal.get("id"), "file": out_path.name, "path": signal.get("path"), "line_start": line_start, "line_end": line_end, "rule_id": signal.get("rule_id"), "title": signal.get("title"), "severity": signal.get("severity"), "confidence": signal.get("confidence")}


def main() -> int:
    parser = argparse.ArgumentParser(description="Render IDE-style code evidence cards from signals.json.")
    parser.add_argument("signals", help="signals.json produced by scan_repo.py")
    parser.add_argument("--out-dir", default="assets", help="Image output directory (default: assets)")
    parser.add_argument("--theme", choices=sorted(THEMES), default="dark", help="Color theme")
    parser.add_argument("--max-chars", type=int, default=160, help="Maximum characters per code line")
    parser.add_argument("--scale", type=int, default=1, choices=[1, 2, 3], help="Render scale")
    args = parser.parse_args()

    signals_path = Path(args.signals)
    if not signals_path.is_file():
        print(f"error: signals JSON not found: {signals_path}", file=sys.stderr)
        return 2
    try:
        data = json.loads(signals_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"error: cannot parse signals JSON: {exc}", file=sys.stderr)
        return 2
    signals = data.get("signals", [])
    if not isinstance(signals, list):
        print("error: signals must be a list", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        sid = str(signal.get("id", "unknown"))
        out_path = out_dir / f"{sid}.png"
        rendered.append(render_signal(signal, out_path, args.theme, args.max_chars, args.scale))
        print(f"rendered {out_path}")
    manifest = {"generated_at": datetime.now().astimezone().isoformat(), "theme": args.theme, "images": rendered}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"rendered {len(rendered)} images into {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
