.PHONY: check compile test plugin-check doctor demo

check: compile test plugin-check

compile:
	PYTHONPATH=src python3 -m compileall -q src tests

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

plugin-check:
	cd integrations/openclaw-capture && npm run check

doctor:
	PYTHONPATH=src python3 -m obsidian_assistant --env-file .env doctor

demo:
	PYTHONPATH=src python3 -m obsidian_assistant --env-file .env capture --title "Demo capture" --text "Safe dry-run demo"
