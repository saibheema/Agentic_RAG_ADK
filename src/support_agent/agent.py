"""Support Agent — Agentic RAG issue triage, code investigation, and auto-fix.

Flow:
  User reports issue
    → Classify: bug | enhancement | usage_question | ambiguous
        bug + confidence=100  → investigate → branch → commit → PR → merge → CI/CD deploys
        bug + confidence<100  → investigate → branch → commit → PR → admin review
        enhancement/ambiguous → GitHub issue created → admin notified
        usage_question        → answer directly from codebase knowledge
"""

from __future__ import annotations

import base64
import datetime
import logging
import os
import re

from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.adk.tools import FunctionTool
from google.genai import types

_log = logging.getLogger(__name__)

_REPO_OWNER = "saibheema"
_REPO_NAME = "Agentic_RAG_ADK"
_DEFAULT_BRANCH = "main"
_GH_PAT_SECRET = os.environ.get(
    "GITHUB_PAT_SECRET",
    "projects/unicon-494419/secrets/github-support-agent-pat/versions/latest",
)

_token_cache: str = ""


def _get_github_token() -> str:
    global _token_cache
    if _token_cache:
        return _token_cache
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        resp = client.access_secret_version(request={"name": _GH_PAT_SECRET})
        _token_cache = resp.payload.data.decode("utf-8").strip()
        return _token_cache
    except Exception as exc:
        _log.warning("Secret Manager lookup failed (%s), falling back to GITHUB_PAT env var", exc)
    return os.environ.get("GITHUB_PAT", "")


def _gh(method: str, path: str, **kwargs) -> dict:
    """Make an authenticated GitHub REST API call. Returns parsed JSON or error dict."""
    import requests

    token = _get_github_token()
    if not token:
        return {"ok": False, "error": "GitHub token not configured. Set GITHUB_PAT env var or GITHUB_PAT_SECRET."}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        r = requests.request(
            method,
            f"https://api.github.com{path}",
            headers=headers,
            timeout=30,
            **kwargs,
        )
    except Exception as exc:
        return {"ok": False, "error": f"Request failed: {exc}"}
    if not r.ok:
        return {"ok": False, "status": r.status_code, "error": r.text[:500]}
    if not r.content:
        return {"ok": True}
    try:
        return r.json()
    except Exception:
        return {"ok": True, "raw": r.text[:500]}


# ── Tool: read a file from repo ──────────────────────────────────────────────


def read_repo_file(path: str, ref: str = "main") -> dict:
    """Read a file's full content from the GitHub repository.

    Args:
        path: File path relative to repo root, e.g. 'src/agentic_rag/agent.py'
        ref: Branch, tag, or commit SHA to read from (default: 'main')

    Returns dict with: content (decoded text), sha, path, size. Or error.
    """
    result = _gh("GET", f"/repos/{_REPO_OWNER}/{_REPO_NAME}/contents/{path}?ref={ref}")
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    if isinstance(result, dict) and result.get("type") == "file":
        try:
            decoded = base64.b64decode(
                result["content"].replace("\n", "")
            ).decode("utf-8")
            return {
                "ok": True,
                "path": result["path"],
                "sha": result["sha"],
                "size": result.get("size", 0),
                "content": decoded,
            }
        except Exception as exc:
            return {"ok": False, "error": f"Failed to decode content: {exc}"}
    if isinstance(result, list):
        return {"ok": False, "error": f"'{path}' is a directory, not a file. Use list_repo_directory instead."}
    return {"ok": False, "error": f"Unexpected response: {str(result)[:300]}"}


# ── Tool: list a directory in repo ───────────────────────────────────────────


def list_repo_directory(path: str = "", ref: str = "main") -> dict:
    """List files and subdirectories at a path in the repository.

    Args:
        path: Directory path relative to repo root (empty string = root)
        ref: Branch or commit ref
    """
    result = _gh("GET", f"/repos/{_REPO_OWNER}/{_REPO_NAME}/contents/{path}?ref={ref}")
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    if isinstance(result, list):
        items = [
            {"name": i["name"], "type": i["type"], "path": i["path"]}
            for i in result
        ]
        return {"ok": True, "path": path or "/", "items": items}
    return {"ok": False, "error": f"Not a directory or unexpected response: {str(result)[:200]}"}


