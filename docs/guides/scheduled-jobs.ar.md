[English](scheduled-jobs.md)

# المهام المجدولة

تتيح المهام المجدولة للبيئة أن تشغّل أوامر SQL في مواعيد محددة عبر `cron.schedule()`، وأن تتصل من قاعدة البيانات بخدمات أخرى عبر `net.http_get()` و`net.http_post()` و`net.http_delete()`، كما في Supabase Cron وpg_net. كل بيئة تشغّلها لنفسها. الميزة ليست جاهزة للإنتاج: جُرّبت في حاويات مؤقتة، ولم تُجرَّب بعد على خادم حقيقي.

## شغّلها

1. افتح البيئة في لوحة الإدارة، ثم **Services**.
2. تحت **Scheduled jobs** اضغط **Turn on scheduled jobs**. تظهر **Applying…** ثم **On**، وعادةً خلال ثوانٍ.

وحدهم مالكو العميل ومديروه يرون هذا القسم. إذا لم يبقَ في الخادم اتصالات قاعدة بيانات كافية، يقول القسم ذلك ولا يتغير شيء.

تعمل المهام بدور المطوّر الخاص بالبيئة، وهو الدور نفسه الذي يدخل به [الوصول المباشر إلى قاعدة البيانات](database-access.ar.md). يُنشأ هذا الدور حين يُشغَّل الوصول المباشر أول مرة، وقبل ذلك تُسجَّل كل محاولة تشغيل فاشلةً برسالة "The developer role does not exist yet; turn database access on once". ولا يلزم أن يبقى الوصول المباشر مشغّلًا بعدها: تستمر المهام في العمل.

## استخدمها

تعمل ملفات الترحيل وأوامر SQL كما هي:

```sql
select cron.schedule('nightly-cleanup', '0 3 * * *', $$delete from public.events where created_at < now() - interval '30 days'$$);

select cron.schedule('app-maintenance', '* * * * *', $$
  select net.http_post(
    url := (select decrypted_secret from vault.decrypted_secrets where name = 'project_url') || '/functions/v1/maintenance',
    headers := jsonb_build_object('apikey', (select decrypted_secret from vault.decrypted_secrets where name = 'service_key')),
    body := '{}'::jsonb, timeout_milliseconds := 25000)
$$);

select jobid, jobname, schedule, active from cron.job;
select status, return_message, start_time from cron.job_run_details order by start_time desc limit 20;
select cron.unschedule('nightly-cleanup');
```

- **المواعيد:** خمسة حقول بتوقيت غرينتش (`*/5 * * * *`، و`0 3 * * mon-fri`، و`0 0 $ * *` لآخر يوم في الشهر)، أو `@hourly` و`@daily` و`@weekly` و`@monthly` و`@yearly`، أو `'30 seconds'` لفترة من ثانية إلى 59 ثانية.
- **الدوال:** هي `cron.schedule` و`cron.schedule_in_database` (لقاعدة البيانات هذه وحدها) و`cron.unschedule` و`cron.alter_job` و`net.http_get` و`net.http_post` و`net.http_delete`، بالمعاملات والقيم الافتراضية نفسها التي في Supabase. يُحفظ رد الطلب في `net._http_response` تحت الرقم الذي أعادته الدالة، لمدة 6 ساعات.
- **السجل:** لكل تشغيل صف في `cron.job_run_details` حالته `succeeded` أو `failed` مع الرسالة، ويُحفظ 7 أيام.
- **المشغّلات (triggers):** يعمل المشغّل الذي يستدعي `net.http_post` أيًّا كان الدور الذي يكتب الصف (`anon` أو `authenticated` أو `service_role`)، كما تعمل خطافات قاعدة البيانات في Supabase. ولا يقرأ الردود إلا دور المطوّر و`service_role`.

للتحقق في الشيفرة من توفر الميزة، ابحث عن الدالة لا عن الامتداد:

```sql
select to_regprocedure('cron.schedule(text,text,text)') is not null;
```

## الطلبات الصادرة

تخرج الطلبات من الخادم نفسه، لذلك تخضع لقاعدة أشد من قاعدة pg_net:

- فقط `http` و`https`، على المنفذين 80 و443.
- لا يذهب طلب أبدًا إلى عنوان loopback أو عنوان خاص أو محلي الرابط أو أي عنوان محجوز آخر، ولا إلى عناوين الخادم نفسه أو شبكات Docker فيه. يُرفض الاسم الذي يُترجم إلى أي عنوان من هذه، ويذهب الاتصال إلى العنوان الذي فُحص نفسه.
- الطريق الوحيد إلى هذا الخادم هو عنوانه العام (`SBARBASE_PUBLIC_URL`، بالبروتوكول والمضيف والمنفذ نفسها تمامًا)، فتستطيع المهمة أن تستدعي Edge Functions الخاصة بالبيئة عبر البوابة كأي عميل آخر.
- خمس إعادات توجيه على الأكثر، يُفحص كل منها من جديد. ستون ثانية على الأكثر لكل طلب، ويُحفظ من الرد 1 MiB على الأكثر.

يحصل الطلب المرفوض على صف رد يبدأ فيه `error_msg` بعبارة "Refused by the server's outgoing request policy".

## ما يبقى مختلفًا

- يبقى `create extension pg_cron` و`create extension pg_net` مرفوضين، وتبقى صفحة **Cron** في Studio مغلقة لأنها تبحث عن الامتداد. استخدم SQL كما في الأمثلة.
- تعمل المهام بدور المطوّر لا بالدور `postgres`. يُسجَّل العمود `username` ولا يُستخدم.
- تشغّل كل بيئة مهمتين معًا على الأكثر، وتحتفظ بمئة مهمة على الأكثر، ويُنهى التشغيل بعد 10 دقائق. وإذا حان موعد مهمة وتشغيلها السابق لم ينتهِ، تنتظره مرة واحدة.
- إطفاء المهام المجدولة يوقف التشغيل والطلبات ويحتفظ بالمهام وسجلها. وما دام [إيقاف الكتابة](write-pause.ar.md) قائمًا على البيئة فلا يعمل شيء.
- يرفض تصدير الاسترداد البيئةَ ما دامت مهامها المجدولة مشغّلة؛ أطفئها أولًا.

كيف تعمل ولماذا صُممت هكذا: [مذكرة التصميم](../engineering/SCHEDULED-JOBS.md).
