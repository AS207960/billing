# Generated by Django 5.0.4 on 2024-04-23 14:05

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0048_ledgeritem_krw_exchange_rate"),
    ]

    operations = [
        migrations.AddField(
            model_name="account",
            name="cloudflare_account_id",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
