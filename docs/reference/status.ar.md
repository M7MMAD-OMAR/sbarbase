[English](status.md)

# الحالة

المكان الوحيد الذي يجمع ما يعمل وما لا يعمل وكل الأرقام. آخر تحديث 2026-09-25. الإصدار الحالي للمصدر: [0.1.0](../../CHANGELOG.ar.md) (2026-09-21)، ومعه ترحيل جيل الحاوية الذي لم يصدر بعد.

كل ما يلي تحقق منه على محطة عمل تطوير واحدة، ما عدا تجربة الخادم الفارغ التي جرت في آلة افتراضية محلية. **لم يُشغَّل شيء على خادم حقيقي بعد.** لا شيء هنا يشهد بالجاهزية للإنتاج أو بالأمان، ولا ندّعي عددًا ثابتًا من المشاريع لكل خادم.

## مجموعات الاختبار، 2026-09-24

شُغّلت من جذر المستودع في حاوية نظيفة فيها Python 3.14 و`cryptography`، بصلاحيات root. لا تشغّل هذه المجموعات أي حاوية، وتنجح أيضًا دون الوصول إلى أي خدمة Docker، وهذا ما يفحصه CI الآن.

| المجموعة | الأمر | النتيجة |
|---|---|---|
| اختبارات Python للوحدات | `DOCKER_HOST=unix:///var/run/docker.sock /usr/bin/python3 -m unittest discover -s lab -p 'test_*.py'` | 642 اختبارًا، OK؛ تُخطّى 4 عند التشغيل بصلاحيات root أو حين يكون `/usr/bin/python3` أقدم من 3.14، ولكل منها سببه؛ لا يُتخطّى شيء على CI |
| اختبارات Bun (الجذر) | `bun test` | 101 نجاح، 0 فشل، 620 تأكيدًا، 20 ملفًا (الملفات الـ19 في `tests/` ومعها `website/tests/site.test.ts`) |
| الموقع | `cd website && bun run build && bun test` | البناء OK؛ 2 نجاح، 0 فشل، 40 تأكيدًا |
| فحص أنواع لوحة الإدارة | `bun run typecheck:ui` | ينجح |

سجّلت صفحات سابقة مجاميع أخرى (مثلًا 575 اختبار Python و87 اختبار Bun عند بوابة إصدار 0.1.0، و625 و93 على محطة العمل في 2026-09-23). كانت صحيحة لتاريخها ونطاقها، وهذا الجدول يحل محلها.

في 2026-09-25 ضمّت مجموعة Python على 765 اختبارًا: نجحت (`OK`) دون تخطٍّ على محطة العمل، و`OK (skipped=2)` عليها حين لا تصل إلى أي خدمة Docker، وفي حاوية `python:3.12` مؤقتة `OK (skipped=6)` بصلاحيات root و`OK (skipped=4)` بمستخدم عادي. كل اختبار يُتخطّى يذكر ما ينقص الجهاز: أن يعمل بغير root لفحص رفض الأذونات، أو `/usr/bin/python3` بإصدار 3.12 وخدمة Docker لسكربت القبول، أو `systemd-analyze`، أو جهاز تخزين معروف للمسار `/`.

### إصدارات Python، 2026-09-25

صار الفحص المسبق يقبل `/usr/bin/python3` بإصدار 3.12 أو أحدث، فيقبل المفسّرين اللذين يأتيان مع Ubuntu 24.04 وDebian 13. للتحقق من أن الشيفرة تعمل عليهما، شُغّلت مجموعة اختبارات Python نفسها في حاويات مؤقتة بمستخدم عادي (uid 1000)، مع ربط نسخة المستودع، وBun في المسار، ودون أي خدمة Docker. استخدم Ubuntu وDebian حزمتيهما `python3` و`python3-cryptography`، واستخدمت صور `python:` أحدث إصدار من `cryptography`. وينجح `python -m compileall lab deploy` عليها كلها.

