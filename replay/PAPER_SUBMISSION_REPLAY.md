# Exact Paper-Submission Replay

This report compares the immutable benchmark supplied with the paper against fresh deterministic reconstructions. Model APIs are not called.

## Plain-Language Result

The reconstruction starts from raw timestamp/value archives and the retained semantic mapping, rebuilds the read-only telemetry database, reruns every family builder, and reconstructs every submitted interaction contract.

- All 532 static tasks were regenerated from telemetry-backed candidates.
- All 532 final episodes matched the submitted rows as complete JSON objects in Replay 1 and Replay 2.
- The equality check covers user turns, clarification answers, goal revisions, tool calls, phase golds, final actions, evidence, verifiers, provenance, and serialization.
- Both replay runs completed with zero coded preflight issues.
- A full raw-to-static-to-agentic example is shown in [`examples/REPLAY_TRACE.md`](../examples/REPLAY_TRACE.md).

## Integrity Identifiers

- Overall replay passed: `true`
- Construction runs: `2`
- Cross-run byte equality: `true`
- Selection contract SHA-256: `2487dce6bbb01bb0ab4e1d5b388ff40cc509ab26082770c867ed085e96ecddd6`
- Submitted source bundle SHA-256: `9c5502b3718113fee812818e71b47c23ed54ec1f694d37c35ea29686b2c64496`
- Submitted dataset bundle SHA-256: `70ad2e641a2332fe94a5d81e612279ba9f8e90914fa605b083c8441a2ab01f76`

## Verified Raw Boundary

The two episode builds shared one fresh read-only tool store reconstructed from the following checksummed raw telemetry archives and retained normalized metadata contract.

| Raw archive | Bytes | SHA-256 |
|---|---:|---|
| `Site_Aaa.zip` | 8,475,679,488 | `ffc13b3710c66de505678cf5b48e8c7b3d5be97900653c82f48c2f5dfec7e77f` |
| `Site_Baa.zip` | 1,513,172,125 | `fade67675e97274075e003c27e411eadc50f17c5fe0cb294bd3569388a517ef8` |
| `Site_Caa.zip` | 8,984,334,527 | `fa03a0629fb1da4eb9ef3c430546311470fc9bd8f5e53cfcd76853d535676b5b` |

| Retained catalog file | Bytes | SHA-256 |
|---|---:|---|
| `catalog_summary.json` | 248 | `a9ebb46dc7293fc17230d7d26a8f00b847b23136a6cbf29749db2b547cfcb722` |
| `entities.parquet` | 3,151,084 | `fb63ebf4b4cfd63893fa508afacafa1240f2636a9e3a7c0c5725407958613731` |
| `relations.parquet` | 1,770,519 | `4a512fc1bdcc93dbf3e822458eb9193cb576dfadafe45f44d4e7298607d530d3` |
| `stream_targets.parquet` | 3,706,992 | `7785971531d03a9f80e2c9620fc34d7c828f9892adbad962d8820b3c95599994` |
| `streams.parquet` | 4,222,572 | `3e848eb68be296ca39756aeb9ecb6ea5038ab9788df272be898fc27c822e23b7` |

| Site | Streams processed | Skipped archive members |
|---|---:|---:|
| `BTS_A` | 8,345 | 0 |
| `BTS_B` | 730 | 14 |
| `BTS_C` | 5,347 | 0 |

The fresh tool store matched `14,422` raw streams to the retained metadata contract. The `14` skipped members are AppleDouble archive metadata rather than retained telemetry streams.

The normalized metadata mapping is retained and checksummed as the versioned input contract used by the paper release. `scripts/build_catalog.py` remains available for compiling metadata mappings for new releases.

## Independent Reconstruction Evidence

Two independent raw telemetry preprocessing executions were compared before downstream episode verification.

- Raw archive inventories equal: `true`
- Byte-identical exported logical tool-store files: `11/11`
- Exact static-to-final episode builds across both raw stores: `3`
- Independent replay report SHA-256: `0744d0d0aade92f3f678f303a4a6e0d1a3d5c88a8d1e7a39461b5ce78c7cfefa`

The two DuckDB container files are not byte-identical because their physical storage layout is not a canonical serialization. They are excluded from the determinism decision; the sorted exported tables are byte-identical, and both stores produce the same submitted static and final split hashes.

## Split Hashes

