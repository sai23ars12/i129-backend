# I-129 Petition Generator

Fills the **official USCIS Form I-129** PDF from a web questionnaire.

## How it works
1. Consultant opens your URL and fills a 5-step questionnaire
2. Hits "Submit & Download I-129"  
3. The server fills the real official PDF and returns it as a download
4. You also receive an email with the filled PDF attached (optional)

## Files
```
i129-app/
├── app.py               ← Flask backend
├── i-129.pdf            ← Official USCIS I-129 PDF
├── requirements.txt
├── Procfile
├── railway.toml         ← Railway config
├── render.yaml          ← Render config
└── static/
    └── index.html       ← Frontend questionnaire
```

---

## Deploy on Railway (Recommended — Free tier available)

1. Go to **railway.app** and sign up (free)
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Push this folder to a GitHub repo first (see below), OR use **"Empty Project"** → drag and drop
4. Set environment variables (optional, for email):
   - `NOTIFY_EMAIL` — your email to receive submissions
   - `SMTP_HOST` — e.g. `smtp.gmail.com`
   - `SMTP_PORT` — `587`
   - `SMTP_USER` — your Gmail address
   - `SMTP_PASS` — Gmail App Password (not your regular password)
5. Railway auto-detects Python and deploys → you get a URL like `https://i129-form.up.railway.app`

### Push to GitHub first (required for Railway)
```bash
cd i129-app
git init
git add .
git commit -m "Initial deploy"
# Create a repo on github.com, then:
git remote add origin https://github.com/YOURNAME/i129-form.git
git push -u origin main
```

---

## Deploy on Render (Alternative — also free)

1. Go to **render.com** and sign up
2. Click **"New"** → **"Web Service"**
3. Connect your GitHub repo
4. Render reads `render.yaml` automatically
5. Edit `render.yaml` with your email credentials before pushing

---

## Optional: Gmail App Password setup
1. Go to myaccount.google.com → Security → 2-Step Verification (must be ON)
2. Search "App passwords" → Create one named "I129 Form"
3. Use that 16-character password as `SMTP_PASS`

---

## Local testing
```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
```
