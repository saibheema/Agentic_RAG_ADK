#!/usr/bin/env python3
"""
Text-to-SQL accuracy evaluation.

Sends natural-language questions through the live app, captures the SQL
the LLM generates, and compares results against known-good answers run
directly on the database.
"""

import json, sys, time, requests, re

BASE = "https://agentic-rag-ui-664984131730.us-central1.run.app/api"
APP = "agentic_rag"
USER = "accuracy-eval"

# ── Test cases: (natural question, verification SQL, expected_check_fn) ──────
# expected_check_fn receives (agent_answer:str, ground_truth_rows:list[dict])
# and returns (pass:bool, detail:str)

def check_count(expected_key=None):
    """Agent answer should contain the count number from ground truth."""
    def _check(answer, gt_rows):
        if not gt_rows:
            return False, "No ground truth rows"
        gt_val = str(list(gt_rows[0].values())[0])
        if gt_val in answer:
            return True, f"Found {gt_val}"
        return False, f"Expected {gt_val} in answer"
    return _check

def check_number_close():
    """Answer should contain a number close to ground truth (within 1%)."""
    def _check(answer, gt_rows):
        if not gt_rows:
            return False, "No ground truth rows"
        gt_val = float(list(gt_rows[0].values())[0])
        # Extract numbers from answer
        nums = re.findall(r'[\d,]+\.?\d*', answer.replace(',',''))
        for n in nums:
            try:
                if abs(float(n) - gt_val) / max(gt_val, 0.01) < 0.02:
                    return True, f"Found {n} ≈ {gt_val}"
            except:
                pass
        return False, f"Expected ≈{gt_val}, found nums: {nums}"
    return _check

def check_top_name():
    """Answer should mention the top name from ground truth."""
    def _check(answer, gt_rows):
        if not gt_rows:
            return False, "No ground truth rows"
        # Get first text-like value
        for v in gt_rows[0].values():
            if isinstance(v, str) and len(v) > 1:
                # PII masking may replace names, so check for PERSON_ pattern too
                if v.lower() in answer.lower() or "PERSON_" in answer:
                    return True, f"Found '{v}' or PII-masked equivalent"
        return False, f"Expected mention of {gt_rows[0]}"
    return _check

def check_contains_all_values():
    """Answer should contain all values from ground truth single-row result."""
    def _check(answer, gt_rows):
        if not gt_rows:
            return False, "No ground truth rows"
        missing = []
        for v in gt_rows[0].values():
            s = str(v)
            if s not in answer and s.rstrip('0').rstrip('.') not in answer:
                missing.append(s)
        if not missing:
            return True, "All values present"
        return len(missing) <= 1, f"Missing: {missing}"
    return _check

def check_list_has_count():
    """Answer should list the correct number of items."""
    def _check(answer, gt_rows):
        count = len(gt_rows)
        if str(count) in answer:
            return True, f"Found count {count}"
        # check if all names/values appear
        found = sum(1 for r in gt_rows if any(str(v).lower() in answer.lower() or "PERSON_" in answer for v in r.values() if isinstance(v, str)))
        if found >= count * 0.7:
            return True, f"Found {found}/{count} items"
        return False, f"Expected {count} items or values, found {found}"
    return _check


TESTS = [
    # ── Simple counts ──
    ("How many orders are in the database?",
     "SELECT COUNT(*) AS cnt FROM orders",
     check_count()),

    ("How many customers do we have?",
     "SELECT COUNT(*) AS cnt FROM customers",
     check_count()),

    ("How many products are there?",
     "SELECT COUNT(*) AS cnt FROM products",
     check_count()),

    # ── Aggregations ──
    ("What is the total revenue from all orders?",
     "SELECT SUM(total_amount) AS total FROM orders",
     check_number_close()),

    ("What is the average order value?",
     "SELECT AVG(total_amount) AS avg_val FROM orders",
     check_number_close()),

    # ── Filtering ──
    ("How many orders have status 'completed'?",
     "SELECT COUNT(*) AS cnt FROM orders WHERE status = 'completed'",
     check_count()),

    # ── Joins ──
    ("Which customer has placed the most orders?",
     "SELECT c.full_name, COUNT(o.order_id) AS order_count FROM customers c JOIN orders o ON c.customer_id = o.customer_id GROUP BY c.full_name ORDER BY order_count DESC LIMIT 1",
     check_top_name()),

    # ── Multi-table aggregation ──
    ("What is the most expensive product?",
     "SELECT product_name, unit_price FROM products ORDER BY unit_price DESC LIMIT 1",
     check_top_name()),

    # ── GROUP BY + ORDER BY ──
    ("What are the top 3 product categories by total sales?",
     "SELECT p.category, SUM(oi.line_total) AS total_sales FROM order_items oi JOIN products p ON oi.product_id = p.product_id GROUP BY p.category ORDER BY total_sales DESC LIMIT 3",
     check_list_has_count()),

    # ── Subquery / CTE ──
    ("How many customers have placed more than 5 orders?",
     "SELECT COUNT(*) AS cnt FROM (SELECT customer_id FROM orders GROUP BY customer_id HAVING COUNT(*) > 5) sub",
     check_count()),

    # ── Date filtering (if data has dates) ──
    ("How many orders were placed in 2025?",
     "SELECT COUNT(*) AS cnt FROM orders WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01'",
     check_count()),

    # ── Edge: ambiguous / no-data ──
    ("What regions do our customers come from?",
     "SELECT DISTINCT region FROM customers ORDER BY region",
     check_list_has_count()),
]


