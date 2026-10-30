export type TreeRow={path:string;name:string;depth:number;folder:boolean};
/** The file names of a function as the cloud's tree shows them: folders first, then files, shared files under `../_shared`. */
export function treeRows(paths:string[]):TreeRow[] {
 type Node={name:string;path:string;children:Map<string,Node>;file:boolean};
 const root:Node={name:'',path:'',children:new Map(),file:false};
 for(const path of paths){
  let node=root;const parts=path.split('/');
  parts.forEach((part,position)=>{
   const here=parts.slice(0,position+1).join('/');
   if(!node.children.has(part))node.children.set(part,{name:part,path:here,children:new Map(),file:position===parts.length-1});
   node=node.children.get(part)!;
  });
 }
 const rows:TreeRow[]=[];
 const visit=(node:Node,depth:number)=>{
  const ordered=[...node.children.values()].sort((a,b)=>Number(a.file)-Number(b.file)||a.name.localeCompare(b.name));
  for(const child of ordered){rows.push({path:child.path,name:child.name,depth,folder:!child.file});if(!child.file)visit(child,depth+1);}
 };
 visit(root,0);
 return rows;
}
