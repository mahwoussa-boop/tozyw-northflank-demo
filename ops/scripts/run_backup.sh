#!/usr/bin/env bash
# يشغّل من systemd على الخادم نفسه الذي يملك /srv/tozyw/data.
# لا يضع أسراراً في Git ولا يسجّلها في المخرجات.
set -Eeuo pipefail
umask 077

: "${TOZYW_DATA_DIR:=/srv/tozyw/data}"
: "${TOZYW_BACKUP_DIR:=/var/backups/tozyw}"
: "${TOZYW_BACKUP_RETENTION_DAYS:=30}"
: "${TOZYW_BACKUP_LABEL:=tozyw}"
: "${TOZYW_DATABASES:=pricing_v18.db perfume_pricing.db}"
: "${RCLONE_CONFIG:=/etc/tozyw/rclone.conf}"
: "${RCLONE_REMOTE:?يجب تعريف RCLONE_REMOTE كمسار remote من نوع crypt في rclone}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_PYTHON="${BACKUP_PYTHON:-python3}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

for command in "$BACKUP_PYTHON" rclone; do
  command -v "$command" >/dev/null 2>&1 || fail "الأمر المطلوب غير متاح: $command"
done

[[ -d "$TOZYW_DATA_DIR" && -r "$TOZYW_DATA_DIR" ]] || fail "مسار بيانات التطبيق غير قابل للقراءة: $TOZYW_DATA_DIR"
[[ -f "$RCLONE_CONFIG" && -r "$RCLONE_CONFIG" ]] || fail "ملف إعداد rclone غير قابل للقراءة: $RCLONE_CONFIG"
[[ -n "${RCLONE_CONFIG_PASS:-}" ]] || fail "RCLONE_CONFIG_PASS غير معرّف؛ لا يجوز رفع نسخة غير مشفرة"

# أسماء القواعد متعمدة نسبية كي لا يسمح ملف البيئة بقراءة ملفات عشوائية خارج data/.
backup_arguments=()
for database in $TOZYW_DATABASES; do
  case "$database" in
    ''|*'/'*|*'..'*) fail "اسم قاعدة غير آمن في TOZYW_DATABASES: $database" ;;
  esac
  backup_arguments+=(--database "$TOZYW_DATA_DIR/$database")
done

mkdir -p "$TOZYW_BACKUP_DIR"
"$BACKUP_PYTHON" "$SCRIPT_DIR/sqlite_backup.py" \
  "${backup_arguments[@]}" \
  --output-dir "$TOZYW_BACKUP_DIR" \
  --retention-days "$TOZYW_BACKUP_RETENTION_DAYS" \
  --label "$TOZYW_BACKUP_LABEL"

# النقل Copy لا Sync: لا يحذف الأرشيفات البعيدة عند حذف نسخة محلية أو تلفها.
rclone copy \
  --config "$RCLONE_CONFIG" \
  --immutable \
  --checksum \
  --retries 3 \
  --low-level-retries 10 \
  --log-level NOTICE \
  --include "${TOZYW_BACKUP_LABEL}-sqlite-*.tar.gz" \
  --include "${TOZYW_BACKUP_LABEL}-sqlite-*.manifest.json" \
  --include "${TOZYW_BACKUP_LABEL}-sqlite-*.sha256" \
  "$TOZYW_BACKUP_DIR" "$RCLONE_REMOTE"

printf 'Backup upload completed successfully.\n'
