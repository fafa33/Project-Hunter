from pathlib import Path

rv = Path("src/hunter/evidence_intelligence/response_validator.py")
text = rv.read_text()
old = "            self._discard_transient_response(signal.transient_cleanup_coordinates)\n"
new = """            self._discard_transient_response(
                signal.transient_cleanup_coordinates,
                refusing_validation_event_id=allocation.validation_event_id,
            )
"""
assert text.count(old) == 1, text.count(old)
text = text.replace(old, new, 1)

old = """    def _discard_transient_response(
        self,
        coordinates: tuple[tuple[str, str], ...] | None,
    ) -> None:
        boundary = self._transient_response_vault
        if boundary is not None and coordinates is not None:
            boundary.discard_authorized(**dict(coordinates))
"""
new = """    def _discard_transient_response(
        self,
        coordinates: tuple[tuple[str, str], ...] | None,
        *,
        refusing_validation_event_id: str,
    ) -> None:
        boundary = self._transient_response_vault
        if boundary is None or coordinates is None:
            return
        resolved = dict(coordinates)
        capture_identity = resolved.get("response_capture_identity")
        if capture_identity:
            owner = self._foundation._repository.transient_capture_owner(capture_identity)  # noqa: SLF001
            if owner is not None and owner != refusing_validation_event_id:
                # A later/refusing event must never destroy the first owner's
                # single-use body. Reservation ownership is durable and wins.
                return
        boundary.discard_authorized(**resolved)
"""
assert text.count(old) == 1, text.count(old)
text = text.replace(old, new, 1)

old = """    for name in ("minLength", "maxLength", "minItems", "maxItems"):
        item = schema.get(name)
        if item is not None and (not isinstance(item, int) or isinstance(item, bool) or item < 0):
            raise ResponseValidationRuleUnavailable(f"output-contract {name} must be a non-negative integer")
"""
new = """    for name in ("minLength", "maxLength", "minItems", "maxItems"):
        item = schema.get(name)
        if item is not None and not _is_non_negative_json_integer(item):
            raise ResponseValidationRuleUnavailable(f"output-contract {name} must be a non-negative integer")
"""
assert text.count(old) == 1, text.count(old)
text = text.replace(old, new, 1)

anchor = "\n\ndef _schema_type_invalid(value: Any, schema: Mapping[str, Any]) -> bool:\n"
helper = """

def _is_non_negative_json_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    if isinstance(value, Decimal):
        return value.is_finite() and value >= 0 and value == value.to_integral_value()
    return False
"""
assert text.count(anchor) == 1
text = text.replace(anchor, helper + anchor, 1)
rv.write_text(text)

tw = Path("src/hunter/evidence_intelligence/transient_worker.py")
text = tw.read_text()
old = """        parent_endpoint, worker_endpoint = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        parent_endpoint.set_inheritable(False)
        worker_endpoint.set_inheritable(False)
        pid = os.fork()
        if pid == 0:  # pragma: no cover - assertions observe the parent side
            try:
                parent_endpoint.close()
                self.__worker_mode = True
                self.__sessions = {}
"""
new = """        validator = self.__response_validator_ref()
        if validator is None:
            raise _access_error("canonical ResponseValidator owner disappeared")

        parent_endpoint, worker_endpoint = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        parent_endpoint.set_inheritable(False)
        worker_endpoint.set_inheritable(False)

        # Fork only from a quiescent validator boundary. A concurrent authorization
        # or execution may otherwise leave _state_lock locked in the child with no
        # surviving owning thread. The child installs a fresh lock before any
        # validator operation; the parent releases the original immediately after
        # the fork.
        validator_state_lock = validator._state_lock  # noqa: SLF001 - same protected boundary
        validator_state_lock.acquire()
        try:
            pid = os.fork()
        except BaseException:
            validator_state_lock.release()
            parent_endpoint.close()
            worker_endpoint.close()
            raise
        if pid == 0:  # pragma: no cover - assertions observe the parent side
            try:
                validator._state_lock = threading.Lock()  # noqa: SLF001 - post-fork child reset
                parent_endpoint.close()
                self.__worker_mode = True
                self.__sessions = {}
"""
assert text.count(old) == 1, text.count(old)
text = text.replace(old, new, 1)
old = """        worker_endpoint.close()
        try:
            hardened = _recv_message(parent_endpoint)
"""
new = """        validator_state_lock.release()
        worker_endpoint.close()
        try:
            hardened = _recv_message(parent_endpoint)
"""
assert text.count(old) == 1, text.count(old)
text = text.replace(old, new, 1)
tw.write_text(text)

tests = Path("tests/test_response_validator_phase_b.py")
text = tests.read_text()
marker = "\n\ndef test_pr335_non_owner_refusal_preserves_reserved_transient_body"
assert marker not in text
text += r'''


def test_pr335_non_owner_refusal_preserves_reserved_transient_body(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path, transient=True)
    owner_authorization = _authorize(harness)

    policy_time = fixture.VALIDATION_CUTOFF + timedelta(seconds=1)
    publish_policy_successor(harness.source_authority, cutoff=policy_time, processing="DENY")
    harness.foundation._clock = fixture.SequenceClock(policy_time + timedelta(seconds=1))  # noqa: SLF001
    competing = harness.foundation.allocate_revalidation(
        predecessor_validation_event_id=harness.allocation.validation_event_id
    )

    refused = harness.validator.authorize_event(competing)
    assert refused.refusal is not None
    assert refused.refusal.refusal.state is ValidationState.SOURCE_HANDLING_BLOCKED

    owner_result = harness.validator.execute(owner_authorization)
    assert owner_result.outcome.state is ValidationState.VALID


def test_pr335_integral_decimal_schema_size_keywords_are_valid(tmp_path: Path) -> None:
    harness = fixture.make_harness(
        tmp_path,
        output_contract='{"type":"string","minLength":1.0,"maxLength":3.0}',
        raw_response='"ab"',
        profile_overrides={"required_dimensions": ("SYNTAX", "SCHEMA", "OUTPUT_CONTRACT")},
    )

    result = _execute(harness)
    assert result.outcome.state is ValidationState.VALID


def test_pr335_protected_worker_waits_for_validator_quiescence_and_resets_child_lock(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path, transient=True)
    vault = harness.transient_response_vault
    capability = vars(harness.adapter)["_ModelAdapterService__transient_handoff_capability"]
    entered = __import__("threading").Event()
    release = __import__("threading").Event()

    def hold_validator_lock() -> None:
        with harness.validator._state_lock:  # noqa: SLF001 - adversarial fork regression
            entered.set()
            assert release.wait(5)

    def probe_operation() -> Any:
        joined = harness.validator.authorize_event(harness.allocation)
        assert joined.authorization is not None
        from types import SimpleNamespace

        return SimpleNamespace(
            outcome=SimpleNamespace(outcome_id="protected-worker-lock-probe", recorded_at=fixture.CONCLUDED_AT),
            response_artifact=None,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        holder = executor.submit(hold_validator_lock)
        assert entered.wait(5)
        dispatch = executor.submit(
            lambda: vault._dispatch_authorized(capability, operation=probe_operation)  # noqa: SLF001
        )
        assert not dispatch.done()
        release.set()
        metadata = dispatch.result(timeout=10)
        holder.result(timeout=5)

    assert metadata["outcome_id"] == "protected-worker-lock-probe"
    assert metadata["validation_ready"] is False
'''
tests.write_text(text)
