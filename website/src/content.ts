// Copy for the Arabic (/) and English (/en/) pages. Every claim here is grounded
// in the repository's docs: docs/reference/status.md for what works and every
// number, docs/engineering/plans/2026-09-23-roadmap.md for what comes next, and
// docs/evidence/vm-empty-server-rehearsal.json for the empty-server rehearsal.
// No long dashes anywhere; the test refuses them.

const repo='https://github.com/M7MMAD-OMAR/sbarbase';
const doc=(path:string)=>`${repo}/blob/main/${path}`;

export type Lang='ar'|'en';
type Pair=[string,string];

export const links={
 repo,
 docs:doc('docs/README.md'),
 quickstart:doc('docs/guides/quickstart.md'),
 server:doc('docs/guides/choosing-a-server.md'),
 status:doc('docs/reference/status.md'),
 roadmap:doc('docs/engineering/plans/2026-09-23-roadmap.md'),
 architecture:doc('docs/explain/architecture.md'),
 hierarchy:doc('docs/explain/hierarchy.md'),
 isolation:doc('docs/explain/isolation-and-trust.md'),
 threat:doc('docs/explain/threat-model.md'),
 recovery:doc('docs/explain/recovery.md'),
 evidence:doc('docs/evidence/vm-empty-server-rehearsal.json'),
 contributing:doc('CONTRIBUTING.md'),
 security:doc('SECURITY.md'),
 license:doc('LICENSE'),
 issue4907:'https://github.com/orgs/supabase/discussions/4907',
 issue38048:'https://github.com/orgs/supabase/discussions/38048',
 issue39820:'https://github.com/orgs/supabase/discussions/39820',
};

// The one command the quickstart runs, identical in both languages.
export const command='sudo deploy/server-acceptance.sh --rehearse --install-unit --first-project \\\n  --service-user sbarbase --home /home/sbarbase --bun-dir /home/sbarbase/.bun/bin \\\n  --bootstrap-file /home/sbarbase/operator.json';

// Start requirements as the admission rules state them (resource_policy.start_placement):
// 1792 MiB of limits for the three system containers, 512 MiB and two containers per
// environment, and a 2560 MiB reserve for the host.
export const rules={systemContainers:3,perEnvironmentContainers:2,systemMib:1792,perEnvironmentMib:512,reserveMib:2560,maxEnvironments:4};

