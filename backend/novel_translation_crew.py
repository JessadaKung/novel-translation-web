"""
novel_translation_crew.py
--------------------------
Pipeline แปลนิยายครบวงจร 6 Agents
Input → [Agent4 Context] → [Agent1 แปล] → [Agent2 Glossary]
     → [Agent3 ตรวจสำนวน] → [Agent5 Style] → [Agent6 QA]
     → Output + อัปเดต Glossary DB
"""

from crewai import Agent, Task, Crew, Process
from llm_manager import LLMManager
from glossary_db import (
    load_glossary, save_glossary, merge_glossary,
    add_chapter_summary, get_recent_context
)
import json
import re

# ─────────────────────────────────────────────────────────────────────
# 1. ตั้งค่า API Keys
# ─────────────────────────────────────────────────────────────────────

API_KEYS = [
    "AIza_KEY_A",
    "AIza_KEY_B",
    "AIza_KEY_C",
    "AIza_KEY_D",
    "AIza_KEY_E",
    "AIza_KEY_F",
]

manager = LLMManager(api_keys=API_KEYS)

# ─────────────────────────────────────────────────────────────────────
# 2. สร้าง 6 Agents — แต่ละตัวดึง LLM จาก manager (key rotation อัตโนมัติ)
# ─────────────────────────────────────────────────────────────────────

context_agent = Agent(
    role="Context Manager",
    goal="ดึง context และ Glossary จากตอนก่อนหน้า เพื่อให้นักแปลรู้ว่าเรื่องดำเนินมาถึงไหน",
    backstory=(
        "ผู้เชี่ยวชาญด้านการจัดการข้อมูลนิยาย รู้จักตัวละคร สถานที่ "
        "และเหตุการณ์สำคัญทุกอย่างที่ผ่านมา"
    ),
    llm=manager.get_llm(agent_role="Agent4-Context"),
    verbose=True,
)

translator_agent = Agent(
    role="Novel Translator",
    goal="แปลนิยายจากต้นฉบับเป็นภาษาไทยให้ลื่นไหล รักษาอารมณ์ต้นฉบับ และเก็บ Glossary ใหม่",
    backstory=(
        "นักแปลนิยายมืออาชีพที่เชี่ยวชาญการถ่ายทอดอารมณ์และสไตล์ของผู้เขียต้นฉบับ "
        "รักษาบุคลิกของตัวละครแต่ละตัวได้อย่างสม่ำเสมอ"
    ),
    llm=manager.get_llm(agent_role="Agent1-Translator"),
    verbose=True,
)

glossary_agent = Agent(
    role="Glossary Researcher",
    goal=(
        "ค้นหาและยืนยันชื่อตัวละคร สถานที่ คำศัพท์เฉพาะ "
        "ว่ามีชื่อไทยที่เป็นที่รู้จักหรือไม่ แล้วอัปเดต Glossary"
    ),
    backstory=(
        "ผู้เชี่ยวชาญด้านการค้นหาข้อมูลและยืนยันความถูกต้องของ Glossary "
        "มีประสบการณ์ทำงานกับนิยายแปลหลายแนว"
    ),
    llm=manager.get_llm(agent_role="Agent2-Glossary"),
    verbose=True,
)

style_checker_agent = Agent(
    role="Style & Repetition Checker",
    goal="ตรวจสอบสำนวนซ้ำ คำซ้ำ และแก้ไขให้บทแปลอ่านสนุกและหลากหลายมากขึ้น",
    backstory=(
        "บรรณาธิการอาวุโสที่มีสายตาเฉียบคมในการจับสำนวนซ้ำและปรับให้งานแปล "
        "อ่านลื่นไหลเป็นธรรมชาติ"
    ),
    llm=manager.get_llm(agent_role="Agent3-StyleChecker"),
    verbose=True,
)

tone_agent = Agent(
    role="Tone & Voice Keeper",
    goal=(
        "ตรวจสอบว่าบทแปลรักษา tone และ voice ของตัวละครแต่ละตัวได้ถูกต้อง "
        "เช่น ตัวละครที่พูดหยาบต้องพูดหยาบ ตัวละครสุภาพต้องพูดสุภาพ"
    ),
    backstory=(
        "นักเขียนและนักแปลที่เชี่ยวชาญด้านการสร้างบุคลิกตัวละครผ่านภาษา "
        "สามารถแยกแยะ voice ของตัวละครต่างๆ ได้อย่างแม่นยำ"
    ),
    llm=manager.get_llm(agent_role="Agent5-ToneKeeper"),
    verbose=True,
)

qa_agent = Agent(
    role="QA Final Reviewer",
    goal=(
        "ตรวจสอบรอบสุดท้ายก่อน output จริง ให้แน่ใจว่าชื่อตัวละครสถานที่ถูกต้อง "
        "ครบถ้วน ไม่มีข้อผิดพลาด และสรุปตอนนี้ไว้สำหรับ context ตอนต่อไป"
    ),
    backstory=(
        "QA specialist ที่ผ่านการตรวจงานแปลนิยายมาหลายร้อยตอน "
        "มีความละเอียดรอบคอบสูงและไม่ปล่อยให้ข้อผิดพลาดใดๆ ผ่านไปได้"
    ),
    llm=manager.get_llm(agent_role="Agent6-QA"),
    verbose=True,
)

