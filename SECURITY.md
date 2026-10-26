# Security

Project Xray scans source repositories and may encounter secrets.

## Reporting

Please open a private security advisory or contact the maintainer instead of posting a working exploit publicly.

## Scan safety

The scanner is designed to:

- read files only
- not execute repository code
- not install repository dependencies
- not upload source code
- redact detected secret values in evidence snippets

## Known limits

Secret detection is heuristic. A clean scan does not prove that a repository contains no secrets. Always review generated reports before sharing them.
