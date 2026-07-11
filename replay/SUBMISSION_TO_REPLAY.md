# Submitted Snapshot to Replayed Release Audit

This document separates the benchmark supplied with the paper from the later reproducibility-maintenance release. Replay is an artifact-construction result, not a second model evaluation.

## Evidence Chain

```text
checksummed raw BTS archives
  -> rebuilt read-only tool store
  -> retained static executable tasks
  -> clean replay A and clean replay B
  -> reproducibility-maintenance release

submitted dataset.zip -----------------------> semantic comparison
```

The submitted ZIP is preserved byte-for-byte. The semantic comparison excludes only the top-level `metadata`, `generation_history`, and `agentic_lifting` provenance records. It includes every evaluator-facing user turn, tool path, phase target, final gold, evidence field, verifier, split, and release-filter field.

## Snapshot Results

- Submitted dataset bundle SHA-256: `70ad2e641a2332fe94a5d81e612279ba9f8e90914fa605b083c8441a2ab01f76`
- Scenario ID sets preserved: `true` (532/532)
- Exact full-object equality, submitted versus maintained: 0/532
- Provenance-label-only changes: 522/532
- Evaluator-facing semantic equality: 522/532
- Evaluator-facing maintenance changes: 10/532, all in train

| Split | Submitted SHA-256 | Current/A/B SHA-256 | A = B = current |
|---|---|---|:---:|
| train | `9e5afdf45faf` | `857a537cd052` | yes |
| dev | `4082a8625ede` | `58dda2cb6e09` | yes |
| test | `a79223139342` | `a1a7834c7362` | yes |

All 532 submitted objects carry legacy internal provenance labels, which were replaced by stable public artifact labels. Those label changes explain the zero full-object equality count. After removing only the three provenance records named above, 522 rows are unchanged; the remaining 10 train rows are enumerated below. Dev and test semantics are unchanged.

## Family Audit

| Family | Candidate space | Submitted T/D/Test | Semantic unchanged | Train delta | A/B/current | GPT | Gemini | Opus | Controller |
|---|---:|---:|---:|---:|:---:|---:|---:|---:|---:|
| Point disambiguation | 4,263 | 40/10/10 | 60/60 | 0 | yes | 8/10 | 5/10 | 6/10 | 0/60 |
| Day mean lookup | 2,193,431 | 40/10/10 | 60/60 | 0 | yes | 10/10 | 8/10 | 7/10 | 0/60 |
| Relative 24h mean lookup | 2,193,431 | 40/10/10 | 60/60 | 0 | yes | 10/10 | 9/10 | 9/10 | 0/60 |
| Window mean lookup | 315,929 | 36/7/10 | 53/53 | 0 | yes | 9/10 | 10/10 | 7/10 | 0/53 |
| Window pairwise compare | 5,989,083 | 40/10/10 | 52/60 | 8 | yes | 6/10 | 5/10 | 5/10 | 0/60 |
| Window rank | 1,084 | 40/10/10 | 58/60 | 2 | yes | 8/10 | 5/10 | 4/10 | 0/60 |
| Timestamp value lookup | 2,123 | 40/10/10 | 60/60 | 0 | yes | 9/10 | 10/10 | 5/10 | 0/60 |
| Timestamp nearest lookup | 2,123 | 40/10/10 | 60/60 | 0 | yes | 10/10 | 10/10 | 7/10 | 0/60 |
| Quality gate | 315 | 40/10/9 | 59/59 | 0 | yes | 9/9 | 9/9 | 8/9 | 0/59 |

## Representative Family Contracts

### Point disambiguation

