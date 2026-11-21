[English](2026-11-21-telegram-login.md)

# تسجيل الدخول عبر Telegram لمشاريع المستأجرين

الحالة: بُحث ونُفّذ في المصدر على الفرع `feat/telegram-stars` بتاريخ 2026-11-21. الوسم VERIFIED يعني قراءة في مصدر GoTrue عند الوسم v2.197.0 أو تشغيلًا فعليًا، والوسم INFERRED يعني استنتاجًا لم يُشغَّل. لم يُنشر شيء، ويحتاج الدخول الحقيقي عبر Telegram إلى بوت وبياناته، فلم يُجرَّب. هذه الصفحة لا تدّعي جاهزية للإنتاج. التفاصيل الكاملة والروابط في [النسخة الإنجليزية](2026-11-21-telegram-login.md).

## السؤال

يريد المالك أن يفعّل مشروع المستأجر «تسجيل الدخول عبر Telegram» من إعدادات الدخول في وحدة التحكم كما يفعّل Google أو GitHub، ثم يستدعي التطبيق `supabase.auth.signInWithOAuth({provider: 'custom:telegram'})`. Telegram مزوّد OpenID Connect: المُصدِر `https://oauth.telegram.org`، ونقطتا التفويض `/auth` والرمز `/token`، ومفاتيح JWKS، ونوع الاستجابة `code`، وPKCE بالطريقة `S256`، ومصادقة العميل `client_secret_basic`، وتوقيع `RS256` افتراضيًا، والنطاقان `openid` و`profile` (و`phone` اختياريًا)، بلا بريد وبلا نقطة userinfo.

## ما وُجد في مصدر GoTrue v2.197.0 (VERIFIED)

1. المزوّدات المخصّصة لا تُضبط بمتغيّرات بيئة. هي صفوف في الجدول `auth.custom_oauth_providers` تُدار عبر واجهة الإدارة. المتغيّرات الوحيدة `GOTRUE_CUSTOM_OAUTH_ENABLED` (افتراضيًا true) و`GOTRUE_CUSTOM_OAUTH_MAX_PROVIDERS` و`GOTRUE_CUSTOM_OAUTH_EXTERNAL_URL`. فلا تلزم متغيّرات جديدة، والمتغيّر `GOTRUE_EXTERNAL_TELEGRAM_*` يُتجاهل.
2. النقاط: `GET` و`POST /admin/custom-providers`، و`GET` و`PUT` و`DELETE /admin/custom-providers/{identifier}`، بتوكن مدير (دور `service_role`)، وهو التوكن نفسه الذي يستعمله صباربيز مع `/admin/users`.
3. الحمولة: `provider_type` و`identifier` (يبدأ بـ`custom:`) و`name` و`client_id` و`client_secret` و`scopes` و`pkce_enabled` و`email_optional` و`enabled` و`custom_claims_allowlist`، وللـOIDC: `issuer` و`discovery_url` و`skip_nonce_check`.
4. الإنشاء يجلب وثيقة الاكتشاف من حاوية Auth ويتحقق من المُصدِر، لذلك تحتاج Auth إلى الشبكة الخارجية عند الإنشاء وعند كل دخول.
5. الاسم `custom:<id>` في `/authorize` يوجّه إلى `loadCustomProvider`، ومع `pkce_enabled` يولّد GoTrue المُتحقّق ويضيف تحدّي S256. عنوان الرجوع هو `API_EXTERNAL_URL/callback`.
6. يمكن تجاوز userinfo، وهذا ما يحدث: يُقرأ `id_token` ويُتحقق منه ويُطابَق `aud` مع `client_id`.
7. المطالبات المكتوبة في `identity_data` هي الحقول المعرّفة فقط (`sub` و`name` و`preferred_username` و`picture` وغيرها). المطالبة `id` (المعرّف الرقمي في Telegram) و`phone_number` تسقطان ما لم تُذكرا في `custom_claims_allowlist`. لذلك يضبط صباربيز `["id"]`، فيوجد المعرّف في `identity_data.custom_claims.id`. أما `sub` فمعرّف مختلف.
8. بلا بريد: يلزم `email_optional: true`.
9. لا يُرسل nonce في تدفق إعادة التوجيه، فيبقى `skip_nonce_check` معطّلًا.
10. أبقِ خوارزمية BotFather على RS256: مكتبة go-oidc لا تدعم ES256K، وTelegram يحجب مطالبات `profile` عن EdDSA وES256K.

## كيف يطبّق صباربيز إعدادات Auth (VERIFIED)

يحفظ الملف الخاص `.secrets/upstream/<runtime>-auth.json`، ثم ينفّذ المشرف `reconcile_auth` فيعيد إنشاء Auth بمتغيّرات `auth_settings.configuration`، ويصله بالشبكة الخارجية عند الحاجة (`egress_wanted` صحيحة أصلًا مع أي مزوّد مفعّل)، وينتظر `/health`، ثم يكتب النسخة المطبّقة. الفشل يعيد Auth السابقة. التحقق مكتوب مرتين ويجب أن يتطابق: `lab/auth_settings.py` و`src/control/sign-in.ts`.

## التصميم

