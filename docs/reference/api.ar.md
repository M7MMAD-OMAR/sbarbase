[English](api.md)

# واجهة API

هذه المسارات يخدمها خادم الحلقة المحلية (`lab/upstream-server.ts`). كل مسار يبدأ بـ`/management/` ينتمي إلى واجهة الإدارة، وما عداه ينتمي إلى بوابة التطبيقات. قُرئت من الكود في 2026-09-23، والكود هو المرجع:

- التوجيه بين المجموعات الثلاث: [src/control/application.ts](../../src/control/application.ts)
- بيانات الإدارة الوصفية: [src/control/http.ts](../../src/control/http.ts)
- المفاتيح وتفاصيل الاتصال: [src/control/key-http.ts](../../src/control/key-http.ts)، ويوزّعها [src/control/handler.ts](../../src/control/handler.ts)
- البوابة: [src/gateway/managed.ts](../../src/gateway/managed.ts) و[src/gateway/handler.ts](../../src/gateway/handler.ts) و[src/gateway/concurrency.ts](../../src/gateway/concurrency.ts)

تُخدم لوحة الإدارة وملفاتها الثابتة إلى جانب هذه المسارات عبر `lab/ui-static.ts`. يرفض خادم الحلقة المحلية أي ترويسة `Host` غير `127.0.0.1` أو `localhost`، والوصول العام يمر عبر وكيل TLS (انظر [نشر الخادم](../guides/server-deployment.ar.md)).

## دخول الإدارة

تُمرَّر هذه الطلبات إلى نطاق الإدارة، أي نسخة Auth المخصصة للمشغّلين، بمفتاح التوجيه العام `sb_publishable_sbarbase_local_management`. وجّه Supabase SDK إلى `<base URL>/management`.

| الطريقة | المسار | الغرض |
|---|---|---|
| POST | `/management/auth/v1/token` | تسجيل الدخول (password grant) أو تجديد الرمز |
| GET | `/management/auth/v1/user` | المشغّل الحالي |
| POST | `/management/auth/v1/logout` | تسجيل الخروج |
| GET | `/management/auth/v1/settings` | إعدادات Auth |

أي مسار آخر تحت `/management/auth/...` يعيد `404`، والطريقة الخاطئة تعيد `405`.

## واجهة الإدارة

كل المسارات تحتاج `Authorization: Bearer <management access token>`. هوية الفاعل تؤخذ من هذا الرمز، ولا تؤخذ أبدًا من جسم الطلب. المعرّفات من نوع UUID. رموز الأخطاء: `400` مدخل غير صالح، و`401` لا رمز صالح، و`403` غير مسموح (وتُعاد أيضًا لمعرّف مجهول)، و`404` مسار مجهول، و`405` طريقة خاطئة، و`409` تعارض (البيئة غير جاهزة، أو الاسم مستخدم في العميل أو المشروع نفسه، أو بلغ عدد البيئات حده، أو الطلب لا يقبل الإعادة، أو ما زالت فيه مشاريع أو بيئات، أو ما زال التجهيز أو إحدى الخدمات نشطًا)، ولا يتغير شيء، و`503` فحص الهوية غير متاح، و`500` خطأ داخلي منقّى من التفاصيل.

