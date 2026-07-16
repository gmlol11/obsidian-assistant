.PHONY: check compile test doctor demo

check: compile test

compile:
	PYTHONPATH=src python3 -m compileall -q src tests

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

doctor:
	PYTHONPATH=src python3 -m obsidian_assistant --env-file .env doctor

demo:
	PYTHONPATH=src python3 -m obsidian_assistant --env-file .env capture --title "Demo capture" --text "Safe dry-run demo"
