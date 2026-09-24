"""Write the documentation diagrams as SVG, in English and in Arabic.

Run with /usr/bin/python3 docs/diagrams/build.py. Each diagram is drawn once with
coordinates written left to right; the Arabic variant (name.ar.svg) mirrors every
x coordinate and sets direction="rtl", so it reads in the reader's direction.
Colours follow the website: paper, ink, coral for shared parts, teal for parts
that exist once per environment, mustard for an environment's database, and a
dashed lilac outline for something planned and not built. Every file carries a
solid background, a <title> and a <desc>, and uses only system fonts.
"""
import re
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent

PAPER, INK, MUTED = '#F5EFE3', '#2A2632', '#5B5465'
CORAL, TEAL, MUSTARD, LILAC = '#E06A4B', '#1F8A78', '#F0C23E', '#B7A2E6'
CORAL_INK, TEAL_INK = '#A8412A', '#146B5C'
FONT = 'system-ui, sans-serif'
MONO = 'ui-monospace, Menlo, Consolas, monospace'

# fill, stroke, stroke width, dash
KINDS = {
    'card': ('#FFFBF1', INK, 1.4, None),
    'shared': ('#F6C7B6', CORAL, 2, None),
    'env': ('#BFE3D8', TEAL, 1.8, None),
    'db': (MUSTARD, INK, 1.3, None),
    'other': ('#EAE2D2', MUTED, 1.2, '4 4'),
    'record': ('#FBE7A8', INK, 1.3, None),
    'warn': ('#F9D9CF', CORAL, 1.8, None),
    'borrowed': ('#FDEDE6', CORAL, 1.2, '3 2'),
    'planned': ('#EFE9FA', LILAC, 2, '7 5'),
    'zone': ('none', INK, 1.3, '7 5'),
}
ARABIC = re.compile('[؀-ۿ]')


class Diagram:
    def __init__(self, name, width, height, lang, title, desc):
        self.name, self.w, self.h, self.lang = name, width, height, lang
        self.rtl = lang == 'ar'
        self.title, self.desc = title, desc
        self.parts = []
        self.id = name.replace('-', '')[:6] + ('a' if self.rtl else 'e')

    def x(self, x, w=0):
        return round(self.w - x - w, 1) if self.rtl else x

    def box(self, x, y, w, h, kind='card', rx=8):
        fill, stroke, sw, dash = KINDS[kind]
        extra = f' stroke-dasharray="{dash}"' if dash else ''
        self.parts.append(f'<rect x="{self.x(x, w)}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
                          f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{extra}/>')

    def text(self, x, y, s, size=13, weight=None, anchor='middle', fill=INK, mono=False):
        attrs = [f'x="{self.x(x)}"', f'y="{y}"', f'font-size="{size}"']
        if weight:
            attrs.append(f'font-weight="{weight}"')
        if fill != INK:
            attrs.append(f'fill="{fill}"')
        if mono:
            attrs.append(f'font-family="{MONO}"')
        if self.rtl and not ARABIC.search(s):
            # A Latin-only label keeps left-to-right order; its anchor flips instead.
            attrs.append('direction="ltr"')
            anchor = {'start': 'end', 'end': 'start'}.get(anchor, anchor)
        if anchor != 'middle':
            attrs.append(f'text-anchor="{anchor}"')
        self.parts.append(f'<text {" ".join(attrs)}>{escape(s)}</text>')

    def lines(self, x, y, items, size=13, gap=None, **kw):
        gap = gap or round(size * 1.35)
        for i, s in enumerate(items):
            if s:
                self.text(x, y + i * gap, s, size=size, **kw)

    def arrow(self, d, color=INK, dash=None, head=True, width=1.6):
        d = re.sub(r'(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)', lambda m: f'{self.x(float(m[1])):g} {m[2]}', d)
        marker = f' marker-end="url(#{self.id}-{"c" if color == CORAL else "i"})"' if head else ''
        extra = f' stroke-dasharray="{dash}"' if dash else ''
        self.parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
                          f'stroke-linecap="round" stroke-linejoin="round"{extra}{marker}/>')

    def badge(self, x, y, n):
        self.parts.append(f'<circle cx="{self.x(x)}" cy="{y}" r="11" fill="{INK}"/>')
        self.parts.append(f'<text x="{self.x(x)}" y="{y + 4.5}" font-size="12.5" font-weight="700" fill="#FFFBF1">{n}</text>')

    def legend(self, y, items):
        x = 24
        for kind, label in items:
            self.box(x, y - 11, 22, 14, kind, rx=3)
            self.text(x + 30, y, label, size=12, anchor='start', fill=MUTED)
            x += 30 + 6.4 * len(label) + 28

    def svg(self):
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" '
                f'height="{self.h}" role="img" aria-labelledby="{self.id}-t {self.id}-d"'
                + (' xml:lang="ar" direction="rtl"' if self.rtl else ' xml:lang="en"') + '>')
        defs = ''.join(
            f'<marker id="{self.id}-{k}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M1 1 L9 5 L1 9" fill="none" stroke="{c}" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round"/></marker>' for k, c in (('i', INK), ('c', CORAL)))
        body = '\n  '.join(self.parts)
        return (f'{head}\n  <title id="{self.id}-t">{escape(self.title)}</title>\n'
                f'  <desc id="{self.id}-d">{escape(self.desc)}</desc>\n  <defs>{defs}</defs>\n'
                f'  <rect x="0" y="0" width="{self.w}" height="{self.h}" fill="{PAPER}"/>\n'
                f'  <g font-family="{FONT}" fill="{INK}" text-anchor="middle">\n  {body}\n  </g>\n</svg>\n')


def pick(lang, en, ar):
    return ar if lang == 'ar' else en


def hierarchy(lang):
    t = lambda en, ar: pick(lang, en, ar)
    d = Diagram('hierarchy', 900, 470, lang,
                t('Clients, projects and environments on one server', 'العملاء والمشاريع والبيئات على خادم واحد'),
                t('Left, the ownership tree in the catalog: Client A owns the Shop project with production and staging '
                  'environments, and Client B owns the Blog project with a production environment. Right, where they run: '
                  'on your server one shared PostgreSQL engine holds a separate database for each environment. A routing '
                  'record links each environment to its placement, so moving an environment changes only that record; '
                  'its owner, identifiers and keys stay the same. All of it runs on one ordinary server.',
                  'على اليسار شجرة الملكية في الفهرس: العميل أ يملك مشروع المتجر وله بيئتا الإنتاج والتجربة، والعميل ب '
                  'يملك مشروع المدونة وله بيئة الإنتاج. وعلى اليمين مكان التشغيل: على خادمك محرك PostgreSQL مشترك واحد '
                  'فيه قاعدة بيانات منفصلة لكل بيئة. سجل التوجيه يربط كل بيئة بمكان تشغيلها، فنقل البيئة يغيّر هذا السجل '
                  'وحده، ويبقى مالكها ومعرّفاتها ومفاتيحها كما هي. ويعمل كل ذلك على خادم عادي واحد.'))
    d.text(24, 34, d.title, size=16, weight=700, anchor='start')
    d.text(24, 72, t('Who owns it (catalog)', 'من يملكها (الفهرس)'), size=12.5, weight=600, anchor='start', fill=MUTED)
    d.text(496, 72, t('Where it runs (routing records)', 'أين تعمل (سجلات التوجيه)'), size=12.5, weight=600, anchor='start', fill=MUTED)
    # ownership tree
    for y, client, project in ((110, t('Client A', 'العميل أ'), t('project: Shop', 'مشروع: المتجر')),
                               (250, t('Client B', 'العميل ب'), t('project: Blog', 'مشروع: المدونة'))):
        d.box(24, y, 124, 44)
        d.text(86, y + 27, client, weight=700)
        d.box(178, y, 132, 44)
        d.text(244, y + 27, project)
        d.arrow(f'M148 {y + 22} L178 {y + 22}', head=False)
    envs = ((86, t('production', 'الإنتاج')), (138, t('staging', 'التجربة')), (252, t('production', 'الإنتاج')))
    for y, label in envs:
        d.box(340, y, 124, 40, 'env')
        d.text(402, y + 25, label)
    d.arrow('M310 132 C325 132 325 106 340 106', head=False)
    d.arrow('M310 132 C325 132 325 158 340 158', head=False)
    d.arrow('M310 272 L340 272', head=False)
    d.lines(24, 346, [t('The environment is the unit of isolation,', 'البيئة هي وحدة العزل والنسخ الاحتياطي'),
                      t('backup, move and restore.', 'والنقل والاستعادة.')], size=12.5, anchor='start')
    d.lines(24, 384, [t('In the code: client = organization,', 'في الكود: العميل هو organization،'),
                      t('your server = installation.', 'وخادمك هو installation.')], size=12, anchor='start', fill=MUTED)
    # placement
    d.box(496, 86, 380, 236, 'zone')
    d.text(512, 108, t('Your server', 'خادمك'), weight=700, anchor='start')
    d.box(512, 120, 348, 186, 'shared')
    d.text(686, 142, t('one shared PostgreSQL engine', 'محرك PostgreSQL مشترك واحد'), weight=700)
    dbs = (t('database: Shop, production', 'قاعدة: المتجر، الإنتاج'), t('database: Shop, staging', 'قاعدة: المتجر، التجربة'),
           t('database: Blog, production', 'قاعدة: المدونة، الإنتاج'))
    for i, label in enumerate(dbs):
        y = 156 + i * 48
        d.box(532, y, 308, 38, 'db')
        d.text(686, y + 24, label)
    for (y, _), target in zip(envs, (175, 223, 271)):
        d.arrow(f'M464 {y + 20} C498 {y + 20} 498 {target} 530 {target}', dash='4 4')
    d.text(686, 352, t('one ordinary server; no second server needed', 'خادم عادي واحد، دون حاجة إلى خادم ثانٍ'),
           size=12.5, weight=600)
    d.text(24, 426, t('Moving an environment changes only its routing record; its owner, identifiers and keys stay the same.',
                      'نقل بيئة يغيّر سجل توجيهها وحده؛ ويبقى مالكها ومعرّفاتها ومفاتيحها كما هي.'),
           size=12.5, anchor='start')
    d.legend(456, [('env', t('one per environment', 'واحدة لكل بيئة')), ('shared', t('shared', 'مشترك')),
                   ('db', t('environment database', 'قاعدة البيئة'))])
    return d


