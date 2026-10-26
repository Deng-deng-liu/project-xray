# Java / Spring Boot 扫描规则（v0.1）

扫描器只做静态读取，不执行项目代码。规则命中是“信号”，不是自动结论。

## SRC-TODO

- 命中：`TODO`、`FIXME`、`HACK`、`XXX`
- 默认等级：低；`verified`
- 可以说什么：源码中存在未完成或临时标记。
- 不能说什么：一定是严重 bug、一定没有完成。
- 核实：查看标记上下文、后续提交和调用路径。

## JAVA-EMPTY-CATCH

- 命中：`catch (...) { }`
- 默认等级：高；`verified`
- 风险：异常被吞掉，上层无法感知失败。
- 核实：是否应抛出、记录并告警，或执行补偿。

## JAVA-LOG-ONLY-CATCH

- 命中：catch 块中只有日志、`printStackTrace` 等语句。
- 默认等级：中；`likely`
- 风险：调用方可能继续按成功路径执行。
- 核实：上层是否有统一异常处理、重试或补偿。

## JAVA-DB-CALL-IN-LOOP

- 命中：`for` / `while` 循环体内出现 mapper、repository 或 JDBC 查询/写入调用。
- 默认等级：高；`likely`
- 风险：可能形成 N+1 查询或逐条写入。
- 误报来源：循环规模很小、内部已有缓存、调用的是批量接口。
- 核实：确认循环规模、访问对象类型，以及是否有 `selectBatchIds`、`saveBatch`、join 或缓存。

## JAVA-TX-REMOTE-CALL

- 命中：`@Transactional` 方法内出现 HTTP、Feign、消息发送等远程调用。
- 默认等级：高；`likely`
- 风险：事务持有时间变长，外部副作用和数据库回滚可能不一致。
- 误报来源：方法内实际是事务后回调、调用的是本地对象、事务由其他机制管理。
- 核实：远程调用是否应放到事务提交后，是否有幂等、重试和补偿。

## CONFIG-HARD-CODED-SECRET

- 命中：Java、YAML、Properties、XML 中出现字面量密码、Token、密钥等。
- 默认等级：高；`verified`
- 风险：凭据进入版本库，可能泄露或被误用。
- 误报来源：示例值、占位符、测试专用假值。
- 核实：是否为真实凭据；真实凭据应迁移到环境变量或密钥管理并轮换。

## CONFIG-HARD-CODED-IP

- 命中：Java 字符串或配置中出现字面量 IP。
- 默认等级：低；`likely`
- 风险：环境切换困难，可能暴露内部网络信息。
- 误报来源：本地地址、示例地址、监控目标。
- 核实：是否应改为域名或配置中心。

## JAVA-HTTP-NO-TIMEOUT

- 命中：仓库使用 `RestTemplate`、`WebClient`、`OkHttpClient` 或 `HttpClient`，但未发现显式超时配置。
- 默认等级：中；`likely`
- 风险：下游变慢时线程可能长时间阻塞。
- 误报来源：超时由父配置、网关、连接池或依赖库统一设置。
- 核实：检查全局配置和客户端工厂，而不是只看某个调用点。

## SQL-LIMIT-WITHOUT-ORDER

- 命中：`SELECT ... LIMIT` 片段中未出现 `ORDER BY`。
- 默认等级：中；`likely`
- 风险：分页或截取结果顺序不稳定。
- 误报来源：业务不关心顺序，或数据库有稳定的默认顺序假设。
- 核实：业务是否依赖顺序；建议按主键或业务时间显式排序。

## JAVA-THREAD-SLEEP

- 命中：`Thread.sleep(...)`
- 默认等级：中；`verified`
- 风险：阻塞当前线程；位于请求链路或持锁代码中时影响更大。
- 误报来源：测试、定时任务、重试退避。
- 核实：调用位置、线程池配置和锁范围。

## JAVA-SYNCHRONIZED

- 命中：`synchronized`
- 默认等级：低；`uncertain`
- 风险：锁粒度、锁对象和跨实例一致性需要确认。
- 误报来源：单机场景下完全合理，或只保护极小临界区。
- 核实：是否单机部署、锁对象是否共享、临界区是否包含 I/O 或远程调用。

## SQL-WHERE-COLUMN-NOT-INDEXED

- 命中：简单 `SELECT ... FROM table ... WHERE col = ...` 中，`col` 未出现在解析到的 DDL 索引列中。
- 默认等级：中；`likely`
- 风险：数据量增长后可能出现全表扫描，具体取决于执行计划和数据分布。
- 误报来源：DDL 未覆盖全部迁移脚本、复合索引、函数索引、数据库优化器选择。
- 核实：必须用 `EXPLAIN` 验证，并检查所有迁移脚本。

## 全局注意

- 扫描器目前只解析 `CREATE TABLE` 形式的 DDL；ORM 自动建表、Flyway/Liquibase 的非标准写法可能漏报。
- 规则命中不等于代码一定有问题；`likely` 和 `uncertain` 必须保留核实提示。
- 报告写完后必须运行 `validate_report.py`。


## JAVA-SECRET-LOGGING

- 命中：`System.out.println`、`System.err.print` 或日志语句中出现 `secret/password/token/key` 等相关变量。
- 默认等级：高；控制台输出视为 `verified`，普通日志视为 `likely`。
- 风险：凭据或完整 token 进入日志采集链路。
- 核实：确认日志是否会被生产采集；应只记录脱敏标识或配置加载状态。

## JAVA-MULTI-WRITE-NO-TX

- 命中：一个方法内出现两次以上保存、更新、删除等写操作，且方法附近未见 `@Transactional`。
- 默认等级：高；`likely`。
- 风险：中途失败时出现部分提交。
- 核实：确认这些写操作是否必须原子完成；数据量大时再讨论事务、软删除或异步补偿。

## JAVA-GET-WITH-BODY

- 命中：`@GetMapping` 方法的参数中出现 `@RequestBody`。
- 默认等级：中；`verified`。
- 风险：GET 请求体在代理、网关、缓存和客户端中的行为不一致。
- 核实：更新类操作应改用 PUT/PATCH；查询参数应放入 URL 或 query string。

## JAVA-USERID-PARAM-NO-OWNERSHIP

- 命中：Controller 方法从 `@RequestParam` 接收 `userId`，方法内未看到当前登录用户绑定校验。
- 默认等级：中；`likely`。
- 风险：可能出现 IDOR/越权查询。
- 核实：确认接口是否只允许管理员调用；普通用户应从 token 中取 userId，并校验资源归属。

## JAVA-CONTAINS-PATH-FILTER

- 命中：安全过滤器使用 `path.contains(...)` 判断是否跳过拦截。
- 默认等级：高；`likely`。
- 风险：包含匹配可能让非预期路径绕过认证或黑名单检查。
- 核实：改为精确路径或 PathPattern，并增加边界路径测试。

## JAVA-DELETE-BY-USERID-MISMATCH

- 命中：`delete...ByUserId(Long userId)` 方法内部调用 `removeById(userId)` 或 `deleteById(userId)`。
- 默认等级：高；`verified`。
- 风险：按用户删除变成按主键删除，可能误删或静默失败。
- 核实：改为按 `userId` 条件删除，并补测试覆盖主键与外键不同的情况。

## JAVA-CACHE-INVALIDATION-MISSING

- 命中：类中存在 Redis 缓存写入和数据库写操作，但未见缓存删除或 `@CacheEvict`。
- 默认等级：中；`likely`。
- 风险：写操作后缓存可能继续返回旧值。
- 核实：确认业务能否接受 TTL 内的最终一致；如不能，写操作后主动删除对应缓存。
