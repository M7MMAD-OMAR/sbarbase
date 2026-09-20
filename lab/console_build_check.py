"""Console build check: build the UI and verify the served page is intact.

Usage:
  /usr/bin/python3 lab/console_build_check.py            # build then verify
  /usr/bin/python3 lab/console_build_check.py --verify-only

This is install step "console built" on its own: it runs the same command the
installer runs, then proves the produced page references assets that exist, so a
build that yields a page the console cannot serve is a failure, not a success.
Evidence goes to docs/evidence/console-build.json. No container, no secret.
"""
import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'.lab'/'ui'
EVIDENCE=ROOT/'docs'/'evidence'/'console-build.json'
REFERENCE=re.compile(r'(?:src|href)="([^"]+)"')


def asset_references(page):
    """Local asset URLs the built page needs, in document order."""
    found=[]
    for value in REFERENCE.findall(page):
        if value.startswith(('http://','https://','//','data:')):continue
        found.append(value.lstrip('/'))
    return found


def verify(out=OUT):
    """Fail when the page is missing, empty, or points at a file that is absent."""
    problems=[]
    index=out/'index.html'
    if not index.exists():return ['index.html is missing from the build output'],{}
    page=index.read_text()
    if not page.strip():return ['index.html is empty'],{}
    references=asset_references(page)
    if not references:problems.append('index.html references no local asset')
    assets={}
    for reference in references:
        path=out/reference
        if not path.exists():
            problems.append('referenced asset missing: '+reference);continue
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        assets[reference]={'bytes':path.stat().st_size,'sha256':digest}
    return problems,{'page_bytes':index.stat().st_size,'page_sha256':hashlib.sha256(index.read_bytes()).hexdigest(),
                     'assets':assets,'referenced':len(references)}


SOURCE_ROOTS=('ui','vite.config.ts','vite.config.mts','vite.config.js','vite.config.mjs','package.json','tsconfig.json','index.html')
SOURCE_SUFFIXES=('.ts','.tsx','.mts','.js','.jsx','.mjs','.css','.scss','.html','.json','.svg','.map')


def newest_source_mtime(roots=SOURCE_ROOTS):
    """Newest modification time among the console's inputs."""
    newest=0.0
    for relative in roots:
        path=ROOT/relative
        if path.is_dir():
            for candidate in path.rglob('*'):
                if candidate.is_file() and candidate.suffix in SOURCE_SUFFIXES:
                    newest=max(newest,candidate.stat().st_mtime)
        elif path.exists():
            newest=max(newest,path.stat().st_mtime)
    return newest


def is_fresh(out=OUT,roots=None):
    """A usable build that is newer than every input can be reused as it stands."""
    problems,_=verify(out)
    if problems:return False,'build output is not usable: '+'; '.join(problems)
    newest=newest_source_mtime(roots or SOURCE_ROOTS)
    built=(out/'index.html').stat().st_mtime
    if built<newest:return False,'a source file is newer than the built page'
    return True,'built page is newer than every input'


def build(command=('bun','run','build:ui'),cwd=ROOT,timeout=900):
    started=time.monotonic()
    result=subprocess.run(list(command),cwd=cwd,capture_output=True,text=True,timeout=timeout)
    return {'command':' '.join(command),'exit':result.returncode,'seconds':round(time.monotonic()-started,2),
            'tail':'\n'.join((result.stdout+result.stderr).strip().splitlines()[-6:])}


def main():
    parser=argparse.ArgumentParser(description='console build check')
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    build_record=None
    if not args.verify_only:
        build_record=build()
        print('build exit',build_record['exit'],'in',build_record['seconds'],'s')
    problems,page=verify()
    passed=not problems and (build_record is None or build_record['exit']==0)
    evidence={'scope':('Install step "console built", isolated: the production UI build is run and the produced page is '
                       'checked for a non-empty index and for every local asset it references. Not a browser render, not '
                       'a serving check, not the console API.'),
              'build':build_record,'page':page,'problems':problems,
              'run_at':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
              'passed':bool(passed)}
    EVIDENCE.parent.mkdir(parents=True,exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence,indent=1)+'\n')
    for problem in problems:print('FAIL:',problem)
    print('assets:',len(page.get('assets',{})),'page bytes:',page.get('page_bytes'))
    print('evidence:',EVIDENCE)
    print('console build check:','passed' if passed else 'failed')
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':main()