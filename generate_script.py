"""
generate_script.py
يستدعي Groq API عشان يولّد سيناريو القصة + كلمات البحث البصرية.

=== تعديل جديد (قصص حقيقية + هوك + كلمات بحث دقيقة + تنويع جغرافي) ===

1) القصص بقت مطلوب منها تكون مبنية على حالات حقيقية/موثقة أو أساطير
   حضرية مشهورة يُتداول إنها حقيقية (اختفاءات غامضة، قضايا غير محلولة،
   أماكن مسكونة موثّقة إعلاميًا...)، بدل التأليف الكامل من الصفر.
   ⚠️ تنويه مهم وصادق: الموديل مايقدرش "يتحقق" فعليًا من صحة أي حدث —
   مفيش أداة بحث جوه السكريبت. اللي بيحصل هو توجيه الموديل لاستخدام
   معرفته بقضايا/أساطير مشهورة فعلاً (زي أسلوب "مبنية على أحداث حقيقية"
   الشائع في محتوى الرعب)، مش تحقق واقعي مضمون 100%. لو عايز تحقق حقيقي،
   محتاج تضيف خطوة بحث ويب فعلية قبل التوليد (مش موجودة حاليًا).

2) أُضيف حقل جديد إلزامي "hook" في الـ schema: جملة واحدة قوية وصادمة
   تُستخدم كأول سطر يظهر في الفيديو (قبل أو مع بداية narration) عشان
   تمسك المشاهد في أول ثانيتين. الموديل مطلوب منه يكتبها منفصلة، وبرضو
   يبدأ بيها (أو بصياغة قريبة منها) أول narration.

3) حقل جديد اختياري "region": المنطقة/الدولة اللي القصة منها (مثلاً
   "اليابان"، "المكسيك"، "بولندا"...). بيتسجل في التاريخ عشان نمنع تكرار
   نفس المنطقة كل مرة ونضمن تنويع جغرافي حقيقي. الحقل اختياري في القراءة
   (load_used_history) عشان الكود يفضل شغال حتى لو ملف used_clips.json
   القديم مفيهوش الحقل ده أصلاً.

4) visual_keywords بقت مطلوب منها تكون مشتقة من تفاصيل ملموسة داخل نص
   القصة نفسها (مكان/عصر/أغراض/شخصيات محددة مذكورة فعلاً)، مش كلمات رعب
   عامة (زي "spooky forest" أو "scary house") بتجيب لقطات ستوك عشوائية
   ملهاش علاقة مباشرة بالموضوع.

⚠️ لأقوى التزام من الموديل، المفروض تضيف نفس التوجيهات دي (خصوصًا بند
القصص الحقيقية والهوك) كجزء من prompts/horror_system_prompt.md نفسه،
لأن الموديل بيتقيّد بالـ system prompt أكتر من رسالة المستخدم. ابعتلي
محتوى الملف ده لو عايزني أدمج التوجيهات فيه مباشرة.

=== تعديلات سابقة (نسخة الفصحى + توفير التوكن) ===
[محفوظة كما هي أسفل الكود]
"""
import os
import json
import sys
from pathlib import Path

from groq import Groq  # pip install -U groq

SCRIPT_DIR = Path(__file__).parent
PROMPT_PATH = SCRIPT_DIR.parent / "prompts" / "horror_system_prompt.md"
OUTPUT_PATH = SCRIPT_DIR.parent / "state" / "current_episode.json"

# ─────────────────────────── الإعدادات ───────────────────────────

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
REASONING_EFFORT = os.getenv("GROQ_REASONING_EFFORT", "low")
TEMPERATURE = 0.85
MAX_COMPLETION_TOKENS = 6000
# اتحسب على أساس إن الحلقة دايمًا بتتقسم لجزئين (زي ما assemble_video.py
# بيفترض دايمًا: final_video_part1.mp4 + final_video_part2.mp4)، وكل جزء
# له حد أقصى صلب 90 ثانية (MAX_DURATION_SECONDS في assemble_video.py).
# بمتوسط سرعة نطق عربي فصيح ~2.3-2.7 كلمة/ثانية:
#   300 كلمة إجمالي (±15% = 255-345 كلمة) ≈ 94-150 ثانية إجمالي،
#   يعني تقريبًا 47-75 ثانية للجزء الواحد بعد التقسيم بالنص — مسافة أمان
#   كويسة تحت حد الـ90 ثانية لكل جزء.
# لو قللت الرقم ده كتير، الجزء التاني ممكن يبقى قصير جدًا أو شبه فاضي.
TARGET_WORDS = int(os.getenv("TARGET_WORDS", "300"))
MAX_ATTEMPTS = 2
HISTORY_LIMIT = 8
LENGTH_ESCALATION = 1.5

