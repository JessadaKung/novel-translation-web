"""
glossary_db.py
--------------
Glossary Database — เก็บ/โหลด Glossary สะสมทุกตอน (JSON file)
"""

import json
import os
from dataclasses import dataclass, field, asdict

GLOSSARY_PATH = "glossary_db.json"


@dataclass
class Glossary:
    characters: dict[str, str] = field(default_factory=dict)  # EN → TH
    places:     dict[str, str] = field(default_factory=dict)
    terms:      dict[str, str] = field(default_factory=dict)
    chapter_summaries: list[str] = field(default_factory=list)  # สรุปแต่ละตอน


def load_glossary() -> Glossary:
    """โหลด Glossary จากไฟล์ ถ้าไม่มีให้คืน Glossary ว่าง"""
    if not os.path.exists(GLOSSARY_PATH):
        return Glossary()
    with open(GLOSSARY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Glossary(**data)


def save_glossary(glossary: Glossary) -> None:
    """บันทึก Glossary ลงไฟล์"""
    with open(GLOSSARY_PATH, "w", encoding="utf-8") as f:
        json.dump(asdict(glossary), f, ensure_ascii=False, indent=2)
    print(f"✅ บันทึก Glossary แล้ว ({GLOSSARY_PATH})")


def merge_glossary(base: Glossary, new_data: dict) -> Glossary:
    """
    รวม Glossary ใหม่เข้ากับของเดิม (ไม่ทับของเดิม)
    new_data รูปแบบ: {"characters": {}, "places": {}, "terms": {}}
    """
    base.characters.update(new_data.get("characters", {}))
    base.places.update(new_data.get("places", {}))
    base.terms.update(new_data.get("terms", {}))
    return base


def add_chapter_summary(glossary: Glossary, summary: str, chapter_num: int) -> Glossary:
    """เพิ่มสรุปตอนเข้า Glossary"""
    entry = f"[ตอนที่ {chapter_num}] {summary}"
    glossary.chapter_summaries.append(entry)
    return glossary


def get_recent_context(glossary: Glossary, last_n: int = 3) -> str:
    """ดึงสรุปตอนล่าสุด N ตอน สำหรับให้ Agent 4 ใช้"""
    recent = glossary.chapter_summaries[-last_n:]
    if not recent:
        return "ยังไม่มีประวัติตอนก่อนหน้า"
    return "\n".join(recent)
