[English](configuration.md)

# الإعدادات

ما يستطيع المشغّل ضبطه، وأين يحفظ صباربيز حالته. المسارات نسبية إلى نسخة المستودع. لا يوجد في صباربيز ملف إعدادات واحد: الإعدادات موزعة على الملفات والخيارات أدناه، والباقي يحدده الكود وملفات القفل المثبّتة الإصدار.

## متطلبات الخادم

| الإعداد | مكانه | ملاحظات |
|---|---|---|
| عنوان Docker | `DOCKER_HOST`، أو `--docker-host` في `deploy/server-acceptance.sh` | يجب أن يصل إلى خدمة Docker أصلية على Linux. خدمة النظام لا ترث بيئة الطرفية، فإذا كان سياق Docker على الخادم يشير إلى مقبس نسخة سطح المكتب، فمرّره صراحة في ملف الوحدة |
| Bun | على `PATH`، و`--bun-dir` لوحدة الخدمة | يبني لوحة الإدارة ويشغّل خادم الحلقة المحلية |
| Python | `/usr/bin/python3`، الإصدار 3.14 أو أحدث | الخيار `--python` في `deploy/server-acceptance.sh` يغيّره |

## الصور المثبّتة الإصدار

الملفات `lab/distro-image.lock.json` و`lab/images.lock.json` و`lab/storage-image.lock.json` تثبّت كل صورة من الأصل بوسمها وبصمتها. لا تغيّرها إلا عبر `lab/pin_update.py`، وانظر [الترقيات](../guides/upgrades.ar.md).

## المثبّت والخدمة

أوامر `/usr/bin/python3 lab/install_server.py <command>`:

| الأمر أو الخيار | المعنى |
|---|---|
| `check` | فحص مسبق للقراءة فقط، وينتهي برمز غير الصفر عند أي عائق |
| `plan` | يطبع خطوات التثبيت بدقة |
| `install --bootstrap-file PATH` | يثبّت مستخدمًا ملف المشغّل ذا الصلاحية 0600 (أدناه) |
| `smoke` | يتحقق من أن لوحة الإدارة ونقاط الإدارة تجيب |
| `supervise` | يولّد وحدة systemd لنسخة المستودع هذه ويتحقق منها، والخيار `--apply` يثبّتها (للجذر فقط) |
| `--service-user`، `--home`، `--bun-dir` | الحساب ومجلد المنزل ومجلد Bun التي تُكتب في الوحدة. الحساب الافتراضي `sbarbase` |

يقبل `deploy/server-acceptance.sh` الخيارات `--rehearse` و`--skip-install` و`--install-unit` و`--bootstrap-file` و`--service-user` و`--home` و`--bun-dir` و`--docker-host` و`--python`. الوحدة المرفقة هي `deploy/sbarbase.service`. لا تعدّل نسخة مثبّتة منها يدويًا، بل ولّدها بالأمر `supervise`.

## وكيل TLS

ينهي `bun deploy/console-tls-proxy.ts` اتصالات HTTPS أمام خادم الحلقة المحلية.

| الخيار | الافتراضي | المعنى |
|---|---|---|
| `--cert`، `--key` | إلزامي | ملفا الشهادة والمفتاح |
| `--public-host` | إلزامي | اسم الخادم العام (والمنفذ اختياريًا). يُستخدم في إعادة التوجيه بدل ترويسة `Host` التي يرسلها العميل |
| `--https-port` | `8443` | منفذ استماع HTTPS |
| `--http-port` | `8080` | منفذ HTTP عادي يعيد التوجيه بالرمز `308` |
| `--upstream` | خادم الحلقة المحلية العامل | الوجهة التي تُمرَّر إليها الطلبات |
| `--max-body` | 1 MiB | الأجسام الأكبر تأخذ `413` |

## ملف الإعداد الأولي للمشغّل