def request_path(lang):
    t = lambda en, ar: pick(lang, en, ar)
    d = Diagram('request-path', 900, 470, lang,
                t('One request, from app to database', 'طلب واحد، من التطبيق إلى قاعدة البيانات'),
                t('An app using supabase-js sends a request with its publishable key over HTTPS to the TLS proxy, then to '
                  'the gateway, on a path that names one environment. The gateway refuses if the environment is in '
                  'maintenance, checks the key against that environment, admits the request only if the environment is '
                  'under its concurrent limit, and caps the body at 1 MiB. There is no queue: it refuses at once with 429 '
                  'when the environment is full and 503 when the gateway is full or the environment paused. Admitted '
                  'requests reach that environment\'s own Auth or REST, which reach only its own database through its own '
                  'login inside the shared PostgreSQL engine. File requests go to the shared Storage process with a '
                  'tenant header.',
                  'يرسل تطبيق يستخدم supabase-js طلبًا بمفتاحه العام عبر HTTPS إلى وكيل TLS ثم إلى البوابة، على مسار '
                  'يسمّي بيئة واحدة. ترفض البوابة إن كانت البيئة في الصيانة، وتتحقق من المفتاح لتلك البيئة، ولا تقبل '
                  'الطلب إلا إن كانت البيئة تحت حدها من الطلبات المتزامنة، وتحد حجم الطلب بـ 1 MiB. لا طابور: ترفض فورًا '
                  'بـ 429 إن امتلأت البيئة، وبـ 503 إن امتلأت البوابة أو توقفت البيئة. يصل الطلب المقبول إلى Auth أو REST '
                  'الخاصين بتلك البيئة، ولا يصلان إلا إلى قاعدتها بحساب دخولها داخل محرك PostgreSQL المشترك. وتذهب '
                  'طلبات الملفات إلى عملية Storage المشتركة مع ترويسة المستأجر.'))
    d.text(24, 34, d.title, size=16, weight=700, anchor='start')
    d.box(24, 150, 124, 66)
    d.text(86, 178, t('your app', 'تطبيقك'), weight=700)
    d.text(86, 200, 'supabase-js', size=12, mono=True)
    d.text(86, 240, t('request and', 'طلب مع'), size=12, fill=MUTED)
    d.text(86, 256, t('publishable key', 'المفتاح العام'), size=12, fill=MUTED)
    d.box(174, 150, 104, 66)
    d.text(226, 178, t('TLS proxy', 'وكيل TLS'), weight=700)
    d.text(226, 200, 'HTTPS', size=12)
    d.box(304, 64, 244, 236, 'shared')
    d.text(426, 90, t('gateway', 'البوابة'), size=15, weight=700)
    d.lines(320, 120, [t('1. path names the environment', '1. المسار يحدد البيئة'),
                       t('2. in maintenance? refuse', '2. في الصيانة؟ رفض'),
                       t('3. key valid for it? else 401', '3. المفتاح صالح لها؟ وإلا 401'),
                       t('4. room under its limit?', '4. هل تتسع لطلب آخر؟'),
                       t('5. body up to 1 MiB', '5. حجم الطلب حتى 1 MiB')], size=13, gap=25, anchor='start')
    d.text(426, 280, t('no queue: refuse at once', 'لا طابور: الرفض فوري'), weight=700, fill=CORAL_INK)
    d.box(304, 332, 244, 70, 'warn')
    d.text(426, 360, t('429: environment at its limit', '429: البيئة بلغت حدها'), size=12.5)
    d.text(426, 384, t('503: gateway full, or paused', '503: البوابة ممتلئة أو البيئة متوقفة'), size=12.5)
    d.arrow('M426 300 L426 328', color=CORAL)
    d.box(584, 80, 150, 42, 'env')
    d.text(659, 106, t('Auth, env A', 'Auth للبيئة أ'))
    d.box(584, 138, 150, 42, 'env')
    d.text(659, 164, t('REST, env A', 'REST للبيئة أ'))
    d.box(584, 222, 150, 64, 'shared')
    d.text(659, 248, 'Storage', weight=700)
    d.text(659, 268, t('shared, tenant header', 'مشترك، بترويسة المستأجر'), size=11.5)
    d.box(760, 50, 124, 352, 'shared')
    d.text(822, 76, 'PostgreSQL', weight=700)
    d.text(822, 94, t('shared engine', 'محرك مشترك'), size=12)
    d.box(772, 112, 100, 90, 'db')
    d.lines(822, 138, [t('database', 'قاعدة'), t('env A', 'البيئة أ'), t('own logins', 'بحساباتها')], size=12.5, gap=20)
    d.box(772, 222, 100, 40, 'other')
    d.text(822, 247, t('env B', 'البيئة ب'), size=12.5, fill=MUTED)
    d.box(772, 276, 100, 40, 'other')
    d.text(822, 301, t('env C', 'البيئة ج'), size=12.5, fill=MUTED)
    d.lines(822, 350, [t('one login,', 'حساب واحد'), t('one database', 'لقاعدة واحدة')], size=12, gap=17)
    d.arrow('M148 183 L172 183')
    d.arrow('M278 183 L302 183')
    d.arrow('M548 110 C566 110 566 101 582 101')
    d.arrow('M548 150 C566 150 566 159 582 159')
    d.arrow('M548 240 C566 240 566 254 582 254')
    d.arrow('M734 101 C752 101 752 140 770 140')
    d.arrow('M734 159 C752 159 752 160 770 160')
    d.arrow('M734 254 C752 254 752 186 770 186', dash='4 4')
    d.legend(446, [('shared', t('shared by every environment', 'مشترك بين كل البيئات')),
                   ('env', t('one per environment (env A shown)', 'واحد لكل بيئة (البيئة أ ظاهرة)')),
                   ('db', t('environment database', 'قاعدة البيئة'))])
    return d


