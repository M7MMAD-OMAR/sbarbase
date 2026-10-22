import {bindReadyEnvironmentPublication} from './ready-publication';
import {refreshCurrentManagement} from './management-context';
import {Catalog,type DataApiSettings,type DataApiState} from './catalog';
import {authenticate,reply,type ManagementIdentity} from './auth';

/** Data API settings for one environment: which schemas PostgREST exposes (the first is the
 * default for requests without an Accept-Profile or Content-Profile header), its extra search
 * path and its row limit. The rules mirror lab/data_api.py `validate`; whether each schema exists
 * is known only to the database, so the supervisor checks that when it applies them
 * (docs/engineering/DATA-API-SCHEMAS.md). */

export const MAX_SCHEMAS=16,MAX_ROWS=1_000_000;
const IDENTIFIER=/^[a-z_][a-z0-9_]{0,62}$/;
/** Studio's internal schemas plus Sbarbase's, without the `*_public` ones Studio lets a project expose. */
const INTERNAL=new Set(['auth','storage','extensions','realtime','_realtime','vault','pgsodium','pgsodium_masks','cron','net',
 'graphql','pgbouncer','_analytics','_supavisor','information_schema','pgtle','pgmq','etl','supabase_functions',
 'supabase_migrations','pg_catalog','pg_toast']);
const SEARCH_PATH_ALLOWED=new Set(['extensions','sbarbase_empty']);

export class DataApiError extends Error {}

function platformName(name:string):boolean {
 return INTERNAL.has(name)||name.startsWith('pg_')||name.startsWith('sbarbase')||name.startsWith('supabase');
}

function names(value:unknown,field:string,allowed:Set<string>=new Set()):string[] {
 if(!Array.isArray(value)||!value.length||value.length>MAX_SCHEMAS)throw new DataApiError(`${field} needs between 1 and ${MAX_SCHEMAS} schema names`);
 const seen:string[]=[];
 for(const item of value) {
  if(typeof item!=='string'||!IDENTIFIER.test(item))
   throw new DataApiError(`${field}: a schema name uses lowercase letters, digits and underscores, and starts with a letter or underscore`);
  if(item==='graphql_public')throw new DataApiError(`${field}: GraphQL is not available on Sbarbase, so graphql_public cannot be exposed. Remove it (Supabase's config.toml lists it by default).`);
  if(platformName(item)&&!allowed.has(item))throw new DataApiError(`${field}: the schema ${item} belongs to the platform and cannot be used here`);
  if(seen.includes(item))throw new DataApiError(`${field}: the schema ${item} is listed twice`);
  seen.push(item);
 }
 return seen;
}

/** A comma separated list, as Supabase's API and config.toml join them, to the names in it. */
export function splitNames(value:unknown,field:string):string[] {
 if(typeof value!=='string')throw new DataApiError(`${field} is a comma separated list of schema names`);
 return value.split(',').map(item=>item.trim()).filter(Boolean);
}

/** What lab/data_api.py serves when the Data API is off, or when none of the exposed schemas exists. */
export const EMPTY_SCHEMA='sbarbase_empty';

export function validateDataApi(input:{schemas:unknown;extraSearchPath?:unknown;maxRows?:unknown}):DataApiSettings {
 const rows=input.maxRows??null;
 if(rows!==null&&(typeof rows!=='number'||!Number.isInteger(rows)||rows<1||rows>MAX_ROWS))
  throw new DataApiError(`max_rows is a whole number from 1 to ${MAX_ROWS}, or empty for no limit`);
 // No schema at all turns the Data API off.
 const off=Array.isArray(input.schemas)&&(input.schemas.length===0||(input.schemas.length===1&&input.schemas[0]===EMPTY_SCHEMA));
 return {schemas:off?[EMPTY_SCHEMA]:names(input.schemas,'schemas'),
  extraSearchPath:names(input.extraSearchPath??['public'],'extra_search_path',SEARCH_PATH_ALLOWED),maxRows:rows};
}

/** The settings after a partial change, as Supabase's PATCH applies only the fields it is given. */
export function mergeDataApi(current:DataApiSettings,input:Record<string,unknown>,keys:{schemas:string;extraSearchPath:string;maxRows:string},
 split:boolean):DataApiSettings {
 const pick=(key:string,field:string,fallback:unknown)=>key in input?(split?splitNames(input[key],field):input[key]):fallback;
 return validateDataApi({schemas:pick(keys.schemas,'schemas',current.schemas),
  extraSearchPath:pick(keys.extraSearchPath,'extra_search_path',current.extraSearchPath),
  maxRows:keys.maxRows in input?input[keys.maxRows]:current.maxRows});
}

/** GET and PUT `/environments/{id}/data-api` (`{"schemas": [...], "extraSearchPath": [...], "maxRows": n|null}`),
 * for owners and admins of a ready environment. Viewers may read it. */
export function dataApiHandler(catalog:Catalog,identify:ManagementIdentity) {
 return async(request:Request):Promise<Response>=>{
  const match=new URL(request.url).pathname.match(/^\/management\/v1\/environments\/([a-f0-9-]{36})\/data-api$/);
  if(!match)return reply(404,{message:'Unknown route'});
  if(!['GET','PUT'].includes(request.method))return reply(405,{message:'Method not allowed'});
  const actor=await authenticate(identify,request);
  if(actor instanceof Response)return actor;
  const environment=match[1]!;
  try {
   const answer=(status:number,data:DataApiState,write=true)=>bindReadyEnvironmentPublication(reply(status,{data}),catalog,actor,environment,data.runtime,write);
   if(request.method==='GET')return answer(200,catalog.dataApi(actor,environment,['owner','admin','viewer']),false);
   let input:unknown;
   try{input=await request.json();}catch{return reply(400,{message:'Invalid JSON'});}
   await refreshCurrentManagement();
   if(!input||typeof input!=='object'||Array.isArray(input)||Object.keys(input).some(key=>!['schemas','extraSearchPath','maxRows'].includes(key)))
    return reply(400,{message:'Send {"schemas": [...], "extraSearchPath": [...], "maxRows": null}'});
   const value=input as Record<string,unknown>;
   const settings=validateDataApi({schemas:value.schemas,extraSearchPath:value.extraSearchPath,maxRows:value.maxRows});
   return answer(202,catalog.requestDataApi(actor,environment,settings));
  } catch(error) {
   if(error instanceof DataApiError)return reply(400,{message:error.message});
   const message=error instanceof Error?error.message:'';
   if(message==='Forbidden')return reply(403,{message:'Forbidden'});
   if(message==='Environment is not ready'||message==='Data API change in progress')return reply(409,{message});
   return reply(500,{message:'Management operation failed'});
  }
 };
}
