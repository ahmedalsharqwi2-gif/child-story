"""يفحص عمر YT_REFRESH_TOKEN (من تاريخ آخر تجديد ناجح، المسجّل بمعرفة
publish_youtube_direct.py في state/yt_token_last_success.json)، ولو قرب
على الحد الأقصى لصلاحية توكن تطبيق في وضع Testing عند Google (7 أيام)،
يفتح GitHub Issue تحذيري في الريبو — GitHub بيبعت إشعار/إيميل تلقائي لأي
حد بيراقب الريبو (watching) بمجرد فتح Issue جديد، فده كافي كتنبيه من غير
أي إعداد إضافي.

لا يفشل الـ workflow أبدًا (حتى لو فشل فتح الـ Issue لأي سبب) — ده سكريبت
تنبيه بحت، مش جزء من خط النشر نفسه.

المتغيرات المطلوبة (متوفرة تلقائيًا في GitHub Actions):
    GITHUB_TOKEN         (بصلاحية issues: write)
    GITHUB_REPOSITORY

اختياري:
    TOKEN_WARNING_THRESHOLD_DAYS   افتراضي: 5 (يوم أمان قبل حد الـ7 أيام)

الاستخدام:
    python scripts/check_youtube_token_age.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
TOKEN_STATE_PATH = ROOT_DIR / "state" / "yt_token_last_success.json"

GITHUB_API_BASE = "https://api.github.com"
# اسم عنوان الـ Issue ثابت عمدًا، عشان الفحص عن وجود Issue مفتوح قبل كده
# (تجنّب فتح نسخة جديدة كل يوم) يكون بحث نصي بسيط وموثوق.
ISSUE_TITLE = "🔔 توكن يوتيوب هيخلص قريب — لازم تجديد"
THRESHOLD_DAYS = float(os.environ.get("TOKEN_WARNING_THRESHOLD_DAYS", "5"))
# نفس حد الـ7 أيام اللي Google بتفرضه على تطبيق في وضع Testing.
GOOGLE_TESTING_LIMIT_DAYS = 7


def github_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def find_open_warning_issue(repo: str, headers: dict) -> int | None:
    resp = requests.get(
        f"{GITHUB_API_BASE}/repos/{repo}/issues",
        headers=headers,
        params={"state": "open", "labels": "", "per_page": 20},
        timeout=20,
    )
    resp.raise_for_status()
    for issue in resp.json():
        if issue.get("title") == ISSUE_TITLE:
            return issue["number"]
    return None


def open_warning_issue(repo: str, headers: dict, age_days: float, last_success_utc: str) -> None:
    remaining = max(GOOGLE_TESTING_LIMIT_DAYS - age_days, 0)
    body = (
        f"آخر تجديد ناجح لـ `YT_REFRESH_TOKEN` كان منذ **{age_days:.1f} يوم** "
        f"({last_success_utc} UTC).\n\n"
        f"Google بتلغي صلاحية توكن أي تطبيق في وضع **Testing** تلقائيًا بعد "
        f"**{GOOGLE_TESTING_LIMIT_DAYS} أيام** من آخر إصدار — يعني متبقي "
        f"تقريبًا **{remaining:.1f} يوم** قبل ما نشر الفيديو الكامل على "
        f"يوتيوب يفشل برسالة `invalid_grant`.\n\n"
        "### الحل\n"
        "1. شغّل `get_refresh_token.py` تاني على جهازك (نفس الخطوات "
        "المعتادة) للحصول على `refresh_token` جديد.\n"
        "2. حدّث قيمة `YT_REFRESH_TOKEN` في GitHub Secrets بالقيمة "
        "الجديدة.\n\n"
        "أو، لتفادي الحاجة لتكرار الخطوة دي كل أسبوع بشكل دائم: انشر "
        "تطبيق OAuth من وضع Testing لوضع Production من صفحة "
        "`console.cloud.google.com/apis/credentials/consent` (تاب "
        "Audience → PUBLISH APP)، بعد ما تتأكد إن تاب Branding معبّى "
        "بالكامل (App name وUser support email على الأقل).\n\n"
        "_(هذا الـ Issue اتفتح تلقائيًا بمعرفة scripts/check_youtube_token_age.py.)_"
    )
    resp = requests.post(
        f"{GITHUB_API_BASE}/repos/{repo}/issues",
        headers=headers,
        json={"title": ISSUE_TITLE, "body": body},
        timeout=20,
    )
    resp.raise_for_status()
    print(f"✅ اتفتح تنبيه: {resp.json().get('html_url')}")


def close_warning_issue_if_fresh(repo: str, headers: dict, issue_number: int) -> None:
    """لو التوكن اتجدّد بنجاح (يعني قرب الانتهاء زال)، نقفل أي تنبيه قديم
    كان مفتوح، عشان الـ Issues متفضلش متراكمة من غير داعي."""
    requests.patch(
        f"{GITHUB_API_BASE}/repos/{repo}/issues/{issue_number}",
        headers=headers,
        json={"state": "closed"},
        timeout=20,
    ).raise_for_status()
    print(f"✅ اتقفل تنبيه قديم (رقم #{issue_number}) بعد تجديد ناجح للتوكن.")


def main() -> None:
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not github_token or not repo:
        print("⚠️ GITHUB_TOKEN أو GITHUB_REPOSITORY غير متوفرين؛ تخطي فحص عمر التوكن.")
        return

    if not TOKEN_STATE_PATH.exists():
        print("ℹ️ مفيش ملف تسجيل نجاح لتوكن يوتيوب لسه (أول تشغيلة غالبًا)؛ تخطي الفحص.")
        return

    try:
        data = json.loads(TOKEN_STATE_PATH.read_text(encoding="utf-8"))
        last_success_utc = data["last_success_utc"]
        last_success = datetime.fromisoformat(last_success_utc)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"⚠️ تعذّر قراءة {TOKEN_STATE_PATH}: {exc}؛ تخطي الفحص.")
        return

    age_days = (datetime.now(timezone.utc) - last_success).total_seconds() / 86400
    print(f"ℹ️ عمر آخر تجديد ناجح لتوكن يوتيوب: {age_days:.1f} يوم.")

    headers = github_headers(github_token)
    try:
        existing_issue = find_open_warning_issue(repo, headers)
        if age_days >= THRESHOLD_DAYS:
            if existing_issue is None:
                open_warning_issue(repo, headers, age_days, last_success_utc)
            else:
                print(f"ℹ️ تنبيه مفتوح أصلًا (رقم #{existing_issue})؛ مفيش داعي لواحد جديد.")
        elif existing_issue is not None:
            close_warning_issue_if_fresh(repo, headers, existing_issue)
    except requests.HTTPError as exc:
        # فشل فتح/قفل الـ Issue متعمّد ميوقفش الـ workflow — ده سكريبت
        # تنبيه بحت.
        print(f"⚠️ فشل التواصل مع GitHub Issues API (غير خطير): {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ فحص عمر توكن يوتيوب فشل بشكل غير متوقع (غير خطير): {exc}")
        sys.exit(0)
