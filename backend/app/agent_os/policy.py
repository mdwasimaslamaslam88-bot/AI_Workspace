from __future__ import annotations

from dataclasses import dataclass

from app.agent_os.contracts import AgentKind, AgentPermission, AgentPlan


class AgentPermissionDeniedError(RuntimeError):
    """A plan requested privileges outside its specialist's fixed profile."""


@dataclass(frozen=True, slots=True)
class AgentProfile:
    kind: AgentKind
    permissions: frozenset[AgentPermission]


_MODEL = AgentPermission.MODEL_INFERENCE
DEFAULT_AGENT_PROFILES = (
    AgentProfile(AgentKind.PLANNER, frozenset({_MODEL})),
    AgentProfile(
        AgentKind.CODING,
        frozenset(
            {
                _MODEL,
                AgentPermission.WORKSPACE_READ,
                AgentPermission.WORKSPACE_WRITE,
                AgentPermission.BUILD_EXECUTION,
                AgentPermission.TEST_EXECUTION,
            }
        ),
    ),
    AgentProfile(
        AgentKind.DEBUGGING,
        frozenset(
            {
                _MODEL,
                AgentPermission.WORKSPACE_READ,
                AgentPermission.WORKSPACE_WRITE,
                AgentPermission.BUILD_EXECUTION,
                AgentPermission.TEST_EXECUTION,
            }
        ),
    ),
    AgentProfile(
        AgentKind.RESEARCH,
        frozenset({_MODEL, AgentPermission.NETWORK_RESEARCH}),
    ),
    AgentProfile(
        AgentKind.BROWSER,
        frozenset(
            {
                _MODEL,
                AgentPermission.NETWORK_RESEARCH,
                AgentPermission.BROWSER_CONTROL,
            }
        ),
    ),
    AgentProfile(
        AgentKind.DATA,
        frozenset(
            {
                _MODEL,
                AgentPermission.WORKSPACE_READ,
                AgentPermission.DATA_ANALYSIS,
            }
        ),
    ),
    AgentProfile(AgentKind.VISION, frozenset({_MODEL})),
    AgentProfile(
        AgentKind.IMAGE,
        frozenset(
            {
                _MODEL,
                AgentPermission.IMAGE_GENERATION,
                AgentPermission.IMAGE_EDITING,
            }
        ),
    ),
    AgentProfile(
        AgentKind.VOICE,
        frozenset(
            {
                _MODEL,
                AgentPermission.VOICE_INPUT,
                AgentPermission.VOICE_OUTPUT,
            }
        ),
    ),
    AgentProfile(
        AgentKind.RAG,
        frozenset(
            {
                _MODEL,
                AgentPermission.RAG_READ,
                AgentPermission.MEMORY_READ,
            }
        ),
    ),
    AgentProfile(
        AgentKind.AUTOMATION,
        frozenset({_MODEL, AgentPermission.BOUNDED_TOOL_EXECUTION}),
    ),
    AgentProfile(
        AgentKind.VERIFIER,
        frozenset(
            {
                AgentPermission.WORKSPACE_READ,
                AgentPermission.BUILD_EXECUTION,
                AgentPermission.TEST_EXECUTION,
            }
        ),
    ),
)


class AgentPolicy:
    """Fail-closed least-privilege policy for every specialist handoff."""

    def __init__(
        self,
        profiles: tuple[AgentProfile, ...] = DEFAULT_AGENT_PROFILES,
    ) -> None:
        self._profiles: dict[AgentKind, AgentProfile] = {}
        for profile in profiles:
            if not isinstance(profile, AgentProfile):
                raise TypeError("agent profiles must be AgentProfile values")
            if profile.kind in self._profiles:
                raise ValueError(f"duplicate agent profile: {profile.kind}")
            self._profiles[profile.kind] = profile
        missing = set(AgentKind) - set(self._profiles)
        if missing:
            raise ValueError("agent policy must define every specialist")

    @property
    def profiles(self) -> tuple[AgentProfile, ...]:
        return tuple(self._profiles[kind] for kind in AgentKind)

    def authorize(self, plan: AgentPlan) -> None:
        if not isinstance(plan, AgentPlan):
            raise TypeError("agent policy requires an AgentPlan")
        for step in plan.steps:
            allowed = self._profiles[step.agent].permissions
            unexpected = step.permissions - allowed
            if unexpected:
                raise AgentPermissionDeniedError(
                    f"{step.agent.value} agent requested unauthorized permissions"
                )
