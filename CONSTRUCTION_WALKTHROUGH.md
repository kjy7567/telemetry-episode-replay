# Construction Walkthrough

This document shows how fixed source records become an executable multi-turn episode without model-generated tasks or labels. The example is the released BTS test row `test_timestamp_value_lookup_00051`.

## 1. Fixed Source Inputs

The raw value is stored in:

```text
archive:      Site_Caa.zip
member:       Site_Caa/2254.pickle
stream_id:    c24589e8_a1f3_4529_b409_5a56761c9d20
point_class:  Air_Differential_Pressure_Sensor
equipment:    BTS_C Zone 005
observations: 194,563

2022-02-03T07:03:23.640000+00:00 -> 12.9457
```

The CSV/Brick metadata supplies the site, point class, equipment, and location links. The raw archive supplies the timestamp/value history. `scripts/preprocess_tool_store.py` joins them by stream ID and records the archive/member lineage in `tool_ready_points`. No label or value is inferred from text.

The complete 212-stream release lineage is in `provenance/release_stream_lineage.csv`.

## 2. Read-Only Runtime Record

Fresh raw preprocessing makes the stream available to the runtime:

```text
resolve_point(
  site_id=BTS_C,
  point_class=Air_Differential_Pressure_Sensor,
  equipment_label=BTS_C Zone 005
)
-> c24589e8_a1f3_4529_b409_5a56761c9d20
```

An exact lookup returns:

```json
{
  "requested_timestamp": "2022-02-03T07:03:23.640000+00:00",
  "observed_timestamp": "2022-02-03T07:03:23.640000+00:00",
  "value": 12.9457,
  "exact_match_found": true
}
```

The runtime also exposes aggregate, comparison, ranking, and quality operations over the same read-only store.

## 3. Static Executable Task

`generate_scenario_benchmark` first evaluates family eligibility. An exact-timestamp candidate must use an observed timestamp from the interior of a stream history. The builder then creates structured arguments and executes them before rendering the question.

```text
At 07:03 UTC on February 3, 2022, what reading did the air differential
pressure reading attached to Zone 005 report in BTS_C?
```

Its static contract is:

```text
C1 resolve_point(BTS_C, Air_Differential_Pressure_Sensor, BTS_C Zone 005)
C2 lookup_observation(C1.stream_id, 2022-02-03T07:03:23.640Z, exact)

gold value:       12.9457
gold timestamp:   2022-02-03T07:03:23.640Z
evidence stream:  c24589e8_a1f3_4529_b409_5a56761c9d20
```

The candidate's family, site, calls, gold, and evidence are hashed into its selection identity. `provenance/release_static_selection.jsonl` fixes the identity and test split. Replay must regenerate that identity exactly once.

## 4. Interaction Contract

The static task already defines what must be computed. `build_bts_e2e` adds an interaction topology. For this family it first creates a direct answer followed by an evidence request:

```text
initial request -> exact lookup answer -> evidence follow-up
```

`build_bts_e2e_agentic` then applies the declared missing-time rule. It can withhold the timestamp because the static task contains one recoverable `time_reference` value. The generated initial request is:

```text
Operator handoff: "What was the air differential pressure reading on
Zone 005 in BTS_C?" Use the building tools and ask me for any missing
site or time detail before querying.
```

If the agent asks for time, the deterministic simulator reads the stored slot and returns:

```text
I mean 07:03:23.64 UTC on February 3, 2022.
```

The simulator does not paraphrase or generate a new value. It exposes a field that the transformation deliberately withheld.

## 5. Programmatic Phase Composition

The final builder evaluates additional family predicates and composes four phases.

### P1: exact source task

The recovered timestamp restores the original static operation:

```text
lookup_observation(2022-02-03T07:03:23.640Z, exact)
-> 12.9457 at 07:03:23.640Z
```

### P2: exact-to-nearest timestamp policy

The next operator turn narrows the public time to `07:03` and asks for the nearest reading. The contract permits an exact probe followed by nearest fallback:

