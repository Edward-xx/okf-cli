"""Verify hashes and run acceptance on an extracted release archive."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from assemble_release import SKILL_FILES, read_package, require


root = Path(__file__).resolve().parents[1]
archive = Path(sys.argv[1]).resolve()
manifest = read_package(archive)[0]
with tempfile.TemporaryDirectory(prefix='okf-package-') as temporary:
    destination = Path(temporary)
    with zipfile.ZipFile(archive) as package:
        package.extractall(destination)
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
    require(actual == set(manifest['sha256']) | {'manifest.json'}, 'extracted inventory mismatch')
    binary = destination / ('okf.exe' if os.name == 'nt' else 'okf')
    binary.chmod(0o755)
    env = os.environ.copy()
    env['PATH'] = str(Path(env.get('SYSTEMROOT', 'C:/Windows')) / 'System32') if os.name == 'nt' else '/nonexistent'
    env['OKF_HOME'] = str(destination / 'home')
    info = json.loads(subprocess.check_output([str(binary), '--format', 'json', 'build-info'], env=env, cwd=destination))['data']
    require(all(info[key] == manifest[key] for key in ('version', 'target', 'source_commit', 'source_dirty')), 'binary/manifest build-info mismatch')
    require(subprocess.check_output([str(binary), '--version'], text=True).strip() == f'okf {manifest["version"]}', 'binary/manifest version mismatch')
    installed = destination / 'installed-skill'
    subprocess.run([str(binary), 'skill', 'install', str(installed)], env=env, cwd=destination,
                   check=True, stdout=subprocess.DEVNULL)
    for name in SKILL_FILES:
        require((installed / name).read_bytes() == (destination / 'skills/okf-cli' / name).read_bytes(), 'binary/archive embedded skill mismatch')
    for script in ('smoke.py', 'acceptance.py'):
        subprocess.run([sys.executable, str(root / 'scripts' / script), str(binary)],
                       env=env, check=True, timeout=300)
print('Extracted package, hashes and standalone acceptance passed')
