# Diagram generation record

Generated with the built-in image generation tool on 2026-09-20. These are proposed architecture diagrams, not a deployed system. They are also not the administration surface: that is the original upstream Studio, one instance per environment ([integration specification](../engineering/STUDIO-INTEGRATION.md)), while these pictures describe the sbarbase platform layer and the data plane.

The project count of 10 is a proposed installation pilot cap, not measured hardware capacity. Environments also consume resources; admission must check environment count, active connections, available memory, storage, workload and reserved recovery capacity. Server placement of six and four production environments is illustrative. Multi-server management is a future capability, not an initial deployment requirement.

Organization transfer means ownership and authorization changes for all project environments inside one installation. Server migration moves a selected environment, preserving project identity; endpoints can remain stable behind an installation-owned gateway, while direct connection details may change. Cross-installation export/import is a separate operation requiring destination identity and secret reconciliation.

Recovery must coordinate database state, objects, auth keys, configuration and function artifacts. Temporary recovery disables external jobs. Retaining the old source is not a safe rollback after new destination writes without reconciliation.

## Administration surface, and what these pictures predate

The middle band of `ten-projects.png` claims one console owns projects, permissions,
transfer and backup. That claim is now split: the platform console owns
organizations, projects, environments, connection details, keys and provisioning
status, and each environment is administered by its own instance of the original
upstream Studio. The regenerated band should show both, and the bottom band
"داخل المشروع" should carry the Studio label beside the environment internals.
The prompts below are retained as the generation history of the committed files,
and they name the platform layer's own surfaces, not Studio's.

A first corrected drawing is committed as `administration-surface.svg` with its
rendered `administration-surface.png`, which replaces the middle band of the
ten-project picture for the administration question specifically.

## Prompt 1

Create ONE beautifully clear Arabic technical architecture infographic, landscape 1800x1400 or similar high resolution. Not a photo. Supabase Studio inspired dark UI design: near-black background, charcoal flat panels, subtle gray borders, off-white clear large Arabic typography, restrained mint green #3ECF8E emphasis. No logos or imitation official branding. Exact big title "صبّار بيز: 10 مشاريع". Small subtitle "تصميم مقترح، وليس نظامًا منفذًا".
Make this understandable to a nontechnical founder. Three visually separated horizontal sections with ample space and large type:
TOP section heading "الملكية". Two organization panels side by side: "منظمة أ" contains exactly five project chips P01 P02 P03 P04 P05; "منظمة ب" contains exactly five P06 P07 P08 P09 P10. A note "المنظمة تحدد المالك والفريق".
MIDDLE section heading "التشغيل". One bar "لوحة إدارة واحدة: مشاريع، صلاحيات، نقل، نسخ احتياطي". Under it two server panels: "سيرفر 1" contains one "PostgreSQL مشترك" frame with SIX distinct database icons labeled P01 P02 P03 P04 P05 P06. "سيرفر 2" contains another "PostgreSQL مشترك" frame with FOUR icons labeled P07 P08 P09 P10. Label this distribution "مثال توزيع بعد التوسع، وليس حد سعة". A small note "كل رمز هنا يمثل بيئة إنتاج بقاعدة مستقلة". DO NOT draw ten crossing arrows; same IDs show mapping. P06 deliberately organization B but server1 to show independence.
BOTTOM section heading "داخل المشروع P03". Simple mini hierarchy: "المشروع P03" branches to "إنتاج" and "تجارب اختيارية". Each environment contains short stacked labels "قاعدة مستقلة" "مفاتيح ومستخدمون" "Auth + REST". Under these a band "بوابة واتصالات مشتركة، وخدمات حسب الحاجة". Footnote readable: "المحرك المشترك خيار قيد اختبار التوافق والعزل". Last capacity pill: "حد تجريبي مقترح: 10 مشاريع للتنصيب، والسعة الفعلية تُقاس لكل سيرفر". No claim zero processes, unlimited projects, guaranteed safety or zero downtime. Arabic right-to-left accurate text, Western digits, no em/en dashes, no text clipping, no excessive dense paragraphs. Hierarchy and containment must be precise.

## Prompt 2

Create ONE accurate polished Arabic infographic, landscape ~1800x1400. Supabase Studio inspired dark charcoal UI, thin gray borders, clean off-white Arabic typography, restrained mint green #3ECF8E, amber for pause/caution. Flat professional diagram, no glow, no logos, no official branding. Title "نقل المشروع واستعادته". Subtitle "عمليات مقترحة داخل نفس تنصيب صبّار بيز". Large readable RTL text, Western digits. Three clearly separated horizontal rows with numbered headings. Use icon cards and clear arrows, no long prose.
ROW 1 heading "1. نقل الملكية". Show organization "منظمة أ" containing project "P03", arrow to organization "منظمة ب" containing same "P03". Arrow caption "موافقة وصلاحيات". Below single note "نفس البيانات والسيرفر، تتغير العضوية والصلاحيات". Second short note "إلغاء الوصول السابق وتدوير الأسرار عند الحاجة". Explicit whole project transfer including its environments, tiny label "كل بيئات المشروع".
ROW 2 heading "2. نقل التشغيل". Show five connected steps right to left, Arabic labels:
"سيرفر 1: P03 إنتاج" -> "نسخ البيانات والملفات والإعدادات" -> "إيقاف كتابة مؤقت ومزامنة نهائية" -> "فحص ثم تحويل الاتصال" -> "سيرفر 2: P03 إنتاج".
Short line beneath "يبقى معرف المشروع ثابتًا، وتُعاد الاتصالات". Small amber note "بعد بدء الكتابة في الوجهة، الرجوع يحتاج مصالحة البيانات". Show small separate staging icon remains server1 label "التجارب يمكن أن تبقى مكانها". Make clear only production moved, no claim zero downtime. Transfers need version compatibility and target capacity; a compact prerequisite badge says "شرط: سعة وتوافق الوجهة".
ROW 3 heading "3. النسخ والاستعادة". Project P03 -> off-host vault "نسخة مشفرة خارج السيرفر" -> isolated new database box "استعادة إلى بيئة مؤقتة" -> check icon "فحص ثم استبدال البيئة". Under vault four short tags "قاعدة البيانات" "الملفات" "الأسرار" "الإعدادات والدوال". Note "مشاريع P01 وP02 وبقية المشاريع لا تُستبدل". Another concise note "تعطيل المهام والإرسال أثناء الاستعادة". Footnote amber readable "PITR: نستعيد المحرك مؤقتًا ثم نستخرج قاعدة البيئة فقط".
Bottom narrow principle band exact "ملكية مستقلة • هوية ثابتة • مكان تشغيل قابل للنقل • استعادة قابلة للاختبار"
Small footer "هذه إجراءات تصميم، وتحتاج اختبارات تنفيذ". Ensure arrows represent order and no uncontrolled double writable databases. No max project claims, no em/en dashes. Arabic clear, exact text, no spelling errors.
