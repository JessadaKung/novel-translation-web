"""
main.py — FastAPI Backend for Novel Translation Dashboard
"""

import asyncio
import json
import os
import re
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, PlainTextResponse
from pydantic import BaseModel

from glossary_db import (
    Glossary, load_glossary, save_glossary,
    merge_glossary, add_chapter_summary, get_recent_context
)

app = FastAPI(title="Novel Translation API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory job store ──────────────────────────────────────────────
jobs: dict[str, dict] = {}  # job_id → {status, progress, logs, result}
job_events: dict[str, asyncio.Queue] = {}
OUTPUT_ROOT = Path(__file__).resolve().parent / "outputs"

AGENT_STEPS = [
    {"id": "agent4", "name": "Context Manager",        "icon": "ti-database",       "order": 1},
    {"id": "agent1", "name": "Novel Translator",        "icon": "ti-language",       "order": 2},
    {"id": "agent2", "name": "Glossary Researcher",     "icon": "ti-books",          "order": 3},
    {"id": "agent3", "name": "Style Checker",           "icon": "ti-pencil",         "order": 4},
    {"id": "agent5", "name": "Tone & Voice Keeper",     "icon": "ti-microphone",     "order": 5},
    {"id": "agent6", "name": "QA Final Reviewer",       "icon": "ti-shield-check",   "order": 6},
]


# ── Models ────────────────────────────────────────────────────────────

class TranslateRequest(BaseModel):
    chapter_text: str
    chapter_num: int
    api_keys: list[str]
    output_folder: str = "translated"
    source_name: Optional[str] = None

class GlossaryUpdateRequest(BaseModel):
    characters: dict[str, str] = {}
    places: dict[str, str] = {}
    terms: dict[str, str] = {}

class GlossarySummaryDelete(BaseModel):
    index: int


# ── SSE Helper ────────────────────────────────────────────────────────

def push_event(job_id: str, event_type: str, data: dict):
    """Push an SSE event to the job's queue"""
    q = job_events.get(job_id)
    if q:
        try:
            q.put_nowait({"type": event_type, "data": data})
        except asyncio.QueueFull:
            pass


def safe_path_part(value: str, fallback: str = "translated") -> str:
    value = (value or "").strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(". ")
    return value or fallback


def save_translation_output(
    chapter_num: int,
    translation: str,
    summary: str,
    output_folder: str,
    source_name: Optional[str] = None,
) -> dict:
    folder_name = safe_path_part(output_folder)
    folder = OUTPUT_ROOT / folder_name
    folder.mkdir(parents=True, exist_ok=True)

    source_stem = safe_path_part(Path(source_name or "").stem, "")
    suffix = f" - {source_stem}" if source_stem else ""
    filename = f"chapter-{chapter_num:04d}{suffix}.txt"
    path = folder / filename
    content = translation.strip()
    if summary.strip():
        content = f"{content}\n\n---\nSummary:\n{summary.strip()}\n"
    path.write_text(content, encoding="utf-8")
    return {
        "folder": folder_name,
        "filename": filename,
        "relative_path": f"{folder_name}/{filename}",
        "size": path.stat().st_size,
    }


# ── Translation Job (runs in thread) ─────────────────────────────────

def _run_translation_job(
    job_id: str,
    chapter_text: str,
    chapter_num: int,
    api_keys: list[str],
    output_folder: str,
    source_name: Optional[str],
):
    """Actual CrewAI pipeline execution (blocking — run in thread pool)"""
    import importlib, sys

    jobs[job_id]["status"] = "running"
    jobs[job_id]["started_at"] = time.time()
    loop = asyncio.new_event_loop()

    def emit(event_type, data):
        loop.run_until_complete(asyncio.coroutine(lambda: None)())
        q = job_events.get(job_id)
        if q:
            asyncio.run_coroutine_threadsafe(q.put({"type": event_type, "data": data}), main_loop)

    # Monkey-patch verbose output to capture agent logs
    logs = jobs[job_id]["logs"]

    try:
        from llm_manager import LLMManager
        from glossary_db import load_glossary, save_glossary, merge_glossary, add_chapter_summary, get_recent_context

        manager = LLMManager(api_keys=api_keys)

        # Report key status
        key_status = []
        for k in manager.keys:
            key_status.append({
                "label": k.label,
                "daily_limit": k.daily_limit,
                "requests_today": k.requests_today,
                "cooldown_until": k.cooldown_until,
                "failed_count": k.failed_count,
            })
        push_event(job_id, "key_status", {"keys": key_status})

        glossary = load_glossary()
        recent_context = get_recent_context(glossary, last_n=3)
        glossary_snapshot = {
            "characters": glossary.characters,
            "places": glossary.places,
            "terms": glossary.terms,
        }

        # Import crew
        from crewai import Agent, Task, Crew, Process

        # ── Build agents ──
        agents_list = []
        for step in AGENT_STEPS:
            push_event(job_id, "agent_start", {"agent_id": step["id"], "name": step["name"]})
            logs.append(f"[{step['name']}] กำลังเริ่มทำงาน...")
            push_event(job_id, "log", {"message": logs[-1]})

        # Build all agents
        context_agent = Agent(
            role="Context Manager",
            goal="ดึง context และ Glossary จากตอนก่อนหน้า",
            backstory="ผู้เชี่ยวชาญด้านการจัดการข้อมูลนิยาย",
            llm=manager.get_llm(agent_role="Agent4-Context"),
            verbose=False,
        )
        translator_agent = Agent(
            role="Novel Translator",
            goal="แปลนิยายจากต้นฉบับเป็นภาษาไทย",
            backstory="นักแปลนิยายมืออาชีพ",
            llm=manager.get_llm(agent_role="Agent1-Translator"),
            verbose=False,
        )
        glossary_agent = Agent(
            role="Glossary Researcher",
            goal="ค้นหาและยืนยันชื่อตัวละคร สถานที่ คำศัพท์เฉพาะ",
            backstory="ผู้เชี่ยวชาญด้านการค้นหาข้อมูลและยืนยัน Glossary",
            llm=manager.get_llm(agent_role="Agent2-Glossary"),
            verbose=False,
        )
        style_agent = Agent(
            role="Style & Repetition Checker",
            goal="ตรวจสอบสำนวนซ้ำ",
            backstory="บรรณาธิการอาวุโส",
            llm=manager.get_llm(agent_role="Agent3-StyleChecker"),
            verbose=False,
        )
        tone_agent = Agent(
            role="Tone & Voice Keeper",
            goal="ตรวจสอบ tone และ voice ของตัวละคร",
            backstory="นักเขียนและนักแปลเชี่ยวชาญด้านบุคลิกตัวละคร",
            llm=manager.get_llm(agent_role="Agent5-ToneKeeper"),
            verbose=False,
        )
        qa_agent = Agent(
            role="QA Final Reviewer",
            goal="ตรวจสอบรอบสุดท้ายก่อน output",
            backstory="QA specialist ที่ผ่านการตรวจงานแปลนิยายมาหลายร้อยตอน",
            llm=manager.get_llm(agent_role="Agent6-QA"),
            verbose=False,
        )

        # ── Build tasks ──
        context_task = Task(
            description=f"""
            เตรียม context สำหรับการแปลตอนที่ {chapter_num}
            สรุปตอนก่อนหน้า: {recent_context}
            Glossary: {json.dumps(glossary_snapshot, ensure_ascii=False)}
            ให้สรุปเหตุการณ์สำคัญ ตัวละคร และ Glossary ที่ต้องระวัง
            """,
            expected_output="สรุป context พร้อมข้อมูลตัวละครและ Glossary",
            agent=context_agent,
        )
        translate_task = Task(
            description=f"""
            แปลนิยายตอนที่ {chapter_num} เป็นภาษาไทย
            ต้นฉบับ: {chapter_text}
            ให้ผลลัพธ์เป็น: 1.บทแปล 2.Glossary JSON: {{"characters":{{}},"places":{{}},"terms":{{}}}}
            """,
            expected_output="บทแปลภาษาไทย + Glossary JSON",
            agent=translator_agent,
            context=[context_task],
        )
        glossary_task = Task(
            description="รับ Glossary ใหม่จาก Agent 1 ยืนยันและอัปเดต ส่งคืน JSON",
            expected_output="Glossary JSON ที่ยืนยันแล้ว",
            agent=glossary_agent,
            context=[translate_task],
        )
        style_task = Task(
            description="แทนชื่อจาก Glossary ที่อัปเดต ตรวจสำนวนซ้ำ ปรับให้เป็นธรรมชาติ",
            expected_output="บทแปลที่แก้ไขสำนวนซ้ำแล้ว",
            agent=style_agent,
            context=[translate_task, glossary_task],
        )
        tone_task = Task(
            description="ตรวจ tone/voice ของตัวละคร แก้ส่วนที่ผิดเพี้ยน",
            expected_output="บทแปลที่ tone/voice ถูกต้อง",
            agent=tone_agent,
            context=[context_task, style_task],
        )
        qa_task = Task(
            description=f"""
            ตรวจสอบรอบสุดท้ายตอนที่ {chapter_num}
            ผลลัพธ์:
            FINAL_TRANSLATION:
            (บทแปลฉบับสมบูรณ์)
            CHAPTER_SUMMARY:
            (สรุปตอน 3-5 ประโยค)
            """,
            expected_output="บทแปลสมบูรณ์ + สรุปตอน",
            agent=qa_agent,
            context=[tone_task, glossary_task],
        )

        tasks = [context_task, translate_task, glossary_task, style_task, tone_task, qa_task]
        agent_objs = [context_agent, translator_agent, glossary_agent, style_agent, tone_agent, qa_agent]

        task_index = {"value": 0}

        def on_task_done(_task_output):
            idx = task_index["value"]
            if idx < len(AGENT_STEPS):
                done_step = AGENT_STEPS[idx]
                logs.append(f"[{done_step['name']}] เสร็จแล้ว")
                push_event(job_id, "agent_done", {"agent_id": done_step["id"]})
                push_event(job_id, "log", {"message": logs[-1]})
            task_index["value"] += 1
            if task_index["value"] < len(AGENT_STEPS):
                next_step = AGENT_STEPS[task_index["value"]]
                logs.append(f"[{next_step['name']}] กำลังทำงาน...")
                push_event(job_id, "agent_running", {"agent_id": next_step["id"]})
                push_event(job_id, "log", {"message": logs[-1]})

        first_step = AGENT_STEPS[0]
        logs.append(f"[{first_step['name']}] กำลังทำงาน...")
        push_event(job_id, "agent_running", {"agent_id": first_step["id"]})
        push_event(job_id, "log", {"message": logs[-1]})

        crew = Crew(
            agents=agent_objs,
            tasks=tasks,
            process=Process.sequential,
            verbose=False,
            task_callback=on_task_done,
        )

        raw_result = manager.call_with_retry(crew.kickoff, agent_role=f"Chapter{chapter_num}")
        raw_str = str(raw_result)

        # Parse output
        translation = ""
        summary = ""
        if "FINAL_TRANSLATION:" in raw_str:
            parts = raw_str.split("FINAL_TRANSLATION:")
            rest = parts[1]
            if "CHAPTER_SUMMARY:" in rest:
                translation, summary = rest.split("CHAPTER_SUMMARY:")
            else:
                translation = rest
        else:
            translation = raw_str

        translation = translation.strip()
        summary = summary.strip()

        # Parse glossary
        new_glossary_data = {"characters": {}, "places": {}, "terms": {}}
        try:
            match = re.search(r'\{[\s\S]*"characters"[\s\S]*\}', raw_str)
            if match:
                new_glossary_data = json.loads(match.group())
        except Exception:
            pass

        # Update glossary DB
        glossary = merge_glossary(glossary, new_glossary_data)
        if summary:
            glossary = add_chapter_summary(glossary, summary, chapter_num)
        save_glossary(glossary)

        # Final key status
        key_status_final = []
        for k in manager.keys:
            key_status_final.append({
                "label": k.label,
                "daily_limit": k.daily_limit,
                "requests_today": k.requests_today,
                "cooldown_until": k.cooldown_until,
                "failed_count": k.failed_count,
            })

        for idx in range(task_index["value"], len(AGENT_STEPS)):
            push_event(job_id, "agent_done", {"agent_id": AGENT_STEPS[idx]["id"]})

        output_file = save_translation_output(
            chapter_num=chapter_num,
            translation=translation,
            summary=summary,
            output_folder=output_folder,
            source_name=source_name,
        )
        logs.append(f"บันทึกไฟล์แล้ว: {output_file['relative_path']}")
        push_event(job_id, "log", {"message": logs[-1]})

        jobs[job_id]["status"] = "done"
        jobs[job_id]["result"] = {
            "translation": translation,
            "summary": summary,
            "new_glossary": new_glossary_data,
            "key_status": key_status_final,
            "output_file": output_file,
        }
        push_event(job_id, "done", {
            "translation": translation,
            "summary": summary,
            "new_glossary": new_glossary_data,
            "key_status": key_status_final,
            "output_file": output_file,
        })

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        push_event(job_id, "error", {"message": str(e)})

main_loop: asyncio.AbstractEventLoop = None


@app.on_event("startup")
async def startup():
    global main_loop
    main_loop = asyncio.get_event_loop()


# ── Routes ────────────────────────────────────────────────────────────

@app.post("/api/translate")
async def start_translation(req: TranslateRequest, background_tasks: BackgroundTasks):
    if not req.api_keys or not any(k.strip() for k in req.api_keys):
        raise HTTPException(400, "ต้องมีอย่างน้อย 1 API Key")
    if not req.chapter_text.strip():
        raise HTTPException(400, "กรุณาใส่เนื้อหาต้นฉบับ")

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "pending",
        "logs": [],
        "result": None,
        "error": None,
        "chapter_num": req.chapter_num,
        "output_folder": req.output_folder,
        "source_name": req.source_name,
        "created_at": time.time(),
    }
    job_events[job_id] = asyncio.Queue(maxsize=500)

    import concurrent.futures
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        executor,
        _run_translation_job,
        job_id, req.chapter_text, req.chapter_num,
        [k.strip() for k in req.api_keys if k.strip()],
        req.output_folder,
        req.source_name,
    )

    return {"job_id": job_id}