def full_stack(lang):
    t = lambda en, ar: pick(lang, en, ar)
    d = Diagram('full-stack-vs-shared', 900, 494, lang,
                t('A full stack per project, compared with Sbarbase', 'حزمة كاملة لكل مشروع، مقارنة بصباربيز'),
                t('Left: three projects, each running its own full stack of Studio, Auth, REST, Storage, Postgres, '
                  'gateway and logs. Right: Sbarbase runs one gateway, a separate Auth and REST per environment, one '
                  'PostgreSQL engine holding one database per environment with its own logins, and one shared '
                  'tenant-aware Storage process. The shared parts are a shared failure boundary. A per-environment Studio '
                  'is planned, not built.',
                  'على اليسار ثلاثة مشاريع، يشغّل كل منها حزمة كاملة خاصة به: Studio وAuth وREST وStorage وPostgres '
                  'وبوابة وسجلات. وعلى اليمين صباربيز: بوابة واحدة، وAuth وREST منفصلان لكل بيئة، ومحرك PostgreSQL واحد '
                  'فيه قاعدة بيانات لكل بيئة بحسابات دخولها الخاصة، وعملية Storage واحدة مشتركة تعرف المستأجرين. الأجزاء '
                  'المشتركة حدود فشل مشتركة. Studio لكل بيئة مخطط وغير مبني.'))
    d.text(24, 40, t('A full stack per project', 'حزمة كاملة لكل مشروع'), size=15, weight=700, anchor='start')
    parts = ('Studio', 'Auth', 'REST', 'Storage', 'Postgres', t('gateway, logs', 'بوابة، سجلات'))
    for c in range(3):
        x = 24 + c * 122
        for i, part in enumerate(parts):
            d.box(x, 64 + i * 34, 110, 28)
            d.text(x + 55, 83 + i * 34, part, size=12.5)
        d.text(x + 55, 290, t(f'project {c + 1}', f'المشروع {c + 1}'), size=12.5, weight=700)
    d.lines(24, 330, [t('Every part is repeated, upgraded', 'كل جزء يتكرر، ويُرقّى'),
                      t('and backed up per project.', 'ويُنسخ احتياطيًا لكل مشروع.')], size=12.5, anchor='start', fill=MUTED)
    d.arrow('M410 56 L410 380', color='#C9BFAE', head=False, width=1.2)
    d.text(440, 40, t('Sbarbase: shared engines', 'صباربيز: محركات مشتركة'), size=15, weight=700, anchor='start')
    d.box(440, 60, 436, 34, 'shared')
    d.text(658, 82, t('one gateway and control API', 'بوابة واحدة وواجهة إدارة واحدة'))
    for c, env in enumerate((t('env A', 'البيئة أ'), t('env B', 'البيئة ب'), t('env C', 'البيئة ج'))):
        x = 440 + c * 98
        d.box(x, 110, 90, 30, 'env')
        d.text(x + 45, 130, f'Auth, {env}' if lang == 'en' else f'Auth، {env}', size=12)
        d.box(x, 148, 90, 30, 'env')
        d.text(x + 45, 168, f'REST, {env}' if lang == 'en' else f'REST، {env}', size=12)
        d.arrow(f'M{x + 45} 178 L{x + 45} 226')
    d.box(440, 196, 286, 132, 'shared')
    d.text(583, 216, t('one PostgreSQL engine', 'محرك PostgreSQL واحد'), weight=700, size=12.5)
    for c, name in enumerate((t('database A', 'قاعدة أ'), t('database B', 'قاعدة ب'), t('database C', 'قاعدة ج'))):
        x = 448 + c * 92
        d.box(x, 228, 86, 84, 'db')
        d.lines(x + 43, 262, [name, t('own logins', 'بحساباتها')], size=12, gap=20)
    d.box(740, 110, 136, 218, 'shared')
    d.lines(808, 190, ['Storage', t('one process,', 'عملية واحدة،'), t('a tenant per', 'مستأجر لكل'),
                       t('environment', 'بيئة')], size=12.5, gap=20)
    d.arrow('M658 94 L658 102 L808 102 L808 108', dash='4 4')
    d.text(820, 98, t('files', 'الملفات'), size=11.5, anchor='start', fill=MUTED)
    d.box(440, 344, 436, 36, 'planned')
    d.text(658, 367, t('Studio per environment: planned, not built', 'Studio لكل بيئة: مخطط وغير مبني'), size=12.5)
    d.lines(24, 408, [t('PostgreSQL and Storage run once, so the same server has room for more projects.',
                         'يعمل PostgreSQL وStorage مرة واحدة، فيتسع الخادم نفسه لمشاريع أكثر.'),
                       t('Each extra environment adds its own database in the engine and a small Auth and REST (under 20 MiB idle in one',
                         'وكل بيئة إضافية تضيف قاعدتها في المحرك وAuth وREST صغيرين، أقل من 20 MiB في الخمول في عينة محلية واحدة.'),
                       t('local sample); the preflight still reserves 512 MiB of limits per environment, and a lab guard allows four for now.',
                         'ومع ذلك يحجز فحص الخادم 512 MiB من الحدود لكل بيئة، ويسمح حارس المختبر بأربع بيئات حاليًا.')],
            size=12, gap=18, anchor='start')
    d.legend(476, [('shared', t('shared: a shared failure boundary', 'مشترك: حد فشل مشترك')),
                   ('env', t('one per environment', 'واحد لكل بيئة')), ('planned', t('planned', 'مخطط'))])
    return d


