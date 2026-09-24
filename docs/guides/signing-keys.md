[العربية](signing-keys.ar.md)

# Signing key

Each environment has its own JWT signing key. Its Auth signs every session with it, and REST, Storage, Realtime and Edge Functions check tokens against it. Rotate it when it may have leaked, for example after it was pasted somewhere or someone who knew it left.

## Rotate it

1. Open the environment's page in the console.
2. Under **Signing key**, press **Rotate signing key**, then confirm.
3. Wait until it shows **Rotated**. It takes about a minute; longer when Realtime is on.

What changes:

- Every signed-in person is signed out, on every device: their sessions and refresh tokens end. They sign in again with the same email and password.
- A token you minted yourself with the old key, such as a `service_role` token in a script, stops working.
- Studio and Edge Functions get tokens signed with the new key by themselves. A running Studio starts again.

What does not change:

- Publishable keys. They are not JWTs, so your application keeps its key and needs no new build.
- Users, rows, files, policies and passwords.

For a few seconds while Auth and REST restart, some requests to this environment may be refused. Other environments are not touched.

If the rotation stops halfway, the console shows **Not finished**. Press **Rotate signing key** again, or restart Sbarbase: the change is finished with the same new key.

## Through the API

```bash
curl -X POST -H "authorization: Bearer $TOKEN" https://your-console/management/v1/environments/$ENVIRONMENT/signing-key/rotate
```

`GET .../signing-key` shows the state and when it was last rotated. Neither ever answers the key itself.

## Limits

- One key at a time: the old key stops being accepted immediately, with no overlap period in which both work.
- Only owners and admins of the organization can rotate it.
- Daily backups hold no key, so restoring one keeps the current key. A recovery export does hold one: an export made before a rotation restores with the old key, so rotate again after such a restore if the old key leaked.

CI rotates the key of an environment on a clean machine with every change and checks the old session, refresh token and `service_role` token are refused while a new sign-in works everywhere ([evidence](../evidence/docker-signing-checks.json)).
