[English](authentication.md)

# المصادقة في لوحة الإدارة

لكل بيئة مساحة مصادقة خاصة بها. تمرّ هذه الصفحة عليها بالترتيب الذي تعرضه لوحة الإدارة، فتتبعها واللوحة مفتوحة أمامك وتعرف ما تنقر عليه بعد ذلك. اللقطات من صفحات الاختبار في لوحة الإدارة ببيانات مثال (عناوين `example.com` وأسماء مختلقة)، وليست من تثبيت حقيقي، وكل تعليق يقول ذلك. الأرقام على اللقطة تدلّ على ما تنقر عليه، بالترتيب.

افتح البيئة ثم **Sign-in settings**. لا يرى هذه الصفحة إلا المالك والمدير. كل ما فيها خاص بهذه البيئة وحدها: لا يظهر هنا مزوّدو بيئة أخرى ولا أسرارها.

![مساحة المصادقة في صفحة Sign In / Providers. 1 Sign-in settings، 2 مزوّد Google، 3 Save and apply.](images/authentication/providers.webp)

*بيانات تجريبية. قائمة الصفحات على اليسار، وإعدادات الصفحة المختارة بجانبها، وشريط حفظ واحد في الأسفل. ولذلك بقيت أسماء الأزرار والحقول بالإنجليزية.*

تفاصيل المرجع (لماذا يغيب إعداد ما، وما جُرّب على Auth قيد التشغيل، وكيف يُحمى الحفظ) في [إعدادات تسجيل الدخول](sign-in.ar.md). أما هذه الصفحة فهي الجولة.

## قبل أن تبدأ