def trust(lang):
    t = lambda en, ar: pick(lang, en, ar)
    d = Diagram('trust-boundaries', 900, 480, lang,
                t('Who is trusted, and what is shared', 'من الموثوق، وما المشترك'),
                t('App visitors are untrusted. Their browsers and mobile apps reach the server only over HTTPS through '
                  'the TLS proxy, the one part facing the network, and then the gateway, which requires a publishable '
                  'key. Trusted operators reach the console and control API through the same proxy with a management '
                  'login, and may also use the host shell and SQL directly. Behind the proxy everything listens on '
                  'loopback or internal Docker networks. Each environment has its own Auth, REST, database, logins and '
                  'keys. The PostgreSQL engine and the Storage process are shared, so they are shared failure boundaries.',
                  'زوار التطبيقات غير موثوقين. لا تصل متصفحاتهم وتطبيقاتهم الجوالة إلى الخادم إلا عبر HTTPS من خلال '
                  'وكيل TLS، الجزء الوحيد المواجه للشبكة، ثم البوابة التي تطلب مفتاحًا عامًا. والمشغّلون موثوقون: يصلون '
                  'إلى لوحة الإدارة وواجهة الإدارة عبر الوكيل نفسه بدخول الإدارة، ويمكنهم استخدام سطر أوامر الخادم وSQL '
                  'مباشرة. خلف الوكيل تستمع كل الخدمات على عناوين محلية أو شبكات Docker داخلية. لكل بيئة Auth وREST '
                  'وقاعدة بيانات وحسابات دخول ومفاتيح خاصة بها. محرك PostgreSQL وعملية Storage مشتركان، فهما حدود فشل '
                  'مشتركة.'))
    d.text(24, 34, d.title, size=16, weight=700, anchor='start')
    d.box(24, 76, 176, 100, 'warn')
    d.text(112, 100, t('untrusted', 'غير موثوقين'), weight=700, fill=CORAL_INK)
    d.lines(112, 122, [t('app visitors', 'زوار التطبيقات'), t('browser or mobile app', 'متصفح أو تطبيق جوال'),
                       t('with a user token', 'برمز المستخدم')], size=12.5, gap=18)
    d.box(24, 250, 176, 100, 'env')
    d.text(112, 274, t('trusted', 'موثوقون'), weight=700, fill=TEAL_INK)
    d.lines(112, 296, [t('your operators', 'المشغّلون'), t('operator browser', 'متصفح المشغّل'),
                       t('host shell, SQL author', 'سطر الأوامر وSQL')], size=12.5, gap=18)
    d.box(232, 50, 644, 380, 'zone')
    d.text(248, 72, t('your server (host)', 'خادمك'), weight=700, anchor='start')
    d.text(248, 92, t('behind the proxy: loopback and internal Docker networks, no published ports',
                      'خلف الوكيل: عناوين محلية وشبكات Docker داخلية، بلا منافذ منشورة'), size=12, anchor='start', fill=MUTED)
    d.box(250, 150, 118, 124, 'card')
    d.text(309, 176, t('TLS proxy', 'وكيل TLS'), weight=700)
    d.lines(309, 202, [t('the only part', 'الجزء الوحيد'), t('facing the', 'المواجه'), t('network', 'للشبكة')], size=12, gap=18)
    d.box(400, 112, 172, 58, 'shared')
    d.text(486, 136, t('gateway', 'البوابة'), weight=700)
    d.text(486, 156, t('publishable key', 'مفتاح عام'), size=12)
    d.box(400, 256, 172, 58, 'card')
    d.text(486, 280, t('console, control API', 'لوحة الإدارة وواجهتها'), size=12.5, weight=700)
    d.text(486, 300, t('management login', 'دخول الإدارة'), size=12)
    d.box(590, 108, 270, 50, 'env')
    d.text(725, 128, t('environment A', 'البيئة أ'), weight=700, size=12.5)
    d.text(725, 147, t('own Auth, REST, database, logins, keys', 'Auth وREST وقاعدة وحسابات ومفاتيح خاصة'), size=11.5)
    d.box(590, 168, 270, 50, 'env')
    d.text(725, 188, t('environment B', 'البيئة ب'), weight=700, size=12.5)
    d.text(725, 207, t('own Auth, REST, database, logins, keys', 'Auth وREST وقاعدة وحسابات ومفاتيح خاصة'), size=11.5)
    d.box(590, 248, 128, 66, 'shared')
    d.text(654, 272, t('PostgreSQL', 'محرك PostgreSQL'), weight=700, size=12.5)
    d.text(654, 294, t('shared by all', 'مشترك بين الجميع'), size=12)
    d.box(732, 248, 128, 66, 'shared')
    d.text(796, 272, t('Storage', 'عملية Storage'), weight=700, size=12.5)
    d.text(796, 294, t("every tenant's files", 'ملفات كل المستأجرين'), size=12)
    d.arrow('M200 126 C226 126 222 180 248 184')
    d.arrow('M200 300 C226 300 222 246 248 242')
    d.text(254, 140, 'HTTPS', size=11, anchor='start', fill=MUTED)
    d.text(254, 292, 'HTTPS', size=11, anchor='start', fill=MUTED)
    d.arrow('M368 190 C384 190 382 141 398 141')
    d.arrow('M368 236 C384 236 382 285 398 285')
    d.arrow('M572 132 L588 132')
    d.arrow('M572 152 C582 152 578 192 588 192')
    d.arrow('M725 218 L725 234 L654 234 L654 246')
    d.text(736, 238, t('own logins', 'بحساباتها'), size=11, anchor='start', fill=MUTED)
    d.arrow('M572 162 L580 162 L580 340 L796 340 L796 316', dash='4 4')
    d.text(668, 356, t('files, tenant header', 'الملفات، بترويسة المستأجر'), size=11.5, fill=MUTED)
    d.arrow('M112 350 L112 406 L654 406 L654 316', dash='2 5')
    d.text(380, 400, t('direct host and SQL access', 'وصول مباشر إلى الخادم وSQL'), size=11.5, fill=MUTED)
    d.text(24, 452, t('Shared parts: a failure there reaches every environment. Not built for mutually hostile customers.',
                      'الأجزاء المشتركة: عطلها يصل إلى كل البيئات. التصميم ليس لعملاء يعادي بعضهم بعضًا.'),
           size=12.5, anchor='start')
    d.legend(472, [('shared', t('shared', 'مشترك')), ('env', t('one per environment, or trusted', 'لكل بيئة، أو موثوق')),
                   ('warn', t('untrusted', 'غير موثوق'))])
    d.h = 490
    return d


def key_isolation(lang):
    t = lambda en, ar: pick(lang, en, ar)
    d = Diagram('key-isolation', 900, 470, lang,
                t("Project A's key cannot reach project B's data", 'مفتاح المشروع أ لا يصل إلى بيانات المشروع ب'),
                t("Three rows. First, a request on environment A's path with A's publishable key: the gateway finds the key "
                  "among A's stored key hashes and forwards it to A's own REST, which logs in to database A with A's own "
                  "login. Second, the same key on environment B's path: the key is not one of B's keys, so the gateway "
                  "answers 401 and forwards nothing. A user token signed by A's Auth is also invalid at B, whose Auth has "
                  "its own signing secret. Third, underneath: even a service login that tried the wrong database is "
                  "refused by PostgreSQL, because the connection rules name exact login and database pairs and CONNECT on "
                  "each database is granted only to its own logins.",
                  'ثلاثة صفوف. الأول: طلب على مسار البيئة أ بمفتاح أ العام؛ تجد البوابة المفتاح بين بصمات مفاتيح أ '
                  'المخزنة، فتمرره إلى REST الخاص بـ أ، الذي يدخل إلى القاعدة أ بحساب أ الخاص. الثاني: المفتاح نفسه '
                  'على مسار البيئة ب؛ ليس من مفاتيح ب، فترد البوابة بـ 401 ولا تمرر شيئًا. ورمز المستخدم الذي وقّعه Auth '
                  'الخاص بـ أ غير صالح عند ب أيضًا، لأن لـ Auth ب سر توقيع خاصًا. الثالث، في العمق: حتى حساب خدمة يحاول '
                  'القاعدة الخطأ يرفضه PostgreSQL، لأن قواعد الاتصال تسمّي أزواج الحساب والقاعدة بدقة، وصلاحية CONNECT '
                  'على كل قاعدة ممنوحة لحساباتها وحدها.'))
    d.text(24, 34, d.title, size=16, weight=700, anchor='start')
    # engine on the right
    d.box(700, 60, 176, 326, 'shared')
    d.text(788, 84, t('PostgreSQL engine', 'محرك PostgreSQL'), weight=700, size=12.5)
    d.text(788, 102, t('shared', 'مشترك'), size=12)
    d.box(716, 116, 144, 70, 'db')
    d.lines(788, 142, [t('database A', 'القاعدة أ'), t("A's logins only", 'لحسابات أ وحدها')], size=12.5, gap=20)
    d.box(716, 290, 144, 70, 'db')
    d.lines(788, 316, [t('database B', 'القاعدة ب'), t("B's logins only", 'لحسابات ب وحدها')], size=12.5, gap=20)
    rows = ((70, t('allowed', 'مسموح'), TEAL_INK), (196, t('refused at the gateway', 'مرفوض عند البوابة'), CORAL_INK),
            (300, t('refused in PostgreSQL', 'مرفوض في PostgreSQL'), CORAL_INK))
    for y, label, color in rows:
        d.text(24, y, label, size=12, weight=700, anchor='start', fill=color)
    # row 1: allowed
    d.box(24, 80, 150, 60)
    d.text(99, 104, t("app with A's key", 'تطبيق بمفتاح أ'), weight=600, size=12.5)
    d.text(99, 124, '/envA/rest/v1', size=11.5, mono=True)
    d.box(210, 80, 190, 60, 'shared')
    d.text(305, 104, t('gateway', 'البوابة'), weight=700)
    d.text(305, 124, t("key is one of A's hashes", 'المفتاح من بصمات أ'), size=12)
    d.box(436, 80, 160, 60, 'env')
    d.text(516, 104, t('REST, env A', 'REST للبيئة أ'), weight=600)
    d.text(516, 124, t("login: A's REST login", 'بحساب REST الخاص بـ أ'), size=11.5)
    d.arrow('M174 110 L208 110')
    d.arrow('M400 110 L434 110')
    d.arrow('M596 110 C650 110 660 151 714 151')
    # row 2: wrong path
    d.box(24, 206, 150, 60)
    d.text(99, 230, t("app with A's key", 'تطبيق بمفتاح أ'), weight=600, size=12.5)
    d.text(99, 250, '/envB/rest/v1', size=11.5, mono=True)
    d.box(210, 206, 190, 60, 'shared')
    d.text(305, 230, t('gateway', 'البوابة'), weight=700)
    d.text(305, 250, t("not one of B's keys", 'ليس من مفاتيح ب'), size=12)
    d.box(436, 206, 160, 60, 'warn')
    d.text(516, 230, t('401, nothing forwarded', '401، ولا يمر شيء'), weight=700, size=12.5)
    d.text(516, 250, t("B's data never reached", 'بيانات ب لا تُمس'), size=12)
    d.arrow('M174 236 L208 236')
    d.arrow('M400 236 L434 236', color=CORAL)
    d.lines(210, 164, [t("A user token from A's Auth is also invalid at B: each Auth has its own signing secret.",
                         'ورمز مستخدم من Auth الخاص بـ أ غير صالح عند ب: لكل Auth سر توقيع خاص.')],
            size=12, anchor='start', fill=MUTED)
    # row 3: defence underneath
    d.box(24, 310, 150, 60)
    d.text(99, 334, t("A's REST login", 'حساب REST لـ أ'), weight=600, size=12.5)
    d.text(99, 354, t('tries database B', 'يحاول القاعدة ب'), size=12)
    d.box(210, 310, 386, 60, 'warn')
    d.text(403, 334, t('connection rules: one login, one database', 'قواعد الاتصال: حساب واحد لقاعدة واحدة'), weight=700, size=12.5)
    d.text(403, 354, t('CONNECT is granted only to its own logins', 'صلاحية CONNECT لحساباتها وحدها'), size=12)
    d.arrow('M174 340 L208 340')
    d.arrow('M596 340 C650 340 660 325 700 325', color=CORAL, dash='4 4')
    d.text(648, 318, t('refused', 'رفض'), size=11.5, fill=CORAL_INK)
    d.text(24, 414, t('Each environment also has its own Storage tenant and signing keys. Trusted operators and SQL authors '
                      'are outside this boundary.',
                      'ولكل بيئة أيضًا مستأجر Storage ومفاتيح توقيع خاصة. المشغّلون وكتّاب SQL الموثوقون خارج هذا الحد.'),
           size=12, anchor='start', fill=MUTED)
    d.legend(446, [('env', t('one per environment', 'واحد لكل بيئة')), ('shared', t('shared', 'مشترك')),
                   ('db', t('environment database', 'قاعدة البيئة')), ('warn', t('refused', 'مرفوض'))])
    return d


