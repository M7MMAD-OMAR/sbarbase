import {defineConfig,type Plugin} from 'vite';

// Preserve dependency client directives in their containing output chunk.
// The browser console has no server components. Moving the directive to the
// Lucide chunk keeps its client boundary while normal tree shaking applies.
function clientBoundaries():Plugin {
 const clientModules=new Set<string>();
 return {
  name:'client-boundaries',apply:'build',
  buildStart(){clientModules.clear();},
  transform(code,id) {
   if(!code.includes('use client'))return;
   const statements=this.parse(code).body;
   for(const statement of statements) {
    if(statement.type!=='ExpressionStatement'||statement.expression.type!=='Literal'||typeof statement.expression.value!=='string')break;
    if(statement.expression.value==='use client') {
     clientModules.add(id);
     // Keep offsets intact for subsequent transforms.
     return {code:code.slice(0,statement.start)+code.slice(statement.start,statement.end).replace(/[^\r\n]/g,' ')+code.slice(statement.end),map:null};
    }
   }
  },
  renderChunk(code,chunk) {
   if(Object.keys(chunk.modules).some(id=>clientModules.has(id)))return {code:'"use client";\n'+code,map:null};
  }
 };
}

export default defineConfig({
 root:'ui',plugins:[clientBoundaries()],
 build:{outDir:'../.lab/ui',emptyOutDir:true,rolldownOptions:{output:{codeSplitting:{groups:[
  // React, its DOM renderer and scheduler share runtime initialization.
  {name:'react-runtime',test:/node_modules[\/](?:react|react-dom|scheduler)[\/]/},
  // Isolate the icon library so its retained directive marks only its chunk.
  {name:'lucide',test:/node_modules[\/]lucide-react[\/]/,includeDependenciesRecursively:false}
 ]}}}},
 server:{host:'127.0.0.1',strictPort:true}
});
