# Published environment resume

Routine source startup now calls `Runtime.resume` rather than provisioning each published environment again. The purpose is to separate existing-service startup from authority to create or repair an environment.

Resume requires a settled installation, published endpoints, retained credentials, an unfenced source and retained Auth/REST containers. REST deadlines are read and validated, not rewritten. The service launcher refuses creation if a retained container disappears. A missing Storage tenant is an explicit reconciliation error, never an automatic POST. The existing service configuration/image drift validation remains in force.

No native environment role, database, extension, schema or privilege provisioning runs in this resume path. Endpoint addresses are still refreshed after service readiness. Starting Auth or Storage can perform service-owned migrations, and outer Runtime.start still manages shared HBA and infrastructure. This is not read-only startup or a whole-operation revocation guarantee.

Four isolated tests cover absence of native repair, publication/deadline refusal, retained-container disappearance and missing-tenant refusal. Existing startup selection still excludes unpublished credential reservations and moved/fenced environments. Independent review found no new must-fix in this scope.

The full Python suite passes 110 tests. The retained combined-supervisor rehearsal passes 13 checks: console and all four environments respond, the existing capacity-refused job settles without allocating an environment, and all owned runtimes stop afterward. Retained credentials were unchanged; no pending receipt remains. This rehearsal exercises real resume, not database-stage crash recovery.

Next bind worker-created environments to exact receipt identities and the guarded SQL executor. Keep direct operator reconciliation explicit. Do not infer permission to replay unknown database, configuration or service effects from successful resume.
