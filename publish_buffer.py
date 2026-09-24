"""نشر أصول الحلقة الجديدة عبر Buffer.

الترتيب:
- الفيديو الكامل الأفقي أولًا.
- short_1 بعد FULL_TO_SHORT_1_HOURS (افتراضيًا 3 ساعات).
- short_2 بعد FULL_TO_SHORT_2_HOURS (افتراضيًا 7 ساعات).

لا يوجد هنا منطق part1/part2. كل أصل يرفع وينشر Native على كل قناة.

=== إصلاح جديد (يوتيوب / انستجرام) ===
1) يوتيوب: Buffer's `YoutubePostMetadataInput` ليس فيه حقل `type` إطلاقًا
   (تأكدنا من توثيق Buffer الرسمي على developers.buffer.com). حقل `type`
   موجود بس في نوع الـ output (`YoutubePostMetadata`) اللي بيوصف حالة
   البوست بعد النشر، مش نوع الـ input اللي بنستخدمه وقت الإنشاء. إرساله
   كان بيرجّع "Field 'type' is not defined by type 'YoutubePostMetadataInput'"
   لكل الأصول (الفيديو الكامل + الشورتس) بنفس الرسالة بالظبط. الحل: حذف
   الحقل من فرع يوتيوب خالص. يوتيوب بيحدد تلقائيًا لو الفيديو "Short" من
   مواصفات الملف نفسه (نسبة أبعاد رأسية/مربعة + مدة قصيرة)، مش من أي حقل
   بتبعته لـ Buffer.
2) انستجرام: `InstagramPostMetadataInput.type` حقل `PostType!` إلزامي،
   وقيمه المسموحة (enum PostType) هي: post, reel, story, short, carousel,
   event, offer, ghost_post, thread, whats_new — القيمة "video" مش من
   ضمنهم، فكانت هترجع خطأ enum مشابه لو اتبعتت فعلًا. الحل: "post"
   للفيديو الكامل (فيديو Feed عادي)، و"reel" للشورتس (زي ما هو).
3) انستجرام كان مش بيتنشرله خالص (ولا حتى محاولة فاشلة في اللوج) لأن
   channel_ids() كانت بتعتمد على متغير BUFFER_CHANNEL_ID المنفصل، ولو
   القيمة دي متظبطة يدويًا وناقص منها معرّف انستجرام، بيرجع بس يوتيوب/
   فيسبوك ويتجاهل انستجرام بصمت تام من غير أي رسالة خطأ. الحل: channel_ids()
   بقت تعتمد دايمًا على CHANNEL_SERVICES (المبني من BUFFER_YOUTUBE_CHANNEL_ID
   / BUFFER_FACEBOOK_CHANNEL_ID / BUFFER_INSTAGRAM_CHANNEL_ID مباشرة)، بدل
   القائمة المجمّعة القديمة اللي ممكن تفضل من غير تحديث. لسه لازم تتأكد إن
   BUFFER_INSTAGRAM_CHANNEL_ID نفسه متظبط في GitHub Secrets بمعرّف قناة
   انستجرام الصحيح — لو الـ secret ده مش موجود أصلًا، مفيش كود يقدر يعوّض
   عنه.
4) يوتيوب كان بيرفض الفيديو الكامل برسالة:
   "Video must be no longer than 3 minutes for YouTube Shorts." +
   "Video must be vertical (portrait orientation) for YouTube Shorts."
   رغم إن الفيديو أفقي فعلًا (تم التأكد من أبعاده قبل الرفع). السبب:
   وجود هاشتاج #Shorts/#Short جوه نص البوست (منقول من caption الحلقة،
   اللي بيتشارك بين الفيديو الكامل والشورتس) بيخلي يوتيوب يحاول يصنّف
   الفيديو تلقائيًا كـ Short بغض النظر عن أبعاده الحقيقية، فيرفضه لما
   يفشل شروط الـ Shorts. الحل: دالة strip_shorts_hashtag() بتشيل أي
   هاشتاج #Shorts/#Short من نص البوست الخاص بالفيديو الكامل تحديدًا
   (caption + hashtags المجمّعة) قبل إرساله لـ Buffer.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
EPISODE_PATH = ROOT_DIR / "state" / "current_episode.json"
OUTPUT_DIR = ROOT_DIR / "output"
BUFFER_GRAPHQL_API = "https://api.buffer.com"
GITHUB_API = "https://api.github.com"
RELEASE_TAG = "media-assets"
CHANNEL_PENDING_LIMIT = int(os.environ.get("CHANNEL_PENDING_LIMIT", "10"))
ENABLE_PREFLIGHT_CHECK = os.environ.get("ENABLE_PREFLIGHT_CHECK", "true").lower() != "false"

FULL_TO_SHORT_1_HOURS = float(os.environ.get("FULL_TO_SHORT_1_HOURS", "3"))
FULL_TO_SHORT_2_HOURS = float(os.environ.get("FULL_TO_SHORT_2_HOURS", "7"))
# الفيديو الكامل (طويل) أداؤه أفضل مساءً لما المشاهد يكون عنده وقت فراغ
# فعلي، بعكس الشورتس اللي أداؤها أفضل صبحًا/ضهرًا أثناء تصفّح سريع —
# فمش منطقي ينشر الفيديو الكامل فورًا وقت التشغيل (صباحًا عادة) زي ما
# كان قديمًا (تأخير=0). القيمة الافتراضية هنا بتفترض تشغيل الـ workflow
# صباحًا وتؤجل النشر الفعلي لنفس اليوم مساءً؛ لو غيّرت معاد الـ cron،
# اضبط القيمة دي معاه.
FULL_VIDEO_DELAY_HOURS = float(os.environ.get("FULL_VIDEO_DELAY_HOURS", "0"))

# يوتيوب بيعامل أي فيديو نصّه فيه #Shorts/#Short كـ Short تلقائيًا بغض النظر
# عن أبعاده الحقيقية. لازم نشيله من نص الفيديو الكامل حتى لا يُرفض برسالة
# "Video must be no longer than 3 minutes / must be vertical for YouTube Shorts".
SHORTS_HASHTAG_RE = re.compile(r"(?<!\w)#[Ss]hort[s]?\b")


def strip_shorts_hashtag(text: str) -> str:
    """يشيل أي هاشتاج #Shorts/#Short من نص الفيديو الكامل حتى لا يصنّفه
    يوتيوب تلقائيًا كـ Short (سلوك معروف: وجود #Shorts في الوصف بيخلي
    يوتيوب يحاول يعامل الفيديو كـ Short بغض النظر عن أبعاده الحقيقية)،
    فيرفض الرفع بالخطأين:
    "Video must be no longer than 3 minutes for YouTube Shorts."
    "Video must be vertical (portrait orientation) for YouTube Shorts."
    """
    return SHORTS_HASHTAG_RE.sub("", text).strip()


def build_channel_services() -> dict[str, str]:
    result = {}
    for env_name, service in {
        "BUFFER_YOUTUBE_CHANNEL_ID": "youtube",
        "BUFFER_FACEBOOK_CHANNEL_ID": "facebook",
        "BUFFER_INSTAGRAM_CHANNEL_ID": "instagram",
    }.items():
        value = os.environ.get(env_name, "").strip()
        if value:
            result[value] = service
    # توافق مع المعرّفات القديمة الموجودة في النسخة السابقة.
    result.setdefault("6aaa8778ea19ca0bde57da16", "youtube")
    result.setdefault("6aaa853fea19ca0bde57b5f7", "facebook")
    return result


CHANNEL_SERVICES = build_channel_services()

CREATE_POST_MUTATION = """
mutation CreatePost($text: String!, $channelId: ChannelId!, $videoUrl: String!, $metadata: PostInputMetaData, $dueAt: DateTime!) {
  createPost(input: {
    text: $text
    channelId: $channelId
    schedulingType: automatic
    mode: customScheduled
    dueAt: $dueAt
    metadata: $metadata
    assets: [{ video: { url: $videoUrl } }]
  }) {
    ... on PostActionSuccess { post { id text dueAt } }
    ... on MutationError { message }
  }
}
"""
GET_ORGANIZATIONS_QUERY = "query GetOrganizations { account { organizations { id name } } }"
GET_PENDING_QUERY = """
query GetPendingPosts($organizationId: OrganizationId!, $channelId: ChannelId!) {
  posts(first: 10, input: {organizationId: $organizationId, filter: {status: [scheduled], channelIds: [$channelId]}}) {
    edges { node { id } }
  }
}
"""


def graphql(query: str, variables: dict, api_key: str) -> dict:
    response = requests.post(
        BUFFER_GRAPHQL_API,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        json={"query": query, "variables": variables},
        timeout=40,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    return data.get("data", {})


def github_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def upload_media(video_path: Path, token: str) -> str:
    repo = os.environ.get("MEDIA_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("MEDIA_REPOSITORY أو GITHUB_REPOSITORY غير موجود.")
    release_response = requests.get(f"{GITHUB_API}/repos/{repo}/releases/tags/{RELEASE_TAG}", headers=github_headers(token), timeout=20)
    if release_response.status_code == 404:
        release_response = requests.post(
            f"{GITHUB_API}/repos/{repo}/releases",
            headers=github_headers(token),
            json={"tag_name": RELEASE_TAG, "name": "Media Assets", "body": "Temporary hosting for Buffer assets.", "prerelease": True},
            timeout=20,
        )
    release_response.raise_for_status()
    release = release_response.json()
    asset_name = f"video_{video_path.stem}_{os.environ.get('GITHUB_RUN_ID', 'local')}.mp4"
    with video_path.open("rb") as handle:
        response = requests.post(
            release["upload_url"].split("{")[0], headers={**github_headers(token), "Content-Type": "video/mp4"},
            params={"name": asset_name}, data=handle, timeout=300,
        )
    response.raise_for_status()
    return response.json()["browser_download_url"]


def video_dimensions(video_path: Path) -> tuple[int, int]:
    """يقرأ أبعاد الفيديو قبل رفعه حتى لا يصل الكامل إلى YouTube كـShort."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=p=0:s=x", str(video_path)],
        capture_output=True, text=True, check=False,
    )
    try:
        width, height = result.stdout.strip().split("x", 1)
        return int(width), int(height)
    except (ValueError, AttributeError):
        raise RuntimeError(f"تعذر قراءة أبعاد الفيديو: {video_path}")


