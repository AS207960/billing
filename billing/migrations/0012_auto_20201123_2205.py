# Generated by Django 2.2.17 on 2020-11-23 22:05

import as207960_utils.models
from django.db import migrations, models


def set_base_amount(apps, schema_editor):
    ChargeState = apps.get_model('billing', 'ChargeState')
    for charge_state in ChargeState.objects.all():
        if charge_state.ledger_item:
            charge_state.base_amount = -charge_state.ledger_item.amount
            charge_state.save()


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0011_accountbillingaddress_default'),
    ]

    operations = [
        migrations.AddField(
            model_name='chargestate',
            name='base_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=9),
        ),
        migrations.AddField(
            model_name='chargestate',
            name='vat_rate',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=9),
        ),
        migrations.RunPython(set_base_amount, lambda a, b: None),
        migrations.AlterField(
            model_name='accountbillingaddress',
            name='default',
            field=models.BooleanField(blank=True, default=False),
        ),
        migrations.AlterField(
            model_name='accountbillingaddress',
            name='id',
            field=as207960_utils.models.TypedUUIDField(data_type='billing_billingaddress', primary_key=True, serialize=False),
        ),
    ]