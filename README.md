# 🎯 Campus Quiz App — v4

Real-time local network quiz with timed scoring, explanations, spectator mode, and PDF results.

---

## Setup

```bash
pip install -r requirements.txt
python app.py
```

**Before running for real:**
- Change the default admin PIN in `quiz_config.json` (default: `1234`) via the admin panel or by editing the file directly
- Generate your own Flask secret key and replace the placeholder in `app.py` line 16:
  ```bash
  python -c "import secrets; print(secrets.token_hex(16))"
  ```

---

## URLs

| URL | Who uses it |
|-----|-------------|
| `http://localhost:5000/admin` | You (host) — admin tab |
| `http://localhost:5000/` | You + coursemates — player tab |
| `http://localhost:5000/spectator` | Projector / big screen view |
| `<your-ip>:5000/` | Hotspot-connected coursemates |

---

## .docx Format

```
1. What is the powerhouse of the cell?
a. Nucleus
b. Ribosome
c. Mitochondria
d. Golgi body

2. Which blood type is the universal donor?
a. AB+
b. A-
c. O-
d. B+

ANSWERS
1. C | Mitochondria produce ATP through oxidative phosphorylation.
2. C | O- has no antigens, so it can donate to any blood type.
```

- Questions: numbered `1.` `2.` etc, options `a.` through `e.`
- Answers section: `ANSWERS` alone on its own line
- Explanations: optional, add after `|` on the answer line
- If no explanation needed, just write `1. C` with no pipe

---

## Scoring System

| Answer timing | Points earned |
|--------------|---------------|
| Answered instantly | ~1000 pts |
| Answered halfway through timer | ~750 pts |
| Answered just before timer ends | ~500 pts |
| Wrong answer | 0 pts |

---

## Find Your Hotspot IP

- **Windows:** `ipconfig` → IPv4 Address
- **Mac/Linux:** `ip addr` or `ifconfig`
- **Android (Termux):** `ip route`

---

## Features

- ✅ Question bank support (pick N from 500 questions)
- ✅ Speed-based scoring (500–1000 pts per correct answer)
- ✅ Explanation shown after each answer is revealed
- ✅ Spectator mode for projector display
- ✅ Live leaderboard after every question
- ✅ Past quiz history saved to SQLite
- ✅ Export results as PDF
- ✅ Light/dark theme toggle
- ✅ Reconnect-safe (score preserved)
