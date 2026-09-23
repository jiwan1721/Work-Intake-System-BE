"""Demo data (PLAN §11).

    python manage.py seed_work_items [--analyse]

Goes through the intake service rather than `objects.create`, so the seeded
rows have the same content hashes and validation as anything the external
system posts — and so running it twice is a no-op instead of a pile of
duplicates.

The last four items carry `[simulate:...]` markers, which means a reviewer can
click Analyse on them and see every failure path the UI handles without
touching a config file.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from work_items.domain.errors import DomainError
from work_items.services.analysis import analyse_work_item
from work_items.services.intake import create_or_get_work_item

SEED_ITEMS: tuple[dict[str, str], ...] = (
    {
        "external_id": "CRM-10001",
        "title": "Missing income document",
        "description": (
            "The applicant submitted their mortgage application on Monday but no payslip "
            "was attached. Underwriting cannot proceed until we have the most recent "
            "three months."
        ),
    },
    {
        "external_id": "CRM-10002",
        "title": "Customer asking about application status",
        "description": (
            "How long will my application take? I submitted everything two weeks ago and "
            "have not heard anything since the acknowledgement email."
        ),
    },
    {
        "external_id": "CRM-10003",
        "title": "Complaint about repeated document requests",
        "description": (
            "This is unacceptable. I have now uploaded my bank statement three times and "
            "each time someone asks for it again. I want this escalated."
        ),
    },
    {
        "external_id": "CRM-10004",
        "title": "Cannot log in to the portal",
        "description": (
            "Every time I try to sign in I get an error saying my session expired. I have "
            "cleared my cookies and tried a different browser."
        ),
    },
    {
        "external_id": "CRM-10005",
        "title": "Change of address after house move",
        "description": (
            "I need to change my address on file. I moved last weekend and the old address "
            "is still showing on my statements."
        ),
    },
    {
        "external_id": "CRM-10006",
        "title": "Proof of address required for verification",
        "description": (
            "Compliance flagged this account for re-verification. The customer needs to "
            "upload a utility bill dated within the last three months. This is urgent, the "
            "regulator deadline is Friday."
        ),
    },
    {
        "external_id": "CRM-10007",
        "title": "Request to close savings account",
        "description": (
            "Please close my account and transfer the remaining balance to the current "
            "account I hold with you."
        ),
    },
    # --- One per simulated failure mode, so every path is clickable. --------
    {
        "external_id": "CRM-90001",
        "title": "[simulate:timeout] Missing payslip for underwriting",
        "description": "Demo item: the model call will time out when you analyse this.",
    },
    {
        "external_id": "CRM-90002",
        "title": "[simulate:malformed] Customer query about fees",
        "description": "Demo item: the model will answer with prose instead of JSON.",
    },
    {
        "external_id": "CRM-90003",
        "title": "[simulate:bad_enum] Unclassifiable request",
        "description": "Demo item: the model will return a category that is not in the enum.",
    },
    {
        "external_id": "CRM-90004",
        "title": "[simulate:error] Address change request",
        "description": "Demo item: the provider will report itself unavailable.",
    },
)


class Command(BaseCommand):
    help = "Create ~10 realistic demo work items, including one per simulated failure mode."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--analyse",
            action="store_true",
            help="Also run the analysis on each newly created item.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        created = 0
        skipped = 0

        for payload in SEED_ITEMS:
            result = create_or_get_work_item(**payload)
            if result.created:
                created += 1
                if options["analyse"]:
                    self._analyse(result.item.id, payload["external_id"])
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete: {created} created, {skipped} already present "
                f"({len(SEED_ITEMS)} total)."
            )
        )
        if not options["analyse"]:
            self.stdout.write(
                "Tip: run with --analyse to populate results, or click Analyse in the UI."
            )

    def _analyse(self, item_id: Any, external_id: str) -> None:
        try:
            item = analyse_work_item(item_id)
        except DomainError as exc:
            # A seeded item failing to analyse is demo data, not a crash.
            self.stdout.write(self.style.WARNING(f"  {external_id}: {exc.code}"))
            return
        self.stdout.write(f"  {external_id}: {item.status}")