export const content={
 en:{
  dir:'ltr',lang:'en',
  title:'Sbarbase | Many Supabase projects on one server',
  description:'Open source and in development: run original Supabase services for many projects on one server, with a database and scoped logins per environment, and recovery designed in from the start.',
  skip:'Skip to content',menu:'Main menu',
  nav:[['Why','why'],['How it works','how'],['Hierarchy','hierarchy'],['Safety','recovery'],['Proof','proof'],['Start','start']] as Pair[],
  language:'العربية',languageUrl:'/',source:'Source',
  themeLabel:'Switch between day and night',
  mascot:'Sbarbase cactus waving from its pot',

  badge:'Open source · in development · installed on an empty server in a VM',
  headline:'Many Supabase projects.\nOne server you can trust.',
  intro:'Run the original Supabase services for all your clients on one machine: a database and its own logins for every environment, one place to manage them, and backups and restores designed in from day one.',
  ctaStart:'Install in one command',ctaDocs:'Read the docs',
  heroNotes:['Original Supabase: Auth, REST, Storage, PostgreSQL','A database and scoped logins per environment','3 containers, plus 2 per environment'],
  heroHonest:'Not production ready. Rehearsed on an empty server in a local virtual machine, not yet on a real one.',

  whyLabel:'The problem',
  whyTitle:'Self-hosted Supabase is one project per server.',
  whyText:'The Supabase team has said it plainly since 2022: the self-hosted package runs one project. More clients means more full stacks, more ports, more upgrades and more backups to remember.',
  pains:[
   ['One project per install','A discussion asking for several projects in self-hosted Studio has been open since January 2022; the answer is still one instance per project.','Discussion #4907',links.issue4907],
   ['A full stack every time','Each project repeats the database, Auth, REST, Storage, Studio and more, and a second stack on the same machine collides on ports.','Discussion #38048',links.issue38048],
   ['Backups and upgrades are yours','The recurring complaints from self-hosters are upgrades without a guide, unprotected Studio and no scheduled backups.','Discussion #39820',links.issue39820],
  ] as [string,string,string,string][],

  compareLabel:'Share the engine, keep the boundaries',
  compareTitle:'See what changes as you add environments',
  compareToday:'Today',compareOurs:'Sbarbase',
  compareSlider:'Environments',
  compareFull:'A full stack per project',
  compareFullNote:'Every part repeated, upgraded and backed up on its own.',
  compareSharedNote:'Shared parts are coral: they are shared failure boundaries too, and the page says so.',
  compareContainers:'containers Sbarbase starts',compareMemory:'MiB of memory limits, plus a 2560 MiB host reserve',
  compareCaption:'These are the admission rules, not a benchmark: limits are what the installer reserves. Idle, one environment measured about 250 MiB of container memory.',
  labels:{gateway:'gateway',engine:'one PostgreSQL engine',storage:'one Storage, a tenant per environment',db:'database',env:'environment',studio:'Studio',auth:'Auth',rest:'REST',store:'Storage',pg:'Postgres',logs:'gateway, logs',project:'project',planned:'planned'},

  howLabel:'How a request travels',
  howTitle:'Your app talks Supabase. Sbarbase routes it.',
  howText:'Nothing new for your application to learn: point supabase-js at the environment\'s address with its key. Follow one request.',
  steps:[
   ['Your app sends a request','supabase-js calls the environment\'s path with a publishable key.'],
   ['The gateway checks it','The path names the environment. Paused? Refused. Wrong key? 401. No room? Refused at once, no queue.'],
   ['That environment\'s own services','Original Auth or PostgREST, running only for this environment, receives it.'],
   ['Its own database, its own login','The service reaches only its own database, with a login that works nowhere else.'],
   ['The answer comes back','The response returns through the gateway. Other environments never saw it.'],
  ] as Pair[],
  refused:'429: this environment is busy · 503: server full or paused',
  play:'Play',pause:'Pause',replay:'Replay',seek:'Walkthrough position',step:'Step',
  diagram:{app:'your app',checks:'path, key, room',own:'env A',shared:'shared',other:'other environments',files:'files, with a trusted tenant header',login:'its own login'},

  hierLabel:'Hierarchy',
  hierTitle:'Clients own projects.\nServers only run them.',
  hierText:'A client owns projects, a project has environments such as production and staging, and the environment is the unit that is isolated, moved and restored. Ownership never depends on which server runs it.',
  levels:[
   ['Client','A client of yours, called an organization in the code. It owns projects and the people who manage them.'],
   ['Project','One application, with all its environments together.'],
   ['Environment','Production or staging: its own database, logins, Auth, REST and keys. The unit you isolate, move and restore.'],
   ['Server','Where environments run. Moving an environment to another server does not change who owns it.'],
  ] as Pair[],
  tree:{clientA:'Client A',clientB:'Client B',shop:'Shop',blog:'Blog',prod:'production',staging:'staging',server:'Your server',server2:'Second server'},
  moveAction:'Move an environment',moveBack:'Move it back',
  moveNote:'Moving between servers is planned, not built. Restoring an environment onto a separate engine on the same host is rehearsed.',

  isoLabel:'Isolation and trust',
  isoTitle:'What is yours, what is shared.',
  isoText:'Choose a part to see where its boundary is. Separate databases are not separate servers; the shared parts are named, not hidden.',
  isoOwn:'Per environment',isoShared:'Shared',
  parts:[
   ['Database','own','Each environment has its own database inside the engine.'],
   ['Service logins','own','Auth, REST and Storage reach it with logins that exist for this environment only, with exact connection rules.'],
   ['Auth and REST','own','Original Supabase processes, one set per environment. No request-time database switching.'],
   ['API keys','own','Publishable keys are issued and revoked per environment; the server keeps only their hashes.'],
   ['PostgreSQL engine','shared','One engine holds every environment\'s database: CPU, memory and failures are shared.'],
   ['Storage process','shared','One tenant-aware Storage serves every environment; a compromise of that process reaches all tenants.'],
   ['Gateway','shared','One gateway admits requests with per-environment limits, so one busy environment is refused before it starves the rest.'],
   ['Operators','shared','Whoever runs the server is trusted. Application visitors are not.'],
  ] as [string,'own'|'shared',string][],
  trustText:'Read the threat model',

  recLabel:'Recovery',
  recTitle:'A backup is a start.\nGetting back is the point.',
  recText:'Restoring one environment is rehearsed as four steps. The source is never touched until the copy is verified.',
  recSteps:[
   ['Stop writes','Put the environment in maintenance at the gateway and fence its database.'],
   ['Encrypted export','Database, files and signing keys, sealed under a new backup key.'],
   ['Restore and verify','Onto an independent engine; identity, data and files are checked.'],
   ['Switch the route','The gateway points at the verified copy. If anything failed, the source is still there.'],
  ] as Pair[],
  recLimit:'Rehearsed on one machine. Exporting today stops the shared Storage process, so every environment pauses. Scheduled, off-host backups are the next milestone.',

  proofLabel:'Proof, not promises',
  proofTitle:'We installed it on an empty server.\nIt found ten bugs.',
  proofText:'Before buying a server we simulated one: a clean Fedora 44 virtual machine with 4 cores and 6 GB, a fresh clone, one command. Every defect it found was fixed with a test.',
  proofStats:[['12 / 12','install checks on an empty server'],['13 / 13','first project: login, environment, key, supabase-js'],['reboot','the service came back on its own']] as Pair[],
  bugsTitle:'The ten defects the empty server found',
  bugs:[
   'The console was checked before it was built',
   'Disk limits named a partition, so the database never started on a usual VPS',
   'The installer asked for 8 cores for mostly idle services',
   'It asked for the full retained placement\'s memory on an empty host',
   'A failed first start was reported as something to adopt',
   'Requests without a body got one, so the console could not issue keys',
   'The console port changed at every start, which breaks HTTPS after a reboot',
   'A 1.7 GB image pull timed out silently on a slow link',
   'The runtime refused to start below a fixed 6 GiB',
   'An environment could be added that the next reboot could not start',
  ],
  proofNote:'A virtual machine, not a real server: no public network or certificate yet.',
  proofLink:'Read the evidence',

  roadLabel:'Roadmap',
  roadTitle:'Built in the open, in this order.',
  milestones:[
   ['Empty-server rehearsal','Install from nothing, first project, reboot. Ten defects fixed.','done'],
   ['A real server','The same command on a rented server, HTTPS, a week of soak.','next'],
   ['Backups as a feature','Scheduled, encrypted, off-host, restored one environment at a time, without pausing the others.','planned'],
   ['Studio per environment','The original Supabase Studio for each environment, behind your login.','planned'],
   ['Upgrades you can undo','A backup first, one environment first, a way back.','planned'],
   ['Daily operations','Invitations, an audit view and a small command for everyday tasks.','planned'],
  ] as [string,string,'done'|'next'|'planned'][],
  states:{done:'done',next:'next',planned:'planned'},

  startLabel:'Start',
  startTitle:'One command, from an empty server.',
  startText:'On a Fedora 44 or Ubuntu 26.04 server, after the preparation steps in the quickstart, one command installs the service, rehearses a start and a stop, and creates your first project.',
  needs:['4 cores (3 minimum)','8 GB memory','x86-64','Fedora 44 or Ubuntu 26.04','Python 3.14, Docker, systemd'],
  copy:'Copy',copied:'Copied',
  startLinks:[['Quickstart',links.quickstart],['Choosing a server',links.server],['What works today',links.status]] as Pair[],

  faqTitle:'Good questions, straight answers',
  faqs:[
   ['Is Sbarbase a replacement for Supabase?','No. It runs the original Supabase services and adds the layer that self-hosted Supabase lacks: many projects and environments on one server, and the operations around them.'],
   ['Can I use it in production?','Not yet. It is in development, and the install has been rehearsed on an empty server in a virtual machine only. Keep data you cannot lose elsewhere.'],
   ['How many projects fit on a server?','It depends on the server, and the installer tells you. With 4 cores and 8 GB, about four environments fit, which is also the current limit per installation while real-server measurements are pending.'],
   ['Is each environment isolated?','Each has its own database, logins, Auth, REST and keys. The PostgreSQL engine and the Storage process are shared, and so are their failures. Operators are trusted.'],
   ['Other projects share Postgres too. What is different?','Credentials scoped to each environment rather than shared across projects, provisioning that refuses to run twice after a crash, and recovery as the goal. The docs compare them openly.'],
   ['How can I help?','Run the VM rehearsal, read the roadmap, and follow the contribution workflow. Reproducible recovery and upgrade tests help most.'],
  ] as Pair[],

  ctaTitle:'Understand it. Try it.\nHelp make it better.',
  ctaText:'The code, the decisions and every experiment are in one repository.',
  cta:'Open the repository',
  footerNote:'An open, documented project by Sbarah. Not a hosted service.',
  docs:'Documentation',security:'Security',license:'Apache-2.0 license',rights:'Supabase is a trademark of Supabase, Inc. Sbarbase is not affiliated with it.',home:'sbarah.com',
 },

 ar:{
  dir:'rtl',lang:'ar',
  title:'صباربيز | مشاريع Supabase كثيرة على خادم واحد',
  description:'مشروع مفتوح المصدر قيد التطوير: خدمات Supabase الأصلية لمشاريع كثيرة على خادم واحد، مع قاعدة بيانات وحسابات دخول خاصة لكل بيئة، والاستعادة مصممة من البداية.',
  skip:'انتقل إلى المحتوى',menu:'القائمة الرئيسية',
  nav:[['لماذا','why'],['كيف يعمل','how'],['الهرمية','hierarchy'],['الأمان','recovery'],['الإثبات','proof'],['ابدأ','start']] as Pair[],
  language:'English',languageUrl:'/en/',source:'المصدر',
  themeLabel:'التبديل بين النهار والليل',
  mascot:'صبّارة صباربيز تلوّح من أصيصها',

  badge:'مفتوح المصدر · قيد التطوير · ثُبّت على خادم فارغ في آلة افتراضية',
  headline:'مشاريع Supabase كثيرة.\nوخادم واحد تثق به.',
  intro:'شغّل خدمات Supabase الأصلية لكل عملائك على جهاز واحد: قاعدة بيانات وحسابات دخول خاصة لكل بيئة، ومكان واحد لإدارتها، ونسخ واستعادة مصممان من اليوم الأول.',
  ctaStart:'ثبّته بأمر واحد',ctaDocs:'اقرأ الوثائق',
  heroNotes:['Supabase الأصلي: Auth وREST وStorage وPostgreSQL','قاعدة وحسابات دخول لكل بيئة','3 حاويات، و2 لكل بيئة'],
  heroHonest:'ليس جاهزا للإنتاج. جُرّب على خادم فارغ في آلة افتراضية محلية، ولم يُجرّب على خادم حقيقي بعد.',

  whyLabel:'المشكلة',
  whyTitle:'Supabase المستضاف ذاتيا مشروع واحد لكل خادم.',
  whyText:'فريق Supabase يقولها صراحة منذ 2022: الحزمة المستضافة ذاتيا تشغّل مشروعا واحدا. عملاء أكثر يعني نسخا كاملة أكثر، ومنافذ أكثر، وترقيات ونسخا احتياطية أكثر عليك أن تتذكرها.',
  pains:[
   ['مشروع واحد لكل تثبيت','نقاش يطلب عدة مشاريع في Studio المستضاف ذاتيا مفتوح منذ كانون الثاني 2022، والجواب ما زال: نسخة لكل مشروع.','النقاش #4907',links.issue4907],
   ['نسخة كاملة كل مرة','كل مشروع يكرر قاعدة البيانات وAuth وREST وStorage وStudio وغيرها، ونسخة ثانية على الجهاز نفسه تتصادم على المنافذ.','النقاش #38048',links.issue38048],
   ['النسخ والترقيات عليك','الشكاوى المتكررة عند من يستضيف بنفسه: ترقيات بلا دليل، وStudio بلا حماية، ولا نسخ احتياطي مجدول.','النقاش #39820',links.issue39820],
  ] as [string,string,string,string][],

  compareLabel:'شارك المحرك، واحفظ الحدود',
  compareTitle:'انظر ماذا يتغير كلما أضفت بيئة',
  compareToday:'اليوم',compareOurs:'صباربيز',
  compareSlider:'عدد البيئات',
  compareFull:'نسخة كاملة لكل مشروع',
  compareFullNote:'كل جزء مكرر، ويُحدَّث ويُنسخ وحده.',
  compareSharedNote:'الأجزاء المرجانية مشتركة: وهي أيضا نقاط فشل مشتركة، ونقول ذلك صراحة.',
  compareContainers:'حاوية يشغّلها صباربيز',compareMemory:'MiB حدود ذاكرة، فوقها احتياطي 2560 MiB للخادم',
  compareCaption:'هذه قواعد القبول لا قياس أداء: الحدود هي ما يحجزه المثبّت. في حالة الخمول قيست بيئة واحدة بنحو 250 MiB من ذاكرة الحاويات.',
  labels:{gateway:'البوابة',engine:'محرك PostgreSQL واحد',storage:'Storage واحد، مستأجر لكل بيئة',db:'قاعدة',env:'بيئة',studio:'Studio',auth:'Auth',rest:'REST',store:'Storage',pg:'Postgres',logs:'بوابة وسجلات',project:'مشروع',planned:'مخطط'},

  howLabel:'رحلة الطلب',
  howTitle:'تطبيقك يتكلم Supabase. وصباربيز يوجّهه.',
  howText:'لا شيء جديد يتعلمه تطبيقك: وجّه supabase-js إلى عنوان البيئة مع مفتاحها. تابع طلبا واحدا.',
  steps:[
   ['تطبيقك يرسل طلبا','supabase-js يطلب مسار البيئة بمفتاح عام.'],
   ['البوابة تفحصه','المسار يحدد البيئة. متوقفة؟ رفض. مفتاح خاطئ؟ 401. لا مكان؟ رفض فوري بلا طابور.'],
   ['خدمات هذه البيئة وحدها','Auth أو PostgREST الأصلي، يعمل لهذه البيئة فقط، يستقبل الطلب.'],
   ['قاعدتها وحسابها','الخدمة تصل إلى قاعدتها فقط، بحساب دخول لا يعمل في أي مكان آخر.'],
   ['الجواب يعود','الرد يرجع عبر البوابة. البيئات الأخرى لم تره أصلا.'],
  ] as Pair[],
  refused:'429: هذه البيئة مشغولة · 503: الخادم ممتلئ أو متوقف',
  play:'تشغيل',pause:'إيقاف مؤقت',replay:'إعادة',seek:'موضع الشرح',step:'خطوة',
  diagram:{app:'تطبيقك',checks:'المسار، المفتاح، المكان',own:'بيئة أ',shared:'مشترك',other:'بيئات أخرى',files:'ملفات، بهوية مستأجر موثوقة',login:'بحسابها الخاص'},

  hierLabel:'الهرمية',
  hierTitle:'العملاء يملكون المشاريع.\nوالخوادم تشغّلها فقط.',
  hierText:'العميل يملك مشاريع، والمشروع فيه بيئات مثل الإنتاج والتجربة، والبيئة هي الوحدة التي تُعزل وتُنقل وتُستعاد. الملكية لا تتعلق أبدا بالخادم الذي يشغّلها.',
  levels:[
   ['العميل','أحد عملائك، يسمّى منظمة في الكود. يملك المشاريع والأشخاص الذين يديرونها.'],
   ['المشروع','تطبيق واحد، مع كل بيئاته معا.'],
   ['البيئة','الإنتاج أو التجربة: قاعدتها وحساباتها وAuth وREST ومفاتيحها. الوحدة التي تعزلها وتنقلها وتستعيدها.'],
   ['الخادم','حيث تعمل البيئات. نقل بيئة إلى خادم آخر لا يغيّر مالكها.'],
  ] as Pair[],
  tree:{clientA:'عميل أ',clientB:'عميل ب',shop:'متجر',blog:'مدونة',prod:'إنتاج',staging:'تجربة',server:'خادمك',server2:'خادم ثان'},
  moveAction:'انقل بيئة',moveBack:'أرجعها',
  moveNote:'النقل بين الخوادم مخطط وغير مبني. استعادة بيئة إلى محرك مستقل على الخادم نفسه مجرّبة.',

  isoLabel:'العزل والثقة',
  isoTitle:'ما هو لك، وما هو مشترك.',
  isoText:'اختر جزءا لترى أين حدوده. القواعد المنفصلة ليست خوادم منفصلة؛ والأجزاء المشتركة مسمّاة لا مخفية.',
  isoOwn:'لكل بيئة',isoShared:'مشترك',
  parts:[
   ['قاعدة البيانات','own','لكل بيئة قاعدتها داخل المحرك.'],
   ['حسابات الخدمات','own','Auth وREST وStorage تصل إليها بحسابات موجودة لهذه البيئة فقط، مع قواعد اتصال دقيقة.'],
   ['Auth وREST','own','عمليات Supabase الأصلية، مجموعة لكل بيئة. لا تبديل لقاعدة البيانات أثناء الطلب.'],
   ['مفاتيح API','own','المفاتيح العامة تصدر وتُلغى لكل بيئة؛ الخادم يحفظ بصماتها فقط.'],
   ['محرك PostgreSQL','shared','محرك واحد يحمل قواعد كل البيئات: المعالج والذاكرة والأعطال مشتركة.'],
   ['عملية Storage','shared','Storage واحد يعرف المستأجرين ويخدم كل البيئات؛ اختراق هذه العملية يصل إلى الجميع.'],
   ['البوابة','shared','بوابة واحدة تقبل الطلبات بحدود لكل بيئة، فترفض البيئة المزدحمة قبل أن تجوّع غيرها.'],
   ['المشغّلون','shared','من يدير الخادم موثوق. زوار التطبيقات ليسوا كذلك.'],
  ] as [string,'own'|'shared',string][],
  trustText:'اقرأ نموذج التهديد',

  recLabel:'الاستعادة',
  recTitle:'النسخة الاحتياطية بداية.\nالرجوع هو المقصود.',
  recText:'استعادة بيئة واحدة مجرّبة في أربع خطوات. المصدر لا يُمسّ حتى تتأكد النسخة.',
  recSteps:[
   ['أوقف الكتابة','ضع البيئة في وضع الصيانة عند البوابة وسيّج قاعدتها.'],
   ['صدّر مشفّرا','القاعدة والملفات ومفاتيح التوقيع، مختومة بمفتاح نسخ جديد.'],
   ['استعد وتحقق','على محرك مستقل؛ تُفحص الهوية والبيانات والملفات.'],
   ['حوّل المسار','البوابة تشير إلى النسخة المتحقق منها. إن فشل شيء، المصدر ما زال موجودا.'],
  ] as Pair[],
  recLimit:'جُرّب على جهاز واحد. التصدير اليوم يوقف عملية Storage المشتركة، فتتوقف كل البيئات مؤقتا. النسخ المجدول خارج الخادم هو المرحلة القادمة.',

  proofLabel:'إثبات لا وعود',
  proofTitle:'ثبّتناه على خادم فارغ.\nفوجد عشرة عيوب.',
  proofText:'قبل شراء خادم حاكينا واحدا: آلة افتراضية نظيفة بنظام Fedora 44 وأربع أنوية و6 GB، ونسخة جديدة من الكود، وأمر واحد. كل عيب ظهر أُصلح مع اختبار.',
  proofStats:[['12 / 12','فحص تثبيت على خادم فارغ'],['13 / 13','أول مشروع: دخول، بيئة، مفتاح، supabase-js'],['إعادة تشغيل','الخدمة رجعت وحدها']] as Pair[],
  bugsTitle:'العيوب العشرة التي كشفها الخادم الفارغ',
  bugs:[
   'الواجهة كانت تُفحص قبل أن تُبنى',
   'حدود القرص كانت على القسم، فلا تبدأ القاعدة على أي VPS عادي',
   'المثبّت كان يطلب 8 أنوية لخدمات خاملة غالبا',
   'كان يطلب ذاكرة التثبيت الكامل القديم على خادم فارغ',
   'التشغيل الأول الفاشل كان يُعرض كشيء يجب تبنّيه',
   'الطلبات بلا جسم كانت تكتسب جسما، فلا تصدر الواجهة مفاتيح',
   'منفذ الواجهة يتغير عند كل تشغيل، فيتعطل HTTPS بعد إعادة التشغيل',
   'تنزيل صورة 1.7 GB كان ينقطع بصمت على اتصال بطيء',
   'التشغيل كان يرفض تحت 6 GiB ثابتة',
   'كان ممكنا إضافة بيئة لا يستطيع الخادم تشغيلها بعد إعادة الإقلاع',
  ],
  proofNote:'آلة افتراضية لا خادم حقيقي: لا شبكة عامة ولا شهادة بعد.',
  proofLink:'اقرأ الأدلة',

  roadLabel:'خارطة الطريق',
  roadTitle:'نبنيه علنا، وبهذا الترتيب.',
  milestones:[
   ['تجربة الخادم الفارغ','تثبيت من لا شيء، أول مشروع، إعادة تشغيل. عشرة عيوب أُصلحت.','done'],
   ['خادم حقيقي','الأمر نفسه على خادم مستأجر، مع HTTPS وأسبوع تشغيل متواصل.','next'],
   ['النسخ الاحتياطي كميزة','مجدول ومشفر وخارج الخادم، وتُستعاد كل بيئة وحدها دون إيقاف غيرها.','planned'],
   ['Studio لكل بيئة','Supabase Studio الأصلي لكل بيئة، خلف تسجيل دخولك.','planned'],
   ['ترقيات يمكن التراجع عنها','نسخة أولا، وبيئة واحدة أولا، وطريق للرجوع.','planned'],
   ['التشغيل اليومي','دعوات، وسجل تدقيق، وأمر صغير للمهام اليومية.','planned'],
  ] as [string,string,'done'|'next'|'planned'][],
  states:{done:'تم',next:'التالي',planned:'مخطط'},

  startLabel:'ابدأ',
  startTitle:'أمر واحد، من خادم فارغ.',
  startText:'على خادم Fedora 44 أو Ubuntu 26.04، بعد خطوات التحضير في دليل البدء، أمر واحد يثبّت الخدمة ويجرّب تشغيلها وإيقافها وينشئ مشروعك الأول.',
  needs:['4 أنوية (3 كحد أدنى)','ذاكرة 8 GB','x86-64','Fedora 44 أو Ubuntu 26.04','Python 3.14 وDocker وsystemd'],
  copy:'نسخ',copied:'نُسخ',
  startLinks:[['دليل البدء السريع',links.quickstart],['اختيار الخادم',links.server],['ما يعمل اليوم',links.status]] as Pair[],

  faqTitle:'أسئلة جيدة، وأجوبة صريحة',
  faqs:[
   ['هل صباربيز بديل عن Supabase؟','لا. يشغّل خدمات Supabase الأصلية ويضيف الطبقة التي تنقص Supabase المستضاف ذاتيا: مشاريع وبيئات كثيرة على خادم واحد، والعمليات حولها.'],
   ['هل أستطيع استخدامه في الإنتاج؟','ليس بعد. هو قيد التطوير، والتثبيت جُرّب على خادم فارغ في آلة افتراضية فقط. احفظ البيانات التي لا تحتمل ضياعها في مكان آخر.'],
   ['كم مشروعا يتسع الخادم؟','يعتمد على الخادم، والمثبّت يخبرك. مع 4 أنوية و8 GB تتسع نحو أربع بيئات، وهو أيضا الحد الحالي لكل تثبيت حتى نقيس على خادم حقيقي.'],
   ['هل كل بيئة معزولة؟','لكل بيئة قاعدتها وحساباتها وAuth وREST ومفاتيحها. محرك PostgreSQL وعملية Storage مشتركان، وكذلك أعطالهما. المشغّلون موثوقون.'],
   ['مشاريع أخرى تشارك Postgres أيضا. ما الفرق؟','حسابات خاصة بكل بيئة بدل حسابات مشتركة بين المشاريع، وتجهيز يرفض أن يعمل مرتين بعد انهيار، والاستعادة هي الهدف. الوثائق تقارنها بوضوح.'],
   ['كيف أساعد؟','شغّل تجربة الآلة الافتراضية، واقرأ خارطة الطريق، واتبع سير عمل المساهمة. اختبارات الاستعادة والترقية القابلة للتكرار هي الأنفع.'],
  ] as Pair[],

  ctaTitle:'افهمه. جرّبه.\nوساعد في تحسينه.',
  ctaText:'الكود والقرارات وكل تجربة في مستودع واحد.',
  cta:'افتح المستودع',
  footerNote:'مشروع مفتوح وموثق من صبّارة. ليس خدمة مستضافة.',
  docs:'الوثائق',security:'الأمان',license:'رخصة Apache-2.0',rights:'Supabase علامة تجارية لشركة Supabase, Inc. وصباربيز غير تابع لها.',home:'sbarah.com',
 },
};
