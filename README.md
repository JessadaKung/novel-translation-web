# NovelFlow — AI Novel Translation Dashboard

Pipeline แปลนิยายแบบ 6 Agents พร้อม Dashboard จัดการครบวงจร

## โครงสร้างไฟล์

```
novel-translation-web/
├── backend/
│   ├── main.py               ← FastAPI server
│   ├── novel_translation_crew.py
│   ├── glossary_db.py
│   ├── llm_manager.py
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx           ← React dashboard
│   │   ├── index.css         ← Styles
│   │   └── main.jsx
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── nginx.conf                ← Nginx reverse proxy
├── novelflow.service         ← Systemd service
├── deploy.sh                 ← 1-click deploy script
└── README.md
```

## Features

| Feature | Details |
|---|---|
| 🌏 แปลนิยาย | อัปโหลดไฟล์ .txt หรือวางข้อความ เลือกตอน แล้วกด Run |
| 📡 Realtime Progress | ดู agent แต่ละตัว (1-6) ทำงาน live ผ่าน SSE |
| 📚 Glossary Manager | เพิ่ม/ลบ ตัวละคร/สถานที่/คำศัพท์ บน UI ได้เลย |
| 🔑 Key Status | ตรวจ cooldown/quota ของทุก API Key แบบ realtime |
| 📝 Chapter Summaries | ดูและจัดการ context summaries ของทุกตอน |

## Deploy บน DigitalOcean Droplet

### Requirements
- Ubuntu 22.04 LTS
- 2 GB RAM ขึ้นไป (แนะนำ 4 GB)
- Python 3.11, Node.js 18+

### ขั้นตอน

```bash
# 1. อัปโหลดไฟล์ทั้งโฟลเดอร์ขึ้น Droplet
scp -r novel-translation-web/ root@YOUR_IP:~/

# 2. SSH เข้าไป
ssh root@YOUR_IP
cd novel-translation-web

# 3. รัน deploy script
sudo bash deploy.sh
```

เว็บจะพร้อมใช้ที่ `http://YOUR_DROPLET_IP`

### HTTPS (optional, แนะนำ)
```bash
apt install certbot python3-certbot-nginx
certbot --nginx -d yourdomain.com
```

## Local Development

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend (terminal ใหม่)
cd frontend
npm install
npm run dev
```

Frontend: http://localhost:5173
Backend API: http://localhost:8000/docs

## Glossary DB

ไฟล์ `glossary_db.json` เก็บที่ working directory ของ backend
(สำหรับ production = `/var/www/novel-translation/backend/glossary_db.json`)

## Commands ที่ใช้บ่อย

```bash
# ดู logs แบบ live
journalctl -u novelflow -f

# Restart backend
systemctl restart novelflow

# ดูสถานะ
systemctl status novelflow

# Reload nginx
systemctl reload nginx
```
