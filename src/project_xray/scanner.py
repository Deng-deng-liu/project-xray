#!/usr/bin/env python3
"""Deterministic evidence scanner for Java/Spring Boot repositories.

The scanner is intentionally conservative. It extracts code-grounded signals and
leaves interpretation to the agent. It never executes repository code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.0"

IGNORE_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", "node_modules", "target",
    "build", "dist", "out", "bin", "obj", "coverage", ".gradle", ".mvn",
    "vendor", "generated", "generated-sources", ".venv", "venv",
}
TEXT_EXTS = {
    ".java", ".kt", ".kts", ".xml", ".yml", ".yaml", ".properties",
    ".sql", ".gradle", ".json", ".toml", ".ini", ".conf", ".md", ".txt",
}
CODE_EXTS = {".java", ".kt", ".xml", ".yml", ".yaml", ".properties", ".sql", ".gradle", ".kts"}
JAVA_EXTS = {".java"}
MAX_FILE_BYTES = 2_000_000

SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}
CONFIDENCE_RANK = {"verified": 3, "likely": 2, "uncertain": 1}


def rel_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_test_path(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    s = path.as_posix().lower()
    return "src/test" in s or "src\\test" in s or "test" in parts or "tests" in parts


def line_no(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def snippet_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def find_matching_brace(text: str, open_idx: int) -> int:
    """Return index just after the matching closing brace, or len(text)."""
    if open_idx < 0 or open_idx >= len(text) or text[open_idx] != "{":
        return len(text)
    depth = 0
    i = open_idx
    state = "code"
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if ch == '"':
                state = "string"
                i += 1
                continue
            if ch == "'":
                state = "char"
                i += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
        elif state == "line_comment":
            if ch == "\n":
                state = "code"
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                continue
        elif state == "string":
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                state = "code"
        elif state == "char":
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                state = "code"
        i += 1
    return len(text)


def make_snippet(text: str, start: int, end: int, context: int = 2, redactions: Sequence[Tuple[str, str]] = ()) -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    s = max(1, line_no(text, start) - context)
    e = min(len(lines), line_no(text, max(start, end - 1)) + context)
    out = "\n".join(lines[s - 1:e])
    for raw, replacement in redactions:
        if raw:
            out = out.replace(raw, replacement)
    return out


def read_text(path: Path) -> Optional[str]:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
        if b"\x00" in data[:4096]:
            return None
        return data.decode("utf-8", errors="replace")
    except OSError:
        return None


def run_git(root: Path, args: Sequence[str]) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=4, check=False,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()
    except Exception:
        return None


def collect_files(root: Path, max_files: int) -> Tuple[Dict[str, str], Dict[str, int], List[str]]:
    files: Dict[str, str] = {}
    stats = {"files_total": 0, "files_scanned": 0, "files_skipped": 0}
    warnings: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            stats["files_total"] += 1
            if len(files) >= max_files:
                warnings.append(f"Stopped reading after {max_files} text files.")
                return files, stats, warnings
            if p.suffix.lower() not in TEXT_EXTS:
                stats["files_skipped"] += 1
                continue
            text = read_text(p)
            if text is None:
                stats["files_skipped"] += 1
                continue
            files[rel_posix(p, root)] = text
            stats["files_scanned"] += 1
    return files, stats, warnings


def parse_pom_artifact(files: Dict[str, str]) -> Optional[str]:
    pom = files.get("pom.xml")
    if not pom:
        return None
    # The first artifactId is usually the parent; remove <parent> before lookup.
    without_parent = re.sub(r"(?is)<parent\b.*?</parent>", "", pom)
    m = re.search(r"<artifactId>\s*([^<]+?)\s*</artifactId>", without_parent)
    return m.group(1).strip() if m else None


def detect_tech_stack(files: Dict[str, str]) -> List[str]:
    source_blob = "\n".join(
        text for path, text in files.items()
        if Path(path).suffix.lower() in CODE_EXTS and not is_test_path(Path(path))
    )
    blob = source_blob.lower()
    checks = [
        ("spring-boot-starter-web", "Spring Boot Web"),
        ("spring-boot", "Spring Boot"),
        ("spring-webflux", "Spring WebFlux"),
        ("spring-cloud", "Spring Cloud"),
        ("spring-security", "Spring Security"),
        ("mybatis-plus", "MyBatis-Plus"),
        ("mybatis", "MyBatis"),
        ("spring-data-jpa", "Spring Data JPA"),
        ("hibernate", "Hibernate"),
        ("mysql-connector", "MySQL"),
        ("mariadb", "MariaDB"),
        ("postgresql", "PostgreSQL"),
        ("redis", "Redis"),
        ("spring-kafka", "Kafka"),
        ("kafka", "Kafka"),
        ("rabbitmq", "RabbitMQ"),
        ("rocketmq", "RocketMQ"),
        ("elasticsearch", "Elasticsearch"),
        ("mongodb", "MongoDB"),
        ("junit", "JUnit"),
    ]
    found: List[str] = []
    for needle, label in checks:
        if needle in blob and label not in found:
            found.append(label)
    if "pom.xml" in files:
        found.insert(0, "Maven")
    elif any(p.endswith(("build.gradle", "build.gradle.kts")) for p in files):
        found.insert(0, "Gradle")
    return found


def build_profile(files: Dict[str, str], root: Path, git_meta: Dict[str, object]) -> Dict[str, object]:
    java_all = [p for p in files if Path(p).suffix.lower() in JAVA_EXTS]
    java_main = [p for p in java_all if not is_test_path(Path(p))]
    sql_files = [p for p in files if Path(p).suffix.lower() == ".sql"]
    test_files = [p for p in java_all if is_test_path(Path(p))]
    controllers = 0
    endpoints = 0
    services = 0
    mappers = 0
    repositories = 0
    entities = 0
    scheduled = 0
    for p in java_main:
        t = files[p]
        controllers += 1 if re.search(r"@(?:RestController|Controller)\b", t) else 0
        endpoints += len(re.findall(r"@(?:GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\b", t))
        services += 1 if re.search(r"@Service\b", t) else 0
        mappers += 1 if re.search(r"@Mapper\b|extends\s+BaseMapper\b", t) else 0
        repositories += 1 if re.search(r"@Repository\b|extends\s+(?:JpaRepository|CrudRepository|PagingAndSortingRepository)\b", t) else 0
        entities += 1 if re.search(r"@Entity\b|@TableName\b", t) else 0
        scheduled += len(re.findall(r"@Scheduled\b", t))
    loc = 0
    test_loc = 0
    for p in java_main:
        loc += files[p].count("\n") + (1 if files[p] else 0)
    for p in test_files:
        test_loc += files[p].count("\n") + (1 if files[p] else 0)
    module_counts: Dict[str, int] = {}
    for p in java_main:
        parts = Path(p).parts
        idx = -1
        for i, part in enumerate(parts):
            if part == "java":
                idx = i
                break
        if idx >= 0 and idx + 2 < len(parts):
            key = "/".join(parts[idx + 1: idx + 3])
            module_counts[key] = module_counts.get(key, 0) + 1
    modules = [
        {"name": name, "java_files": count}
        for name, count in sorted(module_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
    ]
    tech = detect_tech_stack(files)
    project_name = parse_pom_artifact(files) or root.name
    summary_bits = []
    if tech:
        summary_bits.append(" + ".join(tech[:5]))
    else:
        summary_bits.append("未识别到明确框架")
    summary_bits.append(f"Java 主代码 {len(java_main)} 个文件，约 {loc} 行")
    summary_bits.append(f"{controllers} 个控制器、{endpoints} 个接口映射、{scheduled} 个定时任务")
    summary = "；".join(summary_bits) + "。"
    return {
        "name": project_name,
        "root": str(root),
        "git": git_meta,
        "tech_stack": tech,
        "build_system": "Maven" if "pom.xml" in files else ("Gradle" if any(p.endswith("build.gradle") for p in files) else "unknown"),
        "summary": summary,
        "counts": {
            "java_files_main": len(java_main),
            "java_files_test": len(test_files),
            "java_loc_main": loc,
            "java_loc_test": test_loc,
            "java_loc_total": loc + test_loc,
            "controllers": controllers,
            "endpoints": endpoints,
            "services": services,
            "mappers": mappers,
            "repositories": repositories,
            "entities": entities,
            "scheduled_tasks": scheduled,
            "sql_files": len(sql_files),
        },
        "modules": modules,
    }


class Scanner:
    def __init__(self, files: Dict[str, str], root: Path, include_tests: bool = False):
        self.files = files
        self.root = root
        self.include_tests = include_tests
        self.signals: List[Dict[str, object]] = []
        self.seen = set()
        self.redactions_by_path: Dict[str, List[Tuple[str, str]]] = {}
        self.global_redactions: List[Tuple[str, str]] = []

    def add(self, rule_id: str, path: str, start: int, end: int, text: str,
            title: str, severity: str, confidence: str, observed: str,
            risk: str, verify: str, tags: Sequence[str], redactions: Sequence[Tuple[str, str]] = ()):
        if is_test_path(Path(path)) and not self.include_tests:
            return
        line_start = line_no(text, start)
        line_end = line_no(text, max(start, end - 1))
        all_redactions = list(redactions) + self.redactions_by_path.get(path, []) + self.global_redactions
        snippet = make_snippet(text, start, end, context=2, redactions=all_redactions)
        key = (rule_id, path, line_start, line_end, observed)
        if key in self.seen:
            return
        self.seen.add(key)
        self.signals.append({
            "rule_id": rule_id,
            "title": title,
            "path": path,
            "line_start": line_start,
            "line_end": line_end,
            "snippet": snippet,
            "snippet_hash": snippet_hash(snippet),
            "severity": severity,
            "confidence": confidence,
            "observed_fact": observed,
            "inferred_risk": risk,
            "verify_hint": verify,
            "tags": list(tags),
            "_start": start,
            "_end": end,
        })

    def iter_java(self) -> Iterable[Tuple[str, str]]:
        for p, t in self.files.items():
            if Path(p).suffix.lower() in JAVA_EXTS:
                yield p, t

    def scan_todo(self):
        pat = re.compile(r"(?im)\b(TODO|FIXME|HACK|XXX)\b[^\n]*")
        for p, t in self.files.items():
            if Path(p).suffix.lower() not in CODE_EXTS:
                continue
            for m in pat.finditer(t):
                marker = m.group(1).upper()
                line = m.group(0).strip()
                self.add(
                    "SRC-TODO", p, m.start(), m.end(), t,
                    f"{marker} 标记", "low", "verified",
                    f"源码中出现 {marker} 标记：{line}",
                    "可能代表未完成逻辑、临时方案或待处理风险。",
                    "确认标记是否仍然有效，以及是否已有后续提交或替代方案。",
                    ["maintenance", "todo"],
                )

    def scan_catch(self):
        pat = re.compile(r"\bcatch\s*\([^)]*\)\s*\{")
        log_only_pat = re.compile(
            r"\s*(?:(?:log(?:ger)?|LOG|LOGGER)\.\w+\s*\([^;]*\)\s*;\s*|"
            r"System\.(?:out|err)\.print\w*\s*\([^;]*\)\s*;\s*|"
            r"(?:e|ex|exception)\.printStackTrace\s*\(\s*\)\s*;\s*)*",
            re.I,
        )
        for p, t in self.iter_java():
            for m in pat.finditer(t):
                brace = t.find("{", m.start(), m.end())
                if brace < 0:
                    continue
                end = find_matching_brace(t, brace)
                body = t[brace + 1:end - 1]
                clean = strip_comments(body).strip()
                if not clean:
                    self.add(
                        "JAVA-EMPTY-CATCH", p, m.start(), end, t,
                        "空 catch 吞掉异常", "high", "verified",
                        "catch 块为空，异常被直接吞掉。",
                        "上层可能无法感知失败，导致数据不一致或问题难以定位。",
                        "确认是否应在 catch 中抛出、记录并告警，或执行补偿逻辑。",
                        ["exception", "reliability"],
                    )
                elif log_only_pat.fullmatch(clean):
                    self.add(
                        "JAVA-LOG-ONLY-CATCH", p, m.start(), end, t,
                        "catch 仅记录日志", "medium", "likely",
                        "catch 块只记录了日志，没有重新抛出或补偿。",
                        "调用方可能继续按成功路径执行，异常状态可能被掩盖。",
                        "确认上层是否有统一异常处理、重试或补偿机制。",
                        ["exception", "reliability"],
                    )

    def scan_loop_db(self):
        loop_pat = re.compile(r"\b(?:for|while)\s*\([^)]*\)\s*\{")
        db_call = re.compile(
            r"\.\s*(select(?:ById|One|List|Page|Count|Maps?|Objs?|BatchIds)?|"
            r"find(?:ById|All|By\w+)?|query(?:For\w+)?|count|exists|"
            r"save(?:Batch)?|insert|update(?:ById)?|delete(?:ById)?)\s*\(",
            re.I,
        )
        for p, t in self.iter_java():
            for m in loop_pat.finditer(t):
                brace = t.find("{", m.start(), m.end())
                if brace < 0:
                    continue
                end = find_matching_brace(t, brace)
                body = t[brace + 1:end - 1]
                calls = list(db_call.finditer(body))
                calls = [c for c in calls if not re.search(r"batch", c.group(1), re.I)]
                if not calls:
                    continue
                call = calls[0].group(0).strip()
                self.add(
                    "JAVA-DB-CALL-IN-LOOP", p, m.start(), end, t,
                    "循环内数据库访问", "high", "likely",
                    f"循环体内出现数据库访问调用：{call}",
                    "数据量增大时可能产生 N+1 查询或逐条写入，放大数据库压力。",
                    "确认循环规模、数据访问对象类型，以及是否存在 selectBatchIds、saveBatch 或 join 等批量路径。",
                    ["database", "n+1", "performance"],
                )

    def scan_transaction_rpc(self):
        ann = re.compile(r"@Transactional(?:\s*\([^)]*\))?")
        method = re.compile(
            r"\b(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?"
            r"[A-Za-z_$][\w$<>\[\], ?@.]*\s+([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws[^{]+)?\{",
            re.M,
        )
        remote = re.compile(
            r"\b(?:restTemplate|webClient|httpClient|okHttpClient|feignClient)\b|"
            r"\.(?:exchange|postForObject|postForEntity|getForObject|getForEntity|execute)\s*\(|"
            r"(?:rabbitTemplate|kafkaTemplate|rocketMQTemplate)\.(?:send|syncSend|asyncSend)\s*\(",
            re.I,
        )
        for p, t in self.iter_java():
            for a in ann.finditer(t):
                mm = method.search(t, a.end())
                if not mm:
                    continue
                brace = t.find("{", mm.start(), mm.end())
                if brace < 0:
                    continue
                end = find_matching_brace(t, brace)
                body = t[brace + 1:end - 1]
                rm = remote.search(body)
                if not rm:
                    continue
                self.add(
                    "JAVA-TX-REMOTE-CALL", p, a.start(), end, t,
                    "事务方法内调用远程服务", "high", "likely",
                    f"@Transactional 方法内出现远程调用：{rm.group(0).strip()}",
                    "事务持有时间可能被远程调用拉长，同时带来回滚与外部副作用不一致的风险。",
                    "确认远程调用是否应在事务提交后执行，以及是否有幂等、重试和补偿设计。",
                    ["transaction", "rpc", "consistency"],
                )

    @staticmethod
    def _secret_value_is_placeholder(value: str) -> bool:
        v = value.strip().strip('"\'')
        if len(v) < 3:
            return True
        low = v.lower()
        markers = (
            "${", "#{", "changeme", "change_me", "example", "your_", "your-",
            "xxx", "****", "dummy", "placeholder", "localhost", "test", "none",
            "null", "false", "true",
        )
        return any(x in low for x in markers)

    def scan_secrets(self):
        java_secret = re.compile(
            r'(?i)\b([A-Za-z_]\w*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)[A-Za-z0-9_]*)\s*=\s*"([^"\n]{3,})"'
        )
        config_secret = re.compile(
            r'(?i)^\s*([A-Za-z0-9_.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)[A-Za-z0-9_.-]*)\s*[:=]\s*(.+?)\s*$'
        )
        config_exts = {".properties", ".yml", ".yaml", ".xml", ".conf", ".ini"}
        for p, t in self.files.items():
            ext = Path(p).suffix.lower()
            if ext not in {".java"} | config_exts:
                continue
            for line_match in re.finditer(r"(?m)^.*$", t):
                line = line_match.group(0)
                if ext == ".java":
                    m = java_secret.search(line)
                else:
                    m = config_secret.search(line)
                if not m:
                    continue
                key, value = m.group(1), m.group(2).strip().strip('"\'')
                if self._secret_value_is_placeholder(value):
                    continue
                start = line_match.start() + m.start()
                end = line_match.end()
                self.redactions_by_path.setdefault(p, []).append((value, "***REDACTED***"))
                self.global_redactions.append((value, "***REDACTED***"))
                self.add(
                    "CONFIG-HARD-CODED-SECRET", p, start, end, t,
                    "硬编码敏感配置", "high", "verified",
                    f"出现字面量敏感配置或硬编码字符串：{key}（值已脱敏）",
                    "凭据进入版本库后可能被复制、泄露或误用于生产环境。",
                    "确认是否为示例值；生产密钥应迁移到环境变量或密钥管理服务，并轮换已提交的凭据。",
                    ["security", "secret", "config"],
                    redactions=[(value, "***REDACTED***")],
                )

    def scan_hardcoded_ip(self):
        pat = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
        allowed = {"127.0.0.1", "0.0.0.0", "255.255.255.255"}
        for p, t in self.files.items():
            if Path(p).suffix.lower() not in {".java", ".properties", ".yml", ".yaml", ".xml"}:
                continue
            for m in pat.finditer(t):
                ip = m.group(0)
                if ip in allowed or ip.startswith("127."):
                    continue
                before = t[max(0, m.start() - 120):m.start()]
                if not re.search(r"[\"']\s*$", before):
                    continue
                self.add(
                    "CONFIG-HARD-CODED-IP", p, m.start(), m.end(), t,
                    "硬编码 IP 地址", "low", "likely",
                    f"源码或配置中出现字面量 IP：{ip}",
                    "环境切换和故障迁移时容易失效，也可能暴露内部网络信息。",
                    "确认是否为本地/示例地址；生产环境建议使用域名或配置中心。",
                    ["config", "environment", "network"],
                )

    def scan_http_timeout(self):
        http_pat = re.compile(r"\b(?:RestTemplate|WebClient|OkHttpClient|HttpClient)\b")
        timeout_pat = re.compile(
            r"(?i)(?:setConnectTimeout|setReadTimeout|connectTimeout|readTimeout|"
            r"responseTimeout|\.timeout\s*\()"
        )
        has_timeout = any(timeout_pat.search(t) for t in self.files.values())
        if has_timeout:
            return
        for p, t in self.files.items():
            if Path(p).suffix.lower() not in {".java", ".xml", ".properties", ".yml", ".yaml"}:
                continue
            m = http_pat.search(t)
            if not m:
                continue
            self.add(
                "JAVA-HTTP-NO-TIMEOUT", p, m.start(), m.end(), t,
                "HTTP 客户端未见显式超时", "medium", "likely",
                "使用了 HTTP 客户端，但仓库中未发现显式连接/读取超时配置。",
                "下游变慢或不可用时，请求线程可能长时间阻塞，放大故障范围。",
                "确认是否由框架全局配置、网关或客户端工厂统一设置超时。",
                ["stability", "http", "timeout"],
            )

    def scan_limit_without_order(self):
        limit_pat = re.compile(r"(?i)\blimit\b")
        select_pat = re.compile(r"(?i)\bselect\b")
        for p, t in self.files.items():
            if Path(p).suffix.lower() not in {".sql", ".xml", ".java"}:
                continue
            for m in limit_pat.finditer(t):
                prefix_start = max(0, m.start() - 3000)
                prefix = t[prefix_start:m.start()]
                selects = list(select_pat.finditer(prefix))
                if not selects:
                    continue
                sql_start = prefix_start + selects[-1].start()
                segment = t[sql_start:m.end()]
                if re.search(r"(?i)\border\s+by\b", segment):
                    continue
                self.add(
                    "SQL-LIMIT-WITHOUT-ORDER", p, sql_start, m.end(), t,
                    "LIMIT 查询缺少 ORDER BY", "medium", "likely",
                    "查询使用 LIMIT，但片段中未发现 ORDER BY。",
                    "分页或截取结果可能随执行计划变化，导致顺序不稳定或数据漂移。",
                    "确认业务是否依赖稳定顺序，以及是否需要按主键或业务时间排序。",
                    ["sql", "pagination", "consistency"],
                )

    def scan_thread_sleep(self):
        pat = re.compile(r"\bThread\.sleep\s*\(")
        for p, t in self.iter_java():
            for m in pat.finditer(t):
                self.add(
                    "JAVA-THREAD-SLEEP", p, m.start(), m.end(), t,
                    "代码中调用 Thread.sleep", "medium", "verified",
                    "代码中出现 Thread.sleep 调用。",
                    "若位于请求链路或持锁代码中，会阻塞线程并放大延迟。",
                    "确认是否用于测试、定时任务或重试退避；生产链路优先使用调度器或异步等待。",
                    ["stability", "blocking"],
                )

    def scan_synchronized(self):
        pat = re.compile(r"\bsynchronized\b")
        for p, t in self.iter_java():
            for m in pat.finditer(t):
                self.add(
                    "JAVA-SYNCHRONIZED", p, m.start(), m.end(), t,
                    "synchronized 使用点", "low", "uncertain",
                    "代码中出现 synchronized 关键字。",
                    "锁粒度、锁对象和跨实例一致性需要结合业务确认，不能仅凭关键字判定为问题。",
                    "确认是否单机部署、锁对象是否共享、临界区是否包含 I/O 或远程调用。",
                    ["concurrency", "review"],
                )

    def scan_secret_logging(self):
        pat = re.compile(
            r'(?i)(?:System\.(?:out|err)\.print\w*|log(?:ger)?\.(?:info|debug|warn|error|trace))'
            r'\s*\([^;\n]*(?:secret|password|passwd|token|api[_-]?key|access[_-]?key)[^;\n]*\)'
        )
        for p, t in self.files.items():
            if Path(p).suffix.lower() not in JAVA_EXTS:
                continue
            for m in pat.finditer(t):
                text = m.group(0)
                is_stdout = "System.out" in text or "System.err" in text
                self.add(
                    "JAVA-SECRET-LOGGING", p, m.start(), m.end(), t,
                    "敏感信息写入日志", "high" if is_stdout else "medium",
                    "verified" if is_stdout else "likely",
                    "代码将 secret/password/token 相关变量写入控制台或日志。",
                    "日志可能被采集、转发或长期保存，扩大凭据泄露面。",
                    "确认日志中是否包含真实凭据；生产环境应只记录脱敏后的标识，不记录密钥、密码或完整 token。",
                    ["security", "logging", "secret"],
                )

    def scan_multi_write_no_tx(self):
        method = re.compile(
            r"\b(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?"
            r"[A-Za-z_$][\w$<>\[\], ?@.]*\s+([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws[^{]+)?\{",
            re.M,
        )
        write_call = re.compile(
            r"\.\s*(?:delete(?:ById|Batch|By\w+)?|remove(?:ById|Batch|By\w+)?|"
            r"update(?:ById|Batch|By\w+)?|save(?:Batch|OrUpdate)?|insert(?:Batch)?)\s*\(",
            re.I,
        )
        for p, t in self.iter_java():
            for m in method.finditer(t):
                brace = t.find("{", m.start(), m.end())
                if brace < 0:
                    continue
                end = find_matching_brace(t, brace)
                body = t[brace + 1:end - 1]
                writes = list(write_call.finditer(body))
                if len(writes) < 2:
                    continue
                prefix = t[max(0, m.start() - 240):m.start()]
                if "@Transactional" in prefix:
                    continue
                self.add(
                    "JAVA-MULTI-WRITE-NO-TX", p, m.start(), end, t,
                    "多次写操作缺少事务边界", "high", "likely",
                    f"方法 {m.group(1)} 内出现 {len(writes)} 次写操作，但方法附近未见 @Transactional。",
                    "中途失败时可能只完成部分写入，导致任务和用户等关联数据不一致。",
                    "确认这些写操作是否必须原子提交；如果是，应放在同一事务中并明确回滚边界。",
                    ["transaction", "consistency", "database"],
                )

    def scan_get_with_body(self):
        ann = re.compile(r"@GetMapping(?:\s*\([^)]*\))?")
        method = re.compile(
            r"\b(?:public|protected|private)\s+[A-Za-z_$][\w$<>\[\], ?@.]*\s+"
            r"([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws[^{]+)?\{",
            re.M,
        )
        for p, t in self.iter_java():
            for a in ann.finditer(t):
                mm = method.search(t, a.end())
                if not mm:
                    continue
                header = t[mm.start():mm.end()]
                if "@RequestBody" not in header:
                    continue
                brace = t.find("{", mm.start(), mm.end())
                if brace < 0:
                    continue
                end = find_matching_brace(t, brace)
                self.add(
                    "JAVA-GET-WITH-BODY", p, a.start(), end, t,
                    "GET 接口使用请求体", "medium", "verified",
                    f"GET 映射方法 {mm.group(1)} 的签名中出现 @RequestBody。",
                    "GET 请求体在代理、缓存、网关和不同客户端中的行为不一致，接口语义也不清晰。",
                    "确认是否应改为 POST/PUT/PATCH，或把查询参数放到 URL/查询参数中。",
                    ["api", "http", "design"],
                )

    def scan_userid_param_ownership(self):
        ann = re.compile(r"@(?:Get|Post|Put|Delete|Patch)Mapping(?:\s*\([^)]*\))?")
        method = re.compile(
            r"\b(?:public|protected|private)\s+[A-Za-z_$][\w$<>\[\], ?@.]*\s+"
            r"([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws[^{]+)?\{",
            re.M,
        )
        for p, t in self.iter_java():
            if "/controller/" not in p.replace("\\", "/").lower():
                continue
            for a in ann.finditer(t):
                mm = method.search(t, a.end())
                if not mm:
                    continue
                header = t[mm.start():mm.end()]
                if not re.search(r"@RequestParam(?:\s*\([^)]*\))?\s+[^,)]*\buserId\b", header, re.I):
                    continue
                brace = t.find("{", mm.start(), mm.end())
                if brace < 0:
                    continue
                end = find_matching_brace(t, brace)
                body = t[brace + 1:end - 1]
                if 'getAttribute("userId")' in body or "getUserIdFromToken" in body:
                    continue
                self.add(
                    "JAVA-USERID-PARAM-NO-OWNERSHIP", p, a.start(), end, t,
                    "接口直接接收 userId 参数", "medium", "likely",
                    f"接口方法 {mm.group(1)} 从请求参数接收 userId，但方法内未看到当前登录用户绑定校验。",
                    "如果接口返回或修改用户数据，可能存在越权查询或操作风险。",
                    "确认该接口是否应改为从 token 中取 userId，并补充资源归属校验。",
                    ["security", "authorization", "api"],
                )

    def scan_contains_path_filter(self):
        pat = re.compile(r"path\.contains\s*\(")
        for p, t in self.iter_java():
            for m in pat.finditer(t):
                self.add(
                    "JAVA-CONTAINS-PATH-FILTER", p, m.start(), m.end(), t,
                    "路径过滤使用 contains", "high", "likely",
                    "过滤器使用 path.contains(...) 判断是否跳过拦截。",
                    "包含匹配可能让非预期路径绕过认证或黑名单检查，例如路径中包含 /login 的其他接口。",
                    "确认是否应使用精确路径或 Servlet PathPattern 匹配。",
                    ["security", "filter", "path"],
                )

    def scan_delete_by_userid_mismatch(self):
        method = re.compile(
            r"\b(delete\w*ByUserId)\s*\([^)]*Long\s+userId[^)]*\)\s*\{",
            re.I,
        )
        for p, t in self.iter_java():
            for m in method.finditer(t):
                brace = t.find("{", m.start(), m.end())
                if brace < 0:
                    continue
                end = find_matching_brace(t, brace)
                body = t[brace + 1:end - 1]
                if not re.search(r"\b(?:removeById|deleteById)\s*\(\s*userId\s*\)", body):
                    continue
                self.add(
                    "JAVA-DELETE-BY-USERID-MISMATCH", p, m.start(), end, t,
                    "按用户删除却传入了用户 ID", "high", "verified",
                    f"方法 {m.group(1)} 按 userId 命名，但内部使用 removeById(userId)/deleteById(userId) 删除主键。",
                    "这会删除错误记录或静默失败，属于典型的主键与业务外键混用风险。",
                    "确认应使用按 userId 条件删除（LambdaQueryWrapper/自定义 SQL），并补充测试。",
                    ["database", "bug", "query"],
                )

    def scan_cache_invalidation(self):
        for p, t in self.iter_java():
            if "opsForValue().set(" not in t:
                continue
            if not re.search(r"\b(?:save|saveBatch|updateById|removeById|delete|insert)\s*\(", t):
                continue
            if "redisTemplate.delete(" in t or "@CacheEvict" in t:
                continue
            m = re.search(r"opsForValue\(\)\.set\(", t)
            if not m:
                continue
            self.add(
                "JAVA-CACHE-INVALIDATION-MISSING", p, m.start(), m.end(), t,
                "缓存写入后未见失效逻辑", "medium", "likely",
                "类中出现 Redis 缓存写入和数据库写操作，但未见缓存删除或 @CacheEvict。",
                "相关数据更新后缓存可能继续返回旧值，具体取决于 TTL 和业务容忍度。",
                "确认计数缓存是否允许 TTL 内的短暂不一致；如需强一致，应在写操作后删除对应缓存。",
                ["cache", "consistency", "redis"],
            )

    def parse_ddl(self) -> Dict[str, set]:
        schema: Dict[str, set] = {}
        ddl_pat = re.compile(
            r"(?is)\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?"
            r"[`\"]?([A-Za-z0-9_.]+)[`\"]?\s*\((.*?)\)\s*(?:engine|default\s+charset|;)"
        )
        col_pat = re.compile(
            r"(?im)^\s*[`\"]?([A-Za-z_]\w*)[`\"]?\s+"
            r"(?:bigint|int|integer|smallint|tinyint|varchar|char|text|longtext|mediumtext|"
            r"datetime|timestamp|date|time|decimal|numeric|double|float|json|blob|boolean|bit)\b"
        )
        index_pat = re.compile(
            r"(?i)(?:primary\s+key|unique(?:\s+(?:key|index))?|key|index)"
            r"\s*(?:[`\"][^`\"]+[`\"]|\w+)?\s*\(([^)]+)\)"
        )
        for p, t in self.files.items():
            if Path(p).suffix.lower() != ".sql":
                continue
            for m in ddl_pat.finditer(t):
                table = m.group(1).split(".")[-1].strip("`\"")
                body = m.group(2)
                indexed = set()
                for cm in col_pat.finditer(body):
                    col = cm.group(1)
                    col_start = cm.start(1)
                    line_start = body.rfind("\n", 0, col_start) + 1
                    line_end = body.find("\n", col_start)
                    line = body[line_start:line_end if line_end >= 0 else len(body)]
                    if re.search(r"(?i)\bprimary\s+key\b", line):
                        indexed.add(col)
                for im in index_pat.finditer(body):
                    cols = [c.strip().strip("`\"") for c in im.group(1).split(",")]
                    if cols:
                        indexed.add(cols[0])
                if table not in schema:
                    schema[table] = set()
                schema[table].update(indexed)
        return schema

    def scan_where_index(self, schema: Dict[str, set]):
        if not schema:
            return
        query_pat = re.compile(
            r"(?is)\bselect\b.{0,2500}?\bfrom\s+[`\"]?([A-Za-z0-9_.]+)[`\"]?"
            r"(?:\s+(?:as\s+)?[A-Za-z_]\w*)?\s+where\s+(.{0,1200}?)"
            r"(?:\bgroup\s+by\b|\border\s+by\b|\blimit\b|;|$)"
        )
        where_col = re.compile(
            r"(?i)\b([A-Za-z_]\w*)\s*(?:=|<>|!=|>=|<=|>|<|\blike\b|\bin\b|\bis\b)"
        )
        keywords = {
            "and", "or", "not", "null", "exists", "in", "is", "like", "between",
            "select", "from", "where", "order", "group", "limit", "join", "on", "mapper",
        }
        for p, t in self.files.items():
            if Path(p).suffix.lower() not in {".sql", ".xml", ".java"}:
                continue
            for m in query_pat.finditer(t):
                table = m.group(1).split(".")[-1].strip("`\"")
                if table not in schema:
                    continue
                where = re.sub(r"<[^>]+>", " ", m.group(2))
                cols = []
                for cm in where_col.finditer(where):
                    col = cm.group(1)
                    if col.lower() not in keywords and col not in cols:
                        cols.append(col)
                missing = [c for c in cols if c not in schema[table]]
                if not missing:
                    continue
                self.add(
                    "SQL-WHERE-COLUMN-NOT-INDEXED", p, m.start(), m.end(), t,
                    "WHERE 字段未被 DDL 索引覆盖", "medium", "likely",
                    "简单 WHERE 查询使用了未在解析到的 DDL 索引中出现的字段：" + ", ".join(missing),
                    "数据量增长后可能出现全表扫描或索引失效，具体取决于执行计划和数据分布。",
                    "用 EXPLAIN 验证实际执行计划，并确认 DDL 解析是否覆盖了全部迁移脚本和复合索引。",
                    ["sql", "index", "performance"],
                )

    def finalize_snippets(self):
        for signal in self.signals:
            path = str(signal["path"])
            text = self.files.get(path, "")
            start = int(signal.pop("_start", 0))
            end = int(signal.pop("_end", start))
            snippet = make_snippet(text, start, end, context=2, redactions=self.global_redactions)
            signal["snippet"] = snippet
            signal["snippet_hash"] = snippet_hash(snippet)

    def scan_all(self) -> List[Dict[str, object]]:
        # Collect secret redactions first so every later snippet is sanitized.
        self.scan_secrets()
        self.scan_todo()
        self.scan_catch()
        self.scan_loop_db()
        self.scan_transaction_rpc()
        self.scan_secret_logging()
        self.scan_multi_write_no_tx()
        self.scan_get_with_body()
        self.scan_userid_param_ownership()
        self.scan_contains_path_filter()
        self.scan_delete_by_userid_mismatch()
        self.scan_cache_invalidation()
        self.scan_hardcoded_ip()
        self.scan_http_timeout()
        self.scan_limit_without_order()
        self.scan_thread_sleep()
        self.scan_synchronized()
        schema = self.parse_ddl()
        self.scan_where_index(schema)
        self.finalize_snippets()
        return self.signals


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan a Java/Spring Boot repository for evidence-linked signals.")
    parser.add_argument("repo", help="Repository root")
    parser.add_argument("--out", default="signals.json", help="Output JSON path (default: signals.json)")
    parser.add_argument("--max-files", type=int, default=8000, help="Maximum text files to inspect")
    parser.add_argument("--max-signals", type=int, default=300, help="Maximum signals to emit")
    parser.add_argument("--include-tests", action="store_true", help="Include findings from test sources")
    args = parser.parse_args()

    root = Path(args.repo).resolve()
    if not root.is_dir():
        print(f"error: repository path is not a directory: {root}", file=sys.stderr)
        return 2

    files, scan_stats, warnings = collect_files(root, args.max_files)
    if not files:
        print("error: no readable text files found", file=sys.stderr)
        return 2

    git_meta = {
        "head": run_git(root, ["rev-parse", "HEAD"]),
        "branch": run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"]),
        "dirty": bool(run_git(root, ["status", "--porcelain"])),
    }
    profile = build_profile(files, root, git_meta)
    scanner = Scanner(files, root, include_tests=args.include_tests)
    signals = scanner.scan_all()
    signals.sort(
        key=lambda s: (
            -SEVERITY_RANK.get(str(s["severity"]), 0),
            -CONFIDENCE_RANK.get(str(s["confidence"]), 0),
            str(s["path"]),
            int(s["line_start"]),
        )
    )
    signals = signals[: args.max_signals]
    for idx, s in enumerate(signals, start=1):
        s["id"] = f"E{idx:03d}"

    by_rule: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    by_confidence: Dict[str, int] = {}
    for s in signals:
        by_rule[str(s["rule_id"])] = by_rule.get(str(s["rule_id"]), 0) + 1
        by_severity[str(s["severity"])] = by_severity.get(str(s["severity"]), 0) + 1
        by_confidence[str(s["confidence"])] = by_confidence.get(str(s["confidence"]), 0) + 1

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "repo": {
            "path": str(root),
            "git": git_meta,
            "files_total": scan_stats["files_total"],
            "files_scanned": scan_stats["files_scanned"],
            "files_skipped": scan_stats["files_skipped"],
        },
        "project": profile,
        "signals": signals,
        "stats": {
            "signals": len(signals),
            "by_rule": dict(sorted(by_rule.items())),
            "by_severity": by_severity,
            "by_confidence": by_confidence,
        },
        "warnings": warnings,
    }

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    verified_high = sum(1 for s in signals if s["severity"] == "high" and s["confidence"] == "verified")
    print(f"Scanned {scan_stats['files_scanned']} text files from {root}")
    print(f"Project: {profile['name']} | {profile['summary']}")
    print(f"Signals: {len(signals)} (high={by_severity.get('high', 0)}, verified_high={verified_high})")
    print(f"Wrote {out}")
    if warnings:
        for w in warnings:
            print(f"warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