1. اضبط العنوان العام للخادم مرة واحدة، حتى لا تشير روابط callback والبريد إلى `http://localhost` ([الخطوة 1 في إعدادات تسجيل الدخول](sign-in.md#1-give-the-server-its-public-address)).
2. اعرف الصفحات التي تحفظ بنفسها. الصفحات **Sign In / Providers** و**URL Configuration** و**Sessions** و**Attack Protection** و**Multi-Factor** و**Hooks** و**Single Sign-On** و**Audit Logs** يحفظها معًا الشريط السفلي (**Save and apply**). أما **Emails** (وفيها SMTP Settings وTemplates) و**Phone** فلكل منهما زر **Save changes** خاص به، والشريط السفلي مخفي فيهما. وإضافة مزوّد دخول موحّد أو حذفه يسري فورًا.
3. يعيد الحفظ تشغيل Auth في هذه البيئة. يبقى من سجّل دخوله مسجَّلًا، وتبقى بيانات المستخدمين والجلسات.

## معنى "Applied" و"Not applied"

الشارة في أعلى المساحة جهة اليمين تبيّن حالة آخر حفظ.

| الشارة | المعنى | ما تفعله |
|---|---|---|
| **Default settings** | لم يُحفظ شيء هنا قط. يعمل Auth بالإعدادات المثبّتة. | غيّر إعدادًا واحفظ. |
| **Applying…** | يعاد تشغيل Auth بما حفظت. ثوانٍ قليلة. | انتظر. تعيد الصفحة الفحص وحدها. |
| **Applied** | بدأ Auth بالإعدادات المحفوظة. | جرّب. **Applied لا يعني أن المزوّد أو خادم SMTP أو خدمة الرسائل القصيرة قبلت بيانات اعتمادك.** سجّل حسابًا مرة واحدة لتتأكد. |
| **Not applied** | لم يبدأ Auth بالإعدادات الجديدة. يستمر Auth السابق في العمل، فلا يُحرم أحد من الدخول. يظهر السبب تحت عنوان الصفحة. | عالج السبب ثم اضغط **Save and apply** من جديد. الزر يبقى متاحًا عمدًا. |

![شارة Not applied مع السبب تحت عنوان الصفحة وتلميح إعادة المحاولة في شريط الحفظ. 1 Save and apply.](images/authentication/failed.webp)

*بيانات تجريبية. نص السبب في هذه اللقطة مثال؛ سببك الحقيقي يأتي من Auth عندك.*

قد تواجه رفضين: "Wait a few seconds before saving again" (حفظ ثانٍ خلال 10 ثوانٍ من آخر تغيّر في الحالة، لأن كل حفظ يعيد تشغيل Auth) و"These settings changed since you opened them" (سبقك شخص آخر إلى الحفظ؛ أعد تحميل الصفحة واحفظ).

## 1. Sign In / Providers

**الغرض:** من يسجّل حسابًا جديدًا، وبأي مزوّدين يدخل الناس.

في **User signups** ثلاثة مفاتيح: **Allow new users to sign up** و**Allow manual linking** (يتيح لمن سجّل دخوله ربط طريقة دخول أخرى بحسابه) و**Allow anonymous sign-ins**. إيقاف الأول يمنع أي تسجيل جديد تمامًا.

تعرض **Auth providers** المزوّدين Apple وMicrosoft (Azure) وBitbucket وDiscord وFacebook وFigma وGitHub وGitLab وGoogle وKakao وKeycloak وLinkedIn وNotion وSlack وSpotify وTwitch وX (Twitter) وWorkOS وZoom، لكل منها شعارها (يعرض Microsoft وWorkOS حرفًا) وشارة **Enabled** أو **Disabled**. بعد **Save and apply** فاشل تظهر الشارة **Needs attention** على المزوّد الذي في حقوله خطأ (معرّف عميل أو سر ناقص، أو مسافة في قيمة، أو عنوان خادم غير صالح)، ويُرفض الحفظ حتى تصلحه.

### لوحة المزوّد

انقر مزوّدًا لتفتح لوحته. الحقول المهمة:

| الحقل | ما تكتبه |
|---|---|
| **Enable Sign in with ...** | مفتاح يشغّل المزوّد لهذه البيئة وحدها. |
| **Client ID** (**Client IDs** عند Google) | من المزوّد. يقبل Google قائمة مفصولة بفواصل (الويب وAndroid وiOS وOne Tap وإضافات Chrome). |
| **Client secret** | من المزوّد. **لا يُعرض مرة ثانية** بعد حفظه: يظهر في الحقل "Saved. Leave empty to keep it.". لا تكتب سرًا جديدًا إلا لتستبدل به القديم. |
| **Server address** | للمزوّدات التي لها عنوان خادم خاص فقط. يشترطه Keycloak، ويقبله Microsoft (Azure) وGitLab وWorkOS إن شغّلت خادمك. |
| **Skip nonce checks** | لـ Apple وGoogle فقط. أقل أمانًا؛ استعمله حين لا تستطيع الحصول على nonce، كما في iOS. |
| **Allow users without an email** | لمزوّد قد لا يعيد عنوان بريد. |
| **Callback URL (for OAuth)** | للقراءة فقط، وبجانبه زر **Copy**. **هذه هي القيمة الوحيدة التي تنسخها من لوحة الإدارة** وتسجّلها عند المزوّد. |

يحذف **Remove saved credentials** (الأحمر) معرّف العميل والسر المحفوظين لهذا المزوّد عند الحفظ. ويغلق **Done** اللوحة، ولا يُحفظ شيء قبل **Save and apply**.

### مثال: Google

1. افتح **Google** واضغط **Copy** بجانب **Callback URL (for OAuth)**.
2. في Google Cloud console افتح APIs & Services ثم Credentials، وأنشئ OAuth client ID من نوع Web application، ثم الصق عنوان callback في authorized redirect URIs.
3. انسخ معرّف العميل وسره من Google إلى اللوحة، وشغّل **Enable Sign in with Google**، واضغط **Done** ثم **Save and apply**.

![لوحة مزوّد Google. 1 Client IDs، 2 Client secret، 3 Callback URL مع زر Copy، 4 Done.](images/authentication/provider-google.webp)

*بيانات تجريبية. يظهر في حقل السر "Saved. Leave empty to keep it." لأن سرًا محفوظًا موجود.*

### مثال: GitHub

1. افتح **GitHub** وانسخ **Callback URL (for OAuth)**.
2. في GitHub افتح Settings ثم Developer settings ثم OAuth Apps، وسجّل OAuth App جديدًا. والصق عنوان callback في authorization callback URL.
3. الصق معرّف العميل وسرًا مولَّدًا في اللوحة، وشغّل المفتاح، واضغط **Done** ثم **Save and apply**.

### مثال: Microsoft (Azure)

1. افتح **Microsoft (Azure)** وانسخ **Callback URL (for OAuth)**.
2. في Microsoft Entra سجّل تطبيقًا، وأضف عنوان callback كـ Web redirect URI، ثم أنشئ سر عميل. تعرض Microsoft قيمة السر مرة واحدة، فانسخها حينها.
3. الصق معرّف التطبيق (client ID) والسر في اللوحة. يمكن ترك **Server address (optional)** فارغًا؛ لا تملأه إلا لتحصر الدخول في مستأجر Microsoft واحد، بعنوان المستأجر كما تعرضه Microsoft. جرّب الدخول بمستأجرك قبل أن تعتمد عليه.
4. شغّل المفتاح، واضغط **Done** ثم **Save and apply**.

![لوحة Microsoft (Azure) لمزوّد لم يُضبط من قبل. 1 مفتاح التشغيل، 2 Client ID، 3 Client secret، 4 Server address (optional)، 5 Callback URL.](images/authentication/provider-microsoft.webp)

*بيانات تجريبية. هكذا تبدو لوحة فارغة في أول مرة.*

في تطبيقك يبقى الاستدعاء المعتاد:

```js
await supabase.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: 'https://app.example.com/welcome' } })
```

**انتبه:** لا يمكن قراءة السر بعد حفظه. إن ضاع فأنشئ سرًا جديدًا عند المزوّد والصقه. وترفض الصفحة مزوّدًا شغّلته دون سره قبل أن يصل الطلب إلى Auth.

## 2. URL Configuration

**الغرض:** إلى أين يرسل Auth الناس بعد الدخول أو بعد فتح رابط في بريد.

- **Site URL**: عنوان تطبيقك، مثل `https://app.example.com`. هو الصفحة التي يرسل Auth الناس إليها افتراضيًا. اكتب هنا عنوانًا واحدًا عاديًا؛ والأنماط مكانها **Redirect URLs**.
- **Redirect URLs**: العناوين الأخرى التي يجوز لـ Auth أن يرسل الناس إليها، عنوان في كل سطر وحتى 50، ويبدأ كل عنوان بمخطط (`https://`، أو `myapp://` لتطبيق جوال) ولا يحوي مسافات ولا فواصل. يطابق `*` و`**` أجزاء من العنوان، فيسمح `https://app.example.com/**` بالموقع كله.

![صفحة URL Configuration. 1 Site URL، 2 Redirect URLs، 3 Save and apply.](images/authentication/url-configuration.webp)

*بيانات تجريبية.*

**انتبه:** عنوان إعادة التوجيه الأوسع مما تحتاج يتيح لرابط دخول أن يرسل الناس إلى صفحة لا تتحكم بها. اجعل القائمة بأضيق ما يسمح به تطبيقك، ولا تُبقِ `http://localhost:3000/**` إلا مدة التطوير.

## 3. Sessions

**الغرض:** كم يبقى الشخص مسجَّلًا دخوله.

| الحقل | المدى | إن تُرك فارغًا |
|---|---|---|
| **Access token expiry time (seconds)** | من 60 إلى 86400 | القيمة الافتراضية في Auth، 3600 ثانية. يجدّد العميل الرمز، فلا يخرج أحد بسببها. |
| **Time-box user sessions (hours)** | من 1 إلى 87600 | بلا حد: لا تنتهي الجلسة بسبب عمرها. |
| **Inactivity timeout (hours)** | من 1 إلى 87600 | بلا حد: لا تنتهي الجلسة الخاملة. |
| **Enforce single session per user** | مفتاح | مغلق: يجوز الدخول من أجهزة عدة. مفتوح: تبقى الجلسة الأحدث نشاطًا فقط. |

![صفحة Sessions. 1 Access token expiry time، 2 Time-box، 3 Inactivity timeout، 4 Save and apply.](images/authentication/sessions.webp)

*بيانات تجريبية. مهلة الخمول هنا 168 ساعة (7 أيام) والحد الزمني فارغ.*

يسري الحد الجديد على الجلسات التي تبدأ بعد الحفظ، وتحتفظ الجلسة الجارية بالعمر الذي بدأت به. وحقلا الساعات يقبلان ساعات كاملة، لا أجزاء ساعة.

## 4. Rate limits

**الغرض:** ألا يستهلك زائر واحد حصة البريد أو الرسائل القصيرة. ليست في لوحة الإدارة صفحة مستقلة للحدود؛ كل حد بجانب ما يحدّه.

- في **Emails** حدود البريد: عدد الرسائل في الساعة، وطلبات رسائل الدخول والتحقق من الرموز كل 5 دقائق (تُحسب للبيئة كلها لا لكل زائر)، وأقل عدد من الثواني بين رسالتين لشخص واحد. انظر [Emails](#8-emails).
- في **Phone** الحد **Rate limit for sending SMS (messages/h)**، من 1 إلى 1000000، على كل الأرقام معًا. ويحدّ كذلك مما قد يحاسبك عليه مزوّد الرسائل القصيرة. انظر [Phone](#7-phone).

حدود تجديد الرموز والدخول المجهول وWeb3 غير معروضة: يطبّقها Auth لكل عنوان عميل، والبوابة لا تمرّر إليه عنوان عميل موثوقًا حتى الآن.

## 5. Attack Protection

**الغرض:** كلمات المرور سهلة التخمين.

- **Prevent use of leaked passwords**: يرفض كلمات مرور وُجدت في تسريبات معروفة. يسأل Auth خدمة Have I Been Pwned العامة، ويصل Auth في هذه البيئة إلى الشبكة الخارجية ما دام المفتاح مفتوحًا.
- **Minimum password length**: من 6 إلى 72 حرفًا. يجزّئ Auth 72 بايتًا من كلمة المرور على الأكثر، فأعلى قيمة 72.

![صفحة Attack Protection. 1 Minimum password length، 2 Save and apply.](images/authentication/attack-protection.webp)

*بيانات تجريبية.*

إعداد captcha في سحابة Supabase غير معروض هنا.

## 6. Multi-Factor

**الغرض:** خطوة ثانية (تطبيق مصادقة، TOTP) لمن يستعمل تطبيقك. وهي منفصلة عن الدخول بخطوتين الخاص بالمشغّل إلى لوحة الإدارة، وإعداده في Account security.

- **Allow people to add an authenticator app (TOTP)** و**Allow people to verify with an authenticator app (TOTP)**: مفتوحان افتراضيًا.
- **Maximum number of per-user MFA factors**: من 1 إلى 30.

![صفحة Multi-Factor. 1 Maximum number of per-user MFA factors، 2 Save and apply.](images/authentication/multi-factor.webp)

*بيانات تجريبية.*

**انتبه:** إيقاف التحقق يوقف قبول رموز TOTP، فلا يستطيع من يعتمد عليها اجتياز الخطوة الثانية. وإيقاف إضافة تطبيق المصادقة لا يمس العوامل الموجودة، فتبقى تعمل.

## 7. Phone

**الغرض:** التسجيل والدخول برقم هاتف ورمز يصل برسالة قصيرة.

![صفحة Phone. 1 Provider، 2 بيانات اعتماد المزوّد، 3 Enable phone confirmations، 4 Rate limit for sending SMS، 5 Save changes.](images/authentication/phone.webp)

*بيانات تجريبية. معرّف الحساب (account SID) قيمة مثال.*

شغّل **Enable phone sign-ups and sign-ins**، واختر **Provider** (Twilio أو Twilio Verify أو MessageBird أو Textlocal أو Vonage)، واملأ بيانات الاعتماد التي تعرضها الصفحة، واضغط **Save changes**.

- **بيانات الاعتماد السرية لا تُعرض مرة ثانية** ("Stored value is hidden").
- تغيير المزوّد يحتاج بيانات اعتماده هو؛ لا تنتقل القديمة.
- **Enable phone confirmations** مفتوح افتراضيًا. **انتبه:** إن أُغلق دخل أصحاب الأرقام الجديدة دون التحقق من أن الرقم لهم.
- ما دام الدخول بالهاتف مفتوحًا، يصل Auth في هذه البيئة إلى الإنترنت، لأن كل المزوّدين خدمات HTTPS.
- لا تعني **Applied** أن رسالة وصلت. سجّل برقم حقيقي لتتأكد.

## 8. Emails

**الغرض:** الرسائل التي يرسلها Auth (تأكيد التسجيل، الرابط السحري، إعادة تعيين كلمة المرور). فيها صفحتان فرعيتان: **SMTP Settings** و**Templates**.

### SMTP Settings

شغّل **Enable custom SMTP** ثم املأ **Sender email address** و**Sender name** و**Host** و**Port number** (المعتاد 465 أو 587، وتجنّب 25) و**Username** و**Password**، واضغط **Save changes**.

- **لا تُعرض كلمة المرور مرة ثانية.** المحفوظة تظهر "Stored password is hidden. Enter a new password to replace it.". اترك الحقل فارغًا لتبقيها.
- **بلا custom SMTP لا يُرسل أي بريد**، وتؤكَّد التسجيلات الجديدة تلقائيًا.
- مع custom SMTP يجب أن يؤكد المسجِّلون الجدد بريدهم؛ ولا يتأثر من سجّل قبلهم.
- إيقاف المفتاح ثم الحفظ يحذف إعدادات SMTP وكلمة مروره المخزّنة، ويعيد Confirm email وحدود البريد إلى قيمها الافتراضية.
- تعني **Applied** أن Auth بدأ بالإعدادات. لا تعني أن مزوّد SMTP قبلها. جرّب تسجيلًا تجريبيًا.

**Confirm email** وحدود البريد في صفحة Emails (اليوم تحت **Confirmation and limits** في SMTP Settings، وتظهر ما دام custom SMTP مفتوحًا). **انتبه إلى Confirm email:** حين يُغلق لا يُتحقق من عنوان المسجِّلين الجدد، فيستطيع أي شخص تسجيل عنوان ليس له، ولا يطبّق Auth حد الرسائل في الساعة.

### Templates

افتح **Templates** لتعدّل الرسائل الست: Confirm sign up وInvite user وMagic link وChange email address وReset password وReauthentication. لكل رسالة **Subject heading** وجسم HTML، وعرض **Source** وعرض **Preview**، وقائمة بالمتغيّرات التي يملؤها Auth مثل `{{ .ConfirmationURL }}` و`{{ .Token }}`. ويعيد **Reset to default** نص Supabase الأصلي.

- لا تُسلَّم الرسالة المخصّصة إلا ما دام custom SMTP مفتوحًا. وتقول الصفحة ذلك حين يكون مغلقًا.
- العنوان سطر واحد بحد أقصى 200 بايت، والجسم 32 KiB على الأكثر، ولكل `{{` ما يقابله `}}`. ترفض الصفحة الأقواس غير المتقابلة قبل الحفظ، وقد يرفض الخادم قالبًا مشوّهًا برسالة عند الحفظ.
- يجلب Auth الرسالة من لوحة الإدارة ويحتفظ بالجسم في الذاكرة بعد أول جلب، ولهذا يعيد كل حفظ تشغيل Auth. جدار ناري على الخادم يحجب ذلك المسار يجعل Auth يعود إلى نصه الأصلي مع أن الشارة تقول **Applied**؛ أرسل رسالة تجريبية لتتأكد.

## 9. Hooks

**الغرض:** تشغيل دالتك في Postgres عند نقاط محددة من عمل Auth. الخطافات السبعة هي Customize Access Token (JWT) Claims وSend Email وSend SMS وMFA Verification Attempt وPassword Verification Attempt وBefore User Created وAfter User Created.

![صفحة Hooks وفيها خطاف واحد مفتوح. 1 المفتاح، 2 Schema وFunction، 3 Save and apply.](images/authentication/hooks.webp)

*بيانات تجريبية. add_user_role اسم دالة مثال.*

1. أنشئ الدالة في قاعدة بيانات هذه البيئة (من محرر SQL).
2. نفّذ سطري `grant` اللذين تعرضهما الصفحة، ليستطيع حساب دخول Auth تشغيلها.
3. بعد ذلك فقط شغّل الخطاف، واكتب **Schema** و**Function**، ثم **Save and apply**.

**انتبه:** الخطاف المفتوح الذي يسمّي دالة غير موجودة، أو لا يملك حساب دخول Auth إذن تشغيلها، يُفشل التسجيلات وعمليات الدخول التي يغطيها. أنشئ الدالة وامنحها الإذن أولًا ثم افتح الخطاف، وجرّبه قبل أن تتركه مفتوحًا. المعروض خطافات دوال Postgres فقط.

## 10. Single Sign-On

**الغرض:** أن يدخل الناس عبر مزوّد هوية SAML 2.0 تابع لشركتهم.

![صفحة Single Sign-On. 1 مفتاح SAML، 2 العنوانان اللذان تعطيهما لمزوّد الهوية، 3 Add identity provider.](images/authentication/sso.webp)

*بيانات تجريبية. example.com نطاق الشركة في مزوّد المثال.*

1. شغّل **Enable SAML 2.0 Single Sign-On** واضغط **Save and apply**. في المرة الأولى تنشئ لوحة الإدارة مفتاح توقيع هذه البيئة وتحتفظ به.
2. انسخ **SAML metadata URL** و**Assertion consumer service (ACS) URL** وسجّلهما في مزوّد الهوية.
3. نزّل ملف metadata XML من المزوّد، واضغط **Add identity provider**، والصقه في **Metadata XML**، واكتب في **Domains** النطاقات التي يخدم أصحابها، واضغط **Add provider**. لا يُجلب عنوان ويب للبيانات الوصفية: الصق ملف XML نفسه.
4. في تطبيقك استدعِ `signInWithSSO` بنطاق بريد الشخص.

**انتبه:** حين يُحذف المزوّد لا يعود أصحابه قادرين على الدخول عبره. وإن أُزيل ملف مفتاح التوقيع من الخادم يُنشأ مفتاح جديد، ويجب إعطاء كل مزوّد هوية الشهادة الجديدة.

## 11. Audit Logs

**الغرض:** أن ترى ما سجّله Auth: عمليات الدخول والتسجيل وتجديد الرموز وتغييرات الحسابات، الأحدث أولًا، 50 في كل صفحة، مع من فعل وماذا وعنوان IP ومتى.

![صفحة Audit Logs. 1 مفتاح Write audit logs to the database، 2 Refresh وسهما الصفحات.](images/authentication/audit-logs.webp)

*بيانات تجريبية. الأشخاص والعناوين مختلقة.*

تأتي القائمة من واجهة Auth الإدارية نفسها، فلا يلزم تشغيل الوصول إلى قاعدة البيانات. ويتحكم المفتاح **Write audit logs to the database** في تسجيل المدخلات الجديدة: إغلاقه يوقف المدخلات الجديدة ويُبقي القديمة.

## المظهر الداكن

تتبع لوحة الإدارة مظهر النظام، وفي الشريط الجانبي زر للمظهر. كل ما سبق يبدو في المظهر الداكن بالشكل نفسه.

![صفحة Sign In / Providers في المظهر الداكن.](images/authentication/providers-dark.webp)

*بيانات تجريبية.*

## واجهة من اليمين إلى اليسار

واجهة لوحة الإدارة الآن بالإنجليزية فقط ولا تنقلب إلى الاتجاه العربي وحدها. لكن تخطيطها يدعم الاتجاه من اليمين إلى اليسار، وتُظهره هذه اللقطة حين تُعرض الصفحة بهذا الاتجاه.

![صفحة Sign In / Providers في صفحة اتجاهها من اليمين إلى اليسار. 1 Sign-in settings، 2 مزوّد Google، 3 Save and apply.](images/authentication/providers-rtl.webp)

*بيانات تجريبية. النصوص إنجليزية والتخطيط معكوس.*

## قائمة تحقق

- العنوان العام مضبوط، فلا تشير روابط callback والبريد إلى `http://localhost`.
- Site URL وقائمة ضيقة من عناوين إعادة التوجيه.
- لكل مزوّد: انسخ callback من لوحته، والصق معرّف العميل والسر فيها، ثم **Save and apply**، ثم سجّل دخولًا حقيقيًا واحدًا.
- شغّل custom SMTP مع تسجيل تجريبي، وترك **Confirm email** مفتوحًا.
- لكل خطاف: دالة منشأة وممنوحة الإذن قبل فتحه.
- الشارة تقول **Applied**.
