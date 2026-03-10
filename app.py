from flask import Flask, request, jsonify, send_file, Response, stream_with_context
from flask_cors import CORS
import os
import json
import re
import tempfile
import time
import logging
import pdfplumber
import pypdf
import docx2txt
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

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

# Quieten noisy third-party loggers
logging.getLogger("pdfplumber").setLevel(logging.WARNING)
logging.getLogger("pdfminer").setLevel(logging.WARNING)
logging.getLogger("pypdf").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger.info("ResumeGrid starting up (log level: %s)", LOG_LEVEL)
if OCR_AVAILABLE:
    logger.info("OCR support enabled (pdf2image + pytesseract)")
else:
    logger.warning("OCR support NOT available — image-based PDFs will fail silently. "
                   "Install pdf2image and pytesseract to enable OCR.")

# ── AI client setup ────────────────────────────────────────────────────────────
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

groq_client = None
if GROQ_API_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
        logger.info("Groq client initialised (model: %s)", "llama-3.3-70b-versatile")
    except Exception as e:
        logger.error("Failed to initialise Groq client: %s", e)
        groq_client = None
else:
    logger.warning("GROQ_API_KEY not set — Groq provider disabled")

gemini_model = None
if GEMINI_API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel("gemini-2.5-flash")
        logger.info("Gemini client initialised (model: gemini-2.5-flash)")
    except Exception as e:
        logger.error("Failed to initialise Gemini client: %s", e)
        gemini_model = None
else:
    logger.warning("GEMINI_API_KEY not set — Gemini provider disabled")

GROQ_MODEL = "llama-3.3-70b-versatile"

# Round-robin provider state
_provider_index = 0  # 0 = Groq, 1 = Gemini


def _next_provider():
    """Return ('groq'|'gemini') cycling between available providers."""
    global _provider_index
    providers = []
    if groq_client:
        providers.append("groq")
    if gemini_model:
        providers.append("gemini")
    if not providers:
        raise RuntimeError("No AI provider configured. Set GROQ_API_KEY and/or GEMINI_API_KEY.")
    provider = providers[_provider_index % len(providers)]
    _provider_index += 1
    return provider

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
    """
    Detect PDFs where text extraction failed or produced garbage.
    Two signals:
      1. Very low character yield  — fewer than 300 chars per page on average.
      2. High (cid:N) garbling ratio — >10% of word tokens are encoding artifacts,
         which indicates a font that couldn't be decoded (common in scanned/image PDFs).
    Either signal alone is sufficient to trigger OCR.
    """
    stripped = text.strip() if text else ""

    # Signal 1: low yield
    if len(stripped) < page_count * 300:
        return True

    # Signal 2: garbled font encoding
    import re as _re
    cid_hits = len(_re.findall(r'\(cid:\d+\)', stripped))
    word_count = len(stripped.split())
    if word_count > 0 and cid_hits / word_count > 0.10:
        return True

    return False


def ocr_pdf(tmp_path, filename):
    """
    Render each PDF page to an image and run Tesseract OCR.
    Returns the combined OCR text, or raises if OCR is unavailable.
    """
    if not OCR_AVAILABLE:
        raise RuntimeError(
            "OCR is not available. Install pdf2image and pytesseract "
            "to process image-based PDFs."
        )
    logger.info("'%s' — running OCR (image-based PDF detected)", filename)
    t0 = time.monotonic()
    pages = pdf_to_images(tmp_path, dpi=200)
    parts = []
    for i, page_img in enumerate(pages):
        page_text = pytesseract.image_to_string(page_img, lang="eng")
        if page_text.strip():
            parts.append(page_text)
        logger.debug("'%s' — OCR page %d/%d: %d chars", filename, i + 1, len(pages), len(page_text))
    text = "\n".join(parts)
    elapsed = time.monotonic() - t0
    logger.info(
        "'%s' — OCR complete in %.2fs: %d pages → %d chars",
        filename, elapsed, len(pages), len(text)
    )
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
            logger.debug(
                "'%s' — PDF extracted: %d pages, %d chars, %d hyperlinks found",
                filename, num_pages, len(text), len(urls)
            )

            # Fallback to OCR if the PDF appears to be image-based
            if is_image_based_pdf(text, num_pages):
                logger.info(
                    "'%s' — low text yield (%d chars across %d pages), "
                    "treating as image-based PDF and attempting OCR",
                    filename, len(text.strip()), num_pages
                )
                try:
                    ocr_text = ocr_pdf(tmp_path, filename)
                    if len(ocr_text.strip()) > len(text.strip()):
                        logger.info(
                            "'%s' — OCR improved text yield: %d → %d chars",
                            filename, len(text.strip()), len(ocr_text.strip())
                        )
                        text = ocr_text
                    else:
                        logger.warning(
                            "'%s' — OCR did not improve yield (%d chars), keeping original",
                            filename, len(ocr_text.strip())
                        )
                except Exception as ocr_err:
                    logger.error("'%s' — OCR failed: %s", filename, ocr_err)
                    # Continue with whatever pdfplumber managed to extract

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
            logger.debug(
                "'%s' — DOCX extracted: %d chars, %d hyperlinks found",
                filename, len(text) if text else 0, len(url_overrides)
            )
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
- last_ctc (last/current CTC or salary - if not explicitly mentioned, leave as null)
- current_status ("Employed" if currently working, "Not Employed" if between jobs, "Fresher" if no work experience)

