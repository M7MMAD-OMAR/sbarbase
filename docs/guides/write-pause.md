[العربية](write-pause.ar.md)

# Pausing writes to one environment

Some operator work can only be undone by restoring a backup: an upgrade whose Auth, Storage or Realtime release migrates schemas, or a restore itself. A restore brings a database back to the moment of its backup and discards every write made after it. Pause the environment's writes before that backup, and keep the pause until the checks after the change pass. Writes the gateway admitted just before the pause either finish before the writers stop, which is before the pause holds and before the backup, or are cut off and rolled back; the application may see an error even for a write that finished; after that no application write reaches the environment through the gateway or the database port until you resume. Code inside the installation can still reach its services over the internal network; see the limits below.

## Commands

```sh
python3 lab/write_pause.py pause <environment>
python3 lab/write_pause.py status
python3 lab/write_pause.py resume <environment>
```

`<environment>` is the runtime id (`e_` and 24 hex characters: the path in the environment's API URL, without its leading `/`).

## What a pause does

The pause is written to `.lab/upstream/write-pauses/<environment>.json` first. Then, in this order:

1. **The gateway admits nothing new.** The environment's routing goes into maintenance, the same state a move to another placement uses. REST, Auth, Storage and Functions requests and new Realtime sockets get 503, "Environment temporarily paused".
2. **Studio and the direct database port close.** Studio is stopped, and the environment's developer login (the one the database port admits) and its Studio login are turned off, with their open sessions ended.
3. **Whatever was in flight is cut off.** The pause first waits 15 seconds, longer than the gateway takes to read a request body, so every request admitted before the pause has reached a service. Then the environment's Auth, REST, Realtime and Functions containers and the shared Storage process stop, every session left in the environment's database is ended and waited out, and they start again, except Edge Functions, which stay stopped until `resume`; Auth and REST must answer before the pause goes on. Every running request, upload, function and open Realtime socket ends, and its open transaction rolls back. Restarting Storage pauses Storage for every environment for a few seconds and ends the uploads other environments have in flight, as `restore-storage` does. A pause refuses to start this while a backup or a restore runs.

Only then is the pause recorded as held. `status` shows `held`; take the backup after that.

The environment's own services run again, so an upgrade can migrate them, and its health checks still probe them directly; only the probes through the gateway are skipped for a paused environment. Opening Studio, turning direct database access on and turning Edge Functions on are refused while the pause holds (the console's requests to turn database access or Edge Functions on wait, see Resuming; opening Studio fails, so open it again after `resume`). They are refused as soon as a pause is recorded, which it is when it starts. A Studio start that has already opened its login finishes first, and the pause then closes it; one that has not reached that step fails, so open Studio again after `resume`. While any other runtime operation runs, turning Edge Functions on or off and turning database access off included, `pause` and `resume` refuse and ask you to try again when it ends. Turning database access on blocks them only while it checks and while it opens the login, not while it gives the login its rights on the tables; a `resume` that meets that step for the same environment stops, and you run it again. A console request that meets a pause at that very moment stays pending and is applied after `resume`; if it ever shows as failed, repeat it after `resume`.

The pause holds across restarts until you resume it. Running `pause` again while it holds changes nothing. If `pause` stops partway, it says so and starts Auth, REST, Realtime and Storage again if it had stopped them; Edge Functions stay stopped until `resume`, and Studio stays closed until someone opens it. If the routing change's answer is lost, the pause reads routing back before it goes on, so a change that happened is kept as its own and one that did not is never claimed later. If the pause itself stops during that call, killed or cut off, or the read fails, the next `pause` or `resume` reads routing: when the environment is not in maintenance it simply goes on. When it is, the pause cannot tell whose maintenance it is, so it stops and asks you to check that no other operation, such as a cutover export or a move, holds the environment, and then settle it with `write_pause.py pause <environment> --adopt-maintenance`. The record stays, and it refuses turning Studio, direct database access and Edge Functions on until you run `pause` again to finish or `resume` to release it; what was already on may still run until then, so the pause protects nothing before `status` shows `held`. `resume` on a pause that never held also starts the services it stopped, Auth, REST, Realtime, Functions and Storage; Studio stays closed until someone opens it.

## Resuming

`resume` first records the pause as resuming, so `status` never shows it held once writes may land again. It then takes the environment out of maintenance, turns the developer login on if direct database access is on for the environment, and removes the record last; if it stops partway, the pause stays recorded as resuming and you run it again. Access turned off during the pause stays off. The developer login comes back the way turning access on brings it, with the password the console saved last, so a password reset made during the pause takes effect only now and the old password stays revoked. Requests made in the console during the pause to turn direct database access or Edge Functions on, a password reset included, stay pending and are applied after `resume`. The Studio login stays closed until someone opens Studio. Applications reach the environment again at once.

## Limits

- A pause refuses an environment that another operation already holds in maintenance; that operation releases it. It also refuses an environment that has been moved to another placement, for good: this pause covers only environments that never moved. If another operation takes the maintenance over after a pause began, `pause` and `resume` both refuse and leave it to that operation; run `resume` once it is released.
- It is not a fence against the operator, and it does not stop the environment's own service logins.
- It closes the gateway and the database port, not the installation's internal network. While it holds, the environment's Auth, REST and Realtime and the shared Storage run and stay reachable by their container names from code running inside the installation, such as another environment's Edge Functions. A write sent that way during the pause is lost by a later restore. This is true of the internal network at all times, not only during a pause.
- It covers one environment at a time. Pause each environment an upgrade touches.
- A restart of Sbarbase while paused, such as the one an upgrade does, and a signing key rotation leave the environment's Edge Functions stopped; `resume` starts them again as a fresh container, with the current image and signing key, as turning them on does.
- If the `pause` process is killed while the writers are stopped, they stay stopped, and so does the shared Storage, for every environment, until you run `pause` or `resume` again, or Sbarbase starts again, which starts them but leaves Edge Functions stopped.
- It has unit tests only; a live drill on the lab host is planned before it is used for an upgrade.
