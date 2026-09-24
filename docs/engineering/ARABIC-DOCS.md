# Arabic documentation

Every page a person reads to install, run, understand or contribute to Sbarbase
exists in English and in Arabic. The Arabic page sits beside the English one with
the `.ar.md` suffix: `docs/guides/quickstart.md` and `docs/guides/quickstart.ar.md`.
A change to one updates the other in the same commit.

## Which pages

The bilingual set is the reader-facing documentation, listed in
`lab/test_docs_bilingual.py`: the root `README.md`, `SECURITY.md`,
`CONTRIBUTING.md` and `CHANGELOG.md`, `lab/README.md`, `docs/README.md`, every
page under `docs/guides/`, `docs/explain/` and `docs/reference/`,
`docs/decisions/README.md` and the console notes in `docs/design/`. A new page in
one of those directories needs its Arabic sibling or the test fails.

The engineering notebook (`docs/engineering/`, including this note), the
checkpoints log, evidence and `docs/diagrams/README.md` stay English only.
Diagrams are the exception: each one has an Arabic variant `X.ar.svg` for the
Arabic pages. They change daily and are read by the
people and agents building the system.

## Page shape

- The first line of both files is a language switch:
  English file: `[العربية](quickstart.ar.md)`; Arabic file: `[English](quickstart.md)`.
- Fenced code blocks, inline code, paths, environment variables, JSON keys,
  HTTP status codes and error strings are byte for byte the same as the English
  page. `lab/test_docs_bilingual.py` compares the fenced blocks of each pair.
- A relative link goes to the `.ar.md` sibling when one exists, otherwise to the
  English page. A link with a `#fragment` always goes to the English page,
  because Arabic headings produce Arabic anchors.
- Product and upstream names stay in Latin script: Supabase, PostgreSQL, Auth,
  REST, PostgREST, Storage, Studio, Docker, systemd, HBA, GoTrue.
- Western digits (1, 2, 3). No em or en dash, as in English.
- Start each paragraph, heading and list item with an Arabic word, not a code
  span or a Latin name, so GitHub picks right to left for the block.

## Voice

Plain, modern standard Arabic that an engineer in Damascus, Cairo or Riyadh reads
without stopping. Adapt the sentence; never mirror English word order.

- Guides speak to the reader with imperatives: ثبّت، شغّل، انسخ، تحقق.
- Short sentences. One idea each.
- Avoid the translated-English patterns: يتم + مصدر (write the verb: تُنشأ
  البيئة, not يتم إنشاء البيئة), قم بـ, حيث as a general connector, بالإضافة إلى
  ذلك, كما أن at the start of a sentence, and a filler هذا or هذه before every noun.
- Do not repeat a word or idea in the same paragraph to sound thorough.
- Keep the English page's honesty. "Not production ready" is ليس جاهزًا للإنتاج,
  never softened. Say what was run and what it does not prove.

## Terms

Use these, and only these, so the same thing has the same name on every page.
They follow the published website (`website/src/content.ts`).

| English | Arabic |
|---|---|
| Sbarbase | صباربيز |
| server, host | خادم |
| installation | التثبيت (in code terms: `installation`) |
| client (user-facing) | عميل |
| organization (code term) | منظمة |
| project | مشروع |
| environment | بيئة |
| production / staging | إنتاج / تجربة |
| operator | المشغّل |
| owner / admin / viewer | مالك / مدير / مشاهد |
| member, membership | عضو، عضوية |
| console | لوحة الإدارة |
| gateway | البوابة |
| engine (PostgreSQL engine) | محرك |
| database | قاعدة بيانات |
| container | حاوية |
| volume | وحدة تخزين (`volume`) |
| image (container image) | صورة الحاوية |
| login (PostgreSQL) | حساب دخول |
| scoped login | حساب دخول محصور |
| role | دور |
| publishable key | مفتاح عام |
| secret, credential | سر، بيانات اعتماد |
| tenant | مستأجر |
| catalog | الفهرس |
| placement | مكان التشغيل |
| routing record | سجل التوجيه |
| maintenance | الصيانة |
| provisioning | التجهيز |
| worker | العامل |
| job | مهمة |
| effect receipt | إيصال الأثر |
| journal | سجل العملية |
| fence | سياج |
| operation token | رمز العملية |
| tombstone | شاهد الإلغاء |
| generation, generation pin | جيل، تثبيت الجيل |
| retained | محفوظ |
| reconcile | مطابقة |
| unknown outcome | نتيجة مجهولة |
| admission | القبول |
| refusal, refuse | رفض، يرفض |
| pressure | الضغط |
| connection budget | ميزانية الاتصالات |
| drain | التصريف |
| deadline | مهلة |
| noisy neighbour | الجار المزعج |
| isolation | العزل |
| backup | نسخة احتياطية |
| export | تصدير |
| restore | استعادة |
| move | نقل |
| cutover | التحويل |
| upgrade | ترقية |
| rollback | تراجع |
| evidence | الأدلة |
| probe | فحص حي |
| rehearsal (VM) | تجربة كاملة |
| threat model | نموذج التهديد |
| pinned (image) | مثبّت الإصدار |
| upstream | الأصل (upstream) |
| Docker daemon | خدمة Docker |
| runtime (the running services) | طبقة التشغيل |
| supervisor, systemd unit | المشرف، الوحدة |
| server acceptance | اختبار الاستلام |
| adversarial review | مراجعة هجومية |
| issue, pull request, commit | بلاغ (issue)، طلب دمج، إيداع |
| fixture | بيئة ثابتة |
| retire (a recovery target) | إيقاف نهائي |
