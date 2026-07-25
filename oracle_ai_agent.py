#!/usr/bin/env python3
"""
oracle_ai_agent.py — AI Agent for Oracle DBA Automation
Run from a single command: python3 oracle_ai_agent.py [action]

Actions:
  patch       → Run oracle_auto_patch.sh with AI monitoring
  start       → Start DB + listener with AI verification
  stop        → Stop DB + listener with AI verification
  backup      → Run RMAN backup with AI monitoring
  status      → Full DB health check, AI interprets results
  space       → Tablespace space check, AI flags issues
  runbook     → Ask AI to generate + execute a custom task
  menu        → Interactive menu (default)

Examples:
  python3 oracle_ai_agent.py patch
  python3 oracle_ai_agent.py status
  python3 oracle_ai_agent.py runbook "check for blocking locks"
"""

import os, sys, subprocess, threading, time, textwrap, json
from datetime import datetime
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("Installing anthropic SDK...")
    subprocess.run([sys.executable, "-m", "pip", "install", "anthropic", "-q"], check=True)
    import anthropic

# ══════════════════════════════════════════════════════════════════
# CONFIG — edit these to match your environment
# ══════════════════════════════════════════════════════════════════
ORACLE_SID    = "RPRD"
ORACLE_HOME   = "/u01/app/oracle/product/19c/dbhome_1"
PATCH_SCRIPT  = "/u01/dba/scripts/patch.sh"   # your existing patch script
SCRIPTS_DIR   = "/u01//dba/scripts"                         # where your other shell scripts live
LOG_DIR       = "/tmp/agent_logs"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# ══════════════════════════════════════════════════════════════════

os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = f"{LOG_DIR}/agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

os.environ["ORACLE_SID"]  = ORACLE_SID
os.environ["ORACLE_HOME"] = ORACLE_HOME
os.environ["PATH"]        = f"{ORACLE_HOME}/bin:{ORACLE_HOME}/OPatch:{os.environ['PATH']}"

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Colours ───────────────────────────────────────────────────────
R="\033[0;31m"; G="\033[0;32m"; Y="\033[1;33m"
C="\033[0;36m"; B="\033[1;34m"; M="\033[0;35m"
BOLD="\033[1m"; DIM="\033[2m"; RESET="\033[0m"

def banner():
    print(f"""
{C}╔══════════════════════════════════════════════════════╗
║  {BOLD}⚡ Oracle DBA AI Agent{RESET}{C}                              ║
║  {DIM}Powered by Claude · Single-click automation{RESET}{C}          ║
╚══════════════════════════════════════════════════════╝{RESET}
  SID: {BOLD}{ORACLE_SID}{RESET}  |  Oracle Home: {DIM}{ORACLE_HOME}{RESET}
""")

def log(msg, level="INFO"):
    ts  = datetime.now().strftime("%H:%M:%S")
    colours = {"INFO": C, "OK": G, "WARN": Y, "ERROR": R, "AI": M, "STEP": B}
    col = colours.get(level, RESET)
    icons = {"INFO":"·","OK":"✔","WARN":"⚠","ERROR":"✘","AI":"🤖","STEP":"▶"}
    icon = icons.get(level,"·")
    line = f"[{ts}] {icon}  {msg}"
    print(f"{col}{line}{RESET}")
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def spinner(stop_event, msg="Running"):
    chars = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
    i = 0
    while not stop_event.is_set():
        print(f"\r{Y}{chars[i % len(chars)]}  {msg}...{RESET}", end="", flush=True)
        time.sleep(0.1); i += 1
    print("\r" + " " * (len(msg)+10) + "\r", end="")

# ── Shell command runner — captures full output ────────────────────
def run_shell(cmd, timeout=3600, stream=True):
    """Run a shell command. Returns (returncode, full_output)."""
    log(f"$ {cmd}", "STEP")
    output_lines = []
    try:
        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
            env=os.environ.copy()
        )
        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip()
            output_lines.append(line)
            if stream:
                print(f"  {DIM}{line}{RESET}")
            with open(LOG_FILE, "a") as f:
                f.write(line + "\n")
        proc.wait(timeout=timeout)
        return proc.returncode, "\n".join(output_lines)
    except subprocess.TimeoutExpired:
        proc.kill()
        return 1, "\n".join(output_lines) + "\nTIMEOUT"
    except Exception as e:
        return 1, str(e)

# ── SQL runner ────────────────────────────────────────────────────
def run_sql(sql, label=""):
    cmd = f'echo "{sql}\nEXIT;" | sqlplus -S / as sysdba'
    rc, out = run_shell(cmd, stream=False)
    return out

