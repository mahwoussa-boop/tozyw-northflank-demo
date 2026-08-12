# جاهزية نشر Tozyw على Oracle Always Free

> هذا الدليل جاهز للتنفيذ بعد تسجيل دخول مالك الحساب إلى Oracle Cloud. لا يحتوي على أسرار أو بيانات بطاقة أو كلمات مرور.

## حالة المستودع

المستودع الخاص المستهدف هو `mahwoussa-boop/tozyw-production` والفرع المستهدف هو `main`. يحتوي على مصدر Tozyw، ومجلد `ops/` لملفات تشغيل النظام والنسخ، وقالب تدفق النشر في `ops/templates/deploy-to-oci.yml.template`.

لا يمكن تفعيل القالب تلقائياً قبل منح اعتماد GitHub إذن `workflows`؛ لذلك يبقى القالب غير نشط عمداً، ولا يمنع ذلك تشغيل التطبيق أو حماية بياناته على الخادم.

## إعداد Oracle مرة واحدة

أنشئ مثيل Compute في **المنطقة المنزلية** باستخدام Ubuntu ARM64 وshape `VM.Standard.A1.Flex` ضمن حد Always Free. ابدأ بـ`1 OCPU` و`6GB RAM`، ثم زد الموارد ضمن سقف الحساب إذا أثبت القياس أن التحليل يحتاج ذلك. اجعل القرص وموارد الشبكة ضمن Always Free فقط، ولا تضف مورداً مدفوعاً أو قاعدة بيانات مدارة.

أنشئ قاعدة جدار ناري ضيقة: افتح SSH من عنوان الإدارة الموثوق فقط، ولا تكشف Streamlit على المنفذ `8502` مباشرة. مرّر الوصول لاحقاً عبر proxy مع HTTPS ومصادقة، أو اجعله وصولاً خاصاً للفريق.

## إعداد الخادم

بعد ربط SSH الآمن من جهاز المالك، انسخ بيانات SQLite الحية إلى `/srv/tozyw/data`، ثم شغّل:

```bash
cd /path/to/tozyw-production/ops
sudo OPS_ROOT="$PWD" ./scripts/bootstrap_host.sh
```

راجع ملفات البيئة التالية على الخادم فقط، ثم اضبطها بصلاحية مناسبة. لا تدفعها إلى Git:

| الملف | المحتوى |
|---|---|
| `/etc/tozyw/app.env` | `DATA_DIR=/srv/tozyw/data` ومفاتيح مزودي التطبيق عند الحاجة. |
| `/etc/tozyw/backup.env` | مسار قواعد SQLite ووجهة `rclone crypt` وكلمة حماية إعداد rclone. |
| `/etc/tozyw/release.env` | رابط المستودع الخاص ومسارات التطبيق والبيانات. |

## وصول الخادم إلى المستودع

أنشئ مفتاح SSH مستقل على الخادم لمستخدم النشر، ثم أضفه إلى المستودع الخاص كمفتاح نشر **read-only**. استخدم رابط SSH في `TOZYW_REPOSITORY_URL` ضمن `/etc/tozyw/release.env`. يبقى مفتاح النشر على الخادم فقط؛ لا تضعه في GitHub Actions أو في Git أو ضمن ملفات التطبيق.

## النسخ الاحتياطي

أنشئ remote تخزين أساسي ثم remote من نوع `crypt` يلتف حول مسار مخصص له. افحص نسخة تجريبية قبل الاعتماد على الجدولة:

```bash
sudo systemctl start tozyw-backup.service
sudo journalctl -u tozyw-backup.service -n 100 --no-pager
sudo systemctl enable --now tozyw-backup.timer
```

النجاح المطلوب: تنشئ المهمة أرشيفاً وmanifest وبصمة SHA-256 بعد لقطة SQLite متسقة، ثم تنسخها إلى وجهة مشفرة مستقلة.

## تفعيل النشر التلقائي لاحقاً

بعد تجهيز خادم Oracle، امنح الاعتماد المستخدم مع المستودع إذن GitHub `workflows`، ثم انقل القالب:

```bash
mkdir -p .github/workflows
mv ops/templates/deploy-to-oci.yml.template .github/workflows/deploy-to-oci.yml
git add .github/workflows/deploy-to-oci.yml
git commit -m "Enable tested deployment to OCI"
git push
```

أضف في بيئة GitHub المحمية `production` أسرار النشر فقط: `OCI_HOST` و`OCI_DEPLOY_USER` و`OCI_SSH_PRIVATE_KEY` و`OCI_SSH_KNOWN_HOSTS`. لا تضف مفاتيح AI أو قواعد SQLite أو ملف rclone إلى GitHub Actions.

## التحقق النهائي

يعد النشر ناجحاً عندما تعمل الخدمة محلياً، ويستجيب `/_stcore/health`، وتبقى البيانات بعد restart، وتنجح استعادة نسخة مشفرة في مسار معزول.

## المراجع

[1] [Oracle Cloud — موارد Always Free](https://docs.oracle.com/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

[2] [GitHub — حماية الأسرار في Actions](https://docs.github.com/actions/security-guides/using-secrets-in-github-actions)
