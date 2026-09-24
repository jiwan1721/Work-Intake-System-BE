"""Make the list order total, and give the reaper an index.

- `-created_at` alone cannot order rows that share a timestamp, so LIMIT/OFFSET
  paging could show a row twice or skip it. `-id` breaks the tie.
- `reap_stale_analyses` filters on `status='ANALYSING' AND analysis_started_at <
  cutoff`. The partial index covers exactly that and holds only the items in
  flight, so it does not grow with the table.

Both are metadata-only against existing rows: no data is rewritten.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('work_items', '0001_initial'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='workitem',
            options={'ordering': ['-created_at', '-id']},
        ),
        migrations.AddIndex(
            model_name='workitem',
            index=models.Index(condition=models.Q(('status', 'ANALYSING')), fields=['analysis_started_at'], name='work_item_analysing_started'),
        ),
    ]