def load_isolation(lang):
    t = lambda en, ar: pick(lang, en, ar)
    d = Diagram('load-isolation', 900, 520, lang,
                t('A busy environment borrows idle room, never its neighbour\'s share',
                  'البيئة المزدحمة تستعير المكان الفارغ، لا حصة جارتها'),
                t('Environment A receives a burst of requests while environment B has normal traffic. The gateway has '
                  '32 slots. Each environment is guaranteed 8. A busy environment may borrow idle slots up to 24, but '
                  'only while 8 slots stay free for environments within their share. Here A holds its 8 and has borrowed '
                  '14; B uses 2; the last 8 are kept free. A\'s next request gets 429 and retries shortly; B\'s next request '
                  'is admitted at once. When B needs more room, A borrows nothing new and its borrowed slots return as '
                  'its requests finish. After 15 minutes like this the operator gets one notice. Behind the gateway, each '
                  'environment\'s Auth and REST run in their own containers with CPU, memory and IO limits, and each '
                  'database has its own connection limits. CPU, memory and IO inside the one PostgreSQL engine remain '
                  'shared, and the limits are not calibrated under sustained load.',
                  'تتلقى البيئة أ دفعة كبيرة من الطلبات، والبيئة ب حركتها عادية. في البوابة 32 مكانًا، ولكل بيئة 8 مضمونة. '
                  'يمكن للبيئة المزدحمة أن تستعير الأماكن الفارغة حتى 24، ما دامت 8 أماكن تبقى فارغة للبيئات التي لم تتجاوز '
                  'حصتها. هنا تشغل أ أماكنها الثمانية واستعارت 14، وتستخدم ب مكانين، والثمانية الأخيرة محجوزة فارغة. طلب أ '
                  'التالي يتلقى 429 ويعيد المحاولة بعد قليل، وطلب ب التالي يُقبل فورًا. وحين تحتاج ب مكانًا أكثر لا تستعير أ '
                  'شيئًا جديدًا، وتعود أماكنها المستعارة كلما انتهى أحد طلباتها. بعد 15 دقيقة على هذه الحال يصل المشغّل '
                  'تنبيه واحد. خلف البوابة يعمل Auth وREST لكل بيئة في حاوياتهما بحدود للمعالج والذاكرة والقرص، ولكل قاعدة '
                  'حدود اتصالاتها. المعالج والذاكرة والقرص داخل محرك PostgreSQL الواحد تبقى مشتركة، وهذه الحدود غير معايرة '
                  'تحت حمل مستمر.'))
    d.text(24, 34, d.title, size=16, weight=700, anchor='start')
    # sources
    d.box(24, 70, 150, 110, 'warn')
    d.text(99, 96, t('env A', 'البيئة أ'), weight=700)
    d.lines(99, 120, [t('a burst:', 'دفعة كبيرة:'), t('many requests', 'طلبات كثيرة'), t('at once', 'في وقت واحد')], size=12.5, gap=18)
    d.box(24, 236, 150, 90, 'env')
    d.text(99, 262, t('env B', 'البيئة ب'), weight=700)
    d.lines(99, 286, [t('normal traffic', 'حركة عادية'), t('a few requests', 'طلبات قليلة')], size=12.5, gap=18)
    for i in range(6):
        d.arrow(f'M174 {88 + i * 16} L212 {88 + i * 16}', color=CORAL, width=1.3)
    d.arrow('M174 270 L212 270')
    d.arrow('M174 292 L212 292')
    # gateway: 32 slots in one row, A's share, A's borrowed slots, B, and the room kept free
    d.box(214, 60, 290, 290, 'shared')
    d.text(359, 84, t('gateway: 32 slots', 'البوابة: 32 مكانًا'), weight=700, size=13)
    for i in range(32):
        kind = 'warn' if i < 8 else 'borrowed' if i < 22 else 'env' if i < 24 else 'card'
        d.box(231 + i * 8, 100, 7, 26, kind, rx=1.5)
    d.text(231, 144, t('A: its share of 8, plus 14 borrowed', 'أ: حصتها 8، و14 مستعارة'), size=12, anchor='start', fill=CORAL_INK)
    d.text(231, 163, t('B: 2 in use, within its share of 8', 'ب: 2 مستخدمة، ضمن حصتها 8'), size=12, anchor='start', fill=TEAL_INK)
    d.text(231, 182, t('last 8: kept free for any share', 'الثمانية الأخيرة: محجوزة لأي حصة'), size=12, anchor='start')
    d.text(231, 214, t('A asks for more: 429, retry shortly', 'أ تطلب المزيد: 429، أعد المحاولة'), size=12,
           anchor='start', weight=700, fill=CORAL_INK)
    d.text(231, 236, t('B asks: admitted at once', 'ب تطلب: تُقبل فورًا'), size=12, anchor='start', weight=700, fill=TEAL_INK)
    d.lines(231, 264, [t('A never passes 24. When B needs room,', 'أ لا تتجاوز 24. وحين تحتاج ب مكانًا'),
                       t('A borrows nothing new, and its borrowed', 'لا تستعير أ جديدًا، وتعود أماكنها'),
                       t('slots return as its requests finish.', 'المستعارة كلما انتهى طلب.')], size=11.5, gap=17, fill=MUTED, anchor='start')
    d.text(231, 334, t('15 minutes like this: one operator notice', '15 دقيقة هكذا: تنبيه واحد للمشغّل'), size=11.5,
           anchor='start', weight=700)
    d.arrow('M226 210 L116 210', color=CORAL)
    d.box(24, 196, 90, 26, 'warn', rx=4)
    d.text(69, 214, '429', size=12.5, weight=700, fill=CORAL_INK)
    # per-environment services
    for y, env, kind in ((70, t('env A', 'البيئة أ'), 'env'), (236, t('env B', 'البيئة ب'), 'env')):
        d.box(540, y, 170, 100, kind)
        d.text(625, y + 24, f'Auth, REST: {env}' if lang == 'en' else f'Auth وREST: {env}', weight=700, size=12.5)
        d.lines(625, y + 48, [t('own containers:', 'حاوياتها الخاصة:'), t('CPU, memory and', 'حدود للمعالج والذاكرة'),
                              t('IO limits', 'والقرص')], size=12, gap=17)
    d.arrow('M504 120 L538 120')
    d.arrow('M504 286 L538 286')
    # engine
    d.box(740, 60, 136, 290, 'shared')
    d.text(808, 84, 'PostgreSQL', weight=700, size=12.5)
    d.text(808, 102, t('one engine', 'محرك واحد'), size=12)
    d.box(752, 116, 112, 74, 'db')
    d.lines(808, 138, [t('database A', 'القاعدة أ'), t('18 connections', '18 اتصالًا'), t('6 per login', '6 لكل حساب')], size=11.5, gap=17)
    d.box(752, 262, 112, 74, 'db')
    d.lines(808, 284, [t('database B', 'القاعدة ب'), t('18 connections', '18 اتصالًا'), t('6 per login', '6 لكل حساب')], size=11.5, gap=17)
    d.lines(808, 214, [t('REST statements', 'استعلامات REST'), t('stop after 8 s', 'تتوقف بعد 8 ث')], size=11.5, gap=17, fill=MUTED)
    d.arrow('M710 120 C726 120 730 153 750 153')
    d.arrow('M710 286 C726 286 730 299 750 299')
    # outcome
    d.box(24, 372, 852, 64, 'card')
    d.text(450, 396, t('Result: A uses the idle server, and B is still served at once.',
                       'النتيجة: أ تستفيد من الخادم الفارغ، وب ما زالت تُخدم فورًا.'), size=13.5, weight=700)
    d.text(450, 420, t('Local probes measured the fixed share of 8 and a neighbour still served; borrowing is covered by unit tests.',
                       'قاست الفحوص المحلية الحصة الثابتة 8 وبقاء الجارة تُخدم؛ والاستعارة تغطيها اختبارات الوحدة.'), size=12)
    d.text(24, 462, t('Not calibrated under sustained load: CPU, memory and IO inside the one PostgreSQL engine stay shared, '
                      'so a heavy neighbour can still slow the others.',
                      'غير معايرة تحت حمل مستمر: المعالج والذاكرة والقرص داخل محرك PostgreSQL الواحد تبقى مشتركة، فقد يبطئ '
                      'جار ثقيل البقية.'), size=12, anchor='start', fill=MUTED)
    d.legend(494, [('warn', t('A, its share', 'أ، حصتها')), ('borrowed', t('A, borrowed', 'أ، مستعار')),
                   ('env', t('B, or one per environment', 'ب، أو واحد لكل بيئة')), ('card', t('kept free', 'محجوز فارغ')),
                   ('db', t('environment database', 'قاعدة البيئة'))])
    return d


