# Generated by Django 2.2.17 on 2020-11-23 10:48

import django.core.validators
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0008_auto_20201123_0959'),
    ]

    operations = [
        migrations.AddField(
            model_name='chargestate',
            name='country_code',
            field=models.CharField(blank=True, max_length=2, null=True, validators=[django.core.validators.MinLengthValidator(2)]),
        ),
        migrations.AddField(
            model_name='chargestate',
            name='evidence_bank_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.KnownBankAccount'),
        ),
        migrations.AddField(
            model_name='chargestate',
            name='evidence_billing_address',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.AccountBillingAddress'),
        ),
        migrations.AddField(
            model_name='chargestate',
            name='evidence_stripe_pm',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.KnownStripePaymentMethod'),
        ),
    ]
