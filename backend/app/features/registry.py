"""Auditable product feature registry.

This registry describes product exposure, not live runtime health. Runtime-
dependent entries still require the corresponding authenticated capability or
diagnostic probe to report ready before the UI enables execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NamedTuple


FeatureLayer = Literal[
    "ai_presence",
    "mission_control",
    "universal_workspace",
    "ai_command_center",
    "apps_hub",
]
FeatureStatus = Literal[
    "implemented",
    "runtime_dependent",
    "external_dependency",
    "planned",
]


@dataclass(frozen=True, slots=True)
class FeatureRecord:
    id: str
    layer: FeatureLayer
    category: str
    title: str
    description: str
    ui_entry_point: str
    backend_capability: str
    required_permissions: tuple[str, ...]
    dependencies: tuple[str, ...]
    status: FeatureStatus
    test_coverage: tuple[str, ...]


class _Seed(NamedTuple):
    key: str
    title: str


class _Group(NamedTuple):
    layer: FeatureLayer
    category: str
    ui_root: str
    backend_capability: str
    status: FeatureStatus
    permissions: tuple[str, ...]
    dependencies: tuple[str, ...]
    tests: tuple[str, ...]
    features: tuple[_Seed, ...]


def _features(*pairs: tuple[str, str]) -> tuple[_Seed, ...]:
    return tuple(_Seed(*pair) for pair in pairs)


_GROUPS = (
    _Group(
        "ai_presence",
        "Conversation",
        "/home",
        "conversations",
        "implemented",
        ("owner_session",),
        (),
        ("backend:test_conversations_api", "web:app", "mobile:chat"),
        _features(
            ("text_chat", "Text chat"),
            ("ask_ai_anything", "Ask AI Anything"),
            ("conversation_history", "Conversation history"),
            ("conversation_search", "Conversation search"),
            ("conversation_branching", "Conversation branching"),
            ("response_regeneration", "Response regeneration"),
            ("message_edit_resend", "Message edit and resend"),
            ("conversation_rename", "Conversation rename"),
            ("conversation_pin", "Conversation pinning"),
            ("conversation_archive", "Conversation archiving"),
            ("conversation_duplicate", "Conversation duplication"),
            ("conversation_delete", "Conversation deletion"),
            ("response_cancel", "Response cancellation"),
            ("model_selection", "Conversation model selection"),
        ),
    ),
    _Group(
        "ai_presence",
        "Multimodal presence",
        "/home",
        "multimodal_runtime",
        "runtime_dependent",
        ("owner_session", "media_capture"),
        ("verified_local_model", "asset_storage"),
        ("backend:test_voice_api", "backend:test_image_api", "web:app", "mobile:chat"),
        _features(
            ("microphone_input", "Microphone input"),
            ("speech_recognition", "Speech recognition"),
            ("speech_synthesis", "Speech synthesis"),
            ("voice_playback", "Voice playback"),
            ("automatic_language_detection", "Automatic language detection"),
            ("camera_image_input", "Camera image input"),
            ("vision_understanding", "Vision understanding"),
            ("file_input", "File input"),
            ("document_grounding", "Document-grounded conversation"),
            ("image_generation", "Image generation"),
            ("image_editing", "Image editing"),
        ),
    ),
    _Group(
        "ai_presence",
        "Extended speech profiles",
        "/home",
        "external_speech_model_contract",
        "external_dependency",
        ("owner_session", "media_capture"),
        ("verified_female_voice_model", "verified_multilingual_speech_models"),
        ("contract:feature_registry", "manual:external_boundary"),
        _features(
            ("female_voice_profile", "Female voice profile"),
            ("multilingual_conversation", "Multilingual conversation"),
            ("mixed_language_conversation", "Mixed-language conversation"),
        ),
    ),
    _Group(
        "ai_presence",
        "Realtime communications",
        "/home",
        "external_realtime_connector",
        "external_dependency",
        ("owner_session", "microphone", "camera"),
        ("webrtc_or_telephony_provider", "owner_configuration"),
        ("contract:feature_registry", "manual:external_boundary"),
        _features(
            ("phone_call", "Phone call"),
            ("configured_callback", "Configured callback"),
            ("video_interaction", "Video interaction"),
            ("live_screen_sharing", "Live screen sharing"),
            ("voice_interruption", "Voice interruption"),
            ("natural_turn_taking", "Natural conversational turn-taking"),
            ("realtime_camera_stream", "Realtime camera stream"),
        ),
    ),
    _Group(
        "ai_presence",
        "Companion state",
        "/home",
        "execution_state",
        "implemented",
        ("owner_session",),
        (),
        ("web:product_shell", "mobile:home"),
        _features(
            ("companion_avatar", "AI companion avatar"),
            ("listening_state", "Listening state"),
            ("thinking_state", "Thinking state"),
            ("working_state", "Working state"),
            ("waiting_state", "Waiting state"),
            ("verifying_state", "Verifying state"),
            ("done_state", "Done state"),
            ("needs_input_state", "Needs input state"),
            ("contextual_responses", "Contextual responses"),
            ("clarification_requests", "Clarification requests"),
        ),
    ),
    _Group(
        "mission_control",
        "Mission lifecycle",
        "/missions",
        "agent_os",
        "implemented",
        ("owner_session", "model_inference"),
        ("verified_local_model",),
        ("backend:test_agent_os", "web:agent_panel", "mobile:agents"),
        _features(
            ("mission_create", "Mission creation"),
            ("intent_capture", "Intent capture"),
            ("mission_plan", "Mission planning"),
            ("specialist_selection", "Specialist agent selection"),
            ("model_routing", "Mission model routing"),
            ("bounded_execution", "Bounded mission execution"),
            ("queued_state", "Queued state"),
            ("planning_state", "Planning state"),
            ("running_state", "Running state"),
            ("verifying_state", "Mission verifying state"),
            ("retrying_state", "Retrying state"),
            ("completed_state", "Completed state"),
            ("failed_state", "Failed state"),
            ("timed_out_state", "Timed-out state"),
            ("mission_cancel", "Mission cancellation"),
            ("mission_inspect", "Mission inspection"),
            ("attempt_history", "Attempt history"),
            ("model_evidence", "Model execution evidence"),
            ("output_integrity", "Output integrity hashes"),
            ("independent_verification", "Independent verification"),
            ("bounded_retry", "Bounded retry and repair"),
            ("objective_evidence_gate", "Objective evidence gate"),
            ("deadline_enforcement", "Mission deadline enforcement"),
            ("permission_contract", "Typed permission contract"),
            ("result_delivery", "Verified result delivery"),
        ),
    ),
    _Group(
        "mission_control",
        "Workflow execution",
        "/missions",
        "bounded_workflows",
        "implemented",
        ("owner_session", "bounded_tool_execution"),
        ("workflow_runtime",),
        ("backend:test_workflows_api", "web:workflow_panel", "mobile:studio"),
        _features(
            ("workflow_create", "Workflow creation"),
            ("workflow_start", "Workflow start"),
            ("workflow_progress", "Workflow progress"),
            ("workflow_step_history", "Workflow step history"),
            ("workflow_cancel", "Workflow cancellation"),
            ("workflow_timeout", "Workflow timeout"),
            ("tool_result_capture", "Tool result capture"),
            ("failure_classification", "Failure classification"),
            ("live_activity_refresh", "Live activity refresh"),
        ),
    ),
    _Group(
        "mission_control",
        "Owner checkpoints",
        "/missions",
        "mission_checkpoint_roadmap",
        "planned",
        ("owner_session",),
        ("persistent_mission_scheduler",),
        ("contract:feature_registry", "manual:documented_gap"),
        _features(
            ("mission_pause", "Mission pause"),
            ("mission_resume", "Mission resume"),
            ("mission_approve", "Mission approval checkpoint"),
            ("mission_modify", "In-flight mission modification"),
            ("mission_manual_retry", "Owner-triggered mission retry"),
        ),
    ),
    _Group(
        "universal_workspace",
        "Core workspaces",
        "/workspaces",
        "conversation_and_agent_workspace",
        "implemented",
        ("owner_session",),
        ("verified_local_model",),
        ("web:feature_catalog", "mobile:studio", "contract:feature_registry"),
        _features(
            ("coding", "Coding workspace"),
            ("writing", "Writing workspace"),
            ("documents", "Documents workspace"),
            ("data", "Data workspace"),
            ("research", "Research workspace"),
            ("files", "Files workspace"),
            ("browser", "Browser workspace"),
            ("terminal", "Terminal workspace"),
            ("developer_tools", "Developer tools workspace"),
            ("memory", "Memory workspace"),
            ("knowledge_rag", "Knowledge and RAG workspace"),
            ("automation", "Automation workspace"),
            ("ai_companion", "AI companion workspace"),
            ("learning", "Learning workspace"),
            ("language_tutor", "Language tutor workspace"),
            ("business", "Business workspace"),
            ("marketing", "Marketing workspace"),
            ("seo", "SEO workspace"),
            ("finance", "Finance workspace"),
            ("trading", "Trading workspace"),
            ("crypto", "Crypto workspace"),
            ("fx", "FX workspace"),
        ),
    ),
    _Group(
        "universal_workspace",
        "Local media runtimes",
        "/workspaces",
        "multimodal_workspace",
        "runtime_dependent",
        ("owner_session", "asset_access"),
        ("verified_capability_model", "asset_storage"),
        ("backend:test_image_api", "backend:test_voice_api", "web:feature_catalog"),
        _features(
            ("image", "Image workspace"),
            ("audio", "Audio workspace"),
            ("voice", "Voice workspace"),
        ),
    ),
    _Group(
        "universal_workspace",
        "Creative experiences",
        "/workspaces/creative",
        "creative_experience_service",
        "implemented",
        ("owner_session", "model_inference"),
        ("verified_local_model", "database"),
        (
            "backend:test_creative_api",
            "backend:test_creative_postgres",
            "web:creative_panel",
            "mobile:studio",
        ),
        _features(
            ("interactive_stories", "Interactive stories"),
            ("games", "Games workspace"),
            ("character_experiences", "Fictional character experiences"),
        ),
    ),
    _Group(
        "universal_workspace",
        "Video creation",
        "/workspaces/video",
        "external_video_connector",
        "external_dependency",
        ("owner_session", "asset_access"),
        ("verified_video_runtime_or_provider",),
        ("contract:feature_registry", "manual:external_boundary"),
        _features(
            ("video", "Video workspace"),
            ("multimedia_creation", "Multimedia creation workspace"),
        ),
    ),
    _Group(
        "universal_workspace",
        "AI Teacher",
        "/workspaces/learning",
        "learning_program_service",
        "implemented",
        ("owner_session", "memory_read", "model_inference"),
        ("verified_local_model", "database"),
        (
            "backend:test_learning_api",
            "backend:test_learning_postgres",
            "web:learning_panel",
            "mobile:studio",
        ),
        _features(
            ("curriculum_beginner_advanced", "Beginner-to-advanced curriculum"),
            ("lessons", "Personalized lessons"),
            ("exercises", "Learning exercises"),
            ("quizzes", "Adaptive quizzes"),
            ("conversation_practice", "Conversation practice"),
            ("revision", "Guided revision"),
            ("progress_tracking", "Learning progress tracking"),
            ("personalized_difficulty", "Personalized difficulty"),
            ("multilingual_teaching", "Multilingual teaching"),
        ),
    ),
    _Group(
        "universal_workspace",
        "Spaced repetition",
        "/workspaces/learning",
        "learning_program_service",
        "implemented",
        ("owner_session",),
        ("database",),
        (
            "backend:test_learning_api",
            "backend:test_learning_postgres",
            "web:learning_panel",
            "mobile:studio",
        ),
        _features(
            ("spaced_repetition", "Spaced repetition scheduling"),
        ),
    ),
    _Group(
        "universal_workspace",
        "Pronunciation scoring",
        "/workspaces/learning#pronunciation-scoring",
        "external_pronunciation_scoring_provider",
        "external_dependency",
        ("owner_session", "microphone"),
        ("pronunciation_scoring_provider",),
        ("contract:feature_registry", "manual:external_boundary"),
        _features(("pronunciation_scoring", "Pronunciation scoring")),
    ),
    _Group(
        "universal_workspace",
        "Marketing intelligence",
        "/workspaces/marketing",
        "marketing_campaign_service",
        "implemented",
        ("owner_session", "model_inference"),
        ("verified_local_model", "database"),
        (
            "backend:test_marketing_api",
            "backend:test_marketing_postgres",
            "web:marketing_panel",
            "mobile:studio",
        ),
        _features(
            ("marketing_research", "Marketing research"),
            ("content_planning", "Content planning"),
            ("copywriting", "Copywriting"),
            ("campaign_planning", "Campaign planning"),
            ("seo_analysis", "SEO analysis"),
            ("reporting", "Marketing reporting"),
            ("optimization_suggestions", "Optimization suggestions"),
            ("lead_workflow_planning", "Lead workflow planning"),
            ("crm_workflow_planning", "CRM workflow planning"),
        ),
    ),
    _Group(
        "universal_workspace",
        "Market intelligence",
        "/workspaces/finance",
        "market_intelligence_service",
        "implemented",
        ("owner_session", "model_inference"),
        ("verified_local_model", "database", "owner_supplied_market_sources"),
        (
            "backend:test_finance_api",
            "backend:test_finance_postgres",
            "web:finance_panel",
            "mobile:studio",
        ),
        _features(
            ("indian_stock_research", "Indian stock research"),
            ("global_stock_research", "Global stock research"),
            ("crypto_research", "Crypto research"),
            ("fx_research", "FX research"),
            ("portfolio_analysis", "Portfolio analysis"),
            ("market_research", "Market research"),
            ("risk_analytics", "Risk analytics"),
        ),
    ),
    _Group(
        "universal_workspace",
        "Market operations",
        "/workspaces/finance",
        "market_workspace_service",
        "implemented",
        ("owner_session",),
        ("database", "owner_supplied_market_sources"),
        (
            "backend:test_finance_api",
            "backend:test_finance_postgres",
            "web:finance_panel",
            "mobile:studio",
        ),
        _features(
            ("watchlists", "Market watchlists"),
            ("alerts", "Market alerts"),
            ("strategy_backtesting", "Strategy backtesting"),
            ("paper_trading", "Paper trading"),
            ("trading_journal", "Trading journal"),
        ),
    ),
    _Group(
        "ai_command_center",
        "Operations",
        "/command",
        "diagnostics",
        "implemented",
        ("owner_session",),
        (),
        ("backend:test_diagnostics_api", "web:settings_panel", "mobile:settings"),
        _features(
            ("active_agents", "Active agents"),
            ("mission_status", "Mission status"),
            ("queued_tasks", "Queued tasks"),
            ("model_catalog", "Model catalog"),
            ("model_routes", "Model routes"),
            ("fallback_routes", "Fallback routes"),
            ("model_eligibility", "Model eligibility"),
            ("blocked_reasons", "Model blocked reasons"),
            ("gpu_diagnostics", "GPU diagnostics"),
            ("cpu_diagnostics", "CPU diagnostics"),
            ("ram_diagnostics", "RAM diagnostics"),
            ("vram_diagnostics", "VRAM diagnostics"),
            ("storage_diagnostics", "Storage diagnostics"),
            ("runtime_health", "Runtime health"),
            ("provider_health", "Provider health"),
            ("network_mode", "Network mode"),
            ("security_events", "Security events"),
            ("audit_visibility", "Audit visibility"),
            ("update_status", "Update status"),
            ("rollback_status", "Rollback status"),
            ("system_health", "System health"),
            ("hardware_refresh", "Hardware refresh state"),
            ("upgrade_detection", "GPU upgrade detection"),
            ("capability_cache_state", "Capability cache state"),
        ),
    ),
    _Group(
        "ai_command_center",
        "Owner controls",
        "/command",
        "owner_settings",
        "implemented",
        ("owner_session",),
        (),
        ("web:settings_panel", "mobile:settings", "backend:test_users_api"),
        _features(
            ("light_theme", "Light theme"),
            ("dark_theme", "Dark theme"),
            ("system_theme", "System theme"),
            ("session_management", "Session management"),
            ("session_rotation", "Session rotation"),
            ("session_revocation", "Session revocation"),
            ("memory_controls", "Memory controls"),
            ("controlled_forgetting", "Controlled forgetting"),
            ("api_fallback_toggle", "API fallback global toggle"),
            ("provider_toggle", "Provider toggle"),
            ("provider_key_isolation", "Provider key isolation"),
            ("provider_cost_controls", "Provider cost controls"),
            ("self_update_decision", "Self-update owner decision"),
            ("backup_checkpoint", "Last-known-good checkpoint"),
            ("rollback_gate", "Rollback gate"),
            ("notifications", "Private notifications"),
        ),
    ),
    _Group(
        "ai_command_center",
        "Platform clients",
        "/command/platforms",
        "shared_authenticated_api",
        "implemented",
        ("owner_session",),
        ("platform_runtime",),
        ("desktop:config", "mobile:config", "web:pwa"),
        _features(
            ("ubuntu_desktop", "Ubuntu desktop client"),
            ("windows_desktop", "Windows desktop client contract"),
            ("macos_desktop", "macOS desktop client contract"),
            ("android_client", "Android client"),
            ("web_pwa", "Web PWA client"),
            ("shared_branding", "Shared product branding"),
            ("shared_api_contract", "Shared API contract"),
            ("secure_remote_client", "Secure remote client mode"),
            ("desktop_deep_links", "Desktop deep links"),
            ("desktop_notifications", "Desktop notifications"),
            ("desktop_window_state", "Desktop window state"),
            ("mobile_bottom_navigation", "Mobile bottom navigation"),
        ),
    ),
    _Group(
        "apps_hub",
        "Local tools",
        "/apps",
        "bounded_tools",
        "implemented",
        ("owner_session", "bounded_tool_execution"),
        (),
        ("backend:test_tools_api", "web:tool_panel", "mobile:studio"),
        _features(
            ("calculator", "Calculator tool"),
            ("document_search", "Document search tool"),
            ("memory_search", "Memory search tool"),
            ("conversation_search", "Conversation search tool"),
            ("filesystem_boundary", "Filesystem boundary"),
            ("tool_audit_log", "Tool audit log"),
            ("tool_timeout", "Tool timeout"),
            ("tool_output_limit", "Tool output limit"),
            ("tool_permission_allowlist", "Tool permission allowlist"),
        ),
    ),
    _Group(
        "apps_hub",
        "Connected productivity",
        "/apps",
        "external_connector",
        "external_dependency",
        ("owner_session", "connector_scope"),
        ("provider_oauth_or_api_key", "owner_consent"),
        ("contract:feature_registry", "manual:external_boundary"),
        _features(
            ("email", "Email connector"),
            ("calendar", "Calendar connector"),
            ("meetings", "Meetings connector"),
            ("crm", "CRM connector"),
            ("social_media", "Social media connector"),
            ("social_scheduling", "Social scheduling"),
            ("campaign_publishing", "Campaign publishing"),
            ("marketing_analytics", "Marketing analytics connector"),
            ("lead_automation", "Lead automation"),
            ("webhook_integration", "Webhook integration"),
            ("rest_integration", "REST integration"),
            ("graphql_integration", "GraphQL integration"),
            ("oauth_integration", "OAuth integration"),
            ("sdk_integration", "SDK integration"),
            ("database_integration", "Database integration"),
            ("local_api_integration", "Local API integration"),
            ("browser_automation", "Browser automation connector"),
            ("desktop_automation", "Desktop automation connector"),
        ),
    ),
    _Group(
        "apps_hub",
        "Connector governance",
        "/apps",
        "connector_service",
        "implemented",
        ("owner_session",),
        (),
        (
            "backend:test_connectors_api",
            "backend:test_connectors_postgres",
            "web:connector_panel",
            "mobile:studio_connections",
        ),
        _features(
            ("connector_management", "Connector management"),
            ("connector_permissions", "Connector permissions"),
            ("credential_isolation", "Credential isolation"),
            ("connector_audit", "Connector audit logging"),
            ("connector_retry", "Connector retry policy"),
            ("connector_health", "Connector health checks"),
            ("rate_limit_handling", "Rate-limit handling"),
            ("connector_failure_state", "Connector failure states"),
            ("connector_revocation", "Connector revocation"),
        ),
    ),
    _Group(
        "apps_hub",
        "External AI governance",
        "/apps",
        "external_ai_provider_service",
        "implemented",
        ("owner_session",),
        (),
        ("backend:test_external_ai_api", "web:settings_panel", "mobile:settings"),
        _features(
            ("quota_awareness", "Provider quota awareness"),
            ("cost_tracking", "Provider cost tracking"),
            ("spending_limits", "Provider spending limits"),
        ),
    ),
    _Group(
        "apps_hub",
        "Broker execution",
        "/apps/finance",
        "external_broker_connector",
        "external_dependency",
        ("owner_session", "broker_trade_scope"),
        ("broker_account", "broker_api", "owner_risk_confirmation"),
        ("contract:feature_registry", "manual:external_boundary"),
        _features(
            ("broker_integration", "Broker API integration"),
            ("live_order_execution", "Live order execution"),
            ("order_risk_confirmation", "Order risk confirmation"),
            ("broker_permission_enforcement", "Broker permission enforcement"),
        ),
    ),
    _Group(
        "apps_hub",
        "Protected experiences",
        "/apps/entertainment",
        "age_gate_policy",
        "external_dependency",
        ("owner_session", "age_verified"),
        ("jurisdiction_check", "age_verification", "consent_policy"),
        ("contract:feature_registry", "manual:legal_boundary"),
        _features(
            ("adult_age_gate", "Adult capability age gate"),
            ("consent_enforcement", "Consent enforcement"),
            ("minor_safety_boundary", "Minor safety boundary"),
            ("jurisdiction_boundary", "Jurisdiction boundary"),
        ),
    ),
)


def _description(group: _Group, title: str) -> str:
    if group.status == "external_dependency":
        return (
            f"Governed {title.lower()} entry point; execution remains disabled until "
            "the listed external dependency and owner permission are configured."
        )
    if group.status == "planned":
        return (
            f"Visible {title.lower()} contract with an explicit implementation boundary; "
            "the UI must not present it as executable."
        )
    if group.status == "runtime_dependent":
        return (
            f"{title} using the existing local capability layer; availability follows "
            "authenticated runtime and model diagnostics."
        )
    return f"{title} exposed through the existing authenticated {group.category.lower()} capability."


def _build_registry() -> tuple[FeatureRecord, ...]:
    records: list[FeatureRecord] = []
    for group in _GROUPS:
        for seed in group.features:
            feature_id = f"{group.layer}.{seed.key}"
            records.append(
                FeatureRecord(
                    id=feature_id,
                    layer=group.layer,
                    category=group.category,
                    title=seed.title,
                    description=_description(group, seed.title),
                    ui_entry_point=f"{group.ui_root}#{seed.key.replace('_', '-')}",
                    backend_capability=group.backend_capability,
                    required_permissions=group.permissions,
                    dependencies=group.dependencies,
                    status=group.status,
                    test_coverage=group.tests,
                )
            )
    ids = [record.id for record in records]
    if len(ids) < 140 or len(ids) != len(set(ids)):
        raise RuntimeError("feature registry must contain at least 140 unique features")
    return tuple(records)


FEATURE_REGISTRY = _build_registry()
