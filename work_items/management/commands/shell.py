"""Override the built-in `shell` command to use shell_plus when available."""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Start the Django shell (shell_plus when django-extensions is installed)."

    def add_arguments(self, parser):
        # Accept any arguments so callers can still pass --interface, --command, etc.
        parser.add_argument("args", nargs="*")

    def run_from_argv(self, argv):
        # Delegate entirely to shell_plus (or fall back) so its own argument
        # parser handles flags like --ipython, --print-sql, etc.
        try:
            from django_extensions.management.commands import shell_plus  # noqa: F401
            argv = [argv[0], "shell_plus"] + argv[2:]
            from django.core.management import ManagementUtility
            ManagementUtility(argv).execute()
        except ImportError:
            super().run_from_argv(argv)

    def handle(self, *args, **options):
        # Reached only on the ImportError fallback path.
        call_command("shell")
