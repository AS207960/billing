# Generated by Django 2.2.17 on 2021-02-07 18:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0038_auto_20210207_1706'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='freeagent_contact_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]