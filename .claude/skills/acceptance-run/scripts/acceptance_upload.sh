#!/usr/bin/env bash
# 验收辅助：上传测试件 → 轮询到终态 → 输出 extraction/conflicts/violations。
# 用法：bash acceptance_upload.sh <测试件路径> [期望 rule_id ...]
# 环境：BASE（默认 http://localhost:8001）。Git Bash 下运行。
set -u
BASE="${BASE:-http://localhost:8001}"
FILE="${1:?用法: $0 <测试件路径> [期望规则id...]}"
EXPECT="${*:2}"

resp=$(curl -s -F "file=@$FILE" "$BASE/api/files/upload")
task=$(echo "$resp" | python -c 'import sys,json
try:
    print(json.load(sys.stdin)["task_id"])
except Exception:
    print("")')
if [ -z "$task" ]; then
  echo "上传失败: $resp"
  exit 1
fi
echo "[task $task] uploaded $(basename "$FILE")"

for i in $(seq 1 90); do
  st=$(curl -s "$BASE/api/tasks/$task" | python -c 'import sys,json
try:
    print(json.load(sys.stdin)["status"])
except Exception:
    print("")')
  case "$st" in
    PENDING|PARSING|EXTRACTING|VALIDATING|ANALYZING) sleep 4 ;;
    *) break ;;
  esac
done
echo "[task $task] final: ${st:-?}"

curl -s "$BASE/api/tasks/$task" | python -c 'import sys,json
d=json.load(sys.stdin)
print("  extraction:", d.get("extraction_status"), "| conflicts:", d.get("conflicts"), "| msg:", (d.get("message") or "")[:60])'

curl -s "$BASE/api/violations?task_id=$task" | python -c 'import sys,json
d=json.load(sys.stdin)
items = d if isinstance(d, list) else d.get("items") or d.get("violations") or []
print("  violations:", len(items))
for v in items:
    print("   ", v.get("rule_id"), v.get("severity"), v.get("confidence"), (v.get("message") or "")[:70])'

if [ -n "$EXPECT" ]; then
  echo "  期望命中 rule_id: $EXPECT"
fi
