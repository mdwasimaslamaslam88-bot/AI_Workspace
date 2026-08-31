from app.security_events import SecurityEventKind, SecurityEventRecorder


def test_security_event_recorder_retains_only_bounded_metadata():
    recorder = SecurityEventRecorder(maximum_events=2)
    recorder.record(SecurityEventKind.AUTHENTICATION_FAILURE)
    recorder.record(SecurityEventKind.OVERSIZED_REQUEST_CONTAINMENT)
    recorder.record(SecurityEventKind.RATE_LIMIT_CONTAINMENT)

    events = recorder.snapshot(limit=2)
    assert [event.kind for event in events] == [
        SecurityEventKind.RATE_LIMIT_CONTAINMENT,
        SecurityEventKind.OVERSIZED_REQUEST_CONTAINMENT,
    ]
    assert all(set(event.__slots__) == {"kind", "occurred_at"} for event in events)
