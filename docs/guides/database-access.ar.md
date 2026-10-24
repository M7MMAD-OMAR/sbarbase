[English](database-access.md)

# الوصول إلى قاعدة البيانات

تستطيع كل بيئة أن تعطيك سلسلة اتصال PostgreSQL للترحيلات والأدوات: `psql` و`pg_dump` وPrisma وDrizzle وأمثالها. تكون مطفأة حتى يشغّلها مالك أو مدير.

## التشغيل

1. افتح صفحة البيئة في لوحة الإدارة.
2. تحت **Database** اضغط **Turn on database access**.
3. انسخ سلسلة الاتصال التي تظهر. لا تظهر كلمة المرور إلا مرة واحدة؛ وزر **New password** يستبدلها، فتتوقف القديمة عن العمل.

تبدو السلسلة هكذا:

```text
postgresql://e_…_developer:<password>@127.0.0.1:6543/e_…
```

## الاتصال من حاسوبك

العنوان على الخادم نفسه. افتح نفق SSH، ثم استخدم السلسلة كما هي:

```bash
ssh -N -L 6543:127.0.0.1:6543 you@your-server
psql "postgresql://e_…_developer:<password>@127.0.0.1:6543/e_…?sslmode=disable"
```

والأمر `deploy/db-tunnel you@your-server` يفعل الشيء نفسه ويعيد الاتصال إن انقطع الرابط (سكربت قصير؛ انسخه إلى حاسوبك إن لم يكن عندك المستودع).

أمثلة:

```bash
psql "postgresql://…@127.0.0.1:6543/e_…?sslmode=disable" -v ON_ERROR_STOP=1 -1 -f supabase/migrations/20261017000000_init.sql   # one migration file
pg_dump "postgresql://…@127.0.0.1:6543/e_…?sslmode=disable" --schema=public > public.sql
DATABASE_URL="postgresql://…@127.0.0.1:6543/e_…?sslmode=disable" npx prisma migrate deploy
supabase db push --db-url "postgresql://…@127.0.0.1:6543/e_…?sslmode=disable"   # every file in supabase/migrations
supabase gen types typescript --db-url "postgresql://…@127.0.0.1:6543/e_…?sslmode=disable" > database.types.ts
```

## الاتصال من خدمة، مثل Cloudflare Hyperdrive

لا يستطيع Worker على Cloudflare فتح نفق SSH، وHyperdrive يشترط TLS وليس له عناوين ثابتة لتُسمح. لذلك يستطيع المالك أو المسؤول فتح **الوصول الخارجي** لبيئة واحدة. هو مطفأ حتى تفتحه، ويحتاج إلى تشغيل الوصول إلى قاعدة البيانات.

1. تحت **Database** اضغط **Allow external connections**. تعرض اللوحة جهة إصدار الشهادات (CA) التي يتحقق بها العميل من الخادم؛ انسخها أو نزّلها. وعلى الخادم يفعل `sbarbase database-external <environment> on` الشيء نفسه من الصدفة ([الأمر](cli.ar.md)).
2. اجعل المنفذ قابلًا للوصول من الخدمة **دون فتح أي منفذ على الإنترنت**. يستمع على عنوان loopback في الخادم، على المنفذ 6545 (`SBARBASE_DATABASE_EXTERNAL_PORT`). مع Cloudflare شغّل موصل Cloudflare Tunnel على الخادم، ثم أنشئ في Workers VPC خدمة TCP تشير إليه:

```bash
# 1. Cloudflare dashboard, Networking, Tunnels: create a tunnel and run the install command it shows on the server
#    (cloudflared as a service). `wrangler tunnel create my-server-db` also creates one and prints its id.
# 2. Point a Workers VPC service at the listener, through that tunnel:
wrangler vpc service create my-server-db --type tcp --tcp-port 6545 --app-protocol postgresql \
  --tunnel-id <tunnel-id> --ipv4 127.0.0.1 --cert-verification-mode disabled
```

   القيمة `disabled` مقصودة: لا يثق Workers VPC إلا بشهادات الجهات العامة وCloudflare Origin CA، والنفق هو المسار المصادَق والمشفّر إلى خادمك. وبلا Cloudflare استخدم أي شبكة خاصة أو نفق يصل إلى المنفذ 6545 (WireGuard أو Tailscale أو وكيل يمرّر TLS). وضبط `SBARBASE_DATABASE_EXTERNAL_BIND` على عنوان عام ينشر المنفذ: افعله خلف جدار ناري لا يسمح إلا بعناوين الخدمة، وأضف اسم المضيف إلى `SBARBASE_DATABASE_TLS_NAMES` ليطابقه `sslmode=verify-full`.
3. أنشئ Hyperdrive بحساب المطوّر لهذه البيئة (كلمة المرور التي حفظتها) وبالتخزين المؤقت مطفأ، فمنطق الدفع يحتاج إلى قراءة حديثة:

