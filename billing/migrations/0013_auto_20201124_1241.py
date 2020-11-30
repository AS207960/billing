# Generated by Django 2.2.17 on 2020-11-24 12:41

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0012_auto_20201123_2205'),
    ]

    operations = [
        migrations.AlterField(
            model_name='chargestate',
            name='payment_ledger_item',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='charge_state_payment', to='billing.LedgerItem'),
        ),
    ]