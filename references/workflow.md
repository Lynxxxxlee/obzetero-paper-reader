# ObZetero Workflow

Use this workflow for Zotero-to-Obsidian paper reading tasks.

1. Confirm the target Zotero collection name.
2. Run `scan` to inspect item count, status tags, and local PDFs.
3. Run `read` to create notes, extract PDF text, rebuild the collection index, and update the dashboard.
4. Read the extracted text in `Collections/<collection>/.extracts/<zotero_key>.txt`.
5. Fill `AUTO: Chinese Reading Notes` in each paper note in Chinese.
6. Run `index` after manual note edits.
7. Run `sync --write-zotero` only when the user wants Obsidian status changes written back to Zotero and Zotero is closed.

Do not write to Zotero while the Zotero app is running. The helper script blocks this for `--write-zotero`.

Common commands:

```bash
cd ~/.codex/skills/obzetero-paper-reader
python3 scripts/obzetero_sync.py scan --collection "LLM"
python3 scripts/obzetero_sync.py read --collection "LLM"
python3 scripts/obzetero_sync.py sync --collection "LLM"
python3 scripts/obzetero_sync.py sync --collection "LLM" --write-zotero
python3 scripts/obzetero_sync.py index --collection "LLM"
```