| الطريقة | المسار | من يستطيع | النتيجة |
|---|---|---|---|
| GET | `/management/v1/organizations` | أي مشغّل | عملاء المستدعي (المنظمات) بالمعرّف والاسم والدور، مع `operator: true` إذا كان يحق له إنشاء عملاء |
| POST | `/management/v1/organizations` | مالك أو مدير العميل الذي أُنشئ عند الإعداد الأولي | الجسم `{"name": "..."}`. يعيد `201` مع `{id}`، ويصبح المستدعي مالكه |
| PATCH | `/management/v1/organizations/{id}` | مالك | الجسم `{"name": "..."}`. يغيّر اسم العميل |
| DELETE | `/management/v1/organizations/{id}` | مالك | يحذف عميلًا فارغًا مع عضوياته ودعواته المعلقة. يُرجع `409` ما دامت فيه مشاريع، ودائمًا للعميل الذي أُنشئ عند التهيئة |
| GET | `/management/v1/organizations/{id}/members` | مالك، مدير | أعضاء ذلك العميل مع أدوارهم |
| PUT | `/management/v1/organizations/{id}/members/{member}` | مالك | الجسم `{"role": "owner"|"admin"|"viewer"}` لعضو موجود. يُرجع `404` لمن ليس عضوًا (إضافة الأشخاص تنتظر الدعوات)، و`409` إذا بقيت المنظمة بلا مالك |
| DELETE | `/management/v1/organizations/{id}/members/{member}` | مالك | يزيل العضو، وينتهي وصوله إلى الإدارة وStudio من الطلب التالي. يُرجع `409` للمالك الأخير |
| GET | `/management/v1/organizations/{id}/invitations` | مالك، مدير | الدعوات المعلّقة: البريد والدور ومن دعا وموعد الانتهاء، دون الرمز أبدًا |
| POST | `/management/v1/organizations/{id}/invitations` | مالك، مدير | الجسم `{"email": "...", "role": "..."}`، ولا يدعو المدير مالكًا. يُرجع `201` مع `{id, token, expires_at}`؛ يُعرض الرمز مرة واحدة ويصلح 7 أيام |
| DELETE | `/management/v1/organizations/{id}/invitations/{invitation}` | مالك، مدير | يلغي دعوة معلّقة |
| POST | `/management/invitations/redeem` | كل من يحمل الرمز | الجسم `{"token": "...", "password": "..."}` ينشئ الحساب (كلمة مرور من 12 حرفًا أو أكثر) وينضم؛ ومع جلسة للبريد المدعو يكفي `{"token": "..."}`. يُرجع `400 This invitation is not valid` لأي رمز مجهول أو مستعمل أو ملغى أو منتهٍ، و`409` إن وُجد حساب وعليه تسجيل الدخول أولًا، و`429` بعد 20 محاولة فاشلة في دقيقة |
| GET | `/management/v1/organizations/{id}/projects` | عضو | مشاريع ذلك العميل |
| POST | `/management/v1/organizations/{id}/projects` | مالك، مدير | الجسم `{"name": "..."}`. يعيد `201` مع `{id, state: "metadata_only"}` |
| PATCH | `/management/v1/projects/{id}` | مالك، مدير | الجسم `{"name": "..."}`. يُرجع `409` إن كان الاسم مستخدمًا في ذلك العميل |
| DELETE | `/management/v1/projects/{id}` | مالك | يحذف مشروعًا لا بيئة فيه، ويُرجع `409` في غير ذلك |
| POST | `/management/v1/projects/{id}/move` | مالك في العميلين | الجسم `{"organization": "<id>"}`. ينقل المشروع مع بقاء معرّفاته ومعرّفات التشغيل. يُلغى التجهيز المنتظر (أعده في العميل الجديد)، وتُلغى كل مفاتيح API لبيئاته. يُرجع `200` مع `{organization, cancelled, revoked}`، و`409` إن تعارض الاسم في العميل الهدف أو كان التجهيز جاريًا |
| GET | `/management/v1/projects/{id}/environments` | عضو | بيئات ذلك المشروع |
| POST | `/management/v1/projects/{id}/environments` | مالك، مدير | الجسم `{"name": "..."}`. يعيد `202` مع `{id, state: "queued"}`، ثم يجهّزها العامل |
| PATCH | `/management/v1/environments/{id}` | مالك، مدير | الجسم `{"name": "..."}`. يُرجع `409` إن كان الاسم مستخدمًا في ذلك المشروع |
| DELETE | `/management/v1/environments/{id}` | مالك | يخرج البيئة من الإدارة ويلغي مفاتيحها، ومن بعدها تجيب البوابة `401` لمعرّف تشغيلها. تبقى قاعدة بياناتها وحاوياتها وملفاتها على الخادم. يُرجع `409` ما دام التجهيز منتظرًا أو جاريًا، أو ما دام Studio أو Realtime أو Edge Functions أو الوصول المباشر إلى قاعدة البيانات أو تغيير في تسجيل الدخول أو تدوير لمفتاح التوقيع أو توجيه موقوف أو منقول، مشغّلًا أو قيد التنفيذ |
| POST | `/management/v1/relink` | مشغّل التثبيت | الجسم `{"runtime": "e_...", "ownership": {...}}`، والحقل `ownership` كما يسجله بيان النسخة الاحتياطية. يعيد إنشاء ذلك العميل والمشروع والبيئة هنا بالمعرّفات نفسها ومعرّف التشغيل نفسه، ويضع التجهيز في الانتظار؛ يستدعيه `sbarbase relink` ([النسخ الاحتياطي والاستعادة](../guides/backup-and-restore.ar.md)). يُرجع `409` لأي تعارض: عميل بهذا الاسم لكن بمعرّف آخر، أو معرّف يخص شيئًا آخر هنا، أو معرّف تشغيل مستعمل أو محذوف هنا |
| GET | `/management/v1/environments/{id}/provision` | عضو | حالة التجهيز `state` ورقم المحاولة `attempt`، والسبب `failure` إن وُجد |
| POST | `/management/v1/environments/{id}/retry` | مالك، مدير | بلا جسم. يعيد `202 {state: "queued"}` لبيئة فشلت أو أُلغيت، و`409` في غير ذلك أو عند بلوغ حد البيئات |
| GET | `/management/v1/environments/{id}/connection` | عضو، حين تجهز البيئة | `{environment, apiPath: "/<runtime>", services}` |
| GET | `/management/v1/environments/{id}/keys` | مالك، مدير | البيانات الوصفية للمفاتيح فقط، ولا يُعاد المفتاح نفسه أبدًا |
| POST | `/management/v1/environments/{id}/keys` | مالك، مدير | بلا جسم. يعيد `201` مع مفتاح عام جديد يظهر مرة واحدة |
| DELETE | `/management/v1/environments/{id}/keys/{keyId}` | مالك، مدير | يعيد `200 {revoked: true}`، أو `404` إذا لم يكن مفتاحًا فعّالًا لهذه البيئة |
| GET | `/management/v1/environments/{id}/metrics` | عضو، عندما تكون جاهزة | آخر ساعة عند البوابة: `window` (`requests` و`clientErrors` و`serverErrors`، و`p50` و`p95` بالميلي ثانية، وعدد الطلبات لكل خدمة في `services`)، و`perMinute` (60 صفًا)، و`since`، و`services` (استهلاك الذاكرة والمعالج لـ Auth وREST وRealtime وEdge Functions). في الذاكرة، وتفرغ بعد إعادة التشغيل ([السجلات والمقاييس](../guides/logs-and-metrics.ar.md)) |
| GET | `/management/v1/environments/{id}/logs?source=requests\|auth\|rest\|storage\|realtime\|functions&lines=1-1000&errors=1` | مالك، مدير، عندما تكون جاهزة | `requests`: آخر طلبات البوابة، الأحدث أولًا، بلا نصوص الاستعلام. المصادر الأخرى: `lines`، آخر أسطر الخدمة، الأقدم أولًا، مع استبدال المفاتيح والرموز وكلمات المرور بـ `[redacted]`؛ وأسطر Storage فقط إن ذكرت هذه البيئة. `503` إذا تعذّر الوصول إلى Docker |
| GET | `/management/v1/environments/{id}/functions` | مالك، مدير، عندما تكون جاهزة | حالة Edge Functions في `state` و`desired` (كما في Realtime)، والدوال المنشورة في `functions` مع `verify_jwt` و`updated_at` و`size` و`path`، وأسماء الأسرار في `secrets` (لا قيمها أبدًا) |
| PUT | `/management/v1/environments/{id}/functions` | مالك، مدير، عندما تكون جاهزة | `{"enabled": true\|false}`. يأخذ `202`، ويشغّل المشرف بيئة تشغيل البيئة أو يوقفها |
| PUT | `/management/v1/environments/{id}/functions/{name}` | مالك، مدير، عندما تكون جاهزة | النشر: `{"files": {"index.ts": "..."}, "shared"?: {...}, "verify_jwt"?: bool}`، ملفات نصية بمسارات نسبية، حتى 500 ملف و10 MiB. يأخذ `201`، وأول نشر يشغّل Edge Functions |
| DELETE | `/management/v1/environments/{id}/functions/{name}` | مالك، مدير، عندما تكون جاهزة | يحذف الدالة، و`404` إن لم تكن موجودة |
| PUT | `/management/v1/environments/{id}/function-secrets` | مالك، مدير، عندما تكون جاهزة | `{"secrets": {"NAME": "value" \| null}}`، و`null` يحذف. الأسماء من `A-Z` والأرقام و`_`، ولا تبدأ بـ `SUPABASE_` أو `SB_`. يردّ بالأسماء فقط |
| GET | `/management/v1/environments/{id}/database` | مالك، مدير، عندما تكون جاهزة | حالة الوصول المباشر إلى قاعدة البيانات في `state` و`desired`، وبيانات الاتصال في `connection` (المضيف والمنفذ والمستخدم وقاعدة البيانات)، و`url` بمكان محجوز لكلمة المرور |
| PUT | `/management/v1/environments/{id}/database` | مالك، مدير، عندما تكون جاهزة | `{"enabled": true\|false}`. يأخذ `202`، والتشغيل يردّ مرة واحدة بكلمة مرور جديدة في `password` و`url` كامل |
| POST | `/management/v1/environments/{id}/database/password` | مالك، مدير، عندما تكون جاهزة | كلمة مرور جديدة تُعرض مرة واحدة، وتتوقف القديمة. يأخذ `409` إن كان الوصول مطفأً |
| GET | `/management/v1/environments/{id}/signing-key` | مالك، مدير، عندما تكون جاهزة | حالة مفتاح توقيع JWT في `state` (`never` أو `pending` أو `done` أو `failed`)، و`failure` و`rotatedAt`؛ ولا يردّ بالمفتاح أبدًا |
| POST | `/management/v1/environments/{id}/signing-key/rotate` | مالك، مدير، عندما تكون جاهزة | مفتاح توقيع جديد ([مفتاح التوقيع](../guides/signing-keys.ar.md)). يأخذ `202`، و`409` إن كان تغيير سابق قيد التنفيذ |
| GET | `/management/v1/environments/{id}/mail` | عضو | حالة البريد غير السرية للبيئة، بلا أي حقل لبيانات الاعتماد |
| GET | `/management/v1/notifications` | مالك أو مدير أي عميل | عدد التنبيهات غير المسلّمة وأحدث أحداث المشغّل لعملاء المستدعي وحدهم. الأحداث التي لا تخص عميلًا (بدء التثبيت، إعادة تشغيل العامل) تذهب إلى مالكي ومديري العميل الذي أُنشئ عند الإعداد الأولي |
| GET | `/management/v1/organizations/{id}/audit` | مالك، مدير | ما حدث في هذا العميل، الأحدث أولًا (حتى 100): الوقت، ومن فعله، والفعل، واسم ما جرى عليه، وتفصيل قصير. المشروع المنقول من عميل آخر لا يظهر منه إلا ما حدث بعد وصوله |
| GET | `/management/v1/environments/{id}/share` | عضو | حصة البيئة المضمونة في البوابة، والقيمة الافتراضية، والسقف؛ ويحصل مشغّلو التثبيت أيضًا على `total` و`allocated` |
| PUT | `/management/v1/environments/{id}/share` | مشغّل التثبيت | الجسم `{"share": n}`، من 1 إلى 24. يُرجع `409` إذا تجاوزت حصص كل البيئات الجاهزة 32. يُطبَّق من الطلب التالي |