# ── Tool: search repo code ───────────────────────────────────────────────────


def search_repo_code(query: str) -> dict:
    """Search for code patterns across all files in the repository.

    Args:
        query: Search terms, e.g. 'year filter date' or 'YEAR(today) agent'

    Returns matching file paths. Read the files with read_repo_file for details.
    """
    import requests as _req

    token = _get_github_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    full_query = f"{query} repo:{_REPO_OWNER}/{_REPO_NAME}"
    try:
        r = _req.get(
            "https://api.github.com/search/code",
            headers=headers,
            params={"q": full_query, "per_page": 10},
            timeout=30,
        )
        if not r.ok:
            return {"ok": False, "error": f"Search failed {r.status_code}: {r.text[:300]}"}
        data = r.json()
        results = [
            {"path": item["path"], "name": item["name"], "url": item["html_url"]}
            for item in data.get("items", [])
        ]
        return {"ok": True, "total_matches": data.get("total_count", 0), "files": results}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Tool: create fix branch ──────────────────────────────────────────────────


def create_fix_branch(issue_slug: str) -> dict:
    """Create a new branch off main for a bug fix.

    Args:
        issue_slug: Short kebab-case description of the fix,
                    e.g. 'year-filter-wrong-default'

    Returns: branch_name to use in subsequent commit/PR calls.
    """
    date_str = datetime.date.today().strftime("%Y%m%d")
    slug = re.sub(r"[^a-z0-9-]", "-", issue_slug.lower())[:50].strip("-")
    branch_name = f"support/fix-{slug}-{date_str}"

    ref_data = _gh(
        "GET",
        f"/repos/{_REPO_OWNER}/{_REPO_NAME}/git/ref/heads/{_DEFAULT_BRANCH}",
    )
    if not isinstance(ref_data, dict) or "object" not in ref_data:
        return {"ok": False, "error": f"Could not get main branch SHA: {str(ref_data)[:200]}"}
    main_sha = ref_data["object"]["sha"]

    result = _gh(
        "POST",
        f"/repos/{_REPO_OWNER}/{_REPO_NAME}/git/refs",
        json={"ref": f"refs/heads/{branch_name}", "sha": main_sha},
    )
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    return {"ok": True, "branch_name": branch_name, "base_sha": main_sha}


# ── Tool: commit file fix ────────────────────────────────────────────────────


def commit_file_fix(
    branch: str,
    path: str,
    new_content: str,
    commit_message: str,
) -> dict:
    """Commit a complete file replacement to a branch.

    IMPORTANT: new_content must be the COMPLETE file content, not a diff.

    Args:
        branch: Target branch (must exist — call create_fix_branch first)
        path: File path relative to repo root, e.g. 'src/agentic_rag/agent.py'
        new_content: The entire new file content (replaces the existing file)
        commit_message: Convention: 'fix: <short description>'
    """
    # Get current file SHA from the branch (needed for update); fall back to main for new branches
    file_data = _gh("GET", f"/repos/{_REPO_OWNER}/{_REPO_NAME}/contents/{path}?ref={branch}")
    if isinstance(file_data, dict) and file_data.get("ok") is False:
        file_data = _gh("GET", f"/repos/{_REPO_OWNER}/{_REPO_NAME}/contents/{path}?ref=main")
    if not isinstance(file_data, dict) or "sha" not in file_data:
        return {"ok": False, "error": f"Could not get current SHA for {path}: {str(file_data)[:300]}"}
    current_sha = file_data["sha"]

    encoded = base64.b64encode(new_content.encode("utf-8")).decode("ascii")

    result = _gh(
        "PUT",
        f"/repos/{_REPO_OWNER}/{_REPO_NAME}/contents/{path}",
        json={
            "message": commit_message,
            "content": encoded,
            "sha": current_sha,
            "branch": branch,
        },
    )
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    commit_info = result.get("commit", {})
    return {
        "ok": True,
        "path": path,
        "branch": branch,
        "commit_sha": commit_info.get("sha", ""),
    }


# ── Tool: open pull request ──────────────────────────────────────────────────


