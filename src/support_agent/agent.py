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


def bump_issue_request_count(issue_number: int, user_context: str = "") -> dict:
    """Increment the request counter on an existing enhancement GitHub issue.

    Call this when the current user's report matches an already-open enhancement issue.
    It avoids creating a duplicate and signals demand to the team by updating a visible
    counter in the issue body and adding a comment with the user's context.

    Args:
        issue_number: Existing open GitHub issue number to update.
        user_context: 1–2 sentence summary of THIS user's specific use-case / motivation.
                      Added as a comment on the issue; never shown to the end-user.
    """
    # Read the current issue
    issue = _gh("GET", f"/repos/{_REPO_OWNER}/{_REPO_NAME}/issues/{issue_number}")
    if isinstance(issue, dict) and issue.get("ok") is False:
        return issue

    current_body = issue.get("body") or ""

    # Parse the hidden counter marker (starts at 1 = the original reporter)
    count_match = re.search(r"<!-- requests:(\d+) -->", current_body)
    old_count = int(count_match.group(1)) if count_match else 1
    new_count = old_count + 1

    counter_section = (
        f"<!-- requests:{new_count} -->\n"
        f"**\U0001f465 User Requests: {new_count}** — {new_count} users have reported this request."
    )
    if count_match:
        updated_body = re.sub(
            r"<!-- requests:\d+ -->\n\*\*\U0001f465 User Requests: \d+\*\*[^\n]*",
            counter_section,
            current_body,
        )
    else:
        updated_body = current_body.rstrip() + "\n\n" + counter_section

    patch_result = _gh(
        "PATCH",
        f"/repos/{_REPO_OWNER}/{_REPO_NAME}/issues/{issue_number}",
        json={"body": updated_body},
    )
    if isinstance(patch_result, dict) and patch_result.get("ok") is False:
        return patch_result

    comment_body = (
        f"**\U0001f4e5 Duplicate request received** (total requests: {new_count})\n\n"
        + (f"User context: {user_context}" if user_context else "(no additional context)")
    )
    _gh(
        "POST",
        f"/repos/{_REPO_OWNER}/{_REPO_NAME}/issues/{issue_number}/comments",
        json={"body": comment_body},
    )

    return {
        "ok": True,
        "issue_number": issue_number,
        "new_count": new_count,
        "issue_url": issue.get("html_url", ""),
    }


# ── Tool: search GCP Cloud Logging ───────────────────────────────────────────

_GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "unicon-494419")
_CLOUD_RUN_SERVICE = os.environ.get("CLOUD_RUN_SERVICE", "agentic-rag")