For current_status:
- If mentions "current" role or dates like "2023-Present" -> "Employed"
- If most recent job ended in past or mentions "seeking" -> "Not Employed"
- If no work experience or only internships/projects -> "Fresher"

Return ONLY valid JSON with no explanation or markdown."""


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

Return ONLY a JSON object with exactly these fields:
{{
  "match_score": <integer 0-100>,
  "match_reason": "<one sentence summary of why they fit or don't fit>"
}}

No markdown, no explanation, just the JSON."""


def score_resume_against_jd(resume_data, job_description, provider_used_hint=None):
    """Score a single already-extracted resume dict against a JD. Returns (score_int, reason_str)."""
    # Build a compact resume summary from extracted fields for the scoring call
    summary_parts = []
    for field in ["full_name", "current_title", "years_of_experience", "skills",
                  "programming_languages", "frameworks", "education_degree",
                  "certifications", "summary", "companies_worked"]:
        val = resume_data.get(field)
        if val:
            summary_parts.append(f"{field}: {val}")
    resume_summary = "\n".join(summary_parts)

    prompt_text = f"RESUME DATA:\n{resume_summary}\n\n{get_scoring_prompt(job_description)}"

    try:
        raw, _ = call_ai_with_fallback(prompt_text, max_retries=3)
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
        logger.warning("Scoring failed for candidate: %s", e)
    return None, None


# ── Provider call functions ────────────────────────────────────────────────────

def parse_retry_seconds(error_message):
    m = re.search(r'try again in (\d+)m([\d.]+)s', str(error_message))
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.search(r'try again in ([\d.]+)s', str(error_message))
    if m:
        return float(m.group(1))
    return 30.0


def call_groq(text):
    """Single Groq call (no retry — retry handled by caller)."""
    logger.debug("Calling Groq (%s), text length: %d chars", GROQ_MODEL, len(text))
    t0 = time.monotonic()
    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.0,
        max_tokens=2000,
        messages=[
            {
                "role": "system",
                "content": "You are a resume parser. Extract structured data and return ONLY valid JSON with no markdown, no backticks, no explanation."
            },
            {
                "role": "user",
                "content": f"Resume:\n\n{text}\n\n{get_extraction_prompt()}"
            }
        ]
    )
    elapsed = time.monotonic() - t0
    logger.debug("Groq response received in %.2fs", elapsed)
    return response.choices[0].message.content.strip()


def call_gemini(text):
    """Single Gemini call."""
    logger.debug("Calling Gemini (gemini-2.5-flash), text length: %d chars", len(text))
    t0 = time.monotonic()
    prompt = (
        "You are a resume parser. Extract structured data and return ONLY valid JSON "
        "with no markdown, no backticks, no explanation.\n\n"
        f"Resume:\n\n{text}\n\n{get_extraction_prompt()}"
    )
    response = gemini_model.generate_content(prompt)
    elapsed = time.monotonic() - t0
    logger.debug("Gemini response received in %.2fs", elapsed)
    raw = response.text.strip()
    # Strip markdown fences if Gemini adds them
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return raw.strip()


