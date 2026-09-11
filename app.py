import os
import re
import time
import random
import glob
import html
import sqlite3
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response, session, redirect, url_for
from flask_socketio import SocketIO, emit
from docx import Document
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'CHANGE_ME_generate_your_own_random_key'  # e.g. run: python -c "import secrets; print(secrets.token_hex(16))"
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', ping_timeout=80, ping_interval=20)

# ─── NETWORK HELPERS ────────────────────────────────────────────
def get_local_ip():
    import socket as _socket
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

# ─── DATABASE ────────────────────────────────────────────────────
DB_PATH = 'quiz_history.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS quiz_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_name TEXT NOT NULL,
            played_at TEXT NOT NULL,
            total_questions INTEGER,
            player_count INTEGER,
            results TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def save_quiz_to_db(quiz_name, total_questions, leaderboard):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        INSERT INTO quiz_sessions (quiz_name, played_at, total_questions, player_count, results)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        quiz_name,
        datetime.now().strftime('%Y-%m-%d %H:%M'),
        total_questions,
        len(leaderboard),
        json.dumps(leaderboard)
    ))
    conn.commit()
    conn.close()

def get_all_sessions():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM quiz_sessions ORDER BY played_at DESC').fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_session_by_id(session_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM quiz_sessions WHERE id = ?', (session_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def delete_session(session_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('DELETE FROM quiz_sessions WHERE id = ?', (session_id,))
    conn.commit()
    conn.close()

# ─── CONFIG (PIN) ────────────────────────────────────────────────
CONFIG_PATH = 'quiz_config.json'

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    # First run — create default config
    cfg = {"admin_pin": "1234"}
    save_config(cfg)
    return cfg

def save_config(cfg):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)

# ─── GLOBAL STATE ────────────────────────────────────────────────
PRELOADED_QUIZZES = {}

game_state = {
    "is_running": False,
    "current_question_index": 0,
    "current_question": None,       # broadcast-safe question (no correct_id)
    "scores": {},                   # sid -> player dict
    "name_registry": {},            # name -> saved score data (for rejoin)
    "total_questions": 0,
    "seconds_per_question": 30,
    "lb_frequency": 1,              # show leaderboard every N questions
    "quiz_name": "",
    "questions": [],
    "current_correct_id": -1,
    "current_explanation": "",
    "question_start_time": 0.0,
    "is_paused": False,
}

# ─── QUIZ PARSING ────────────────────────────────────────────────
def parse_quiz_lines(all_text):
    """
    Core parser — accepts a list of non-empty stripped strings.
    Handles both .docx paragraph lists and .txt line lists.

    Question formats : 1. text  |  1) text  |  [1]. text  |  [1] text
    Option formats   : A. text  |  A) text  |  a. text    |  a) text
    Answer formats   : 1. A     |  [1]. A   |  1) A
    Explanation      : inline after | or — , or on the very next line
    """

    split_index = -1
    for i, line in enumerate(all_text):
        if "ANSWERS" in line.upper() and len(line) < 20:
            split_index = i
            break

    if split_index == -1:
        return [], "No 'ANSWERS' section found in file."

    # Parse answer map + optional explanations
    # Supports BOTH inline (1. A | explanation) and next-line formats:
    #   1. A
    #   Explanation on next paragraph
    answer_map = {}      # question_num -> correct_option_index (0-4)
    explanation_map = {} # question_num -> explanation string
    mapper = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4}
    # Answer pattern handles: 1. A  |  1) A  |  [1]. A  |  [1] A
    ans_pattern = re.compile(r'^\[?(\d+)\]?\s*[.):?]?\s*([A-E])\b(.*)', re.IGNORECASE)
    answer_lines = all_text[split_index:]
    last_num = None

    for line in answer_lines:
        match = ans_pattern.match(line)
        if match:
            num = int(match.group(1))
            letter = match.group(2).upper()
            rest = match.group(3).strip()
            answer_map[num] = mapper.get(letter, 0)
            last_num = num
            # Inline explanation after | or - or – or —
            exp_match = re.match(r'^[\|\-\u2013\u2014]\s*(.+)', rest)
            if exp_match:
                explanation_map[num] = exp_match.group(1).strip()
                last_num = None  # already consumed
        elif last_num is not None and last_num not in explanation_map:
            # Next-line explanation: non-empty, not another answer line
            stripped = line.strip()
            if stripped and not ans_pattern.match(stripped) and "ANSWERS" not in stripped.upper():
                explanation_map[last_num] = stripped
                last_num = None

    # Parse questions
    quiz_list = []
    current_q = None
    # Question pattern handles: 1. text  |  1) text  |  [1]. text  |  [1] text
    q_pattern = re.compile(r'^\[?(\d+)\]?\s*[.)]\s+(.+)')

    for line in all_text[:split_index]:
        q_match = q_pattern.match(line)
        if q_match:
            if current_q and current_q['options'] and current_q['number'] in answer_map:
                current_q['correct_id'] = answer_map[current_q['number']]
                current_q['explanation'] = explanation_map.get(current_q['number'], '')
                quiz_list.append(current_q)
            current_q = {
                'number': int(q_match.group(1)),
                'text': q_match.group(2),
                'options': [],
                'correct_id': -1,
                'explanation': '',
            }
            continue
        if current_q:
            if re.match(r'^[a-eA-E][.)]', line):  # handles A. A) a. a)
                current_q['options'].append(re.sub(r'^[a-eA-E][.)]\s*', '', line))
            elif not current_q['options']:
                current_q['text'] += " " + line

    if current_q and current_q['options'] and current_q['number'] in answer_map:
        current_q['correct_id'] = answer_map[current_q['number']]
        current_q['explanation'] = explanation_map.get(current_q['number'], '')
        quiz_list.append(current_q)

    return quiz_list, None


