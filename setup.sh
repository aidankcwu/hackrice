#!/usr/bin/env bash
# One-time setup. Run from the repo root AFTER unzipping brian-claude-code.zip there.
#   bash setup.sh
# Safe to re-run.
set -euo pipefail

root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "Run this inside the hackrice repo."; exit 1; }
cd "$root"

echo "→ branch"
git fetch -q origin
if git show-ref --quiet refs/heads/brian-ios; then
  git checkout -q brian-ios
else
  git checkout -q -b brian-ios origin/reactive-glasses
fi
echo "   on $(git rev-parse --abbrev-ref HEAD) at $(git rev-parse --short HEAD)"

echo "→ folders"
mkdir -p .claude/logs ios/Brian/Fixtures ios/Brian/Screenshots ios/Brian/Sources ios/Brian/Tests
grep -qx '.claude/logs/' .gitignore 2>/dev/null || echo '.claude/logs/' >> .gitignore

echo "→ tools"
missing=0
for t in claude xcodegen uv node npm xcodebuild xcrun gh; do
  if command -v "$t" >/dev/null 2>&1; then printf "   ok   %s\n" "$t"; else printf "   MISSING %s\n" "$t"; missing=1; fi
done
if command -v xcode-select >/dev/null 2>&1; then
  sel=$(xcode-select -p 2>/dev/null || true)
  case "$sel" in
    /Applications/Xcode*.app/Contents/Developer) printf "   ok   xcode-select → %s\n" "$sel" ;;
    *) printf "   FIX  xcode-select → '%s' (run: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer)\n" "$sel"; missing=1 ;;
  esac
fi
if [ "$missing" = 1 ]; then
  echo "   install what is MISSING (docs/README_FIRST.md step 1), then re-run. Continuing anyway."
fi

echo "→ commit the plan"
git add -A
if git diff --cached --quiet; then
  echo "   nothing new to commit"
else
  git commit -q -m "brian-ios: plan, agents, skills, settings, iOS project spec"
  echo "   committed"
fi

cat <<'EOF'

Done. Next:
  1. claude --model opus
  2. paste:  Read docs/PLAN.md. You are the orchestrator. Follow section 0. Start Job 0.
  3. approve the two project MCP servers when Claude Code asks (meta-wearables-docs, apple-docs)
EOF