| المفسّر | النتيجة |
|---|---|
| Ubuntu 24.04: `python3` 3.12.3، و`python3-cryptography` 41.0.7 | 768 اختبارًا؛ تُخطّى 3؛ يفشل 6، وكلها مرتبطة بالمضيف (انظر أدناه) |
| Debian 13: `python3` 3.13.5، و`python3-cryptography` 43.0.0 | 768 اختبارًا؛ تُخطّى 3؛ يفشل 7، مرتبطة بالمضيف |
| `python:3.12` (3.12.14)، و`python:3.13` (3.13.15) | 768 اختبارًا؛ تُخطّى 3؛ تفشل الـ7 نفسها |
| `python:3.14` (3.14.7)، للمقارنة | 768 اختبارًا؛ تُخطّى 3؛ تفشل الـ7 نفسها |
| `/usr/bin/python3` 3.14.7 على محطة العمل | 768 اختبارًا، OK |

تفشل الاختبارات السبعة نفسها في حاوية المقارنة، فسببها الحاوية لا إصدار المفسّر. خمسة منها تحتاج جهاز تخزين فعليًا خلف `/` (ترفض حدود الإدخال والإخراج بالرسالة `io_device_unavailable`)، وواحد يحتاج `systemd-analyze`، وواحد يحتاج مجلدًا منزليًا للمستخدم uid 1000، وهو موجود في صورة Ubuntu وحدها. أما الثلاثة المتخطّاة فتحتاج خدمة Docker أو مصدر جهاز لـ`/`. لم يُجرَّب تثبيت كامل بعد على Ubuntu 24.04 ولا على Debian 13؛ النظام الوحيد المجرَّب هو Fedora 44.

## الأدلة الحية

تشغّل الفحوص الحية حاويات حقيقية وتكتب نتائجها في [docs/evidence](../evidence/). كل ملف يسمّي نطاقه، والأعداد في الملفات المختلفة متداخلة فلا تُجمع. العدد أدناه هو المسجل في الملف.

### المنصة والإدارة

| ماذا | الدليل | الفحوص |
|---|---|---|
| Auth مخصصة للإدارة، والعضويات، والـAPI المركّبة | [upstream-management-checks.json](../evidence/upstream-management-checks.json) | 28 |
| إعداد أول مشغّل، والتعافي من الانقطاع، والاكتشاف | [bootstrap-checks.json](../evidence/bootstrap-checks.json) | 18 |
| مفاتيح تصدرها الإدارة، والوصول عبر SDK، والإلغاء | [connection-checks.json](../evidence/connection-checks.json) | 9 |
| بناء لوحة الإدارة وتقديمها كملفات ثابتة | [console-build.json](../evidence/console-build.json)، [console-serve.json](../evidence/console-serve.json) | نجح؛ 19 |

### البيئات والعزل

| ماذا | الدليل | الفحوص |
|---|---|---|
| أربع بيئات: عزل Auth وRLS وبيانات الاعتماد والرموز | [four-environment-component-checks.json](../evidence/four-environment-component-checks.json) | 97 |
| الشيء نفسه عبر SDK وبوابة المفاتيح | [four-environment-sdk-checks.json](../evidence/four-environment-sdk-checks.json) | 44 |
| PostgreSQL الأصلي من Supabase، بيئتان، وترحيلات Auth حقيقية | [upstream-environment-checks.json](../evidence/upstream-environment-checks.json) | 40 |
| بيئة التشغيل الدائمة: عامل مستمر، ووحدات تخزين، وإعادة إنشاء الحاويات | [durable-upstream-checks.json](../evidence/durable-upstream-checks.json) | 25 |
| Storage مشترك مع تجربة لاستعادة قاعدة البيانات والملفات | [storage-recovery-checks.json](../evidence/storage-recovery-checks.json) | 122 |
| تهيئة أولية جديدة مغلقة: Auth وREST وStorage المشترك | [upstream-closed-bootstrap-checks.json](../evidence/upstream-closed-bootstrap-checks.json) | 122 |
| تشبّع حد الاتصالات مع استمرار خدمة الجار | [connection-limit-checks.json](../evidence/connection-limit-checks.json) | 7 |
| بيئة مزدحمة تستعير أماكن البوابة الفارغة، وتبقى الحصة كاملة لكل جارة نشطة مؤخرًا، ولا يُطلق تنبيه التشبّع إلا الرفض (HTTP محلي، وخادم خلفي مضبوط، لا Supabase) | [fair-share-checks.json](../evidence/fair-share-checks.json) | 7 |
| الاستجابة للضغط على حاوية مؤقتة | [pressure-response-checks.json](../evidence/pressure-response-checks.json) | 9 |