def metadata_for(channel_id: str, asset_type: str, title: str) -> dict | None:
    service = CHANNEL_SERVICES.get(channel_id)
    if service == "youtube":
        # لا نرسل isAiGenerated=true تلقائيًا ولا نضع نصًا يعلن ذلك؛ هذا الحقل
        # إفصاح رسمي وليس وسمًا شكليًا. إذا كان مطلوبًا في حسابك، أضفه صراحةً
        # عبر إعداد مستقل بعد مراجعة سياسة YouTube.
        # ملحوظة: بدون "type" — YoutubePostMetadataInput مفيهوش الحقل ده
        # أصلًا (شوف الشرح في أعلى الملف).
        return {"youtube": {
            "title": title[:100] or "Horror Episode",
            "categoryId": "24",
            "privacy": "public",
            "madeForKids": False,
            "notifySubscribers": asset_type == "full_video",
        }}
    if service == "facebook":
        # قيم Facebook الرسمية هي post / reel / story؛ لا توجد قيمة video.
        return {"facebook": {"type": "reel" if asset_type == "short" else "post"}}
    if service == "instagram":
        # كل الفيديوهات (الكامل والشورتس) بتتبعت كـ"reel" — لا "post"،
        # لأن نوع "post" عند Buffer بيفرض حد قديم 60 ثانية لفيديوهات
        # Instagram (رسالة الخطأ: "Video must be no longer than 1 minute
        # for Instagram Posts")، بينما Instagram Graph API الرسمي بيسمح
        # بحد 15 دقيقة (900 ثانية) لـReels — والفيديو الكامل (~6 دقايق)
        # داخل الحد ده براحة. shouldShareToFeed=True بيخلي الـreel يظهر
        # في الـFeed العادي كمان زي منشور فيديو تقليدي.
        return {"instagram": {"type": "reel", "shouldShareToFeed": True}}
    return None


