# Exact Paper-Submission Replay

This report compares the immutable benchmark supplied with the paper against fresh deterministic reconstructions. Model APIs are not called.

## Result

- Overall replay passed: `true`
- Construction runs: `1`
- Cross-run byte equality: `true`
- Selection contract SHA-256: `2487dce6bbb01bb0ab4e1d5b388ff40cc509ab26082770c867ed085e96ecddd6`
- Submitted source bundle SHA-256: `9c5502b3718113fee812818e71b47c23ed54ec1f694d37c35ea29686b2c64496`
- Submitted dataset bundle SHA-256: `70ad2e641a2332fe94a5d81e612279ba9f8e90914fa605b083c8441a2ab01f76`

## Split Hashes

| Split | Static rows | Static submitted/replay SHA-256 | Final rows | Final submitted/replay SHA-256 | Exact |
|---|---:|---|---:|---|:---:|
| Train | 356 | `65f0384bf97318b628ff9431c8bdbd36a2347fcb0ee4a521169fbf3a22b7d825` | 356 | `9e5afdf45fafcd28c408d131216950800717a891b2e530eae15c645db7720a65` | yes |
| Dev | 87 | `294f394147a27eba052d1421b8cff5814cdeeab8246194670a7b4a5b93c72b8d` | 87 | `4082a8625ede78bb7528bf544fc8e896bff9dfa929d5f999dad5b64a557339d6` | yes |
| Test | 89 | `1f561e93dbcd748bc1f94aa00827512869c8e6e220b15b3943f6c8a5af45120e` | 89 | `a7922313934258dce878a8218ce5bfb87b8628be639a52d279fd5a38304d3867` | yes |

## Family-Wise Submitted vs Replay

`Exact rows` counts complete JSON-object equality, including user turns, calls, phase targets, evidence, verifiers, provenance, and metadata.

| Family | Submitted train/dev/test | Submitted rows | Replay 1 exact | Replay 2 exact |
|---|---:|---:|---:|---:|
| Point disambiguation | 40/10/10 | 60 | 60/60 | not run |
| Day mean lookup | 40/10/10 | 60 | 60/60 | not run |
| Relative 24h mean lookup | 40/10/10 | 60 | 60/60 | not run |
| Window mean lookup | 36/7/10 | 53 | 53/53 | not run |
| Window pairwise compare | 40/10/10 | 60 | 60/60 | not run |
| Window rank | 40/10/10 | 60 | 60/60 | not run |
| Timestamp value lookup | 40/10/10 | 60 | 60/60 | not run |
| Timestamp nearest lookup | 40/10/10 | 60 | 60/60 | not run |
| Quality gate | 40/10/9 | 59 | 59/59 | not run |

## Representative Family Traces

### Point disambiguation

- Scenario: `test_point_disambiguation_00003`
- Retained static identity: `251848beaae3136cff5fe2460d3221bca071fe4883ee138eef5db56f080feb5c`
- Tool path: `resolve_point -> resolve_point -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Phase count: `5`
- Final gold: `{"commitment_action": "abstain", "reason": "long_gap"}`
- Submitted row digest: `11ec65d2ae9b4905575b5ef6a01886ed4cdbdcb88e497daf24ce583aaa96798f`
- Replay 1 row digest: `11ec65d2ae9b4905575b5ef6a01886ed4cdbdcb88e497daf24ce583aaa96798f`
- All available digests equal: `yes`

Initial request:

> Operator handoff: "Which stream should I use for the electrical energy sensor on Electrical Meter 042?" Use the building tools and ask me for any missing site or time detail before querying.

### Day mean lookup

- Scenario: `test_day_mean_lookup_00003`
- Retained static identity: `dbef22b11e52b6c13718c0360e9e96f02e3b25dae061bf3a2e868abf29a9838c`
- Tool path: `resolve_point -> aggregate_window -> aggregate_window -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Phase count: `5`
- Final gold: `{"commitment_action": "abstain", "reason": "low_coverage"}`
- Submitted row digest: `8181bdfd554c43a1c7cfaee110bc766e8e98d170a037b69515128b8f0da1d177`
- Replay 1 row digest: `8181bdfd554c43a1c7cfaee110bc766e8e98d170a037b69515128b8f0da1d177`
- All available digests equal: `yes`

Initial request:

> Operator handoff: "For BTS_C, what was the average electrical power reading on Supply Fan 035?" Use the building tools and ask me for any missing site or time detail before querying.

### Relative 24h mean lookup

- Scenario: `test_relative_24h_mean_lookup_00003`
- Retained static identity: `0469215db1dc7a7788bc5f83a974a569a506bc02da0a5821bd229048d8a664f5`
- Tool path: `resolve_point -> aggregate_window -> aggregate_window -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Phase count: `5`
- Final gold: `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}`
- Submitted row digest: `f148444668c085e64940471222f2f154cb31ccb58c37d9ce80ea4fda21722ebc`
- Replay 1 row digest: `f148444668c085e64940471222f2f154cb31ccb58c37d9ce80ea4fda21722ebc`
- All available digests equal: `yes`

Initial request:

> Operator handoff: "What was the average over the previous 24 hours for the electrical power reading on Supply Fan 035 in BTS_C?" Use the building tools and ask me for any missing site or time detail before querying.

### Window mean lookup

- Scenario: `test_window_mean_lookup_00003`
- Retained static identity: `fc1cf101e10a9aa6b54d7fb98f9486d14995998d2f8e59e670a8963ec5baa3a2`
- Tool path: `resolve_point -> aggregate_window -> aggregate_window -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Phase count: `5`
- Final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Submitted row digest: `084786d3802a15319b3621bf89efd40ba0d86d779c077faf52b0b2d2234779df`
- Replay 1 row digest: `084786d3802a15319b3621bf89efd40ba0d86d779c077faf52b0b2d2234779df`
- All available digests equal: `yes`

