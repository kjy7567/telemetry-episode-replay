.PHONY: replay trace manifest verify bundles audit-traces

replay:
	@test -n "$(RAW_DIR)" || (echo "Set RAW_DIR" >&2; exit 2)
	python scripts/replay_release.py \
		--raw-dir "$(RAW_DIR)" \
		--work-dir "$${WORK_DIR:-data/local-build/release-replay}" \
		--raw-runs "$${RUNS:-2}" \
		--controller-audit

trace:
	@test -n "$(SCENARIO_ID)" || (echo "Set SCENARIO_ID" >&2; exit 2)
	python scripts/trace_scenario.py "$(SCENARIO_ID)" $(TRACE_ARGS)

manifest:
	python scripts/build_release_manifest.py

verify:
	python scripts/build_release_manifest.py --check
	python scripts/verify_packaged_release.py

bundles:
	python scripts/build_public_bundles.py --output-dir "$${OUTPUT_DIR:-dist}"
	python scripts/verify_packaged_release.py \
		--dist-dir "$${OUTPUT_DIR:-dist}" \
		--require-bundles

audit-traces:
	@test -n "$(BTS_TOOL_STORE_DB)" || (echo "Set BTS_TOOL_STORE_DB" >&2; exit 2)
	@test -n "$(XAI4HEAT_TOOL_STORE_DB)" || (echo "Set XAI4HEAT_TOOL_STORE_DB" >&2; exit 2)
	python scripts/audit_model_traces.py \
		--bts-benchmark-dir artifacts/bts-agentbench \
		--bts-tool-store-db "$(BTS_TOOL_STORE_DB)" \
		--xai4heat-benchmark-dir artifacts/xai4heat-agentbench \
		--xai4heat-tool-store-db "$(XAI4HEAT_TOOL_STORE_DB)" \
		--check