# آخر N مناطق/دول اتستخدمت — بتتبعت في الـ prompt عشان نضمن تنويع جغرافي
# ومنمنعش نفس المنطقة تتكرر أكتر من مرة قريبة.
REGION_HISTORY_LIMIT = 6

# ⚠️ لو السكريبت اللي بيجيب الفيديوهات من Pexels بيتوقع visual_keywords
# كـ string مفصول بفواصل بدل list، غيّر "type": "array" لـ "type": "string"
# في الـ schema تحت.
EPISODE_SCHEMA = {
    "name": "horror_episode",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "hook": {"type": "string"},
            "region": {"type": "string"},
            "narration": {"type": "string"},
            "visual_keywords": {"type": "array", "items": {"type": "string"}},
            "caption": {"type": "string"},
            # === تعديل جديد: تلميحات نطق للأسماء الأجنبية ===
            # الأسماء الأجنبية (مناطق/مدن/أشخاص) هي أكثر ما يخرج نطقه سيئًا
            # وواضح إنه صوت آلي من edge-tts، لأنها من غير قاعدة صرفية عربية
            # ثابتة. كل مدخل: "word" (الكلمة/العبارة الأجنبية كما وردت
            # بالضبط في narration) و"phonetic" (نفس الكلمة بالحروف الأساسية
            # نفسها + تشكيل كامل يوضح نطقها الصحيح، من غير أي حرف زيادة).
            # يُطبَّق في generate_voice.py قبل التوليد الصوتي فقط، ولا يظهر
            # في الترجمة/الكابشن.
            "phonetic_hints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "word": {"type": "string"},
                        "phonetic": {"type": "string"},
                    },
                    "required": ["word", "phonetic"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "title", "hook", "region", "narration",
            "visual_keywords", "caption", "phonetic_hints",
        ],
        "additionalProperties": False,
    },
}

REQUIRED_KEYS = set(EPISODE_SCHEMA["schema"]["required"])


# ─────────────────────────── مساعدات ───────────────────────────

def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def load_used_history(limit: int = HISTORY_LIMIT) -> list[str]:
    """يجيب آخر N عناوين عشان الموديل يتجنب التكرار."""
    history_path = SCRIPT_DIR.parent / "state" / "used_clips.json"
    if not history_path.exists():
        return []
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [h.get("title", "") for h in data.get("history", [])][-limit:]


def load_used_regions(limit: int = REGION_HISTORY_LIMIT) -> list[str]:
    """
    يجيب آخر N مناطق/دول اتستخدمت، عشان نطلب من الموديل يتجنب تكرارها.
    آمن على ملفات used_clips.json القديمة اللي مفيهاش حقل "region" أصلاً
    (هيتجاهلها ببساطة من غير ما يفشل).
    """
    history_path = SCRIPT_DIR.parent / "state" / "used_clips.json"
    if not history_path.exists():
        return []
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    regions = [h.get("region", "") for h in data.get("history", []) if h.get("region")]
    return regions[-limit:]


def load_used_hooks(limit: int = HISTORY_LIMIT) -> list[str]:
    """
    يجيب آخر N هوكات اتستخدمت. الهوك بيوصف الحادثة الواقعية نفسها بدقة
    أكتر من العنوان (اللي ممكن يتغيّر صياغةً بين حلقة وحلقة عن نفس
    الحادثة بالظبط) — بيُستخدم هنا عشان نمنع الموديل يرجع لنفس القضية
    الشهيرة (زي حادثة ممر دياتلوف) تحت عنوان مختلف. آمن على ملفات
    used_clips.json القديمة اللي مفيهاش حقل "hook" أصلاً.
    """
    history_path = SCRIPT_DIR.parent / "state" / "used_clips.json"
    if not history_path.exists():
        return []
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    hooks = [h.get("hook", "") for h in data.get("history", []) if h.get("hook")]
    return hooks[-limit:]


def looks_truncated(narration: str) -> bool:
    stripped = narration.strip()
    if not stripped:
        return True
    return not stripped.endswith((".", "!", "؟", "?", "…", '"', "”", "»"))


def log_usage(completion, attempt: int) -> None:
    usage = getattr(completion, "usage", None)
    if not usage:
        return
    print(
        f"   🔢 محاولة {attempt} | مدخل: {usage.prompt_tokens} "
        f"| مخرج: {usage.completion_tokens} "
        f"| إجمالي: {usage.total_tokens}"
    )