Initial request:

> Operator handoff: "For BTS_C, what was the average electrical power measurement on Supply Fan 035?" Use the building tools and ask me for any missing site or time detail before querying.

### Window pairwise compare

- Scenario: `test_window_pairwise_compare_00003`
- Retained static identity: `e8f9cfe64c582a6b8e80000c9fd0a9fca5c18e9e4c5ff6ae9b9ee9d9c9835918`
- Tool path: `resolve_point -> resolve_point -> compare_window -> compare_window -> lookup_observation -> lookup_observation -> inspect_quality_window -> inspect_quality_window`
- Phase count: `5`
- Final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Submitted row digest: `a336e3b427aad307db61945ec957cbaa39e0805fecda869033da95ae552373cd`
- Replay 1 row digest: `a336e3b427aad307db61945ec957cbaa39e0805fecda869033da95ae552373cd`
- All available digests equal: `yes`

Initial request:

> Operator handoff: "In BTS_C, which side averaged higher for run time reading: Terminal Unit 001 or Floor 008?" Use the building tools and ask me for any missing site or time detail before querying.

### Window rank

- Scenario: `test_window_rank_00003`
- Retained static identity: `4aafec087baec84f0753f7bf6028739c704c12ef24cab5446e03dbf47d7f0d56`
- Tool path: `list_points -> rank_window -> rank_window -> inspect_quality_window`
- Phase count: `5`
- Final gold: `{"commitment_action": "answer", "reason": "healthy_quality"}`
- Submitted row digest: `8ef3ed32625b1c799e9a62c71df9357290f6179456916ad30c16e1e000bbe807`
- Replay 1 row digest: `8ef3ed32625b1c799e9a62c71df9357290f6179456916ad30c16e1e000bbe807`
- All available digests equal: `yes`

Initial request:

> Operator handoff: "In BTS_C, looking across locations, which stream topped the average position sensor readings?" Use the building tools and ask me for any missing site or time detail before querying.

### Timestamp value lookup

- Scenario: `test_timestamp_value_lookup_00051`
- Retained static identity: `ecede82e935558e3ba0e0ee7cf723df3a0575dd6602803864ac78915aea62f6d`
- Tool path: `resolve_point -> lookup_observation -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Phase count: `4`
- Final gold: `{"commitment_action": "answer", "reason": "nearest_but_acceptable"}`
- Submitted row digest: `be81122fc2edac119aee6a3b8a08dfd838149e3abcb354de32530fc73ddf8510`
- Replay 1 row digest: `be81122fc2edac119aee6a3b8a08dfd838149e3abcb354de32530fc73ddf8510`
- All available digests equal: `yes`

Initial request:

> Operator handoff: "What was the air differential pressure reading on Zone 005 in BTS_C?" Use the building tools and ask me for any missing site or time detail before querying.

### Timestamp nearest lookup

- Scenario: `test_timestamp_nearest_lookup_00051`
- Retained static identity: `eb9abbd778d2d4a7a3b8ef2ff265f0d4ec22963c116e2fc64484b7892f0c7758`
- Tool path: `resolve_point -> lookup_observation -> lookup_observation -> inspect_quality_window`
- Phase count: `3`
- Final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Submitted row digest: `8b92689727342a0b14aceb3769d1258aa549548312d919eeb16ae48256fcc3bf`
- Replay 1 row digest: `8b92689727342a0b14aceb3769d1258aa549548312d919eeb16ae48256fcc3bf`
- All available digests equal: `yes`

Initial request:

> Ops ticket: "For BTS_C, what was the air differential pressure reading on Zone 005 at 00:21 UTC on May 22, 2020?" Use the building telemetry tools and report the logged reading you can justify.

### Quality gate

- Scenario: `test_quality_gate_00051`
- Retained static identity: `1b8da74bbe9b2b792faf4afa8a3b37f52dd79e760209cbaf3b6a2eb5ee2b990e`
- Tool path: `resolve_point -> inspect_quality_window -> inspect_quality_window`
- Phase count: `4`
- Final gold: `{"commitment_action": "abstain", "reason": "marginal_quality"}`
- Submitted row digest: `a870bba49fbc149603fa38589b7aef82363305fdbc1aeab94523f068985eb96c`
- Replay 1 row digest: `a870bba49fbc149603fa38589b7aef82363305fdbc1aeab94523f068985eb96c`
- All available digests equal: `yes`

Initial request:

> Data-quality review request: "Would you trust this signal enough for the weekly trend question about the air differential pressure measurement on Zone 005 for the week beginning May 19, 2020, or would you abstain?" If the site is missing, ask for it first; then tell me whether you would answer or abstain.

## What Was Recomputed

Each run re-enumerated family candidates from the supplied tool store, matched all 532 frozen static identities exactly once, rebuilt E2E and operator surfaces, executed telemetry tools for phase targets, applied typed family repairs, and ran contract preflight. The submitted dataset bundle was read only after output generation for comparison.

When the report's tool-store mode is `raw_archives_to_fresh_tool_store`, metadata normalization and raw telemetry preprocessing were also rerun before both episode builds.

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
