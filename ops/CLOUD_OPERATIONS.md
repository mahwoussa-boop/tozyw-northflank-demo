# حزمة تشغيل Tozyw السحابية الآمنة

> **الهدف:** تشغيل تطبيق Tozyw الخاص بالتسعير على خادم دائم، مع بقاء SQLite خارج مسار الإصدارات، ونشر مقيد قابل للتراجع، ونسخ يومية متسقة ومشفرة إلى تخزين مستقل.

هذه الحزمة **لا تنشر شيئاً ولا تنشئ حسابات أو أسراراً ولا تعدّل المستودع المؤرشف**. توضع محتوياتها داخل مجلد `ops/` في مستودع نشط خاص، أو تستخدم كمرجع لعملية الإعداد اليدوي. يحظر وضع قواعد البيانات أو ملفات البيئة أو مفاتيح الوصول ضمن Git.

## القرار الهندسي

تطبيق Tozyw هو تطبيق Python/Streamlit يستخدم SQLite ويبلغ في التحليل الحي ذروة ذاكرة تقارب 1.1GB. لذلك لا يناسبه نشر مجاني بخادم 512MB أو بنظام ملفات مؤقت. الخيار الأساسي هو **OCI Always Free A1** في المنطقة المنزلية، بموارد لا تتجاوز الحد المجاني، مع تخزين محلي دائم للبيانات. وثائق OCI الحالية تذكر أن A1 المجاني يعادل إجمالياً 2 OCPU و12GB ذاكرة، مع 200GB من Block Volume؛ لكنه يتطلب تحقق بطاقة، وقد تسترد OCI المثيلات الخاملة وفق معاييرها، كما قد لا تتوافر السعة في المنطقة المطلوبة [1] [2].

| المكوّن | الاختيار الأساسي | المسؤولية | قاعدة الأمان |
|---|---|---|---|
| مصدر الشيفرة | GitHub، مستودع خاص **نشط** | مراجعات الشيفرة وCI | لا أسرار ولا بيانات حية في Git. |
| التشغيل | OCI A1 / Ubuntu ARM64 | عملية Streamlit واحدة | لا يكتب إلى قاعدة SQLite إلا تطبيق واحد. |
| البيانات | `/srv/tozyw/data` على Block Volume | SQLite وWAL وملفات الجلسة | لا يُستبدل أثناء الإصدار أو rollback. |
| النشر | GitHub Actions ثم SSH محدود | اختبار SHA ثم نشر SHA نفسه | قفل نشر، فحص صحة، ورجوع إلى الإصدار السابق عند الفشل. |
| نسخة خارجية أولى | Google Drive أو R2 عبر `rclone crypt` | أرشيفات SQLite يومية مشفرة | اتصال `crypt` فقط، لا remote الخام. |
| نسخة خارجية ثانية | Backblaze B2 عبر `rclone crypt` | استقلال عن مزود النسخة الأولى | سياسة احتفاظ منفصلة واختبار استعادة شهري. |

بديل أخف تعقيداً هو تشغيل Tozyw على جهاز Windows المحلي وتفعيل النسخ اليومية المشفرة فقط. يبقى هذا خياراً بلا بطاقة إذا كانت الخدمة لا تحتاج وصولاً دائماً من الإنترنت؛ لكنه لا يحقق استمرارية خادم مستقل. لا يُنصح بـRender Free كمصدر لملف SQLite، لأن نظام الملفات المؤقت وسياسة الخمول لا يحققان ديمومة البيانات المطلوبة.

## مكونات الحزمة

| المسار | الوظيفة |
|---|---|
| `scripts/sqlite_backup.py` | يأخذ لقطة عبر SQLite Backup API، يشغل `PRAGMA quick_check`، ثم ينشئ أرشيفاً وmanifest وSHA-256. |
| `scripts/run_backup.sh` | يرفض النسخ غير المشفر ويرفع باستخدام `rclone copy` إلى remote من نوع `crypt` فقط. |
| `scripts/tozyw-release` | ينشر SHA كاملاً محدداً، يحافظ على `data/`، يفحص الصحة، ثم يرجع للرابط السابق عند الفشل. |
| `scripts/bootstrap_host.sh` | تهيئة الخادم مرة واحدة: مستخدم خدمة مقيد ومسارات ووحدات systemd، من دون إنشاء أسرار أو حسابات. |
| `systemd/` | خدمة Streamlit ومهمة النسخ اليومية ومؤقتها. |
| `.github/workflows/deploy-to-oci.yml` | بوابة اختبار قبل النشر وتفعيل النشر على `main` فقط. |
| `config/*.example` | قوالب إعداد بلا قيم سرية. |
| `tests/test_sqlite_backup.py` | اختبارات إنشاء اللقطة واستعادتها ومنع أسماء الأرشيفات غير الآمنة. |

