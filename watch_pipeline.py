import time
import psycopg2
from datetime import datetime

DB = "postgresql://postgres:12345@localhost:5432/recruitment_system_db"
LOG = "watch_pipeline.log"

def query(cur, sql):
    cur.execute(sql)
    return cur.fetchone()[0]

def snapshot(cur):
    jr_status = query(cur, "SELECT status FROM job_requisitions WHERE requisition_id=1")
    proc_status = query(cur, "SELECT processing_status FROM job_requisitions WHERE requisition_id=1")
    semantic = query(cur, "SELECT COUNT(*) FROM semantic_analysis_reports")
    shortlisted = query(cur, "SELECT COUNT(*) FROM shortlisted_candidates")
    final = query(cur, "SELECT COUNT(*) FROM final_rankings")
    assessments = query(cur, "SELECT COUNT(*) FROM candidate_assessments")
    return jr_status, proc_status, int(semantic), int(shortlisted), int(final), int(assessments)

prev = None
conn = psycopg2.connect(DB)
conn.autocommit = True
cur = conn.cursor()

with open(LOG, "w") as f:
    f.write(f"[{datetime.now().strftime('%H:%M:%S')}] Watching pipeline for JR #1...\n")
    f.flush()

    while True:
        try:
            state = snapshot(cur)
            jr_status, proc_status, semantic, shortlisted, final, assessments = state
            ts = datetime.now().strftime("%H:%M:%S")
            line = (f"[{ts}] jr={jr_status} | proc={proc_status} | "
                    f"semantic_reports={semantic} | shortlisted={shortlisted} | "
                    f"assessments={assessments} | final_rankings={final}")
            if state != prev:
                f.write(f"*** CHANGE *** {line}\n")
                f.flush()
                prev = state
            else:
                f.write(f"{line}\n")
                f.flush()
        except Exception as e:
            f.write(f"[ERROR] {e}\n")
            f.flush()
            try:
                conn = psycopg2.connect(DB)
                conn.autocommit = True
                cur = conn.cursor()
            except:
                pass
        time.sleep(15)