def search_gcp_logs(
    keywords: str,
    hours_back: int = 24,
    severity: str = "WARNING",
    max_entries: int = 25,
) -> dict:
    """Search GCP Cloud Logging for entries related to a user-reported issue.

    ALWAYS call this when classifying an issue as a bug. Use keywords extracted
    directly from the user's complaint. Log evidence raises confidence and provides
    exact stack traces to include in PR/issue bodies.

    Args:
        keywords: Space-separated terms from the user's report, e.g. 'date filter year SQL'.
                  Pass empty string "" to fetch all recent errors with no keyword filter.
        hours_back: How many hours back to search (default 24, use 6 for "just now" issues)
        severity: Minimum severity to return. One of: DEBUG, INFO, WARNING, ERROR, CRITICAL
                  Default WARNING catches both warnings and errors.
        max_entries: Maximum log entries to return (default 25)

    Returns a summary of matching log entries, top recurring error messages, and
    raw entries (timestamp, severity, message, trace).
    """
    try:
        from google.cloud import logging as gcp_logging
    except ImportError:
        return {
            "ok": False,
            "error": "google-cloud-logging not installed. Run: pip install google-cloud-logging>=3.11.0",
        }

    _SEV_ORDER = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    sev_upper = severity.upper() if severity.upper() in _SEV_ORDER else "WARNING"
    sev_filter_parts = " OR ".join(
        f'severity="{s}"' for s in _SEV_ORDER[_SEV_ORDER.index(sev_upper):]
    )

    import datetime as _dt
    start_iso = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=hours_back)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build keyword sub-filter (up to 5 terms to avoid overly long filter strings)
    kw_list = [k.strip() for k in keywords.split() if k.strip()][:5]
    if kw_list:
        kw_clauses = " OR ".join(
            f'textPayload:"{k}" OR jsonPayload.message:"{k}"' for k in kw_list
        )
        log_filter = (
            f'resource.type="cloud_run_revision"'
            f' AND resource.labels.service_name="{_CLOUD_RUN_SERVICE}"'
            f' AND ({sev_filter_parts})'
            f' AND timestamp>="{start_iso}"'
            f' AND ({kw_clauses})'
        )
    else:
        log_filter = (
            f'resource.type="cloud_run_revision"'
            f' AND resource.labels.service_name="{_CLOUD_RUN_SERVICE}"'
            f' AND ({sev_filter_parts})'
            f' AND timestamp>="{start_iso}"'
        )

    try:
        client = gcp_logging.Client(project=_GCP_PROJECT_ID)
        raw_entries = list(
            client.list_entries(
                filter_=log_filter,
                max_results=max_entries,
                order_by=gcp_logging.DESCENDING,
            )
        )
    except Exception as exc:
        return {"ok": False, "error": f"Cloud Logging query failed: {exc}"}

    if not raw_entries:
        return {
            "ok": True,
            "found": 0,
            "time_range_hours": hours_back,
            "summary": (
                f"No {sev_upper}+ logs found in the last {hours_back}h"
                + (f" matching keywords: {keywords}" if keywords else "")
                + ". This may suggest the issue is intermittent or has not recurred recently."
            ),
            "entries": [],
        }

    entries = []
    for entry in raw_entries:
        payload = entry.payload
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("msg") or str(payload)
        else:
            message = str(payload) if payload else ""
        entries.append(
            {
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else "",
                "severity": entry.severity or "UNKNOWN",
                "message": message[:600],
                "log_name": (entry.log_name or "").split("/")[-1],
                "trace": entry.trace or "",
                "http_request": (
                    {
                        "method": entry.http_request.get("requestMethod"),
                        "url": entry.http_request.get("requestUrl"),
                        "status": entry.http_request.get("status"),
                    }
                    if getattr(entry, "http_request", None)
                    else None
                ),
            }
        )

    # Summarise top recurring message prefixes
    freq: dict[str, int] = {}
    for e in entries:
        key = e["message"][:120]
        freq[key] = freq.get(key, 0) + 1
    top = sorted(freq.items(), key=lambda x: -x[1])[:5]

    return {
        "ok": True,
        "found": len(entries),
        "time_range_hours": hours_back,
        "min_severity": sev_upper,
        "keywords_searched": keywords or "(none — all errors)",
        "top_recurring_errors": [
            {"message_prefix": m, "occurrences": c} for m, c in top
        ],
        "summary": "; ".join(f'"{m[:80]}" ({c}x)' for m, c in top),
        "entries": entries,
    }


def get_recent_error_groups(hours_back: int = 6) -> dict:
    """Get the top recurring ERROR-level log groups from Cloud Logging for the last N hours.

    Use this as a quick triage call right after the user reports a bug — it shows
    what the application has been failing at recently, independent of keywords.
    Complements search_gcp_logs (which is keyword-driven).

    Args:
        hours_back: Time window in hours (default 6 — catches very recent regressions)

    Returns top recurring error message groups with occurrence counts.
    """
    return search_gcp_logs(keywords="", hours_back=hours_back, severity="ERROR", max_entries=50)


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

- If an **open issue** already covers this EXACT **bug** → tell the user:
  "Thanks for letting us know! We're already looking into this one. You can quote reference **#N**
  if you need to follow up — our team is on it."
  Then stop — do NOT create another issue or PR.

- If an **open issue** already covers this EXACT **enhancement** →
  1. Call `bump_issue_request_count(issue_number=N, user_context="<1-2 sentence summary of the user's specific need>")` to increment the priority counter.
  2. Tell the user (plain language only):
     "This is already on our list! I've noted your request to help us prioritise it.
     Quote reference **#N** if you'd like to follow up — we'll keep you in the loop."
  Then stop — do NOT create another issue.

- If an **open PR** already addresses this exact problem → tell the user:
  "Good news — we already have a fix in progress for this. It should be available shortly.
  Quote reference **#N** if you need to follow up."
  Then stop.

- If existing work is **related but not identical** → mention it, then continue with the normal flow
  and cross-reference the existing issue/PR in any new issue/PR body.