def call_ai_with_fallback(text, max_retries=4):
    """
    Try providers in round-robin order. On rate-limit from one provider,
    immediately try the other. Falls back to waiting only if both are exhausted.
    """
    providers_available = []
    if groq_client:
        providers_available.append("groq")
    if gemini_model:
        providers_available.append("gemini")

    if not providers_available:
        raise RuntimeError("No AI provider configured. Set GROQ_API_KEY and/or GEMINI_API_KEY.")

    # Determine starting provider via round-robin
    primary = _next_provider()
    # Build ordered list: [primary, other, primary, other, ...]
    others = [p for p in providers_available if p != primary]
    order = []
    for i in range(max_retries):
        order.append(primary if i % 2 == 0 else (others[0] if others else primary))

    logger.debug("AI call plan: primary=%s, attempts=%s", primary, order)

    last_error = None
    for attempt, provider in enumerate(order):
        try:
            logger.debug("AI attempt %d/%d using provider: %s", attempt + 1, max_retries, provider)
            if provider == "groq":
                result = call_groq(text), "groq"
            else:
                result = call_gemini(text), "gemini"
            logger.debug("AI call succeeded on attempt %d via %s", attempt + 1, provider)
            return result

        except Exception as e:
            err_str = str(e)
            last_error = e
            is_rate_limit = (
                ('429' in err_str and 'rate_limit_exceeded' in err_str)  # Groq
                or ('429' in err_str)                                      # Gemini
                or ('quota' in err_str.lower())
                or ('resource_exhausted' in err_str.lower())
            )

            if is_rate_limit:
                logger.warning(
                    "Rate limit hit on %s (attempt %d/%d) — %s",
                    provider, attempt + 1, max_retries,
                    err_str[:120]
                )
            else:
                logger.error(
                    "AI error on %s (attempt %d/%d): %s",
                    provider, attempt + 1, max_retries, err_str[:200]
                )

            # If rate limited and we have an alternative provider, switch immediately
            if is_rate_limit and others:
                continue  # next iteration uses other provider

            # For non-rate-limit errors, wait briefly and retry same provider
            if attempt < len(order) - 1:
                wait = min(parse_retry_seconds(err_str) + 2, 90) if is_rate_limit else 5
                logger.info("Waiting %.1fs before retry...", wait)
                time.sleep(wait)
                continue

            raise

    raise RuntimeError(f"All AI providers failed after {max_retries} attempts. Last error: {last_error}")


# ── Core extraction ───────────────────────────────────────────────────────────

def extract_resume_data(file_content, filename):
    t0 = time.monotonic()
    logger.info("Processing '%s' (%d bytes)", filename, len(file_content))

    text, url_overrides = extract_text_from_file(file_content, filename)

    if not text or len(text.strip()) < 50:
        raise ValueError("Could not extract readable text from file")

    original_len = len(text)
    text = text[:6000]
    if original_len > 6000:
        logger.debug("'%s' — text truncated from %d to 6000 chars", filename, original_len)

    raw, provider_used = call_ai_with_fallback(text)

    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    match = re.search(r'\{.*\}', raw, re.DOTALL)
    data = {}
    if match:
        try:
            data = normalize_fields(json.loads(match.group()))
        except json.JSONDecodeError as e:
            logger.warning("'%s' — JSON parse failed (%s), raw snippet: %s", filename, e, raw[:100])
    else:
        logger.warning("'%s' — no JSON object found in AI response, raw snippet: %s", filename, raw[:100])

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
    return output_path


# ── Flask routes ──────────────────────────────────────────────────────────────

def sse_event(data):
    return f"data: {json.dumps(data)}\n\n"


@app.route("/")
def index():
    logger.debug("GET / — serving index.html")
    return app.send_static_file('index.html')


@app.route("/health")
def health():
    logger.debug("GET /health")
    providers = {
        "groq": groq_client is not None,
        "gemini": gemini_model is not None
    }
    return jsonify({"status": "ok", "providers": providers})