def build_post_text(service: str, asset_type: str, title: str, caption: str, full_url: str | None = None) -> str:
    hashtags = " ".join(dict.fromkeys(re.findall(r"(?<!\w)#\S+", caption)))
    if asset_type == "full_video":
        # مهم: نشيل #Shorts/#Short من كابشن ومن الهاشتاجات المجمّعة للفيديو
        # الكامل، وإلا يوتيوب هيحاول يصنّفه Short ويرفضه لأنه أفقي وأطول
        # من 3 دقايق (شوف strip_shorts_hashtag بالتفصيل في أعلى الملف).
        clean_caption = strip_shorts_hashtag(caption)
        clean_hashtags = strip_shorts_hashtag(hashtags)
        parts = [title.strip(), clean_caption.strip()]
        if clean_hashtags and clean_hashtags not in "\n".join(parts):
            parts.append(clean_hashtags)
        return "\n\n".join(part for part in parts if part).strip()
    elif service == "youtube":
        parts = [f"{title.strip()} — مقتطف", "عايز تعرف النهاية؟ شاهد الحلقة كاملة على YouTube."]
        if full_url:
            parts.append(f"🔗 الحلقة الكاملة: {full_url}")
    elif service == "facebook":
        parts = [f"{title.strip()} — مقتطف", "عايز تعرف النهاية؟ شاهد الحلقة كاملة على صفحتنا."]
        if full_url:
            parts.append(f"🔗 الحلقة الكاملة: {full_url}")
    else:
        parts = [f"{title.strip()} — مقتطف", "عايز تعرف النهاية؟ الحلقة كاملة على صفحتنا."]
    if hashtags and hashtags not in "\n".join(parts):
        parts.append(hashtags)
    return "\n\n".join(part for part in parts if part).strip()