يسأل `/usr/bin/python3 lab/operator_file.py PATH` عن البيانات دون إظهار ما تكتبه، ثم يكتب ملف JSON بصلاحية 0600 فيه `email` و`password` و`organization` فقط. الخيار `--stdin` يقرأ الكائن نفسه من الإدخال القياسي (بحد أقصى 8192 بايت) للأتمتة. مرّر الملف إلى `install --bootstrap-file`، أو شغّل `lab/bootstrap.py` تفاعليًا بدلًا منه. انظر [إعداد المشغّل](../guides/operator-setup.ar.md).

## بريد كل بيئة

يُفعَّل بريد التطبيق (التأكيد والاستعادة) لبيئة واحدة بملف واحد هو `.secrets/upstream/<runtime>-mail.json` بصلاحية 0600. اكتبه أو اعرضه أو احذفه بالأمر `lab/mail_config.py write|show|remove PATH`، ولا تُطبع كلمة المرور أبدًا. المفاتيح: `host` و`port` و`user` و`pass` و`admin_email` و`sender_name` و`reply_to` و`max_frequency` و`otp_exp` و`otp_length` و`secure_email_change` و`autoconfirm`. يسري التغيير بمطابقة حاوية Auth لتلك البيئة. المخطط الكامل: [بريد البيئة](../engineering/ENVIRONMENT-EMAIL.md).

## تنبيهات المشغّل

إذا وُجد الملف `.lab/upstream/notifications.json`، يرسل العامل أحداث المشغّل بالبريد، أو بخطاف ويب موقّع، أو بالاثنين:

```json
{
  "schema": 1,
  "email": {"enabled": true, "host": "smtp.example.invalid", "port": 587,
            "from": "sbarbase@example.invalid", "to": "operator@example.invalid", "tls": "starttls"},
  "webhook": {"enabled": true, "url": "https://hooks.example.invalid/sbarbase",
              "secretFile": ".secrets/upstream/notifier.json"}
}
```

قيمة `tls` واحدة من `none` أو `starttls` أو `tls`. سر توقيع خطاف الويب محفوظ في `.secrets/upstream/notifier.json` بالشكل `{"schema": 1, "webhookSecret": "<64 hex characters>"}`. التصميم الكامل: [تنبيهات المشغّل](../engineering/OPERATOR-NOTIFICATIONS.md).

## مجلدات الحالة

يتجاهل Git المجلدين كليهما. لا تطبع محتوى `.secrets/` أبدًا، ولا تحذف أيًّا منهما لتتجاوز رفضًا.

| المسار | ما يحفظه |
|---|---|
| `.lab/upstream/control.sqlite` | الفهرس: العملاء والمشاريع والبيئات والعضويات والمهام والتوجيه |
| `.lab/upstream/server.json` | عنوان خادم الحلقة المحلية العامل ورقم عمليته (PID) |
| `.lab/upstream/*.json` | سجلات العمليات وواصفات الاستعادة ومخرجات الفحوص الحية |
| `.lab/ui/` | لوحة الإدارة بعد بنائها |
| `.secrets/upstream/runtime.json` | بيانات الاعتماد المولّدة للخدمات التي يملكها صباربيز |
| `.secrets/upstream/managed-keys.sqlite` | بصمات المفاتيح العامة وبياناتها الوصفية |
| `.secrets/upstream/bootstrap.json` | سجل عملية إعداد المشغّل (بلا كلمة مرور) |

## متغيرات البيئة الداخلية

المتغيرات التي تبدأ بـ`SBARBASE_*` (مثل `SBARBASE_WORKER_FD` و`SBARBASE_EFFECT_TOKEN`) تنقل واصفات الأقفال والرموز بين عمليات صباربيز نفسها. ليست إعدادات للمشغّل، فلا تضبطها يدويًا.

## ما لا يمكن ضبطه بعد

فئات الموارد وعتبات القبول ثابتة في الكود ([lab/resource_policy.py](../../lab/resource_policy.py)، [lab/pressure_admission.py](../../lab/pressure_admission.py)). حدود التزامن في البوابة ومهلها قيم افتراضية في المُنشئ داخل [src/gateway/concurrency.ts](../../src/gateway/concurrency.ts).