```bash
wrangler hyperdrive create my-db --service-id <vpc-service-id> --database e_… --user e_…_developer \
  --password '<password>' --caching-disabled --origin-connection-limit 8
```

   أبقِ الحدّ دون الاتصالات العشرة للحساب، ليجد ترحيل عبر نفق SSH جلسة متاحة.
4. في الـ Worker استخدم سلسلة الاتصال من الربط (binding) مع مشغّل يدعم المعاملات (`pg` من 8.16.3 فما فوق، أو `postgres`). تعمل المعاملة (`sql.begin`) لأن Hyperdrive يعطي الـ Worker جلسة PostgreSQL حقيقية.

أما العملاء الآخرون فيتصلون بالمنفذ مباشرة بـ `sslmode=verify-ca` وشهادة CA من اللوحة (`sslrootcert=ca.pem`). يُرفض الاتصال غير المشفّر بهذا المنفذ، ويُرفض أي دخول غير حساب المطوّر لهذه البيئة. وإغلاق الوصول الخارجي، أو إطفاء الوصول إلى قاعدة البيانات، يرفض الاتصالات الجديدة فورًا ويُنهي جلسات هذه البيئة الخارجية المفتوحة خلال ثانية. وأي نفق أو شبكة تضعها أمام المنفذ يجب أن تكون مصادَقة (نفق Cloudflare خلف Workers VPC، أو WireGuard، أو SSH)، لأن كل ما خلفها يصل إلى المنفذ بعنوان واحد.

## ما يستطيعه حساب المطوّر

يعمل مثل المستخدم `postgres` في مشروع Supabase، داخل قاعدة بيانات هذه البيئة وحدها:

- إنشاء الجداول والعروض والدوال والسياسات في `public` وتعديلها. تُمنح الجداول الجديدة لـ `anon` و`authenticated` و`service_role`، فتصل إليها الواجهة، ويحدد أمان الصفوف ما يراه كل مستدعٍ. تُضبط هذه الصلاحيات الافتراضية مرة واحدة، عند أول تشغيل للوصول، والترحيل الذي يضيّقها يبقى اختياره قائمًا.
- إضافة مشغّلات (triggers) على `auth.users` (مثلًا لإنشاء ملف شخصي عند التسجيل) وسياسات على `storage.objects`.
- قراءة كل الصفوف وكتابتها؛ فهو يتجاوز أمان الصفوف، كما يفعل المستخدم `postgres`.
- حفظ سجل الترحيلات في `supabase_migrations`، وهو مخطط (schema) أُنشئ له، يسجّل فيه [وكلاء الذكاء الاصطناعي](agents.ar.md) ما طبّقوه.
- إنشاء مخططات خاصة به، مثل `app` أو `private`، وتغيير أسمائها. لكنه لا يستطيع استخدام اسم تنشئه المنصة نفسها: `auth` و`storage` و`extensions` و`public` و`realtime` و`_realtime` و`vault` و`pgsodium` و`cron` و`net` و`graphql` و`graphql_public` وأسماء أخرى قليلة (`RESERVED_SCHEMAS` في `lab/durable_runtime.py`)، وأي اسم يبدأ بـ `supabase` أو `sbarbase`. فالمخطط الذي يسبق إليه بأحد هذه الأسماء سيشغّل شيفرته بصلاحيات المنصة، لذلك يرفض PostgreSQL الأمر، ويرفض كذلك تغيير اسم مخطط إلى أحدها.
- حفظ الأسرار في Vault (`vault.create_secret` و`vault.update_secret` و`vault.decrypted_secrets`) كما في Supabase، ويقرؤها `service_role` أيضًا. ولا يستطيع الحساب أن يعطي هذا الوصول لأدوار أخرى، فلا يفتح منحُه لـ `anon` الأسرارَ للواجهة.
- إنشاء الإضافات (extensions) التي يستخدمها التطبيق، مثل `pg_trgm` و`btree_gist` و`vector` و`pgtap` و`pg_graphql`. أما الإضافات التي تصل إلى شبكة الخادم، أو ترى نشاط كل البيئات، أو تحمّل شيفرة، أو تكتب ملفات على الخادم، أو تمسك مفتاح الخادم، فمرفوضة على الخادم المشترك: `dblink` و`postgres_fdw` و`http` و`pg_net` و`wrappers` و`pg_cron` و`pg_tle` و`pgsodium` و`pg_stat_statements` و`pg_stat_monitor` و`pg_walinspect` و`pg_buffercache` و`pg_prewarm` و`pg_repack` و`pgaudit` و`pgroonga` وغيرها قليل (`RESERVED_EXTENSIONS` في `lab/durable_runtime.py`).

- إضافة جداول إلى منشور `supabase_realtime`، وهو موجود دائمًا ويملكه الحساب، كما يملكه المستخدم `postgres` في مشروع Supabase. يشمل المنشور مخطط `public` كله، فجداوله منشورة أصلًا، وإضافة أحدها مقبولة ولا تغيّر شيئًا. ويمكن إضافة جداول المخططات الأخرى واحدًا واحدًا. وإن حذفه الحساب، تعيد المنصة إنشاءه في المرة التالية التي يُشغَّل فيها الوصول.

