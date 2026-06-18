#!/bin/bash
# Claude Code PreToolUse hook — manual review gate for git commits.
#
# Triggered by: PreToolUse matcher "Bash(git commit*)"
# Blocks git commit unless .review-approved exists.
#
# The .review-approved file is created by the AI agent after:
#   1. Mechanical checks pass (git pre-commit hook)
#   2. Manual review complete (dead code, docs, test gaps, consistency)
#   3. User explicitly approves
#
# The git pre-commit hook does NOT check this file — Claude Code
# intercepts the Bash call before git even sees it.

REVIEW_FILE="${CLAUDE_PROJECT_DIR}/.review-approved"

if [ -f "$REVIEW_FILE" ]; then
    # Review was done — allow the commit, clean up for next time
    rm "$REVIEW_FILE"
    exit 0
fi

cat << 'EOF'

╔══════════════════════════════════════════════════════════════╗
║  🔒  COMMIT BLOCKED — MANUAL REVIEW REQUIRED               ║
╚══════════════════════════════════════════════════════════════╝

  The code may compile and tests may pass, but the CLAUDE.md
  commit workflow requires a manual review before every commit.

  Before retrying git commit:

    1. Review: dead code, unused imports, stale references
    2. Review: docs up to date? new features documented?
    3. Review: any test gaps? did a bug escape?
    4. Report findings to the user
    5. User approves → echo approved > .review-approved
    6. Retry: git commit ...

  See CLAUDE.md § Commit Workflow.
  (Use git commit --no-verify to bypass this gate in emergencies.)

EOF
exit 2
