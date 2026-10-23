import type {Database} from 'bun:sqlite';

/** One ladder per control store file, recorded in PRAGMA user_version, as Catalog.migrate() does.
 * Version 0 is the baseline: the shape every release before the ladder opens (lab/upgrade.py
 * schema_support reads a missing constant as 0), so a store stays at 0 until a real schema step
 * bumps its constant. The version 0 steps must be idempotent: they bring any older shape to the
 * baseline and leave it as it is.
 *
 * Opening reads before it writes: refuseNewer first, before the caller changes the file mode or
 * the journal mode, then migrateStore takes the write lock only when the store is behind or lacks
 * part of its schema, so an open never waits behind another process's writer. */
export function refuseNewer(db:Database,store:string,current:number):number {
  const version=storeVersion(db);
  if(version>current)throw new Error(`${store} schema is newer than this release`);
  return version;
}

/** Runs the steps for the stored version and stamps the current one, in one IMMEDIATE transaction
 * that reads the version again under the lock. Skipped when the version is current and `ready`. */
export function migrateStore(db:Database,store:string,current:number,ready:()=>boolean,steps:(version:number)=>void) {
  if(storeVersion(db)===current&&ready())return;
  db.transaction(()=>{
    const version=refuseNewer(db,store,current);
    steps(version);
    if(version!==current)db.exec(`PRAGMA user_version=${current}`);
  }).immediate();
}

/** Read only: every table with at least these columns, and every index. */
export function hasSchema(db:Database,tables:Record<string,string[]>,indexes:string[]=[]):boolean {
  for(const [table,wanted] of Object.entries(tables)) {
    const columns=db.query<{name:string},[]>(`PRAGMA table_info(${table})`).all().map(column=>column.name);
    if(!columns.length||wanted.some(column=>!columns.includes(column)))return false;
  }
  return indexes.every(index=>db.query("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?").get(index)!==null);
}

export function storeVersion(db:Database):number {
  return db.query<{user_version:number},[]>('PRAGMA user_version').get()!.user_version;
}