- `telegram` اسم جديد في قائمة المزوّدين: `client_id` هو معرّف البوت أرقامًا فقط (حتى 20)، بلا عنوان خادم، وبلا `skip_nonce_check`، و`email_optional` يُخزَّن true دائمًا وتخفي الواجهة مفتاحه.
- `configuration()` لا تُصدر له أي متغيّر.
- الدالة `custom_providers(settings)` تُرجع حمولة `custom:telegram` (`oidc`، والمُصدِر ثابت، والنطاقان `openid` و`profile`، وPKCE مفعّل، و`custom_claims_allowlist` هي `["id"]`) أو `None` حين لا يرد `telegram` في الإعدادات.
- `apply_custom_providers` في `lab/durable_runtime.py` تُستدعى من `reconcile_auth` بعد انتظار الصحة وقبل كتابة النسخة المطبّقة، على عنوان الحاوية الداخلي بتوكن الخدمة. تسرد كل المزوّدات المخصّصة بلا مرشّح نوع، ولا تمسّ إلا `custom:telegram`: تنشئه إن غاب، وتحدّثه (بلا ذكر المُصدِر) إن كان مزوّد OIDC الثابت، وتحذفه ثم تنشئه إن كان بهذا الاسم صف من نوع أو مُصدِر آخر، وتحذفه إن حُفظ الإدخال معطّلًا أو كان في آخر إعدادات مطبّقة ولم يعد مذكورًا. لا تقرأ أسرار أي صف آخر ولا تغيّره. الفشل يرفع خطأ فيعمل التراجع القائم، وتعيد `restore_custom_providers` الصف أولًا: سرّ الصف الموجود لا يُقرأ، فيُؤخذ الصف السابق من آخر إعدادات مطبّقة، ويُحذف الصف الذي لم يكن موجودًا ولا في تلك الإعدادات. والرسالة فيها رمز HTTP ورمز خطأ Auth ولا تحمل نص الرد أبدًا.
- مدخل السجل يحمل `"mechanism": "custom_oidc"`، فتسمّي القراءة وقائمة المفاتيح المتغيّرة مفاتيحه `custom_provider:telegram.client_id` وما شابهها، ولا تسمّيها متغيّرات لأنه لا متغيّر له.
- تبقى `OWNED_PREFIXES` كما هي: المتغيّرات `GOTRUE_CUSTOM_OAUTH_*` تبقى للمشغّل نفسه.

## المخاطر والحدود

- **السرّ يُخزَّن نصًّا صريحًا في قاعدة بيانات البيئة** (`auth.custom_oauth_providers.client_secret`) بخلاف المزوّدين الآخرين. يستطيع دور المطوّر ودور الخدمة قراءته بـSQL، وتحتويه النسخة الاحتياطية. تشفير قاعدة بيانات GoTrue يغيّر سلوكًا عامًا في Auth ولم يُفعَّل هنا.
- استعادة نسخة سابقة لتفعيل Telegram لا تحوي الصف حتى الحفظ التالي. والإصدار الأقدم من هذا التغيير يرفض ملفًا يذكر `telegram`، وهذا فشل آمن.
- يُتراجع عن الصف مع الحاوية: يقرأ GoTrue صفوف `custom:*` من قاعدة البيانات عند كل طلب، فلو بقي الصف المتغيّر لخدمته Auth العائدة. بعد أي فشل يلي كتابة الصف يُعاد الصف من آخر إعدادات مطبّقة. هذه الاستعادة بأفضل جهد: إن رفضها Auth يُكتب الرفض في السجل ويبقى الفشل الأصلي هو المبلَّغ عنه. والصف الذي كان موجودًا بلا إعدادات مطبّقة خلفه (صُنع يدويًا ثم استُبدل) لا يمكن استعادته لأن سرّه لم يكن مقروءًا.
- الاسم `custom:telegram` محجوز لهذا الإعداد متى حُفظ إدخال `telegram`: الصف المصنوع يدويًا بهذا الاسم يُستبدل عند التفعيل ويُحذف عند التعطيل، ويُحذف أيضًا إن أُزيل الإدخال لاحقًا. أما صف بهذا الاسم لم يغطّه إدخال Telegram محفوظ قط فيُترك.
- الخروج إلى الشبكة مفتوح لكل بيئة فيها أي مزوّد مفعّل، وقائمة السماح الافتراضية المغلقة ما زالت بندًا مفتوحًا في [خطة مساحة المصادقة](2026-10-30-auth-providers-workspace.md) (البند 8). لم تُضعَف أي قيمة افتراضية.
- نطاق `phone` غير مطلوب. وإن أُريد، أضف `phone_number` إلى القائمة معه.
- غير مُتحقَّق منه: الدخول الحقيقي عبر Telegram وتبادل الرمز والتحقق من توقيع `id_token`. يغطي الفحص الحي كل شيء حتى إعادة التوجيه.

## الدوال والمهام (بحث للقراءة فقط، VERIFIED)

- **`verify_jwt = false` لدالة webhook**: بثلاث طرق: `verify_jwt = false` تحت `[functions.<name>]` في `supabase/config.toml` (يقرؤه `lab/functions-deploy.ts`)، أو الحقل `verify_jwt` في `PUT /management/v1/environments/{id}/functions/{name}`، أو `PATCH` بالجسم `{"verify_jwt": false}` تمامًا، أو مفتاح **Require a valid JWT** في إعدادات الدالة. عندها تعمل الدالة بلا مفتاح، فيجب أن تتحقق بنفسها من الترويسة `X-Telegram-Bot-Api-Secret-Token`. ومع Edge Functions عند الطلب قد يردّ أول استدعاء بعد خمول بـ`503` مع `retry-after`، وTelegram يعيد المحاولة.
- **pg_cron وpg_net**: الامتدادان الحقيقيان مرفوضان على الخادم المشترك. البيئة التي تفعّل **Scheduled jobs** تحصل على دوال `cron.*` و`net.http_*` بالتواقيع نفسها (pg_cron 1.6.4 وpg_net 0.20.4)، والطلبات الصادرة إلى http وhttps على المنفذين 80 و443 فقط وليس إلى عناوين خاصة أو محجوزة، فاستدعاء `api.telegram.org` مسموح بحسب القاعدة (INFERRED). تحتاج دور المطوّر. غير جاهزة للإنتاج.
