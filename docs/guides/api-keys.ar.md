[English](api-keys.md)

# مفاتيح API

لكل بيئة نوعان من مفاتيح API، كما في Supabase. يُرسل كلاهما في الترويسة `apikey`، وتتولى supabase-js ذلك عنك.

| المفتاح | يبدأ بـ | يعمل بصفة | مكانه |
| --- | --- | --- | --- |
| العام (publishable) | `sb_publishable_` | `anon`، أو المستخدم المسجّل دخوله | صفحة الويب وتطبيق الجوال وأي مكان. أمان مستوى الصفوف (RLS) يقرر ما يراه كل زائر |
| السري (secret) | `sb_secret_` | `service_role` | على الخادم فقط: الواجهة الخلفية والسكربتات والمهام المجدولة وEdge Functions ووكلاء الذكاء الاصطناعي. يتجاوز أمان مستوى الصفوف ويستطيع إدارة المستخدمين |

## إنشاء مفتاح

1. افتح صفحة البيئة في لوحة الإدارة واختر **API keys**.
2. اضغط **Create key** لمفتاح عام، أو **Create secret key** وأعطه اسمًا مثل `backend` أو `agent_claude`.
3. انسخ المفتاح.
   - **المفتاح السري لا يظهر إلا مرة واحدة.** يحتفظ صباربيز ببصمة SHA-256 له فقط، فلا يستطيع أحد عرضه مرة أخرى، ولا مدير الخادم نفسه. إن أضعته فأنشئ مفتاحًا آخر وألغِ القديم.
   - **المفتاح العام يمكن نسخه لاحقًا** من الصفحة نفسها، كما في Supabase، لأنه عام بطبيعته. ينطبق هذا على المفاتيح المنشأة منذ 0.5.0، أما المفتاح الأقدم فلا يُحفظ منه إلا بصمته.

لا ينشئ المفاتيح ولا يعرض قائمتها ولا يلغيها إلا مالكو المنظمة ومشرفوها. ويُسجَّل كل إنشاء وإلغاء في سجل تدقيق المنظمة، دون المفتاح نفسه.

## استعمال المفتاح السري

```js
import {createClient} from '@supabase/supabase-js';

const admin = createClient('https://console.example.com/<runtime>', process.env.SUPABASE_SECRET_KEY, {
  auth: {persistSession: false, autoRefreshToken: false},
});
await admin.auth.admin.createUser({email: 'someone@example.com', email_confirm: true});
const {data} = await admin.from('orders').select('*'); // every row, whatever the policies say
```

يعمل حيث يعمل مفتاح `service_role` في Supabase: REST، وإدارة المستخدمين في Auth (`/auth/v1/admin/...`)، وStorage، وRealtime، وEdge Functions. تستبدل البوابة المفتاحَ برمز `service_role` قصير العمر، موقّع بمفتاح التوقيع الحالي للبيئة، فلا ترى قاعدة البيانات ولا Auth ولا REST ولا Storage المفتاحَ نفسه. الاستثناء هو Edge Functions: كما في Supabase، تتلقى الدالة ترويسات المتصل، والمفتاح منها.

إن حمل الطلب أيضًا رمز مستخدم مسجّل دخوله في `Authorization`، فالرمز هو الذي يُعتمد ويعمل الطلب بصفة ذلك المستخدم، كما في Supabase. ويُقبل كذلك إرسال المفتاح السري في `apikey` وفي `Authorization: Bearer` معًا.

## المتصفحات مرفوضة

يُرفض المفتاح السري في أي طلب يبدو أنه آتٍ من متصفح: `401` مع الرسالة "Secret API keys cannot be used in a browser". يبحث صباربيز عمّا ترسله كل المتصفحات ولا ترسله الخوادم: معرّف متصفح (user agent) يبدأ بـ `Mozilla/`، أو ترويسة `Origin`، أو ترويسة تبدأ بـ `Sec-Fetch-`. وهكذا يفشل المفتاح السري فورًا إن لُصق في صفحة ويب، بدل أن يتسرّب إلى كل زائر. استعمل هناك مفتاحًا عامًا.

## التبديل والإلغاء

- **الإلغاء** يوقف المفتاح فورًا، في كل طلب وكل اتصال Realtime جديد. أما اتصال Realtime المفتوح به من قبل فيبقى يعمل حتى يُغلق، يومًا واحدًا على الأكثر. ولإغلاق هذه الاتصالات فورًا أعد تشغيل Realtime للبيئة (أوقفه ثم شغّله).
- للتبديل دون انقطاع: أنشئ مفتاحًا سريًا جديدًا، وضعه حيث كان القديم مستعملًا، ثم ألغِ القديم.
- أعطِ كل خادم أو مهمة أو وكيل مفتاحه السري الخاص، لتستطيع إلغاء واحد دون الباقين.
- تغيير [مفتاح التوقيع](signing-keys.ar.md) للبيئة لا يغيّر مفاتيح API، لأنها ليست رموز JWT.

## عبر الواجهة البرمجية

```bash
# a secret key named "backend"; the answer holds the key, once
curl -X POST -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"type":"secret","name":"backend"}' https://console.example.com/management/v1/environments/$ENVIRONMENT/keys
# list (names, kinds and dates, never the keys) and revoke
curl -H "authorization: Bearer $TOKEN" https://console.example.com/management/v1/environments/$ENVIRONMENT/keys
curl -X DELETE -H "authorization: Bearer $TOKEN" https://console.example.com/management/v1/environments/$ENVIRONMENT/keys/$KEY_ID
```

الجسم اختياري. وبدونه يُنشأ مفتاح عام اسمه `default`، كما كان من قبل.

## الفروق عن Supabase

- يظهر المفتاح السري مرة واحدة. يستطيع Supabase أن يكشفه لاحقًا، أما صباربيز فلا، لأنه لا يحتفظ إلا ببصمته.
- لا تُصدَر للتطبيقات مفاتيح JWT القديمة (رموز `anon` و`service_role`).
- لم ينشر Supabase طريقته الدقيقة في كشف المتصفح، أما طريقة صباربيز فموصوفة أعلاه.
- الدالة في Edge Functions التي تُستدعى بمفتاح سري تتلقى رمز `service_role` في `Authorization` والمفتاحَ نفسه في `apikey`، كما في Supabase. فلا تمرّر المفاتيح السرية إلى دوال لم تكتبها بنفسك.