### التجهيز والأمان عند الانهيار

| ماذا | الدليل | الفحوص |
|---|---|---|
| دورة حياة عامل جديد مع الإيصالات والعقود وفحوص SDK | [fresh-worker-checks.json](../evidence/fresh-worker-checks.json) | 76 |
| قتل العامل الأصلي بـSIGKILL بعد النية أو بعد الشاهد | [worker-hba-crash-after-intent.json](../evidence/worker-hba-crash-after-intent.json)، [worker-hba-crash-after-witness.json](../evidence/worker-hba-crash-after-witness.json) | 90 لكل منهما |
| تسوية الإيصالات عبر المشرف الحقيقي | [worker-receipt-checks.json](../evidence/worker-receipt-checks.json) | 13 |
| قتل المشرف بـSIGKILL أثناء الفحص المسبق | [active-preflight-crash-checks.json](../evidence/active-preflight-crash-checks.json) | 25 |
| ترحيل الجيل، خمس نقاط انهيار بـSIGKILL على بيئة الاختبار المؤقتة | [fresh-worker-generation-crash-all.json](../evidence/fresh-worker-generation-crash-all.json) | 196 |
| استبدال ملف HBA كاملًا | [upstream-atomic-hba-checks.json](../evidence/upstream-atomic-hba-checks.json) | 36 |
| انقطاع جزئي في قاعدة البيانات، على المكوّن وعلى الأصل | [partial-database-crash-checks.json](../evidence/partial-database-crash-checks.json)، [upstream-partial-database-crash-checks.json](../evidence/upstream-partial-database-crash-checks.json) | 64؛ 65 |
| تبنّي المصدر المحفوظ تحت سلطة HBA | [retained-source-adoption.json](../evidence/retained-source-adoption.json) | مسجّل |

### الاستعادة

| ماذا | الدليل | الفحوص |
|---|---|---|
| تصدير مشفّر خلف سياج | [recovery-export-checks.json](../evidence/recovery-export-checks.json) | 34 |
| الاستعادة إلى محرك منفصل | [independent-database-restore.json](../evidence/independent-database-restore.json) | 49 |
| Auth وREST الأصليان على النسخة المستعادة | [independent-service-checks.json](../evidence/independent-service-checks.json) | 11 |
| Storage وRLS المستخدم النهائي على النسخة المستعادة | [independent-storage-checks.json](../evidence/independent-storage-checks.json)، [independent-storage-rls-checks.json](../evidence/independent-storage-rls-checks.json) | 9؛ 14 |
| انقطاع pg_restore يتراجع بنظافة | [recovery-interruption-checks.json](../evidence/recovery-interruption-checks.json) | 38 |
| SDK عبر البوابة إلى البيئة المنقولة | [cutover-sdk-checks.json](../evidence/cutover-sdk-checks.json) | 11 |
| الجيران غير المتأثرين أعيد تشغيلهم على المصدر | [cutover-neighbor-checks.json](../evidence/cutover-neighbor-checks.json) | 16 |

### الاستيراد من Supabase (الفحص)

| ماذا | الدليل | الفحوص |
|---|---|---|
| فحص قاعدة بيانات المصدر للقراءة فقط: حالات الرفض والتحذيرات والخطوات اليدوية قبل أي تفريغ، على الصورة مثبّتة الإصدار وبدور `postgres` | [import-inspect-checks.json](../evidence/import-inspect-checks.json) | 6 |

الاستيراد الكامل (المخطط والمستخدمون والصفوف والملفات والتحقق) هو `lab/import_project.py`؛ وتشغيله من البداية إلى النهاية في جدول Docker أدناه.

### التثبيت عبر Docker والنسخ الاحتياطي اليومي وStudio والترقيات (CI، جهاز نظيف)

على جهاز GitHub نظيف ليس عليه غير Docker، يشغّل CI التثبيت عبر Docker مع كل تغيير ([التثبيت عبر Docker](../guides/docker.ar.md)). ليس خادمًا حقيقيًا: لا شبكة عامة ولا شهادة ولا إعادة تشغيل للجهاز.

