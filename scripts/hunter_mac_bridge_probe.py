"""Bounded, read-only macOS runner liveness receipt; never executes request text."""
import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
from pathlib import Path

REPO = 'fafa33/Project-Hunter'
SHA = re.compile(r'[0-9a-f]{40}\Z')


def receipt(repository: str, expected_sha: str, runner_os: str, *, root: Path) -> dict:
    if repository != REPO or not SHA.fullmatch(expected_sha):
        raise ValueError('untrusted repository or malformed HEAD')
    if runner_os != 'macOS' or platform.system() != 'Darwin':
        raise ValueError('macOS runner required')
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != expected_sha:
        raise ValueError('stale checkout HEAD')
    return {'schema':'hunter-mac-bridge-probe/v1', 'repository':repository,
            'trusted_head':actual, 'runner_os':'macOS', 'mode':'READ_ONLY',
            'outcome':'PASS', 'nonce':hashlib.sha256((repository+':'+actual).encode()).hexdigest()}


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument('--repository',required=True)
    p.add_argument('--head',required=True)
    p.add_argument('--output',required=True)
    args=p.parse_args()
    result=receipt(args.repository,args.head,os.environ.get('RUNNER_OS',''),root=Path.cwd())
    Path(args.output).write_text(json.dumps(result,sort_keys=True)+'\n')
    print('HUNTER_MAC_BRIDGE_READ_ONLY_PROBE_PASS')

if __name__=='__main__':main()
