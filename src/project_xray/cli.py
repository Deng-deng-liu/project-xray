from __future__ import annotations

import argparse
import sys

from . import __version__


def _run(module, argv):
    old_argv = sys.argv
    sys.argv = [f"project-xray {module.__name__.rsplit('.', 1)[-1]}"] + argv
    try:
        return int(module.main())
    finally:
        sys.argv = old_argv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project-xray",
        description="Evidence-linked project interview report generator for Java/Spring Boot.",
    )
    parser.add_argument("--version", action="version", version=f"project-xray {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Scan a repository and write signals.json")
    scan.add_argument("repo", help="Repository root")
    scan.add_argument("--out", default="signals.json", help="Output signals JSON")
    scan.add_argument("--max-files", type=int, default=8000)
    scan.add_argument("--max-signals", type=int, default=300)
    scan.add_argument("--include-tests", action="store_true")

    render = sub.add_parser("render", help="Render IDE-style code evidence cards")
    render.add_argument("signals", help="signals.json")
    render.add_argument("--out-dir", default="assets")
    render.add_argument("--theme", choices=["dark", "light"], default="dark")
    render.add_argument("--max-chars", type=int, default=160)
    render.add_argument("--scale", type=int, choices=[1, 2, 3], default=1)

    fix = sub.add_parser("fixes", help="Render after-fix code cards")
    fix.add_argument("signals", help="signals.json")
    fix.add_argument("fixes", help="fixes.json")
    fix.add_argument("--out-dir", default="assets")
    fix.add_argument("--theme", choices=["dark", "light"], default="dark")
    fix.add_argument("--max-chars", type=int, default=160)
    fix.add_argument("--scale", type=int, choices=[1, 2, 3], default=1)

    val = sub.add_parser("validate", help="Validate a Markdown report against signals.json")
    val.add_argument("report", help="Markdown report")
    val.add_argument("signals", help="signals.json")
    val.add_argument("--allow-missing-summary", action="store_true")

    sub.add_parser("doctor", help="Check local dependencies and version")
    return parser


def doctor() -> int:
    print(f"project-xray {__version__}")
    print(f"python: {sys.version.split()[0]}")
    ok = True
    try:
        import PIL
        print(f"pillow: {getattr(PIL, '__version__', 'ok')}")
    except Exception as exc:
        ok = False
        print(f"pillow: MISSING ({exc})")
    try:
        import pygments
        print(f"pygments: {getattr(pygments, '__version__', 'ok')}")
    except Exception as exc:
        ok = False
        print(f"pygments: MISSING ({exc})")
    print("status: OK" if ok else "status: MISSING DEPENDENCIES")
    return 0 if ok else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "doctor":
        return doctor()
    if args.command == "scan":
        from . import scanner
        argv = [args.repo, "--out", args.out, "--max-files", str(args.max_files), "--max-signals", str(args.max_signals)]
        if args.include_tests:
            argv.append("--include-tests")
        return _run(scanner, argv)
    if args.command == "render":
        from . import rendering
        return _run(rendering, [args.signals, "--out-dir", args.out_dir, "--theme", args.theme, "--max-chars", str(args.max_chars), "--scale", str(args.scale)])
    if args.command == "fixes":
        from . import fixes
        return _run(fixes, [args.signals, args.fixes, "--out-dir", args.out_dir, "--theme", args.theme, "--max-chars", str(args.max_chars), "--scale", str(args.scale)])
    if args.command == "validate":
        from . import validation
        argv = [args.report, args.signals]
        if args.allow_missing_summary:
            argv.append("--allow-missing-summary")
        return _run(validation, argv)
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
