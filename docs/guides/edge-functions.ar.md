[English](edge-functions.md)

# Edge Functions

تشغّل Edge Functions كود Deno الخاص بك بجانب البيئة، وتُستدعى عبر `supabase.functions.invoke()` على `<address>/functions/v1/<name>`، كما في Supabase. لكل بيئة بيئة تشغيل خاصة بها، هي edge-runtime الأصلي، وتبدأ مع أول نشر.

## النشر من مشروع Supabase

إن كانت لديك دوال في مشروع Supabase، فانشر مجلد `supabase/functions` كله بأمر واحد من نسخة من مستودع صباربيز:

```bash
SBARBASE_EMAIL=you@example.com bun lab/functions-deploy.ts https://console.example.com <environment id> ../my-app/supabase/functions
```

- يطلب كلمة مرور المشغّل (أو يقرؤها من `SBARBASE_PASSWORD`) ولا يطبعها أبدًا.
- كل مجلد فيه `index.ts` يصبح دالة؛ واذكر أسماء بعد المجلد لتنشر بعضها فقط.
- يُنشر `_shared` بجانب كل دالة، فيعمل `import { corsHeaders } from '../_shared/cors.ts'` دون تغيير.
- يُحترم `verify_jwt = false` تحت `[functions.<name>]` في `supabase/config.toml`، كما في Supabase CLI.
- تعمل استيرادات `npm:` و`jsr:` والروابط: بيئة التشغيل تصل إلى الإنترنت.

معرّف البيئة موجود في صفحتها في لوحة الإدارة. إعادة النشر تُطبَّق فورًا دون إعادة تشغيل، والطلب الجاري ينتهي على الإصدار الذي بدأ به.

## كتابة دالة في اللوحة

في صفحة البيئة، تحت **Edge Functions**، يأخذ زر **Write a function** اسمًا وملف `index.ts` وينشرهما. يناسب الدوال الصغيرة، ويناسب مجلدُ المشروع ما عداها.

## استدعاء دالة

```js
const { data, error } = await supabase.functions.invoke('hello-world', { body: { name: 'Sbarbase' } })
```

تحصل كل دالة على هذه المتغيرات، كما في Supabase:

| المتغير | ما هو |
|---|---|
| `SUPABASE_URL` | واجهة هذه البيئة، لاستدعاء `createClient()` داخل الدالة |
| `SUPABASE_ANON_KEY` | يعمل كزائر، ويطبَّق أمان الصفوف |
| `SUPABASE_SERVICE_ROLE_KEY` | يعمل كالخدمة نفسها ويتجاوز أمان الصفوف. أبقِه داخل الدوال |

أضف متغيراتك تحت **Secrets** (مثل `STRIPE_SECRET_KEY`) واقرأها بـ `Deno.env.get('STRIPE_SECRET_KEY')`. لا تظهر القيمة مرة أخرى بعد حفظها.

## من يستطيع استدعاء دالة

- تحتاج الدالة افتراضيًا رمز JWT صالحًا: المفتاح العام (يرسله supabase-js) أو رمز مستخدم مسجّل الدخول. المفتاح الخاطئ يُرفض قبل أن تعمل الدالة.
- الدالة المنشورة مع إطفاء **Require a valid JWT** (`verify_jwt = false`) تقبل أيضًا الاستدعاء دون أي مفتاح، لخطاف ويب من مزوّد دفع أو خدمة أخرى. عندها تتحقق الدالة من الاستدعاء بنفسها، مثلًا بترويسة التوقيع من المزوّد، التي تصلها كما هي.

## كيف تعمل

- بيئة تشغيل واحدة لكل بيئة، حدها 384 MiB ونصف معالج؛ وتعمل كل دالة في عامل خاص بها حده 150 MiB، ويتوقف بعد 150 ثانية.
- لا ترى الدالة إلا كود بيئتها وأسرارها وخدماتها. وتصل بيئة التشغيل إلى Auth وREST وStorage الخاصة بالبيئة مباشرة عبر الشبكة الداخلية.
- السجلات تحت **Logs**، المصدر **Edge Functions**، بجانب الخدمات الأخرى.
- زر **Turn off Edge Functions** يوقف بيئة التشغيل ويُبقي الكود؛ والتشغيل أو النشر من جديد يعيدها.

ينشر CI مجلد دوال بالأمر أعلاه على جهاز نظيف مع كل تغيير، ويستدعي الدوال عبر البوابة باستخدام supabase-js، ومنها دالة تستخدم supabase-js من npm بصلاحية الخدمة ([الدليل](../evidence/docker-functions-checks.json)).

## الحدود

- الكود ملفات نصية، حتى 500 ملف و10 MiB في كل نشر. يُحفظ الكود المنشور في `.lab/upstream/functions/` وليس في النسخ الاحتياطية للبيئة بعد: احتفظ بمجلد مشروعك.
- لا يُعطى `SUPABASE_DB_URL`؛ صِل إلى قاعدة البيانات عبر `SUPABASE_URL`.
- الأمر `supabase functions deploy` في Supabase CLI يخاطب واجهة منصة Supabase ولا يعمل هنا؛ استخدم الأمر أعلاه.
- جسم الطلب أو الرد يمر عبر البوابة حتى حد الرفع (`SBARBASE_UPLOAD_LIMIT_MB`).
