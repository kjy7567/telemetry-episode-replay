.PHONY: bundle human-packet lineage replay trace verify

bundle:
	python scripts/make_release_bundle.py

human-packet:
	python scripts/build_human_validation_packet.py

lineage:
	@test -n "$(BTS_TOOL_STORE_DB)" || (echo "Set BTS_TOOL_STORE_DB" >&2; exit 2)
	python scripts/export_release_stream_lineage.py --tool-store-db "$(BTS_TOOL_STORE_DB)"

replay:
	@test -n "$(BTS_TOOL_STORE_DB)" || (echo "Set BTS_TOOL_STORE_DB" >&2; exit 2)
	python scripts/replay_release.py --tool-store-db "$(BTS_TOOL_STORE_DB)"

trace:
	@test -n "$(SCENARIO_ID)" || (echo "Set SCENARIO_ID" >&2; exit 2)
	python scripts/trace_scenario.py "$(SCENARIO_ID)"

verify:
	python scripts/verify_packaged_release.py