@app.get("/api/translate/{job_id}/stream")
async def stream_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "ไม่พบ Job")

    async def event_generator():
        # Send current status first
        job = jobs[job_id]
        yield f"data: {json.dumps({'type': 'status', 'data': {'status': job['status']}})}\n\n"

        if job["status"] in ("done", "error"):
            if job["status"] == "done":
                yield f"data: {json.dumps({'type': 'done', 'data': job['result']})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'data': {'message': job['error']}})}\n\n"
            return

        q = job_events.get(job_id)
        if not q:
            return

        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                yield "data: {\"type\":\"ping\"}\n\n"
                if jobs[job_id]["status"] in ("done", "error"):
                    break

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/translate/{job_id}")
async def get_job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "ไม่พบ Job")
    job = jobs[job_id]
    return {
        "job_id": job_id,
        "status": job["status"],
        "chapter_num": job.get("chapter_num"),
        "result": job.get("result"),
        "error": job.get("error"),
        "logs": job.get("logs", []),
    }


@app.get("/api/files")
async def list_output_files():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    folders = []
    for folder in sorted([p for p in OUTPUT_ROOT.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        files = []
        for path in sorted(folder.glob("*"), key=lambda p: p.name.lower()):
            if path.is_file():
                files.append({
                    "name": path.name,
                    "relative_path": f"{folder.name}/{path.name}",
                    "size": path.stat().st_size,
                    "modified_at": path.stat().st_mtime,
                })
        folders.append({"name": folder.name, "files": files})
    return {"root": str(OUTPUT_ROOT), "folders": folders}


@app.get("/api/files/{folder}/{filename}")
async def read_output_file(folder: str, filename: str):
    folder_name = safe_path_part(folder)
    file_name = safe_path_part(filename, "")
    path = OUTPUT_ROOT / folder_name / file_name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "ไม่พบไฟล์")
    return PlainTextResponse(path.read_text(encoding="utf-8"))


# ── Glossary Routes ───────────────────────────────────────────────────

@app.get("/api/glossary")
async def get_glossary():
    g = load_glossary()
    return {
        "characters": g.characters,
        "places": g.places,
        "terms": g.terms,
        "chapter_summaries": g.chapter_summaries,
    }


@app.patch("/api/glossary")
async def update_glossary(req: GlossaryUpdateRequest):
    g = load_glossary()
    g.characters.update(req.characters)
    g.places.update(req.places)
    g.terms.update(req.terms)
    save_glossary(g)
    return {"ok": True}


@app.delete("/api/glossary/character/{key}")
async def delete_character(key: str):
    g = load_glossary()
    g.characters.pop(key, None)
    save_glossary(g)
    return {"ok": True}


@app.delete("/api/glossary/place/{key}")
async def delete_place(key: str):
    g = load_glossary()
    g.places.pop(key, None)
    save_glossary(g)
    return {"ok": True}


@app.delete("/api/glossary/term/{key}")
async def delete_term(key: str):
    g = load_glossary()
    g.terms.pop(key, None)
    save_glossary(g)
    return {"ok": True}


@app.delete("/api/glossary/summary/{index}")
async def delete_summary(index: int):
    g = load_glossary()
    if 0 <= index < len(g.chapter_summaries):
        g.chapter_summaries.pop(index)
        save_glossary(g)
    return {"ok": True}


@app.delete("/api/glossary/all")
async def clear_glossary():
    from glossary_db import Glossary
    save_glossary(Glossary())
    return {"ok": True}


# ── Upload Route ──────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("utf-8-sig")
        except Exception:
            raise HTTPException(400, "ไม่สามารถอ่านไฟล์ได้ — รองรับเฉพาะ UTF-8")
    return {"text": text, "filename": file.filename, "size": len(content)}


# ── Key Status Route ──────────────────────────────────────────────────

@app.post("/api/key-status")
async def check_key_status(body: dict):
    api_keys = body.get("api_keys", [])
    api_keys = [k.strip() for k in api_keys if k.strip()]
    if not api_keys:
        raise HTTPException(400, "ไม่มี API Keys")
    from llm_manager import LLMManager
    mgr = LLMManager(api_keys=api_keys)
    now = time.time()
    result = []
    for k in mgr.keys:
        cooldown_left = max(0.0, k.cooldown_until - now)
        result.append({
            "label": k.label,
            "daily_limit": k.daily_limit,
            "requests_today": k.requests_today,
            "cooldown_until": k.cooldown_until,
            "cooldown_left": round(cooldown_left),
            "failed_count": k.failed_count,
            "status": "cooldown" if cooldown_left > 0 else "ready",
        })
    return {"keys": result}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
