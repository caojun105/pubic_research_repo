"""Verify immutable original archives; optionally verify a pristine extraction."""
import argparse
import hashlib
import json
import tarfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--extracted-root', type=Path, help='Optional pristine extraction directory, not the evolved working source')
args = parser.parse_args()
root = Path(__file__).resolve().parent
manifest = json.loads((root / 'MANIFEST.json').read_text())
count = 0
for archive, record in manifest.items():
    data = (root / archive).read_bytes()
    if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
        raise SystemExit(f'FAILED archive: {archive}')
    count += 1
    with tarfile.open(root / archive) as tar:
        expected = {entry['path'] for entry in record['files']}
        members = [m.name for m in tar.getmembers() if m.isfile()]
        if len(members) != len(expected) or set(members) != expected:
            raise SystemExit(f'FAILED member list: {archive}')
        for entry in record['files']:
            data = tar.extractfile(entry['path']).read()
            if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise SystemExit(f'FAILED member: {entry["path"]}')
            if args.extracted_root:
                data = (args.extracted_root / entry['path']).read_bytes()
                if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                    raise SystemExit(f'FAILED extraction: {entry["path"]}')
            count += 1
print(f'OK: {count} original archives/members verified; evolving working files are not archive contents')