- Scenario: `test_point_disambiguation_00003`
- Submitted initial request: Operator handoff: "Which stream should I use for the electrical energy sensor on Electrical Meter 042?" Use the building tools and ask me for any missing site or time detail before querying.
- Tool path: `resolve_point -> resolve_point -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Gold final answer: `{"commitment_action": "abstain", "reason": "long_gap"}`
- Semantic hashes (submitted/current/A/B): `4d874dd63ed2` / `4d874dd63ed2` / `4d874dd63ed2` / `4d874dd63ed2`
- Equal across all snapshots: `yes`

### Day mean lookup

- Scenario: `test_day_mean_lookup_00003`
- Submitted initial request: Operator handoff: "For BTS_C, what was the average electrical power reading on Supply Fan 035?" Use the building tools and ask me for any missing site or time detail before querying.
- Tool path: `resolve_point -> aggregate_window -> aggregate_window -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Gold final answer: `{"commitment_action": "abstain", "reason": "low_coverage"}`
- Semantic hashes (submitted/current/A/B): `9a6b2a521de7` / `9a6b2a521de7` / `9a6b2a521de7` / `9a6b2a521de7`
- Equal across all snapshots: `yes`

### Relative 24h mean lookup

- Scenario: `test_relative_24h_mean_lookup_00003`
- Submitted initial request: Operator handoff: "What was the average over the previous 24 hours for the electrical power reading on Supply Fan 035 in BTS_C?" Use the building tools and ask me for any missing site or time detail before querying.
- Tool path: `resolve_point -> aggregate_window -> aggregate_window -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Gold final answer: `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}`
- Semantic hashes (submitted/current/A/B): `6e2a21cc328c` / `6e2a21cc328c` / `6e2a21cc328c` / `6e2a21cc328c`
- Equal across all snapshots: `yes`

### Window mean lookup

- Scenario: `test_window_mean_lookup_00003`
- Submitted initial request: Operator handoff: "For BTS_C, what was the average electrical power measurement on Supply Fan 035?" Use the building tools and ask me for any missing site or time detail before querying.
- Tool path: `resolve_point -> aggregate_window -> aggregate_window -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Gold final answer: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Semantic hashes (submitted/current/A/B): `d0f0b5288d08` / `d0f0b5288d08` / `d0f0b5288d08` / `d0f0b5288d08`
- Equal across all snapshots: `yes`

### Window pairwise compare

- Scenario: `test_window_pairwise_compare_00003`
- Submitted initial request: Operator handoff: "In BTS_C, which side averaged higher for run time reading: Terminal Unit 001 or Floor 008?" Use the building tools and ask me for any missing site or time detail before querying.
- Tool path: `resolve_point -> resolve_point -> compare_window -> compare_window -> lookup_observation -> lookup_observation -> inspect_quality_window -> inspect_quality_window`
- Gold final answer: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Semantic hashes (submitted/current/A/B): `0fb8e50f4302` / `0fb8e50f4302` / `0fb8e50f4302` / `0fb8e50f4302`
- Equal across all snapshots: `yes`

### Window rank

- Scenario: `test_window_rank_00003`
- Submitted initial request: Operator handoff: "In BTS_C, looking across locations, which stream topped the average position sensor readings?" Use the building tools and ask me for any missing site or time detail before querying.
- Tool path: `list_points -> rank_window -> rank_window -> inspect_quality_window`
- Gold final answer: `{"commitment_action": "answer", "reason": "healthy_quality"}`
- Semantic hashes (submitted/current/A/B): `df458cd50a5b` / `df458cd50a5b` / `df458cd50a5b` / `df458cd50a5b`
- Equal across all snapshots: `yes`

### Timestamp value lookup

- Scenario: `test_timestamp_value_lookup_00051`
- Submitted initial request: Operator handoff: "What was the air differential pressure reading on Zone 005 in BTS_C?" Use the building tools and ask me for any missing site or time detail before querying.
- Tool path: `resolve_point -> lookup_observation -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Gold final answer: `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}`
- Semantic hashes (submitted/current/A/B): `b5e6fca048c2` / `b5e6fca048c2` / `b5e6fca048c2` / `b5e6fca048c2`
- Equal across all snapshots: `yes`

### Timestamp nearest lookup

- Scenario: `test_timestamp_nearest_lookup_00051`
- Submitted initial request: Ops ticket: "For BTS_C, what was the air differential pressure reading on Zone 005 at 00:21 UTC on May 22, 2020?" Use the building telemetry tools and report the logged reading you can justify.
- Tool path: `resolve_point -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Gold final answer: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Semantic hashes (submitted/current/A/B): `43110b10f372` / `43110b10f372` / `43110b10f372` / `43110b10f372`
- Equal across all snapshots: `yes`