| Split | Static rows | Static submitted/replay SHA-256 | Final rows | Final submitted/replay SHA-256 | Exact |
|---|---:|---|---:|---|:---:|
| Train | 356 | `65f0384bf97318b628ff9431c8bdbd36a2347fcb0ee4a521169fbf3a22b7d825` | 356 | `9e5afdf45fafcd28c408d131216950800717a891b2e530eae15c645db7720a65` | yes |
| Dev | 87 | `294f394147a27eba052d1421b8cff5814cdeeab8246194670a7b4a5b93c72b8d` | 87 | `4082a8625ede78bb7528bf544fc8e896bff9dfa929d5f999dad5b64a557339d6` | yes |
| Test | 89 | `1f561e93dbcd748bc1f94aa00827512869c8e6e220b15b3943f6c8a5af45120e` | 89 | `a7922313934258dce878a8218ce5bfb87b8628be639a52d279fd5a38304d3867` | yes |

## Family-Wise Submitted vs Replay

The final action below is shown directly so that replay is visible without interpreting a hash. `Exact rows` counts complete JSON-object equality, including user turns, calls, phase targets, evidence, verifiers, provenance, and metadata.

| Family | Rows train/dev/test | Representative submitted final | Replay 1 final | Replay 2 final | Exact rows in both replays |
|---|---:|---|---|---|---:|
| Point disambiguation | 40/10/10 | `{"commitment_action": "abstain", "reason": "long_gap"}` | `{"commitment_action": "abstain", "reason": "long_gap"}` | `{"commitment_action": "abstain", "reason": "long_gap"}` | 60/60; 60/60 |
| Day mean lookup | 40/10/10 | `{"commitment_action": "abstain", "reason": "low_coverage"}` | `{"commitment_action": "abstain", "reason": "low_coverage"}` | `{"commitment_action": "abstain", "reason": "low_coverage"}` | 60/60; 60/60 |
| Relative 24h mean lookup | 40/10/10 | `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}` | `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}` | `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}` | 60/60; 60/60 |
| Window mean lookup | 36/7/10 | `{"commitment_action": "abstain", "reason": "marginal_quality"}` | `{"commitment_action": "abstain", "reason": "marginal_quality"}` | `{"commitment_action": "abstain", "reason": "marginal_quality"}` | 53/53; 53/53 |
| Window pairwise compare | 40/10/10 | `{"commitment_action": "abstain", "reason": "marginal_quality"}` | `{"commitment_action": "abstain", "reason": "marginal_quality"}` | `{"commitment_action": "abstain", "reason": "marginal_quality"}` | 60/60; 60/60 |
| Window rank | 40/10/10 | `{"commitment_action": "answer", "reason": "healthy_quality"}` | `{"commitment_action": "answer", "reason": "healthy_quality"}` | `{"commitment_action": "answer", "reason": "healthy_quality"}` | 60/60; 60/60 |
| Timestamp value lookup | 40/10/10 | `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}` | `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}` | `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}` | 60/60; 60/60 |
| Timestamp nearest lookup | 40/10/10 | `{"commitment_action": "abstain", "reason": "marginal_quality"}` | `{"commitment_action": "abstain", "reason": "marginal_quality"}` | `{"commitment_action": "abstain", "reason": "marginal_quality"}` | 60/60; 60/60 |
| Quality gate | 40/10/9 | `{"commitment_action": "abstain", "reason": "marginal_quality"}` | `{"commitment_action": "abstain", "reason": "marginal_quality"}` | `{"commitment_action": "abstain", "reason": "marginal_quality"}` | 59/59; 59/59 |

## Frozen Controller Audit

The frozen construction-exclusion controller was rerun against Replay 1 after its final JSONL files had matched the submitted files byte for byte.

- Scenarios audited: `532`
- Accomplished: `0`
- Audit report SHA-256: `d9ea6de7c9db1448dab3702d44217897da1d7e4e2a6a1359aeb4a8022ece41b7`

| Split | Rows | Accomplished |
|---|---:|---:|
| Train | 356 | 0 |
| Dev | 87 | 0 |
| Test | 89 | 0 |

This confirms that the predefined exclusion rule remains satisfied. It is not reported as an independent estimate of benchmark difficulty or as a model baseline.

## Representative Family Traces

### Point disambiguation

