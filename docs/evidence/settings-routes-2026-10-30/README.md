# Settings routes, 2026-10-30

- `mail-settings-names.json`: live check of Confirm email and the mail limits set through the sign-in settings
  (`/usr/bin/python3 lab/auth-names-check.py --only mail_settings`). Pinned Auth, throwaway database and mail catcher on a private
  internal network, one Auth at a time.
- `*.png`: the Emails page, SMTP Settings, rendered by headless Chrome from the console's own components with a stubbed API: custom SMTP
  off with the defaults, off with Confirm email on (the warning), on (dark), right to left, and phone width (light and dark).