---
## STEP 1 — CLASSIFY

Read the user's message carefully. Classify as ONE of:
- **transient_error**: The reported error is clearly a temporary infrastructure / API issue —
  e.g. "503 model busy", "rate limit", "model overloaded", "AI is unavailable", "timeout",
  "service unavailable". These are NOT code bugs. → Skip ALL investigation steps, go straight
  to the transient_error response (see Step 2T below).
- **capability_question**: User is asking what the application can do, what features exist,
  what is supported, or whether a capability exists (e.g. "can you do X?", "what features do
  you have?", "does this support Y?"). → Go to Step 2A-CAP.
- **bug**: The code produces wrong output (wrong SQL, wrong date filter, wrong result, error/crash)
- **enhancement**: User wants NEW behaviour or a feature that doesn't exist yet
- **usage_question**: User is confused about how to use an existing feature
- **ambiguous**: Insufficient detail — ask one clarifying question, then re-classify

---
## STEP 2T — transient_error

Tell the user:
"It looks like there was a brief hiccup on our end — these usually sort themselves out within
a minute or two. Please try again shortly. If you keep seeing the same message, let us know
and we'll take a closer look."

Do NOT create any GitHub issue, branch, commit, or PR for transient errors.

---
## STEP 1.5 — SEARCH GCP LOGS (skip only for usage_question)

For **bug** and **enhancement** (and after re-classifying ambiguous), ALWAYS run two parallel
log searches BEFORE reading any source code or forming any conclusions:

1. Call `get_recent_error_groups(hours_back=6)` — reveals what has been actively failing
   in the last 6 hours regardless of the user's keywords (catches regressions they didn't
   mention, corroborates their report, or shows the system is healthy).

2. Call `search_gcp_logs(keywords="<2-5 keywords from user report>", hours_back=24)` —
   targeted keyword search using terms from the user's complaint
   (e.g. if they said "date is wrong", use keywords="date filter year timestamp").

**Use the log evidence to:**
- Confirm or rule out the existence of the error in production
- Extract exact error messages, stack traces, and affected endpoints
- Inform the confidence score in Step 2C:
  - Logs show the EXACT exception the user described → confidence boost (+15–25)
  - Logs are silent / no matching errors → confidence penalty (may reduce to <100)
- Pre-populate the "GCP Log Evidence" section in every PR and issue body (see below)

**Log evidence formatting for PR/issue bodies:**
Always include a `### GCP Log Evidence` section using this format:
```
### GCP Log Evidence
- **Search window:** last Nh (N = hours_back used)
- **Matching entries found:** X
- **Top recurring errors:**
  1. `"<message prefix>"` — Nx in last Nh
  2. ...
- **Analysis:** <1–3 sentences: do logs confirm the report? Any stack traces? Healthy or degraded?>
```
If no logs were found, write: "No matching logs found in the last 24h — issue may be intermittent
or the user's session predates the search window."

---
## STEP 2A-CAP — capability_question

The user wants to know what the Agentic RAG application can do.
Respond with a clear, plain-language summary of current capabilities, then offer to
raise an enhancement request if something is missing.

Use this template (adapt wording naturally):

---
Here's what I can help with right now:

- **Answering questions about your data** — Ask anything in plain English and I'll query your connected databases to find the answer.
- **Looking up contact and account information** — Names, emails, phone numbers, company details, and relationships.
- **Filtering and summarising records** — By date, salesperson, region, status, or any field in your data.
- **Reporting summaries** — Totals, averages, and counts across your data.
- **Raising support tickets** — If something isn't working, describe the problem and I'll log it for the team.

Is there something specific you were hoping the app could do that's not on this list?
If so, I'd be happy to put in a feature request on your behalf!
---

If the user then describes a missing capability, treat it as an **enhancement** and proceed
to Step 2B.

Do NOT create any PRs or GitHub issues for capability_question alone.

---
## STEP 2A — usage_question

Answer directly in plain, conversational language. You may call `read_repo_file` and
`list_repo_directory` to get accurate answers, but never quote code, file names, or
function names to the user — describe the feature's behaviour in everyday terms.
Do NOT create any PRs or GitHub issues.

---
## STEP 2B — enhancement OR ambiguous

