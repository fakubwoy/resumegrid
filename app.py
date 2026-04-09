from flask import Flask, request, jsonify, send_file, Response, stream_with_context
from flask_cors import CORS
import os
import json
import re
import tempfile
import time
import logging
import base64
import pdfplumber
import pypdf
import docx2txt
import httpx
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# OCR imports — optional, gracefully degraded if unavailable
try:
    from pdf2image import convert_from_path as pdf_to_images
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

# ── Logging setup ──────────────────────────────────────────────────────────────

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("resumegrid")

logging.getLogger("pdfplumber").setLevel(logging.WARNING)
logging.getLogger("pdfminer").setLevel(logging.WARNING)
logging.getLogger("pypdf").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger.info("ResumeGrid (Ollama-local) starting up (log level: %s)", LOG_LEVEL)
if OCR_AVAILABLE:
    logger.info("OCR support enabled (pdf2image + pytesseract)")
else:
    logger.warning("OCR support NOT available — install pdf2image + pytesseract to enable.")

# ── Ollama configuration ────────────────────────────────────────────────────────
# 
# Recommended model for your hardware (Nvidia Quadro GPU, Xeon CPU, 32 GB RAM):
#   mistral-nemo:12b  — Best balance of speed + accuracy for structured extraction.
#                        Fits easily in VRAM, fast inference, strong JSON compliance.
#   Pull with: ollama pull mistral-nemo
#
# Fallback (if mistral-nemo unavailable):
#   llama3.1:8b       — Great accuracy, very fast on GPU
#   Pull with: ollama pull llama3.1
#
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL", "mistral-nemo")

# Concurrency: how many resumes to process in parallel.
# With a Quadro GPU, Ollama handles one request at a time anyway (GPU-locked),
# but we can queue up text-extraction concurrently and send AI calls sequentially.
# The AI calls are serialised via a semaphore to avoid overloading Ollama.
MAX_CONCURRENT_EXTRACT = int(os.environ.get("MAX_CONCURRENT_EXTRACT", "4"))  # text extraction threads
OLLAMA_SEMAPHORE = threading.Semaphore(1)   # one AI call at a time (GPU constraint)

logger.info("Ollama endpoint: %s  model: %s", OLLAMA_BASE_URL, OLLAMA_MODEL)
logger.info("Concurrent text-extraction workers: %d", MAX_CONCURRENT_EXTRACT)