def extract_quiz_from_docx(docx_path):
    """Read a .docx file and parse it."""
    try:
        doc = Document(docx_path)
    except Exception as e:
        return [], f"Cannot open .docx: {e}"
    all_text = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return parse_quiz_lines(all_text)


def extract_quiz_from_txt(txt_path):
    """Read a plain .txt file and parse it."""
    try:
        with open(txt_path, 'r', encoding='utf-8', errors='replace') as f:
            all_text = [line.strip() for line in f if line.strip()]
    except Exception as e:
        return [], f"Cannot open .txt: {e}"
    return parse_quiz_lines(all_text)


def load_quiz_files():
    """Scan uploads/ and root for .docx and .txt quiz files."""
    PRELOADED_QUIZZES.clear()
    paths = (
        glob.glob("*.docx") + glob.glob("uploads/*.docx") +
        glob.glob("uploads/*.txt")
    )
    for path in paths:
        name = os.path.basename(path).rsplit('.', 1)[0].lower()
        ext  = path.rsplit('.', 1)[-1].lower()
        if ext == 'docx':
            qs, err = extract_quiz_from_docx(path)
        else:
            qs, err = extract_quiz_from_txt(path)
        if qs:
            PRELOADED_QUIZZES[name] = qs
            print(f"  ✅ Loaded: {name} ({len(qs)} questions) [{ext}]")
        else:
            print(f"  ❌ Skipped {name}: {err}")


