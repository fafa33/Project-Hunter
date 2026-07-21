from __future__ import annotations

from hunter.value_capture.models import (
    FundamentalEvidenceRecord,
    SupplyBasisSnapshot,
    ValueCaptureRuleSnapshot,
)
from hunter.value_capture.providers import AcquisitionReceipt, ValueCaptureVerificationKeyRegistry
from hunter.value_capture.repository import (
    SupplyAndValueCaptureRepository,
    ValueCaptureIntegrityError,
    _connect,
    _insert_receipt,
    _insert_record,
    _table_for,
    _validate_receipt_binding,
)

Record = FundamentalEvidenceRecord | SupplyBasisSnapshot | ValueCaptureRuleSnapshot


class _ValueCaptureAuthorityWriter:
    def __init__(
        self,
        *,
        repository: SupplyAndValueCaptureRepository,
        verification_keys: ValueCaptureVerificationKeyRegistry,
    ) -> None:
        self.__path = repository.path
        self.__verification_keys = verification_keys

    def persist(self, receipt: AcquisitionReceipt, record: Record) -> None:
        if not self.__verification_keys.verify_receipt(receipt):
            raise ValueCaptureIntegrityError("receipt hash or signature is not verification-key authorized")
        _validate_receipt_binding(receipt, record)
        with _connect(self.__path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                _insert_receipt(conn, receipt)
                _insert_record(conn, _table_for(record), record)
            except Exception:
                conn.rollback()
                raise
            conn.commit()
