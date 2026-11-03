"""Exercise cached LFS integrity through the real CLI and a temporary Git repository."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

binary = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix='okf-integrity-') as temporary:
    root = Path(temporary)
    repo = root / 'repo'
    repo.mkdir()
    env = dict(os.environ, OKF_HOME=str(root / 'home'))

    def git(*args):
        return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                               '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
                               *args], capture_output=True, check=True, timeout=30)

    def cli(*args, code=0, error=None):
        result = subprocess.run([str(binary), '--format', 'json', *args], cwd=root,
                                env=env, capture_output=True, timeout=30)
        response = json.loads(result.stdout)
        assert result.returncode == code, (args, result.returncode, response)
        assert not result.stderr, result.stderr
        if error:
            assert response['data']['error']['code'] == error, response
        return response

    git('init', '--initial-branch=main')
    content = b'---\ntype: Note\n---\nVerified LFS content\n'
    oid = hashlib.sha256(content).hexdigest()
    (repo / 'note.md').write_text(
        f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(content)}\n',
        encoding='utf-8')
    git('add', 'note.md')
    git('commit', '-m', 'LFS pointer')
    cache = repo / '.git/lfs/objects' / oid[:2] / oid[2:4] / oid
    cache.parent.mkdir(parents=True)
    cache.write_bytes(content)
    cli('bundle', 'add', 'lfs', '--kind', 'git', '--path', str(repo))
    assert cli('read', '@lfs/note')['data']['document']['text'] == content.decode()
    for corrupt in [content.replace(b'Verified', b'Modified'), content[:-1], content + b'extra']:
        cache.write_bytes(corrupt)
        cli('read', '@lfs/note', code=5, error='LFS_INTEGRITY_ERROR')
    cache.unlink()
    cli('read', '@lfs/note', code=7, error='LFS_CONTENT_UNAVAILABLE')
    cache.write_bytes(content)
    assert cli('read', '@lfs/note')['data']['document']['text'] == content.decode()
print('Real CLI LFS reads: valid content, same-size corruption, truncation, appended bytes, missing cache, and recovery passed')
