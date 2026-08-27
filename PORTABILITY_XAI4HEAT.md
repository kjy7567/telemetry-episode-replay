# XAI4HEAT Portability Boundary

The XAI4HEAT experiment tests whether the telemetry-to-episode recipe can be reused on a second continuous telemetry corpus. It does not establish portability to arbitrary event logs, IT incidents, or manufacturing state machines.

## What Is Shared

After corpus adaptation, BTS and XAI4HEAT use the same normalized records:

```text
metadata:     site_id, stream_id, point_class, equipment, location
observations: site_id, stream_id, timestamp, value
```

They then share:

- `build_tool_store_from_tabular_corpus` quality and aggregate tables;
- the read-only point, lookup, aggregate, and quality tools;
- the same five applicable static family builders;
- deterministic clarification and operator-surface construction;
- canonical phase generation;
- single-stream typed repair logic;
- contract preflight, controller audit, runner protocol, and scorer.

## What Is Corpus-Specific

`src/bts_agentbench/xai4heat.py` is the adapter. It performs three corpus-specific operations:

1. reads `xai4heat_heating_area.csv` and sorted `xai4heat_scada_*_processed.csv` files;
2. maps seven SCADA columns to typed point classes;
3. constructs stable site, stream, equipment, and location identifiers.

The seven mappings are:

| Raw column | Normalized point class |
|---|---|
| `t_amb` | `Outdoor_Temperature_Sensor` |
| `t_ref` | `Reference_Temperature_Setpoint` |
| `t_sup_prim` | `Primary_Supply_Temperature_Sensor` |
| `t_ret_prim` | `Primary_Return_Temperature_Sensor` |
| `t_sup_sec` | `Secondary_Supply_Temperature_Sensor` |
| `t_ret_sec` | `Secondary_Return_Temperature_Sensor` |
| `delta_e` | `Energy_Transfer_Sensor` |

For example, column `delta_e` at substation `L17` becomes:

```text
site_id:         XAI4HEAT_L17
stream_id:       XAI4HEAT_L17__delta_e
point_class:     Energy_Transfer_Sensor
equipment_type:  Heating_Substation
equipment_label: XAI4HEAT Substation L17
location_type:   District_Heating_Substation
```

`XAI4HEAT_L17` is held out for test. Corpus-specific operator wrappers refer to district-heating telemetry.

## Applicable Family Subset

The adapter reuses five single-stream temporal families:

- day mean lookup;
- relative 24-hour mean lookup;
- window mean lookup;
- exact timestamp value lookup;
- nearest timestamp lookup.

Point disambiguation, pairwise comparison, rank, and standalone quality-gate families were not released for XAI4HEAT. Its compact one-substation/seven-signal schema does not provide the same ambiguity and candidate-group structure as BTS. Excluding those families is a declared schema decision, not a silent generation failure.

## Released Output

The released data bundle retains 204 XAI4HEAT episodes:

| Family | Train | Dev | Test | Total |
|---|---:|---:|---:|---:|
| Day mean lookup | 40 | 10 | 10 | 60 |
| Relative 24h mean lookup | 40 | 10 | 10 | 60 |
| Window mean lookup | 6 | 1 | 7 | 14 |
| Timestamp value lookup | 23 | 5 | 7 | 35 |
| Timestamp nearest lookup | 23 | 5 | 7 | 35 |
| **Total** | **132** | **31** | **41** | **204** |

The public test artifacts report `0/41` rows accomplished by the fixed deterministic controller and `41/41` by the retained GPT-5.5 run. These numbers demonstrate execution of the shared contract on the second telemetry schema; they are not evidence for arbitrary-log portability.

## Concrete Shared Contract

`test_day_mean_lookup_00002` starts from energy-transfer observations for `XAI4HEAT_L17__delta_e`. The shared pipeline constructs:

1. a missing-time clarification;
2. a day-bounded mean query;
3. a previous-day goal revision;
4. exact-then-nearest timestamp policy calls;
5. a day-bounded quality decision;
6. a typed reporting commitment and evidence follow-up.

Its canonical call sequence is:

```text
resolve_point
  -> aggregate_window
  -> aggregate_window
  -> lookup_observation(exact)
  -> lookup_observation(nearest)
  -> inspect_quality_window
```

This is the same contract topology and runtime interface used by the corresponding BTS day-mean family. Only the adapter-provided identifiers, point ontology, source timestamps, and values differ.

## Porting Another Corpus

A new continuous telemetry corpus must provide:

1. stable site and stream identifiers;
2. UTC-normalizable timestamps and numeric values;
3. point classes and enough equipment/location metadata for the selected families;
4. an explicit held-out split policy;
5. a declared family subset supported by its schema.

The reusable pipeline begins after the adapter emits normalized metadata and observation tables. A corpus with event causality, incident narratives, action side effects, or manufacturing state transitions would require new tools, family contracts, and evaluators. This artifact does not describe that work as configuration-only portability.

## Entry Points

Build only the normalized tool store and static family subset:

```bash
python scripts/build_xai4heat_benchmark.py \
  --raw-dir /absolute/path/to/xai4heat \
  --tool-store-dir ./data/local-build/xai4heat/tool-store \
  --benchmark-dir ./data/local-build/xai4heat/static
```

Build the full episode artifact:

```bash
python scripts/build_xai4heat_final_canonical.py \
  --raw-dir /absolute/path/to/xai4heat \
  --tool-store-dir ./data/local-build/xai4heat/tool-store \
  --static-dir ./data/local-build/xai4heat/static \
  --e2e-out-dir ./data/local-build/xai4heat/interaction-contract \
  --agentic-out-dir ./data/local-build/xai4heat/operator-surface \
  --canonical-seed-out-dir ./data/local-build/xai4heat/canonical-seed \
  --canonical-seed-core-out-dir ./data/local-build/xai4heat/canonical-seed-core \
  --final-out-dir ./data/local-build/xai4heat/final \
  --rebuild-static \
  --controller-split test
```

The complete XAI4HEAT rows and retained GPT trace are included in `dist/dataset.zip` and checked into `artifacts/xai4heat-agentbench/` and `reports/model-runs/gpt-5.5-xai4heat/`.

Run the matching retained configuration after construction:

```bash
export XAI4HEAT_TOOL_STORE_DB="$PWD/data/local-build/xai4heat/tool-store/tool_store.duckdb"
export XAI4HEAT_BENCHMARK_DIR="$PWD/data/local-build/xai4heat/final"
bash runners/gpt55_xai4heat.sh
```
