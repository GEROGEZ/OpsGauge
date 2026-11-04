# RCA raw scene task

请从告警与可观测数据中自主完成告警收敛、根因实体定位、故障机制辨认、跨层因果链恢复和证据完整性检查。

<code vibeops_object type="alert_event">promotion 错误次数告警 (&gt;10 over 1min)<alert_event event_id="a6ab8ccf4a3efcbf7f2495b2db517e26" trans_id="4e8t1m5nkt1hd8vun4k20j4o52" rule_id="284851df-a2f6-4e5a-88a6-c20879f79859" rule_name="promotion 错误次数告警 (&gt;10 over 1min)" entity_id="ee07ecf72f9a10eafeb80856e53c6351" entity_name="/api/promotion/calculate" entity_type="apm.operation" entity_domain="apm" severity="CRITICAL" alert_time="2026-06-21T11:49:19+08:00" /></code> 帮我分析下根因。

The scene is mounted at `/scene`. Start with `/scene/manifest.json`, discover
entities in `/scene/entities.jsonl`, and inspect raw observations under
`/scene/telemetry/*.jsonl` using Bash, jq, or Python. Each observation exposes
its exact `record_ref`; cite those identifiers in evidence-bearing answers. The
files contain scene distractors, including observations outside the requested
query window, and do not identify which entities answer the question. Apply the
question's time window and perform all selection, alignment, judgement, and
causal assembly yourself. Do not read `/tests`; it is verifier-only. Write the
required JSON to `/logs/agent/answer.json`.
For RCA, `/scene/fault-taxonomy.json` exposes the complete release-wide, answer-independent fault vocabulary; the same vocabulary is inlined here:

