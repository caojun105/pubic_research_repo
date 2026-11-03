"""Download the pinned R02 public assets, verify SHA256, extract safely."""
import argparse,hashlib,json,subprocess,tarfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
p=argparse.ArgumentParser();p.add_argument('--dest',required=True);a=p.parse_args();root=Path(a.dest);root.mkdir(parents=True,exist_ok=True)
r=json.loads(Path('experiments/r02/release_assets.json').read_text())
def get(a):
 name=a['name'];assert Path(name).name==name
 target=root/name;expected=a['digest'].split(':')[1]
 if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=expected:
  subprocess.run(['curl','-fL','--retry','2','--connect-timeout','15','--max-time','180','-sS',a['browser_download_url'],'-o',str(target)],check=True)
 assert target.stat().st_size==a['size'] and hashlib.sha256(target.read_bytes()).hexdigest()==expected
 if name.endswith('.tar.gz'):
  with tarfile.open(target) as archive:archive.extractall(root/'extracted',filter='data')
 print('Verified',name,flush=True)
with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(get,r['assets']))
