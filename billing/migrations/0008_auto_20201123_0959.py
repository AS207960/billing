# Generated by Django 2.2.17 on 2020-11-23 09:59

import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import django_countries.fields


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0007_chargestate_notif_queue'),
    ]

    operations = [
        migrations.AlterField(
            model_name='chargestate',
            name='ledger_item',
            field=models.OneToOneField(default=None, on_delete=django.db.models.deletion.PROTECT, related_name='charge_state', to='billing.LedgerItem'),
            preserve_default=False,
        ),
        migrations.CreateModel(
            name='KnownStripePaymentMethod',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('method_id', models.CharField(max_length=255)),
                ('account', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='billing.Account')),
            ],
        ),
        migrations.CreateModel(
            name='KnownBankAccount',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('country_code', models.CharField(max_length=2, validators=[django.core.validators.MinLengthValidator(2)])),
                ('bank_code', models.CharField(blank=True, max_length=255, null=True)),
                ('branch_code', models.CharField(blank=True, max_length=255, null=True)),
                ('account_code', models.CharField(max_length=255)),
                ('account', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='billing.Account')),
            ],
        ),
        migrations.CreateModel(
            name='AccountBillingAddress',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('organisation', models.CharField(blank=True, max_length=255, null=True)),
                ('street_1', models.CharField(max_length=255, verbose_name='Address line 1')),
                ('street_2', models.CharField(blank=True, max_length=255, null=True, verbose_name='Address line 2')),
                ('street_3', models.CharField(blank=True, max_length=255, null=True, verbose_name='Address line 3')),
                ('city', models.CharField(max_length=255)),
                ('province', models.CharField(blank=True, max_length=255, null=True)),
                ('postal_code', models.CharField(max_length=255)),
                ('country_code', django_countries.fields.CountryField(max_length=2, verbose_name='Country')),
                ('account', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='billing.Account')),
            ],
        ),
    ]