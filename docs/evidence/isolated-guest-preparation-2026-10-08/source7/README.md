Source7 isolated guest preparation repair, 2026-10-08

The exact Source7 candidate and ten actual regression fixtures are preserved with their SHA256 and sizes. The local fixtures passed in 0.293 seconds under the recorded finite service profile. The independent reviewer read the complete two-file change and accepted the implemented repair only, without rerunning the fixtures.

Source5 and Source6 records remain historical. Their inherited child file size limit also restricted regular installation files, which would prevent large dpkg and Bun outputs. Source7 limits each diagnostic stream to 1MiB in the parent collector, allows large application files and concurrently drains stdout and stderr. It also accepts a new source archive commit with exact manifest coverage instead of fixing the historical commit and file count.

The CI observation binds commit 0d732333f563055c7e414ff75faca033219cb7f7 to four successful GitHub jobs. It does not execute this new candidate or establish production acceptance. Guest boot, Docker package installation and image import, full Supabase installation and complete restoration remain unexecuted locally. The candidate targets a fixed privileged Linux amd64 disposable guest. Its experiment does not impose workstation helper paths or byte pins on the product.