def check_ollama():
    """Verify Ollama is running and the chosen model is available."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            logger.info("Ollama available. Models pulled: %s", models)
            # Check if our model (or a variant) is present
            model_base = OLLAMA_MODEL.split(":")[0]
            if any(model_base in m for m in models):
                logger.info("Model '%s' found ✓", OLLAMA_MODEL)
            else:
                logger.warning(
                    "Model '%s' NOT found in Ollama. "
                    "Run: ollama pull %s", OLLAMA_MODEL, OLLAMA_MODEL
                )
    except Exception as e:
        logger.warning("Could not reach Ollama at %s: %s", OLLAMA_BASE_URL, e)


check_ollama()

# ──────────────────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder='static')
CORS(app)

UPLOAD_FOLDER = tempfile.mkdtemp()

CANONICAL_FIELDS = {
    "full_name": ["name", "full name", "candidate name", "applicant name", "your name"],
    "email": ["email", "email address", "e-mail", "mail", "contact email"],
    "phone": ["phone", "phone number", "mobile", "cell", "telephone", "contact number", "tel"],
    "location": ["location", "address", "city", "city/state", "current location", "residence", "based in"],
    "linkedin": ["linkedin", "linkedin url", "linkedin profile", "linkedin.com"],
    "github": ["github", "github url", "github profile", "github.com"],
    "portfolio": ["portfolio", "website", "personal website", "portfolio url"],
    "current_title": ["current title", "job title", "position", "current position", "role", "current role"],
    "years_of_experience": ["years of experience", "experience", "total experience", "work experience years"],
    "summary": ["summary", "objective", "profile", "about", "professional summary", "career objective", "about me"],
    "skills": ["skills", "technical skills", "core skills", "key skills", "competencies", "technologies", "tech stack", "tools"],
    "programming_languages": ["programming languages", "languages", "coding languages", "development languages"],
    "frameworks": ["frameworks", "libraries", "frameworks & libraries", "tools & frameworks"],
    "education_degree": ["degree", "education", "highest degree", "qualification", "academic background"],
    "education_institution": ["university", "college", "institution", "school", "alma mater"],
    "education_year": ["graduation year", "year of graduation", "year of passing", "completion year"],
    "education_gpa": ["gpa", "cgpa", "grade", "percentage", "marks"],
    "companies_worked": ["companies", "employers", "work history", "previous companies", "organisations"],
    "most_recent_company": ["most recent company", "current company", "last employer", "current employer"],
    "most_recent_role": ["most recent role", "current role", "latest position", "last position"],
    "most_recent_duration": ["most recent duration", "current duration", "last job duration"],
    "certifications": ["certifications", "certificates", "professional certifications", "credentials", "licenses"],
    "languages_spoken": ["languages spoken", "languages known", "spoken languages", "language proficiency"],
    "projects": ["projects", "key projects", "notable projects", "personal projects", "academic projects"],
    "achievements": ["achievements", "accomplishments", "awards", "honors", "recognition"],
    "total_companies": ["total companies", "number of companies", "companies count"],
    "last_ctc": ["last ctc", "current ctc", "last salary", "current salary", "compensation", "ctc"],
    "current_status": ["current status", "employment status", "work status", "availability"]
}

COLUMN_ORDER = [
    "match_score", "match_reason",
    "full_name", "email", "phone", "location", "linkedin", "github", "portfolio",
    "current_title", "years_of_experience", "summary",
    "skills", "programming_languages", "frameworks",
    "education_degree", "education_institution", "education_year", "education_gpa",
    "companies_worked", "most_recent_company", "most_recent_role", "most_recent_duration", "total_companies",
    "certifications", "languages_spoken", "projects", "achievements",
    "last_ctc", "current_status", "duplicate_of"
]

COLUMN_HEADERS = {
    "full_name": "Full Name", "email": "Email", "phone": "Phone",
    "location": "Location", "linkedin": "LinkedIn", "github": "GitHub",
    "portfolio": "Portfolio/Website", "current_title": "Current Title",
    "years_of_experience": "Years of Experience", "summary": "Summary/Objective",
    "skills": "Skills", "programming_languages": "Programming Languages",
    "frameworks": "Frameworks/Libraries", "education_degree": "Degree",
    "education_institution": "Institution", "education_year": "Graduation Year",
    "education_gpa": "GPA/Grade", "companies_worked": "Companies Worked At",
    "most_recent_company": "Most Recent Company", "most_recent_role": "Most Recent Role",
    "most_recent_duration": "Most Recent Duration", "total_companies": "Total Companies",
    "certifications": "Certifications", "languages_spoken": "Languages Spoken",
    "projects": "Key Projects", "achievements": "Achievements/Awards",
    "source_file": "Source File", "last_ctc": "Last CTC", "current_status": "Current Status",
    "match_score": "Match Score (/100)", "match_reason": "Match Reason",
    "duplicate_of": "Duplicate Of"
}


# ── Text extraction helpers ───────────────────────────────────────────────────

def extract_hyperlinks_from_pdf(tmp_path):
    urls = []
    try:
        reader = pypdf.PdfReader(tmp_path)
        for page in reader.pages:
            annots = page.get("/Annots")
            if not annots:
                continue
            for annot in annots:
                obj = annot.get_object()
                a = obj.get("/A")
                if not a:
                    continue
                a_obj = a.get_object() if hasattr(a, 'get_object') else a
                uri = a_obj.get("/URI")
                if uri:
                    url = str(uri)
                    if url.startswith("http") and url not in urls:
                        urls.append(url)
    except Exception:
        pass
    return urls


def is_image_based_pdf(text, page_count):
    stripped = text.strip() if text else ""
    if len(stripped) < page_count * 300:
        return True
    import re as _re
    cid_hits = len(_re.findall(r'\(cid:\d+\)', stripped))
    word_count = len(stripped.split())
    if word_count > 0 and cid_hits / word_count > 0.10:
        return True
    return False


def ocr_pdf(tmp_path, filename):
    if not OCR_AVAILABLE:
        raise RuntimeError(
            "OCR is not available. Install pdf2image and pytesseract to process image-based PDFs."
        )
    logger.info("'%s' — running OCR (image-based PDF detected)", filename)
    t0 = time.monotonic()
    pages = pdf_to_images(tmp_path, dpi=200)
    parts = []
    for i, page_img in enumerate(pages):
        page_text = pytesseract.image_to_string(page_img, lang="eng")
        if page_text.strip():
            parts.append(page_text)
    text = "\n".join(parts)
    elapsed = time.monotonic() - t0
    logger.info("'%s' — OCR complete in %.2fs: %d pages → %d chars", filename, elapsed, len(pages), len(text))
    return text


def classify_urls(urls):
    result = {}
    for url in urls:
        url_lower = url.lower()
        if "linkedin.com" in url_lower and "linkedin" not in result:
            result["linkedin"] = url
        elif "github.com" in url_lower and "github" not in result:
            result["github"] = url
        elif "mailto:" in url_lower and "email" not in result:
            result["email"] = url.replace("mailto:", "").strip()
        elif "portfolio" not in result and "linkedin.com" not in url_lower and "github.com" not in url_lower:
            result["portfolio"] = url
    return result


def extract_text_from_file(file_content, filename):
    ext = Path(filename).suffix.lower()
    logger.debug("Extracting text from '%s' (type: %s, size: %d bytes)", filename, ext, len(file_content))

    if ext == ".pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name
        try:
            parts = []
            with pdfplumber.open(tmp_path) as pdf:
                num_pages = len(pdf.pages)
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        parts.append(t)
            text = "\n".join(parts)
            urls = extract_hyperlinks_from_pdf(tmp_path)
            url_overrides = classify_urls(urls)

            if is_image_based_pdf(text, num_pages):
                try:
                    ocr_text = ocr_pdf(tmp_path, filename)
                    if len(ocr_text.strip()) > len(text.strip()):
                        text = ocr_text
                except Exception as ocr_err:
                    logger.error("'%s' — OCR failed: %s", filename, ocr_err)

            return text, url_overrides
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    elif ext in [".doc", ".docx"]:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name
        try:
            text = docx2txt.process(tmp_path)
            url_overrides = extract_hyperlinks_from_docx(tmp_path)
            return text, url_overrides
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    logger.warning("'%s' — falling back to plain text decode", filename)
    return file_content.decode("utf-8", errors="ignore"), {}


def extract_hyperlinks_from_docx(tmp_path):
    urls = []
    try:
        from docx import Document
        doc = Document(tmp_path)
        for rel in doc.part.rels.values():
            if "hyperlink" in rel.reltype.lower():
                url = rel.target_ref
                if url.startswith("http") and url not in urls:
                    urls.append(url)
    except Exception:
        pass
    return classify_urls(urls)


# ── AI prompt ─────────────────────────────────────────────────────────────────

def get_extraction_prompt():
    return """Extract ALL information from this resume and return it as a JSON object.

