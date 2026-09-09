========================================================
ASTER / PERSONAL AI OS AUTONOMOUS STATUS
========================================================
READINESS      : NOT READY
BENCHMARK      : 97.82 / 100
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
PERFORMANCE    : mean 8.1446s / P95 16.6314s
GIT            : CLEAN_SYNCED
COMMIT         : c08032529f0df6c0ac50b77b45a50b13b70a7165
NEXT ACTION    : Reproduce the report-provenance defect against current HEAD. Verify current-source, runtime, artifact, benchmark, security and release evidence by content, preserve historical roots, and implement or verify the smallest guard/report repair.
========================================================
