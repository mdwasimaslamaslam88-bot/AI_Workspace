========================================================
ASTER / PERSONAL AI OS AUTONOMOUS STATUS
========================================================
READINESS      : NOT READY
BENCHMARK      : 97.84 / 100
PASS           : 457
PARTIAL        : 1
FAIL           : 1
ISSUES         : 7
CURRENT        : ASTER-027
ASTER→AI OS    : PARTIAL: current owner-authenticated HTTP exchange and integrity verified; fabricated exec
AI OS→ASTER    : BLOCKED_EXTERNAL: parent MCP/reverse callback is not available; no bidirectional verificat
DEX            : BLOCKED_EXTERNAL: strict host sandbox fails closed with RTM_NEWADDR EPERM; synthetic trans
VOICE          : PARTIAL: ASTER-028 preserves reproducible exact-WAV lexical variability.
SECURITY       : PASS_WITH_RECORDED_RISK
BACKEND        : 3152 PASS
WEB            : 206 PASS
MOBILE         : 64 PASS
PERFORMANCE    : mean 8.052s / P95 16.6673s
GIT            : CLEAN_SYNCED
COMMIT         : f0f9b9c5a7260eedc8690d3cc91e9cc0af8debf2
NEXT ACTION    : Reproduce the report-provenance defect against current HEAD. Verify current-source, runtime, artifact, benchmark, security and release evidence by content, preserve historical roots, and implement or verify the smallest guard/report repair.
========================================================
