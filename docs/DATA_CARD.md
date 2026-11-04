# OpsGauge dataset card

## Scope and units

OpsGauge contains Data Retrieval (DR), Anomaly Detection (AD), Health Assessment (HA), and Root-Cause Analysis (RCA). There are 429 measurement families and 1,287 cases at three information levels. Each case has File-based, Native-API, and Semantic-query access, yielding 3,861 executable packages.

DR has 74 families, AD 50, HA 177, and RCA 128. Families can share evidence; counts are not independent-incident counts. The release includes 15,444 scores for four model–harness pairings.

## Information and slices

L1 supplies more investigative context and L3 less. AD source IL3/IL2/IL1 maps to paper L1/L2/L3. Source and paper identifiers are explicitly associated in the task index. DR has six evaluation slices, AD seven, HA eight, and RCA seven; S1, S2, etc. are task-local. HA healthy-scope confirmation has only one family.

`analysis_cluster_id` groups related paper-analysis observations and differs from a source `bundle_id`. A bundle ID is null where not established by source metadata. AD Fxxx names identify families, not RCA fault-taxonomy labels.

## Annotation

Question rewriting and reference-answer construction used GPT-6.1 Sol with incident and measurement subgraphs, followed by human sampling review of whether observations support the reference. Reward computation is deterministic; no LLM judges submissions. Inspection for injection-tool artifacts was performed. Audit sample size, error rate, and inter-reviewer agreement are not quantitatively reported.

Construction manifests and disclosure/reference metadata are included in task packages. The release provides executable evaluation artifacts; a complete reusable authoring pipeline and authoring prompt collection are not included.

## Agent/evaluator separation

Reference answers and evaluator contracts are downloadable for research but must remain outside agent-visible build contexts and mounts during evaluation. Keep model credentials outside task data.

## Conditions of use

Evidence is recorded for replay; reconstructing incident collection requires the corresponding infrastructure. Exact fault labels and explicit entity-linked citations define the grading contract. They are not certificates of general causal or semantic correctness. Execution configurations are documented in `configs/`.

Credential, workstation-path, and recorded URL-host substitutions are described in [data handling](SANITIZATION.md). Test deployment identifiers and business names remain. License terms have not yet been specified.
