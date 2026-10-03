"""Verify that every pinned image is present locally and matches its pin.

Usage: /usr/bin/python3 lab/pinned_images_check.py

This is install step "pinned images present" on its own: for each lock entry it
asks the local daemon for the exact repository-qualified immutable reference.
Repository digest association is mandatory; the daemon image Id can identify
an index or a configuration depending on its image store. Evidence goes to docs/evidence/pinned-images.json. Never pulls.
"""
import datetime
import json
import subprocess
import sys
from pathlib import Path
import install_server
import image_identity

ROOT=Path(__file__).resolve().parent.parent
EVIDENCE=ROOT/'docs'/'evidence'/'pinned-images.json'


def docker(*args):
    result=subprocess.run(['docker',*args],text=True,capture_output=True,timeout=60)
    return result.returncode,result.stdout,result.stderr


def inspect_image(reference):
    try:reference=image_identity.immutable(reference)
    except image_identity.IdentityError as error:return None,str(error)
    code,out,error=docker('image','inspect',reference)
    if code:return None,error.strip() or 'image inspect failed'
    try:record=image_identity.record(out)
    except image_identity.IdentityError as error:return None,str(error)
    return record,None


def evaluate(reference,record):
    """Prove an exact repository reference, allowing either daemon store Id."""
    if record is None:return False,'not present locally'
    try:identifier=image_identity.resolved_id(reference,record)
    except image_identity.IdentityError as error:return False,str(error)
    return True,'present and matches the pin; daemon identity '+identifier


def main():
    pins=install_server.pinned_images()
    entries=[]
    for label,digest,reference in pins:
        record,error=inspect_image(reference)
        ok,detail=evaluate(reference,record)
        detail=error or detail
        entries.append({'component':label,'pin':digest,'pull_reference':reference,'ok':ok,
                        'detail':detail,'daemon_id':(record or {}).get('Id'),'repo_tags':(record or {}).get('RepoTags')})
        print(('ok:  ' if ok else 'FAIL ')+label+'  '+detail)
    passed=bool(entries) and all(item['ok'] for item in entries)
    evidence={'scope':('Install step "pinned images present", isolated: every pin in lab/images.lock.json, '
                       'lab/distro-image.lock.json, Storage, Studio, Realtime and Functions locks is present in the daemon and proves '
                       'the pinned digest. Nothing is pulled, no container starts.'),
              'run_at':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
              'images':entries,'count':len(entries),'passed':passed}
    EVIDENCE.parent.mkdir(parents=True,exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence,indent=1)+'\n')
    print('evidence:',EVIDENCE)
    print('pinned images check:','passed' if passed else 'failed')
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':main()