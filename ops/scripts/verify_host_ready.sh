#!/usr/bin/env bash
# تحقق غير مدمر من جاهزية خادم Tozyw قبل أو بعد النشر.
# لا يطبع قيم الأسرار ولا ينشئ أو يحذف بيانات.
set -Eeuo pipefail

readonly APP_ENV="${TOZYW_APP_ENV:-/etc/tozyw/app.env}"
readonly BACKUP_ENV="${TOZYW_BACKUP_ENV:-/etc/tozyw/backup.env}"
readonly RELEASE_ENV="${TOZYW_RELEASE_ENV:-/etc/tozyw/release.env}"
readonly SERVICE_NAME="${TOZYW_SERVICE_NAME:-tozyw.service}"
readonly HEALTH_URL="${TOZYW_HEALTH_URL:-http://127.0.0.1:8502/_stcore/health}"

failures=0
warnings=0

ok() { printf 'OK      %s\n' "$*"; }
warn() { printf 'WARNING %s\n' "$*" >&2; warnings=$((warnings + 1)); }
fail() { printf 'FAIL    %s\n' "$*" >&2; failures=$((failures + 1)); }

load_env_file() {
  local file="$1"
  if [[ ! -r "$file" ]]; then
    fail "ملف الإعداد غير قابل للقراءة: $file"
    return
  fi
  # shellcheck disable=SC1090
  source "$file"
  ok "ملف الإعداد حاضر: $file"
}

for command in python3 systemctl curl; do
  command -v "$command" >/dev/null 2>&1 && ok "الأمر متاح: $command" || fail "الأمر غير متاح: $command"
done

load_env_file "$APP_ENV"
load_env_file "$BACKUP_ENV"
load_env_file "$RELEASE_ENV"

: "${DATA_DIR:=}"
: "${TOZYW_DATA_DIR:=$DATA_DIR}"
: "${TOZYW_REPOSITORY_URL:=}"
: "${RCLONE_REMOTE:=}"

if [[ -z "$DATA_DIR" ]]; then
  fail "DATA_DIR غير معرّف في $APP_ENV"
elif [[ ! -d "$DATA_DIR" ]]; then
  fail "مسار البيانات غير موجود: $DATA_DIR"
else
  database_count="$(find "$DATA_DIR" -maxdepth 1 -type f -name '*.db' | wc -l)"
  if [[ "$database_count" -lt 1 ]]; then
    warn "لم يُعثر على قواعد SQLite تحت $DATA_DIR"
  else
    ok "قواعد SQLite المكتشفة: $database_count"
  fi
fi

if [[ "$TOZYW_REPOSITORY_URL" == *OWNER* || -z "$TOZYW_REPOSITORY_URL" ]]; then
  fail "TOZYW_REPOSITORY_URL لم يُضبط على المستودع الفعلي"
elif [[ "$TOZYW_REPOSITORY_URL" == https://*:*@* ]]; then
  fail "رابط المستودع يتضمن بيانات اعتماد؛ استخدم مفتاح SSH محدوداً بدلاً من ذلك"
else
  ok "رابط مستودع الإصدار مضبوط"
fi

if [[ -z "$RCLONE_REMOTE" || "$RCLONE_REMOTE" == *REPLACE* || "$RCLONE_REMOTE" != *crypt* ]]; then
  fail "RCLONE_REMOTE يجب أن يشير إلى remote من نوع crypt"
elif command -v rclone >/dev/null 2>&1; then
  ok "rclone متاح ووجهة النسخ المشفرة مضبوطة"
else
  fail "rclone غير مثبت، لذلك لن تعمل النسخ الاحتياطية"
fi

if systemctl is-enabled --quiet "$SERVICE_NAME"; then
  ok "خدمة التطبيق مفعلة: $SERVICE_NAME"
else
  warn "خدمة التطبيق ليست مفعلة: $SERVICE_NAME"
fi

if systemctl is-active --quiet "$SERVICE_NAME"; then
  ok "خدمة التطبيق تعمل: $SERVICE_NAME"
  if curl --fail --silent --show-error --max-time 15 "$HEALTH_URL" >/dev/null; then
    ok "فحص صحة Streamlit نجح"
  else
    fail "فشل فحص صحة Streamlit: $HEALTH_URL"
  fi
else
  warn "الخدمة ليست عاملة حالياً؛ يُتوقع ذلك قبل أول نشر"
fi

if [[ "$failures" -gt 0 ]]; then
  printf 'النتيجة: %s فشل، %s تحذير. لا تفعّل النشر التلقائي قبل معالجة الإخفاقات.\n' "$failures" "$warnings" >&2
  exit 2
fi

printf 'النتيجة: الخادم جاهز (%s تحذير).\n' "$warnings"
