# ResumeGrid — Bulk Resume Extractor (Railway Edition)

Upload multiple PDF/DOC/DOCX resumes and get a clean Excel file with every candidate's data.
Powered by **Groq** using `llama-3.3-70b-versatile` — fast, free-tier available, open-source model.

**NEW:** Includes recruiter-critical fields: **Last CTC** and **Current Status** (Employed/Not Employed/Fresher)

## 🚀 Quick Deploy to Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template)

### Railway Deployment Steps

1. **Click the Railway button above** or go to [railway.app](https://railway.app)

2. **Connect your GitHub account** and create a new project from this repository

3. **Set environment variable:**
   - Go to your Railway project
   - Click on "Variables" tab
   - Add: `GROQ_API_KEY` = `your-groq-api-key-here`
   - Get a free Groq API key at: https://console.groq.com/keys

4. **Deploy:**
   - Railway will automatically detect the configuration
   - Build and deploy will start automatically
   - Your app will be live at: `https://your-app-name.railway.app`

5. **Access your app:**
   - Click the generated domain in Railway dashboard
   - Or set up a custom domain in Railway settings

## 📁 Project Structure

```
resumegrid/
├── app.py                  ← Flask API + static file serving
├── static/
│   └── index.html          ← Frontend UI
├── requirements.txt        ← Python dependencies
├── Procfile                ← Railway/Heroku deployment config
├── railway.json            ← Railway-specific settings
├── nixpacks.toml          ← Build configuration
├── .gitignore
└── README.md
```

## 🎯 Features

### Extracted Fields (28 columns total)

| Category      | Fields |
|---------------|--------|
| Contact       | Name, Email, Phone, Location, LinkedIn, GitHub, Portfolio |
| Career        | Current Title, Years of Experience, Summary |
| Skills        | Skills, Programming Languages, Frameworks |
| Education     | Degree, Institution, Graduation Year, GPA |
| Work History  | Companies Worked, Most Recent Company/Role/Duration, Total Companies |
| **Recruiter Fields** | **Last CTC, Current Status** |
| Other         | Certifications, Languages Spoken, Projects, Achievements |

### Smart Current Status Detection

The AI automatically determines candidate status:
- **Employed**: Has current role or dates like "2023-Present"
- **Not Employed**: Recent job ended or mentions "seeking opportunities"
- **Fresher**: No work experience or only internships/projects

## 🔧 Local Development

### Prerequisites
- Python 3.10+
- Groq API key (free at https://console.groq.com/keys)

### Setup

```bash
# Clone the repository
git clone <your-repo-url>
cd resumegrid

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set environment variable
export GROQ_API_KEY='your-groq-api-key-here'

# Run the app
python app.py
```

Open http://localhost:5000 in your browser

## 📊 How It Works

1. **Upload**: Drag & drop or select multiple resume files (PDF/DOC/DOCX)
2. **Extract**: Backend extracts text using pdfplumber (PDF) or docx2txt (DOC/DOCX)
3. **Parse**: Groq's llama-3.3-70b-versatile analyzes each resume
4. **Structure**: AI extracts all 28 fields including Last CTC and Status
5. **Export**: Excel file generated with one row per candidate
6. **Download**: Get your structured data instantly

## 🛠️ Configuration

### Environment Variables

Required:
- `GROQ_API_KEY`: Your Groq API key

Optional:
- `PORT`: Server port (default: 5000, Railway sets this automatically)

### Field Normalization

The system handles various field name aliases automatically:

- "Mobile" / "Cell" / "Tel" → Phone
- "Objective" / "Profile" / "About Me" → Summary
- "University" / "College" / "School" → Institution
- "Tech Stack" / "Technologies" / "Competencies" → Skills
- "CTC" / "Salary" / "Compensation" → Last CTC
- "Employment Status" / "Work Status" → Current Status

## 🎨 Tech Stack

- **Backend**: Flask + Gunicorn
- **AI Model**: Groq llama-3.3-70b-versatile
- **Text Extraction**: pdfplumber, pypdf, docx2txt
- **Excel Generation**: openpyxl
- **Frontend**: Vanilla JavaScript (no build step)
- **Deployment**: Railway (also compatible with Heroku, Render)

## 📝 Notes

### For Recruiters

The new fields help track candidates better:

- **Last CTC**: Extracted when mentioned; blank if not stated
- **Current Status**: Automatically inferred from resume context
  - Helps prioritize actively employed vs. immediately available candidates
  - Identifies fresh graduates automatically

### Accuracy Tips

- **CTC**: Will only appear if candidate mentioned it in resume
- **Status**: Inferred from work history dates and context
- **Best Results**: Use well-formatted resumes (DOCX or PDF)
- **DOC Files**: May have lower extraction quality; use DOCX when possible

## 🐛 Troubleshooting

**App not starting on Railway?**
- Check that `GROQ_API_KEY` is set in environment variables
- View build logs in Railway dashboard

**Extraction errors?**
- Verify your Groq API key is valid and has quota
- Check that uploaded files are valid PDF/DOC/DOCX
- Ensure resume text is extractable (not scanned images without OCR)

**Missing fields in output?**
- Last CTC only appears if mentioned in resume
- Current Status is always attempted but may be blank if unclear

## 📄 License

MIT License - feel free to use and modify

## 🙏 Credits

- AI Model: [Groq](https://groq.com) (llama-3.3-70b-versatile)
- Deployment: [Railway](https://railway.app)
- Icon/UI Design: Custom built with modern design principles

---

**Version 2.0** — Now with recruiter-critical fields!