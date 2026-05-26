import { useState, useEffect, useRef, useCallback } from "react";

const API_BASE = import.meta.env.VITE_API_URL ?? "";
const STORAGE_KEY = "novelflow.translateState.v1";
const SOURCE_DB_NAME = "novelflow-source-files";
const SOURCE_DB_VERSION = 1;
const SOURCE_STORE = "batchFiles";

const AGENTS = [
  { id: "agent4", name: "Context Manager",     icon: "🗂️",  desc: "ดึง context + Glossary" },
  { id: "agent1", name: "Novel Translator",    icon: "🌏",  desc: "แปลต้นฉบับเป็นไทย" },
  { id: "agent2", name: "Glossary Researcher", icon: "📚",  desc: "ยืนยันชื่อตัวละคร/สถานที่" },
  { id: "agent3", name: "Style Checker",       icon: "✏️",  desc: "ตรวจสำนวนซ้ำ" },
  { id: "agent5", name: "Tone & Voice",        icon: "🎭",  desc: "ตรวจ tone ตัวละคร" },
  { id: "agent6", name: "QA Reviewer",         icon: "🛡️",  desc: "ตรวจรอบสุดท้าย" },
];

// ── Tabs ────────────────────────────────────────────────────────────
const TABS = ["translate", "glossary", "keys"];