## خطوات الإعداد الأولى

ابدأ بإنشاء مستودع خاص غير مؤرشف، ثم انقل هذه الحزمة إليه داخل `ops/`. لا يمكن إضافة ملفات workflow إلى المستودع `mahwoussa-boop/tozyw` الحالي لأنه مؤرشف للقراءة فقط. بعد إنشاء خادم OCI يدويًا بموافقتك، انسخ بيانات الإنتاج إلى `/srv/tozyw/data` فقط؛ لا تضمّنها داخل image أو مجلد releases.

```bash
# على خادم Ubuntu، من مجلد ops بعد التحقق من مصدره.
sudo OPS_ROOT="$PWD" ./scripts/bootstrap_host.sh

# راجع المسارات وأضف القيم الحقيقية خارج Git، ثم اضبط الصلاحيات.
sudoedit /etc/tozyw/app.env
sudoedit /etc/tozyw/backup.env
sudo chmod 0640 /etc/tozyw/app.env /etc/tozyw/backup.env
```

ثم هيئ rclone مرة واحدة في جلسة يوافق فيها المالك على OAuth أو مفاتيح التخزين. أنشئ أولاً remote أسفل التخزين، ثم remote آخر من نوع `crypt` يلتف حول **مسار مخصص** منه. التشفير في `crypt` يتم محلياً قبل الرفع، ويخزن البيانات مشفرة في الوجهة؛ لكن ملف `rclone.conf` يحتاج هو أيضاً إلى تشفير وحماية لأن إخفاء كلمة المرور فيه وحده ليس حماية كافية [3]. احفظ إعداد rclone وكلمة مروره ضمن OCI Vault أو مدير أسرار مماثل، ولا تضعها في ملفات الدليل المنزلي الدائمة.

```bash
# مثال أسماء فقط؛ لا تنسخ أي كلمة مرور أو token إلى الطرفية أو Git.
sudo -u tozyw rclone config
sudo systemctl enable --now tozyw-backup.timer
sudo systemctl list-timers tozyw-backup.timer
```

بعد التحقق اليدوي من أن ملف `/etc/tozyw/backup.env` يشير إلى `RCLONE_REMOTE=tozyw-crypt:archives`، نفّذ نسخة اختبارية محلية ثم تحقق منها. لا تعتبر عملية الرفع الناجحة دليلاً على قابلية الاستعادة.

```bash
sudo systemctl start tozyw-backup.service
sudo journalctl -u tozyw-backup.service -n 100 --no-pager
```

## النشر الآلي

ضع القيم التالية في بيئة GitHub المحمية باسم `production`، ولا تضعها في YAML. `OCI_SSH_PRIVATE_KEY` مفتاح منفصل لمستخدم `deployer`، و`OCI_SSH_KNOWN_HOSTS` هو بصمة الخادم المثبتة، و`OCI_HOST` و`OCI_DEPLOY_USER` يحددان الاتصال. لا تضف مفاتيح التطبيق إلى GitHub Actions؛ تبقى على الخادم في `/etc/tozyw/app.env` أو في Vault.

| السر | مكانه | نطاقه |
|---|---|---|
| `OCI_SSH_PRIVATE_KEY` | GitHub Environment `production` | النشر فقط، قابل للإبطال. |
| `OCI_SSH_KNOWN_HOSTS` | GitHub Environment `production` | منع استبدال الخادم عند SSH. |
| `OCI_HOST`, `OCI_DEPLOY_USER` | GitHub Environment `production` | عنوان ومستخدم النشر فقط. |
| مفاتيح AI وMake وسلة وسوبابيس | Vault/ملف خادم محمي | التطبيق فقط، لا Workflow. |
| `rclone.conf`, `RCLONE_CONFIG_PASS` | Vault/خادم محمي | وحدة النسخ فقط. |

