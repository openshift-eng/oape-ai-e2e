#!/usr/bin/env python3
from __future__ import annotations
"""
Build actionable review threads from PR comments.

Two-pass approach:
  Pass 1: Fetch metadata (lightweight) from 3 GitHub API endpoints
  Pass 2: Fetch full body only for kept comments (reduces API traffic)

Groups comments into threads, checks for existing bot replies, and outputs
a JSON array of threads with action="process" or action="skip".

Usage:
    build_threads.py --owner openshift --repo hypershift --pr 123 \
        --bot-user openshift-app-platform-shift-bot \
        --skip-users openshift-ci,openshift-bot,dependabot,codecov,sonarcloud \
        --max-comment-size 5000 \
        --output /tmp/review-threads.json
"""

import argparse
import json
import os
import subprocess
import sys
from typing import Any

CODERABBIT_USER = "coderabbitai[bot]"
CODERABBIT_MAX_BODY_SIZE = 50000


def run_gh(args: list[str]) -> Any:
    """Run gh CLI command and return parsed JSON."""
    result = subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh command failed: {result.stderr}")
    if not result.stdout.strip():
        return []
    return json.loads(result.stdout)


def fetch_resolved_thread_ids(owner: str, repo: str, pr: int) -> dict[int, bool]:
    """Fetch resolved status for review threads, keyed by first comment's databaseId."""
    query = '''
    query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $number) {
          reviewThreads(first: 100, after: $cursor) {
            nodes {
              isResolved
              comments(first: 1) {
                nodes { databaseId }
              }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    }
    '''
    resolved: dict[int, bool] = {}
    cursor = None

    while True:
        try:
            args = [
                "api", "graphql",
                "-f", f"query={query}",
                "-f", f"owner={owner}",
                "-f", f"repo={repo}",
                "-F", f"number={pr}",
            ]
            if cursor:
                args.extend(["-f", f"cursor={cursor}"])
            result = run_gh(args)
        except RuntimeError:
            return resolved

        threads_data = result["data"]["repository"]["pullRequest"]["reviewThreads"]
        for thread in threads_data["nodes"]:
            comments = thread.get("comments", {}).get("nodes", [])
            if comments and comments[0].get("databaseId"):
                resolved[comments[0]["databaseId"]] = thread["isResolved"]

        if not threads_data["pageInfo"]["hasNextPage"]:
            break
        cursor = threads_data["pageInfo"]["endCursor"]

    return resolved


def fetch_paginated_meta(endpoint: str, jq_expr: str) -> list[dict]:
    """Pass 1: Fetch paginated metadata from a GitHub API endpoint with jq filtering."""
    try:
        data = run_gh(["api", endpoint, "--paginate", "--jq", jq_expr])
    except RuntimeError:
        return []
    if isinstance(data, list) and data and isinstance(data[0], list):
        return [item for page in data for item in page]
    return data if isinstance(data, list) else []


def fetch_full_comment(owner: str, repo: str, pr: int, comment_id: int, comment_type: str) -> dict | None:
    """Pass 2: Fetch full body for a single comment."""
    endpoints = {
        "inline": f"repos/{owner}/{repo}/pulls/comments/{comment_id}",
        "review": f"repos/{owner}/{repo}/pulls/{pr}/reviews/{comment_id}",
        "issue": f"repos/{owner}/{repo}/issues/comments/{comment_id}",
    }
    endpoint = endpoints[comment_type]
    try:
        return run_gh(["api", endpoint])
    except RuntimeError:
        return None


def filter_comments(
    comments: list[dict],
    bot_user: str,
    skip_users: set[str],
    max_size: int,
    comment_type: str,
) -> list[dict]:
    """Filter comments based on user, size, and validity."""
    kept = []
    for c in comments:
        login = c.get("user_login", "")
        if login == bot_user:
            continue
        if login in skip_users:
            continue
        body_len = c.get("body_len", 0)
        is_coderabbit_review = (login == CODERABBIT_USER and comment_type == "review")
        if not is_coderabbit_review and body_len > max_size:
            continue
        if is_coderabbit_review and body_len > CODERABBIT_MAX_BODY_SIZE:
            continue
        if body_len == 0:
            continue

        if comment_type == "inline":
            line = c.get("line")
            original_line = c.get("original_line")
            if line is None and original_line is None:
                continue

        c["_type"] = comment_type
        kept.append(c)
    return kept