def provisioning(lang):
    t = lambda en, ar: pick(lang, en, ar)
    d = Diagram('provisioning', 900, 440, lang,
                t('Provisioning: record first, act, then settle', 'التجهيز: سجّل أولًا، ثم نفّذ، ثم أغلق'),
                t('Five steps. The control API queues a job in the catalog. One worker holding an exclusive lock claims '
                  'it. Before any effect, the worker writes an effect receipt naming the claim, attempt and operation token '
                  'and flushes it to disk. The fenced effect runs: SQL, connection rules and services, under a guardian '
                  'with a deadline. The outcome is recorded in the catalog with the job, then the receipt is consumed. '
                  'After a crash, a completed receipt is settled, an interruption proven to precede any change is '
                  'requeued a bounded number of times, and an unknown outcome blocks startup and replay until an operator '
                  'reconciles it.',
                  'خمس خطوات. تضع واجهة الإدارة مهمة في الفهرس. يستلمها عامل واحد يملك قفلًا حصريًا. قبل أي أثر، يكتب '
                  'العامل إيصال أثر يسمّي المطالبة والمحاولة ورمز العملية ويثبّته على القرص. ثم ينفَّذ الأثر المسيّج: SQL '
                  'وقواعد الاتصال والخدمات، تحت حارس بمهلة. تُسجَّل النتيجة في الفهرس مع المهمة، ثم يُستهلك الإيصال. بعد '
                  'الانهيار يُغلق الإيصال المكتمل، ويعاد إلى الطابور ما ثبت أنه توقف قبل أي تغيير لعدد محدود من المرات، '
                  'وتمنع النتيجة المجهولة البدء والإعادة حتى يطابقها المشغّل.'))
    d.text(24, 34, d.title, size=16, weight=700, anchor='start')
    steps = (
        ('card', t('queue a job', 'مهمة جديدة'), [t('control API writes it', 'تكتبها واجهة الإدارة'), t('to the catalog', 'في الفهرس')]),
        ('card', t('claim it', 'الاستلام'), [t('one worker holds', 'عامل واحد'), t('an exclusive lock', 'بقفل حصري')]),
        ('record', t('write a receipt', 'إيصال الأثر'), [t('claim, attempt, token;', 'المطالبة والمحاولة'), t('flushed to disk', 'والرمز، على القرص')]),
        ('shared', t('fenced effect', 'أثر مسيّج'), [t('SQL, connection rules,', 'SQL وقواعد الاتصال'), t('services; guardian', 'والخدمات، بحارس'), t('with a deadline', 'ومهلة')]),
        ('env', t('record outcome', 'تسجيل النتيجة'), [t('in the catalog with', 'في الفهرس مع المهمة'), t('the job, then consume', 'ثم يُستهلك'), t('the receipt', 'الإيصال')]),
    )
    for i, (kind, head, body) in enumerate(steps):
        x = 24 + i * 174
        d.box(x, 62, 160, 110, kind)
        d.badge(x + 20, 82, i + 1)
        d.text(x + 38, 87, head, weight=700, anchor='start', size=13)
        d.lines(x + 80, 116, body, size=12, gap=18)
        if i < 4:
            d.arrow(f'M{x + 160} 117 L{x + 172} 117')
    d.text(24, 214, t('After a crash, on restart', 'بعد انهيار، عند إعادة التشغيل'), weight=700, anchor='start')
    d.arrow('M452 172 L452 226', dash='4 4', head=False)
    d.arrow('M160 226 L740 226', head=False)
    outcomes = (
        ('env', t('completed receipt', 'إيصال مكتمل'), [t('settled against the catalog', 'يُطابق مع الفهرس ويُغلق')]),
        ('env', t('stopped before any change', 'توقف قبل أي تغيير'), [t('proven, so requeued a bounded', 'ثبت ذلك، فيعاد إلى الطابور'), t('number of times', 'لعدد محدود من المرات')]),
        ('warn', t('unknown outcome', 'نتيجة مجهولة'), [t('blocks startup and replay;', 'تمنع البدء والإعادة،'), t('an operator reconciles it', 'ويطابقها المشغّل')]),
    )
    for i, (kind, head, body) in enumerate(outcomes):
        x = 24 + i * 290
        d.arrow(f'M{x + 136} 226 L{x + 136} 244')
        d.box(x, 246, 272, 84, kind)
        d.text(x + 136, 272, head, weight=700)
        d.lines(x + 136, 296, body, size=12.5, gap=18)
    d.text(24, 368, t('Never delete a pending receipt, journal, generation pin or tombstone to get past a refusal.',
                      'لا تحذف إيصالًا معلقًا أو سجل عملية أو تثبيت جيل أو شاهد إلغاء لتتجاوز رفضًا.'), size=12.5, anchor='start')
    d.text(24, 390, t('Crash tests cover named checkpoints on disposable fixtures, not every possible crash.',
                      'اختبارات الانهيار تغطي نقاطًا مسماة على بيئات ثابتة مؤقتة، لا كل انهيار ممكن.'),
           size=12.5, anchor='start', fill=MUTED)
    d.legend(424, [('record', t('durable record', 'سجل دائم')), ('shared', t('touches the shared engine', 'يمس المحرك المشترك')),
                   ('env', t('settled', 'مُغلق')), ('warn', t('stops for an operator', 'يتوقف للمشغّل'))])
    return d