```text
lookup_observation(2022-02-03T07:03:00Z, exact)
-> no exact observation

lookup_observation(2022-02-03T07:03:00Z, nearest)
-> 12.9457 at 07:03:23.640Z; offset 23.64 seconds
```

This phase is not filled from the earlier answer. It is executed against the runtime with the revised timestamp and mode.

### P3: quality decision

The operator asks whether the signal supports reporting for the week beginning January 31. The builder derives a UTC week window and executes:

```text
inspect_quality_window(
  2022-01-31T00:00:00Z,
  2022-02-07T00:00:00Z,
  week
)
-> observed_fraction=1.0, gap_ratio=1.0563, decision=answer
```

### P4: reporting commitment

The terminal turn asks whether to report, abstain, or request more time. A fixed reporting policy consumes the timestamp and quality phase fields:

```json
{
  "commitment_action": "answer",
  "reason": "nearest_but_acceptable"
}
```

The evidence follow-up still requires the original source stream.

## 6. Typed Repair

Typed repair keeps a composed episode internally executable. It is not manual editing and it does not alter source observations.

A repair rule has four parts:

1. **Predicate:** family, phase shape, and required fields that permit the rule.
2. **Transformation:** a bounded change such as aligning a revised window, replacing a point target, adding exact-to-nearest fallback, or attaching a reporting commitment.
3. **Re-execution:** if tool arguments change, the affected tool operation runs again against `ToolStoreRuntime`.
4. **Coupled update:** calls, phase gold, final target, evidence, and verifier are replaced together, then the before/after contract summary is appended to `generation_history`.

For this row, the transformation history records the original static task, E2E contract, missing-time surface, timestamp policy, multi-axis phase composition, reportability alignment, and final family alignment. A surface-only wording normalization leaves tool outputs unchanged. A timestamp or window change cannot reuse an old gold; it must re-execute the runtime operation.

This discipline is what makes the conversion programmatic: templates control language, typed fields control arguments, and executable outputs control labels.

## 7. Contract Preflight

`audit_bts_canonical_contract.py` checks the completed row before release:

- phase count agrees with the user-turn sequence;
- the final phase is the row's final target;
- required fields and verifier specifications are nonempty;
- the scorer accepts each rendered gold answer;
- exact/nearest timestamps, offsets, values, and fallback flags agree with runtime execution;
- quality windows and reporting decisions agree with their source phases;
- evidence IDs agree with the contributing tool path;
- prompt revisions align with phase order.

The release reports zero coded findings across 532 BTS rows. This means the rows passed these declared checks; it is not a claim about unrestricted operator language.

## 8. Retained Agent Execution

The GPT-5.5 trace for this row follows the contract:

```text
ask missing timestamp
  -> resolve point
  -> exact lookup at 07:03:23.640
  -> nearest lookup at 07:03
  -> inspect weekly quality
  -> answer with nearest qualification
  -> cite source stream
```

The retained final answer is:

```text
Report it as-is, but label it as the nearest available reading—not an exact
07:03:00 reading. Data quality for that week supports answering, so no need
to abstain or ask for more time detail.
```

The evidence response cites `c24589e8_a1f3_4529_b409_5a56761c9d20`. Deterministic rescoring yields `final=1`, `phase=1`, `evidence=1`, `task=1`, `protocol_ok=true`, and `label=accomplished`.

The full 21-message exchange, including tool-result JSON, is in `examples/REPLAY_TRACE.md`.

## 9. Replay Equality

Two independent raw preprocessing runs rebuilt the tool store. Each complete downstream build regenerated this row and all other rows. For this example, the public release row and both replay rows have the same canonical object digest, and their containing split files match the release SHA-256.

The equality check includes every turn, call, phase target, final target, evidence field, verifier, history entry, provenance field, and serialized byte. The expected result is visible in the worked trace:

```text
Complete JSON-object equality: YES
```
