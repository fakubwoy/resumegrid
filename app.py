from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import json
import re
import tempfile
import pdfplumber
import pypdf
import docx2txt
from groq import Groq
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

app = Flask(__name__, static_folder='static')
CORS(app)

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
GROQ_MODEL = "llama-3.3-70b-versatile"

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
    "full_name", "email", "phone", "location", "linkedin", "github", "portfolio",
    "current_title", "years_of_experience", "summary",
    "skills", "programming_languages", "frameworks",
    "education_degree", "education_institution", "education_year", "education_gpa",
    "companies_worked", "most_recent_company", "most_recent_role", "most_recent_duration", "total_companies",
    "certifications", "languages_spoken", "projects", "achievements",
    "last_ctc", "current_status"
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
    "last_ctc": "Last CTC", "current_status": "Current Status"
}


def extract_hyperlinks_from_pdf(tmp_path: str) -> list[str]:
    """
    Extract all hyperlink URLs from PDF annotations using pypdf.
    Handles both /URI annotations and /GoToR actions.
    """
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


def classify_urls(urls: list[str]) -> dict:
    """
    Classify a list of URLs into linkedin / github / portfolio / email buckets.
    Returns a dict of field -> url for any that are identified.
    """
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
            # Anything else is likely a portfolio/personal site
            result["portfolio"] = url
    return result


def extract_text_from_file(file_content: bytes, filename: str) -> tuple[str, dict]:
    """
    Extract plain text AND any embedded hyperlink URLs from the file.
    Returns (text, url_overrides) where url_overrides is a dict of
    canonical field -> URL pulled from annotations (not text).
    """
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name
        try:
            # Extract plain text
            parts = []
            with pdfplumber.open(tmp_path) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        parts.append(t)
            text = "\n".join(parts)

            # Extract hyperlinks from annotations
            urls = extract_hyperlinks_from_pdf(tmp_path)
            url_overrides = classify_urls(urls)

            return text, url_overrides
        finally:
            os.unlink(tmp_path)

    elif ext in [".doc", ".docx"]:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name
        try:
            text = docx2txt.process(tmp_path)
            # docx2txt doesn't extract hyperlinks; try python-docx for those
            url_overrides = extract_hyperlinks_from_docx(tmp_path)
            return text, url_overrides
        finally:
            os.unlink(tmp_path)

    return file_content.decode("utf-8", errors="ignore"), {}


def extract_hyperlinks_from_docx(tmp_path: str) -> dict:
    """Extract hyperlinks from a .docx file using python-docx relationships."""
    urls = []
    try:
        from docx import Document
        doc = Document(tmp_path)
        # Relationships hold the actual hyperlink targets
        for rel in doc.part.rels.values():
            if "hyperlink" in rel.reltype.lower():
                url = rel.target_ref
                if url.startswith("http") and url not in urls:
                    urls.append(url)
    except Exception:
        pass
    return classify_urls(urls)


def get_extraction_prompt() -> str:
    return """Extract ALL information from this resume and return it as a JSON object.

Include these fields (use null if not found):
- full_name, email, phone, location, linkedin, github, portfolio
- current_title, years_of_experience, summary
- skills, programming_languages, frameworks
- education_degree, education_institution, education_year, education_gpa
- companies_worked (comma-separated list), most_recent_company, most_recent_role, most_recent_duration, total_companies
- certifications, languages_spoken, projects, achievements
- last_ctc (last/current CTC or salary - if not explicitly mentioned, leave as null)
- current_status (determine from resume context: "Employed" if currently working, "Not Employed" if between jobs/seeking opportunities, "Fresher" if no work experience - infer from context)

For current_status, analyze the resume:
- If mentions "current" role or dates like "2023-Present" → "Employed"
- If most recent job ended in past or mentions "seeking" → "Not Employed"  
- If no work experience section or only internships/projects → "Fresher"

Return ONLY valid JSON with no explanation or markdown."""


def extract_resume_data(file_content: bytes, filename: str) -> dict:
    text, url_overrides = extract_text_from_file(file_content, filename)

    if not text or len(text.strip()) < 50:
        raise ValueError("Could not extract readable text from file")

    text = text[:6000]

    response = client.chat.completions.create(
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

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    match = re.search(r'\{.*\}', raw, re.DOTALL)
    data = {}
    if match:
        try:
            data = normalize_fields(json.loads(match.group()))
        except json.JSONDecodeError:
            pass

    # Override with actual embedded hyperlink URLs — these are ground truth
    # Only override if the LLM produced a vague label like "GitHub" or "LinkedIn"
    # or if it found nothing at all
    for field, url in url_overrides.items():
        existing = data.get(field, "")
        # Replace if empty, or if value looks like a display label rather than a real URL
        if not existing or not existing.startswith("http"):
            data[field] = url

    return data


def normalize_fields(data: dict) -> dict:
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


def create_excel(all_data: list) -> str:
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

    header_font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
    header_fill = PatternFill("solid", start_color="1A3A5C")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell_font = Font(name="Arial", size=9)
    cell_align = Alignment(vertical="top", wrap_text=True)
    alt_fill = PatternFill("solid", start_color="EBF2FA")
    thin_border = Border(
        left=Side(style='thin', color='CCCCCC'), right=Side(style='thin', color='CCCCCC'),
        top=Side(style='thin', color='CCCCCC'), bottom=Side(style='thin', color='CCCCCC')
    )

    for col_idx, col_key in enumerate(final_columns, 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.value = "Source File" if col_key == "source_file" else COLUMN_HEADERS.get(col_key, col_key.replace("_", " ").title())
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border
    ws.row_dimensions[1].height = 30

    for row_idx, candidate in enumerate(all_data, 2):
        for col_idx, col_key in enumerate(final_columns, 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            value = candidate.get("filename", "") if col_key == "source_file" else candidate.get(col_key, "")

            # Make URL fields clickable hyperlinks in Excel
            if value and str(value).startswith("http") and col_key in ("linkedin", "github", "portfolio"):
                cell.value = value
                cell.hyperlink = value
                cell.font = Font(name="Arial", size=9, color="0563C1", underline="single")
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
        "last_ctc": 18, "current_status": 18
    }
    for col_idx, col_key in enumerate(final_columns, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width_map.get(col_key, 20)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(final_columns))}1"

    output_path = os.path.join(UPLOAD_FOLDER, "extracted_resumes.xlsx")
    wb.save(output_path)
    return output_path


@app.route("/")
def index():
    return app.send_static_file('index.html')


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/extract", methods=["POST"])
def extract():
    if "files" not in request.files:
        return jsonify({"error": "No files uploaded"}), 400

    files = request.files.getlist("files")
    results, errors = [], []

    for file in files:
        if not file.filename:
            continue
        filename = file.filename
        ext = Path(filename).suffix.lower()
        if ext not in [".pdf", ".doc", ".docx"]:
            errors.append({"file": filename, "error": "Unsupported file type"})
            continue
        try:
            data = extract_resume_data(file.read(), filename)
            data["filename"] = filename
            results.append(data)
        except Exception as e:
            errors.append({"file": filename, "error": str(e)})

    if not results:
        return jsonify({"error": "No resumes could be processed", "details": errors}), 400

    create_excel(results)
    return jsonify({"success": True, "processed": len(results), "errors": errors, "download_url": "/download"})


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)