def restore(lang):
    t = lambda en, ar: pick(lang, en, ar)
    d = Diagram('restore-flow', 900, 470, lang,
                t('Restore one environment: verify before switching', 'استعادة بيئة واحدة: تحقق قبل التحويل'),
                t('Five steps. 1, stop writes: the gateway puts the environment in maintenance and its service logins are '
                  'disabled, with their state kept in a journal. 2, encrypted export of the database, logins, files and '
                  'signing keys, after which the source database is closed. 3, restore into a fresh pinned engine with its '
                  'own network and volume. 4, verify rows and hashes, roles, the original login, row-level security and an '
                  'old signed URL. 5, switch the routing record to the new engine with a revision check. If verification '
                  'fails, the source data is untouched but stays fenced, and an operator reopens it deliberately. '
                  'Exporting stops the shared Storage process and the other services of the source, so every environment '
                  'on that engine is offline until they are restarted. Scheduled and off-host backups are planned, not built.',
                  'خمس خطوات. 1، إيقاف الكتابة: تضع البوابة البيئة في الصيانة وتُعطَّل حسابات خدمتها، وتُحفظ حالتها في سجل '
                  'العملية. 2، تصدير مشفّر للقاعدة والحسابات والملفات ومفاتيح التوقيع، ثم تُغلق قاعدة المصدر. 3، استعادة '
                  'إلى محرك جديد مثبّت الإصدار بشبكته ووحدة تخزينه. 4، التحقق من الصفوف والبصمات والأدوار والدخول الأصلي '
                  'وRLS ورابط موقّع قديم. 5، تحويل سجل التوجيه إلى المحرك الجديد بعد فحص المراجعة. إن فشل التحقق تبقى '
                  'بيانات المصدر سليمة لكنها مسيّجة، ويعيد المشغّل فتحها عمدًا. التصدير يوقف عملية Storage المشتركة وبقية '
                  'خدمات المصدر، فتتوقف كل البيئات على ذلك المحرك حتى يُعاد تشغيلها. النسخ المجدولة وخارج الخادم مخططة '
                  'وغير مبنية.'))
    d.text(24, 34, d.title, size=16, weight=700, anchor='start')
    steps = (
        ('card', t('stop writes', 'إيقاف الكتابة'), [t('maintenance: 503', 'صيانة: 503'), t('service logins off,', 'حسابات الخدمة معطلة'), t('state in a journal', 'وحالتها في سجل')]),
        ('record', t('encrypted export', 'تصدير مشفّر'), [t('database, logins,', 'القاعدة والحسابات'), t('files, signing keys;', 'والملفات والمفاتيح،'), t('source then closed', 'ثم يُغلق المصدر')]),
        ('card', t('restore', 'استعادة'), [t('a fresh engine,', 'محرك جديد'), t('pinned, with its own', 'مثبّت الإصدار'), t('network and volume', 'بشبكته ووحدة تخزينه')]),
        ('card', t('verify', 'تحقق'), [t('rows, hashes, roles,', 'الصفوف والبصمات'), t('original login, RLS,', 'والأدوار والدخول وRLS'), t('an old signed URL', 'ورابط موقّع قديم')]),
        ('env', t('switch the route', 'تحويل المسار'), [t('routing record points', 'سجل التوجيه يشير'), t('at the new engine,', 'إلى المحرك الجديد'), t('revision checked', 'بعد فحص المراجعة')]),
    )
    for i, (kind, head, body) in enumerate(steps):
        x = 24 + i * 174
        d.box(x, 62, 160, 110, kind)
        d.badge(x + 20, 82, i + 1)
        d.text(x + 38, 87, head, weight=700, anchor='start')
        d.lines(x + 80, 116, body, size=12, gap=18)
        if i < 4:
            d.arrow(f'M{x + 160} 117 L{x + 172} 117')
    d.arrow('M626 172 L626 204', color=CORAL)
    d.box(372, 206, 504, 62, 'warn')
    d.text(624, 230, t('verification fails: source data untouched, still fenced',
                       'فشل التحقق: بيانات المصدر سليمة وتبقى مسيّجة'), size=12.5, weight=700)
    d.text(624, 252, t('an operator reopens it deliberately; nothing reopens it automatically',
                       'يعيد المشغّل فتحها عمدًا، ولا شيء يفتحها تلقائيًا'), size=12.5)
    d.box(24, 290, 852, 62, 'shared')
    d.text(450, 315, t("Exporting stops shared Storage and the source's other services:",
                       'التصدير يوقف Storage المشترك وبقية خدمات المصدر:'), size=13, weight=700)
    d.text(450, 337, t('every environment on that engine is offline until its neighbours are restarted.',
                       'كل البيئات على ذلك المحرك تبقى متوقفة حتى يُعاد تشغيلها.'), size=12.5)
    d.box(24, 368, 852, 38, 'planned')
    d.text(450, 392, t('planned: scheduled, off-host backups, and an online backup of one environment',
                       'مخطط: نسخ احتياطية مجدولة خارج الخادم، ونسخ بيئة واحدة دون إيقاف'), size=12.5)
    d.text(24, 430, t('A downtime procedure, exercised on one host with a retained test fixture.',
                      'إجراء يتطلب توقفًا، جُرّب على خادم واحد مع بيئة اختبار محفوظة.'), size=12.5, anchor='start', fill=MUTED)
    d.legend(458, [('record', t('encrypted artifact', 'ملف مشفّر')), ('shared', t('shared: affects every environment', 'مشترك: يمس كل البيئات')),
                   ('warn', t('stops for an operator', 'يتوقف للمشغّل')), ('planned', t('planned', 'مخطط'))])
    d.h = 480
    return d


