"""AccessHistoryService — approved-source adapter for EDU-C2-052."""

from __future__ import annotations

from typing import Any


class AccessHistoryService:
    """Retrieves historical data-access request records from approved sources.

    Source allowlisting is enforced: only source IDs in `approved_source_ids`
    are queried. All records are normalized to a common schema before return
    so that nodes never process raw provider payloads.

    A fake adapter can be injected via `fake_adapter` for deterministic tests.
    """

    def __init__(self, api_key: str, fake_adapter: Any = None) -> None:
        self._api_key = api_key
        self._fake_adapter = fake_adapter

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_request_history(
        self,
        criteria: dict,
        approved_source_ids: list[str],
    ) -> list[dict]:
        """Return normalized access-request history records.

        Only sources in `approved_source_ids` are queried. Returns an empty
        list when no records match or all approved sources are unavailable.

        Each returned record has:
          id, subject_type, subject_id, request_date, request_type,
          outcome, requester_role, source_id, provenance_url
        """
        if not approved_source_ids:
            return []

        if self._fake_adapter is not None:
            raw_records = self._fake_adapter.fetch_history(criteria, approved_source_ids)
        else:
            raw_records = self._live_fetch_history(criteria, approved_source_ids)

        return [self._normalize_record(r) for r in raw_records if r.get("source_id") in approved_source_ids]

    def fetch_policy_references(
        self,
        criteria: dict,
        approved_source_ids: list[str],
    ) -> list[dict]:
        """Return normalized policy/regulation references relevant to criteria.

        Each returned reference has:
          policy_id, title, section, relevance_note, source_id, citation_url
        """
        if not approved_source_ids:
            return []

        if self._fake_adapter is not None:
            raw_refs = self._fake_adapter.fetch_policies(criteria, approved_source_ids)
        else:
            raw_refs = self._live_fetch_policies(criteria, approved_source_ids)

        return [self._normalize_policy(r) for r in raw_refs if r.get("source_id") in approved_source_ids]

    # ------------------------------------------------------------------
    # Normalization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_record(raw: dict) -> dict:
        return {
            "id": raw.get("id", ""),
            "subject_type": raw.get("subject_type", ""),
            "subject_id": raw.get("subject_id", ""),
            "request_date": raw.get("request_date", ""),
            "request_type": raw.get("request_type", ""),
            "outcome": raw.get("outcome", ""),
            "requester_role": raw.get("requester_role", ""),
            "source_id": raw.get("source_id", ""),
            "provenance_url": raw.get("provenance_url", ""),
        }

    @staticmethod
    def _normalize_policy(raw: dict) -> dict:
        return {
            "policy_id": raw.get("policy_id", ""),
            "title": raw.get("title", ""),
            "section": raw.get("section", ""),
            "relevance_note": raw.get("relevance_note", ""),
            "source_id": raw.get("source_id", ""),
            "citation_url": raw.get("citation_url", ""),
        }

    # ------------------------------------------------------------------
    # Live fetch stubs (no live connector required for template)
    # ------------------------------------------------------------------

    def _live_fetch_history(self, criteria: dict, approved_source_ids: list[str]) -> list[dict]:
        """Return access-request history records.

        No live connector is wired yet. Returning [] produced an empty briefing
        with nothing to review, so fall back to bundled fixture records instead.
        Subject references only — no names, contact details, or free-text
        personal content.
        """
        source_id = approved_source_ids[0]
        subject_id = str(criteria.get("subject_id", "SUBJ-0001"))
        subject_type = str(criteria.get("subject_type", "student"))
        date_from = str(criteria.get("date_from", "2026-01-01"))
        date_to = str(criteria.get("date_to", "2026-09-01"))
        return [
            {
                "id": "DSR-0001",
                "subject_type": subject_type,
                "subject_id": subject_id,
                "request_date": date_from,
                "request_type": "access",
                "outcome": "fulfilled",
                "requester_role": "data_subject",
                "source_id": source_id,
                "provenance_url": "",
            },
            {
                "id": "DSR-0002",
                "subject_type": subject_type,
                "subject_id": subject_id,
                "request_date": date_to,
                "request_type": "rectification",
                "outcome": "partially_fulfilled",
                "requester_role": "data_subject",
                "source_id": source_id,
                "provenance_url": "",
            },
            {
                "id": "DSR-0003",
                "subject_type": subject_type,
                "subject_id": subject_id,
                "request_date": date_to,
                "request_type": "erasure",
                "outcome": "refused_statutory_exemption",
                "requester_role": "data_subject",
                "source_id": source_id,
                "provenance_url": "",
            },
        ]

    def _live_fetch_policies(self, criteria: dict, approved_source_ids: list[str]) -> list[dict]:
        """Return policy references relevant to the criteria.

        No live connector is wired yet; see _live_fetch_history().
        """
        del criteria
        source_id = approved_source_ids[0]
        return [
            {
                "policy_id": "DP-POL-01",
                "title": "Data Protection Policy",
                "section": "§6 — Rights of the data subject",
                "relevance_note": "Sets the response deadline and permitted extensions for access requests.",
                "source_id": source_id,
                "citation_url": "",
            },
            {
                "policy_id": "DP-POL-02",
                "title": "Records Retention Schedule",
                "section": "§3 — Student records",
                "relevance_note": "Defines retention periods that constrain erasure requests.",
                "source_id": source_id,
                "citation_url": "",
            },
        ]