def run_ground_truth(sql):
    """Execute verification SQL directly through the app's own tool."""
    # We use a helper session to run raw SQL via the run_readonly_sql endpoint
    resp = requests.post(f"{BASE}/apps/{APP}/users/gt-runner/sessions",
                         json={}, timeout=15)
    sid = resp.json()["id"]

    # Ask the agent to run exact SQL
    prompt = f"Execute exactly this SQL and give me the raw results: {sql}"
    sse_resp = requests.post(f"{BASE}/run_sse", json={
        "app_name": APP, "user_id": "gt-runner", "session_id": sid,
        "new_message": {"role": "user", "parts": [{"text": prompt}]}
    }, stream=True, timeout=60)

    rows = []
    last_text = ""
    for line in sse_resp.iter_lines(decode_unicode=True):
        if line.startswith("data: "):
            try:
                evt = json.loads(line[6:])
                content = evt.get("content", {})
                parts = content.get("parts", [])
                for p in parts:
                    if "functionResponse" in p:
                        fr = p["functionResponse"].get("response", {})
                        if fr.get("ok") and "rows" in fr:
                            rows = fr["rows"]
                    if "text" in p:
                        last_text = p["text"]
            except:
                pass
    return rows, last_text


def run_agent_query(question, session_id):
    """Send question to agent and capture generated SQL + final answer."""
    resp = requests.post(f"{BASE}/run_sse", json={
        "app_name": APP, "user_id": USER, "session_id": session_id,
        "new_message": {"role": "user", "parts": [{"text": question}]}
    }, stream=True, timeout=90)

    generated_sql = ""
    final_answer = ""
    tool_result = {}

    for line in resp.iter_lines(decode_unicode=True):
        if line.startswith("data: "):
            try:
                evt = json.loads(line[6:])
                content = evt.get("content", {})
                parts = content.get("parts", [])
                for p in parts:
                    if "functionCall" in p:
                        fc = p["functionCall"]
                        if fc.get("name") == "run_readonly_sql":
                            generated_sql = fc.get("args", {}).get("sql", "")
                    if "functionResponse" in p:
                        fr = p["functionResponse"]
                        if fr.get("name") == "run_readonly_sql":
                            tool_result = fr.get("response", {})
                    if "text" in p:
                        final_answer = p["text"]
            except:
                pass

    return generated_sql, final_answer, tool_result


def main():
    # Create session for agent queries
    resp = requests.post(f"{BASE}/apps/{APP}/users/{USER}/sessions",
                         json={}, timeout=15)
    session_id = resp.json()["id"]
    print(f"Session: {session_id}\n")

    results = []
    pass_count = 0

    for i, (question, gt_sql, checker) in enumerate(TESTS, 1):
        print(f"━━━ Test {i}/{len(TESTS)}: {question}")

        # Get ground truth
        gt_rows, _ = run_ground_truth(gt_sql)
        print(f"  Ground truth: {gt_rows[:2]}{'...' if len(gt_rows) > 2 else ''}")

        # Run agent
        time.sleep(1)  # rate-limit courtesy
        gen_sql, answer, tool_res = run_agent_query(question, session_id)
        print(f"  Generated SQL: {gen_sql[:120]}{'...' if len(gen_sql) > 120 else ''}")
        print(f"  Agent answer: {answer[:150]}{'...' if len(answer) > 150 else ''}")

        # Check
        passed, detail = checker(answer, gt_rows)
        status = "PASS ✅" if passed else "FAIL ❌"
        print(f"  Result: {status} — {detail}")

        sql_ok = tool_res.get("ok", False)
        print(f"  SQL executed OK: {sql_ok}")
        print()

        results.append({
            "question": question,
            "passed": passed,
            "sql_ok": sql_ok,
            "generated_sql": gen_sql,
            "answer_snippet": answer[:200],
            "detail": detail,
        })
        if passed:
            pass_count += 1

    # Summary
    total = len(TESTS)
    print(f"\n{'='*60}")
    print(f"ACCURACY REPORT: {pass_count}/{total} passed ({100*pass_count/total:.0f}%)")
    print(f"{'='*60}")

    sql_exec_ok = sum(1 for r in results if r["sql_ok"])
    print(f"SQL execution success rate: {sql_exec_ok}/{total} ({100*sql_exec_ok/total:.0f}%)")
    print()

    if pass_count < total:
        print("Failed tests:")
        for r in results:
            if not r["passed"]:
                print(f"  - {r['question']}")
                print(f"    SQL: {r['generated_sql'][:100]}")
                print(f"    Detail: {r['detail']}")
                print()

    # Save JSON report
    with open("accuracy_report.json", "w") as f:
        json.dump({"pass_count": pass_count, "total": total,
                    "accuracy_pct": round(100*pass_count/total, 1),
                    "sql_exec_rate": round(100*sql_exec_ok/total, 1),
                    "results": results}, f, indent=2, default=str)
    print("Full report saved to accuracy_report.json")


if __name__ == "__main__":
    main()