أجسام الطلبات لا تقبل إلا الحقول المذكورة لكل مسار، بحجم أقصاه 4 KiB، وتُقرأ خلال خمس ثوانٍ. مسارات المفاتيح والاتصال وكل طلبات `DELETE` ترفض أي جسم. الردود غير قابلة للتخزين المؤقت. نقل المشروع يلغي مفاتيح API، لكنه لا يدوّر مفتاح توقيع JWT لبيئاته ولا كلمة مرور الوصول المباشر إلى قاعدة البيانات؛ دوّرهما ([مفتاح التوقيع](../guides/signing-keys.ar.md)) إذا كان على أشخاص العميل القديم أن يفقدوا كل وصول. وحذف البيئة لا يحرر مكانها في حد البيئات عند العامل، لأن طبقة تشغيلها تبقى.

## بوابة التطبيقات

```
{METHOD} /{runtime}/auth/v1/{path}
{METHOD} /{runtime}/rest/v1/{path}
{METHOD} /{runtime}/storage/v1/{path}
```

القيمة `{runtime}` هي معرّف التشغيل للبيئة، ويعيده المسار السابق في الحقل `apiPath`. وجّه `supabase-js` إلى `<base URL>/{runtime}` مع مفتاح عام.

- **الطرق:** GET وHEAD وPOST وPUT وPATCH وDELETE، وOPTIONS لطلب التحقق المسبق الذي يرسله المتصفح.
- **المتصفحات:** كما في Supabase، تستطيع صفحة على أي نطاق استدعاء الـ API. كل رد، ومنه الرفض، يحمل `Access-Control-Allow-Origin: *` ويكشف `Content-Range`، ويأخذ طلب التحقق المسبق الرد `204` دون مفتاح. لا تُستخدم ملفات تعريف الارتباط ولا بيانات اعتماد، فيبقى مفتاح API ورمز المستخدم وحدهما ما يحدد ما يستطيع الطلب فعله.
- **مفتاح API:** يجب أن تحمل ترويسة `apikey` مفتاحًا عامًا فعّالًا لتلك البيئة. الطلبات الوحيدة المقبولة بلا مفتاح هي GET أو HEAD على `object/public/...` في Storage، وعلى `object/sign/...` مع معامل استعلام `token` واحد بالضبط. تتحقق Storage نفسها من كون الـbucket عامًا أو خاصًا، ومن صحة التوقيع. وتُقبل بلا مفتاح أيضًا خطوات Auth التي يصل إليها المتصفح عبر رابط أو إعادة توجيه: GET على `verify`، وGET على `authorize`، وGET أو POST على `callback`، ويتحقق Auth من كل منها بنفسه.
- **التفويض:** يُمرَّر رمز المستخدم `Bearer` كما هو. إذا غاب (أو ساوى مفتاح API) يُستخدم الرمز المجهول (anonymous) الخاص بالبيئة.
- **مستأجر Storage:** تختاره البوابة من سجل التوجيه وترسله في ترويسة موثوقة، ولا يستطيع العميل اختياره.
- **الجسم:** حده الأقصى 1 MiB، ويُقرأ خلال عشر ثوانٍ. أما رفع ملف إلى Storage (POST أو PUT) فيُمرَّر كما يصل، حتى حد الرفع (`SBARBASE_UPLOAD_LIMIT_MB`، و50 MiB افتراضيًا)، ويفشل إذا توقف ثلاثين ثانية.

