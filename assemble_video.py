"""
assemble_video.py

ينتج أصلين من نفس الحلقة:

1) فيديو كامل أفقي 16:9:
   output/final_video_full.mp4

2) شورتس رأسية 9:16، مقتطفة من الفيديو الكامل وتتوقف قبل النهاية/الحل:
   output/short_1_youtube.mp4
   output/short_1_facebook.mp4
   output/short_1_instagram.mp4
   ...

مصدر الحقيقة للصوت والترجمة هو current_episode.json. يدعم الملف الحقول الجديدة:

{
  "final_audio": "downloaded_clips/narration_with_music.mp3",
  "subtitles": "downloaded_clips/narration.ass",
  "shorts": [
    {"start_seconds": 0, "end_seconds": 75},
    {"start_seconds": 80, "end_seconds": 145}
  ]
}

إذا لم توجد قائمة shorts، يتم إنشاء شورتين تلقائيًا من بداية/منتصف الفيديو،
مع ترك AUTO_END_MARGIN_SECONDS في نهاية الحلقة حتى لا يصل المقتطف إلى الحل.

مهم: مدة 90 ثانية حد للشورتس فقط، وليست حدًا للفيديو الكامل.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
STATE_DIR = ROOT_DIR / "state"
CLIPS_DIR = ROOT_DIR / "downloaded_clips"
OUTPUT_DIR = ROOT_DIR / "output"

FETCHED_CLIPS_PATH = STATE_DIR / "fetched_clips.json"
EPISODE_PATH = STATE_DIR / "current_episode.json"

# الفيديو الكامل: أفقي 16:9
FULL_WIDTH = 1920
FULL_HEIGHT = 1080

# الشورتس: رأسي 9:16
SHORT_WIDTH = 1080
SHORT_HEIGHT = 1920
MAX_SHORT_DURATION_SECONDS = 90.0
DEFAULT_SHORT_COUNT = 2
AUTO_END_MARGIN_SECONDS = 8.0
CTA_DURATION_SECONDS = 4.0
FPS = 24

PLATFORM_CTA = {
    "youtube": "عايز تعرف النهاية؟\nشاهد الحلقة كاملة على YouTube",
    "facebook": "عايز تعرف النهاية؟\nشاهد الحلقة كاملة على صفحتنا",
    "instagram": "عايز تعرف النهاية؟\nالحلقة كاملة على صفحتنا",
}


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(
            "❌ فشل الأمر:\n"
            + " ".join(command)
            + "\n\n"
            + result.stderr
        )
    return result


def probe_duration(path: Path) -> float:
    result = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    try:
        return float(result.stdout.strip())
    except ValueError:
        sys.exit(f"❌ تعذر قراءة مدة الملف: {path}")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    # المسارات القديمة قد تكون نسبية إلى جذر المشروع أو إلى scripts/.
    candidates = [ROOT_DIR / path, SCRIPT_DIR / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return ROOT_DIR / path


def normalize_clip(input_path: Path, output_path: Path, duration: float) -> None:
    """يحوّل أي كليب إلى 1920x1080 أفقيًا مع ملء الإطار وقص الحواف."""
    run([
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", str(input_path),
        "-t", f"{duration:.3f}",
        "-vf",
        f"scale={FULL_WIDTH}:{FULL_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={FULL_WIDTH}:{FULL_HEIGHT},fps={FPS}",
        "-an",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ])


def concat_clips(paths: list[Path], output_path: Path, list_path: Path) -> None:
    if not paths:
        sys.exit("❌ لا توجد مقاطع صالحة لتجميعها.")

    list_path.write_text(
        "\n".join(f"file '{path.resolve().as_posix()}'" for path in paths) + "\n",
        encoding="utf-8",
    )
    run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ])


def subtitle_filter(subtitles: Path | None) -> str | None:
    if not subtitles or not subtitles.exists():
        return None
    path = str(subtitles.resolve()).replace("\\", "/").replace(":", "\\:")
    return f"subtitles='{path}'"


def add_audio_and_subtitles(
    video_path: Path,
    final_audio: Path,
    subtitles: Path | None,
    output_path: Path,
) -> None:
    filters = []
    sub_filter = subtitle_filter(subtitles)
    if sub_filter:
        filters.append(sub_filter)

    command = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(final_audio),
    ]
    if filters:
        command += ["-vf", ";".join(filters)]
    command += [
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-t", f"{probe_duration(final_audio):.3f}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]
    run(command)


def build_full_video(
    clips: list[dict],
    final_audio: Path,
    subtitles: Path | None,
    output_path: Path,
) -> float:
    """يبني الحلقة الكاملة الأفقية دون قصها إلى 90 ثانية."""
    audio_duration = probe_duration(final_audio)
    if audio_duration <= 0:
        sys.exit("❌ مدة الصوت النهائي غير صالحة.")

    duration_per_clip = max(audio_duration / len(clips), 2.0)
    normalized: list[Path] = []
    for index, clip in enumerate(clips):
        source = resolve_path(clip["file"])
        if not source.exists():
            sys.exit(f"❌ الكليب غير موجود: {source}")
        norm_path = CLIPS_DIR / f"norm_full_{index:03d}.mp4"
        normalize_clip(source, norm_path, duration_per_clip)
        normalized.append(norm_path)

    concatenated = CLIPS_DIR / "concatenated_full.mp4"
    concat_clips(normalized, concatenated, CLIPS_DIR / "concat_list_full.txt")
    add_audio_and_subtitles(concatenated, final_audio, subtitles, output_path)
    return probe_duration(output_path)


def write_cta_ass(path: Path, start: float, end: float, text: str) -> None:
    """ينشئ Overlay ASS عربيًا بدل drawtext لتفادي مشاكل تشكيل العربية."""
    def ass_time(seconds: float) -> str:
        centiseconds = max(0, int(round(seconds * 100)))
        hours, rem = divmod(centiseconds, 360000)
        minutes, rem = divmod(rem, 6000)
        secs, cs = divmod(rem, 100)
        return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"

    safe_text = text.replace("\\", "\\\\").replace("\n", r"\N")
    content = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {SHORT_WIDTH}\n"
        f"PlayResY: {SHORT_HEIGHT}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # أبيض مع خلفية شبه شفافة، أسفل/منتصف الشاشة.
        "Style: CTA,Arial,62,&H00FFFFFF,&H00FFFFFF,&H00101010,&H99000000,"
        "1,0,0,0,100,100,0,0,1,4,1,2,70,70,180,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
        f"Dialogue: 0,{ass_time(start)},{ass_time(end)},CTA,,0,0,0,,{safe_text}\n"
    )
    path.write_text(content, encoding="utf-8")


def default_short_specs(full_duration: float) -> list[dict]:
    """ينشئ شورتين تلقائيًا ويترك هامشًا قبل نهاية القصة."""
    usable_end = max(1.0, full_duration - AUTO_END_MARGIN_SECONDS)
    if usable_end <= 1:
        return [{"start_seconds": 0.0, "end_seconds": min(full_duration, MAX_SHORT_DURATION_SECONDS)}]

    count = DEFAULT_SHORT_COUNT
    window = min(MAX_SHORT_DURATION_SECONDS, usable_end / count)
    specs = []
    for index in range(count):
        start = index * (usable_end / count)
        end = min(start + window, usable_end)
        if end - start < 1:
            continue
        specs.append({"start_seconds": start, "end_seconds": end})
    return specs


def load_short_specs(episode: dict, full_duration: float) -> list[dict]:
    raw = episode.get("shorts")
    if not isinstance(raw, list) or not raw:
        return default_short_specs(full_duration)

    specs = []
    safe_end = max(0.0, full_duration - AUTO_END_MARGIN_SECONDS)
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            start = max(0.0, float(item.get("start_seconds", 0)))
            requested_end = float(item.get("end_seconds", start + MAX_SHORT_DURATION_SECONDS))
        except (TypeError, ValueError):
            continue

        end = min(requested_end, start + MAX_SHORT_DURATION_SECONDS, safe_end)
        if end - start >= 1.0:
            specs.append({"start_seconds": start, "end_seconds": end})

    return specs or default_short_specs(full_duration)


def create_short(
    full_video: Path,
    spec: dict,
    short_index: int,
    platform: str,
    output_path: Path,
) -> float:
    start = float(spec["start_seconds"])
    end = float(spec["end_seconds"])
    duration = min(end - start, MAX_SHORT_DURATION_SECONDS)
    if duration <= 0:
        raise ValueError("مدة الشورت يجب أن تكون أكبر من صفر")

    cta_start = max(0.0, duration - CTA_DURATION_SECONDS)
    cta_ass = CLIPS_DIR / f"cta_short_{short_index}_{platform}.ass"
    write_cta_ass(cta_ass, cta_start, duration, PLATFORM_CTA[platform])
    cta_filter = subtitle_filter(cta_ass)

    # crop مركزي من 16:9 إلى 9:16، مع الإبقاء على صوت الفيديو الكامل.
    vf = (
        f"scale={SHORT_WIDTH}:{SHORT_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={SHORT_WIDTH}:{SHORT_HEIGHT}"
    )
    if cta_filter:
        vf += f",{cta_filter}"

    run([
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-i", str(full_video),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ])
    return probe_duration(output_path)


def main() -> None:
    for path in (FETCHED_CLIPS_PATH, EPISODE_PATH):
        if not path.exists():
            sys.exit(f"❌ الملف غير موجود: {path}")

    clips = json.loads(FETCHED_CLIPS_PATH.read_text(encoding="utf-8"))
    if not isinstance(clips, list) or not clips:
        sys.exit("❌ fetched_clips.json فارغ أو غير صالح.")

    episode = json.loads(EPISODE_PATH.read_text(encoding="utf-8"))
    final_audio_value = episode.get("final_audio")
    subtitles_value = episode.get("subtitles")

    # توافق مع current_episode القديم الذي كان يحفظ المخرجات داخل parts.
    if not final_audio_value:
        parts = episode.get("parts") or []
        if len(parts) == 1:
            final_audio_value = parts[0].get("final_audio")
            subtitles_value = subtitles_value or parts[0].get("subtitles")
        elif len(parts) > 1:
            sys.exit(
                "❌ current_episode.json ما زال يحتوي على أجزاء متعددة. "
                "شغّل generate_voice.py بالنسخة الجديدة لإنتاج صوت كامل واحد."
            )

    if not final_audio_value:
        final_audio_value = str(CLIPS_DIR / "narration_with_music.mp3")
    final_audio = resolve_path(final_audio_value)
    subtitles = resolve_path(subtitles_value) if subtitles_value else None

    if not final_audio.exists():
        sys.exit(f"❌ ملف الصوت النهائي غير موجود: {final_audio}")
    if subtitles and not subtitles.exists():
        print(f"⚠️ ملف الترجمة غير موجود؛ سيتم إنتاج الفيديو بدون ترجمة: {subtitles}")
        subtitles = None

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    # لا نترك ملفات أصول قديمة تُفهم على أنها ناتج التشغيل الحالي.
    for old in OUTPUT_DIR.glob("final_video_part*.mp4"):
        old.unlink(missing_ok=True)
    for old in OUTPUT_DIR.glob("short_*.mp4"):
        old.unlink(missing_ok=True)

    full_output = OUTPUT_DIR / "final_video_full.mp4"
    full_duration = build_full_video(clips, final_audio, subtitles, full_output)
    print(f"✅ الفيديو الكامل الأفقي: {full_output}")
    print(f"✅ مدة الفيديو الكامل: {full_duration:.1f} ثانية")

    specs = load_short_specs(episode, full_duration)
    print(f"✅ عدد الشورتس: {len(specs)} — الحد الأقصى لكل شورت: {MAX_SHORT_DURATION_SECONDS:.0f}s")

    generated = 0
    for short_index, spec in enumerate(specs, 1):
        for platform in PLATFORM_CTA:
            output = OUTPUT_DIR / f"short_{short_index}_{platform}.mp4"
            duration = create_short(full_output, spec, short_index, platform, output)
            generated += 1
            print(
                f"✅ شورت {short_index} / {platform}: {output} "
                f"({duration:.1f}s، يتوقف قبل نهاية القصة)"
            )

    if generated == 0:
        sys.exit("❌ لم يتم إنشاء أي شورت.")

    print("✅ اكتمل إنتاج الفيديو الكامل والشورتس لجميع المنصات.")


if __name__ == "__main__":
    main()
