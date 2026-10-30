# Supabase cloud Edge Functions pages, observed 2026-10-30

Read only observation of one cloud project with five functions, signed in by the owner in their own Chrome. Nothing was saved, deployed, tested or deleted; the Test panel and two menus were opened and closed without sending. No project names, references, keys or secret values are recorded here.

## Routes and frame

Left sub menu "Edge Functions", group MANAGE: Functions, Secrets. Routes under `/functions`: the list, `/functions/secrets`, and per function `/functions/{slug}` (Overview), `/logs`, `/code`, `/details` (Settings). The function header (breadcrumb "Edge Functions > name") carries three buttons on every tab: Docs, Download, Test. Tabs, in order: Overview, Logs, Code, Settings.

## Function list

Title "Edge Functions", grey line "Run server-side logic close to your users". Buttons: Docs, Examples, and a green "Deploy a new function" menu with three entries: Via Editor (write and deploy in the browser), Via CLI (write locally, deploy with the CLI), Via AI Assistant. Toolbar: search "Search function names", a sort menu "Sorted by name" (by name, by created at, by updated at; each opens a direction submenu), Refresh, and "Viewing N functions in total". Table columns: NAME, URL (with a copy icon), CREATED and UPDATED (relative, "a month ago"), DEPLOYMENTS (the version number). A row opens the function.

## Overview

Top: the name, the URL with copy, "last deployed" relative time, and a range switch: 15 min, 1 hour (default), 3 hours, 1 day. Sections: stat strip TOTAL INVOCATIONS, 5XX RATE, 4XX RATE above a chart, with an open-logs icon; "Errors in the last 24h" with a View logs button and the line "There have been N invocations since last deploy and no errors."; "Performance": AVERAGE EXECUTION TIME and MAX EXECUTION TIME with a chart; "Usage": AVERAGE CPU TIME, MAX CPU TIME, AVERAGE MEMORY USAGE, HEAP and EXTERNAL percentages. Empty charts say "No data to show. It may take up to 24 hours for data to refresh".

## Logs

A filter bar "Filter by Time range, Log Type, Level...", a refresh, a column toggle, a download and a Live switch. A time histogram above a table with DATE, METHOD, PATHNAME, EVENT MESSAGE. Empty state "No results found".

## Code

Left pane FILES with an "Add File" button and a file list (a function's own files, and shared files shown as `../_shared/...`), a Monaco style editor on the right with line numbers and highlighting, and a footer button "Deploy updates".

## Settings

"Function configuration" section: Name (text, "Your slug and endpoint URL will remain the same"), a switch "Verify JWT with legacy secret" ("Requires a JWT signed only by the legacy secret in the Authorization header. The anon key satisfies this. Recommended: OFF with JWT and custom auth logic in your function code."), and a "Save changes" button that stays disabled until something changes. "Invoke function": tabs cURL, JavaScript, Swift, Flutter, Python with a "Show anon key" button; the cURL sample sends POST with `Authorization: Bearer`, `apikey` and `Content-Type` headers and `{"name":"Functions"}`. "Develop locally": three lines, `functions download NAME`, `functions deploy NAME`, `functions delete NAME`, each with a copy icon. "Delete function": "Once your function is deleted, it can no longer be restored", a "Delete edge function" button.

## Test

A right side panel "Test NAME": HTTP Method (a select, POST by default), Request Body (default `{ "name": "Functions" }`), Headers (name and value rows, "Add header" with a presets menu, a delete icon per row), Query Parameters (name and value rows, "Add parameter"), and below a response area that says "Send your first test request"; the footer button is "Send Request".

## Secrets

Title "Edge Function Secrets", "Manage encrypted values for your functions". Section ADD OR REPLACE SECRETS: Name ("A unique name for your secret", placeholder `e.g. CLIENT_KEY`), Value (multi line, "Supports multi-line values such as PEM keys, JSON, or functions", with a show and hide eye), a delete icon, a footer line "Insert or update multiple secrets at once by pasting key-value pairs", and buttons "Add another" and "Save". Section "Custom secrets" ("Secrets you have defined for this project") with a search and a table NAME, DIGEST (SHA256), UPDATED; empty state "No custom secrets created". Section "Default secrets" ("Reserved secrets available in every project", a Docs button), table NAME, DESCRIPTION: SUPABASE_URL, SUPABASE_DB_URL, SUPABASE_PUBLISHABLE_KEYS, SUPABASE_SECRET_KEYS, SUPABASE_ANON_KEY (deprecated), SUPABASE_SERVICE_ROLE_KEY (deprecated), SUPABASE_JWKS, SB_REGION, SB_EXECUTION_ID, DENO_DEPLOYMENT_ID.

## Function record

From the read only management listing: id, slug, name, status, version (the DEPLOYMENTS number), created_at, updated_at (milliseconds), verify_jwt, import_map, entrypoint_path, ezbr_sha256 (a digest of the deployed bundle).