def iso_after(hours: float) -> str:
    due = datetime.now(timezone.utc) + timedelta(hours=hours, minutes=1 if hours == 0 else 0)
    return due.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def organization_id(api_key: str) -> str:
    organizations = graphql(GET_ORGANIZATIONS_QUERY, {}, api_key).get("account", {}).get("organizations", [])
    if not organizations:
        raise RuntimeError("لم يتم العثور على Buffer organization.")
    return organizations[0]["id"]


def pending_count(org_id: str, channel_id: str, api_key: str) -> int:
    data = graphql(GET_PENDING_QUERY, {"organizationId": org_id, "channelId": channel_id}, api_key)
    return len(data.get("posts", {}).get("edges", []))


def create_post(video_url: str, text: str, title: str, channel_id: str, api_key: str, due_at: str, asset_type: str) -> dict:
    variables = {"text": text, "channelId": channel_id, "videoUrl": video_url, "metadata": metadata_for(channel_id, asset_type, title), "dueAt": due_at}
    data = graphql(CREATE_POST_MUTATION, variables, api_key)
    result = data.get("createPost") or {}
    if result.get("message"):
        raise RuntimeError(result["message"])
    if not result.get("post"):
        raise RuntimeError(f"لم يُرجع Buffer منشورًا: {result}")
    return result["post"]


def channel_ids() -> list[str]:
    # بقت دايمًا بترجع كل القنوات المعرّفة فعليًا (من BUFFER_YOUTUBE_CHANNEL_ID
    # / BUFFER_FACEBOOK_CHANNEL_ID / BUFFER_INSTAGRAM_CHANNEL_ID) بدل ما تعتمد
    # على BUFFER_CHANNEL_ID المنفصل، اللي كان ممكن يفضل من غير تحديث ويستبعد
    # قناة زي انستجرام بصمت تام من غير أي رسالة خطأ في اللوج.
    return list(CHANNEL_SERVICES.keys())


