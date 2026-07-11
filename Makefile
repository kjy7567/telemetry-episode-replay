.PHONY: bundle human-packet lineage paper-replay paper-replay-fast replay submission-audit trace verify

bundle:
	python scripts/make_release_bundle.py

human-packet:
	python scripts/build_human_validation_packet.py

lineage:
	@test -n "$(BTS_TOOL_STORE_DB)" || (echo "Set BTS_TOOL_STORE_DB" >&2; exit 2)
	python scripts/export_release_stream_lineage.py --tool-store-db "$(BTS_TOOL_STORE_DB)"

paper-replay:
	@test -n "$(RAW_DIR)" || (echo "Set RAW_DIR" >&2; exit 2)
	python scripts/replay_paper_submission.py --raw-dir "$(RAW_DIR)" --work-dir "$${WORK_DIR:-data/local-build/paper-submission-replay}" --runs 2

paper-replay-fast:
	@test -n "$(BTS_TOOL_STORE_DB)" || (echo "Set BTS_TOOL_STORE_DB" >&2; exit 2)
	python scripts/replay_paper_submission.py --tool-store-db "$(BTS_TOOL_STORE_DB)" --work-dir "$${WORK_DIR:-data/local-build/paper-submission-replay-fast}" --runs 2

replay:
	@test -n "$(BTS_TOOL_STORE_DB)" || (echo "Set BTS_TOOL_STORE_DB" >&2; exit 2)
	python scripts/replay_release.py --tool-store-db "$(BTS_TOOL_STORE_DB)"

submission-audit:
	python scripts/build_submission_replay_audit.py

trace:
	@test -n "$(SCENARIO_ID)" || (echo "Set SCENARIO_ID" >&2; exit 2)
	python scripts/trace_scenario.py "$(SCENARIO_ID)"

verify:
	python scripts/verify_packaged_release.py
	python scripts/build_submission_replay_audit.py --verify-recorded
