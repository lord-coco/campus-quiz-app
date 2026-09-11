# 🎯 Campus Quiz App — v4

Real-time local network quiz with timed scoring, explanations, spectator mode, and PDF results.

---

## Setup

Works the same whether you host from a **laptop** or an **Android phone (via Termux)**. Just needs Python 3.9+ on whichever device is acting as host.

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

## Hosting Options

| Hosting from | Players connect via |
|---|---|
| **Laptop**, same WiFi network as players | Laptop's local network IP |
| **Laptop**, no shared WiFi available | Turn on the laptop's mobile hotspot, players join that |
| **Phone (Termux)** | Turn on the phone's hotspot, players join that |

Either way, the host machine needs to stay on and running `app.py` for the whole quiz session.

---

## URLs

| URL | Who uses it |
|-----|-------------|
| `http://localhost:5000/admin` | You (host) — admin tab |
| `http://localhost:5000/` | You + coursemates — player tab |
| `http://localhost:5000/spectator` | Projector / big screen view |
| `<host-ip>:5000/` | Everyone else connected to the same network/hotspot |

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

## Find Your Host IP

Run whichever command matches the device you're hosting from:

- **Laptop (Windows):** `ipconfig` → IPv4 Address
- **Laptop (Mac/Linux):** `ip addr` or `ifconfig`
- **Phone (Termux):** `ip route`

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
- 