def create_completion(client: Groq, **kwargs):
    try:
        return client.chat.completions.create(**kwargs)
    except TypeError:
        effort = kwargs.pop("reasoning_effort", None)
        if effort:
            kwargs["extra_body"] = {"reasoning_effort": effort}
        return client.chat.completions.create(**kwargs)


# ─────────────────────────── التوليد ───────────────────────────

def build_user_message(
    recent_titles: list[str], recent_regions: list[str], recent_hooks: list[str],
) -> str:
    message = (
        "اكتب حلقة جديدة تمامًا.\n\n"
        "⚠️ مهم جدًا بخصوص اللغة: اكتب حقل narration بالكامل باللغة العربية "
        "الفصحى المبسّطة (Modern Standard Arabic) فقط. ممنوع استخدام أي "
        "لهجة عامية أو محلية حتى لو كلمة واحدة.\n\n"
        "⚠️ مهم جدًا بخصوص مصدر القصة: لازم تكون القصة مبنية على حادثة "
        "حقيقية موثّقة، أو قضية غامضة معروفة إعلاميًا، أو أسطورة حضرية "
        "مشهورة يُتداول على نطاق واسع إنها حقيقية (اختفاء غامض، بيت مسكون "
        "موثّق، حادثة غير محلولة...). ممنوع اختراع قصة خيالية بالكامل من "
        "الصفر. لو التفاصيل الدقيقة مش متأكد منها 100%، استخدم صياغة "
        "شائعة زي 'تقول الروايات إن...' أو 'وفقًا لما تم توثيقه...' بدل "
        "تقديم تفاصيل مختلقة كحقيقة مؤكدة قطعيًا.\n\n"
        "⚠️ مهم جدًا جدًا بخصوص عدم التكرار: ممنوع منعًا باتًا اختيار نفس "
        "الحادثة الواقعية اللي اتستخدمت في حلقة سابقة، حتى لو غيّرت "
        "العنوان أو الصياغة بالكامل. راجع قائمة الهوكات (وليس العناوين "
        "فقط) اللي اتستخدمت قبل كده تحت — لو الحادثة اللي في بالك بتوصف "
        "نفس واقعة أي هوك منهم (حتى بتفاصيل أو زاوية مختلفة شكليًا)، "
        "ارفضها فورًا واختار حادثة مختلفة تمامًا. بالتحديد: تجنب حادثة "
        "'ممر دياتلوف' (Dyatlov Pass) في روسيا كليًا إلا في حالات نادرة "
        "جدًا، لأنها من أكثر القضايا استخدامًا وتكرارًا في هذا النوع من "
        "المحتوى على الإنترنت وأصبحت مستهلكة ومتوقعة للمشاهد، وركّز بدلًا "
        "منها على قضايا حقيقية موثقة لكن أقل شهرة وأقل تداولًا.\n\n"
        "⚠️ مهم جدًا بخصوص الهوك: أول جملة في حقل hook لازم تكون صادمة "
        "ومباشرة وتخلق فضول فوري (سؤال مثير، حقيقة صادمة، أو مشهد لحظة "
        "الذروة) — الهدف إنها توقف المشاهد عن الاسكرول في أول ثانيتين. "
        "وبعدين ابدأ narration بنفس الهوك أو صياغة قريبة جدًا منه كأول "
        "جملة فيه، مش بمقدمة عامة بطيئة.\n\n"
        "⚠️ مهم جدًا بخصوص شدة الرعب: الهدف مش إنك تحكي واقعة حصلت وخلاص "
        "— الهدف إن قلب المشاهد يدق بسرعة وهو بيسمع. استخدم تفاصيل حسّية "
        "(نبض، تنفّس متقطع، عرق بارد، صمت مفاجئ يقطعه صوت) بدل جمل عامة "
        "زي 'كان خايف'. صعّد التوتر باستمرار جملة بعد جملة، مش بس في "
        "الذروة، واستخدم جملاً قصيرة متقطعة في لحظات الترقب. راجع "
        "التعليمات التفصيلية في الـ system prompt لو محتاج تفاصيل أكتر.\n\n"
        "⚠️ مهم جدًا بخصوص visual_keywords: كل كلمة بحث لازم تكون مشتقة "
        "من تفاصيل ملموسة ومحددة مذكورة فعليًا في narration (المكان "
        "بالاسم أو الوصف، العصر/الفترة الزمنية، الأغراض أو المشاهد "
        "المحددة المذكورة في القصة). ممنوع كلمات رعب عامة وفضفاضة زي "
        "'spooky forest' أو 'scary house' من غير علاقة مباشرة بتفاصيل "
        "القصة، لأنها بتجيب لقطات ستوك عشوائية ملهاش علاقة بالموضوع.\n\n"
        "⚠️ التنويع الجغرافي: اختار منطقة/دولة مختلفة عن المناطق اللي "
        "اتذكرت قبل كده (تحت). حط اسم المنطقة/الدولة في حقل region.\n\n"
        f"الطول المستهدف لحقل narration: حوالي {TARGET_WORDS} كلمة (±15%).\n"
        "لازم القصة تكون مكتملة: بداية واضحة (الهوك)، تصاعد حقيقي، وخاتمة "
        "فعلية تقفل القصة من غير تقطيع.\n"
        "اكتب النص النهائي مباشرة: من غير أي تمهيد أو شرح أو تعليق."
    )
    if recent_titles:
        message += (
            "\n\nالعناوين اللي اتستخدمت قبل كده (تجنب أي تشابه معاها):\n- "
            + "\n- ".join(recent_titles)
        )
    if recent_hooks:
        message += (
            "\n\nالهوكات (ومن ثم الحوادث الفعلية) اللي اتستخدمت قبل كده — "
            "ممنوع اختيار نفس الحادثة حتى بهوك أو عنوان مختلف:\n- "
            + "\n- ".join(recent_hooks)
        )
    if recent_regions:
        message += (
            "\n\nالمناطق/الدول اللي اتستخدمت قبل كده (اختار منطقة مختلفة "
            "عنها):\n- " + "\n- ".join(recent_regions)
        )
    return message