@app.route("/extract", methods=["POST"])
def extract():
    """
    Streaming SSE endpoint. Processes files sequentially.
    Uses Groq + Gemini in round-robin; auto-falls back on rate limits.
    Supports optional job_description field for match scoring.
    Detects duplicate candidates by email/phone.
    """
    if "files" not in request.files:
        logger.warning("POST /extract — no files in request")
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
            logger.warning("Rejected unsupported file type: '%s'", file.filename)
            file_payloads.append({"filename": file.filename, "content": None, "error": "Unsupported file type"})
        else:
            content = file.read()
            logger.debug("Accepted file: '%s' (%d bytes)", file.filename, len(content))
            file_payloads.append({"filename": file.filename, "content": content, "error": None})

    if not file_payloads:
        logger.warning("POST /extract — no valid files after filtering")
        return jsonify({"error": "No valid files found"}), 400

    logger.info("POST /extract — starting batch of %d file(s)", len(file_payloads))

    def generate():
        results = []
        errors  = []
        total   = len(file_payloads)
        batch_start = time.monotonic()

        # Duplicate tracking: maps normalised email/phone -> first candidate name
        seen_emails  = {}
        seen_phones  = {}

        yield sse_event({"type": "start", "total": total})

        for i, payload in enumerate(file_payloads):
            filename  = payload["filename"]
            completed = i + 1

            if payload["error"]:
                logger.warning("[%d/%d] Skipping '%s': %s", completed, total, filename, payload["error"])
                errors.append({"file": filename, "error": payload["error"]})
                yield sse_event({
                    "type": "progress", "filename": filename, "ok": False,
                    "error": payload["error"], "completed": completed,
                    "total": total, "pct": round((completed / total) * 90)
                })
                continue

            try:
                logger.info("[%d/%d] Extracting '%s'", completed, total, filename)
                data = extract_resume_data(payload["content"], filename)
                data["filename"] = filename

                # ── Duplicate detection ───────────────────────────────────────
                dup_of = None
                email_key = (data.get("email") or "").lower().strip()
                phone_key  = re.sub(r'\D', '', data.get("phone") or "")

                if email_key and email_key in seen_emails:
                    dup_of = seen_emails[email_key]
                elif phone_key and len(phone_key) >= 7 and phone_key in seen_phones:
                    dup_of = seen_phones[phone_key]

                if dup_of:
                    data["duplicate_of"] = dup_of
                    logger.info("'%s' flagged as duplicate of '%s'", filename, dup_of)
                else:
                    candidate_label = data.get("full_name") or filename
                    if email_key:
                        seen_emails[email_key] = candidate_label
                    if phone_key and len(phone_key) >= 7:
                        seen_phones[phone_key] = candidate_label

                # ── Job match scoring (only if JD provided) ───────────────────
                if job_description and not dup_of:
                    try:
                        score, reason = score_resume_against_jd(data, job_description)
                        if score is not None:
                            data["match_score"] = str(score)
                            data["match_reason"] = reason or ""
                            logger.info("'%s' — match score: %s/100", filename, score)
                    except Exception as score_err:
                        logger.warning("Scoring skipped for '%s': %s", filename, score_err)

                results.append(data)
                logger.info("[%d/%d] '%s' — OK", completed, total, filename)
                yield sse_event({
                    "type": "progress", "filename": filename, "ok": True,
                    "error": None, "completed": completed,
                    "total": total, "pct": round((completed / total) * 90),
                    "data": data
                })
            except Exception as e:
                logger.error("[%d/%d] '%s' — FAILED: %s", completed, total, filename, e, exc_info=True)
                errors.append({"file": filename, "error": str(e)})
                yield sse_event({
                    "type": "progress", "filename": filename, "ok": False,
                    "error": str(e), "completed": completed,
                    "total": total, "pct": round((completed / total) * 90)
                })

            # Small pause between files
            if i < total - 1:
                time.sleep(0.3)

        batch_elapsed = time.monotonic() - batch_start

        if results:
            try:
                create_excel(results)
                logger.info(
                    "Batch complete in %.2fs — %d/%d succeeded, %d error(s)",
                    batch_elapsed, len(results), total, len(errors)
                )
                yield sse_event({
                    "type": "done", "success": True,
                    "processed": len(results), "errors": errors,
                    "download_url": "/download", "pct": 100,
                    "results": results
                })
            except Exception as e:
                logger.error("Excel generation failed: %s", e, exc_info=True)
                yield sse_event({
                    "type": "done", "success": False,
                    "error": f"Excel generation failed: {str(e)}",
                    "processed": 0, "errors": errors, "pct": 0
                })
        else:
            logger.error("Batch complete — no resumes could be processed (%d error(s))", len(errors))
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
        logger.warning("GET /download — no Excel file found at %s", excel_path)
        return jsonify({"error": "No file to download"}), 404
    logger.info("GET /download — serving extracted_resumes.xlsx")
    return send_file(
        excel_path, as_attachment=True,
        download_name="extracted_resumes.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info("Starting Flask dev server on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)