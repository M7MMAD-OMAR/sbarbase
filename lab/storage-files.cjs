// Test-only file-backend snapshot, including xattrs used for MIME/cache metadata.
const fs=require('node:fs'),path=require('node:path'),xattr=require('/app/node_modules/fs-xattr');
const attributes=new Set(['user.supabase.cache-control','user.supabase.content-type','user.supabase.etag']);
const input=JSON.parse(fs.readFileSync(0,'utf8'));
if(!/^[a-z][a-z0-9_]{1,30}$/.test(input.tenant))throw Error('Invalid tenant');
const root='/tmp/storage-data/sbarbase-lab/'+input.tenant;
if(input.operation==='snapshot') {
 const files=[];
 function walk(dir) {
  for(const name of fs.readdirSync(dir).sort()) {
   const file=path.join(dir,name),stat=fs.lstatSync(file);
   if(stat.isSymbolicLink())throw Error('Symlinks are not supported');
   if(stat.isDirectory())walk(file);
   else if(stat.isFile())files.push({path:path.relative(root,file),data:fs.readFileSync(file).toString('base64'),
    attributes:Object.fromEntries(xattr.listAttributesSync(file).filter(key=>attributes.has(key)).sort().map(key=>[key,xattr.getAttributeSync(file,key).toString('base64')]))});
  }
 }
 walk(root);process.stdout.write(JSON.stringify(files));
} else if(input.operation==='restore') {
 if(fs.existsSync(root))throw Error('Restore destination already exists');
 for(const file of input.files) {
  if(typeof file.path!=='string'||path.isAbsolute(file.path)||file.path.split('/').some(p=>!p||p==='.'||p==='..'))throw Error('Invalid archive path');
  if(Object.keys(file.attributes).some(key=>!attributes.has(key)))throw Error('Unsupported file attribute');
 }
 fs.mkdirSync(root,{recursive:true,mode:0o700});
 for(const file of input.files) {
  const destination=path.join(root,file.path);fs.mkdirSync(path.dirname(destination),{recursive:true});
  fs.writeFileSync(destination,Buffer.from(file.data,'base64'),{flag:'wx',mode:0o600});
  for(const [key,value] of Object.entries(file.attributes))xattr.setAttributeSync(destination,key,Buffer.from(value,'base64'));
 }
 process.stdout.write(JSON.stringify({restored:input.files.length}));
} else throw Error('Invalid operation');
