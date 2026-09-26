"""Management command: generate RSA key pair for RS256 JWT signing.

Adapted from BaseProject core/common/management/commands/generate_rsa_keys.py.
Keys are written to BASE_DIR/key-files/. Add that directory to .gitignore.
Run once per environment: python manage.py generate_rsa_keys
Force regeneration:       python manage.py generate_rsa_keys --regenerate
"""

from __future__ import annotations

import os

from django.conf import settings
from django.core.management.base import BaseCommand

KEY_DIR = settings.BASE_DIR / "key-files"
PRIVATE_KEY_PATH = KEY_DIR / "private_key.pem"
PUBLIC_KEY_PATH = KEY_DIR / "public_key.pem"


class Command(BaseCommand):
    help = "Generate a 2048-bit RSA key pair for RS256 JWT signing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--regenerate",
            action="store_true",
            help="Force regeneration even if keys already exist.",
        )

    def handle(self, *args, **options):
        try:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError:
            self.stderr.write(
                "The 'cryptography' package is required. "
                "Add it to requirements.txt and re-install."
            )
            return

        regenerate = options["regenerate"]
        KEY_DIR.mkdir(parents=True, exist_ok=True)

        if PRIVATE_KEY_PATH.is_file() and not regenerate:
            self.stdout.write("private_key.pem found inside key-files  [skipping]")
        else:
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend(),
            )
            PRIVATE_KEY_PATH.write_bytes(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            self.stdout.write("stored private_key.pem inside key-files  [complete]")

            if PUBLIC_KEY_PATH.is_file() and not regenerate:
                self.stdout.write("public_key.pem found inside key-files   [skipping]")
            else:
                PUBLIC_KEY_PATH.write_bytes(
                    private_key.public_key().public_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PublicFormat.SubjectPublicKeyInfo,
                    )
                )
                self.stdout.write("stored public_key.pem inside key-files   [complete]")
            return

        if PUBLIC_KEY_PATH.is_file() and not regenerate:
            self.stdout.write("public_key.pem found inside key-files   [skipping]")
        else:
            self.stderr.write(
                "public_key.pem is missing but private_key.pem exists. "
                "Run with --regenerate to recreate both."
            )