### Quality gate

- Scenario: `test_quality_gate_00051`
- Submitted initial request: Data-quality review request: "Would you trust this signal enough for the weekly trend question about the air differential pressure measurement on Zone 005 for the week beginning May 19, 2020, or would you abstain?" If the site is missing, ask for it first; then tell me whether you would answer or abstain.
- Tool path: `resolve_point -> inspect_quality_window -> inspect_quality_window`
- Gold final answer: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Semantic hashes (submitted/current/A/B): `ca123579cd30` / `ca123579cd30` / `ca123579cd30` / `ca123579cd30`
- Equal across all snapshots: `yes`

## Enumerated Maintenance Delta

### Pairwise deterministic ordering

Eight training rows changed when stream IDs were added as the final ordering key for equal-gap candidate pairs. No dev or test row changed.

| Scenario | Submitted point pair | Maintained point pair |
|---|---|---|
| `train_window_pairwise_compare_00045` | BTS_B Cooling Valve 010 vs. BTS_B Heating Valve 001 | BTS_B Heating Valve 016 vs. BTS_B Cooling Valve 010 |
| `train_window_pairwise_compare_00047` | BTS_B Cooling Valve 010 vs. BTS_B Heating Valve 005 | BTS_B Heating Valve 007 vs. BTS_B Cooling Valve 010 |
| `train_window_pairwise_compare_00048` | BTS_B Cooling Valve 005 vs. BTS_B Cooling Valve 001 | BTS_B Heating Valve 007 vs. BTS_B Cooling Valve 005 |
| `train_window_pairwise_compare_00049` | BTS_B Cooling Valve 010 vs. BTS_B Cooling Valve 017 | BTS_B Cooling Valve 010 vs. BTS_B Cooling Valve 006 |
| `train_window_pairwise_compare_00054` | BTS_B Cooling Valve 010 vs. BTS_B Heating Valve 014 | BTS_B Cooling Valve 004 vs. BTS_B Heating Valve 014 |
| `train_window_pairwise_compare_00055` | BTS_B Cooling Valve 005 vs. BTS_B Heating Valve 014 | BTS_B Cooling Valve 004 vs. BTS_B Heating Valve 002 |
| `train_window_pairwise_compare_00056` | BTS_B Heating Valve 002 vs. BTS_B Cooling Valve 001 | BTS_B Cooling Valve 009 vs. BTS_B Heating Valve 002 |
| `train_window_pairwise_compare_00057` | BTS_B Heating Valve 014 vs. BTS_B Cooling Valve 016 | BTS_B Heating Valve 007 vs. BTS_B Heating Valve 014 |

### Rank month-direction alignment

Two January training rows already executed a next-month fallback because the preceding month had no data, while their submitted revision text said `previous month`. The maintained rows preserve the executable windows and change the text to `next month`.

| Scenario | Submitted revision | Maintained revision |
|---|---|---|
| `train_window_rank_00011` | Now keep the same candidate group and site, but rank the previous month. | Now keep the same candidate group and site, but rank the next month. |
| `train_window_rank_00050` | Now keep the same candidate group and site, but rank the previous month. | Now keep the same candidate group and site, but rank the next month. |

## Reproduce This Audit

After rebuilding the raw tool store and running `scripts/replay_release.py`, execute:

```bash
python scripts/build_submission_replay_audit.py
```

The command exits on a submitted-ZIP hash mismatch, scenario-set mismatch, family replay mismatch, or split replay mismatch, and rewrites both this Markdown file and the machine-readable JSON report.

## Interpretation

The submitted benchmark and maintained release have identical dev/test evaluator semantics, so the submitted 89-row model traces remain attached to the same test contracts. The current construction pipeline reproducibly generates the maintained release from the retained static layer and the newly rebuilt tool store. It does not claim that the submitted ZIP and maintained release are byte-identical; their provenance labels differ throughout, and the 10 disclosed train contracts were deliberately corrected.
