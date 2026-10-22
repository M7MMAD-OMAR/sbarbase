// Copy for the Arabic (/) and English (/en/) pages. Kept deliberately short: the
// page shows the idea, the hierarchy, one request and the install; the docs carry
// the detail. No long dashes anywhere; the test refuses them.

export const repo='https://github.com/M7MMAD-OMAR/sbarbase';
const github=(path:string)=>`${repo}/blob/main/${path}`;

export type Lang='ar'|'en';

// A page of the rendered docs: Arabic under /docs/, English under /en/docs/. slug is
// the path under docs/ without the extension, and '' for the docs map.
export const docPage=(lang:Lang,slug:string)=>`${lang==='ar'?'':'/en'}/docs/${slug?`${slug}/`:''}`;

// The docs live on this site; security policy and licence stay on GitHub.
export const links=(lang:Lang)=>({
 repo,
 docs:docPage(lang,''),
 docker:docPage(lang,'guides/docker'),
 quickstart:docPage(lang,'guides/quickstart'),
 server:docPage(lang,'guides/choosing-a-server'),
 changelog:docPage(lang,'changelog'),
 security:github('SECURITY.md'),
 license:github('LICENSE'),
});

// The install, exactly as docs/guides/docker.md gives it and lab/vm-docker-rehearsal.sh
// ran it on a bare Ubuntu 26.04 machine. Identical in both languages; the copy
// buttons copy these strings verbatim. Each one works pasted into a fresh shell.
export const commands=[
 'sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 docker-buildx git && sudo systemctl enable --now docker',
 'sudo git clone https://github.com/M7MMAD-OMAR/sbarbase /opt/sbarbase',
 'cd /opt/sbarbase && sudo deploy/compose.sh check',
 'cd /opt/sbarbase && sudo deploy/compose.sh up',
 'cd /opt/sbarbase && sudo deploy/compose.sh bootstrap',
] as const;

