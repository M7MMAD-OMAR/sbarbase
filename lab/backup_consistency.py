"""Admit file bytes against Storage rows from the dump's exported snapshot."""
import base64
import hashlib
import re
import tarfile
import restore_files


class ConsistencyError(RuntimeError):
    pass


def inventory(api, environment, exported):
    if not re.fullmatch(r'[0-9A-F]+-[0-9A-F]+-[0-9]+', exported):
        raise ConsistencyError('Invalid Storage snapshot identity')
    import json
    query = ("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET TRANSACTION SNAPSHOT '" + exported + "'; "
             "SELECT coalesce(jsonb_agg(jsonb_build_object('bucket_id',bucket_id,'name',name,'version',version,"
             "'metadata',metadata) ORDER BY bucket_id,name),'[]'::jsonb) FROM storage.objects; COMMIT;")
    try:
        rows = json.loads(api.sql(query, environment))
        if not isinstance(rows, list):
            raise ValueError()
        return rows
    except (ValueError, TypeError):
        raise ConsistencyError('Storage snapshot inventory unavailable') from None


def validate(path, environment, rows, expected_count):
    """Native versioned paths with MD5 fingerprints only, unsupported metadata refuses.

    The native mtime ETag cannot prove bytes. Refuse it rather than approve an
    archive based on object counts, timestamp or same-length content alone.
    Extra immutable versions are harmless; every snapshot reference must match.
    Hostile same-version volume writes and intentional MD5 collisions are outside
    this native overwrite/delete contract. The archive itself is SHA-256 bound.
    """
    try:
        restore_files.admit_tar(path, environment)
        if len(rows) != expected_count:
            raise ConsistencyError('Storage snapshot inventory count mismatch')
        with tarfile.open(path, 'r:') as archive:
            members = {item.name.rstrip('/'): item for item in archive}
            checked = set()
            for row in rows:
                parts = [environment, row['bucket_id'], *row['name'].split('/')]
                if not isinstance(row['version'],str) or not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',row['version']):
                    raise ConsistencyError('Storage backup requires native UUID object versions')
                # The pinned runtime uses the native default slash separator.
                parts.append(row['version'])
                if any(not isinstance(part, str) or part in ('', '.', '..') or '/' in part or '\x00' in part for part in parts):
                    raise ConsistencyError('Unsupported Storage snapshot path')
                name = '/'.join(parts)
                if name in checked:
                    raise ConsistencyError('Duplicate Storage snapshot path')
                checked.add(name)
                metadata = row['metadata']
                etag = metadata.get('eTag')
                if not isinstance(etag, str) or not re.fullmatch(r'"[0-9a-f]{32}"', etag):
                    raise ConsistencyError('Storage backup requires a content MD5 ETag; mtime and unknown ETags are unsupported')
                member = members.get(name)
                if not member or not member.isfile() or type(metadata.get('size')) is not int or member.size != metadata['size']:
                    raise ConsistencyError('Storage snapshot file missing or size mismatch')
                content = hashlib.md5(usedforsecurity=False)
                with archive.extractfile(member) as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b''):
                        content.update(chunk)
                if content.hexdigest() != etag[1:-1]:
                    raise ConsistencyError('Storage snapshot file content mismatch')
                for field, attribute in [('mimetype', 'content-type'), ('cacheControl', 'cache-control')]:
                    if field in metadata:
                        value = member.pax_headers.get('SBARBASE.xattr.user.supabase.' + attribute)
                        if value is None or base64.b64decode(value, validate=True) != metadata[field].encode():
                            raise ConsistencyError('Storage snapshot file metadata mismatch')
        return {'contract': 'native-version-path-md5-v1', 'referenced_files': len(checked)}
    except (restore_files.FilesError, tarfile.TarError, OSError, KeyError, TypeError, ValueError, AttributeError):
        raise ConsistencyError('Storage snapshot files are malformed or unsupported') from None


# Python is included in the pinned native Storage image. PAX keeps the three
# native Linux metadata attributes without requiring GNU tar on Alpine.
ARCHIVE_SCRIPT = r'''
import base64, os, pathlib, sys, tarfile
root=pathlib.Path(sys.argv[2] if len(sys.argv)>2 else '/data/sbarbase-lab'); tenant=sys.argv[1]
if not __import__('re').fullmatch(r'e_[a-f0-9]{24}',tenant): raise RuntimeError('Invalid tenant')
attributes=('user.supabase.cache-control','user.supabase.content-type','user.supabase.etag')
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|',format=tarfile.PAX_FORMAT) as archive:
    source=root/tenant
    if not source.exists():
        item=tarfile.TarInfo(tenant); item.type=tarfile.DIRTYPE; item.mode=0o700; archive.addfile(item)
    else:
        if source.is_symlink() or not source.is_dir(): raise RuntimeError('Unsupported tenant root')
        for location in [source,*sorted(source.rglob('*'))]:
            if location.is_symlink() or not (location.is_dir() or location.is_file()): raise RuntimeError('Unsupported file type')
            item=archive.gettarinfo(str(location),arcname=str(location.relative_to(root)))
            item.pax_headers['mtime']=str(__import__('decimal').Decimal(location.stat().st_mtime_ns)/1000000000)
            if location.is_file():
                for key in attributes:
                    if key in os.listxattr(location,follow_symlinks=False):
                        item.pax_headers['SBARBASE.xattr.'+key]=base64.b64encode(os.getxattr(location,key,follow_symlinks=False)).decode()
                with location.open('rb') as handle: archive.addfile(item,handle)
            else: archive.addfile(item)
'''

EXTRACT_SCRIPT = r'''
import base64, decimal, os, pathlib, sys, tarfile
root=pathlib.Path(sys.argv[1]); allowed={'user.supabase.cache-control','user.supabase.content-type','user.supabase.etag'}
directories=[]
def metadata(target,member):
    os.chown(target,member.uid,member.gid,follow_symlinks=False)
    os.chmod(target,member.mode&0o777,follow_symlinks=False)
    stamp=int(decimal.Decimal(member.pax_headers.get('mtime',str(member.mtime)))*1000000000)
    os.utime(target,ns=(stamp,stamp),follow_symlinks=False)
with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:
    for member in archive:
        parts=member.name.rstrip('/').split('/')
        if any(part in ('','.','..') for part in parts) or member.name.startswith('/') or not (member.isdir() or member.isfile()): raise RuntimeError('Unsupported archive')
        target=root.joinpath(*parts)
        for parent in [root,*target.parents]:
            if parent==root.parent: break
            if parent.is_symlink(): raise RuntimeError('Symlink parent refused')
        if member.isdir():
            target.mkdir(mode=0o700,parents=True,exist_ok=True)
            directories.append((target,member))
        else:
            target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            with target.open('xb') as handle, archive.extractfile(member) as source:
                __import__('shutil').copyfileobj(source,handle)
            for key,value in member.pax_headers.items():
                if key.startswith('SBARBASE.xattr.'):
                    attribute=key[len('SBARBASE.xattr.'):]
                    if attribute not in allowed: raise RuntimeError('Unsupported attribute')
                    os.setxattr(target,attribute,base64.b64decode(value,validate=True),follow_symlinks=False)
            metadata(target,member)
for target,member in reversed(directories): metadata(target,member)
'''
