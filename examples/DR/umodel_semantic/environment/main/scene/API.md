# 查询接口

运行时提供 `UMODEL_URL`、`UMODEL_ENVIRONMENT_ID`、`UMODEL_WORKSPACE`、`UMODEL_REGION_ID`。使用提供的API读取冻结观测数据，不修改环境。

- `GET /v1/health`：健康状态。
- `GET /v1/capabilities`：能力与分页上限。
- `POST /v1/entities/search`：实体目录。至少提供query（名称关键词）、entity_types（类型列表）、attributes（属性匹配）中的一项非空条件。
- `POST /v1/signals`：信号目录，entity_selector选择范围，modalities选择模态。
- `POST /v1/query`：观测查询。

POST公共字段：environment_id、workspace、region_id，使用对应环境变量值。

```json
{
  "request_id": "<逻辑查询标识>",
  "environment_id": "<环境标识>",
  "workspace": "<工作空间>",
  "region_id": "<区域>",
  "time_range": {"start":"<带时区时间>","end":"<带时区时间>"},
  "entity_selector": {"entity_types":["<实体类型>"]},
  "query": {"kind":"<模态>"},
  "page_size": 1000
}
```

entity_selector也支持entity_ids。query参数：

```json
{"kind":"metric","metric_name":"<信号名>","stat":"series"}
{"kind":"log","text":"","filters":{},"order":"asc"}
{"kind":"trace","trace_id":"","service_name":"","operation_name":"","status":"","attributes":{}}
{"kind":"event","event_type":"","status":"","rule_id":"","attributes":{}}
{"kind":"topology","direction":"both","max_depth":2}
```

空字符串表示不指定该过滤条件。业务字段在attributes中；记录用record_ref引用。时间过滤包含两端。

响应含results和page；将page.next_cursor作为下一请求顶层cursor，其余参数不变，直至next_cursor为null。新逻辑查询使用新的request_id，不使用offset。
