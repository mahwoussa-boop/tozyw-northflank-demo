#!/usr/bin/env bash
# يُشغّل مرة واحدة بصلاحية root من نسخة عمل موثوقة، بعد نقل محتويات هذه الحزمة إلى <repo>/ops.
# لا ينشئ حسابات سحابية ولا مفاتيح ولا يفتح منافذ شبكة عامة.
set -Eeuo pipefail
umask 027

readonly APP_ROOT="${TOZYW_APP_ROOT:-/opt/tozyw}"
readonly DATA_ROOT="${TOZYW_DATA_ROOT:-/srv/tozyw}"
readonly OPS_ROOT="${OPS_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
readonly APP_USER="${TOZYW_APP_USER:-tozyw}"
readonly DEPLOY_USER="${TOZYW_DEPLOY_USER:-deployer}"
readonly RELEASE_HELPER_SOURCE="$OPS_ROOT/scripts/tozyw-release"

[[ "${EUID}" -eq 0 ]] || { echo 'شغّل السكربت بصلاحية root.' >&2; exit 2; }
[[ -f "$RELEASE_HELPER_SOURCE" ]] || { echo "لم يُعثر على $RELEASE_HELPER_SOURCE" >&2; exit 2; }
[[ -f "$OPS_ROOT/systemd/tozyw.service" ]] || { echo 'ملفات systemd غير موجودة ضمن الحزمة.' >&2; exit 2; }

apt-get update
apt-get install --yes --no-install-recommends git python3 python3-venv rclone curl ca-certificates

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$DATA_ROOT" --shell /usr/sbin/nologin "$APP_USER"
id -u "$DEPLOY_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$DEPLOY_USER"

install -d -o "$APP_USER" -g "$APP_USER" -m 0750 \
  "$APP_ROOT/releases" \
  "$APP_ROOT/staging" \
  "$DATA_ROOT/data" \
  "$DATA_ROOT/.cache" \
  /var/backups/tozyw \
  /var/log/tozyw
install -d -o root -g "$APP_USER" -m 0750 /etc/tozyw

install -o root -g root -m 0750 "$RELEASE_HELPER_SOURCE" /usr/local/sbin/tozyw-release
install -o root -g root -m 0644 "$OPS_ROOT/systemd/tozyw.service" /etc/systemd/system/tozyw.service
install -o root -g root -m 0644 "$OPS_ROOT/systemd/tozyw-backup.service" /etc/systemd/system/tozyw-backup.service
install -o root -g root -m 0644 "$OPS_ROOT/systemd/tozyw-backup.timer" /etc/systemd/system/tozyw-backup.timer

# نموذج فقط؛ لا يطغى على إعداد حقيقي وقد يحتوي على أسرار سبق إعدادها.
[[ -f /etc/tozyw/app.env ]] || install -o root -g "$APP_USER" -m 0640 "$OPS_ROOT/config/app.env.example" /etc/tozyw/app.env
[[ -f /etc/tozyw/backup.env ]] || install -o root -g "$APP_USER" -m 0640 "$OPS_ROOT/config/backup.env.example" /etc/tozyw/backup.env
[[ -f /etc/tozyw/release.env ]] || install -o root -g root -m 0600 "$OPS_ROOT/config/release.env.example" /etc/tozyw/release.env

cat > /etc/sudoers.d/tozyw-deployer <<'SUDOERS'
# النشر محصور في script واحد؛ لا تمنح مستخدم النشر طرفية root عامة.
deployer ALL=(root) NOPASSWD: /usr/local/sbin/tozyw-release [0-9a-fA-F]*
SUDOERS
chmod 0440 /etc/sudoers.d/tozyw-deployer
visudo -cf /etc/sudoers.d/tozyw-deployer

systemctl daemon-reload
systemctl enable tozyw.service tozyw-backup.timer

echo 'إعداد الخادم الأساسي مكتمل.'
echo 'قبل التشغيل: انقل قواعد SQLite إلى /srv/tozyw/data، واضبط /etc/tozyw/{app,backup,release}.env، ثم نفّذ rclone config يدوياً بموافقة المالك.'
