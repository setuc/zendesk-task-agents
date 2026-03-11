#!/bin/bash
#
# 3-Terminal Live SLA Guardian Demo — with PII Codec
#
# Same as demo-3terminal.sh but with --pii flag on all commands.
# PII (customer names, emails, resolution summaries) is stripped from
# Temporal event history and stored in Redis instead.
#
# Usage:
#   ./demo-3terminal-pii.sh              # 100 tickets, default settings
#   ./demo-3terminal-pii.sh 200 5        # 200 tickets, 5/sec injection rate
#   ./demo-3terminal-pii.sh 50 2 99      # 50 tickets, 2/sec, seed=99
#
# Prerequisites:
#   - tmux installed
#   - temporal server start-dev (running in another terminal)
#   - redis-server running (redis-server --daemonize yes)
#   - uv sync (dependencies installed)
#
# After the demo, verify PII redaction:
#   redis-cli -n 1 KEYS "*"                          # PII keys in Redis
#   redis-cli -n 1 GET <key>                          # real customer name
#   temporal workflow show -w agent-ticket-TKT-30001   # only pii:ref: tokens
#

set -e

TICKETS="${1:-100}"
RATE="${2:-3}"
SEED="${3:-42}"
SLA_OFFSET="${4:-5}"
SESSION="sla-guardian-pii-demo"

cd "$(dirname "$0")"

# Check prerequisites
if ! command -v tmux &>/dev/null; then
    echo "Error: tmux is not installed. Install with: brew install tmux"
    exit 1
fi

if ! command -v temporal &>/dev/null; then
    echo "Error: temporal CLI not found. Install with: brew install temporal"
    exit 1
fi

if ! command -v redis-cli &>/dev/null; then
    echo "Error: redis-cli not found. Install with: brew install redis"
    exit 1
fi

# Check if Temporal is running
if ! temporal workflow list --limit 1 &>/dev/null 2>&1; then
    echo "Error: Temporal dev server is not running."
    echo "Start it first:  temporal server start-dev"
    exit 1
fi

# Check if Redis is running
if ! redis-cli ping &>/dev/null 2>&1; then
    echo "Error: Redis is not running."
    echo "Start it first:  redis-server --daemonize yes"
    exit 1
fi

# Clean up old workflows and PII data
echo "Cleaning up stale workflows..."
temporal workflow terminate --query "TaskQueue='sla-guardian-live'" --reason "demo reset" --yes 2>/dev/null || true
echo "Flushing PII Redis DB..."
redis-cli -n 1 FLUSHDB >/dev/null

# Kill existing session if it exists
tmux kill-session -t "$SESSION" 2>/dev/null || true

echo ""
echo "Starting SLA Guardian PII Demo"
echo "  Tickets:  $TICKETS"
echo "  Rate:     $RATE tickets/sec"
echo "  Seed:     $SEED"
echo "  PII:      ENABLED (Redis-backed redaction)"
echo ""

# Create tmux session with first pane (Dashboard)
tmux new-session -d -s "$SESSION" -x 220 -y 60

# Name the first pane
tmux rename-window -t "$SESSION" "SLA Guardian PII Demo"

# Split horizontally (top/bottom)
tmux split-window -t "$SESSION" -v -p 30

# Split the top pane vertically (left/right)
tmux select-pane -t "$SESSION:0.0"
tmux split-window -t "$SESSION" -h -p 50

# Pane layout:
#   0.0 = Top-left  (Dashboard)
#   0.1 = Top-right (Worker)
#   0.2 = Bottom    (Injector)

# --- Pane 0: Dashboard (top-left) ---
tmux select-pane -t "$SESSION:0.0"
tmux send-keys -t "$SESSION:0.0" "echo '=== LIVE DASHBOARD (PII decoded from Redis) ===' && echo 'Waiting for worker to start...' && sleep 5 && uv run python -m sla_guardian.main live-dashboard --refresh 3 --pii" Enter

# --- Pane 1: Worker (top-right) ---
tmux select-pane -t "$SESSION:0.1"
tmux send-keys -t "$SESSION:0.1" "echo '=== AGENT WORKER (PII redaction ON) ===' && uv run python -m sla_guardian.main live-worker --tickets $TICKETS --seed $SEED --sla-offset $SLA_OFFSET --pii" Enter

# --- Pane 2: Injector (bottom) ---
tmux select-pane -t "$SESSION:0.2"
tmux send-keys -t "$SESSION:0.2" "echo '=== TICKET INJECTOR (PII redaction ON) ===' && echo 'Waiting 8s for worker to initialize...' && sleep 8 && uv run python -m sla_guardian.main live-inject --tickets $TICKETS --rate $RATE --seed $SEED --sla-offset $SLA_OFFSET --pii" Enter

# Select the dashboard pane as the active one
tmux select-pane -t "$SESSION:0.0"

# Attach to the session
echo "Attaching to tmux session '$SESSION'..."
echo "  Ctrl+B then D to detach"
echo "  Ctrl+B then arrow keys to switch panes"
echo ""
echo "After the demo, try:"
echo "  redis-cli -n 1 KEYS '*'                        # see PII keys"
echo "  redis-cli -n 1 GET <key>                        # see real name"
echo "  redis-cli -n 1 DEL <key>                        # right-to-erasure"
echo ""
tmux attach-session -t "$SESSION"