| ماذا | الدليل | الفحوص |
|---|---|---|
| البناء والتشغيل، أول مشغّل، أول مشروع عبر supabase-js، إعادة تشغيل الحاوية، إيقاف نظيف | [docker-install-checks.json](../evidence/docker-install-checks.json) | 15 |
| نسخ بيئة واحدة وهي تعمل، تغيير الصفوف والمستخدمين والملفات، الاستعادة، المقارنة مع النسخة، حذف الحالة المحفوظة جانبًا | [docker-backup-restore.json](../evidence/docker-backup-restore.json) | 15 |
| Supabase Studio لبيئة واحدة: يُشغَّل عند الطلب، ويُدخل إليه بتذكرة الواجهة، وتُستخدم عبره قائمة الجداول وSQL والمستخدمون والحاويات، ويُرفض دون الجلسة أو بجلسة بيئة أخرى، ثم يُوقف ([دليل Studio](../guides/studio.ar.md)) | [docker-studio-checks.json](../evidence/docker-studio-checks.json) | 22 |
| إعدادات تسجيل الدخول: حفظ عنوان الموقع وعناوين إعادة التوجيه ومزوّد GitHub وتطبيقها بإعادة إنشاء Auth، وبدء OAuth ورابط بريد دون مفتاح، وإنشاء حساب بعد إعادة الإنشاء، ثم إزالة المزوّد ([تسجيل الدخول](../guides/sign-in.ar.md)) | [docker-sign-in-checks.json](../evidence/docker-sign-in-checks.json) | 15 |
| Realtime لبيئة واحدة: تشغيله وبدؤه من المشرف، والبث والحضور وتغيير في قاعدة البيانات بين عميلين من supabase-js عبر البوابة، وواجهة البث REST، ورفض مفتاح خاطئ، ثم إطفاؤه ([Realtime](../guides/realtime.ar.md)) | [docker-realtime-checks.json](../evidence/docker-realtime-checks.json) | 19 |
| النسخ المشفّرة خارج الخادم: الإعداد من stdin، ونسخة احتياطية تُنسخ وحدها إلى تخزين متوافق مع S3 (‏MinIO مؤقت)، ولا يُخزَّن إلا نص مشفّر، ورفض عبارة مرور خاطئة، وإعادة النسخة واستعادتها ([النسخ الاحتياطي والاستعادة](../guides/backup-and-restore.ar.md#نسخ-خارج-الخادم)) | [docker-offsite-checks.json](../evidence/docker-offsite-checks.json) | 12 |
| الوصول المباشر إلى قاعدة البيانات: تشغيل حساب المطوّر، وعميل PostgreSQL عبر المنفذ يشغّل ترحيلًا بأسلوب Supabase (سياسة، ومشغّل على `auth.users`، وسياسة Storage)، ورفض المستخدم الخارق وقواعد البيانات الأخرى، وتجديد كلمة المرور، ثم الإطفاء ([الوصول إلى قاعدة البيانات](../guides/database-access.ar.md)) | [docker-database-checks.json](../evidence/docker-database-checks.json) | 16 |
| Edge Functions لبيئة واحدة: نشر مجلد دوال Supabase بأمر واحد، واستدعاؤها بـ supabase-js، ودالة تستخدم supabase-js من npm بصلاحية الخدمة على Auth وREST وStorage الخاصة ببيئتها، وخطاف ويب دون مفتاح، وسر، وإعادة نشر، وحذف، ثم الإطفاء ([Edge Functions](../guides/edge-functions.ar.md)) | [docker-functions-checks.json](../evidence/docker-functions-checks.json) | 24 |
| السجلات والمقاييس لبيئة واحدة: عدّ الطلبات عبر البوابة مع الأخطاء وأزمنة الاستجابة واستهلاك الخدمات للذاكرة، وقراءة سجل الطلبات وسجلات Auth وREST وStorage عبر واجهة الإدارة، دون أي مفتاح أو رمز في أي رد ([السجلات والمقاييس](../guides/logs-and-metrics.ar.md)) | [docker-observe-checks.json](../evidence/docker-observe-checks.json) | 20 |
| الرفع: ملف بحجم 20 MiB عبر البوابة ثم تنزيله مطابقًا بايتًا ببايت، وقبول ملف أصغر قليلًا من حد 50 MiB ورفض ملف أكبر منه، ثم الأمر نفسه بعد أن أعاد `SBARBASE_UPLOAD_LIMIT_MB` جديد إنشاء Storage | [docker-upload-checks.json](../evidence/docker-upload-checks.json) | 20 |
| الاستيراد إلى بيئة جديدة من بيئة أخرى تقوم مقام مشروع Supabase: يسجّل المستخدم الدخول بكلمة مروره القديمة، وينتقل أمان مستوى الصفوف والملف الخاص مع سياسة Storage الخاصة به ومشغّل التسجيل، ويُرفض استيراد ثانٍ إلى البيئة الممتلئة ([الانتقال من Supabase](../guides/move-from-supabase.ar.md)) | [docker-import-checks.json](../evidence/docker-import-checks.json) | 12 |
| تغيير مفتاح التوقيع وRealtime تعمل: رفض الجلسة القديمة ورمز التحديث القديم ورمز `service_role` القديم في Auth وREST وStorage، وقبول تسجيل دخول جديد بالمفتاح العام نفسه في كل مكان، وبقاء Realtime تجيب ([مفتاح التوقيع](../guides/signing-keys.ar.md)) | [docker-signing-checks.json](../evidence/docker-signing-checks.json) | 18 |
| ترقية إلى PostgREST أحدث عبر `lab/upgrade.py`، ثم إصدار معطوب يعود منه المشرف وحده، مع بقاء المستخدمين والهويات وحاويات Storage والملفات كما هي ([الترقيات](../guides/upgrades.ar.md)) | [docker-upgrade-checks.json](../evidence/docker-upgrade-checks.json) | 10 |

### خادم فارغ، بمحاكاة في آلة افتراضية محلية

آلة Fedora 44 Cloud افتراضية مؤقتة فيها 4 معالجات افتراضية و6 GiB، ونسخة نظيفة من المستودع، ثم اختبار الاستلام بأمر واحد مع `--install-unit --first-project`، ثم إعادة تشغيل ([lab/vm-rehearsal.sh](../../lab/vm-rehearsal.sh)). ليست خادمًا حقيقيًا: لا شبكة عامة ولا شهادة. كشفت التشغيلات عشرة عيوب لم تستطع محطة العمل إظهارها، وأُصلحت كلها مع اختبارات، وهي مدرجة في سجل الملخص.

| ماذا | الدليل | الفحوص |
|---|---|---|
| الملخص: الآلة، والأمر، وإعادة التشغيل، والاستهلاك في الخمول، والعيوب المكتشفة | [vm-empty-server-rehearsal.json](../evidence/vm-empty-server-rehearsal.json) | مسجّل |
| التثبيت، والبدء تحت الإشراف، ولوحة الإدارة، ونطاق الإدارة، وتهيئة المشغّل، والوحدة، والإيقاف النظيف | [vm-empty-server-acceptance.json](../evidence/vm-empty-server-acceptance.json) | 12 |
| المشروع الأول: الدخول، والمشروع، وبيئة جُهّزت في 10 ثوانٍ، والمفتاح، وتسجيل مستخدم في Auth عبر supabase-js، وREST وStorage عبر البوابة، ورفض المفتاح الملغى بـ401 | [vm-empty-server-first-project.json](../evidence/vm-empty-server-first-project.json) | 13 |
| إعادة التشغيل: عادت الخدمة والبيئة دون تدخل | [vm-empty-server-rehearsal.json](../evidence/vm-empty-server-rehearsal.json) | نجح |

في الخمول مع بيئة واحدة، استهلكت الحاويات نحو 250 MiB والمشرف نحو 130 MiB. ما زال الفحص المسبق يحجز حدود الحاويات (2304 MiB لمكان التشغيل ذاك) ومعها 2560 MiB للخادم؛ انظر [اختيار الخادم](../guides/choosing-a-server.ar.md).

### مسار النشر (على محطة العمل فقط)

| ماذا | الدليل | الفحوص |
|---|---|---|
| سكربت استلام الخادم من أوله إلى آخره، مع تثبيت الوحدة بصلاحيات root | [server-acceptance-latest.json](../evidence/server-acceptance-latest.json) | 13 |
| تجربة النشر، دورة حياة المصدر والهدف | [deployment-rehearsal.json](../evidence/deployment-rehearsal.json) | 11 |
| المسار الخاضع للإشراف تحت systemd | [supervised-run.json](../evidence/supervised-run.json) | 10 |
| إنهاء TLS | [tls-termination.json](../evidence/tls-termination.json) | 23 |
| مكان تشغيل هدف الاستعادة | [target-placement-rehearsal.json](../evidence/target-placement-rehearsal.json) | 12 |
| الصور مثبّتة الإصدار موجودة ومطابقة لبصماتها | [pinned-images.json](../evidence/pinned-images.json) | 5 تثبيتات |

المصفوفة المفصّلة للخادم في [جاهزية النشر](deployment-readiness.ar.md).

## قناة التحديث، 2026-09-25

بُنيت في 2026-09-25: وسوم إصدارات موقّعة يُتحقق منها مقابل `deploy/release-signers`، وصنف للإصدار يُحسب من الفرق (آمن، يحتاج إعادة بناء، يحتاج ترحيلًا)، وإشعار وصفحة تحديثات في لوحة الإدارة لمشغّل التثبيت، وتثبيت الإصدارات الآمنة الموقّعة بنقرة، وتحديث تلقائي اختياري داخل نافذة صيانة، ولقطة لحالة التحكم، وحجز لحركة التطبيقات حتى تنجح جولة فحوص سلامة خلال 120 ثانية، والعودة ([الترقيات](../guides/upgrades.ar.md)).

| ما الذي | الدليل | النتيجة |
|---|---|---|
| قناة الإصدارات، وملفات الطلب والإعدادات، وقرار التحديث التلقائي، واللقطة واستعادتها، والاعتماد المشروط بالفحوص (ملف الترقية يضم أيضًا اختبارات الترقية الأقدم) | `lab/test_release_channel.py`، `lab/test_updates.py`، `lab/test_upgrade.py`، `lab/test_upgrade_health.py` | 86 اختبار Python، نجحت على محطة العمل |
| حجز الحركة، ومسارات التحديث، ومنطق صفحة لوحة الإدارة | `tests/hold.test.ts`، `tests/updates-routes.test.ts`، `tests/updates-ui.test.ts` | 42 اختبار Bun، نجحت |
| حالات CI الثلاث (إصدار يرحّل الفهرس ثم يفشل، وإصدار يفشل في فحوص السلامة، ووسم غير موقّع) | لا شيء بعد | **لم تُشغَّل** |
| تجربة كاملة في الآلة الافتراضية لترقية حقيقية والعودة منها عبر القناة | لا شيء بعد | **لم تُشغَّل** |

اختبارات وحدات فقط. لم يُسرد بعد أي مفتاح توقيع في `deploy/release-signers`، فيُرفض كل إصدار على أنه غير موقّع حتى يُسرد مفتاح. وترقيات CI والآلة الافتراضية في الجداول أعلاه سلكت `lab/upgrade.py start --to`، لا القناة ولا لوحة الإدارة ولا التحديث التلقائي.

## الموارد

يحتاج كل تشغيل إلى حدود ذاكرة حاوياته مضافًا إليها احتياطي 2560 MiB: حدود بمقدار 1792 MiB على خادم فارغ، و512 MiB أخرى لكل بيئة، وسقوف معالج لا تتجاوز ضعف عدد الأنوية بعد إبقاء نواة واحدة للخادم. يسمح قيد مؤقت في المختبر بأربع بيئات على الأكثر لكل تثبيت: ترفض API الإدارة البيئة الخامسة بـ409 قبل وضعها في الطابور، وما زال قيد بيئة التشغيل يفرض الحد نفسه. السقوف المضبوطة لمكان التشغيل المشترك المحفوظ على محطة العمل هي 5888 MiB من ذاكرة الحاويات و5.75 معالج، مقبولة تحت سقف 6 GiB و6 معالجات مع احتياطي 2560 MiB للخادم. هذه حدود تخصيص، لا طلب مقيس ولا توصية بعتاد. لم يُقس الحمل المختلط المستمر، فلا حد أقصى متحقق منه عند 10 بيئات أو 100، وعدد الزوار اليومي وحده لا يكفي لتحديد حجم الخادم.

## ما لا وجود له بعد

- تجربة على خادم حقيقي. نجح التثبيت على خادم فارغ في آلة افتراضية محلية فقط.
- مجمّع الاتصالات وcron، و`SUPABASE_DB_URL` داخل Edge Functions.
- الاستعادة إلى نقطة زمنية، وأهداف SSH أو rsync للنسخ خارج الخادم (المدعوم الآن تخزين متوافق مع S3 فقط)، وإعادة بناء خادم مفقود كله من النسخ الخارجية بخطوة واحدة (نسخة كل بيئة تُستعاد، وعلى تثبيت جديد بعد أن يعيد `sbarbase relink` إنشاء عميلها ومشروعها وبيئتها بمعرّفاتها الأصلية؛ ويسجّل بيان التثبيت ما تحتاجه النسخ، أما الأعضاء والمفاتيح والإعدادات فلا تنتقل معها بعد، ولهذا المسار اختبارات وحدات فقط دون تجربة على جهاز ثانٍ).
- استيراد مخططات غير `public`، وأسرار Vault، ومهام cron من مشروع Supabase (أما [الاستيراد](../guides/move-from-supabase.ar.md) فينقل `public` والمستخدمين والصفوف والملفات).
- التعافي التلقائي من إخفاقات التجهيز في المراحل المتأخرة؛ تمنع المتابعة حتى يطابقها المشغّل.
- اعتماد أي إصدار من الأصل عبر سياسة التحديث. وتشغيل حي لقناة التحديث: التحديث التلقائي موجود ومعطّل افتراضيًا، لكن لا تغطيه إلا اختبارات الوحدات (أعلاه).
- دعم MFA وتحديد معدل محاولات الدخول. نقل المشروع بين العملاء يلغي مفاتيح API، لكنه لا يدوّر مفتاح توقيع JWT ولا كلمة مرور الوصول المباشر إلى قاعدة البيانات. وحذف البيئة يُبقي طبقة تشغيلها (قاعدة البيانات والحاويات والملفات)، فتبقى محسوبة ضمن حد البيئات؛ واسترداد تلك الموارد غير مبني بعد.
- مكان تشغيل على عدة خوادم والتنسيق بينها.
- الرفع القابل للاستئناف (TUS) عبر البوابة؛ أما الرفع العادي فيصل إلى حد الرفع، 50 MiB افتراضيًا.

## الخطوة التالية

الخادم الحقيقي: [المرحلة 1 من خارطة الطريق](../engineering/plans/2026-09-23-roadmap.md). على محطة العمل، جرى ترحيل الجيل للقاعدة المحفوظة بإشراف المشغّل في 2026-09-25، وبقيت أعداد الصفوف كلها كما هي ([الدليل](../evidence/generation-migration-retained.json)). نجح فحص دورة الحياة الدائم، بعد إعادة صياغته ليصبح إيقافًا وإعادة بدء للتشغيل المحفوظ دون حذف أي شيء، في 31 تحققًا ([الدليل](../evidence/durable-lifecycle-restart.json)). وعلى البيئتين نفسيهما نجح حمل SDK المختلط دون أي عملية فاشلة ([الدليل](../evidence/sdk-policy-regression.json))، وفشل تشغيل الطلبات الواصلة المستمرة: انتهت 14 من 600 طلب على البيئة المستهدفة دون أي حالة HTTP، بينما جاءت كل طلبات البيئة المجاورة صحيحة ([الدليل](../evidence/gateway-sustained-failure.json)). وُجد السبب في المستمع المحلي وأُصلح دون Docker: كان الاتصال بعد رفض الطلب يُعلَن عنه أنه سيُغلق لكنه يبقى مفتوحًا، فيعيد العميل استخدامه، ثم يقطع تنظيف يجري بعد ثانية واحدة الطلب الطويل التالي عليه ([التفاصيل](../engineering/RESOURCE-POLICY.md)). لم يُعَد تشغيل الطلبات المستمرة على التشغيل الحقيقي بعد الإصلاح، فيبقى محسوبًا فاشلًا حتى يُعاد. وضع أخذ عينات الضغط ومرحلة الفئة التجريبية لم يُبنيا.
