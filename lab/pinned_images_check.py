"""Verify that every pinned image is present locally and matches its pin.

Usage: /usr/bin/python3 lab/pinned_images_check.py

This is install step "pinned images present" on its own: for each lock entry it
asks the local daemon for the image and compares the reported digest with the
pin. A pin that resolves to a different digest at the same tag is a failure, not
a warning. Evidence goes to docs/evidence/pinned-images.json. Never pulls.
"""
import datetime
import json
import subprocess
import sys
from pathlib import Path
import install_server

ROOT=Path(__file__).resolve().parent.parent
EVIDENCE=ROOT/'docs'/'evidence'/'pinned-images.json'


def docker(*args):
    result=subprocess.run(['docker',*args],text=True,capture_output=True,timeout=60)
    return result.returncode,result.stdout,result.stderr


def inspect_image(reference):
    code,out,error=docker('image','inspect',reference)
    if code:return None,error.strip() or 'image inspect failed'
    try:record=json.loads(out)[0]
    except Exception:return None,'image inspect returned unparsable output'
    return record,None


def evaluate(pin,record):
    """Compare a local image with its pin; the digest is the identity, tags are not."""
    if record is None:return False,'not present locally'
    digests=[value for value in record.get('RepoDigests') or [] if isinstance(value,str)]
    identifier=record.get('Id','')
    # RepoDigests entries look like "postgres@sha256:..."; match the digest inside.
    if any(pin in value for value in digests):return True,'present and matches the pin'
    if pin in identifier:return True,'present and matches the pin'
    return False,'present under tag '+str(record.get('RepoTags'))+' but no digest matches '+pin[:26]


def main():
    pins=install_server.pinned_images()
    entries=[]
    for label,digest,reference in pins:
        record,error=inspect_image(reference)
        ok,detail=evaluate(digest,record)
        entries.append({'component':label,'pin':digest,'pull_reference':reference,'ok':ok,
                        'detail':detail,'repo_tags':(record or {}).get('RepoTags')})
        print(('ok:  ' if ok else 'FAIL ')+label+'  '+detail)
    passed=bool(entries) and all(item['ok'] for item in entries)
    evidence={'scope':('Install step "pinned images present", isolated: every pin in lab/images.lock.json, '
                       'lab/distro-image.lock.json and the storage lock is present in the local daemon and resolves to '
                       'the pinned digest. Nothing is pulled, no container starts.'),
              'run_at':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
              'images':entries,'count':len(entries),'passed':passed}
    EVIDENCE.parent.mkdir(parents=True,exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence,indent=1)+'\n')
    print('evidence:',EVIDENCE)
    print('pinned images check:','passed' if passed else 'failed')
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':main()