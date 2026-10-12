/** Local captured-module checks using the pinned TypeScript compiler AST.
 * This validates known local material, not Deno execution or external modules.
 */
import {API} from 'typescript/unstable/sync';
import {createVirtualFileSystem} from 'typescript/unstable/fs';
import {SyntaxKind} from 'typescript/unstable/ast';
import {posix} from 'node:path';
import {ENTRYPOINTS} from '../src/control/function-contract.mjs';

if(process.argv.includes('--preflight')) {
 process.stdout.write(JSON.stringify({status:'parser-ready'})); process.exit(0);
}

const extensions=new Set(['.ts','.js','.tsx','.jsx','.mjs']);
let request=''; for await(const chunk of process.stdin)request+=chunk;
const input=JSON.parse(request);
let api;
let snapshot;
try {
 const virtual={};
 const prefix='/__sbarbase_local_capture__/';
 const sourceNames=Object.keys(input.files).filter(name=>extensions.has(posix.extname(name)));
 for(const name of sourceNames)virtual[prefix+name]=input.files[name];
 const config=prefix+'tsconfig.json';
 virtual[config]=JSON.stringify({compilerOptions:{noLib:true,noResolve:true,allowJs:true,jsx:'preserve',target:'ESNext'},files:sourceNames});
 api=new API({cwd:prefix,fs:createVirtualFileSystem(virtual)});
 snapshot=api.updateSnapshot({openProjects:[config]});
 const project=snapshot.getProject(config);
 if(!project)throw new Error('Captured source parser unavailable');
 const external=new Set();
 const dependencies=new Map();
 for(const name of sourceNames) {
  const source=project.program.getSourceFile(prefix+name);
  if(!source||project.program.getSyntacticDiagnostics(prefix+name).length)throw new Error('Captured module syntax unsupported');
  const references=source.referencedFiles.map(reference=>reference.fileName.startsWith('.')?reference.fileName:'./'+reference.fileName);
  if(source.typeReferenceDirectives.length)throw new Error('External type reference dependency unsupported');
  function literal(node) {
   if(node?.kind!==SyntaxKind.StringLiteral&&node?.kind!==SyntaxKind.NoSubstitutionTemplateLiteral)throw new Error('Dynamic module dependency unsupported');
   if(typeof node.text!=='string')throw new Error('Module dependency unavailable');
   return node.text;
  }
  function visit(node) {
   if(node.kind===SyntaxKind.ImportDeclaration||node.kind===SyntaxKind.ExportDeclaration) {
    if(node.moduleSpecifier)references.push(literal(node.moduleSpecifier));
   } else if(node.kind===SyntaxKind.ImportEqualsDeclaration) {
    if(node.moduleReference?.kind!==SyntaxKind.ExternalModuleReference)throw new Error('Module alias unsupported');
    references.push(literal(node.moduleReference.expression));
   } else if(node.kind===SyntaxKind.ImportType) {
    references.push(literal(node.argument?.literal));
   } else if(node.kind===SyntaxKind.CallExpression||node.kind===SyntaxKind.NewExpression) {
    const callee=node.expression;
    if(callee?.kind===SyntaxKind.ImportKeyword||(callee?.kind===SyntaxKind.Identifier&&callee.text==='require')) {
     if(node.arguments?.length!==1)throw new Error('Module dependency arguments unsupported');
     references.push(literal(node.arguments[0]));
    } else if(callee?.kind===SyntaxKind.Identifier&&['eval','Function','Worker','createRequire'].includes(callee.text)) {
     throw new Error('Dynamic code dependency unsupported');
    } else if(callee?.kind===SyntaxKind.PropertyAccessExpression&&callee.name?.text==='resolve'&&callee.expression?.kind===SyntaxKind.MetaProperty) {
     throw new Error('Dynamic module resolution unsupported');
    }
   }
   node.forEachChild(visit);
  }
  visit(source); dependencies.set(name,references);
 }
 const seen=new Set();
 function walk(name,versionRoot) {
  if(seen.has(name))return; seen.add(name);
  if(!(name in input.files)||!dependencies.has(name))throw new Error('Local module missing or unsupported');
  for(const reference of dependencies.get(name)) {
   if(reference.startsWith('./')||reference.startsWith('../')) {
    const target=posix.normalize(posix.join(posix.dirname(name),reference));
    if(!target.startsWith(versionRoot+'/')||!(target in input.files))throw new Error('Local module outside captured version or missing');
    if(extensions.has(posix.extname(target)))walk(target,versionRoot);
    else if(posix.extname(target)!=='.json')throw new Error('Local module format unsupported');
   } else if(['npm:','jsr:','https:','node:'].some(scheme=>reference.startsWith(scheme)))external.add(reference);
   else throw new Error('Unclassified module dependency unsupported');
  }
 }
 for(const entry of input.entries) {
  const versionRoot=entry.root+'/'+entry.version;
  const start=ENTRYPOINTS.find(point=>(versionRoot+'/'+entry.name+'/'+point) in input.files);
  if(!start)throw new Error('Original Functions entrypoint missing');
  walk(versionRoot+'/'+entry.name+'/'+start,versionRoot);
 }
 process.stdout.write(JSON.stringify({status:'known-local-closure-checked',external:[...external].sort()}));
} catch {
 process.stdout.write(JSON.stringify({status:'refused'})); process.exitCode=1;
} finally {
 try {snapshot?.dispose();} finally {api?.close();}
}
