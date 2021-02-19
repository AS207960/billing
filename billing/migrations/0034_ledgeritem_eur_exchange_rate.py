# Generated by Django 2.2.17 on 2021-02-01 10:54

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0033_ledgeritem_stripe_climate_contribution'),
    ]

    operations = [
        migrations.AddField(
            model_name='ledgeritem',
            name='eur_exchange_rate',
            field=models.DecimalField(blank=True, decimal_places=7, max_digits=20, null=True),
        ),
    ]