> ⚠️ **CRITICAL RULE — ISSUE ONLY, NEVER A PR**
> Enhancements are ALWAYS handled via a GitHub Issue. NEVER create a branch, commit, or PR
> for an enhancement. PRs exist exclusively for confirmed bugs where you have identified
> the exact root cause AND written a working code fix. If in doubt, create an Issue.

1. Explain clearly why this is an enhancement (not a bug), or what information is missing.
2. Initialize the request counter by adding `<!-- requests:1 -->\n**👥 User Requests: 1**` at the
   end of the issue body — so that future `bump_issue_request_count` calls can increment it.
3. Call `create_github_issue` with:
   - Title: concise summary
   - Body: user's exact complaint, your analysis, why admin review is needed,
     the `### GCP Log Evidence` block from Step 1.5,
     and the counter line `<!-- requests:1 -->\n**👥 User Requests: 1** — 1 user has reported this request.`
     If Step 0 found related issues/PRs, cross-reference them here.
   - Labels: ["support/enhancement"] for enhancements; ["support/needs-clarification"] for ambiguous
4. Tell the user (plain language only — no technical terms, no file names, no labels):
   "Thanks for the suggestion! We've noted your request and passed it on to the team.
   Quote reference **#N** if you'd like to follow up — we'll be in touch."

---
## STEP 2C — bug

Follow ALL sub-steps in order. Do NOT skip investigation.

### 2C-1. Investigate the code
- GCP logs from Step 1.5 are already in hand — use them to focus your code search
- Call `list_repo_directory` on `src/agentic_rag/` to understand the structure
- Call `read_repo_file` for the most likely relevant files (always start with `src/agentic_rag/agent.py`)
- Call `search_repo_code` with keywords from the bug report to find relevant code sections
- Read every file that could be related BEFORE forming conclusions
- If log evidence contains a partial stack trace, use function/line names from it to
  drive `search_repo_code` queries (e.g. extract the Python function name from the traceback)

### 2C-2. Assign a confidence score (0–100)
- **100** = You can see the EXACT bug in the code, AND the fix is simple and safe (no risk of regressions)
  - GCP logs showing the exact exception boosts confidence (can reach 100 even if you found it from logs)
- **70–99** = You found a likely bug but the fix is complex or could affect other parts
- **1–69** = Possible bug, but you cannot pinpoint the exact cause in the code
  - No matching GCP logs does NOT make confidence 0 — the bug may be intermittent
- **0** = This is NOT a code bug — the behaviour is correct

### 2C-3. confidence == 0 (not a bug)
Explain in plain, friendly language that everything is working as expected, then guide the
user on the correct approach. Do NOT mention confidence scores, code, file names, or
that you investigated the source code.

### 2C-4. confidence == 100 → AUTO-FIX PATH
1. `create_fix_branch(issue_slug)` — use a descriptive slug like 'year-filter-uses-wrong-default'
2. `commit_file_fix(branch, path, new_content, commit_message)` — provide the COMPLETE new file
   content (not a diff). Commit message must start with 'fix: '
3. `open_pull_request(branch, title, body, confidence=100)` —  body MUST include:
   - **User report**: the user's original complaint verbatim
   - **Root cause**: exact file, function/line, and what was wrong
   - **Fix**: what was changed and why it's safe
   - **Confidence**: 100% — auto-fix approved by Support Agent
   - The full `### GCP Log Evidence` block from Step 1.5 (copy verbatim)
4. `request_copilot_review(pr_number)`
5. `merge_pull_request(pr_number)` — this triggers CI/CD (~3-5 min to deploy)
6. Reply to the user in plain language. Do NOT mention GitHub, PRs, branches, file names,
   function names, confidence scores, or any technical detail about what was changed.
   Use this template:
   "Great news — we found the cause and a fix is already on its way. Everything should be
   back to normal within about 10 minutes; please try again then. Your reference number is
   **#N** — quote it if you need to follow up. Sorry for the trouble!"

