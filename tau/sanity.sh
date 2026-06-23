#!/bin/bash
# Sanity Test Suite for Tau Agent
# Tests: CLI, positional args, tool calling, fork functionality, continue command
# Each test is a SINGLE invocation of tau.py

# IMPORTANT: The prompts (questions) are deliberately crafted the way they are - they should NOT be modified by LLM

# CRITICAL RULE: NEVER MODIFY TESTS, PROMPTS, etc. ...

cd "$(dirname "${BASH_SOURCE[0]}")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

# Prepare the environment
DUTFILE="./tau-sanity.py"
cp ./tau.py $DUTFILE
pkill -f $DUTFILE
DUT="$DUTFILE"

# Optional: pass a positional parameter to add --llm <value> to all tau calls
if [ $# -ge 1 ]; then
    DUT="$DUTFILE --llm $1"
fi

# Redirect test log files away from the real log directory.
# Override with TAU_LOG_DIR=... to point elsewhere.
export TAU_LOG_DIR="${TAU_LOG_DIR:-$HOME/.local/tau/logtest}"

# Temp file for capturing output
TEMP_FILE="/tmp/sanity_test_$$"
TEST_FILE="./test.tmp"
SANITY_LOG="${SANITY_LOG:-/tmp/sanity_complete.log}"
SANITY_AGENT_LOG="${SANITY_AGENT_LOG:-/tmp/sanity_agent.log}"

# Initialize the complete sanity log (delete any leftover from previous run)
> "$SANITY_LOG"

# Append test output + metadata to the complete sanity log.
# Usage: log_test "Test description" duration result
log_test() {
    local desc="$1"
    local duration="$2"
    local result="$3"
    {
        echo "========================================"
        echo "TEST: $desc | $(date '+%Y-%m-%d %H:%M:%S') | $result | ${duration}s"
        echo "========================================"
        cat "$TEMP_FILE"
        echo ""
    } >> "$SANITY_LOG"
}

# Compute PASS/FAIL based on whether PASSED or FAILED increased since last call.
# A test is FAIL if ANY failure occurred (FAILED increased), regardless of PASSED.
# A test is PASS only if PASSED increased AND FAILED did not increase.
# This treats expect + expect_not as one combined test: both must pass.
# Usage: result_since_test $PASSED_BEFORE $FAILED_BEFORE
result_since_test() {
    if [ "$FAILED" -gt "$2" ]; then
        echo "FAIL"
    elif [ "$PASSED" -gt "$1" ]; then
        echo "PASS"
    else
        echo "SKIP"
    fi
}

pass() {
    echo -e "${GREEN}✅ $1${NC}"
    ((PASSED++))
}

fail() {
    echo -e "${RED}❌ $1${NC}"
    ((FAILED++))
}

show_failure_output() {
    local file="$1"

    echo "  Output:"
    cat "$file"
}

# Check for unhandled exceptions or critical context validation errors in $TEMP_FILE.
# Call at the start of EVERY test validation (before pass/expect/expect_not).
# Returns 0 if errors found (caller should fail), 1 if clean.
# NOTE: Only match on actual ERROR conditions, not informational warnings like
# fork_tool_call_id not found (which is benign).
has_exceptions() {
    grep -qiE "Traceback|AttributeError|cannot append assistant after role|consecutive assistant messages|append assistant message after system message" "$TEMP_FILE"
}

expect() {
    local file="$1"
    local msg="$2"
    shift 2

    if has_exceptions; then
        fail "$msg FAILED (unhandled exception in output)"
        show_failure_output "$file"
        return 1
    fi

    local pattern
    for pattern in "$@"; do
        if ! grep -qi -- "$pattern" "$file"; then
            fail "$msg FAILED"
            show_failure_output "$file"
            return 1
        fi
    done

    pass "$msg WORKS" || return 1
}

expect_not() {
    local file="$1"
    local msg="$2"
    shift 2

    if has_exceptions; then
        fail "$msg FAILED (unhandled exception in output)"
        show_failure_output "$file"
        return 1
    fi

    local pattern
    for pattern in "$@"; do
        if grep -qi -- "$pattern" "$file"; then
            fail "$msg FAILED"
            show_failure_output "$file"
            return 1
        fi
    done

    pass "$msg WORKS" || return 1
}

echo "========================================"
echo "  Tau Agent Sanity Test Suite"
echo "========================================"

# ============================================
# TEST 1: Multi-turn CLI via stdin (piping)
# ============================================
echo -e "\nTest 1: Multi-turn CLI (Austria language + capital)..."
# Multi-turn via stdin piping
{
    echo "Lets talk about Austria. What is the language spoken in Austria? Answer super concise."
    echo "What is the name of the capital? If you do not know just say so."
} | timeout 300 $DUT 2>&1 | tee "$TEMP_FILE"

# Expect German for language and Vienna/Wien for capital
PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "Multi-turn CLI via stdin (language + capital)" \
    "german" \
    "vienna\|wien"
log_test "Test 1: Multi-turn CLI (language + capital)" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 2: Multi-turn conversation (same questions via positional args)
# ============================================
echo -e "\nTest 2: Multi-turn conversation (Austria language + capital)..."
# Same questions as Test 1, but via positional arguments
{
    timeout 300 $DUT "Lets talk about Austria. What is the language spoken in Austria? Answer super concise." "What is the name of the capital? If you don't know just say so."
} 2>&1 | tee "$TEMP_FILE"
# Expect German for language and Vienna/Wien for capital
PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "Multi-turn conversation (language + capital)" \
    "german" \
    "vienna\|wien"
log_test "Test 2: Multi-turn conversation (language + capital)" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 3: Tool calling (single invocation)
# ============================================
echo -e "\nTest 3: Tool calling (file creation)..."

rm -f "$TEST_FILE"

{
    timeout 60 $DUT "Create an empty file at $TEST_FILE using a tool and confirm it was created"
} 2>&1 | tee "$TEMP_FILE"

PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
if [ -f "$TEST_FILE" ]; then
    if has_exceptions; then
        fail "Tool calling works (file created) FAILED (unhandled exception in output)"
        show_failure_output "$TEMP_FILE"
    else
        pass "Tool calling works (file created)"
    fi
    rm -f "$TEST_FILE"
else
    fail "Tool calling failed (file not created)"
    echo "  Output:"
    cat "$TEMP_FILE"
fi
log_test "Test 3: Tool calling (file creation)" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 4: Fork command (single invocation)
# ============================================
echo -e "\nTest 4: Fork command (create file with capital)..."

# Fork command creates file in current directory (src/), so use ./capital.tmp
rm -f "$TEST_FILE"

# Single invocation with fork command
{
    timeout 60 $DUT "Lets talk about Austria. What is the language spoken in Austria? Answer super concise." "/fork Create file $TEST_FILE and write the name of the capital into the file. If you don't know the answer just say so."
} 2>&1 | tee "$TEMP_FILE"

# Check if file exists and contains a capital name (any capital)
PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
if [ -f "$TEST_FILE" ]; then
    # Check for common capital names
    expect \
        "$TEST_FILE" \
        "Fork command (file contains capital name)" \
        "vienna\|wien"
else
    fail "Fork command failed to create file"
    echo "  Output:"
    cat "$TEST_FILE"
fi

rm -f "$TEST_FILE"
log_test "Test 4: Fork command (create file with capital)" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 5: Test continue
# ============================================
echo -e "\nTest 5: Test continue using /continue command..."

# Test continue using /continue command
# Both calls must be under same timeout so they share parent PID
# (context is identified by parent PID)
{
    timeout 60 bash -c "
        $DUT 'Lets talk about Austria. What is the language spoken in Austria? Answer super concise.' 2>&1
        $DUT '/continue' 'What is the name of the capital? If you do not know just say so.' 2>&1
    "
} 2>&1 | tee "$TEMP_FILE"

# Expect Vienna/Wien mentioned in response to capital question
PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "Continue using /continue command" \
    "vienna\|wien"
log_test "Test 5: Continue using /continue command" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 6: A2A Tests
# ============================================
echo -e "\nTest 6: A2A Tests..."

# A2A Test
A2ANAME=a2aname-$$
$DUT --keep-alive "/name ${A2ANAME}" "Lets talk about Austria. What is the language spoken in Austria? Answer super concise." > "$SANITY_AGENT_LOG" &
AGENT_PID=$!
sleep 1

PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED
if [[ -z "$AGENT_PID" ]]; then
    fail "A2A failed starting"
else
    pass "A2A started"
fi
log_test "Test 6: A2A start" "0" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

{
    timeout 300 $DUT --list
} 2>&1 | tee "$TEMP_FILE"

# ============================================
# TEST 7: A2A Tests - sanity
# ============================================

# Expect ${A2ANAME}
PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "A2A server visible" \
    "${A2ANAME}"
log_test "Test 7: A2A server visible" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 8: A2A Tests - basic
# ============================================

{
    timeout 300 $DUT --name ${A2ANAME} "What is the name of the capital? If you do not know just say so."
} 2>&1 | tee "$TEMP_FILE"

# Expect Vienna/Wien mentioned in response to capital question
PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "A2A basic capital response" \
    "vienna\|wien"
log_test "Test 8: A2A basic capital response" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 8: A2A Tests - undo
# ============================================

timeout 300 $DUT --name ${A2ANAME} "Lets talk about France now. What is the language spoken in France?"
timeout 300 $DUT --name ${A2ANAME} "/undo"
{
    timeout 300 $DUT --name ${A2ANAME} "What is the capital of the country we are talking about? If you do not know just say so."
} 2>&1 | tee "$TEMP_FILE"

# Expect Vienna/Wien mentioned in response to capital question
PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "Using /undo command" \
    "vienna\|wien"
log_test "Test 8b: A2A undo" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 9+10: A2A Tests - /subagent
# ============================================

echo "Hi, I am the User, my name is George. I lived in Germany for a long time. Berlin is a nice town. Frankly, none of this is important. Originally I am Scottish, so lets talk about UK." > $TEST_FILE
{
    timeout 300 $DUT --name ${A2ANAME} "/subagent Read the file $TEST_FILE, use its information, immediatelly after reading delete the file. Make sure to delete the file, yourself! What is the language in the country we are talking about? Just say the language, ultra concise, single word, don't further qualify. If you do not know just say so."
} 2>&1 | tee "$TEMP_FILE"
rm -f $TEST_FILE

# Expect English mentioned in response to capital question
PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "Using /subagent execution" \
    "english"
log_test "Test 9: A2A subagent execution" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

{
    timeout 300 $DUT --name ${A2ANAME} "What is the Users name? Do you know? If yes, just say the name, if no, just say you don't know. Nothing else. Don't try to research, you either know, or you don't - both is good."
} 2>&1 | tee "$TEMP_FILE"

# Expect Washington mentioned in response to capital question
PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect_not \
    "$TEMP_FILE" \
    "Using /subagent isolation" \
    "George"
log_test "Test 10: A2A subagent isolation" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# TEST 11: A2A Tests - subagent/fork
# ============================================

timeout 300 $DUT --name ${A2ANAME} "/clear"

RULES="Lets play a memory game. Do not use any tools other than subagent, file_read and rm. Do not take any notes, that would be cheating. The game works like this. \
I will tell you some variables and letters, i.e. G5=dog G2=hot H3=dark I1=seven, at a later point I will ask you to concatinate all G in numerical sequence. \
G1 is first numerically, then comes G2. You concatinate G1+G2=hotdog (no space), Correct!
Other variables with other names are not relevant."

{
    timeout 300 $DUT --name ${A2ANAME} "$RULES \
    New variables: A3=cola , A4=monster .\
    Answer for A, concatenate all A?, no spaces."
} 2>&1 | tee "$TEMP_FILE"
# Expect only colamonster
PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "memory game execution (must contain colamonster)" \
    "colamonster"
log_test "Test 11a: memory game colamonster" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

echo "$RULES" > $TEST_FILE
echo "Now lets start playing the game! A5=cool A6=fish X1=magic." >> $TEST_FILE
{
    timeout 300 $DUT --name ${A2ANAME} "The file $TEST_FILE already exists with the correct content. DO NOT create it, DO NOT overwrite it, DO NOT modify it. Just run a subagent (not fork). \
    Give the subagent this exact task: Read file $TEST_FILE. The file contains rules for a memory game and variable assignments. Follow the rules: concatenate all A-prefixed variables in numerical order (A5 then A6). Return ONLY the concatenated result as your answer - nothing else. After returning the answer, DELETE the file. You must delete the file. Use rm to remove it. The file must not exist after you finish. \
    Your only job is to relay the subagent's answer verbatim - output exactly what the subagent returned, nothing else."
} 2>&1 | tee "$TEMP_FILE"
rm -f $TEST_FILE
# Expect only coolfish
PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "subagent memory execution (must contain coolfish)" \
    "coolfish"
# Must not contain colamonster
expect_not \
    "$TEMP_FILE" \
    "subagent memory execution (must not contain colamonster)" \
    "colamonster"
log_test "Test 11b: subagent memory coolfish" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

{
    timeout 300 $DUT --name ${A2ANAME} "Now you again. New variables: X5=rock , X4=big . Concatenate ONLY X variables from your memory in numerical order (X4 then X5). Ignore all other variables from earlier context. Answer only the full result."
} 2>&1 | tee "$TEMP_FILE"
# Expect only bigrock
PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
expect \
    "$TEMP_FILE" \
    "subagent memory execution (must contain bigrock)" \
    "bigrock"
# Must not contain magic
expect_not \
    "$TEMP_FILE" \
    "subagent memory execution (must not contain magic)" \
    "magic"
log_test "Test 11c: subagent memory bigrock" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"

# ============================================
# exit
# ============================================

PASSED_BEFORE=$PASSED; FAILED_BEFORE=$FAILED; SECONDS=0
timeout 300 $DUT --name ${A2ANAME} "/exit" 2>&1 | tee "$TEMP_FILE"
if has_exceptions; then
    fail "Exit command produced unhandled exceptions"
    show_failure_output "$TEMP_FILE"
fi
log_test "Exit: /exit command" "$SECONDS" "$(result_since_test $PASSED_BEFORE $FAILED_BEFORE)"
sleep 1
#ASK ensure AGENT_PID no longer exists
kill $AGENT_PID 2>/dev/null || true

rm $DUTFILE

# ============================================
# SUMMARY
# ============================================

# Final check: scan keep-alive agent log for any exceptions
# Pattern matches actual Python exceptions (Traceback, specific Error types, Exception: with colon)
# Avoids false positives from model output containing the word "exception" in normal text
if [ -f "$SANITY_AGENT_LOG" ] && grep -qiE "Traceback|AttributeError|^[[:space:]]*[A-Za-z]+Error:|^[[:space:]]*[A-Za-z]+Exception:" "$SANITY_AGENT_LOG"; then
    echo -e "\n${RED}❌ Keep-alive agent produced unhandled exceptions${NC}"
    echo "  Output from $SANITY_AGENT_LOG:"
    grep -iE "Traceback|AttributeError|^[[:space:]]*[A-Za-z]+Error:|^[[:space:]]*[A-Za-z]+Exception:" "$SANITY_AGENT_LOG" | head -20
    ((FAILED++))
fi

echo ""
echo "========================================"
echo "  Results"
echo "========================================"
echo "Passed: $PASSED"
echo "Failed: $FAILED"

if [ $FAILED -eq 0 ]; then
    echo -e "\n${GREEN}All tests passed! ✅${NC}"
    rm -f "$TEMP_FILE"
    exit 0
else
    echo -e "\n${RED}Some tests failed!${NC}"
    echo "  Check logs for details:"
    echo "    $SANITY_LOG"
    echo "    $SANITY_AGENT_LOG"
    echo "  CRITICAL: sanity.sh has to be a 100% pass, no exceptions. Regardless of whether related or unrelated to recent changes, sanity.sh must fully pass. YOU OWN THIS!"
    exit 1
fi
