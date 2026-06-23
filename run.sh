#!/bin/bash
# SWE-bench Verified Adapter — Quick start script
#
# Usage:
#   ./run.sh single <instance_id>    # Run a single instance
#   ./run.sh batch <count>           # Run N instances from start
#   ./run.sh resume <count>          # Resume, run up to N new instances
#   ./run.sh check-images            # Check Docker image availability
#   ./run.sh prebuild                # Pull all images
#   ./run.sh generate-preds          # Generate predictions.jsonl
#   ./run.sh eval [workers]          # Run official evaluation
#   ./run.sh gold-validation [w]    # Run gold patch validation (3x)
#   ./run.sh retry [count]           # Retry failed instances (up to 3x each)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

case "${1:-help}" in
    single)
        INSTANCE_ID="${2:?Usage: ./run.sh single <instance_id>}"
        echo -e "${GREEN}Running single instance: ${INSTANCE_ID}${NC}"
        python swe_adapter.py --instance-id "$INSTANCE_ID" --llm cuda
        ;;

    batch)
        COUNT="${2:-1}"
        echo -e "${GREEN}Running batch: ${COUNT} instances${NC}"
        python swe_adapter.py --count "$COUNT" --llm cuda
        ;;

    resume)
        COUNT="${2:-1}"
        echo -e "${GREEN}Resuming: up to ${COUNT} new instances${NC}"
        python swe_adapter.py --resume --count "$COUNT" --llm cuda
        ;;

    retry)
        COUNT="${2:-}"
        if [ -n "$COUNT" ]; then
            echo -e "${GREEN}Retrying failed instances (up to ${COUNT})${NC}"
            python swe_adapter.py --retry --count "$COUNT" --llm cuda
        else
            echo -e "${GREEN}Retrying all failed instances${NC}"
            python swe_adapter.py --retry --llm cuda
        fi
        ;;

    check-images)
        echo -e "${YELLOW}Checking Docker images...${NC}"
        python swe_adapter.py --check-images
        ;;

    prebuild)
        echo -e "${YELLOW}Pulling all images...${NC}"
        python swe_adapter.py --prebuild-images
        ;;

    generate-preds)
        echo -e "${YELLOW}Generating predictions.jsonl...${NC}"
        python swe_adapter.py --generate-preds
        ;;

    eval)
        WORKERS="${2:-4}"
        echo -e "${YELLOW}Running official evaluation (${WORKERS} workers)...${NC}"
        python swe_adapter.py --eval --eval-workers "$WORKERS"
        ;;

    gold-validation)
        WORKERS="${2:-4}"
        echo -e "${YELLOW}Running gold validation (3x, ${WORKERS} workers)...${NC}"
        python swe_adapter.py --gold-validation --eval-workers "$WORKERS"
        ;;

    help|*)
        echo "SWE-bench Verified Adapter for Tau Agent"
        echo ""
        echo "Usage: ./run.sh <command> [args]"
        echo ""
        echo "Commands:"
        echo "  single <instance_id>    Run a single instance"
        echo "  batch <count>           Run N instances from start"
        echo "  resume <count>          Resume, run up to N new instances"
        echo "  retry [count]           Retry failed instances (up to 3x each)"
        echo "  check-images            Check Docker image availability"
        echo "  prebuild                Pull all images"
        echo "  generate-preds          Generate predictions.jsonl"
        echo "  eval [workers]          Run official evaluation"
        echo "  gold-validation [w]    Run gold patch validation (3x)"
        echo ""
        echo "Status:"
        echo "  python3 status.py              Full status report"
        echo "  python3 status.py summary      One-line summary"
        echo "  python3 status.py by_repo      Grouped by repository"
        ;;

esac
