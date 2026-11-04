# 通用作答证据格式

最终答案仍使用题目规定的文件与JSON格式。

可以把工作中已形成的结构化候选及其来源持续保存到`/app/work-evidence.json`。
该文件用于保留未完成交卷时的可验证工作；不改变题目要求。

格式：
```json
{
  "schema_version": "l2_work_evidence_v1",
  "candidate": {},
  "claims": [{"target": "<候选答案字段的JSON Pointer>", "refs": ["<支持该字段的记录引用>"]}],
  "source_refs": ["<其他引用>"],
  "observations": []
}
```

candidate使用与最终答案相同的字段，可以只保存已经形成的部分；最终答案优先。
refs使用数据接口返回的引用，不填写猜测的引用。
若接口返回的是派生时间序列引用，可在observations中保留对应的原始查询结果：
`record_ref`、`metric_name`、`entity_id`、`dimensions`、`unit`和`points`；
points是包含`timestamp`、`value`的列表，保留查询返回的原单位、尺度和时区。
不要写API密钥、访问令牌或连接凭据。此格式没有指定解题顺序或要求使用特定工具。