export default function App() {
  const savedState = loadSavedState();
  const [tab, setTab] = useState(savedState.tab || "translate");
  const [apiKeys, setApiKeys] = useState(savedState.apiKeys?.length ? savedState.apiKeys : [""]);
  const [chapterNum, setChapterNum] = useState(savedState.chapterNum || 1);
  const [chapterText, setChapterText] = useState(savedState.chapterText || "");
  const [batchFiles, setBatchFiles] = useState(savedState.batchFiles || []);
  const [outputFolder, setOutputFolder] = useState(savedState.outputFolder || "translated");
  const [outputFiles, setOutputFiles] = useState([]);
  const [selectedOutput, setSelectedOutput] = useState(savedState.selectedOutput || null);
  const [outputLoading, setOutputLoading] = useState(false);
  const [batchRunning, setBatchRunning] = useState(false);
  const [currentBatchIndex, setCurrentBatchIndex] = useState(-1);
  const [batchResults, setBatchResults] = useState([]);
  const [jobId, setJobId] = useState(savedState.jobId || null);
  const [jobStatus, setJobStatus] = useState(savedState.jobStatus || "idle"); // idle|running|done|error
  const [agentStates, setAgentStates] = useState(savedState.agentStates || {});
  const [logs, setLogs] = useState(savedState.logs || []);
  const [result, setResult] = useState(savedState.result || null);
  const [keyStatus, setKeyStatus] = useState(savedState.keyStatus || []);
  const [glossary, setGlossary] = useState({ characters: {}, places: {}, terms: {}, chapter_summaries: [] });
  const [glossaryLoading, setGlossaryLoading] = useState(false);
  const [newEntry, setNewEntry] = useState({ type: "characters", en: "", th: "" });
  const [copied, setCopied] = useState(false);
  const logsRef = useRef(null);
  const esRef = useRef(null);
  const folderInputRef = useRef(null);
  const batchQueueRef = useRef([]);
  const batchRunningRef = useRef(false);
  const currentBatchIndexRef = useRef(-1);
  const hasRestoredJobRef = useRef(false);

  useEffect(() => {
    batchQueueRef.current = batchFiles;
  }, [batchFiles]);

  // Load glossary on mount & tab change
  useEffect(() => {
    if (tab === "glossary") fetchGlossary();
    if (tab === "translate") fetchOutputFiles();
  }, [tab]);

  useEffect(() => {
    if (folderInputRef.current) {
      folderInputRef.current.setAttribute("webkitdirectory", "");
      folderInputRef.current.setAttribute("directory", "");
    }
  }, []);

  useEffect(() => {
    restoreSavedBatchFiles();
  }, []);

  async function restoreSavedBatchFiles() {
    try {
      const savedBatch = await loadBatchFilesFromDb();
      if (!savedBatch.length) return;
      setBatchFiles(savedBatch);
      setBatchResults(savedBatch.map(file => ({ ...file, status: file.status || "queued" })));
      batchQueueRef.current = savedBatch;
      if (!chapterText && savedBatch[0]) {
        setChapterText(savedBatch[0].text);
        setChapterNum(savedBatch[0].chapterNum);
      }
    } catch (e) {
      setLogs(l => [...l, `⚠️ โหลดรายการไฟล์เดิมไม่ได้: ${e.message}`]);
    }
  }

  useEffect(() => {
    const state = {
      tab,
      apiKeys,
      chapterNum,
      chapterText,
      batchFiles: toPersistableBatchFiles(batchFiles),
      outputFolder,
      jobId,
      jobStatus,
      agentStates,
      logs: logs.slice(-200),
      result,
      keyStatus,
      selectedOutput,
      savedAt: Date.now(),
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }, [tab, apiKeys, chapterNum, chapterText, batchFiles, outputFolder, jobId, jobStatus, agentStates, logs, result, keyStatus, selectedOutput]);

  useEffect(() => {
    if (!jobId || hasRestoredJobRef.current) return;
    hasRestoredJobRef.current = true;
    restoreJob(jobId);
  }, [jobId]);

  useEffect(() => {
    return () => {
      if (esRef.current) esRef.current.close();
    };
  }, []);

  async function fetchGlossary() {
    setGlossaryLoading(true);
    try {
      const r = await fetch(`${API_BASE}/api/glossary`);
      const d = await r.json();
      setGlossary(d);
    } catch {}
    setGlossaryLoading(false);
  }

  async function fetchOutputFiles() {
    setOutputLoading(true);
    try {
      const r = await fetch(`${API_BASE}/api/files`);
      const d = await r.json();
      setOutputFiles(d.folders || []);
    } catch {}
    setOutputLoading(false);
  }

  async function restoreJob(id) {
    try {
      const r = await fetch(`${API_BASE}/api/translate/${id}`);
      if (!r.ok) return;
      const job = await r.json();
      if (job.status === "running" || job.status === "pending") {
        setJobStatus("running");
        setLogs(l => [...l, "↻ เชื่อมต่อ job เดิมหลัง reload..."]);
        connectStream(id);
      } else if (job.status === "done") {
        setJobStatus("done");
        setResult(job.result);
        if (job.result?.key_status) setKeyStatus(job.result.key_status);
        fetchOutputFiles();
      } else if (job.status === "error") {
        setJobStatus("error");
        setLogs(l => [...l, `❌ Job เดิมผิดพลาด: ${job.error}`]);
      }
    } catch {}
  }

  async function openOutputFile(file) {
    try {
      const [folder, ...rest] = file.relative_path.split("/");
      const filename = rest.join("/");
      const r = await fetch(`${API_BASE}/api/files/${encodeURIComponent(folder)}/${encodeURIComponent(filename)}`);
      const text = await r.text();
      setSelectedOutput({ ...file, text });
    } catch (e) {
      setLogs(l => [...l, `❌ เปิดไฟล์ output ไม่ได้: ${e.message}`]);
    }
  }

  // Auto-scroll logs
  useEffect(() => {
    if (logsRef.current) logsRef.current.scrollTop = logsRef.current.scrollHeight;
  }, [logs]);

  // ── Start translation ─────────────────────────────────────────────
  async function startTranslation() {
    if (!chapterText.trim()) return;
    const keys = apiKeys.filter(k => k.trim());
    if (!keys.length) return;

    setBatchRunning(false);
    batchRunningRef.current = false;
    setCurrentBatchIndex(-1);
    currentBatchIndexRef.current = -1;
    setBatchResults([]);
    await startJob(chapterText, chapterNum, keys, null);
  }

  async function startJob(text, num, keys, sourceName = null) {
    setJobStatus("running");
    setAgentStates({});
    setLogs([]);
    setResult(null);
    setKeyStatus([]);

    try {
      const r = await fetch(`${API_BASE}/api/translate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chapter_text: text,
          chapter_num: num,
          api_keys: keys,
          output_folder: outputFolder,
          source_name: sourceName,
        }),
      });
      const { job_id } = await r.json();
      setJobId(job_id);
      connectStream(job_id);
    } catch (e) {
      setJobStatus("error");
      setLogs(l => [...l, `❌ เชื่อมต่อ backend ไม่ได้: ${e.message}`]);
    }
  }

  async function startBatch() {
    const keys = apiKeys.filter(k => k.trim());
    if (!keys.length || !batchFiles.length) return;

    batchRunningRef.current = true;
    batchQueueRef.current = batchFiles;
    currentBatchIndexRef.current = 0;
    setBatchRunning(true);
    setCurrentBatchIndex(0);
    setBatchResults(batchFiles.map(f => ({ ...f, status: "queued" })));
    await startBatchItem(0, keys);
  }

  async function startBatchItem(index, keys = apiKeys.filter(k => k.trim())) {
    const item = batchQueueRef.current[index];
    if (!item) {
      batchRunningRef.current = false;
      setBatchRunning(false);
      setJobStatus("done");
      setLogs(l => [...l, "✅ แปลครบทุกไฟล์ในโฟลเดอร์แล้ว"]);
      fetchGlossary();
      return;
    }

    currentBatchIndexRef.current = index;
    setCurrentBatchIndex(index);
    setChapterNum(item.chapterNum);
    setChapterText(item.text);
    setBatchResults(r => r.map((entry, i) => (
      i === index ? { ...entry, status: "running" } : entry
    )));
    setLogs([`📚 เริ่มแปล ${item.name} เป็นตอนที่ ${item.chapterNum} (${index + 1}/${batchQueueRef.current.length})`]);
    await startJob(item.text, item.chapterNum, keys, item.name);
  }

  function connectStream(id) {
    if (esRef.current) esRef.current.close();
    const es = new EventSource(`${API_BASE}/api/translate/${id}/stream`);
    esRef.current = es;

    es.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        const { type, data } = msg;

        if (type === "agent_start") {
          setAgentStates(s => ({ ...s, [data.agent_id]: "pending" }));
        } else if (type === "agent_running") {
          setAgentStates(s => ({ ...s, [data.agent_id]: "running" }));
          setLogs(l => [...l, `▶ ${AGENTS.find(a => a.id === data.agent_id)?.name} กำลังทำงาน...`]);
        } else if (type === "agent_done") {
          setAgentStates(s => ({ ...s, [data.agent_id]: "done" }));
        } else if (type === "log") {
          setLogs(l => [...l, data.message]);
        } else if (type === "key_status") {
          setKeyStatus(data.keys || []);
        } else if (type === "done") {
          setJobStatus("done");
          setResult(data);
          setKeyStatus(data.key_status || []);
          setLogs(l => [...l, batchRunningRef.current ? "✅ ตอนนี้แปลเสร็จแล้ว" : "✅ แปลเสร็จเรียบร้อย!"]);
          AGENTS.forEach(a => setAgentStates(s => ({ ...s, [a.id]: "done" })));
          es.close();
          fetchGlossary();
          fetchOutputFiles();
          if (batchRunningRef.current) {
            const finishedIndex = currentBatchIndexRef.current;
            setBatchResults(r => r.map((entry, i) => (
              i === finishedIndex ? { ...entry, status: "done", result: data } : entry
            )));
            const nextIndex = finishedIndex + 1;
            if (nextIndex < batchQueueRef.current.length) {
              setTimeout(() => startBatchItem(nextIndex), 300);
            } else {
              batchRunningRef.current = false;
              setBatchRunning(false);
              setLogs(l => [...l, "✅ แปลครบทุกไฟล์ในโฟลเดอร์แล้ว"]);
            }
          }
        } else if (type === "error") {
          setJobStatus("error");
          setLogs(l => [...l, `❌ Error: ${data.message}`]);
          es.close();
          if (batchRunningRef.current) {
            const failedIndex = currentBatchIndexRef.current;
            setBatchResults(r => r.map((entry, i) => (
              i === failedIndex ? { ...entry, status: "error", error: data.message } : entry
            )));
            batchRunningRef.current = false;
            setBatchRunning(false);
          }
        }
      } catch {}
    };

    es.onerror = () => {
      if (jobStatus !== "done") {
        setLogs(l => [...l, "⚠️ การเชื่อมต่อขาด กำลังตรวจสอบสถานะ..."]);
      }
    };
  }

  async function uploadFile(file) {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch(`${API_BASE}/api/upload`, { method: "POST", body: fd });
      const d = await r.json();
      setChapterText(d.text);
      setLogs(l => [...l, `📁 โหลดไฟล์ ${d.filename} สำเร็จ (${d.size} bytes)`]);
    } catch (e) {
      setLogs(l => [...l, `❌ โหลดไฟล์ล้มเหลว: ${e.message}`]);
    }
  }

  async function uploadFiles(files, sourceLabel = "ไฟล์") {
    const selected = Array.from(files || [])
      .filter(file => /\.(txt|md)$/i.test(file.name))
      .sort((a, b) => (a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name, undefined, { numeric: true }));

    const firstChapter = Number(chapterNum) || 1;
    const loaded = await Promise.all(selected.map(async (file, index) => ({
      name: file.webkitRelativePath || file.name,
      chapterNum: firstChapter + index,
      text: await file.text(),
      size: file.size,
      status: "queued",
    })));

    batchQueueRef.current = loaded;
    setBatchFiles(loaded);
    setBatchResults(loaded);
    saveBatchFilesToDb(loaded).catch(e => {
      setLogs(l => [...l, `⚠️ บันทึกรายการไฟล์ไว้ใช้ต่อหลัง reload ไม่ได้: ${e.message}`]);
    });
    saveSourceFilesToServer(selected).then(count => {
      if (count) setLogs(l => [...l, `☁️ บันทึกต้นฉบับบน server แล้ว ${count} ไฟล์`]);
    }).catch(e => {
      setLogs(l => [...l, `⚠️ บันทึกต้นฉบับขึ้น server ไม่ได้: ${e.message}`]);
    });
    if (loaded[0]) {
      setChapterText(loaded[0].text);
      setLogs(l => [...l, `📁 โหลด${sourceLabel}สำเร็จ ${loaded.length} ไฟล์ เริ่มจากตอนที่ ${firstChapter}`]);
    } else {
      setLogs(l => [...l, `⚠️ ไม่พบไฟล์ .txt หรือ .md ใน${sourceLabel}`]);
    }
  }

  async function saveSourceFilesToServer(files) {
    const fd = new FormData();
    for (const file of files) {
      const relativePath = file.webkitRelativePath || file.name;
      const uploadFile = file instanceof File
        ? file
        : new File([await file.text()], file.name, { type: "text/plain" });
      fd.append("files", uploadFile);
      fd.append("relative_paths", relativePath);
    }
    const r = await fetch(`${API_BASE}/api/source-files`, { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    return d.files?.length || 0;
  }

  async function handleUploadSelection(files) {
    const selected = Array.from(files || []).filter(file => /\.(txt|md)$/i.test(file.name));
    if (selected.length <= 1) {
      await uploadFile(selected[0]);
      return;
    }
    await uploadFiles(selected, "หลายไฟล์");
  }

  function readEntryFile(fileEntry, pathPrefix = "") {
    return new Promise((resolve, reject) => {
      fileEntry.file(file => {
        const path = `${pathPrefix}${file.name}`;
        resolve({
          name: file.name,
          size: file.size,
          webkitRelativePath: path,
          text: () => file.text(),
        });
      }, reject);
    });
  }

  async function readDirectoryEntry(directoryEntry, pathPrefix = "") {
    const reader = directoryEntry.createReader();
    const entries = [];

    while (true) {
      const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
      if (!batch.length) break;
      entries.push(...batch);
    }

    const files = await Promise.all(entries.map(entry => {
      const nextPrefix = `${pathPrefix}${directoryEntry.name}/`;
      if (entry.isFile) return readEntryFile(entry, nextPrefix);
      if (entry.isDirectory) return readDirectoryEntry(entry, nextPrefix);
      return [];
    }));

    return files.flat();
  }

  async function handleDropFiles(event) {
    event.preventDefault();
    const items = Array.from(event.dataTransfer.items || []);
    const entries = items.map(item => item.webkitGetAsEntry?.()).filter(Boolean);

    if (entries.length) {
      const files = (await Promise.all(entries.map(entry => {
        if (entry.isFile) return readEntryFile(entry);
        if (entry.isDirectory) return readDirectoryEntry(entry);
        return [];
      }))).flat();
      await handleUploadSelection(files);
      return;
    }

    await handleUploadSelection(event.dataTransfer.files);
  }

  function openSourceFile(item, index) {
    setChapterText(item.text);
    setChapterNum(item.chapterNum);
    setCurrentBatchIndex(index);
  }

  async function deleteGlossaryEntry(type, key) {
    await fetch(`${API_BASE}/api/glossary/${type}/${encodeURIComponent(key)}`, { method: "DELETE" });
    fetchGlossary();
  }

  async function addGlossaryEntry() {
    if (!newEntry.en.trim() || !newEntry.th.trim()) return;
    await fetch(`${API_BASE}/api/glossary`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [newEntry.type]: { [newEntry.en]: newEntry.th } }),
    });
    setNewEntry(e => ({ ...e, en: "", th: "" }));
    fetchGlossary();
  }

  async function clearGlossary() {
    if (!confirm("ลบ Glossary ทั้งหมด?")) return;
    await fetch(`${API_BASE}/api/glossary/all`, { method: "DELETE" });
    fetchGlossary();
  }

  function copyResult() {
    if (result?.translation) {
      navigator.clipboard.writeText(result.translation);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  async function refreshKeyStatus() {
    const keys = apiKeys.filter(k => k.trim());
    if (!keys.length) return;
    try {
      const r = await fetch(`${API_BASE}/api/key-status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_keys: keys }),
      });
      const d = await r.json();
      setKeyStatus(d.keys || []);
    } catch (e) {
      alert("ไม่สามารถตรวจสอบ key ได้: " + e.message);
    }
  }

  const agentProgress = AGENTS.filter(a => agentStates[a.id] === "done").length;
  const totalAgents = AGENTS.length;

  return (
    <div className="app">
      <header className="header">
        <div className="header-inner">
          <div className="logo">
            <span className="logo-icon">📖</span>
            <div>
              <h1>NovelFlow</h1>
              <p>AI Translation Pipeline</p>
            </div>
          </div>
          <nav className="tabs">
            {[
              { id: "translate", label: "แปลนิยาย", icon: "🌏" },
              { id: "glossary", label: "Glossary DB", icon: "📚" },
              { id: "keys", label: "API Keys", icon: "🔑" },
            ].map(t => (
              <button key={t.id} className={`tab-btn ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>
                <span>{t.icon}</span> {t.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="main">
        {/* ─── TRANSLATE TAB ─── */}
        {tab === "translate" && (
          <div className="translate-layout">
            {/* Left: Input Panel */}
            <div className="panel input-panel">
              <div className="panel-header">
                <h2>ต้นฉบับ</h2>
                <div className="chapter-control">
                  <label>ตอนที่</label>
                  <input type="number" min="1" value={chapterNum} onChange={e => setChapterNum(+e.target.value)} className="chapter-input" />
                </div>
              </div>

              {/* Upload zone */}
              <div className="upload-zone" onDrop={handleDropFiles} onDragOver={e => e.preventDefault()}>
                <input type="file" accept=".txt,.md" id="fileup" hidden multiple onChange={e => handleUploadSelection(e.target.files)} />
                <label htmlFor="fileup">
                  <span className="upload-icon">📄</span>
                  <span>วางไฟล์/โฟลเดอร์ หรือ <u>เลือกหลายไฟล์</u> (.txt, .md)</span>
                </label>
              </div>

              <div className="folder-zone">
                <input
                  ref={folderInputRef}
                  type="file"
                  id="folderup"
                  hidden
                  multiple
                  onChange={e => uploadFiles(e.target.files, "โฟลเดอร์")}
                />
                <label htmlFor="folderup" className="btn-ghost folder-btn">📂 เลือกโฟลเดอร์สำหรับแปลอัตโนมัติ</label>
                {batchFiles.length > 0 && (
                  <div className="batch-summary">
                    {batchFiles.length} ไฟล์ · ตอนที่ {batchFiles[0].chapterNum}-{batchFiles[batchFiles.length - 1].chapterNum}
                  </div>
                )}
              </div>

              {batchFiles.length > 0 && (
                <div className="source-manager">
                  <div className="source-manager-header">
                    <span>Source File Manager</span>
                    <button
                      className="btn-ghost-sm"
                      onClick={() => {
                        setBatchFiles([]);
                        setBatchResults([]);
                        batchQueueRef.current = [];
                        clearBatchFilesFromDb().catch(e => {
                          setLogs(l => [...l, `⚠️ ล้างรายการไฟล์เดิมไม่สำเร็จ: ${e.message}`]);
                        });
                      }}
                    >
                      ล้าง
                    </button>
                  </div>
                  <div className="source-list">
                    {batchFiles.map((item, index) => (
                      <button
                        key={`${item.name}-${index}`}
                        className={`source-row ${chapterNum === item.chapterNum ? "active" : ""}`}
                        onClick={() => openSourceFile(item, index)}
                      >
                        <span className="source-chapter">{item.chapterNum}</span>
                        <span className="source-name">{item.name}</span>
                        <span className="source-size">{Math.ceil(item.size / 1024)} KB</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="output-folder-control">
                <label>Output folder</label>
                <input
                  value={outputFolder}
                  onChange={e => setOutputFolder(e.target.value)}
                  placeholder="translated"
                />
              </div>

              <textarea
                className="chapter-textarea"
                placeholder="วางเนื้อหาต้นฉบับที่นี่..."
                value={chapterText}
                onChange={e => setChapterText(e.target.value)}
              />

              <div className="char-count">{chapterText.length.toLocaleString()} ตัวอักษร</div>

              {/* API Keys inline */}
              <div className="keys-inline">
                <div className="keys-row-header">
                  <span className="keys-label">🔑 API Keys</span>
                  <button className="btn-ghost-sm" onClick={() => setApiKeys(k => [...k, ""])}>+ เพิ่ม</button>
                </div>
                {apiKeys.map((k, i) => (
                  <div key={i} className="key-row">
                    <input
                      type="password"
                      className="key-input"
                      placeholder={`Key ${i + 1} — AIza...`}
                      value={k}
                      onChange={e => setApiKeys(keys => keys.map((v, j) => j === i ? e.target.value : v))}
                    />
                    {apiKeys.length > 1 && (
                      <button className="btn-ghost-sm danger" onClick={() => setApiKeys(keys => keys.filter((_, j) => j !== i))}>✕</button>
                    )}
                  </div>
                ))}
              </div>

              <button
                className={`btn-primary ${jobStatus === "running" ? "running" : ""}`}
                onClick={startTranslation}
                disabled={jobStatus === "running" || !chapterText.trim()}
              >
                {jobStatus === "running" ? (
                  <><span className="spinner" /> กำลังแปล...</>
                ) : "▶ เริ่มแปล"}
              </button>

              <button
                className={`btn-primary btn-secondary ${batchRunning ? "running" : ""}`}
                onClick={startBatch}
                disabled={jobStatus === "running" || batchRunning || !batchFiles.length}
              >
                {batchRunning ? (
                  <><span className="spinner" /> แปลอัตโนมัติ {currentBatchIndex + 1}/{batchFiles.length}</>
                ) : "▶ แปลทั้งโฟลเดอร์ทีละตอน"}
              </button>
            </div>

            {/* Right: Progress + Output */}
            <div className="right-column">
              {/* Agent Pipeline */}
              <div className="panel pipeline-panel">
                <div className="panel-header">
                  <h2>Pipeline Progress</h2>
                  {jobStatus === "running" && (
                    <span className="badge badge-running">กำลังทำงาน {agentProgress}/{totalAgents}</span>
                  )}
                  {jobStatus === "done" && (
                    <span className="badge badge-done">เสร็จสิ้น ✓</span>
                  )}
                </div>

                {jobStatus !== "idle" && (
                  <div className="progress-bar-outer">
                    <div className="progress-bar-inner" style={{ width: `${(agentProgress / totalAgents) * 100}%` }} />
                  </div>
                )}

                <div className="agents-grid">
                  {AGENTS.map((agent, idx) => {
                    const state = agentStates[agent.id] || "idle";
                    return (
                      <div key={agent.id} className={`agent-card agent-${state}`}>
                        <div className="agent-num">{idx + 1}</div>
                        <div className="agent-icon">{agent.icon}</div>
                        <div className="agent-info">
                          <div className="agent-name">{agent.name}</div>
                          <div className="agent-desc">{agent.desc}</div>
                        </div>
                        <div className="agent-status">
                          {state === "idle" && <span className="dot dot-idle" />}
                          {state === "pending" && <span className="dot dot-pending" />}
                          {state === "running" && <span className="spinner-sm" />}
                          {state === "done" && <span className="dot dot-done">✓</span>}
                        </div>
                      </div>
                    );
                  })}
                </div>

                {/* Logs */}
                {logs.length > 0 && (
                  <div className="logs-box" ref={logsRef}>
                    {logs.map((l, i) => <div key={i} className="log-line">{l}</div>)}
                  </div>
                )}

                {batchResults.length > 0 && (
                  <div className="batch-list">
                    {batchResults.map((item, i) => (
                      <div key={`${item.name}-${i}`} className={`batch-row batch-${item.status || "queued"}`}>
                        <span className="batch-index">{item.chapterNum}</span>
                        <span className="batch-name">{item.name}</span>
                        <span className="batch-state">
                          {item.status === "running" && <span className="spinner-sm" />}
                          {item.status === "done" && "✓"}
                          {item.status === "error" && "ผิดพลาด"}
                          {(!item.status || item.status === "queued") && "รอ"}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Result */}
              {result && (
                <div className="panel result-panel">
                  <div className="panel-header">
                    <h2>ผลลัพธ์การแปล — ตอนที่ {batchRunning || currentBatchIndex >= 0 ? batchFiles[currentBatchIndex]?.chapterNum || chapterNum : chapterNum}</h2>
                    <button className="btn-ghost" onClick={copyResult}>
                      {copied ? "✓ คัดลอกแล้ว" : "📋 คัดลอก"}
                    </button>
                  </div>
                  <div className="result-text">{result.translation}</div>
                  {result.output_file && (
                    <button className="saved-file" onClick={() => openOutputFile(result.output_file)}>
                      <span>บันทึกแล้ว</span>
                      <strong>{result.output_file.relative_path}</strong>
                    </button>
                  )}

                  {result.summary && (
                    <div className="summary-box">
                      <div className="summary-label">📝 สรุปตอน</div>
                      <p>{result.summary}</p>
                    </div>
                  )}

                  {result.new_glossary && Object.values(result.new_glossary).some(v => Object.keys(v).length > 0) && (
                    <div className="new-glossary-box">
                      <div className="summary-label">✨ Glossary ใหม่ที่พบ</div>
                      {Object.entries(result.new_glossary).map(([cat, entries]) =>
                        Object.keys(entries).length > 0 && (
                          <div key={cat} className="new-glossary-cat">
                            <span className="cat-label">{cat}</span>
                            {Object.entries(entries).map(([en, th]) => (
                              <span key={en} className="glossary-pill">{en} → {th}</span>
                            ))}
                          </div>
                        )
                      )}
                    </div>
                  )}
                </div>
              )}

              <div className="panel file-manager-panel">
                <div className="panel-header">
                  <h2>Output File Manager</h2>
                  <button className="btn-ghost" onClick={fetchOutputFiles}>รีเฟรช</button>
                </div>

                {outputLoading ? (
                  <div className="empty">กำลังโหลดไฟล์...</div>
                ) : outputFiles.length === 0 ? (
                  <div className="empty">ยังไม่มีไฟล์ output</div>
                ) : (
                  <div className="file-manager">
                    <div className="folder-tree">
                      {outputFiles.map(folder => (
                        <div key={folder.name} className="file-folder">
                          <div className="folder-title">📁 {folder.name}</div>
                          {folder.files.length === 0 ? (
                            <div className="file-empty">ว่าง</div>
                          ) : folder.files.map(file => (
                            <button
                              key={file.relative_path}
                              className={`file-row ${selectedOutput?.relative_path === file.relative_path ? "active" : ""}`}
                              onClick={() => openOutputFile(file)}
                            >
                              <span>📄</span>
                              <span className="file-name">{file.name}</span>
                            </button>
                          ))}
                        </div>
                      ))}
                    </div>

                    <div className="file-preview">
                      {selectedOutput ? (
                        <>
                          <div className="file-preview-title">{selectedOutput.relative_path}</div>
                          <pre>{selectedOutput.text}</pre>
                        </>
                      ) : (
                        <div className="empty">เลือกไฟล์เพื่อดูผลลัพธ์</div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ─── GLOSSARY TAB ─── */}
        {tab === "glossary" && (
          <div className="glossary-layout">
            <div className="panel-header sticky-header">
              <h2>Glossary Database</h2>
              <div className="header-actions">
                <button className="btn-ghost" onClick={fetchGlossary}>🔄 รีเฟรช</button>
                <button className="btn-ghost danger" onClick={clearGlossary}>🗑️ ล้างทั้งหมด</button>
              </div>
            </div>

            {/* Add entry */}
            <div className="panel add-entry-panel">
              <h3>➕ เพิ่ม Entry ใหม่</h3>
              <div className="add-row">
                <select value={newEntry.type} onChange={e => setNewEntry(n => ({ ...n, type: e.target.value }))}>
                  <option value="characters">ตัวละคร</option>
                  <option value="places">สถานที่</option>
                  <option value="terms">คำศัพท์</option>
                </select>
                <input placeholder="EN" value={newEntry.en} onChange={e => setNewEntry(n => ({ ...n, en: e.target.value }))} />
                <span className="arrow-label">→</span>
                <input placeholder="TH" value={newEntry.th} onChange={e => setNewEntry(n => ({ ...n, th: e.target.value }))} />
                <button className="btn-primary sm" onClick={addGlossaryEntry}>เพิ่ม</button>
              </div>
            </div>

            <div className="glossary-tables">
              {[
                { key: "characters", label: "👤 ตัวละคร", color: "purple" },
                { key: "places", label: "📍 สถานที่", color: "teal" },
                { key: "terms", label: "💬 คำศัพท์", color: "amber" },
              ].map(({ key, label, color }) => (
                <div key={key} className={`panel glossary-cat cat-${color}`}>
                  <div className="cat-header">
                    <span className="cat-title">{label}</span>
                    <span className="cat-count">{Object.keys(glossary[key] || {}).length} entries</span>
                  </div>
                  {glossaryLoading ? (
                    <div className="loading">กำลังโหลด...</div>
                  ) : Object.keys(glossary[key] || {}).length === 0 ? (
                    <div className="empty">ยังไม่มีข้อมูล</div>
                  ) : (
                    <table className="glossary-table">
                      <thead><tr><th>EN</th><th>TH</th><th></th></tr></thead>
                      <tbody>
                        {Object.entries(glossary[key] || {}).map(([en, th]) => (
                          <tr key={en}>
                            <td className="en-cell">{en}</td>
                            <td className="th-cell">{th}</td>
                            <td>
                              <button className="btn-delete" onClick={() => deleteGlossaryEntry(key, en)}>✕</button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              ))}

              {/* Chapter Summaries */}
              <div className="panel glossary-cat cat-gray" style={{ gridColumn: "1 / -1" }}>
                <div className="cat-header">
                  <span className="cat-title">📖 สรุปตอน</span>
                  <span className="cat-count">{(glossary.chapter_summaries || []).length} ตอน</span>
                </div>
                {(glossary.chapter_summaries || []).length === 0 ? (
                  <div className="empty">ยังไม่มีสรุป</div>
                ) : (
                  <div className="summaries-list">
                    {glossary.chapter_summaries.map((s, i) => (
                      <div key={i} className="summary-row">
                        <span className="summary-text">{s}</span>
                        <button className="btn-delete" onClick={async () => {
                          await fetch(`${API_BASE}/api/glossary/summary/${i}`, { method: "DELETE" });
                          fetchGlossary();
                        }}>✕</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ─── KEYS TAB ─── */}
        {tab === "keys" && (
          <div className="keys-tab">
            <div className="panel keys-panel">
              <div className="panel-header">
                <h2>🔑 API Key Manager</h2>
                <button className="btn-ghost" onClick={refreshKeyStatus}>🔄 ตรวจสอบสถานะ</button>
              </div>

              <div className="keys-list">
                {apiKeys.map((k, i) => (
                  <div key={i} className="key-manage-row">
                    <span className="key-label">Key {i + 1}</span>
                    <input
                      type="password"
                      className="key-input"
                      placeholder="AIza..."
                      value={k}
                      onChange={e => setApiKeys(keys => keys.map((v, j) => j === i ? e.target.value : v))}
                    />
                    {apiKeys.length > 1 && (
                      <button className="btn-ghost-sm danger" onClick={() => setApiKeys(keys => keys.filter((_, j) => j !== i))}>ลบ</button>
                    )}
                  </div>
                ))}
                <button className="btn-ghost" onClick={() => setApiKeys(k => [...k, ""])}>+ เพิ่ม Key</button>
              </div>

              {keyStatus.length > 0 && (
                <div className="key-status-grid">
                  {keyStatus.map(k => (
                    <div key={k.label} className={`key-status-card status-${k.status || "ready"}`}>
                      <div className="ks-header">
                        <span className="ks-label">{k.label}</span>
                        <span className={`ks-badge ${k.cooldown_left > 0 ? "badge-cooldown" : "badge-ready"}`}>
                          {k.cooldown_left > 0 ? `⏳ ${k.cooldown_left}s` : "✅ พร้อม"}
                        </span>
                      </div>
                      <div className="ks-stats">
                        <div className="ks-stat">
                          <span>วันนี้ใช้</span>
                          <strong>{k.requests_today}/{k.daily_limit}</strong>
                        </div>
                        <div className="ks-stat">
                          <span>Errors</span>
                          <strong className={k.failed_count > 0 ? "error-text" : ""}>{k.failed_count}</strong>
                        </div>
                      </div>
                      <div className="ks-progress">
                        <div className="ks-bar" style={{ width: `${Math.min(100, (k.requests_today / k.daily_limit) * 100)}%` }} />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function loadSavedState() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function toPersistableBatchFiles(files) {
  return (files || []).map(({ name, chapterNum, size, status }) => ({
    name,
    chapterNum,
    size,
    status: status || "queued",
  }));
}

function openSourceDb() {
  return new Promise((resolve, reject) => {
    if (!("indexedDB" in window)) {
      reject(new Error("เบราว์เซอร์นี้ไม่รองรับ IndexedDB"));
      return;
    }

    const request = indexedDB.open(SOURCE_DB_NAME, SOURCE_DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(SOURCE_STORE)) {
        db.createObjectStore(SOURCE_STORE, { keyPath: "id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("เปิดฐานข้อมูลไฟล์ไม่ได้"));
  });
}

async function loadBatchFilesFromDb() {
  const db = await openSourceDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(SOURCE_STORE, "readonly");
    const store = tx.objectStore(SOURCE_STORE);
    const request = store.get("current");
    request.onsuccess = () => resolve(request.result?.files || []);
    request.onerror = () => reject(request.error || new Error("อ่านรายการไฟล์ไม่ได้"));
    tx.oncomplete = () => db.close();
    tx.onerror = () => {
      db.close();
      reject(tx.error || new Error("อ่านรายการไฟล์ไม่ได้"));
    };
  });
}

async function saveBatchFilesToDb(files) {
  const db = await openSourceDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(SOURCE_STORE, "readwrite");
    tx.objectStore(SOURCE_STORE).put({ id: "current", files, savedAt: Date.now() });
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
    tx.onerror = () => {
      db.close();
      reject(tx.error || new Error("บันทึกรายการไฟล์ไม่ได้"));
    };
  });
}

async function clearBatchFilesFromDb() {
  const db = await openSourceDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(SOURCE_STORE, "readwrite");
    tx.objectStore(SOURCE_STORE).delete("current");
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
    tx.onerror = () => {
      db.close();
      reject(tx.error || new Error("ล้างรายการไฟล์ไม่ได้"));
    };
  });
}
