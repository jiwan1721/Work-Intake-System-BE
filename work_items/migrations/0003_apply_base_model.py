"""Apply BaseModel to WorkItem, AnalysisAttempt, and StatusTransition.

Changes per model:
  WorkItem       — renamed updated_at→modified_at; added created_by, modified_by
  AnalysisAttempt — added created_at, modified_at, created_by, modified_by
  StatusTransition — added modified_at, created_by, modified_by
                    (created_at was already defined; now inherited, no column change)
"""

import django.db.models.deletion
import django.utils.timezone
import django_currentuser.db.models.fields
import django_currentuser.middleware
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("work_items", "0002_total_ordering_and_reaper_index"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── WorkItem ────────────────────────────────────────────────────────
        migrations.RenameField(
            model_name="workitem",
            old_name="updated_at",
            new_name="modified_at",
        ),
        migrations.AddField(
            model_name="workitem",
            name="created_by",
            field=django_currentuser.db.models.fields.CurrentUserField(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="work_items_workitem_created",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="workitem",
            name="modified_by",
            field=django_currentuser.db.models.fields.CurrentUserField(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                on_update=True,
                related_name="work_items_workitem_modified",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # ── AnalysisAttempt ─────────────────────────────────────────────────
        migrations.AddField(
            model_name="analysisattempt",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="analysisattempt",
            name="modified_at",
            field=models.DateTimeField(
                auto_now=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="analysisattempt",
            name="created_by",
            field=django_currentuser.db.models.fields.CurrentUserField(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="work_items_analysisattempt_created",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="analysisattempt",
            name="modified_by",
            field=django_currentuser.db.models.fields.CurrentUserField(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                on_update=True,
                related_name="work_items_analysisattempt_modified",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # ── StatusTransition ────────────────────────────────────────────────
        migrations.AddField(
            model_name="statustransition",
            name="modified_at",
            field=models.DateTimeField(
                auto_now=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="statustransition",
            name="created_by",
            field=django_currentuser.db.models.fields.CurrentUserField(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="work_items_statustransition_created",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="statustransition",
            name="modified_by",
            field=django_currentuser.db.models.fields.CurrentUserField(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                on_update=True,
                related_name="work_items_statustransition_modified",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
