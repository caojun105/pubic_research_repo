"""Verify original archive bytes and all extracted files using SHA256."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'MANIFEST.json').read_text())
count = 0
for archive, record in manifest.items():
    for entry in [{'path': archive, **{k: record[k] for k in ('bytes', 'sha256')}}, *record['files']]:
        path = root / entry['path']
        if path.stat().st_size != entry['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise SystemExit(f'FAILED: {entry["path"]}')
        count += 1
print(f'OK: {count} archives/files verified')
