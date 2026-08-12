# بيئة تشغيل تجريبية لـNorthflank؛ لا تتضمن بيانات SQLite الحية أو الأسرار.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    DATA_DIR=/var/tozyw-demo/data \
    TOZYW_RESULTS_URL=https://github.com/mahwoussa-boop/tozyw-northflank-demo/releases/download/untagged-f43ddbc5e81085288e0e/tozyw-results-complete.zip

WORKDIR /app

RUN addgroup --system tozyw && adduser --system --ingroup tozyw --home /app tozyw

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY --chown=tozyw:tozyw . .
RUN mkdir -p "$DATA_DIR" \
    && python restore_data_snapshot.py "$DATA_DIR" "$TOZYW_RESULTS_URL" \
    && chown -R tozyw:tozyw /app /var/tozyw-demo

USER tozyw
EXPOSE 7860

# Northflank supplies PORT. المنفذ 7860 هو البديل المحلي فقط.
CMD ["sh", "-c", "streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-7860} --server.headless=true --browser.gatherUsageStats=false"]
