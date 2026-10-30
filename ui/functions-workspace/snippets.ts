export type Language='curl'|'javascript'|'swift'|'flutter'|'python';
export const LANGUAGES:{id:Language;label:string}[]=[{id:'curl',label:'cURL'},{id:'javascript',label:'JavaScript'},{id:'swift',label:'Swift'},{id:'flutter',label:'Flutter'},{id:'python',label:'Python'}];
export const KEY_PLACEHOLDER='YOUR_PUBLISHABLE_KEY';

/** The project's address, from a function's path (`/e_.../functions/v1/name`): what the client libraries take as their URL. */
export function projectUrl(origin:string,functionPath:string):string {return origin+functionPath.replace(/\/functions\/v1\/[^/]+$/,'');}

/** How to call one function from each client. The key is a placeholder until the person asks to see their own publishable key. */
export function snippet(language:Language,input:{origin:string;path:string;name:string;key?:string}):string {
 const key=input.key??KEY_PLACEHOLDER,url=input.origin+input.path,project=projectUrl(input.origin,input.path);
 switch(language){
  case 'curl':return `curl -L -X POST '${url}' \\\n  -H 'Authorization: Bearer ${key}' \\\n  -H 'apikey: ${key}' \\\n  -H 'Content-Type: application/json' \\\n  --data '{"name":"Functions"}'`;
  case 'javascript':return `import { createClient } from '@supabase/supabase-js'\n\nconst supabase = createClient('${project}', '${key}')\n\nconst { data, error } = await supabase.functions.invoke('${input.name}', {\n  body: { name: 'Functions' },\n})`;
  case 'swift':return `let supabase = SupabaseClient(\n  supabaseURL: URL(string: "${project}")!,\n  supabaseKey: "${key}"\n)\n\nlet response: String = try await supabase.functions.invoke(\n  "${input.name}",\n  options: FunctionInvokeOptions(body: ["name": "Functions"])\n)`;
  case 'flutter':return `final supabase = SupabaseClient('${project}', '${key}');\n\nfinal res = await supabase.functions.invoke(\n  '${input.name}',\n  body: {'name': 'Functions'},\n);`;
  case 'python':return `from supabase import create_client\n\nsupabase = create_client("${project}", "${key}")\n\nresponse = supabase.functions.invoke(\n    "${input.name}",\n    invoke_options={"body": {"name": "Functions"}},\n)`;
 }
}
