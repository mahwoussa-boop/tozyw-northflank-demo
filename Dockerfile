# البيانات لا تُخبز في الصورة: تُستورد مرة واحدة عند أول إقلاع إلى الـVolume
# الدائم المثبّت على DATA_DIR، بضبط TOZYW_RESULTS_IMPORT_URL وTOZYW_RESULTS_REVISION
# كمتغيّرات خدمة (انظر runtime_data_bootstrap.py و ops/NORTHFLANK_DEMO.md).
#
# لا تضع رابط أرشيف هنا: رابط الإصدار يتغيّر عند نشر المسودة، فيصير 404 ويُسقط
# البناء كله. أُزيل تنزيل وقت البناء بعد أن أسقط بناءين بهذا السبب بالضبط.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    DATA_DIR=/data

WORKDIR /app

RUN addgroup --system tozyw && adduser --system --ingroup tozyw --home /app tozyw

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY --chown=tozyw:tozyw . .

# Northflank قد يركّب Volume مملوكًا لـroot عند أول تشغيل؛ التشغيل بصلاحية root
# يسمح بتهيئة /data ونسخ البذور دون فشل PermissionError. ملفات التطبيق نفسها
# مملوكة للمستخدم المخصص tozyw من مرحلة البناء.
RUN mkdir -p "$DATA_DIR" && chown -R tozyw:tozyw /app /var/tozyw-demo

EXPOSE 7860

# Northflank يقدّم PORT. المنفذ 7860 هو البديل المحلي فقط.
CMD ["sh", "-c", "python runtime_data_bootstrap.py && exec streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-7860} --server.headless=true --browser.gatherUsageStats=false"]