def generate_episode() -> dict:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        sys.exit("خطأ: لازم تضيف GROQ_API_KEY في GitHub Secrets")

    client = Groq(api_key=api_key)
    system_prompt = load_system_prompt()
    user_message = build_user_message(
        load_used_history(), load_used_regions(), load_used_hooks(),
    )

    budget = MAX_COMPLETION_TOKENS
    last_error = "لا يوجد"

    print(f"🎬 الموديل: {MODEL} | reasoning_effort: {REASONING_EFFORT}")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        completion = create_completion(
            client,
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=TEMPERATURE,
            max_completion_tokens=budget,
            reasoning_effort=REASONING_EFFORT,
            response_format={"type": "json_schema", "json_schema": EPISODE_SCHEMA},
        )

        log_usage(completion, attempt)
        choice = completion.choices[0]

        if choice.finish_reason == "length":
            budget = int(budget * LENGTH_ESCALATION)
            last_error = "الرد اتقطع بسبب حد التوكنز (finish_reason=length)"
            print(f"⚠️ {last_error} — هرفع السقف لـ {budget} وأعيد المحاولة...")
            continue

        raw = choice.message.content or ""

        try:
            episode = json.loads(raw)
        except json.JSONDecodeError as exc:
            last_error = f"رد غير صالح JSON ({exc})"
            print(f"⚠️ محاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هعيد المحاولة...")
            continue

        if not REQUIRED_KEYS.issubset(episode.keys()):
            last_error = f"الرد ناقص حقول مطلوبة: {sorted(episode.keys())}"
            print(f"⚠️ محاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هعيد المحاولة...")
            continue

        narration = str(episode.get("narration", "")).strip()
        if looks_truncated(narration):
            last_error = "نص narration شكله متقطوع (مش منتهي بعلامة ترقيم واضحة)"
            print(f"⚠️ محاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هعيد المحاولة...")
            continue

        if not episode.get("visual_keywords"):
            last_error = "حقل visual_keywords فاضي"
            print(f"⚠️ محاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هعيد المحاولة...")
            continue

        if not str(episode.get("hook", "")).strip():
            last_error = "حقل hook فاضي"
            print(f"⚠️ محاولة {attempt}/{MAX_ATTEMPTS}: {last_error} — هعيد المحاولة...")
            continue

        return episode

    sys.exit(
        f"❌ فشل توليد حلقة سليمة بعد {MAX_ATTEMPTS} محاولات. آخر خطأ: {last_error}"
    )


if __name__ == "__main__":
    episode = generate_episode()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"✅ اتكتبت الحلقة: {episode['title']}")
    print(f"   المنطقة: {episode.get('region', 'غير محدد')}")
    print(f"   الهوك: {episode.get('hook', '')[:80]}")
    print(f"   كلمات البحث: {episode['visual_keywords']}")
    print(f"   تلميحات النطق: {episode.get('phonetic_hints', [])}")