def open_pull_request(
    branch: str,
    title: str,
    body: str,
    confidence: int = 100,
) -> dict:
    """Open a pull request from a fix branch into main.

    Args:
        branch: The fix branch created by create_fix_branch
        title: PR title — must start with 'fix: ' for bugs
        body: Detailed description including: user complaint, root cause,
              files changed, lines affected, and confidence score
        confidence: 0–100. 100 = auto-fix (agent will merge). <100 = admin review.

    Returns: pr_number and pr_url to share with the user.
    """
    result = _gh(
        "POST",
        f"/repos/{_REPO_OWNER}/{_REPO_NAME}/pulls",
        json={
            "title": title,
            "body": body,
            "head": branch,
            "base": _DEFAULT_BRANCH,
        },
    )
    if isinstance(result, dict) and result.get("ok") is False:
        return result

    pr_number = result.get("number")
    pr_url = result.get("html_url", "")

    label = "support/auto-fix" if confidence >= 100 else "support/needs-review"
    _gh(
        "POST",
        f"/repos/{_REPO_OWNER}/{_REPO_NAME}/issues/{pr_number}/labels",
        json={"labels": [label]},
    )

    return {
        "ok": True,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "label": label,
        "confidence": confidence,
    }


# ── Tool: request Copilot code review ────────────────────────────────────────


def request_copilot_review(pr_number: int) -> dict:
    """Request a GitHub Copilot code review on a pull request.

    Args:
        pr_number: PR number returned by open_pull_request
    """
    result = _gh(
        "POST",
        f"/repos/{_REPO_OWNER}/{_REPO_NAME}/pulls/{pr_number}/requested_reviewers",
        json={"reviewers": ["copilot"]},
    )
    if isinstance(result, dict) and result.get("ok") is False:
        _log.warning("Copilot review request failed for PR #%s: %s", pr_number, result)
        return {"ok": True, "note": "Review requested (Copilot availability depends on repo settings)"}
    return {"ok": True, "pr_number": pr_number, "reviewer": "copilot"}


# ── Tool: merge pull request ─────────────────────────────────────────────────


def merge_pull_request(pr_number: int, merge_commit_message: str = "") -> dict:
    """Squash-merge a pull request into main. ONLY call this when confidence == 100.

    Merging triggers the CI/CD pipeline which builds and redeploys to Cloud Run
    automatically (~3-5 minutes to go live).

    Args:
        pr_number: PR number to merge
        merge_commit_message: Optional message for the squash commit
    """
    msg = merge_commit_message or f"fix: auto-fix by support agent (PR #{pr_number})"
    result = _gh(
        "PUT",
        f"/repos/{_REPO_OWNER}/{_REPO_NAME}/pulls/{pr_number}/merge",
        json={
            "merge_method": "squash",
            "commit_message": msg,
        },
    )
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    return {
        "ok": True,
        "pr_number": pr_number,
        "merged": True,
        "sha": result.get("sha", ""),
        "deployment_note": "CI/CD pipeline triggered — fix will be live in approximately 3-5 minutes.",
    }


# ── Tool: create GitHub issue (enhancements / ambiguous) ─────────────────────


def list_open_issues(search: str = "") -> dict:
    """List open GitHub issues in the repository, optionally filtered by a search term.

    Args:
        search: Optional keyword(s) to filter issues by title/body similarity.
                Leave empty to return the 30 most recent open issues.

    Returns a list of issues with number, title, labels, and URL.
    """
    params: dict = {"state": "open", "per_page": 50}
    result = _gh("GET", f"/repos/{_REPO_OWNER}/{_REPO_NAME}/issues", params=params)
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    # GitHub's /issues endpoint also returns PRs — exclude them
    issues = [i for i in result if not i.get("pull_request")]
    if search:
        kw = search.lower()
        issues = [
            i for i in issues
            if kw in i.get("title", "").lower() or kw in (i.get("body") or "").lower()
        ]
    return {
        "ok": True,
        "total": len(issues),
        "issues": [
            {
                "number": i["number"],
                "title": i["title"],
                "state": i["state"],
                "labels": [lb["name"] for lb in i.get("labels", [])],
                "url": i["html_url"],
                "created_at": i["created_at"],
            }
            for i in issues
        ],
    }