يجب أن يمرر كل دفع إلى `main` الاختبارات أولاً. بعد النجاح فقط يستدعي التدفق `tozyw-release` مع SHA الكامل نفسه. يقفل السكربت عمليات النشر المتزامنة، ويثبت الاعتمادات في مجلد إصدار جديد، ثم يتحقق من سلامة قواعد الإنتاج من دون أي ترحيل، ويبدل الرابط الرمزي، ويستدعي فحص Streamlit المحلي `/_stcore/health`. إذا فشل التشغيل أو الصحة يعود رابط `current` إلى الإصدار السابق.

## النسخ والاستعادة

تستخدم الحزمة SQLite Backup API بدلاً من نسخ ملف `.db` مباشرة أثناء وجود WAL، ثم تفحص اللقطة بـ`PRAGMA quick_check`. تؤكد وثائق Python أن `Connection.backup()` موجودة لنسخ قاعدة SQLite إلى اتصال آخر، وهو الأساس الذي تعتمده الحزمة هنا [4]. عملية `rclone copy` مقصودة بدلاً من `sync` كي لا يتحول حذف محلي أو فساد عارض إلى حذف للنسخ البعيدة.

> **اختبار الاستعادة الشهري شرط نجاح، وليس مهمة اختيارية.** نزّل أرشيفاً إلى مجلد معزول، تحقق من SHA-256، فك تشفيره عبر remote `crypt`، ثم افتح كل قاعدة وشغّل `PRAGMA quick_check` وفحصاً لصفوف رئيسية قبل إعلان النسخة قابلة للاستعادة.

Cloudflare R2 يتضمن شهرياً 10GB تخزين و1M عملية Class A و10M عملية Class B مجاناً، بينما لا تطبق الطبقة المجانية على Infrequent Access [5]. Backblaze B2 يقدم أول 10GB مخزنة مجاناً، لكن ما بعد ذلك يخضع للتسعير بالاستخدام [6]. لذلك اضبط الاحتفاظ، واحسب حجم الأرشيفات الفعلي، واترك هامش 20%–30% قبل أي حد مجاني؛ لا تعد بأن كل تشغيل سيبقى صفراً بلا قياس حقيقي.

## فحوص محلية منفذة

```bash
python3 -m unittest -v tests/test_sqlite_backup.py
bash -n scripts/run_backup.sh
bash -n scripts/tozyw-release
python3 -m py_compile scripts/sqlite_backup.py
```

نجحت الاختبارات المرفقة في إنشاء قاعدة WAL صغيرة، أخذ لقطة متسقة، أرشفتها، استخراجها، والتحقق من قراءة الصف المستعاد؛ كما تحقق اختبار مستقل من رفض اسم أرشيف يحوي مساراً غير آمن.

## تعريف النجاح قبل الإنتاج

| الاختبار | النتيجة المطلوبة |
|---|---|
| النشر | دفع SHA إلى `main` يمر بالاختبارات وينشر SHA نفسه فقط. |
| الديمومة | إعادة تشغيل `tozyw.service` أو تغيير `current` لا يمس `/srv/tozyw/data`. |
| النسخ | تظهر أرشيفات مشفرة مؤرخة وmanifest وSHA-256 في وجهة مستقلة. |
| الاستعادة | استعادة اختبارية تفتح SQLite وتنجح فيها `PRAGMA quick_check`. |
| الوصول | لا يفتح Streamlit مباشرة للعامة؛ يوضع خلف بوابة وصول خاصة أو proxy مع HTTPS ومصادقة. |

## المراجع

[1] [Oracle Cloud — Always Free Resources](https://docs.oracle.com/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

[2] [Oracle Cloud — Free Tier](https://www.oracle.com/cloud/free/)

[3] [rclone — Crypt](https://rclone.org/crypt/)

[4] [Python — sqlite3.Connection.backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)

[5] [Cloudflare R2 — Pricing](https://developers.cloudflare.com/r2/pricing/)

[6] [Backblaze B2 — Pricing](https://www.backblaze.com/cloud-storage/pricing)

## تفعيل تدفق GitHub Actions

يتطلب إنشاء ملف داخل `.github/workflows/` اعتماد GitHub يملك إذن `workflows` إضافةً إلى `contents`. إلى أن تمنح هذا الإذن، يوجد التدفق محفوظاً كقالب في `ops/templates/deploy-to-oci.yml.template`. بعد تفعيل الإذن، انقله إلى `.github/workflows/deploy-to-oci.yml` في commit مستقل؛ لن تتأثر عملية تشغيل التطبيق أو ديمومة البيانات بتأجيل هذا التفعيل.
