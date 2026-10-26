# 改前 / 改后代码卡

改前图由 `render_evidence.py` 生成，文件名为 `assets/E###.png`。  
改后图由 `render_fix_cards.py` 生成，文件名为 `assets/E###-after.png`。

## 什么时候生成

只给 3—5 个高价值、可落地的证据生成改后建议。不要给每个信号都硬写修改。

优先级：

1. `high + verified` 的安全、数据一致性或明显 bug
2. `high + likely` 但修改边界清楚的性能/事务问题
3. 初学者最容易理解、最能体现工程判断的问题

## fixes.json 格式

```json
{
  "fixes": [
    {
      "evidence_id": "E004",
      "title": "把按用户删除改成按 userId 条件删除",
      "path": "src/main/java/.../TaskServiceImpl.java",
      "line_start": 52,
      "line_end": 52,
      "after_snippet": "public boolean deleteTaskByUserId(Long userId) {\n    return remove(new LambdaQueryWrapper<Task>().eq(Task::getUserId, userId));\n}"
    }
  ]
}
```

字段说明：

- `evidence_id`：必须对应 `signals.json` 中已有 `E###`。
- `title`：建议修改的短标题。
- `path`：建议代码所属文件；省略时继承原证据路径。
- `line_start` / `line_end`：改后代码卡显示的行号范围。
- `after_snippet`：最小修改后的代码片段，必须是建议方案。
- `badge`：可选，默认 `after fix`。

## 运行

```bash
python scripts/render_fix_cards.py <output>/signals.json <output>/fixes.json --out-dir <output>/assets --theme dark
```

## 硬规则

- 改后代码必须标注“建议修改”，不能假装是项目原有代码。
- 不要为了生成对比图而虚构不存在的上下文。
- 改后片段应尽量短，只展示发生变化的代码。
- 不把密码、secret、token 写入 `fixes.json` 的示例代码。
- 报告中没有生成 `E###-after.png` 时，不要写对应图片引用。
- 图片下方保留原证据的 `[E###] path:line`。