# ── AI analysis — streams response ────────────────────────────────
def ai_analyse(context, output, task="analyse"):
    """Send output to Claude for analysis. Returns AI summary string."""
    system_prompt = textwrap.dedent(f"""
        You are an expert Oracle DBA AI assistant monitoring a live Oracle database
        server. SID={ORACLE_SID}, ORACLE_HOME={ORACLE_HOME}.
        Be concise, direct, and action-oriented. Use plain text, no markdown.
        Flag any errors, warnings, or anomalies clearly.
        If everything is fine, say so in one line.
    """).strip()

    user_prompt = f"""
Task: {task}
Context: {context}
Command output (last 6000 chars):
{output[-6000:]}

Analyse this output and tell me:
1. Did the operation succeed or fail?
2. Any warnings or issues to be aware of?
3. Recommended next action (if any).
Keep your response under 15 lines.
"""
    print(f"\n{M}🤖  AI Analysis:{RESET}")
    print(f"{DIM}{'─'*52}{RESET}")

    full_response = ""
    with client.messages.stream(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        for text in stream.text_stream:
            print(f"{M}{text}{RESET}", end="", flush=True)
            full_response += text

    print(f"\n{DIM}{'─'*52}{RESET}\n")
    with open(LOG_FILE, "a") as f:
        f.write(f"\n[AI ANALYSIS]\n{full_response}\n")
    return full_response

# ── AI decision: should we continue or abort? ─────────────────────
def ai_should_continue(context, output):
    """Ask Claude whether it's safe to continue after this step."""
    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=60,
        messages=[{
            "role": "user",
            "content": (
                f"Oracle DBA task: {context}\n"
                f"Last output:\n{output[-3000:]}\n\n"
                "Reply with exactly one word: CONTINUE or ABORT. "
                "ABORT only if there is a definite error that would corrupt the DB."
            )
        }]
    )
    decision = resp.content[0].text.strip().upper()
    return "ABORT" not in decision

# ══════════════════════════════════════════════════════════════════
# ACTIONS
# ══════════════════════════════════════════════════════════════════

def action_status():
    """Full DB health check — AI interprets all results."""
    log("Running full DB health check...", "INFO")

    checks = [
        ("Instance status",     "SELECT instance_name, status, database_status FROM v$instance;"),
        ("DB version",          "SELECT version FROM v$instance;"),
        ("Invalid objects",     "SELECT COUNT(*) invalid FROM dba_objects WHERE status='INVALID';"),
        ("Tablespace usage",    "SELECT tablespace_name, ROUND(used_space*8192/1073741824,2) used_gb, ROUND(tablespace_size*8192/1073741824,2) total_gb, ROUND(used_percent,1) pct FROM dba_tablespace_usage_metrics ORDER BY used_percent DESC;"),
        ("Active sessions",     "SELECT status, COUNT(*) cnt FROM v$session WHERE type='USER' GROUP BY status;"),
        ("Blocking locks",      "SELECT COUNT(*) blocks FROM v$session WHERE blocking_session IS NOT NULL;"),
        ("Redo log status",     "SELECT group#, status, bytes/1048576 mb FROM v$log;"),
        ("Last RMAN backup",    "SELECT input_type, status, TO_CHAR(start_time,'DD-MON-YYYY HH24:MI') start_t FROM v$rman_backup_job_details WHERE start_time > SYSDATE-7 ORDER BY start_time DESC FETCH FIRST 3 ROWS ONLY;"),
        ("OPatch version",      None),
    ]

    all_output = []
    for label, sql in checks:
        log(f"Checking: {label}", "INFO")
        if sql:
            out = run_sql(sql, label)
        else:
            _, out = run_shell(f"{ORACLE_HOME}/OPatch/opatch version", stream=False)
        all_output.append(f"\n── {label} ──\n{out}")
        print(f"{DIM}{out[:400]}{RESET}")

    combined = "\n".join(all_output)
    ai_analyse("Full Oracle DB health check", combined,
               "Summarise the health of this Oracle database. Flag anything needing attention.")

def action_patch():
    """Run the existing patch shell script with AI monitoring at each stage."""
    log("Starting AI-monitored Oracle patch automation", "INFO")

    if not Path(PATCH_SCRIPT).exists():
        log(f"Patch script not found: {PATCH_SCRIPT}", "WARN")
        log("Using inline patch sequence instead...", "INFO")
        _run_patch_inline()
        return

    # Run the patch script — stream output in real time
    rc, output = run_shell(f"bash {PATCH_SCRIPT}", timeout=7200)

    if rc == 0:
        log("Patch script completed successfully", "OK")
    else:
        log(f"Patch script exited with code {rc}", "ERROR")

    ai_analyse(
        f"Oracle PSU patch application using {PATCH_SCRIPT}",
        output,
        "Did the patch apply successfully? Any errors or rollbacks? What should the DBA do next?"
    )