F001-nodeDown, F002-threadExhaustion, F004-trafficHotspot, F005-messageQueueBacklog, F006-trafficSurge, F007-memoryPressure, F008-offHeapMemoryLeak, F009-cacheBreakdown, F010-slowSQL, F012-cpuDeadLoop, F014-httpError5xx, F016-rateLimiting, F018-dbNetworkLatency, F020-loadBalancerFailure, F022-fullGC, F023-nullPointerException, F024-diskFull, F025-diskIOHigh, F026-nodeCpuHigh, F029-redisUnavailable, F031-nodeMemoryOOM, F032-podFail, F033-slowResource, F034-cpuFullLoad, F036-replicaScaleDown, F037-imagePullFail, F038-podKill, F039-resourceLimitMisconfig, F040-retryStorm, F041-nPlusOneQuery, F042-connectionPoolExhaustion, F043-mutexContention, F044-channelBackpressure, F045-cpuThrottling, F046-configDriftMisroute, F047-connectionIdleTimeoutMismatch, F050-podCrashLoop, F051-podPendingUnschedulable, F052-podRestartFlapping, F054-volumeDiskFull, F055-podNotReady, F056-networkPolicyIsolation, F057-dnsResolutionFailure, F058-podNetworkLatency, F059-podPacketLoss, F061-fileDescriptorLeak, F062-servicePanic, F063-threadLeak, F064-metaspaceLeak, F065-cgroupMemoryOOM, F066-applicationCodeDelay, F070-socketExhaustion, F071-conntrackTableFull, F072-tcpMemoryPressure, F073-pageCacheHigh, F074-nodeNetworkPacketLoss, F075-nodeShmemLeak, F076-kernelSlabPressure, F077-inodeExhaustion, F079-forkProcessStorm, F080-serviceTargetPortMisbind, F082-pvcBindingFailure, F083-probeMisconfig, F084-deployedCodeDefect, F085-silentDataCorruption, F087-nodeComponentFailure, F089-podBandwidthThrottle, F090-jvmMethodReturnMutated, F091-jdbcCallFault, F092-httpPayloadTampered, F093-httpStatusTampered, F094-namespaceQuotaExhausted, F095-admissionPolicyBlock, F096-operatorCrMisconfig, F098-serviceMeshPolicyBlock, F099-credentialExpiry, F101-llmTimeout, F102-ecsSecurityGroupIsolation, F103-ecsSecurityGroupRuleBlock, F106-rdsInstanceRestart, F107-ecsInstanceReboot, F110-rdsTableLockContention, F111-redisInstanceRestart, F112-redisSlowCommand, F113-slbConnectionOverload, F114-rdsCpuExhaustion, F115-paiEasServiceOverload, F116-rdsConnectionExhaustion, F118-redisHotKey, F119-redisBandwidthSaturation, F120-redisConnectionExhaustion, F121-redisMemoryFull, F123-kafkaTrafficSaturation, F124-rocketmqSendTpsSpike, F125-elasticsearchSearchOverload, F126-ossAccessCredentialFailure, F127-fcFunctionError, F128-fcFunctionTimeout, F130-apigatewayBackendError, F131-apigatewayBackendLatency, F134-elasticsearchDiskIOHigh, F135-elasticsearchJvmHeapPressure, F136-elasticsearchNodeCpuHigh, F137-apigatewayBackendThrottled, F139-rocketmqConsumerGroupBacklog, F140-kafkaConsumerGroupBacklog, F141-mongodbConnectionExhaustion, F142-mongodbSlowQueryReadLatency, F143-mongodbQueryStorm, F144-mongodbDiskIopsSaturation, F145-mongodbWriteConflictStorm, F146-mongodbConnectionChurn, F147-polardbDiskIopsSaturation, F148-networkPathUnreachable, F149-lindormLoadSaturation, F150-lindormWriteThroughput, F151-kafkaMessageRateSurge, F152-mongodbInstanceRestart, F153-elasticsearchNodeRestart, F154-kafkaTopicDeleted, F155-ossBucketPolicyDenied, F156-rocketmqTopicDeleted, F158-nlbHealthCheckMisconfig, F159-fcConfigChangeError, F160-gpdbConnectionSurge, F161-rdsInefficientQueryStorm, F163-nasMountTargetUnreachable, F201-mqConsumerBacklog, F202-feeCalcAnomaly, F203-paymentCallbackBlock, F204-dbConnDeadlock, F205-redisConnBlock, F206-fileAnalysisBacklog, F207-resourceExhaustion, F208-componentUpgradeAnomaly, F209-serviceRegistryDelay, F210-nacosConfigRefreshFail, F211-latentCodePathDefect, F212-configBorneRollbackMismatch, F301-renderBlockingResource, F302-staticAssetSlowDelivery, F303-thirdPartyScriptUnavailable, F304-interactionHandlerBlocking, F305-interactionHandlerFailure, F309-sourcemapWrongService, F310-sourcemapDebugIdMismatch

Every `root_causes[*].fault_id` must be selected from and copied exactly as one of these labels; a fault_id outside this vocabulary scores zero on fault_type. When several labels look plausible, choose the one that most directly names the affected entity's primary broken capability — the observed failure state of that entity — rather than the mechanism that induced it or symptoms on downstream entities.

## Strict output contract

Read `/scene/answer-schema.json` before writing the answer. The evaluator reads
only `/logs/agent/answer.json`. Write one UTF-8 JSON object that conforms to
that schema. Do not wrap the object in Markdown and do not read `/tests`.

For every evidence item, copy `record_ref` values returned by successful scene
queries into `record_refs`. Findings and causal claims must reference those
evidence items through `evidence_ids`; unsupported narrative claims do not
receive evidence-grounding credit.

`record_ref` values are opaque server-assigned identifiers: copy them verbatim
and never attempt to decode, reverse-engineer, or validate their internal
structure. This task has a hard wall-clock limit: write a complete draft of
`/logs/agent/answer.json` as soon as you have defensible results — partial
results are better than none — and refine the file in place as evidence
accumulates. An answer file that is never written scores zero, so when time
runs short, stop exploring and finalize the file immediately.