- Scenario: `test_point_disambiguation_00003`
- Submitted final gold: `{"commitment_action": "abstain", "reason": "long_gap"}`
- Replay 1 final gold: `{"commitment_action": "abstain", "reason": "long_gap"}`
- Replay 2 final gold: `{"commitment_action": "abstain", "reason": "long_gap"}`
- Gold tool path in submitted and replayed rows: `resolve_point -> resolve_point -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Phase gold trace identical: `yes` (5 phases)
- Gold tool trace identical: `yes`
- Complete submitted/replay row equality: `yes`
- Secondary integrity digest shared by all copies: `11ec65d2ae9b4905575b5ef6a01886ed4cdbdcb88e497daf24ce583aaa96798f`
- Retained static identity: `251848beaae3136cff5fe2460d3221bca071fe4883ee138eef5db56f080feb5c`

Initial request:

> Operator handoff: "Which stream should I use for the electrical energy sensor on Electrical Meter 042?" Use the building tools and ask me for any missing site or time detail before querying.

### Day mean lookup

- Scenario: `test_day_mean_lookup_00003`
- Submitted final gold: `{"commitment_action": "abstain", "reason": "low_coverage"}`
- Replay 1 final gold: `{"commitment_action": "abstain", "reason": "low_coverage"}`
- Replay 2 final gold: `{"commitment_action": "abstain", "reason": "low_coverage"}`
- Gold tool path in submitted and replayed rows: `resolve_point -> aggregate_window -> aggregate_window -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Phase gold trace identical: `yes` (5 phases)
- Gold tool trace identical: `yes`
- Complete submitted/replay row equality: `yes`
- Secondary integrity digest shared by all copies: `8181bdfd554c43a1c7cfaee110bc766e8e98d170a037b69515128b8f0da1d177`
- Retained static identity: `dbef22b11e52b6c13718c0360e9e96f02e3b25dae061bf3a2e868abf29a9838c`

Initial request:

> Operator handoff: "For BTS_C, what was the average electrical power reading on Supply Fan 035?" Use the building tools and ask me for any missing site or time detail before querying.

### Relative 24h mean lookup

- Scenario: `test_relative_24h_mean_lookup_00003`
- Submitted final gold: `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}`
- Replay 1 final gold: `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}`
- Replay 2 final gold: `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}`
- Gold tool path in submitted and replayed rows: `resolve_point -> aggregate_window -> aggregate_window -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Phase gold trace identical: `yes` (5 phases)
- Gold tool trace identical: `yes`
- Complete submitted/replay row equality: `yes`
- Secondary integrity digest shared by all copies: `f148444668c085e64940471222f2f154cb31ccb58c37d9ce80ea4fda21722ebc`
- Retained static identity: `0469215db1dc7a7788bc5f83a974a569a506bc02da0a5821bd229048d8a664f5`

Initial request:

> Operator handoff: "What was the average over the previous 24 hours for the electrical power reading on Supply Fan 035 in BTS_C?" Use the building tools and ask me for any missing site or time detail before querying.

### Window mean lookup

- Scenario: `test_window_mean_lookup_00003`
- Submitted final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Replay 1 final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Replay 2 final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Gold tool path in submitted and replayed rows: `resolve_point -> aggregate_window -> aggregate_window -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Phase gold trace identical: `yes` (5 phases)
- Gold tool trace identical: `yes`
- Complete submitted/replay row equality: `yes`
- Secondary integrity digest shared by all copies: `084786d3802a15319b3621bf89efd40ba0d86d779c077faf52b0b2d2234779df`
- Retained static identity: `fc1cf101e10a9aa6b54d7fb98f9486d14995998d2f8e59e670a8963ec5baa3a2`

Initial request:

> Operator handoff: "For BTS_C, what was the average electrical power measurement on Supply Fan 035?" Use the building tools and ask me for any missing site or time detail before querying.

### Window pairwise compare

- Scenario: `test_window_pairwise_compare_00003`
- Submitted final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Replay 1 final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Replay 2 final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Gold tool path in submitted and replayed rows: `resolve_point -> resolve_point -> compare_window -> compare_window -> lookup_observation -> lookup_observation -> inspect_quality_window -> inspect_quality_window`
- Phase gold trace identical: `yes` (5 phases)
- Gold tool trace identical: `yes`
- Complete submitted/replay row equality: `yes`
- Secondary integrity digest shared by all copies: `a336e3b427aad307db61945ec957cbaa39e0805fecda869033da95ae552373cd`
- Retained static identity: `e8f9cfe64c582a6b8e80000c9fd0a9fca5c18e9e4c5ff6ae9b9ee9d9c9835918`

Initial request:

> Operator handoff: "In BTS_C, which side averaged higher for run time reading: Terminal Unit 001 or Floor 008?" Use the building tools and ask me for any missing site or time detail before querying.

### Window rank