| الرمز | المعنى |
|---|---|
| `401` | مفتاح API مفقود أو غير صالح، أو ترويسة `Authorization` مشوّهة، أو بيئة محذوفة |
| `404` | بيئة أو مسار مجهول، أو خدمة غير مُعدّة |
| `408` | ألغى العميل الطلب |
| `413` | الجسم أكبر من 1 MiB، أو الملف المرفوع أكبر من حد الرفع |
| `429` | بلغت هذه البيئة حصتها ولا تستطيع الاستعارة الآن، أو بلغت سقفها؛ أعد المحاولة لاحقًا |
| `503` | البيئة في الصيانة، أو بلغ الخادم حده الكلي للطلبات، أو التوجيه غير متاح. يُرسل `retry-after: 1` حين تفيد إعادة المحاولة |
| `504` | تجاوزت الخدمة الأصلية مهلتها |

حدود القبول تخص كل عملية بوابة وحدها، ولا طابور لها.

## Realtime

عندما يكون Realtime مشغّلًا لبيئة ([Realtime](../guides/realtime.ar.md)):

| المسار | المعنى |
|---|---|
| `GET /{runtime}/realtime/v1/websocket?apikey=<publishable key>&vsn=1.0.0` مع `Upgrade: websocket` | اتصال Realtime الذي يفتحه supabase-js. يُفحص المفتاح أولًا، ثم يستلم Realtime الرمز المجهول الخاص بالبيئة بدلًا منه |
| `POST /{runtime}/realtime/v1/api/broadcast` | البث من خادم، مع الترويسة `apikey` |

إذا كان Realtime مطفأً يأخذ الاتصال الرد `404`. المفتاح الخاطئ أو الملغى يأخذ `401`. وكل مسار آخر في Realtime يأخذ `404`.

## Edge Functions

عندما تكون لبيئة دوال منشورة ([Edge Functions](../guides/edge-functions.ar.md)):

| المسار | المعنى |
|---|---|
| `ANY /{runtime}/functions/v1/{name}[/...]` | يشغّل الدالة. مع `apikey` يُفحص المفتاح كما في كل خدمة؛ ودونه لا تعمل إلا دالة منشورة مع إطفاء `verify_jwt`، وتصلها ترويسات المستدعي كما هي |

الاسم حروف وأرقام و`-` و`_`، حتى 64، ويبدأ بحرف أو رقم؛ وأي اسم آخر، أو دالة مجهولة، أو Edge Functions مطفأة، يأخذ `404`. الدالة التي تفحص JWT تردّ بـ `401` على رمز مفقود أو غير صالح. المهلة 150 ثانية، وتمر الأجسام كما تصل حتى حد الرفع.
