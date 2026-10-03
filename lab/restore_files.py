"""Archive member admission and immutable file-tree identities for cutover.

The native helper uses the pinned Storage image's Node runtime, without host
dependencies. Callers must prove the shared writer remains stopped before each
mutation. Host root and arbitrary external volume writers are outside scope.
"""
import re
import tarfile


class FilesError(RuntimeError):
    pass


def admit_tar(path, environment):
    if not re.fullmatch(r'e_[a-f0-9]{24}', environment):
        raise FilesError('Unsupported object archive identity')
    seen = set()
    try:
        with tarfile.open(path, 'r:') as archive:
            for member in archive:
                name = member.name.rstrip('/')
                parts = name.split('/')
                if not name or parts[0] != environment or any(part in ('', '.', '..') for part in parts) \
                        or name in seen or not (member.isdir() or member.isfile()) \
                        or member.mode & 0o7000 or '\x00' in name:
                    raise FilesError('Object archive contains unsupported paths or member types')
                if name == environment and not member.isdir():
                    raise FilesError('Object archive tenant root is not a directory')
                seen.add(name)
        if environment not in seen:
            raise FilesError('Object archive has no tenant root')
    except (tarfile.TarError, OSError):
        raise FilesError('Object archive is unavailable or malformed') from None
    return True


# This is executed only in an admitted, bounded native Storage helper. Names
# and identity JSON are separate positional arguments, never interpolated SQL
# or shell source. A failure emits no file name or object content.
TREE_SCRIPT = r'''
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
function name(value) {
  if (!/^[a-zA-Z0-9_./-]{1,180}$/.test(value) || value.split('/').some(part=>!part || part==='.' || part==='..')) throw Error();
  return path.join('/data/sbarbase-lab', value);
}
function identity(location) {
  let root;
  try { root = fs.lstatSync(location, {bigint:true}); } catch(e) {
    if (e.code === 'ENOENT') return {exists:false,device:null,inode:null,sha256:null};
    throw e;
  }
  if (!root.isDirectory() || root.isSymbolicLink()) throw Error();
  const hash = crypto.createHash('sha256');
  function visit(current, relative) {
    const stat = fs.lstatSync(current, {bigint:true});
    if (stat.isSymbolicLink() || !stat.isFile() && !stat.isDirectory()) throw Error();
    hash.update(JSON.stringify([relative,stat.isDirectory()?'d':'f',String(stat.mode),String(stat.uid),String(stat.gid)])+'\n');
    if (stat.isDirectory()) {
      for (const item of fs.readdirSync(current).sort()) visit(path.join(current,item),relative?relative+'/'+item:item);
    } else {
      const fd = fs.openSync(current, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
      try {
        const before = fs.fstatSync(fd, {bigint:true});
        if (before.ino !== stat.ino || before.dev !== stat.dev) throw Error();
        const content = crypto.createHash('sha256'), chunk = Buffer.alloc(65536);
        let amount;
        while ((amount=fs.readSync(fd,chunk,0,chunk.length,null))) content.update(chunk.subarray(0,amount));
        const after = fs.fstatSync(fd, {bigint:true});
        if (before.size !== after.size || before.mtimeNs !== after.mtimeNs || before.ctimeNs !== after.ctimeNs) throw Error();
        hash.update(content.digest('hex')+'\n');
      } finally { fs.closeSync(fd); }
    }
  }
  visit(location,'');
  return {exists:true,device:String(root.dev),inode:String(root.ino),sha256:hash.digest('hex')};
}
function syncDirectory(directory) {
  const fd=fs.openSync(directory,fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
  try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function syncTree(location) {
  const stat=fs.lstatSync(location);
  if (stat.isSymbolicLink() || !stat.isFile() && !stat.isDirectory()) throw Error();
  if (stat.isDirectory()) {
    for (const item of fs.readdirSync(location)) syncTree(path.join(location,item));
    syncDirectory(location);
  } else {
    const fd=fs.openSync(location,fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  }
}
try {
  const action=process.argv[1], source=name(process.argv[2]);
  const parent=fs.lstatSync('/data/sbarbase-lab');
  if (!parent.isDirectory() || parent.isSymbolicLink()) throw Error();
  if (action==='identity') console.log(JSON.stringify(identity(source)));
  else if (action==='sync') { syncTree(source); syncDirectory(path.dirname(source)); console.log(JSON.stringify(identity(source))); }
  else {
    const expected=JSON.parse(process.argv[3]);
    const actual=identity(source), keys=['exists','device','inode','sha256'];
    if (!expected || Object.keys(expected).length!==keys.length || keys.some(key=>actual[key]!==expected[key])) throw Error();
    if (action==='rename') {
      const target=name(process.argv[4]);
      if (identity(target).exists) throw Error();
      fs.renameSync(source,target);
      syncDirectory(path.dirname(source));
      if (path.dirname(target)!==path.dirname(source)) syncDirectory(path.dirname(target));
      console.log(JSON.stringify(identity(target)));
    } else if (action==='remove') {
      if (expected.exists) fs.rmSync(source,{recursive:true});
      syncDirectory(path.dirname(source)); console.log('null');
    } else throw Error();
  }
} catch(e) { process.stderr.write('Object tree identity operation refused\n'); process.exitCode=1; }
'''


def tree_command(action):
    if action not in ('identity', 'sync', 'rename', 'remove'):
        raise FilesError('Unsupported object tree operation')
    return 'node -e "$1" ' + action + ' "$2" "$3" "$4"'
