import type {Database} from 'bun:sqlite';
import type {Catalog} from '../src/control/catalog';

/** Direct fixture access without widening the production Catalog API. */
export function catalogTestDb(catalog:Catalog):Database {
 return (catalog as unknown as {db:Database}).db;
}