def main() -> None:
    api_key = os.environ.get("BUFFER_API_KEY", "").strip()
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not api_key or not github_token:
        sys.exit("BUFFER_API_KEY و GITHUB_TOKEN مطلوبان.")
    if not EPISODE_PATH.exists():
        sys.exit("state/current_episode.json غير موجود.")

    episode = json.loads(EPISODE_PATH.read_text(encoding="utf-8"))
    title = str(episode.get("title", "Horror Episode")).strip()
    caption = str(episode.get("caption", "")).strip()
    if not caption:
        sys.exit("current_episode.json لا يحتوي caption.")

    full_path = OUTPUT_DIR / "final_video_full.mp4"
    if not full_path.exists() or full_path.stat().st_size == 0:
        sys.exit(f"الفيديو الكامل غير موجود: {full_path}")
    full_width, full_height = video_dimensions(full_path)
    if full_width <= full_height:
        sys.exit(
            f"الفيديو الكامل ليس أفقيًا ({full_width}x{full_height}). "
            "شغّل assemble_video.py من النسخة الجديدة قبل النشر."
        )
    print(f"✅ أبعاد الفيديو الكامل: {full_width}x{full_height} — سيُرسل كفيديو YouTube عادي")

    shorts = sorted(OUTPUT_DIR.glob("short_*_*.mp4"))
    if not shorts:
        sys.exit("لا توجد شورتس جاهزة للنشر.")
    # ترتيب ثابت: short_1 ثم short_2، وداخل كل رقم المنصات.
    shorts = sorted(shorts, key=lambda p: (int(p.stem.split("_")[1]), p.stem))

    ids = channel_ids()
    if not ids:
        sys.exit(
            "لا يوجد أي قناة معرّفة: تأكد إن BUFFER_YOUTUBE_CHANNEL_ID / "
            "BUFFER_FACEBOOK_CHANNEL_ID / BUFFER_INSTAGRAM_CHANNEL_ID متظبطين "
            "في GitHub Secrets."
        )
    missing_services = {"youtube", "facebook", "instagram"} - set(CHANNEL_SERVICES.values())
    if missing_services:
        print(
            "⚠️ القنوات دي مفيش لها معرّف قناة متظبط في GitHub Secrets وهتتخطى "
            "بالكامل: " + ", ".join(sorted(missing_services))
        )

    org_id = organization_id(api_key) if ENABLE_PREFLIGHT_CHECK else None
    services = {cid: CHANNEL_SERVICES[cid] for cid in ids}
    full_urls = {}
    for service in set(services.values()):
        full_urls[service] = os.environ.get(f"FULL_VIDEO_URL_{service.upper()}", "").strip() or None

    assets: list[tuple[str, Path, float]] = [("full_video", full_path, FULL_VIDEO_DELAY_HOURS)]
    short_numbers = sorted({int(p.stem.split("_")[1]) for p in shorts})
    for number in short_numbers:
        delay = FULL_TO_SHORT_1_HOURS if number == 1 else FULL_TO_SHORT_2_HOURS
        for path in [p for p in shorts if int(p.stem.split("_")[1]) == number]:
            assets.append(("short", path, delay))

    successes = 0
    failures = []
    for asset_type, path, delay in assets:
        platform_hint = path.stem.rsplit("_", 1)[-1] if asset_type == "short" else None
        if platform_hint and platform_hint not in services.values():
            # ملهاش قناة معرّفة أصلًا (زي انستجرام لو الـ secret ناقص) —
            # نتخطى رفع الملف نفسه بدل ما نرفعه لـ GitHub من غير أي
            # وجهة نشر، وده كان بيظهر كسطر "📤" مُضلّل بيوحي بنجاح.
            print(f"⏭️  {path.name}: تخطي — لا توجد قناة {platform_hint} معرّفة (لم يُرفع الملف)")
            continue
        url = upload_media(path, github_token)
        due_at = iso_after(delay)
        print(f"📤 {path.name} → {due_at} UTC")
        for cid in ids:
            service = services[cid]
            if platform_hint and platform_hint != service:
                continue
            if asset_type == "full_video" and service == "youtube":
                # Buffer لا يدعم فيديوهات يوتيوب الطويلة (Long-form) إطلاقًا،
                # يدعم Shorts فقط. الفيديو الكامل بيتنشر ليوتيوب بشكل منفصل
                # عبر scripts/publish_youtube_direct.py باستخدام YouTube Data
                # API مباشرة، فبنتخطى محاولته هنا بدل ما يفشل دايمًا برسالة
                # "Video must be no longer than 3 minutes / must be vertical
                # for YouTube Shorts".
                print("  ⏭️  youtube: يُنشر بشكل منفصل عبر publish_youtube_direct.py (تم التخطي هنا)")
                continue
            try:
                if org_id is not None and pending_count(org_id, cid, api_key) >= CHANNEL_PENDING_LIMIT:
                    raise RuntimeError(f"قائمة Buffer ممتلئة ({CHANNEL_PENDING_LIMIT}) للقناة {service}")
                text = build_post_text(service, asset_type, title, caption, full_urls.get(service))
                post = create_post(url, text, title, cid, api_key, due_at, asset_type)
                print(f"  ✅ {service}: {post.get('id')} عند {post.get('dueAt', due_at)}")
                successes += 1
            except Exception as exc:
                failures.append((path.name, service, str(exc)))
                print(f"  ❌ {service}: {exc}")

    print(f"نجاح النشر: {successes}")
    for item in failures:
        print("فشل:", " | ".join(item))
    if successes == 0:
        sys.exit("لم ينجح نشر أي أصل.")


if __name__ == "__main__":
    main()