# ─────────────────────────────────────────────────────────────────────
# 3. สร้าง Tasks
# ─────────────────────────────────────────────────────────────────────

def create_tasks(
    chapter_text: str,
    chapter_num: int,
    glossary_snapshot: dict,
    recent_context: str,
) -> list[Task]:

    # ── Agent 4: Context Manager ──────────────────────────────────────
    context_task = Task(
        description=f"""
        เตรียม context สำหรับการแปลตอนที่ {chapter_num}

        --- สรุปตอนก่อนหน้า (ล่าสุด 3 ตอน) ---
        {recent_context}

        --- Glossary ที่สะสมไว้ ---
        ตัวละคร: {glossary_snapshot.get('characters', {})}
        สถานที่: {glossary_snapshot.get('places', {})}
        คำศัพท์: {glossary_snapshot.get('terms', {})}

        ให้สรุป:
        1. เหตุการณ์สำคัญที่นักแปลควรรู้ก่อนแปลตอนนี้
        2. ตัวละครที่อาจปรากฏในตอนนี้และบุคลิกของแต่ละตัว
        3. Glossary ที่ต้องระวังเป็นพิเศษ
        """,
        expected_output="สรุป context พร้อมข้อมูลตัวละครและ Glossary ที่เกี่ยวข้อง",
        agent=context_agent,
    )

    # ── Agent 1: Translator ───────────────────────────────────────────
    translate_task = Task(
        description=f"""
        แปลนิยายตอนที่ {chapter_num} เป็นภาษาไทย

        --- Context จาก Agent 4 ---
        (ดูจาก output ของ context_task)

        --- เนื้อหาต้นฉบับ ---
        {chapter_text}

        ข้อกำหนด:
        - รักษาอารมณ์และสไตล์ต้นฉบับ
        - ใช้ชื่อจาก Glossary ที่ให้ไว้เท่านั้น
        - บทพูดต้องรักษาบุคลิกของแต่ละตัวละคร

        ให้ผลลัพธ์เป็น:
        1. บทแปลภาษาไทย
        2. Glossary ใหม่ที่พบในตอนนี้ (JSON):
           {{"characters": {{}}, "places": {{}}, "terms": {{}}}}
        """,
        expected_output="บทแปลภาษาไทย + Glossary JSON ใหม่",
        agent=translator_agent,
        context=[context_task],
    )

    # ── Agent 2: Glossary Researcher ──────────────────────────────────
    glossary_task = Task(
        description="""
        รับ Glossary ใหม่จาก Agent 1 แล้ว:
        1. ค้นหาว่ามีชื่อไทยที่เป็นที่รู้จักสำหรับตัวละคร/สถานที่/คำศัพท์เหล่านี้หรือไม่
           (เช่น ถ้าเป็นนิยายดังที่มีคนแปลไทยแล้ว ให้ใช้ชื่อที่แพร่หลาย)
        2. ถ้าพบชื่อที่ถูกต้องกว่า ให้อัปเดต
        3. ถ้าไม่พบ ให้คงชื่อที่ Agent 1 แปลไว้
        4. ส่งคืน Glossary ที่ยืนยันแล้ว (JSON format เดิม)
        """,
        expected_output="Glossary JSON ที่ยืนยันและอัปเดตแล้ว",
        agent=glossary_agent,
        context=[translate_task],
    )

    # ── Agent 3: Style & Repetition Checker ───────────────────────────
    style_task = Task(
        description="""
        รับบทแปลจาก Agent 1 และ Glossary ที่ยืนยันแล้วจาก Agent 2 แล้ว:
        1. แทนที่ชื่อในบทแปลด้วยชื่อที่ถูกต้องจาก Glossary ที่อัปเดตแล้ว
        2. ตรวจหาคำหรือสำนวนที่ซ้ำกันมากเกินไปและแก้ให้หลากหลาย
        3. ปรับประโยคที่อ่านแข็งหรือเหมือนแปลตรงๆ ให้อ่านเป็นธรรมชาติมากขึ้น
        4. ส่งคืนบทแปลที่แก้ไขแล้ว
        """,
        expected_output="บทแปลที่แก้ไขสำนวนซ้ำและอัปเดตชื่อแล้ว",
        agent=style_checker_agent,
        context=[translate_task, glossary_task],
    )

    # ── Agent 5: Tone & Voice Keeper ──────────────────────────────────
    tone_task = Task(
        description="""
        รับบทแปลจาก Agent 3 แล้ว:
        1. ตรวจสอบว่า tone และ voice ของตัวละครแต่ละตัวสม่ำเสมอ
           (ตัวละครที่พูดหยาบ/สุภาพ/เป็นทางการ/เป็นกันเอง ต้องคงบุคลิกตลอด)
        2. ตรวจว่า tone โดยรวมของฉากแต่ละฉาก (ตึงเครียด/ตลก/โรแมนติก)
           สอดคล้องกับต้นฉบับ
        3. แก้ไขส่วนที่ tone ผิดเพี้ยน แล้วส่งคืนบทแปลที่ปรับแล้ว
        """,
        expected_output="บทแปลที่ tone และ voice ถูกต้องสม่ำเสมอ",
        agent=tone_agent,
        context=[context_task, style_task],
    )

    # ── Agent 6: QA Final Reviewer ────────────────────────────────────
    qa_task = Task(
        description=f"""
        ตรวจสอบรอบสุดท้ายสำหรับตอนที่ {chapter_num}:
        1. ตรวจว่าชื่อตัวละคร/สถานที่ใช้สอดคล้องกันตลอดทั้งตอน
        2. ตรวจว่าไม่มีประโยคหลุด หรือเนื้อหาขาดหายไป
        3. ตรวจว่าตัวเลข บทที่ ลำดับเหตุการณ์ถูกต้อง
        4. เขียนสรุปตอนนี้ (3-5 ประโยค) สำหรับใช้เป็น context ตอนต่อไป

        ให้ผลลัพธ์เป็น:
        FINAL_TRANSLATION:
        (บทแปลฉบับสมบูรณ์)

        CHAPTER_SUMMARY:
        (สรุปตอน 3-5 ประโยค)
        """,
        expected_output="บทแปลสมบูรณ์ + สรุปตอน",
        agent=qa_agent,
        context=[tone_task, glossary_task],
    )

    return [context_task, translate_task, glossary_task, style_task, tone_task, qa_task]