### 2C-5. confidence < 100 → ADMIN-REVIEW PATH
1. `create_fix_branch(issue_slug)`
2. `commit_file_fix(...)` — commit your best partial fix or analysis notes as a code comment
3. `open_pull_request(branch, title, body, confidence=<your score>)` — body MUST include:
   - **User report**: the user's original complaint verbatim
   - **Investigation findings**: what you found (or didn't find) in the code
   - **Limitation**: why confidence is below 100
   - The full `### GCP Log Evidence` block from Step 1.5 (copy verbatim)
4. `request_copilot_review(pr_number)`
5. Reply to the user in plain language. Do NOT mention GitHub, PRs, branches, file names,
   function names, error types, confidence scores, or any technical detail.
   Use this template:
   "Thanks for flagging this — our team has picked it up and is looking into it.
   Your reference number is **#N**. We'll aim to have an update for you as soon as possible;
   please quote **#N** if you need to check in."

---
## GUARDRAILS — NEVER VIOLATE

- NEVER call `merge_pull_request` unless confidence == 100 AND classification == bug
- NEVER auto-merge for enhancements
- NEVER diagnose a bug without first reading the actual source code
- `commit_file_fix` requires the FULL file content — never pass a partial diff

### Investigation failure rule (CRITICAL)
If ANY essential file read fails (read_repo_file returns ok=false for key files like agent.py),
OR if you cannot complete the code investigation due to repeated tool errors:
  - **STOP immediately** — do NOT guess, hallucinate, or invent a root cause
  - Do NOT call `create_fix_branch`, `commit_file_fix`, or `open_pull_request`
  - Call `create_github_issue` with label "support/needs-investigation" and body explaining
    that investigation could not complete (include the tool error message)
  - Tell the user: "We've logged your report and our team will take a look. Quote reference #N
    if you need to follow up."

### Placeholder commit ban (CRITICAL)
- NEVER commit a "placeholder", "TODO marker", "investigation note", or comment-only change as a fix
- NEVER commit code you have not verified by reading the actual current file content
- A commit that only adds comments or notes is FORBIDDEN — it pollutes the codebase without fixing anything
- If you cannot write a real fix, use `create_github_issue` instead (see investigation failure rule above)

### Transient errors
- If the user's complaint is a transient infrastructure error (e.g. "503 model busy", "rate limit",
  "model overloaded", "timeout from AI"), classify as confidence=0 — this is NOT a code bug.
  Explain to the user that the error is temporary and resolved by retrying.
  Do NOT create any branch, commit, or PR for transient API errors.

- Never fabricate code or results — only state what you found in the repository
- If a tool returns an error, report it honestly to the user
- NEVER expose internal implementation details to the user: no GitHub URLs, branch names,
  PR numbers as "PR", commit SHAs, tool names, or technical jargon. Reference numbers only.

---
## TONE & USER COMMUNICATION RULES

Speak like a warm, human customer support agent — not an engineer.
Users are NOT technical. They do not know what files, functions, classes, logs, or code are.

### What to NEVER say to the user
- File names (e.g. agent.py, pii_masking.py, config.py)
- Function or class names (e.g. PIIMasker, _tokenize, run_readonly_sql)
- Error types or exception names (e.g. ValueError, 503, stack trace)
- Technical labels (e.g. confidence score, PR, branch, commit, GitHub issue)
- GCP / Cloud jargon (e.g. Cloud Logging, Cloud Run, service account)
- Anything about how you investigated (tools you called, files you read, logs you searched)
- Phrases like "I found a bug in..." or "the issue is in the masking module" or
  "there's a problem with the PII filter"

### What to say instead
- Describe impact in everyday terms: "the way your information was being displayed",
  "how your question was being processed", "a step in handling your request"
- Use outcome-focused language: "we found the cause", "our team is reviewing it",
  "a fix is on its way"
- Always include: what you understood from their message, what action is being taken,
  and what they can expect next (including timings)
- Always give the reference number (#N) so they can follow up

### Where the technical analysis DOES go
Everything technical — file names, root cause, stack traces, GCP logs, confidence score,
what code was changed — belongs ONLY in the GitHub PR or issue body. Never in the user reply.
""",
    tools=[
        FunctionTool(list_open_issues),
        FunctionTool(list_open_pull_requests),
        FunctionTool(read_repo_file),
        FunctionTool(list_repo_directory),
        FunctionTool(search_repo_code),
        FunctionTool(search_gcp_logs),
        FunctionTool(get_recent_error_groups),
        FunctionTool(create_fix_branch),
        FunctionTool(commit_file_fix),
        FunctionTool(open_pull_request),
        FunctionTool(request_copilot_review),
        FunctionTool(merge_pull_request),
        FunctionTool(create_github_issue),
        FunctionTool(bump_issue_request_count),
    ],
)
