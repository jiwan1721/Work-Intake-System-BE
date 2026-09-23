"""Rescue work items whose analysis never finished (PLAN §5).

    python manage.py reap_stale_analyses [--older-than-seconds N]

In production this would be a periodic job (cron, Celery beat, a Kubernetes
CronJob). Here it is a command so the behaviour is explicit and testable.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from work_items.services.analysis import reap_stale_analyses


class Command(BaseCommand):
    help = "Move abandoned ANALYSING work items to FAILED so operators can retry them."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--older-than-seconds",
            type=float,
            default=None,
            help=(
                "Age threshold in seconds. Defaults to 2 x AI_TIMEOUT_SECONDS "
                f"(currently {settings.AI_TIMEOUT_SECONDS * 2:g}s)."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        threshold = options["older_than_seconds"]
        if threshold is not None and threshold < 0:
            raise CommandError("--older-than-seconds must not be negative.")

        reaped = reap_stale_analyses(older_than_seconds=threshold)

        if reaped:
            self.stdout.write(
                self.style.WARNING(f"Reaped {reaped} stale analysis/analyses to FAILED.")
            )
        else:
            self.stdout.write(self.style.SUCCESS("No stale analyses found."))
