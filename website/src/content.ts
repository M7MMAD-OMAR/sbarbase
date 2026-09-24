// Copy for the Arabic (/) and English (/en/) pages. Kept deliberately short: the
// page shows the idea, the hierarchy, one request and the install; the docs carry
// the detail. No long dashes anywhere; the test refuses them.

const repo='https://github.com/M7MMAD-OMAR/sbarbase';
const doc=(path:string)=>`${repo}/blob/main/${path}`;

export type Lang='ar'|'en';

export const links={
 repo,
 docs:doc('docs/README.md'),
 quickstart:doc('docs/guides/quickstart.md'),
 server:doc('docs/guides/choosing-a-server.md'),
 security:doc('SECURITY.md'),
 license:doc('LICENSE'),
};

// The install, exactly as docs/guides/quickstart.md and lab/vm-rehearsal.sh run
// it. Identical in both languages; the copy buttons copy these strings verbatim.
export const commands=[
 'sudo dnf install -y moby-engine git python3-cryptography && sudo systemctl enable --now docker',
 'sudo useradd -m sbarbase && sudo usermod -aG docker sbarbase && sudo install -d -o sbarbase -g sbarbase /opt/sbarbase && sudo -u sbarbase git clone https://github.com/M7MMAD-OMAR/sbarbase /opt/sbarbase',
 "sudo -u sbarbase -H bash -c 'curl -fsSL https://bun.sh/install | bash -s bun-v1.3.14'",
 'cd /opt/sbarbase && sudo -u sbarbase /usr/bin/python3 lab/operator_file.py /home/sbarbase/operator.json',
 'sudo deploy/server-acceptance.sh --rehearse --install-unit --first-project --service-user sbarbase --home /home/sbarbase --bun-dir /home/sbarbase/.bun/bin --bootstrap-file /home/sbarbase/operator.json',
] as const;

export const content={
 en:{
  dir:'ltr',lang:'en',
  title:'Sbarbase | Many Supabase projects on one server',
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
   ['Prepare the server','Fedora 44. On Ubuntu 26.04, use docker.io.'],
   ['Create the user and clone',''],
   ['Install Bun',''],
   ['Write the operator file','Asks for email, organization and password.'],
   ['Install and create a project',''],
  ] as [string,string][],
  needs:['4 cores','8 GB','x86-64','Fedora 44 or Ubuntu 26.04'],
  needsLabel:'Server',
  copy:'Copy',copied:'Copied',copyLabel:'Copy step',
  installLinks:{quickstart:'Quickstart',docs:'Docs',server:'Choosing a server',repo:'GitHub'},

  docs:'Docs',security:'Security',license:'License',home:'sbarah.com',
  trademark:'Supabase is a trademark of Supabase, Inc. Sbarbase is not affiliated.',
 },
 ar:{
  dir:'rtl',lang:'ar',
  title:'صباربيز | مشاريع Supabase كثيرة على خادم واحد',
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
   ['جهّز الخادم','على Fedora 44. أما على Ubuntu 26.04 فاستخدم docker.io'],
   ['أنشئ المستخدم وانسخ المستودع',''],
   ['ثبّت Bun',''],
   ['اكتب ملف المشغّل','يسأل عن البريد والمؤسسة وكلمة المرور.'],
   ['ثبّت وأنشئ مشروعا',''],
  ] as [string,string][],
  needs:['4 أنوية','8 GB','x86-64','Fedora 44 أو Ubuntu 26.04'],
  needsLabel:'الخادم',
  copy:'نسخ',copied:'نُسخ',copyLabel:'نسخ الخطوة',
  installLinks:{quickstart:'البدء السريع',docs:'التوثيق',server:'اختيار الخادم',repo:'GitHub'},

  docs:'التوثيق',security:'الأمان',license:'الرخصة',home:'sbarah.com',
  trademark:'Supabase علامة تجارية لشركة \u2066Supabase, Inc.\u2069، وصباربيز غير تابع لها.',
 },
};
