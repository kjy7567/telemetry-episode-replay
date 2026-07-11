.PHONY: paper-replay paper-replay-fast trace verify

paper-replay:
	@test -n "$(RAW_DIR)" || (echo "Set RAW_DIR" >&2; exit 2)
	python scripts/replay_paper_submission.py \
		--raw-dir "$(RAW_DIR)" \
		--work-dir "$${WORK_DIR:-data/local-build/paper-replay}" \
		--runs "$${RUNS:-2}"

paper-replay-fast:
	@test -n "$(BTS_TOOL_STORE_DB)" || (echo "Set BTS_TOOL_STORE_DB" >&2; exit 2)
	python scripts/replay_paper_submission.py \
		--tool-store-db "$(BTS_TOOL_STORE_DB)" \
		--work-dir "$${WORK_DIR:-data/local-build/paper-replay-fast}" \
		--runs "$${RUNS:-2}"

trace:
	@test -n "$(SCENARIO_ID)" || (echo "Set SCENARIO_ID" >&2; exit 2)
	python scripts/trace_scenario.py "$(SCENARIO_ID)" $(TRACE_ARGS)

verify:
	python scripts/verify_packaged_release.py
	python scripts/audit_model_traces.py
	git diff --exit-code reports/model-runs/trace_audit.json