ACKNOWLEDGMENTS = frozenset({
    "lgtm", "looks good", "looks good to me", "+1", "approved",
    "thank you", "thanks", "ship it", "nit: lgtm",
})


def is_acknowledgment(body: str) -> bool:
    """Check if a comment is a pure acknowledgment with no actionable content."""
    return body.strip().lower().rstrip(".!") in ACKNOWLEDGMENTS


def group_inline_threads(comments: list[dict]) -> list[list[dict]]:
    """Group inline comments into threads by in_reply_to_id."""
    roots: dict[int, list[dict]] = {}
    for c in comments:
        reply_to = c.get("in_reply_to_id")
        if reply_to:
            root_id = reply_to
        else:
            root_id = c["id"]
        roots.setdefault(root_id, []).append(c)

    for thread in roots.values():
        thread.sort(key=lambda x: x.get("created_at", ""))

    return list(roots.values())


def check_replied(owner: str, repo: str, pr: int, comment_id: int, comment_type: str) -> bool:
    """Check if bot already replied to this comment. Returns True if safe to reply."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    check_script = os.path.join(script_dir, "check_replied.py")

    type_map = {
        "inline": "review_comment",
        "review": "review_summary",
        "issue": "issue_comment",
    }
    check_type = type_map.get(comment_type, "review_comment")

    try:
        result = subprocess.run(
            ["python3", check_script, owner, repo, str(pr), str(comment_id), "--type", check_type],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 2:
            print(f"[build_threads] WARNING: check_replied error for {comment_id}, defaulting to safe-to-reply", file=sys.stderr)
            return True
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return True


def build_thread_output(
    thread_comments: list[dict],
    owner: str,
    repo: str,
    pr: int,
    comment_type: str,
) -> dict:
    """Build a single thread output dict."""
    first = thread_comments[0]
    thread_id = first["id"]

    safe = check_replied(owner, repo, pr, thread_id, comment_type)

    output = {
        "thread_id": thread_id,
        "type": comment_type,
        "action": "process" if safe else "skip",
        "skip_reason": "" if safe else "already_replied",
        "file": first.get("path", "") or first.get("file", ""),
        "line": first.get("line") or first.get("original_line"),
        "comments": [],
    }

    for c in thread_comments:
        output["comments"].append({
            "author": c.get("user_login", c.get("user", {}).get("login", "")),
            "body": c.get("body", ""),
            "created_at": c.get("created_at", ""),
            "id": c["id"],
        })

    return output


def main():
    parser = argparse.ArgumentParser(description="Build review threads from PR comments")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--bot-user", default="openshift-app-platform-shift-bot")
    parser.add_argument("--skip-users", default="openshift-ci,openshift-bot,dependabot,codecov,sonarcloud")
    parser.add_argument("--max-comment-size", type=int, default=5000)
    parser.add_argument("--output", required=True)

    args = parser.parse_args()
    skip_users = set(args.skip_users.split(",")) if args.skip_users else set()

    print(f"[build_threads] Fetching comments for {args.owner}/{args.repo}#{args.pr}", file=sys.stderr)

    inline_meta = fetch_paginated_meta(
        f"repos/{args.owner}/{args.repo}/pulls/{args.pr}/comments",
        '[.[] | {id, user_login: .user.login, body_len: (.body | length), '
        'path, line, original_line, in_reply_to_id, created_at, pull_request_review_id}]',
    )
    reviews_meta = fetch_paginated_meta(
        f"repos/{args.owner}/{args.repo}/pulls/{args.pr}/reviews",
        '[.[] | {id, user_login: .user.login, body_len: (.body | length), state}]',
    )
    issue_meta = fetch_paginated_meta(
        f"repos/{args.owner}/{args.repo}/issues/{args.pr}/comments",
        '[.[] | {id, user_login: .user.login, body_len: (.body | length), created_at}]',
    )

    print(f"[build_threads] Pass 1: {len(inline_meta)} inline, {len(reviews_meta)} reviews, {len(issue_meta)} issue comments", file=sys.stderr)

    kept_inline = filter_comments(inline_meta, args.bot_user, skip_users, args.max_comment_size, "inline")
    kept_reviews = filter_comments(reviews_meta, args.bot_user, skip_users, args.max_comment_size, "review")
    kept_issue = filter_comments(issue_meta, args.bot_user, skip_users, args.max_comment_size, "issue")

    total_kept = len(kept_inline) + len(kept_reviews) + len(kept_issue)
    print(f"[build_threads] After filtering: {len(kept_inline)} inline, {len(kept_reviews)} reviews, {len(kept_issue)} issue", file=sys.stderr)

    if total_kept == 0:
        print("[build_threads] No comments to process", file=sys.stderr)
        with open(args.output, "w") as f:
            json.dump([], f)
        return

    for kept, comment_type in [(kept_inline, "inline"), (kept_reviews, "review"), (kept_issue, "issue")]:
        for c in kept:
            full = fetch_full_comment(args.owner, args.repo, args.pr, c["id"], comment_type)
            if full:
                c["body"] = full.get("body", "")

    # Filter out pure acknowledgments after fetching full bodies
    pre_ack = len(kept_inline) + len(kept_reviews) + len(kept_issue)
    kept_inline = [c for c in kept_inline if not is_acknowledgment(c.get("body", ""))]
    kept_reviews = [c for c in kept_reviews if not is_acknowledgment(c.get("body", ""))]
    kept_issue = [c for c in kept_issue if not is_acknowledgment(c.get("body", ""))]
    post_ack = len(kept_inline) + len(kept_reviews) + len(kept_issue)
    if pre_ack != post_ack:
        print(f"[build_threads] Filtered {pre_ack - post_ack} pure acknowledgment(s)", file=sys.stderr)

    threads = []

    # NOTE: distinct inline threads are intentionally NOT merged by proximity.
    # Threads are addressed sequentially (each commit is visible to the next), so
    # there is no parallel-edit hazard to avoid, and merging unrelated comments on
    # nearby lines would collapse them into one thread — with "reply once per thread"
    # that silently leaves a reviewer's request unaddressed.
    inline_threads = group_inline_threads(kept_inline)
    for thread_comments in inline_threads:
        threads.append(build_thread_output(thread_comments, args.owner, args.repo, args.pr, "inline"))

    for review in kept_reviews:
        threads.append(build_thread_output([review], args.owner, args.repo, args.pr, "review"))

    for issue_comment in kept_issue:
        threads.append(build_thread_output([issue_comment], args.owner, args.repo, args.pr, "issue"))

    # Mark resolved threads as skip
    resolved_map = fetch_resolved_thread_ids(args.owner, args.repo, args.pr)
    if resolved_map:
        resolved_count = 0
        for t in threads:
            if t["action"] != "process":
                continue
            first_comment_id = t["comments"][0]["id"] if t["comments"] else None
            if first_comment_id and resolved_map.get(first_comment_id, False):
                t["action"] = "skip"
                t["skip_reason"] = "resolved"
                resolved_count += 1
        if resolved_count:
            print(f"[build_threads] Skipped {resolved_count} resolved thread(s)", file=sys.stderr)

    processable = sum(1 for t in threads if t["action"] == "process")
    print(f"[build_threads] Built {len(threads)} threads, {processable} actionable", file=sys.stderr)

    with open(args.output, "w") as f:
        json.dump(threads, f, indent=2)


if __name__ == "__main__":
    main()
