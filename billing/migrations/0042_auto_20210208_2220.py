# Generated by Django 2.2.17 on 2021-02-08 22:20

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0041_ledgeritem_freeagent_invoice'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='ledgeritem',
            name='freeagent_invoice',
        ),
        migrations.AddField(
            model_name='ledgeritem',
            name='payment_charge_state',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payment_items', to='billing.ChargeState'),
        ),
    ]