def _run_patch_inline():
    """Fallback: run patch steps individually with AI gate between each step."""
    steps = [
        ("Preflight: OPatch version",   f"{ORACLE_HOME}/OPatch/opatch version"),
        ("Preflight: disk space",        f"df -h {ORACLE_HOME}"),
        ("Conflict check",               f"{ORACLE_HOME}/OPatch/opatch prereq CheckConflictAgainstOHWithDetail -ph /u01/stage/APR_2026/39062931 2>&1 | tail -20"),
        ("DB instance status",           'echo "SELECT status FROM v\\$instance; EXIT;" | sqlplus -S / as sysdba'),
        ("Listener status",              "lsnrctl status"),
    ]
    for label, cmd in steps:
        log(f"Step: {label}", "STEP")
        rc, out = run_shell(cmd)
        if not ai_should_continue(label, out):
            log(f"AI flagged ABORT at step: {label}", "ERROR")
            ai_analyse(label, out, "Why did this step fail and what should the DBA do?")
            sys.exit(1)
        log(f"AI cleared step: {label}", "OK")

def action_start():
    """Start database and listener with AI verification."""
    log("Starting Oracle DB and listener...", "INFO")

    rc1, out1 = run_shell('echo "STARTUP; EXIT;" | sqlplus -S / as sysdba')
    rc2, out2 = run_shell("lsnrctl start")

    time.sleep(5)
    verify = run_sql("SELECT status FROM v\\$instance;")
    combined = f"Startup output:\n{out1}\n\nListener:\n{out2}\n\nVerification:\n{verify}"

    ai_analyse("Oracle DB startup", combined, "Did the database start successfully? Is the listener up?")

def action_stop():
    """Stop database and listener with AI verification."""
    log("Stopping Oracle DB and listener...", "WARN")
    print(f"\n{Y}⚠  This will shut down the database. Active users will be disconnected.{RESET}")
    confirm = input(f"{BOLD}Type 'yes' to confirm SHUTDOWN IMMEDIATE: {RESET}").strip()
    if confirm.lower() != "yes":
        log("Shutdown cancelled by user.", "INFO"); return

    rc1, out1 = run_shell("lsnrctl stop")
    rc2, out2 = run_shell('echo "SHUTDOWN IMMEDIATE; EXIT;" | sqlplus -S / as sysdba')
    combined = f"Listener stop:\n{out1}\n\nDB shutdown:\n{out2}"
    ai_analyse("Oracle DB shutdown", combined, "Did the database shut down cleanly?")

def action_backup():
    """Run RMAN backup with AI monitoring."""
    log("Starting RMAN backup...", "INFO")
    rman_script = textwrap.dedent(f"""
        rman target / <<'EOF'
        RUN {{
          BACKUP AS COMPRESSED BACKUPSET DATABASE TAG 'AI_AGENT_BACKUP' PLUS ARCHIVELOG;
          BACKUP CURRENT CONTROLFILE TAG 'AI_AGENT_CTL';
          DELETE NOPROMPT OBSOLETE;
        }}
        LIST BACKUP SUMMARY;
        EXIT;
        EOF
    """).strip()
    rc, output = run_shell(rman_script, timeout=14400)
    ai_analyse("RMAN database backup", output,
               "Did the RMAN backup complete successfully? Any failures or warnings?")

def action_space():
    """Tablespace space check — AI flags issues."""
    log("Checking tablespace space...", "INFO")
    sql = textwrap.dedent("""
        SET LINESIZE 120 PAGESIZE 50
        COL tablespace_name FOR A30
        SELECT
          tablespace_name,
          ROUND(used_space*8192/1073741824,2)       used_gb,
          ROUND(tablespace_size*8192/1073741824,2)  total_gb,
          ROUND(used_percent,1)                     pct_used,
          CASE WHEN used_percent > 90 THEN '*** CRITICAL ***'
               WHEN used_percent > 80 THEN '** WARNING **'
               ELSE 'OK' END                        status
        FROM dba_tablespace_usage_metrics
        ORDER BY used_percent DESC;
    """).strip()
    output = run_sql(sql, "Tablespace space")
    ai_analyse("Tablespace space utilisation", output,
               "Which tablespaces need attention? Recommend specific actions for any over 80% full.")

