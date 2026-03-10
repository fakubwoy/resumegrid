# ResumeGrid — Bulk Resume Extractor + WhatsApp Outreach

Upload multiple PDF/DOC/DOCX resumes → get a clean Excel file.  
Automatically message candidates on WhatsApp about missing resume fields.

Powered by **Groq** (`llama-3.3-70b-versatile`) and/or **Gemini** (`gemini-2.5-flash`).  
Deployed as a **single Docker container on Railway** — Flask + WhatsApp service run together.

---

## Project Structure

```
resumegrid/
│
├── static/
│   └── index.html              ← Full frontend (vanilla JS, no build step)
│
├── whatsapp-service/
│   ├── server.js               ← Node.js WhatsApp microservice (whatsapp-web.js)
│   └── package.json            ← Node dependencies
│
├── app.py                      ← Flask API + /wa/* proxy routes
├── gunicorn.conf.py            ← Gunicorn config (reads $PORT from Railway)
├── requirements.txt            ← Python dependencies
│
├── Dockerfile                  ← Single container: Python + Node + Chromium
├── start.sh                    ← Boots Node WA service (bg) + Gunicorn (fg)
├── railway.json                ← Railway build/deploy settings
├── .gitignore
└── README.md
```

### How the two services communicate

```
Browser
  │
  │  HTTP (Railway public URL)
  ▼
Flask / Gunicorn  (port $PORT — Railway-assigned)
  │
  │  HTTP localhost:3001  (internal, never exposed publicly)
  ▼
WhatsApp Node Service  (port 3001)
  │
  │  whatsapp-web.js / Puppeteer / Chromium
  ▼
WhatsApp Web
```

Flask exposes `/wa/*` routes that proxy straight to the internal Node service.  
The Node service is **never reachable from the public internet** — only from Flask.

---

## Railway Deployment (step-by-step)

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "initial"
gh repo create resumegrid --public --push   # or push to an existing repo
```

### 2. Create Railway project

1. Go to [railway.app](https://railway.app) → **New Project**
2. Choose **Deploy from GitHub repo** → select your repo
3. Railway auto-detects `railway.json` and uses the `Dockerfile`

### 3. Set environment variables

In Railway → your service → **Variables**, add:

| Variable        | Required | Description |
|-----------------|----------|-------------|
| `GROQ_API_KEY`  | ✅ at least one | [console.groq.com/keys](https://console.groq.com/keys) |
| `GEMINI_API_KEY`| ✅ at least one | Google AI Studio |
| `WA_PORT`       | optional | Default `3001` — leave unless there's a conflict |
| `WA_SERVICE_URL`| optional | Default `http://localhost:3001` — do not change |
| `LOG_LEVEL`     | optional | `INFO` or `DEBUG` |

> Railway automatically sets `PORT` — you don't add it yourself.

### 4. Deploy

Railway builds the Docker image and deploys automatically on every push to main.  
Your app will be live at `https://<your-project>.up.railway.app`.

---

## Local Development

### Prerequisites

- Python 3.10+
- Node.js 18+
- Chromium or Chrome installed
- A Groq and/or Gemini API key

### Setup

```bash
# 1. Clone
git clone <your-repo-url>
cd resumegrid

# 2. Python env
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Node env
cd whatsapp-service
npm install
cd ..

# 4. Environment
export GROQ_API_KEY="your-key"
export GEMINI_API_KEY="your-key"          # optional
export WA_SERVICE_URL="http://localhost:3001"
export PUPPETEER_EXECUTABLE_PATH="/usr/bin/chromium"  # or your Chrome path

# 5. Start WhatsApp service (terminal 1)
node whatsapp-service/server.js

# 6. Start Flask (terminal 2)
python app.py
```

Open [http://localhost:5000](http://localhost:5000)

---

## Features

### Resume Extraction (28 fields)

| Category       | Fields |
|----------------|--------|
| Contact        | Name, Email, Phone, Location, LinkedIn, GitHub, Portfolio |
| Career         | Current Title, Years of Experience, Summary |
| Skills         | Skills, Programming Languages, Frameworks |
| Education      | Degree, Institution, Graduation Year, GPA |
| Work History   | Companies, Most Recent Company/Role/Duration, Total Companies |
| Recruiter      | **Last CTC**, **Current Status** (Employed / Not Employed / Fresher) |
| Other          | Certifications, Languages Spoken, Projects, Achievements |

### WhatsApp Outreach

After extracting resumes:

1. Click **WhatsApp Outreach** in the results panel
2. Click **Connect WhatsApp** → scan QR code with your phone
3. The panel shows only candidates who have a phone number but are **missing important fields**
4. Filter by missing field (e.g. show only candidates missing "Last CTC")
5. Customize the message template — `{name}` and `{missing_fields}` are replaced automatically
6. Select candidates → **Send to Selected**

Messages are sent with a 1.5–3 s random delay between each to avoid WhatsApp rate limits.

### Other Features

- **Dual AI providers** — Groq + Gemini round-robin with automatic fallback on rate limits
- **OCR fallback** — image-based PDFs processed via Tesseract
- **Duplicate detection** — flags resumes with matching email or phone
- **JD match scoring** — paste a job description to score all candidates 0–100
- **Live preview table** — see extracted data before downloading

---

## Environment Variables Reference

| Variable              | Default                      | Description |
|-----------------------|------------------------------|-------------|
| `GROQ_API_KEY`        | —                            | Groq API key |
| `GEMINI_API_KEY`      | —                            | Google Gemini API key |
| `PORT`                | `5000`                       | Set by Railway automatically |
| `WA_PORT`             | `3001`                       | Internal WhatsApp service port |
| `WA_SERVICE_URL`      | `http://localhost:3001`      | Flask → WA service URL |
| `PUPPETEER_EXECUTABLE_PATH` | `/usr/bin/chromium`  | Chromium binary path |
| `LOG_LEVEL`           | `INFO`                       | Python log level |

---

## Troubleshooting

**WhatsApp panel shows "Disconnected" after deploy**  
→ Expected. Click **Connect WhatsApp** and scan the QR. The session persists in `/app/.wwebjs_auth` as long as the container isn't restarted. On Railway, the container restarts on redeploy — you'll need to scan again.

**QR never appears**  
→ Chromium may have failed to start. Check Railway logs for `[WA Service]` lines. Ensure `PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium` is set (it's set in the Dockerfile by default).

**"No AI provider configured"**  
→ Set at least one of `GROQ_API_KEY` or `GEMINI_API_KEY` in Railway Variables.

**Build fails on Railway**  
→ Chromium + Node adds ~500 MB to the image. Railway's Hobby plan handles this fine. Free plan may time out on first build — retry once.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Vanilla JS (zero build step) |
| Backend | Flask 3 + Gunicorn + gevent |
| AI | Groq `llama-3.3-70b-versatile` + Google `gemini-2.5-flash` |
| WhatsApp | whatsapp-web.js + Puppeteer + Chromium |
| PDF text | pdfplumber + pypdf |
| OCR | Tesseract + pdf2image |
| Excel | openpyxl |
| Deployment | Railway (single Docker container) |

---

**Version 3.0** — Railway-native single-container with WhatsApp outreach