Include these fields (use null if not found):
- full_name, email, phone, location, linkedin, github, portfolio
- current_title, years_of_experience, summary
- skills, programming_languages, frameworks
- education_degree, education_institution, education_year, education_gpa
- companies_worked (comma-separated list), most_recent_company, most_recent_role, most_recent_duration, total_companies
- certifications, languages_spoken, projects, achievements
- last_ctc (last/current CTC or salary — if not explicitly mentioned, leave as null)
- current_status ("Employed" if currently working, "Not Employed" if between jobs, "Fresher" if no work experience)

For current_status:
- If mentions "current" role or dates like "2023-Present" -> "Employed"
- If most recent job ended in past or mentions "seeking" -> "Not Employed"
- If no work experience or only internships/projects -> "Fresher"

IMPORTANT: Return ONLY a valid JSON object. No markdown, no backticks, no explanation, no preamble."""


def get_scoring_prompt(job_description):
    return f"""You are a technical recruiter. Score this resume against the job description below.

JOB DESCRIPTION:
{job_description[:2000]}

Score the candidate from 0 to 100 based on:
- Skills & technology match (40 points)
- Years of experience relevance (20 points)
- Role/title alignment (20 points)
- Education & certifications (10 points)
- Overall fit (10 points)

Return ONLY a JSON object with exactly these two fields:
{{
  "match_score": <integer 0-100>,
  "match_reason": "<one sentence summary of fit>"
}}