def action_runbook(task_description):
    """AI generates a shell command sequence for a custom task and executes it."""
    log(f"AI Runbook mode: {task_description}", "AI")

    # Ask Claude what commands to run
    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        messages=[{
            "role": "user",
            "content": textwrap.dedent(f"""
                You are an Oracle DBA agent. The DBA wants you to: {task_description}
                Oracle SID: {ORACLE_SID}, ORACLE_HOME: {ORACLE_HOME}

                Return ONLY a JSON array of steps. Each step:
                {{"label": "short description", "cmd": "exact shell command to run", "sql": null}}
                OR for SQL:
                {{"label": "short description", "cmd": null, "sql": "SELECT ... FROM ...;"}}

                Maximum 8 steps. No markdown. Pure JSON array only.
            """).strip()
        }]
    )

    raw = resp.content[0].text.strip()
    # Strip any accidental markdown fences
    raw = raw.replace("```json","").replace("```","").strip()

    try:
        steps = json.loads(raw)
    except json.JSONDecodeError:
        log("AI returned invalid JSON — running as a single shell command", "WARN")
        steps = [{"label": task_description, "cmd": task_description, "sql": None}]

    log(f"AI planned {len(steps)} steps for: {task_description}", "AI")
    all_output = []

    for i, step in enumerate(steps, 1):
        label = step.get("label", f"Step {i}")
        log(f"[{i}/{len(steps)}] {label}", "STEP")

        if step.get("sql"):
            out = run_sql(step["sql"], label)
            rc  = 0
        elif step.get("cmd"):
            rc, out = run_shell(step["cmd"])
        else:
            continue

        all_output.append(f"── {label} ──\n{out}")

        if rc != 0 and not ai_should_continue(label, out):
            log("AI recommends stopping — check output above", "ERROR")
            ai_analyse(label, out, "What went wrong and how should the DBA fix it?")
            return

    combined = "\n\n".join(all_output)
    ai_analyse(task_description, combined, "Did the runbook complete successfully? Any next steps?")

# ══════════════════════════════════════════════════════════════════
# INTERACTIVE MENU
# ══════════════════════════════════════════════════════════════════

MENU_ITEMS = [
    ("1", "DB Status & Health Check",    lambda: action_status()),
    ("2", "Apply Patch (oracle_auto_patch.sh)", lambda: action_patch()),
    ("3", "Start DB + Listener",         lambda: action_start()),
    ("4", "Stop DB + Listener",          lambda: action_stop()),
    ("5", "RMAN Backup",                 lambda: action_backup()),
    ("6", "Tablespace Space Check",      lambda: action_space()),
    ("7", "Custom AI Runbook",           None),
    ("q", "Quit",                        None),
]

def show_menu():
    print(f"\n{BOLD}  Available actions:{RESET}")
    for key, label, _ in MENU_ITEMS:
        colour = R if key == "q" else (Y if "Patch" in label or "Stop" in label else G)
        print(f"  {colour}[{key}]{RESET}  {label}")
    return input(f"\n{BOLD}Enter action: {RESET}").strip().lower()

def interactive_menu():
    while True:
        choice = show_menu()
        actions = {key: fn for key, _, fn in MENU_ITEMS}

        if choice == "q":
            log("Agent exiting. Log saved: " + LOG_FILE, "OK"); break
        elif choice == "7":
            task = input(f"{BOLD}Describe the task: {RESET}").strip()
            if task: action_runbook(task)
        elif choice in actions and actions[choice]:
            try:
                actions[choice]()
            except KeyboardInterrupt:
                log("Action interrupted by user.", "WARN")
            except Exception as e:
                log(f"Unexpected error: {e}", "ERROR")
        else:
            log("Invalid choice.", "WARN")

# ══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════

def main():
    banner()

    if not ANTHROPIC_API_KEY:
        print(f"{R}ERROR: ANTHROPIC_API_KEY environment variable not set.{RESET}")
        print(f"  export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)

    log(f"Agent started | log: {LOG_FILE}", "INFO")

    # CLI argument dispatch
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "menu"

    dispatch = {
        "status":  action_status,
        "patch":   action_patch,
        "start":   action_start,
        "stop":    action_stop,
        "backup":  action_backup,
        "space":   action_space,
        "menu":    interactive_menu,
    }

    if arg == "runbook":
        task = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        if not task:
            task = input(f"{BOLD}Describe the task: {RESET}").strip()
        action_runbook(task)
    elif arg in dispatch:
        try:
            dispatch[arg]()
        except KeyboardInterrupt:
            log("Interrupted by user.", "WARN")
    else:
        print(f"{R}Unknown action: {arg}{RESET}")
        print(__doc__)
        sys.exit(1)

    log(f"Done. Full log: {LOG_FILE}", "OK")

if __name__ == "__main__":
    main()