تُمنح هذه الصلاحيات عند تشغيل الوصول؛ وللبيئة التي كان الوصول فيها مفعّلًا من قبل، أوقفه ثم شغّله مرة واحدة.

تُشفَّر أسرار Vault بمفتاح جذري واحد يخص الخادم لا البيئة: كل البيئات على الخادم تستخدم المفتاح نفسه. وتبقى منفصلة لأن كل بيئة لا تصل إلا إلى قاعدة بياناتها. انتبه لذلك في النسخ الاحتياطية: نسخة من `vault.secrets` لبيئة ما يمكن قراءتها في أي بيئة على الخادم نفسه، وعلى خادم آخر فقط بمفتاح هذا الخادم.

ليس مستخدمًا خارقًا، ولا يستطيع إنشاء أدوار أو قواعد بيانات، ولا الوصول إلى قاعدة بيانات بيئة أخرى. وحده الأقصى 10 اتصالات.

## كيف يبقى آمنًا

- يستمع المنفذ على عنوان loopback في الخادم، على المنفذ 6543 افتراضيًا. يقرأ اسم الدخول في كل اتصال قبل أن يصل أي شيء إلى PostgreSQL، ولا يمرّر إلا حساب مطوّر لبيئة يطلب قاعدة بيانات تلك البيئة نفسها. أي حساب آخر، ومنه المستخدم الخارق، يُرفض هناك.
- ثم يتحقق PostgreSQL من كلمة المرور (SCRAM). وإطفاء الوصول يغلق الحساب وكل الجلسات المفتوحة به.
- يقبل المنفذ 6543 اتصال TLS (فيعمل `sslmode=require`) كما يقبل غير المشفّر؛ ونفق SSH يشفّره لك. يمكن لـ `SBARBASE_DATABASE_BIND` أن يعرضه على عنوان آخر، لكن افعل ذلك على شبكة تثق بها فقط ([الإعدادات](../reference/configuration.ar.md)).
- المنفذ الخارجي (6545) لا يقبل إلا TLS، ولا يقبل إلا بيئات فُتح وصولها الخارجي، وبقواعد الدخول نفسها. يُحسب كل اتصال ضمن حصة لكل عنوان وحصة إجمالية، وعشر كلمات مرور خاطئة لبيئة واحدة خلال دقيقة تغلق دخولها الخارجي دقيقة. يعتمد TLS على جهة إصدار شهادات خاصة ينشئها Sbarbase عند أول تشغيل، وتُحفظ في `.secrets/upstream/database-tls` (ينشئها `openssl` على الخادم، وبدونه يبقى المنفذ الخارجي مطفأ) ([التصميم](../engineering/EXTERNAL-DATABASE.md)). جُرّب على خادم حقيقي مع Cloudflare Hyperdrive، ومنه معاملة من Worker وحالات الرفض ([الدليل](../evidence/external-database-checks-2026-10-24.json)).

يشغّل CI الوصول لبيئة على جهاز نظيف مع كل تغيير، وينفّذ ترحيلًا بأسلوب Supabase عبر المنفذ، ويتحقق من حالات الرفض وتجديد كلمة المرور والإطفاء ([الدليل](../evidence/docker-database-checks.json)). وفحص آخر يدخل بحساب بيئة واحدة ويجرّب كل اسم مخطط محجوز، وتغيير الأسماء، والإضافات المرفوضة، ومنشور Realtime، والحماية نفسها، وVault، وقاعدة بيانات البيئة الأخرى ([الدليل](../evidence/developer-schema-checks-2026-10-19.json)).

## الحدود

- حساب واحد لكل بيئة، حتى 10 اتصالات؛ ولا يوجد مجمّع اتصالات بعد.
- عبر نفق SSH ما زال `sslmode=disable` يعمل، ويعمل `require` أيضًا. أما المنفذ الخارجي فيرفض الاتصال غير المشفّر.
- عند تشغيل الوصول، وعند **New password**، يُعطى الحساب صلاحياته على جداولك من جديد. فإذا ترك تطبيقك معاملة مفتوحة على أحدها (طلب ما زال يعمل، أو جلسة خاملة داخل معاملة)، انتظرت هذه الخطوة حتى 10 ثوانٍ في كل مرحلة، ثم تفشل وتذكر ذلك، فتعيد المحاولة. ولا تنتظرها البيئات الأخرى على الخادم ([التصميم](../engineering/DATABASE-ACCESS.md)).
- يُرفض الترحيل الذي يذكر الدور `postgres`، مثل `alter default privileges for role postgres`: فحساب المطوّر في كل بيئة يقوم بهذا الدور، أما `postgres` نفسه فمشترك بين كل الخادم. اكتبه دون `for role postgres`، فينطبق على الحساب الذي يشغّله.