def list_open_pull_requests(search: str = "") -> dict:
    """List open pull requests in the repository, optionally filtered by a search term.

    Args:
        search: Optional keyword(s) to filter PRs by title/body similarity.
                Leave empty to return the 30 most recent open PRs.

    Returns a list of PRs with number, title, branch, labels, and URL.
    """
    params: dict = {"state": "open", "per_page": 50}
    result = _gh("GET", f"/repos/{_REPO_OWNER}/{_REPO_NAME}/pulls", params=params)
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    prs = result if isinstance(result, list) else []
    if search:
        kw = search.lower()
        prs = [
            p for p in prs
            if kw in p.get("title", "").lower() or kw in (p.get("body") or "").lower()
        ]
    return {
        "ok": True,
        "total": len(prs),
        "pull_requests": [
            {
                "number": p["number"],
                "title": p["title"],
                "branch": p["head"]["ref"],
                "state": p["state"],
                "labels": [lb["name"] for lb in p.get("labels", [])],
                "url": p["html_url"],
                "created_at": p["created_at"],
            }
            for p in prs
        ],
    }


def create_github_issue(
    title: str,
    body: str,
    labels: str = "support/enhancement",
) -> dict:
    """Create a GitHub issue for enhancements or cases needing admin review.

    Args:
        title: Concise issue title
        body: Full description including the user's original complaint verbatim,
              your analysis, and recommended next steps
        labels: Comma-separated label names to apply (default: 'support/enhancement')
    """
    issue_labels = [l.strip() for l in labels.split(",") if l.strip()] or ["support/enhancement"]
    result = _gh(
        "POST",
        f"/repos/{_REPO_OWNER}/{_REPO_NAME}/issues",
        json={"title": title, "body": body, "labels": issue_labels},
    )
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    return {
        "ok": True,
        "issue_number": result.get("number"),
        "issue_url": result.get("html_url", ""),
        "title": title,
    }


# ── Agent ────────────────────────────────────────────────────────────────────

_model = os.environ.get("AGENT_MODEL", "gemini-2.5-flash-lite")