export const content={
 en:{
  dir:'ltr',lang:'en',
  title:'Sbarbase | Self-hosted Supabase for many projects on one server',
  description:'Run the original Supabase services for many projects on one server. Every environment gets its own database, logins, Auth, REST and keys.',
  skip:'Skip to content',menu:'Main menu',
  nav:[['Hierarchy','hierarchy'],['How it works','how'],['Install','install']] as [string,string][],
  language:'العربية',languageUrl:'/',source:'Source code on GitHub',
  themeLabel:'Switch between day and night',

  badge:'Open source · in development',
  headline:'Many Supabase projects. One server.',
  intro:'Original Supabase for every client, on one machine you own.',
  ctaInstall:'Install',ctaDocs:'Docs',
  videoLabel:'Explainer video',
  videoCaption:'The idea in one short video.',

  hierLabel:'Hierarchy',
  hierTitle:'Clients, projects, environments.',
  hierText:'Each environment has its own database, logins, Auth, REST and keys.',
  tree:{client:'client',project:'project',env:'environment',clientA:'Client A',clientB:'Client B',shop:'Shop',blog:'Blog',prod:'production',staging:'staging',server:'Your server',engine:'one PostgreSQL engine',db:'database'},
  hierAlt:'Client A owns the Shop project with production and staging environments. Client B owns the Blog project with a production environment. On your server, each environment has its own database inside one PostgreSQL engine.',

  howLabel:'How it works',
  howTitle:'One request, start to finish.',
  steps:[
   'Your app calls its environment with a key.',
   'The gateway checks path, key and room.',
   'Its own Auth or REST takes it.',
   'It reaches only its own database.',
   'The answer returns, unseen by others.',
  ],
  legend:{shared:'shared',own:'per environment',db:'database'},
  labels:{gateway:'gateway',db:'database',auth:'Auth',rest:'REST',store:'Storage'},
  diagram:{app:'your app',checks:'path, key, room',own:'env A',shared:'shared by all',other:'others',login:'its own login',refused:'busy or paused: refused'},
  play:'Play',pause:'Pause',replay:'Replay',seek:'Walkthrough position',

  installLabel:'Install',
  installTitle:'From an empty server.',
  installSteps:[
   ['Install Docker','Ubuntu 26.04 packages. Nothing else on the host.'],
   ['Get the code',''],
   ['Check the server','Changes nothing. Says what to fix.'],
   ['Start','Pulls about 2.4 GB. Wait until sudo deploy/compose.sh logs shows Local Sbarbase API.'],
   ['Create the first operator','Asks for email, organization and password.'],
  ] as [string,string][],
  needs:['4 cores','8 GB','x86-64','Ubuntu 26.04'],
  needsLabel:'Server',
  copy:'Copy',copied:'Copied',copyLabel:'Copy step',
  installLinks:{docker:'Docker guide',quickstart:'Without Docker',docs:'Docs',server:'Choosing a server',repo:'GitHub'},

  docs:'Docs',security:'Security',license:'License',home:'sbarah.com',support:'Support Sbarah',
  trademark:'Supabase is a trademark of Supabase, Inc. Sbarbase is not affiliated.',
  docsTitle:'Sbarbase docs',docsNav:'Documentation',contents:'Contents',overview:'Overview',changelog:'Changelog',
  edit:'Edit on GitHub',onGithub:'on GitHub',
 },
 ar:{
  dir:'rtl',lang:'ar',
  title:'Sbarbase صباربيز | استضافة Supabase ذاتيًا لمشاريع كثيرة على خادم واحد',
  description:'شغّل خدمات Supabase الأصلية لمشاريع كثيرة على خادم واحد. لكل بيئة قاعدتها وحساباتها وAuth وREST ومفاتيحها.',
  skip:'انتقل إلى المحتوى',menu:'القائمة الرئيسية',
  nav:[['الهرمية','hierarchy'],['كيف يعمل','how'],['التثبيت','install']] as [string,string][],
  language:'English',languageUrl:'/en/',source:'الشيفرة على GitHub',
  themeLabel:'التبديل بين النهار والليل',

  badge:'مفتوح المصدر · قيد التطوير',
  headline:'مشاريع Supabase كثيرة. خادم واحد.',
  intro:'Supabase الأصلي لكل عميل، وكل بيئة معزولة عن غيرها، على خادم تملكه.',
  ctaInstall:'التثبيت',ctaDocs:'التوثيق',
  videoLabel:'فيديو تعريفي',
  videoCaption:'الفكرة في فيديو قصير.',

  hierLabel:'الهرمية',
  hierTitle:'عملاء، مشاريع، بيئات.',
  hierText:'لكل بيئة قاعدتها وحساباتها وAuth وREST ومفاتيحها.',
  tree:{client:'عميل',project:'مشروع',env:'بيئة',clientA:'العميل أ',clientB:'العميل ب',shop:'المتجر',blog:'المدونة',prod:'الإنتاج',staging:'التجربة',server:'خادمك',engine:'محرك PostgreSQL واحد',db:'قاعدة'},
  hierAlt:'العميل أ يملك مشروع المتجر وله بيئتا الإنتاج والتجربة. العميل ب يملك مشروع المدونة وله بيئة الإنتاج. على خادمك، لكل بيئة قاعدتها داخل محرك PostgreSQL واحد.',

  howLabel:'كيف يعمل',
  howTitle:'طلب واحد، من البداية إلى النهاية.',
  steps:[
   'تطبيقك يطلب بيئته بمفتاحها.',
   'البوابة تفحص المسار والمفتاح والسعة.',
   'Auth أو REST الخاص بهذه البيئة يستقبله.',
   'لا يصل إلا إلى قاعدته.',
   'الجواب يعود، ولا تراه أي بيئة أخرى.',
  ],
  legend:{shared:'مشترك',own:'لكل بيئة',db:'قاعدة بيانات'},
  labels:{gateway:'البوابة',db:'قاعدة',auth:'Auth',rest:'REST',store:'Storage'},
  diagram:{app:'تطبيقك',checks:'المسار، المفتاح، السعة',own:'بيئة أ',shared:'مشترك بين البيئات',other:'بيئات أخرى',login:'بحسابها الخاص',refused:'مشغولة أو متوقفة: رفض'},
  play:'تشغيل',pause:'إيقاف مؤقت',replay:'إعادة',seek:'موضع الشرح',

  installLabel:'التثبيت',
  installTitle:'من خادم فارغ.',
  installSteps:[
   ['ثبّت Docker','حزم Ubuntu 26.04، ولا شيء غيرها على الخادم.'],
   ['انسخ المستودع',''],
   ['افحص الخادم','لا يغيّر شيئا، ويخبرك بما يلزم إصلاحه.'],
   ['شغّل','يسحب نحو 2.4 GB. انتظر حتى يظهر Local Sbarbase API في ناتج sudo deploy/compose.sh logs'],
   ['أنشئ أول مشغّل','يسأل عن البريد والمؤسسة وكلمة المرور.'],
  ] as [string,string][],
  needs:['4 أنوية','8 GB','x86-64','Ubuntu 26.04'],
  needsLabel:'الخادم',
  copy:'نسخ',copied:'نُسخ',copyLabel:'نسخ الخطوة',
  installLinks:{docker:'دليل Docker',quickstart:'من دون Docker',docs:'التوثيق',server:'اختيار الخادم',repo:'GitHub'},

  docs:'التوثيق',security:'الأمان',license:'الرخصة',home:'sbarah.com',support:'ادعم صبارة',
  trademark:'Supabase علامة تجارية لشركة \u2066Supabase, Inc.\u2069، وصباربيز غير تابع لها.',
  docsTitle:'توثيق صباربيز',docsNav:'التوثيق',contents:'المحتويات',overview:'نظرة عامة',changelog:'سجل التغييرات',
  edit:'عدّل على GitHub',onGithub:'على GitHub',
 },
};