# ─────────────────────────────────────────────────────────────────────
# 4. Parse output จาก QA Agent
# ─────────────────────────────────────────────────────────────────────

def parse_qa_output(raw: str) -> tuple[str, str]:
    """แยก FINAL_TRANSLATION และ CHAPTER_SUMMARY จาก output ของ Agent 6"""
    translation = ""
    summary     = ""

    if "FINAL_TRANSLATION:" in raw:
        parts = raw.split("FINAL_TRANSLATION:")
        rest  = parts[1]
        if "CHAPTER_SUMMARY:" in rest:
            translation, summary = rest.split("CHAPTER_SUMMARY:")
        else:
            translation = rest
    else:
        translation = raw  # fallback ถ้า format ไม่ตรง

    return translation.strip(), summary.strip()


def parse_glossary_json(raw: str) -> dict:
    """แยก JSON glossary จาก text ของ Agent 1 หรือ 2"""
    try:
        match = re.search(r'\{[\s\S]*"characters"[\s\S]*\}', raw)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {"characters": {}, "places": {}, "terms": {}}


# ─────────────────────────────────────────────────────────────────────
# 5. Main Pipeline
# ─────────────────────────────────────────────────────────────────────

def translate_chapter(chapter_text: str, chapter_num: int) -> str:
    """
    แปล 1 ตอน ครบ pipeline 6 agents
    โหลด/บันทึก Glossary DB อัตโนมัติ
    """

    # โหลด Glossary สะสมจากทุกตอนก่อนหน้า
    glossary = load_glossary()
    recent_context   = get_recent_context(glossary, last_n=3)
    glossary_snapshot = {
        "characters": glossary.characters,
        "places":     glossary.places,
        "terms":      glossary.terms,
    }

    # สร้าง tasks
    tasks = create_tasks(chapter_text, chapter_num, glossary_snapshot, recent_context)

    crew = Crew(
        agents=[
            context_agent,
            translator_agent,
            glossary_agent,
            style_checker_agent,
            tone_agent,
            qa_agent,
        ],
        tasks=tasks,
        process=Process.sequential,
        verbose=True,
    )

    # รัน pipeline พร้อม retry อัตโนมัติ
    raw_result = manager.call_with_retry(crew.kickoff, agent_role=f"Chapter{chapter_num}")

    # Parse output
    raw_str = str(raw_result)
    final_translation, chapter_summary = parse_qa_output(raw_str)

    # หา glossary ใหม่จาก output ของ task (ดึงจาก translate_task)
    new_glossary_data = parse_glossary_json(raw_str)

    # อัปเดต Glossary DB
    glossary = merge_glossary(glossary, new_glossary_data)
    if chapter_summary:
        glossary = add_chapter_summary(glossary, chapter_summary, chapter_num)
    save_glossary(glossary)

    # แสดงสถานะ key
    manager.status()

    return final_translation


# ─────────────────────────────────────────────────────────────────────
# 6. ตัวอย่างการใช้งาน
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    chapter_text = """
    Chapter 1: Awakening in Another World
    Rimuru opened his eyes to find himself in a dark cave.
    The slime body felt strange but oddly comfortable.
    Ahead, a massive dragon named Veldora sat chained by divine light.
    "You there, little slime. What is your name?" Veldora rumbled.
    """

    print("🚀 เริ่มแปลตอนที่ 1...")
    result = translate_chapter(chapter_text, chapter_num=1)

    print("\n" + "="*60)
    print("✅ ผลลัพธ์การแปลตอนที่ 1:")
    print("="*60)
    print(result)