root_agent = LlmAgent(
    name="support_agent",
    model=_model,
    planner=BuiltInPlanner(
        thinking_config=types.ThinkingConfig(thinking_budget=8192)
    ),
    generate_content_config=types.GenerateContentConfig(max_output_tokens=4096),
    description="Support agent: triages user-reported issues, investigates code, and auto-fixes confirmed bugs via GitHub PR.",
    instruction="""
You are the Support Agent for the Agentic RAG application (GitHub repo: saibheema/Agentic_RAG_ADK).

Users report problems they experience. Your job: investigate, classify, and resolve.

---
## STEP 0 — CHECK FOR DUPLICATES FIRST

Before doing ANYTHING else, call `list_open_issues` and `list_open_pull_requests`
with keywords from the user's message to check for existing work.

- If an **open issue** already covers this exact problem → tell the user:
  "Thank you for reaching out! We already have this on our radar — it has been previously logged
  with reference **#N**. Our team is aware and working on it. You can quote **#N** for any follow-ups."
  Then stop — do NOT create another issue or PR.
- If an **open PR** already addresses this exact problem → tell the user:
  "Good news — our team is already working on a fix for this. Reference **#N** is in progress
  and should be available soon. No further action is needed on your end."
  Then stop.
- If existing work is **related but not identical** → mention it, then continue with the normal flow
  and cross-reference the existing issue/PR in any new issue/PR body.

---
## STEP 1 — CLASSIFY

Read the user's message carefully. Classify as ONE of:
- **bug**: The code produces wrong output (wrong SQL, wrong date filter, wrong result, error/crash)
- **enhancement**: User wants NEW behaviour or a feature that doesn't exist yet
- **usage_question**: User is confused about how to use an existing feature
- **ambiguous**: Insufficient detail — ask one clarifying question, then re-classify

---
## STEP 2A — usage_question

Answer directly. You may call `read_repo_file` and `list_repo_directory` to give accurate,
code-backed answers. Do NOT create any PRs or GitHub issues.

---
## STEP 2B — enhancement OR ambiguous

1. Explain clearly why this is an enhancement (not a bug), or what information is missing.
2. Call `create_github_issue` with:
   - Title: concise summary
   - Body: user's exact complaint, your analysis, why admin review is needed.
     If Step 0 found related issues/PRs, cross-reference them here.
   - Labels: ["support/enhancement"] for enhancements; ["support/needs-clarification"] for ambiguous
3. Tell the user a support-style message. Do NOT mention GitHub, issues, or PRs.
   Use this template (fill in the reference number):
   "Thank you for reaching out! We've acknowledged your request and it has been assigned reference number **#N**.
   Our project management team will review it and get back to you. You can quote **#N** for any follow-ups."

---
## STEP 2C — bug

Follow ALL sub-steps in order. Do NOT skip investigation.

### 2C-1. Investigate the code
- Call `list_repo_directory` on `src/agentic_rag/` to understand the structure
- Call `read_repo_file` for the most likely relevant files (always start with `src/agentic_rag/agent.py`)
- Call `search_repo_code` with keywords from the bug report to find relevant code sections
- Read every file that could be related BEFORE forming conclusions

### 2C-2. Assign a confidence score (0–100)
- **100** = You can see the EXACT bug in the code, AND the fix is simple and safe (no risk of regressions)
- **70–99** = You found a likely bug but the fix is complex or could affect other parts
- **1–69** = Possible bug, but you cannot pinpoint the exact cause in the code
- **0** = This is NOT a code bug — the behaviour is correct

### 2C-3. confidence == 0 (not a bug)
Explain why the application is behaving correctly. Guide the user on the correct way to use it.

### 2C-4. confidence == 100 → AUTO-FIX PATH
1. `create_fix_branch(issue_slug)` — use a descriptive slug like 'year-filter-uses-wrong-default'
2. `commit_file_fix(branch, path, new_content, commit_message)` — provide the COMPLETE new file
   content (not a diff). Commit message must start with 'fix: '
3. `open_pull_request(branch, title, body, confidence=100)` —  body MUST include:
   - **User report**: the user's original complaint verbatim
   - **Root cause**: exact file, function/line, and what was wrong
   - **Fix**: what was changed and why it's safe
   - **Confidence**: 100% — auto-fix approved by Support Agent
4. `request_copilot_review(pr_number)`
5. `merge_pull_request(pr_number)` — this triggers CI/CD (~3-5 min to deploy)
6. Reply with a support-style message. Do NOT mention GitHub, PRs, or branches.
   Use this template:
   "✅ Great news! We've identified and resolved the issue. A fix has been applied and is currently
   deploying — it should be live in approximately 5 minutes. Your reference number is **#N**.
   Please reach out if you continue to experience this problem."

### 2C-5. confidence < 100 → ADMIN-REVIEW PATH
1. `create_fix_branch(issue_slug)`
2. `commit_file_fix(...)` — commit your best partial fix or analysis notes as a code comment
3. `open_pull_request(branch, title, body, confidence=<your score>)` — explain limitation in body
4. `request_copilot_review(pr_number)`
5. Reply with a support-style message. Do NOT mention GitHub, PRs, branches, or confidence scores.
   Use this template:
   "Thank you for reporting this. We've investigated the issue and flagged it for our engineering
   team to review. Your reference number is **#N**. We'll keep you updated on progress and aim
   to have a fix available as soon as possible."

---
## GUARDRAILS — NEVER VIOLATE

- NEVER call `merge_pull_request` unless confidence == 100 AND classification == bug
- NEVER auto-merge for enhancements
- NEVER diagnose a bug without first reading the actual source code
- `commit_file_fix` requires the FULL file content — never pass a partial diff
- Never fabricate code or results — only state what you found in the repository
- If a tool returns an error, report it honestly to the user
- NEVER expose internal implementation details to the user: no GitHub URLs, branch names,
  PR numbers as "PR", commit SHAs, tool names, or technical jargon. Reference numbers only.

---
## TONE

Warm, professional, and concise — like a skilled human support agent.
Users are NOT technical. Avoid all developer terminology.
Always tell the user: what you understood, what action was taken, and what they can expect next.
Use the reference number (#N) so they can follow up easily.
""",
    tools=[
        FunctionTool(list_open_issues),
        FunctionTool(list_open_pull_requests),
        FunctionTool(read_repo_file),
        FunctionTool(list_repo_directory),
        FunctionTool(search_repo_code),
        FunctionTool(create_fix_branch),
        FunctionTool(commit_file_fix),
        FunctionTool(open_pull_request),
        FunctionTool(request_copilot_review),
        FunctionTool(merge_pull_request),
        FunctionTool(create_github_issue),
    ],
)
