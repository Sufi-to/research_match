"""Print the text output and errors of an exported notebook run (databricks jobs export-run JSON)."""
import base64
import json
import re
import sys
import urllib.parse

ANSI = re.compile(r"\x1b\[[0-9;]*m")

export = json.load(open(sys.argv[1]))
html = export["views"][0]["content"]
match = re.search(r"__DATABRICKS_NOTEBOOK_MODEL = '([^']+)'", html)
model = json.loads(urllib.parse.unquote(base64.b64decode(match.group(1)).decode()))
for command in model["commands"]:
    results = command.get("results") or {}
    data = results.get("data")
    for item in data if isinstance(data, list) else [results]:
        text = item.get("data") if isinstance(item, dict) else item
        if text:
            print(ANSI.sub("", text if isinstance(text, str) else json.dumps(text)[:4000]))
    if command.get("error"):
        print("ERROR:", ANSI.sub("", command["error"])[:3000])