No markdown, no explanation, just the JSON."""


# ── Ollama call ────────────────────────────────────────────────────────────────

def call_ollama(prompt_text, system_prompt=None, max_retries=3):
    """
    Call the local Ollama API. Uses the semaphore to serialise GPU calls.
    Retries on connection errors (e.g. Ollama temporarily busy).
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt_text})

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.0,       # deterministic for extraction
            "num_predict": 2048,
            "top_p": 1.0,
        }
    }

    last_error = None
    for attempt in range(max_retries):
        try:
            with OLLAMA_SEMAPHORE:  # one GPU call at a time
                t0 = time.monotonic()
                resp = httpx.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json=payload,
                    timeout=120.0
                )
                resp.raise_for_status()
                elapsed = time.monotonic() - t0
                result = resp.json()
                text = result["message"]["content"].strip()
                logger.debug("Ollama response in %.2fs (%d chars)", elapsed, len(text))
                return text
        except Exception as e:
            last_error = e
            logger.warning("Ollama attempt %d/%d failed: %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s backoff
    raise RuntimeError(f"Ollama call failed after {max_retries} attempts: {last_error}")


def call_ai(text):
    """Call Ollama for resume extraction. Returns (raw_response, 'ollama')."""
    system = (
        "You are an expert resume parser. "
        "Extract structured data and return ONLY valid JSON. "
        "No markdown, no backticks, no explanation whatsoever."
    )
    prompt = f"Resume text:\n\n{text}\n\n{get_extraction_prompt()}"
    raw = call_ollama(prompt, system_prompt=system)
    return raw, "ollama"


def score_resume_against_jd(resume_data, job_description):
    """Score a single extracted resume dict against a JD. Returns (score_int, reason_str)."""
    summary_parts = []
    for field in ["full_name", "current_title", "years_of_experience", "skills",
                  "programming_languages", "frameworks", "education_degree",
                  "certifications", "summary", "companies_worked"]:
        val = resume_data.get(field)
        if val:
            summary_parts.append(f"{field}: {val}")
    resume_summary = "\n".join(summary_parts)

    prompt_text = f"RESUME DATA:\n{resume_summary}\n\n{get_scoring_prompt(job_description)}"
    system = "You are a technical recruiter. Return ONLY valid JSON, no markdown, no explanation."

    try:
        raw = call_ollama(prompt_text, system_prompt=system)
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            score = int(parsed.get("match_score", 0))
            score = max(0, min(100, score))
            reason = str(parsed.get("match_reason", ""))
            return score, reason
    except Exception as e:
        logger.warning("Scoring failed: %s", e)
    return None, None


# ── Core extraction ───────────────────────────────────────────────────────────

def extract_resume_data(file_content, filename):
    t0 = time.monotonic()
    logger.info("Processing '%s' (%d bytes)", filename, len(file_content))

    text, url_overrides = extract_text_from_file(file_content, filename)

    if not text or len(text.strip()) < 50:
        raise ValueError("Could not extract readable text from file")

    original_len = len(text)
    # Increase context window slightly for local model — no token cost
    text = text[:8000]
    if original_len > 8000:
        logger.debug("'%s' — text truncated from %d to 8000 chars", filename, original_len)

    raw, provider_used = call_ai(text)

    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    match = re.search(r'\{.*\}', raw, re.DOTALL)
    data = {}
    if match:
        try:
            data = normalize_fields(json.loads(match.group()))
        except json.JSONDecodeError as e:
            logger.warning("'%s' — JSON parse failed (%s), raw snippet: %s", filename, e, raw[:200])
    else:
        logger.warning("'%s' — no JSON object found in response, raw snippet: %s", filename, raw[:200])

    for field, url in url_overrides.items():
        existing = data.get(field, "")
        if not existing or not existing.startswith("http"):
            data[field] = url

    fields_found = [k for k in data if data[k]]
    elapsed = time.monotonic() - t0
    logger.info(
        "Finished '%s' in %.2fs via %s — %d fields extracted",
        filename, elapsed, provider_used, len(fields_found)
    )
    return data


def normalize_fields(data):
    normalized = {}
    for key, value in data.items():
        if value is None or value == "" or value == "null":
            continue
        key_lower = key.lower().strip()
        if key_lower in CANONICAL_FIELDS:
            normalized[key_lower] = str(value)
            continue
        matched = False
        for canonical, aliases in CANONICAL_FIELDS.items():
            if key_lower in aliases or any(alias in key_lower for alias in aliases):
                normalized[canonical] = str(value)
                matched = True
                break
        if not matched:
            normalized[key_lower] = str(value)
    return normalized


# ── Excel generation ──────────────────────────────────────────────────────────

def create_excel(all_data):
    wb = Workbook()
    ws = wb.active
    ws.title = "Candidates"

    all_keys = set()
    for d in all_data:
        all_keys.update(d.keys())

    final_columns = [col for col in COLUMN_ORDER if col in all_keys or col in COLUMN_HEADERS]
    for key in sorted(all_keys):
        if key not in final_columns and key != "filename":
            final_columns.append(key)
    final_columns.append("source_file")

    header_font  = Font(name="Arial", bold=True, color="FFFFFF", size=10)
    header_fill  = PatternFill("solid", start_color="1A3A5C")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell_font    = Font(name="Arial", size=9)
    cell_align   = Alignment(vertical="top", wrap_text=True)
    alt_fill     = PatternFill("solid", start_color="EBF2FA")
    thin_border  = Border(
        left=Side(style='thin', color='CCCCCC'), right=Side(style='thin', color='CCCCCC'),
        top=Side(style='thin', color='CCCCCC'), bottom=Side(style='thin', color='CCCCCC')
    )

    for col_idx, col_key in enumerate(final_columns, 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.value = COLUMN_HEADERS.get(col_key, col_key.replace("_", " ").title())
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border
    ws.row_dimensions[1].height = 30

    for row_idx, candidate in enumerate(all_data, 2):
        for col_idx, col_key in enumerate(final_columns, 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            value = candidate.get("filename", "") if col_key == "source_file" else candidate.get(col_key, "")

            if value and str(value).startswith("http") and col_key in ("linkedin", "github", "portfolio"):
                cell.value = value
                cell.hyperlink = value
                cell.font = Font(name="Arial", size=9, color="0563C1", underline="single")
            elif col_key == "match_score" and value:
                try:
                    score_val = int(value)
                    cell.value = score_val
                    if score_val >= 75:
                        cell.fill = PatternFill("solid", start_color="C6EFCE")
                        cell.font = Font(name="Arial", size=9, bold=True, color="276221")
                    elif score_val >= 50:
                        cell.fill = PatternFill("solid", start_color="FFEB9C")
                        cell.font = Font(name="Arial", size=9, bold=True, color="9C6500")
                    else:
                        cell.fill = PatternFill("solid", start_color="FFC7CE")
                        cell.font = Font(name="Arial", size=9, bold=True, color="9C0006")
                except (ValueError, TypeError):
                    cell.value = value
                    cell.font = cell_font
            elif col_key == "duplicate_of" and value:
                cell.value = value
                cell.fill = PatternFill("solid", start_color="FFF2CC")
                cell.font = Font(name="Arial", size=9, italic=True, color="7F6000")
            else:
                cell.value = value
                cell.font = cell_font

            cell.alignment = cell_align
            cell.border = thin_border
            if row_idx % 2 == 0 and not (value and str(value).startswith("http") and col_key in ("linkedin", "github", "portfolio")):
                cell.fill = alt_fill

    width_map = {
        "full_name": 22, "email": 28, "phone": 16, "location": 18,
        "linkedin": 35, "github": 35, "portfolio": 35, "current_title": 22,
        "years_of_experience": 12, "summary": 45, "skills": 40,
        "programming_languages": 30, "frameworks": 30, "education_degree": 20,
        "education_institution": 25, "education_year": 12, "education_gpa": 10,
        "companies_worked": 35, "most_recent_company": 25, "most_recent_role": 22,
        "most_recent_duration": 15, "total_companies": 12, "certifications": 35,
        "languages_spoken": 20, "projects": 45, "achievements": 35, "source_file": 25,
        "last_ctc": 18, "current_status": 18, "match_score": 14,
        "match_reason": 45, "duplicate_of": 25
    }
    for col_idx, col_key in enumerate(final_columns, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width_map.get(col_key, 20)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(final_columns))}1"

    output_path = os.path.join(UPLOAD_FOLDER, "extracted_resumes.xlsx")
    wb.save(output_path)
    with open(output_path, "rb") as f:
        excel_bytes = f.read()
    return output_path, excel_bytes


# ── Flask routes ──────────────────────────────────────────────────────────────

def sse_event(data):
    return f"data: {json.dumps(data)}\n\n"


@app.route("/")
def index():
    return app.send_static_file('index.html')


@app.route("/health")
def health():
    """Health check — also reports Ollama connectivity."""
    ollama_ok = False
    ollama_model_ready = False
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        if resp.status_code == 200:
            ollama_ok = True
            models = [m["name"] for m in resp.json().get("models", [])]
            model_base = OLLAMA_MODEL.split(":")[0]
            ollama_model_ready = any(model_base in m for m in models)
    except Exception:
        pass

    return jsonify({
        "status": "ok",
        "provider": "ollama",
        "ollama_url": OLLAMA_BASE_URL,
        "ollama_model": OLLAMA_MODEL,
        "ollama_connected": ollama_ok,
        "ollama_model_ready": ollama_model_ready,
    })


@app.route("/extract", methods=["POST"])
def extract():
    """
    Streaming SSE endpoint.

    Architecture for concurrency:
    - Text extraction (PDF parsing, OCR) runs in a ThreadPoolExecutor — fully parallel.
    - AI (Ollama) calls are serialised via OLLAMA_SEMAPHORE (one call at a time, GPU-bound).
    - Results are streamed back as SSE as each resume completes, preserving real-time feedback.
    """
    if "files" not in request.files:
        return jsonify({"error": "No files uploaded"}), 400

    files = request.files.getlist("files")
    job_description = request.form.get("job_description", "").strip()
    if job_description:
        logger.info("Job description provided (%d chars) — scoring enabled", len(job_description))

    file_payloads = []
    for file in files:
        if not file.filename:
            continue
        ext = Path(file.filename).suffix.lower()
        if ext not in [".pdf", ".doc", ".docx"]:
            file_payloads.append({"filename": file.filename, "content": None,
                                   "error": "Unsupported file type"})
        else:
            content = file.read()
            file_payloads.append({"filename": file.filename, "content": content, "error": None})

    if not file_payloads:
        return jsonify({"error": "No valid files found"}), 400

    total = len(file_payloads)
    logger.info("POST /extract — starting batch of %d file(s)", total)

    def generate():
        results = []
        errors  = []
        batch_start = time.monotonic()
        seen_emails = {}
        seen_phones = {}

        # Thread-safety lock for shared state (results, seen_emails/phones)
        state_lock = threading.Lock()

        yield sse_event({"type": "start", "total": total})

        # ── Phase 1: extract text from all files in parallel ──────────────────
        text_results = {}  # filename -> (text, url_overrides, error)

        def extract_text_task(payload):
            fn = payload["filename"]
            if payload["error"]:
                return fn, None, None, payload["error"]
            try:
                text, url_overrides = extract_text_from_file(payload["content"], fn)
                if not text or len(text.strip()) < 50:
                    return fn, None, None, "Could not extract readable text from file"
                return fn, text[:8000], url_overrides, None
            except Exception as e:
                return fn, None, None, str(e)

        logger.info("Phase 1: extracting text from %d files (up to %d in parallel)", total, MAX_CONCURRENT_EXTRACT)
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_EXTRACT) as executor:
            futures = {executor.submit(extract_text_task, p): p for p in file_payloads}
            for future in as_completed(futures):
                fn, text, url_overrides, err = future.result()
                text_results[fn] = (text, url_overrides, err)

        # ── Phase 2: AI extraction + scoring (serialised, GPU-locked) ─────────
        logger.info("Phase 2: AI extraction for %d files (serialised via Ollama)", total)

        for i, payload in enumerate(file_payloads):
            filename  = payload["filename"]
            completed = i + 1
            text, url_overrides, text_err = text_results.get(filename, (None, None, "Text extraction missing"))

            if text_err:
                logger.warning("[%d/%d] Skipping '%s': %s", completed, total, filename, text_err)
                errors.append({"file": filename, "error": text_err})
                yield sse_event({
                    "type": "progress", "filename": filename, "ok": False,
                    "error": text_err, "completed": completed, "total": total,
                    "pct": round((completed / total) * 90)
                })
                continue

            try:
                logger.info("[%d/%d] AI extracting '%s'", completed, total, filename)
                t0 = time.monotonic()

                raw, provider_used = call_ai(text)
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)

                match_obj = re.search(r'\{.*\}', raw, re.DOTALL)
                data = {}
                if match_obj:
                    try:
                        data = normalize_fields(json.loads(match_obj.group()))
                    except json.JSONDecodeError as e:
                        logger.warning("'%s' — JSON parse error: %s", filename, e)
                else:
                    logger.warning("'%s' — no JSON in AI response", filename)

                for field, url in (url_overrides or {}).items():
                    existing = data.get(field, "")
                    if not existing or not existing.startswith("http"):
                        data[field] = url

                data["filename"] = filename

                # ── Duplicate detection ──────────────────────────────────────
                dup_of = None
                email_key = (data.get("email") or "").lower().strip()
                phone_key  = re.sub(r'\D', '', data.get("phone") or "")

                with state_lock:
                    if email_key and email_key in seen_emails:
                        dup_of = seen_emails[email_key]
                    elif phone_key and len(phone_key) >= 7 and phone_key in seen_phones:
                        dup_of = seen_phones[phone_key]

                    if dup_of:
                        data["duplicate_of"] = dup_of
                    else:
                        candidate_label = data.get("full_name") or filename
                        if email_key:
                            seen_emails[email_key] = candidate_label
                        if phone_key and len(phone_key) >= 7:
                            seen_phones[phone_key] = candidate_label

                # ── JD match scoring ─────────────────────────────────────────
                if job_description and not dup_of:
                    try:
                        score, reason = score_resume_against_jd(data, job_description)
                        if score is not None:
                            data["match_score"] = str(score)
                            data["match_reason"] = reason or ""
                            logger.info("'%s' — match score: %s/100", filename, score)
                    except Exception as score_err:
                        logger.warning("Scoring skipped for '%s': %s", filename, score_err)

                elapsed = time.monotonic() - t0
                fields_found = [k for k in data if data[k] and k != "filename"]
                logger.info("[%d/%d] '%s' — OK in %.2fs (%d fields)", completed, total, filename, elapsed, len(fields_found))

                with state_lock:
                    results.append(data)

                yield sse_event({
                    "type": "progress", "filename": filename, "ok": True,
                    "error": None, "completed": completed, "total": total,
                    "pct": round((completed / total) * 90),
                    "data": data
                })

            except Exception as e:
                logger.error("[%d/%d] '%s' — FAILED: %s", completed, total, filename, e, exc_info=True)
                errors.append({"file": filename, "error": str(e)})
                yield sse_event({
                    "type": "progress", "filename": filename, "ok": False,
                    "error": str(e), "completed": completed, "total": total,
                    "pct": round((completed / total) * 90)
                })

        batch_elapsed = time.monotonic() - batch_start

        if results:
            try:
                _, excel_bytes = create_excel(results)
                excel_b64 = base64.b64encode(excel_bytes).decode("utf-8")
                logger.info(
                    "Batch complete in %.2fs — %d/%d succeeded, %d error(s)",
                    batch_elapsed, len(results), total, len(errors)
                )
                yield sse_event({
                    "type": "done", "success": True,
                    "processed": len(results), "errors": errors,
                    "download_url": "/download", "pct": 100,
                    "results": results, "excel_b64": excel_b64
                })
            except Exception as e:
                logger.error("Excel generation failed: %s", e, exc_info=True)
                yield sse_event({
                    "type": "done", "success": False,
                    "error": f"Excel generation failed: {str(e)}",
                    "processed": 0, "errors": errors, "pct": 0
                })
        else:
            yield sse_event({
                "type": "done", "success": False,
                "error": "No resumes could be processed",
                "processed": 0, "errors": errors, "pct": 0
            })

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.route("/download")
def download():
    excel_path = os.path.join(UPLOAD_FOLDER, "extracted_resumes.xlsx")
    if not os.path.exists(excel_path):
        return jsonify({"error": "No file to download"}), 404
    return send_file(
        excel_path, as_attachment=True,
        download_name="extracted_resumes.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ── WhatsApp proxy routes ─────────────────────────────────────────────────────

import urllib.request as _urllib_req

WA_SERVICE_URL = os.environ.get("WA_SERVICE_URL", "http://localhost:3001")


def _wa_request(method, path, body=None):
    url = f"{WA_SERVICE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    req = _urllib_req.Request(url, data=data, headers=headers, method=method)
    try:
        with _urllib_req.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read()), resp.status
    except _urllib_req.HTTPError as e:
        return json.loads(e.read()), e.code
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 503


@app.route("/wa/status")
def wa_status():
    data, code = _wa_request("GET", "/status")
    return jsonify(data), code


@app.route("/wa/connect", methods=["POST"])
def wa_connect():
    data, code = _wa_request("POST", "/connect")
    return jsonify(data), code


@app.route("/wa/disconnect", methods=["POST"])
def wa_disconnect():
    data, code = _wa_request("POST", "/disconnect")
    return jsonify(data), code


@app.route("/wa/send", methods=["POST"])
def wa_send():
    body = request.get_json(silent=True) or {}
    data, code = _wa_request("POST", "/send", body)
    return jsonify(data), code


@app.route("/wa/send-bulk", methods=["POST"])
def wa_send_bulk():
    body = request.get_json(silent=True) or {}
    data, code = _wa_request("POST", "/send-bulk", body)
    return jsonify(data), code


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info("Starting Flask dev server on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)