def install(lang):
    t = lambda en, ar: pick(lang, en, ar)
    d = Diagram('install-flow', 900, 500, lang,
                t('Quickstart: from an empty server to a supabase-js call', 'البداية السريعة: من خادم فارغ إلى استدعاء supabase-js'),
                t('Eight steps. 1, install Docker, Git and Python 3.14. 2, create the sbarbase service account and its '
                  'checkout. 3, install the pinned Bun for that account. 4, write the first operator\'s credentials to a '
                  'private file. 5, run the preflight check until it reports 0 blockers. 6, one acceptance command checks '
                  'prerequisites, builds the console, checks the TLS proxy, installs the systemd unit, pulls the pinned '
                  'images, rehearses a start and a clean stop, and creates a first project with a production environment '
                  'and a supabase-js sign-up through the gateway, ending with Server acceptance: PASSED. 7, open the '
                  'console over an SSH tunnel. 8, point your app at the HTTPS address. Rehearsed in a local Fedora 44 '
                  'virtual machine, not yet on a real server.',
                  'ثماني خطوات. 1، ثبّت Docker وGit وPython 3.14. 2، أنشئ حساب الخدمة sbarbase ونسخة المستودع. 3، ثبّت '
                  'Bun بالإصدار المثبّت لذلك الحساب. 4، اكتب بيانات المشغّل الأول في ملف خاص. 5، شغّل فحص الخادم حتى '
                  'يعطي 0 عوائق. 6، أمر استلام واحد يفحص المتطلبات، ويبني لوحة الإدارة، ويفحص وكيل TLS، ويثبّت وحدة '
                  'systemd، ويسحب الصور المثبّتة، ويجرب تشغيلًا وإيقافًا نظيفًا، وينشئ أول مشروع ببيئة إنتاج ويسجّل '
                  'مستخدمًا بـ supabase-js عبر البوابة، وينتهي بـ Server acceptance: PASSED. 7، افتح لوحة الإدارة عبر نفق '
                  'SSH. 8، وجّه تطبيقك إلى عنوان HTTPS. جُرّب في جهاز افتراضي محلي بنظام Fedora 44، ولم يُجرّب على خادم '
                  'حقيقي بعد.'))
    d.text(24, 34, d.title, size=16, weight=700, anchor='start')
    row1 = (
        (t('system packages', 'حزم النظام'), [t('Docker, Git,', 'Docker وGit'), t('Python 3.14', 'وPython 3.14')]),
        (t('service account', 'حساب الخدمة'), [t('user sbarbase and', 'المستخدم sbarbase'), t('the checkout', 'ونسخة المستودع')]),
        (t('install Bun', 'تثبيت Bun'), [t('the pinned version,', 'بالإصدار المثبّت'), t('for that account', 'لذلك الحساب')]),
        (t('operator file', 'ملف المشغّل'), [t('private, mode 600;', 'ملف خاص بصلاحية 600'), t('no password in args', 'بلا كلمة مرور في الأوامر')]),
    )
    for i, (head, body) in enumerate(row1):
        x = 24 + i * 216
        d.box(x, 60, 204, 88)
        d.badge(x + 20, 80, i + 1)
        d.text(x + 38, 85, head, weight=700, anchor='start')
        d.lines(x + 102, 112, body, size=12.5, gap=19)
        if i < 3:
            d.arrow(f'M{x + 204} 104 L{x + 214} 104')
    d.arrow('M774 148 L774 162 L126 162 L126 176')
    d.box(24, 178, 204, 88)
    d.badge(44, 198, 5)
    d.text(62, 203, t('preflight check', 'فحص الخادم'), weight=700, anchor='start')
    d.lines(126, 230, [t('must end with', 'ينتهي بعبارة'), 'Preflight: 0 blocker(s)'], size=12, gap=19)
    d.arrow('M228 222 L238 222')
    d.box(240, 178, 636, 164, 'record')
    d.badge(260, 198, 6)
    d.text(278, 203, t('one acceptance command, in order', 'أمر استلام واحد، بالترتيب'), weight=700, anchor='start')
    checks = (t('check prerequisites', 'يفحص المتطلبات'), t('build the console', 'يبني لوحة الإدارة'),
              t('check the TLS proxy', 'يفحص وكيل TLS'), t('install the systemd unit', 'يثبّت وحدة systemd'),
              t('pull pinned images (about 2.4 GB)', 'يسحب الصور المثبّتة (نحو 2.4 GB)'),
              t('rehearse a start and a clean stop', 'يجرّب تشغيلًا وإيقافًا نظيفًا'),
              t('first project, production environment', 'أول مشروع ببيئة إنتاج'),
              t('supabase-js sign-up through the gateway', 'تسجيل بـ supabase-js عبر البوابة'))
    for i, s in enumerate(checks):
        col, row = divmod(i, 4)
        d.text(262 + col * 310, 232 + row * 22, f'{i + 1}. {s}' if lang == 'en' else f'{i + 1}. {s}', size=12.5, anchor='start')
    d.text(558, 330, 'Server acceptance: PASSED', size=12.5, weight=700, fill=TEAL_INK, mono=True)
    d.arrow('M450 342 C450 356 234 352 234 368')
    d.arrow('M666 342 C666 356 666 352 666 368')
    d.box(24, 370, 420, 70)
    d.badge(44, 390, 7)
    d.text(62, 395, t('console over an SSH tunnel', 'لوحة الإدارة عبر نفق SSH'), weight=700, anchor='start')
    d.text(234, 422, t('loopback only, on a pinned port', 'على العنوان المحلي فقط، بمنفذ ثابت'), size=12.5)
    d.box(456, 370, 420, 70, 'env')
    d.badge(476, 390, 8)
    d.text(494, 395, t('your app over HTTPS', 'تطبيقك عبر HTTPS'), weight=700, anchor='start')
    d.text(666, 422, t('TLS proxy in front, then supabase-js', 'وكيل TLS في الواجهة، ثم supabase-js'), size=12.5)
    d.text(24, 470, t('Rehearsed in a local Fedora 44 virtual machine (4 cores, 6 GB); not yet run on a real server.',
                      'جُرّب في جهاز افتراضي محلي بنظام Fedora 44 (4 أنوية، 6 GB)، ولم يُشغَّل على خادم حقيقي بعد.'),
           size=12.5, anchor='start')
    d.text(24, 490, t('A failure names its step and leaves the service as it found it; run the same command again.',
                      'عند الفشل تُذكر الخطوة وتبقى الخدمة كما كانت؛ أعد تشغيل الأمر نفسه.'), size=12.5, anchor='start', fill=MUTED)
    d.h = 510
    return d


def docs_map(lang):
    t = lambda en, ar: pick(lang, en, ar)
    d = Diagram('docs-map', 900, 344, lang,
                t('Where to read what', 'أين تقرأ ماذا'),
                t('Four sections, each with one purpose. Explain: why it works this way. Guides: how to do something. '
                  'Reference: facts to look up, including status and the glossary. Decisions: each choice, its '
                  'alternative and when to reconsider it. Below them sits the engineering notebook, the dated notes '
                  'written while building each mechanism, which the explain pages link to when you want to go deeper.',
                  'أربعة أقسام، لكل منها غرض واحد. الشروح: لماذا يعمل بهذه الطريقة. الأدلة العملية: كيف تنفّذ مهمة. '
                  'المرجع: حقائق تبحث عنها، ومنها الحالة والمسرد. القرارات: كل خيار وبديله ومتى نعيد النظر فيه. وتحتها '
                  'الدفتر الهندسي: ملاحظات مؤرخة كُتبت أثناء بناء كل آلية، تحيل إليها صفحات الشروح حين تريد التعمق.'))
    d.text(24, 34, d.title, size=16, weight=700, anchor='start')
    d.text(24, 58, t('New here? Start with explain/why.md', 'جديد هنا؟ ابدأ بصفحة لماذا صباربيز'), size=12.5, anchor='start', fill=MUTED)
    cols = (
        ('explain', t('explain', 'الشروح'), t('why it works this way', 'لماذا يعمل هكذا'),
         [t('why, architecture,', 'لماذا، البنية،'), t('hierarchy, trust,', 'الهرمية، الثقة،'), t('recovery, provisioning', 'الاستعادة، التجهيز')]),
        ('guides', t('guides', 'الأدلة العملية'), t('how to do something', 'كيف تنفّذ مهمة'),
         [t('quickstart, server,', 'البداية السريعة، الخادم،'), t('operator setup, backup,', 'إعداد المشغّل، النسخ'), t('upgrades', 'الاحتياطي، الترقيات')]),
        ('reference', t('reference', 'المرجع'), t('facts to look up', 'حقائق تبحث عنها'),
         [t('status: what works,', 'الحالة: ما الذي يعمل،'), t('glossary, configuration,', 'المسرد، الإعدادات،'), t('API, readiness', 'API، الجاهزية')]),
        ('decisions', t('decisions', 'القرارات'), t('why this design', 'لماذا هذا التصميم'),
         [t('each choice,', 'كل خيار،'), t('its alternative, and when', 'وبديله، ومتى'), t('to reconsider it', 'نعيد النظر فيه')]),
    )
    for i, (_, head, purpose, body) in enumerate(cols):
        x = 24 + i * 216
        d.box(x, 76, 204, 150, 'env' if i == 0 else 'card')
        d.text(x + 102, 104, head, size=15, weight=700)
        d.text(x + 102, 126, purpose, size=12.5, fill=TEAL_INK if i == 0 else CORAL_INK, weight=600)
        d.lines(x + 102, 160, body, size=12.5, gap=20)
    d.box(24, 250, 852, 74, 'card')
    d.text(450, 274, t('go deeper: the engineering notebook (English only)', 'للتعمق: الدفتر الهندسي (بالإنجليزية فقط)'), weight=700)
    d.text(450, 296, t('dated notes written while building each mechanism: designs, crash tests, reviews, plans, checkpoints',
                       'ملاحظات مؤرخة كُتبت أثناء بناء كل آلية: تصاميم واختبارات انهيار ومراجعات وخطط'), size=12.5)
    d.text(450, 314, t('also kept alongside: evidence (probe JSON), diagrams, the console design record',
                       'وبجانبها: الأدلة (ملفات JSON)، والرسوم، وسجل تصميم لوحة الإدارة'), size=12, fill=MUTED)
    d.arrow('M450 226 L450 248', dash='4 4')
    return d


DIAGRAMS = (hierarchy, request_path, full_stack, key_isolation, load_isolation, trust, provisioning, restore,
            install, docs_map)


def main():
    for build in DIAGRAMS:
        for lang in ('en', 'ar'):
            d = build(lang)
            suffix = '.ar.svg' if lang == 'ar' else '.svg'
            (HERE / f'{d.name}{suffix}').write_text(d.svg(), encoding='utf-8')
            print(f'{d.name}{suffix}')


if __name__ == '__main__':
    main()
