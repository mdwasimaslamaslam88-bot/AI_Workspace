========================================================
ASTER / PERSONAL AI OS AUTONOMOUS STATUS
========================================================
READINESS      : NOT READY
BENCHMARK      : 97.83 / 100
PASS           : 457
PARTIAL        : 1
FAIL           : 1
ISSUES         : 0
CURRENT        : ASTER-REPAIR-ASTER-RUNTIME-PROVENANCE-001-006
PHASE          : ACCEPTANCE_TASK
CANDIDATE      : None
ASTER→AI OS    : PARTIAL: current owner-authenticated HTTP exchange and integrity verified; fabricated exec
AI OS→ASTER    : BLOCKED_EXTERNAL: parent MCP/reverse callback is not available; no bidirectional verificat
DEX            : BLOCKED_EXTERNAL: strict host sandbox fails closed with RTM_NEWADDR EPERM; synthetic trans
VOICE          : BLOCKED_EXTERNAL: ASTER-028 installed voice-model/acoustic variability is preserved with e
SECURITY       : PASS_WITH_RECORDED_RISK
PROVENANCE     : NOT_PROVEN
BACKEND        : 3152 PASS
WEB            : 206 PASS
MOBILE         : 64 PASS
PERFORMANCE    : mean 8.1462s / P95 16.5442s
GIT            : CLEAN_SYNCED
COMMIT         : 36795142d961fe06caad364e42d58d67c7102ca0
SOURCE         : 36795142d961fe06caad364e42d58d67c7102ca0
NEXT ACTION    : Use the trusted parent executor to restart work-station-backend.service, verify loopback health, and recapture authenticated current-source runtime identity. Failure evidence: /home/md-wasim/AI_Workspace_Data/aster-evidence/autonomous-loop/acceptance/cycle-1190.
========================================================