- Scenario: `test_window_rank_00003`
- Submitted final gold: `{"commitment_action": "answer", "reason": "healthy_quality"}`
- Replay 1 final gold: `{"commitment_action": "answer", "reason": "healthy_quality"}`
- Replay 2 final gold: `{"commitment_action": "answer", "reason": "healthy_quality"}`
- Gold tool path in submitted and replayed rows: `list_points -> rank_window -> rank_window -> inspect_quality_window`
- Phase gold trace identical: `yes` (5 phases)
- Gold tool trace identical: `yes`
- Complete submitted/replay row equality: `yes`
- Secondary integrity digest shared by all copies: `8ef3ed32625b1c799e9a62c71df9357290f6179456916ad30c16e1e000bbe807`
- Retained static identity: `4aafec087baec84f0753f7bf6028739c704c12ef24cab5446e03dbf47d7f0d56`

Initial request:

> Operator handoff: "In BTS_C, looking across locations, which stream topped the average position sensor readings?" Use the building tools and ask me for any missing site or time detail before querying.

### Timestamp value lookup

- Scenario: `test_timestamp_value_lookup_00051`
- Submitted final gold: `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}`
- Replay 1 final gold: `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}`
- Replay 2 final gold: `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}`
- Gold tool path in submitted and replayed rows: `resolve_point -> lookup_observation -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Phase gold trace identical: `yes` (4 phases)
- Gold tool trace identical: `yes`
- Complete submitted/replay row equality: `yes`
- Secondary integrity digest shared by all copies: `be81122fc2edac119aee6a3b8a08dfd838149e3abcb354de32530fc73ddf8510`
- Retained static identity: `ecede82e935558e3ba0e0ee7cf723df3a0575dd6602803864ac78915aea62f6d`

Initial request:

> Operator handoff: "What was the air differential pressure reading on Zone 005 in BTS_C?" Use the building tools and ask me for any missing site or time detail before querying.

### Timestamp nearest lookup

- Scenario: `test_timestamp_nearest_lookup_00051`
- Submitted final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Replay 1 final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Replay 2 final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Gold tool path in submitted and replayed rows: `resolve_point -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Phase gold trace identical: `yes` (3 phases)
- Gold tool trace identical: `yes`
- Complete submitted/replay row equality: `yes`
- Secondary integrity digest shared by all copies: `8b92689727342a0b14aceb3769d1258aa549548312d919eeb16ae48256fcc3bf`
- Retained static identity: `eb9abbd778d2d4a7a3b8ef2ff265f0d4ec22963c116e2fc64484b7892f0c7758`

Initial request:

> Ops ticket: "For BTS_C, what was the air differential pressure reading on Zone 005 at 00:21 UTC on May 22, 2020?" Use the building telemetry tools and report the logged reading you can justify.

### Quality gate

- Scenario: `test_quality_gate_00051`
- Submitted final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Replay 1 final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Replay 2 final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Gold tool path in submitted and replayed rows: `resolve_point -> inspect_quality_window -> inspect_quality_window`
- Phase gold trace identical: `yes` (4 phases)
- Gold tool trace identical: `yes`
- Complete submitted/replay row equality: `yes`
- Secondary integrity digest shared by all copies: `a870bba49fbc149603fa38589b7aef82363305fdbc1aeab94523f068985eb96c`
- Retained static identity: `1b8da74bbe9b2b792faf4afa8a3b37f52dd79e760209cbaf3b6a2eb5ee2b990e`

Initial request:

> Data-quality review request: "Would you trust this signal enough for the weekly trend question about the air differential pressure measurement on Zone 005 for the week beginning May 19, 2020, or would you abstain?" If the site is missing, ask for it first; then tell me whether you would answer or abstain.

## What Was Recomputed

Each run re-enumerated family candidates from the supplied tool store, matched all 532 frozen static identities exactly once, rebuilt E2E and operator surfaces, executed telemetry tools for phase targets, applied typed family repairs, and ran contract preflight. The orchestrator loaded the submitted bundle as expected output, but did not pass those rows to any builder; comparison occurred only after each reconstructed split had been written.

When the report's tool-store mode is `raw_archives_to_fresh_tool_store`, the replay first verifies the retained normalized metadata contract and then reruns raw telemetry preprocessing before both episode builds. Metadata normalization itself is an upstream, checksummed boundary rather than a claimed cross-environment replay step.

## Compatibility Boundary

The exact replay preserves the paper snapshot, including two January training rank rows whose visible month direction was corrected only in the later maintenance path. Dev and test are unaffected by that maintenance issue. Exact replay and maintenance output are deliberately separate.

## Reproduce

```bash
python scripts/replay_paper_submission.py \
  --raw-dir /absolute/path/to/bts/raw \
  --work-dir ./data/local-build/paper-submission-replay \
  --runs 2
```

See `CONSTRUCTION_WALKTHROUGH.md` for the raw-member-to-static-to-agentic field-level derivation.
