========================================================
ASTER / PERSONAL AI OS AUTONOMOUS STATUS
========================================================
READINESS      : NOT READY
BENCHMARK      : 97.84 / 100
PASS           : 457
PARTIAL        : 1
FAIL           : 1
ISSUES         : 0
CURRENT        : NONE
PHASE          : WATCH_EXTERNAL_GAP
CANDIDATE      : qwen2.5-coder:3b
ASTER→AI OS    : PARTIAL: current owner-authenticated HTTP exchange and integrity verified; fabricated exec
AI OS→ASTER    : BLOCKED_EXTERNAL: parent MCP/reverse callback is not available; no bidirectional verificat
DEX            : BLOCKED_EXTERNAL: strict host sandbox fails closed with RTM_NEWADDR EPERM; synthetic trans
VOICE          : BLOCKED_EXTERNAL: ASTER-028 installed voice-model/acoustic variability is preserved with e
SECURITY       : PASS_WITH_RECORDED_RISK
PROVENANCE     : NOT_PROVEN
BACKEND        : 3152 PASS
WEB            : 206 PASS
MOBILE         : 64 PASS
PERFORMANCE    : mean 8.062s / P95 16.5064s
GIT            : CLEAN_SYNCED
COMMIT         : 68cc070bdeaeaf7b4e7b8ad2a6f5abbe580bef1c
NEXT ACTION    : Wait for a reliable bounded download window for candidate qwen2.5-coder:3b; no production route change is permitted.
========================================================
