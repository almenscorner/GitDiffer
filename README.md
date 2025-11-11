# GitDiffer
Visualize Git diffs as clean, structured insights. Search, filter, and explore changes with clarity.

## Use gitdiffer
Download the gitdiffer script and run it:
```bash
python3 gitdiffer.py --repo_path /path/to/repo --output_json output.json
```

Or to compare to files on disk:
```bash
python3 gitdiffer.py --compare-file-1 /path/to/file1 --compare-file-2 /path/to/file2 --output_json output.json
```

Then import the generated `output.json` into the web app to visualize the diffs.
