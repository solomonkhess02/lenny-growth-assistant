#!/bin/bash
PI="$APPDATA/npm/pi.cmd"; M="--provider ollama --model qwen3:4b-instruct"
run(){ n="$1"; shift; t0=$(date +%s)
  v=$(timeout 400 "$PI" -p --mode json $M "$@" 2>&1 | grep -o '"usage":{"input":[0-9]*,"output":[0-9]*' | tail -1)
  t1=$(date +%s); printf "%-36s %-40s %ss\n" "$n" "${v:-NO_USAGE}" "$((t1-t0))"; }
run H2_minsys_evidence_file --no-tools --no-prompt-templates --system-prompt "Answer briefly." "@evidence_block.txt"
run F2_oursys_evidence_file --no-tools --no-prompt-templates --append-system-prompt system_prompt.txt "@evidence_block.txt"
run G2_oursys_tiny_user     --no-tools --no-prompt-templates --append-system-prompt system_prompt.txt "Say BANANA."
echo "ALLDONE"