# ─── ROUTES ──────────────────────────────────────────────────────
@app.route('/')
def player_page():
    return render_template('player.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    cfg = load_config()
    error = None
    if request.method == 'POST':
        entered = request.form.get('pin', '').strip()
        if entered == cfg['admin_pin']:
            session['admin_authenticated'] = True
            return redirect(url_for('admin_page'))
        error = 'Wrong PIN. Try again.'
    return render_template('admin_login.html', error=error)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_authenticated', None)
    return redirect(url_for('admin_login'))

@app.route('/admin/change-pin', methods=['POST'])
def change_pin():
    if not session.get('admin_authenticated'):
        return jsonify({"error": "Not authenticated"}), 401
    data = request.get_json()
    new_pin = str(data.get('pin', '')).strip()
    if not new_pin.isdigit() or not (4 <= len(new_pin) <= 8):
        return jsonify({"error": "PIN must be 4-8 digits"}), 400
    cfg = load_config()
    cfg['admin_pin'] = new_pin
    save_config(cfg)
    return jsonify({"success": True})

@app.route('/admin')
def admin_page():
    if not session.get('admin_authenticated'):
        return redirect(url_for('admin_login'))
    return render_template('admin.html')

@app.route('/spectator')
def spectator_page():
    return render_template('spectator.html')

@app.route('/api/server-ip')
def get_server_ip():
    return jsonify({"ip": get_local_ip(), "port": 5000})

@app.route('/api/quizzes')
def get_quizzes():
    load_quiz_files()
    return jsonify({name: len(qs) for name, qs in PRELOADED_QUIZZES.items()})

@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files['file']
    if not (f.filename.endswith('.docx') or f.filename.endswith('.txt')):
        return jsonify({"error": "Only .docx or .txt files allowed"}), 400
    fname = secure_filename(f.filename)
    f.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
    load_quiz_files()
    return jsonify({"success": True, "name": fname.rsplit(".", 1)[0].lower()})

@app.route('/api/history')
def get_history():
    sessions = get_all_sessions()
    for s in sessions:
        s['results'] = json.loads(s['results'])
    return jsonify(sessions)

@app.route('/api/history/<int:session_id>', methods=['DELETE'])
def delete_history(session_id):
    delete_session(session_id)
    return jsonify({"success": True})

@app.route('/api/status')
def get_status():
    return jsonify({
        "is_running": game_state["is_running"],
        "player_count": len(game_state["scores"]),
        "quiz_name": game_state["quiz_name"],
    })

@app.route('/api/export/<int:session_id>')
def export_session(session_id):
    """Returns a print-ready HTML page for PDF export."""
    session = get_session_by_id(session_id)
    if not session:
        return "Session not found", 404

    results = json.loads(session['results'])
    medals = ['🥇', '🥈', '🥉']

    rows_html = ""
    for i, p in enumerate(results):
        medal = medals[i] if i < 3 else str(i + 1)
        score = p.get('score', p.get('correct', 0) * 1000)
        rows_html += f"""
        <tr class="{'top3' if i < 3 else ''}">
            <td class="rank">{medal}</td>
            <td class="name">{html.escape(p['name'])}</td>
            <td class="score">{score:,}</td>
            <td>{p.get('correct', 0)}</td>
            <td>{p.get('wrong', 0)}</td>
            <td>{p.get('attempted', 0)} / {session['total_questions']}</td>
        </tr>"""

    page = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Quiz Results — {html.escape(session['quiz_name'])}</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@700;800&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Space Mono', monospace; background: #fff; color: #1c1c2e; padding: 40px; }}
  .header {{ border-bottom: 3px solid #059862; padding-bottom: 20px; margin-bottom: 30px; display: flex; justify-content: space-between; align-items: flex-end; }}
  .header h1 {{ font-family: 'Syne', sans-serif; font-size: 28px; font-weight: 800; }}
  .header h1 span {{ color: #059862; }}
  .meta {{ font-size: 12px; color: #7a7a92; text-align: right; line-height: 2; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; padding: 10px 14px; font-size: 10px; letter-spacing: 2px; text-transform: uppercase; color: #7a7a92; border-bottom: 2px solid #e8eaf4; }}
  td {{ padding: 12px 14px; border-bottom: 1px solid #f0f2f8; font-size: 13px; }}
  tr.top3 td {{ font-weight: 700; }}
  tr:nth-child(1) td {{ background: #fffbea; }}
  tr:nth-child(2) td {{ background: #f8f8f8; }}
  tr:nth-child(3) td {{ background: #fff6f0; }}
  .rank {{ font-size: 18px; width: 50px; }}
  .name {{ font-size: 14px; }}
  .score {{ color: #059862; font-size: 16px; font-weight: 700; }}
  .footer {{ margin-top: 30px; font-size: 11px; color: #aaa; text-align: center; border-top: 1px solid #eee; padding-top: 16px; }}
  @media print {{
    body {{ padding: 20px; }}
    .no-print {{ display: none; }}
  }}
  .print-btn {{
    display: inline-block; margin-bottom: 24px;
    padding: 10px 24px; background: #059862; color: #fff;
    border: none; border-radius: 6px; font-family: 'Space Mono', monospace;
    font-size: 13px; cursor: pointer; font-weight: 700;
  }}
</style>
</head>
<body>
<button class="print-btn no-print" onclick="window.print()">🖨 Print / Save as PDF</button>
<div class="header">
  <div>
    <div style="font-size:11px;letter-spacing:3px;color:#7a7a92;text-transform:uppercase;margin-bottom:6px;">Quiz Results</div>
    <h1>📝 <span>{html.escape(session['quiz_name'].title())}</span></h1>
  </div>
  <div class="meta">
    <div>📅 {session['played_at']}</div>
    <div>❓ {session['total_questions']} Questions</div>
    <div>👥 {session['player_count']} Players</div>
  </div>
</div>
<table>
  <thead>
    <tr>
      <th>Rank</th>
      <th>Player</th>
      <th>🏆 Score</th>
      <th>✅ Correct</th>
      <th>❌ Wrong</th>
      <th>Attempted</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>
<div class="footer">Generated by Campus Quiz App • {session['played_at']}</div>
<script>
  // Auto-open print dialog after fonts load
  window.onload = function() {{
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('autoprint') === '1') setTimeout(() => window.print(), 800);
  }};
</script>
</body>
</html>"""

    return Response(page, mimetype='text/html')


# ─── SOCKET EVENTS ───────────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    if sid in game_state["scores"]:
        player = game_state["scores"][sid]
        name = player["name"]
        # Save score to registry so player can rejoin and restore it
        game_state["name_registry"][name] = {
            "correct": player["correct"],
            "wrong": player["wrong"],
            "score": player["score"],
            "streak": player["streak"],
        }
        del game_state["scores"][sid]
        socketio.emit('player_list', get_player_list())
        print(f"Player left: {name} (score saved for rejoin)")

@socketio.on('join_game')
def on_join(data):
    name = html.escape(data.get('name', 'Anonymous').strip()[:20])
    sid = request.sid

    # ── Duplicate name check ──
    # Check if this name is already held by a DIFFERENT active sid
    for active_sid, player in game_state["scores"].items():
        if player["name"] == name and active_sid != sid:
            emit('join_error', {"message": f'"{name}" is already taken. Choose a different name.'})
            return

    # ── Rejoin: restore saved score if name was previously in the game ──
    if sid not in game_state["scores"]:
        saved = game_state["name_registry"].get(name)
        if saved:
            # Reconnected player — restore their score
            game_state["scores"][sid] = {
                "name": name,
                "correct": saved["correct"],
                "wrong": saved["wrong"],
                "score": saved["score"],
                "answered_current": False,
                "streak": saved["streak"],
            }
            print(f"Player REJOINED: {name} (score restored: {saved['score']})")
        else:
            # Fresh join
            game_state["scores"][sid] = {
                "name": name,
                "correct": 0,
                "wrong": 0,
                "score": 0,
                "answered_current": False,
                "streak": 0,
            }
            print(f"Player joined: {name} ({sid})")

    socketio.emit('player_list', get_player_list())

    if game_state["is_running"] and game_state["current_question"]:
        emit('question', game_state["current_question"])

    emit('joined', {"name": name, "is_running": game_state["is_running"]})

@socketio.on('join_spectator')
def on_join_spectator():
    """Spectator connects — send current state without joining player list."""
    emit('spectator_state', {
        "is_running": game_state["is_running"],
        "quiz_name": game_state["quiz_name"],
        "leaderboard": get_leaderboard_data(),
    })
    if game_state["is_running"] and game_state["current_question"]:
        emit('question', game_state["current_question"])

@socketio.on('start_quiz')
def on_start_quiz(data):
    if game_state["is_running"]:
        emit('error', {"message": "Quiz already running!"})
        return

    quiz_name = data.get('quiz_name', '').lower()
    seconds = int(data.get('seconds', 30))
    num_questions = int(data.get('num_questions', 9999))
    lb_frequency = max(1, min(10, int(data.get('lb_frequency', 1))))

    if quiz_name not in PRELOADED_QUIZZES:
        emit('error', {"message": f"Quiz '{quiz_name}' not found."})
        return

    questions = PRELOADED_QUIZZES[quiz_name].copy()
    random.shuffle(questions)
    questions = [q for q in questions if q['correct_id'] != -1]

    if num_questions < len(questions):
        questions = questions[:num_questions]

    # Reset scores, keep players
    for sid in game_state["scores"]:
        game_state["scores"][sid].update({
            "correct": 0,
            "wrong": 0,
            "score": 0,
            "answered_current": False,
            "streak": 0,
        })

    game_state.update({
        "is_running": True,
        "questions": questions,
        "total_questions": len(questions),
        "current_question_index": 0,
        "quiz_name": quiz_name,
        "seconds_per_question": seconds,
        "lb_frequency": lb_frequency,
        "current_question": None,
        "current_correct_id": -1,
        "current_explanation": "",
        "question_start_time": 0.0,
        "name_registry": {},  # clear rejoin registry for fresh quiz
        "is_paused": False,
    })

    socketio.emit('quiz_starting', {
        "quiz_name": quiz_name,
        "total": len(questions),
        "seconds": seconds,
        "lb_frequency": lb_frequency,
    })

    print(f"Quiz starting: {quiz_name} | {len(questions)} questions | {seconds}s each | LB every {lb_frequency}q")
    socketio.start_background_task(run_quiz_loop)


def run_quiz_loop():
    questions = game_state["questions"]
    seconds = game_state["seconds_per_question"]
    lb_freq = game_state["lb_frequency"]

    # ── Lobby countdown 3..2..1 ──
    for tick in [3, 2, 1]:
        if not game_state["is_running"]:
            return
        socketio.emit('countdown_tick', {"count": tick})
        time.sleep(1)

    socketio.emit('quiz_started', {
        "quiz_name": game_state["quiz_name"],
        "total": len(questions),
        "seconds": seconds,
        "lb_frequency": lb_freq,
    })

    for i, q in enumerate(questions):
        if not game_state["is_running"]:
            break

        for sid in game_state["scores"]:
            game_state["scores"][sid]["answered_current"] = False

        game_state["current_question_index"] = i
        game_state["current_correct_id"] = q["correct_id"]
        game_state["current_explanation"] = q.get("explanation", "")
        game_state["question_start_time"] = time.time()

        question_data = {
            "index": i,
            "total": len(questions),
            "text": q["text"],
            "options": q["options"],
            "seconds": seconds,
            # correct_id intentionally withheld from clients
        }
        game_state["current_question"] = question_data

        socketio.emit('question', question_data)
        print(f"  ➡ Q{i+1}: {q['text'][:55]}...")

        elapsed = 0.0
        while elapsed < seconds:
            if not game_state["is_running"]:
                break
            if not game_state["is_paused"]:
                elapsed += 0.1
            time.sleep(0.1)

        # Decide if leaderboard should show after this question
        # Show on last question always, otherwise every lb_freq questions
        is_last = (i == len(questions) - 1)
        show_lb = is_last or ((i + 1) % lb_freq == 0)

        socketio.emit('answer_reveal', {
            "correct_id": q["correct_id"],
            "correct_text": q["options"][q["correct_id"]],
            "show_leaderboard": show_lb,
            "leaderboard": get_leaderboard_data() if show_lb else [],
        })

        # Give more time when showing leaderboard, less when just flashing answer
        # Also pauseable — same pattern as the question countdown
        lb_wait = 5 if show_lb else 2
        lb_elapsed = 0.0
        while lb_elapsed < lb_wait:
            if not game_state["is_running"]:
                break
            if not game_state["is_paused"]:
                lb_elapsed += 0.1
            time.sleep(0.1)

    lb = get_leaderboard_data()
    save_quiz_to_db(game_state["quiz_name"], game_state["total_questions"], lb)

    game_state["is_running"] = False
    game_state["current_question"] = None

    # Include questions for post-quiz review (with correct answers)
    review_questions = [
        {
            "index": idx,
            "text": q["text"],
            "options": q["options"],
            "correct_id": q["correct_id"],
            "explanation": q.get("explanation", ""),
        }
        for idx, q in enumerate(questions)
    ]

    socketio.emit('quiz_ended', {
        "leaderboard": lb,
        "total": game_state["total_questions"],
        "questions": review_questions,
    })
    print("Quiz ended.")


@socketio.on('submit_answer')
def on_submit_answer(data):
    sid = request.sid
    if sid not in game_state["scores"]:
        return
    if not game_state["is_running"]:
        return
    if game_state["scores"][sid]["answered_current"]:
        return

    option_id = int(data.get('option_id', -1))
    correct = option_id == game_state["current_correct_id"]

    game_state["scores"][sid]["answered_current"] = True

    points_earned = 0
    if correct:
        # Timed bonus: 5 base + up to 5 speed bonus (range: 5-10)
        elapsed = time.time() - game_state["question_start_time"]
        total_secs = game_state["seconds_per_question"]
        speed_ratio = max(0.0, 1.0 - (elapsed / total_secs))
        points_earned = round(5 + 5 * speed_ratio)  # Range: 5-10 points
        game_state["scores"][sid]["correct"] += 1
        game_state["scores"][sid]["score"] += points_earned
        game_state["scores"][sid]["streak"] += 1
    else:
        game_state["scores"][sid]["wrong"] += 1
        game_state["scores"][sid]["streak"] = 0

    emit('answer_result', {
        "correct": correct,
        "correct_id": game_state["current_correct_id"],
        "streak": game_state["scores"][sid]["streak"],
        "points_earned": points_earned,
        "total_score": game_state["scores"][sid]["score"],
    })

    answered_count = sum(1 for s in game_state["scores"].values() if s["answered_current"])
    socketio.emit('answer_progress', {
        "answered": answered_count,
        "total_players": len(game_state["scores"]),
    })

@socketio.on('pause_quiz')
def on_pause_quiz():
    if game_state["is_running"] and not game_state["is_paused"]:
        game_state["is_paused"] = True
        socketio.emit('quiz_paused', {})
        print("Quiz paused.")

@socketio.on('resume_quiz')
def on_resume_quiz():
    if game_state["is_running"] and game_state["is_paused"]:
        game_state["is_paused"] = False
        socketio.emit('quiz_resumed', {})
        print("Quiz resumed.")

@socketio.on('stop_quiz')
def on_stop_quiz():
    game_state["is_running"] = False
    socketio.emit('quiz_stopped', {"message": "Quiz stopped by admin."})


# ─── HELPERS ─────────────────────────────────────────────────────
def get_leaderboard_data():
    players = []
    active_names = set()

    # Currently connected players
    for sid, data in game_state["scores"].items():
        attempted = data["correct"] + data["wrong"]
        players.append({
            "name": data["name"],
            "score": data["score"],
            "correct": data["correct"],
            "wrong": data["wrong"],
            "attempted": attempted,
            "unanswered": max(0, game_state["current_question_index"] + 1 - attempted),
            "streak": data["streak"],
        })
        active_names.add(data["name"])

    # Disconnected players who participated — include if they answered at least one question
    for name, data in game_state["name_registry"].items():
        if name not in active_names:
            attempted = data["correct"] + data["wrong"]
            if attempted > 0:
                players.append({
                    "name": name + " (left)",
                    "score": data["score"],
                    "correct": data["correct"],
                    "wrong": data["wrong"],
                    "attempted": attempted,
                    "unanswered": max(0, game_state["current_question_index"] + 1 - attempted),
                    "streak": data["streak"],
                })

    players.sort(key=lambda x: (x["score"], x["correct"]), reverse=True)
    return players


def get_player_list():
    return [{"name": v["name"]} for v in game_state["scores"].values()]


# ─── ENTRY POINT ─────────────────────────────────────────────────
if __name__ == '__main__':
    import logging
    # Suppress the Werkzeug dev server warning banner
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    os.makedirs('uploads', exist_ok=True)
    init_db()
    load_quiz_files()
    local_ip = get_local_ip()
    print("\n🌐 Quiz Server Running!")
    print(f"   Admin     : http://localhost:5000/admin")
    print(f"   Player    : http://{local_ip}:5000/")
    print(f"   Spectator : http://{local_ip}:5000/spectator")
    print(f"   Share this URL with players: http://{local_ip}:5000/")
    print()

    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
