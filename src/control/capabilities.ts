/** Supported integration contract, not a probe of extension installation or service health. */
export const CAPABILITIES={
 version:1,
 scope:'installation-policy',
 documentation:'https://base.sbarah.com/en/docs/guides/agents',
 workspace:{projects:true,environments:true,gitBranches:false,description:'Environments have independent databases. They are not Git branches.'},
 interfaces:{console:true,studio:'original-upstream',api:'/v1',mcp:'/mcp'},
 extensions:{
  pgmq:{policy:'database-local',activation:'Enable pgmq in Studio Database > Extensions, or run CREATE EXTENSION pgmq WITH SCHEMA pgmq through a writable SQL connection.',prerequisite:'The database must run the Sbarbase PGMQ ownership adapter. Platform startup verifies the root-owned hook without replacing the database container. Reopening Studio reconciles existing pgmq 1.5.1 ownership while retaining queues.',installed:'Query pg_extension in the target environment; this manifest does not report installed state.'},
  pg_stat_statements:{policy:'blocked',reason:'Shared server statistics can disclose other environments. Query Performance in upstream Studio is unavailable on this shared topology.'},
 },
 available:['sql','migrations','auth','storage','realtime','edge-functions','api-keys','logs','security-advisors'],
 unsupported:['hosted-billing','git-branching','clickhouse-log-queries','hosted-health-advisors','graphql'],
 authorization:'Every operation still checks token scope, membership, read-only status and current environment readiness.',
} as const;
