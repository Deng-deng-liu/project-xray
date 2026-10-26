# Contributing

Thanks for trying Project Xray.

## Development setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

## What to contribute

- A new deterministic scan rule with a clear false-positive boundary
- A test fixture that demonstrates the rule and its counterexample
- Better beginner-facing answer guidance
- Documentation and example projects
- Bug reports with the exact command, OS, Python version, and sanitized output

## Rule design

Every rule should include:

- a stable `rule_id`
- severity and confidence
- a narrow `observed_fact`
- a separate `inferred_risk`
- a `verify_hint`
- a positive test fixture
- a note about likely false positives

When in doubt, prefer `likely` or `uncertain` over `verified`.

## Pull requests

- Keep changes focused.
- Do not add network calls or execute scanned repositories.
- Do not include real credentials in fixtures.
- Run the full test suite before opening a PR.
