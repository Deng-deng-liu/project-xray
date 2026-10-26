---
name: False positive
about: A scan finding is wrong or misleading
title: "[False positive] "
labels: false-positive
assignees: ""
---

## Rule ID

<!-- e.g. JAVA-DB-CALL-IN-LOOP -->

## Project type

- Java version:
- Spring Boot version:
- MyBatis / JPA / JDBC:
- OS:

## Evidence

<!-- Paste sanitized signals.json / report excerpt. Remove secrets. -->

## Why it is a false positive

## Suggested rule boundary

## Reproduction command

```bash
project-xray scan <repo> --out signals.json
project-xray render signals.json --out-dir assets
```
