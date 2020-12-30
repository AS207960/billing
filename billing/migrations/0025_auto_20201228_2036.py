# Generated by Django 2.2.17 on 2020-12-28 20:36

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0024_ledgeritem_evidence_stripe_pm'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='achmandate',
            options={'verbose_name': 'ACH Mandate', 'verbose_name_plural': 'ACH Mandates'},
        ),
        migrations.AlterModelOptions(
            name='autogiromandate',
            options={'verbose_name': 'Autogiro Mandate', 'verbose_name_plural': 'Autogiro Mandates'},
        ),
        migrations.AlterModelOptions(
            name='bacsmandate',
            options={'verbose_name': 'Stripe BACS Mandate', 'verbose_name_plural': 'Stripe BACS Mandates'},
        ),
        migrations.AlterModelOptions(
            name='becsmandate',
            options={'verbose_name': 'BECS Mandate', 'verbose_name_plural': 'BECS Mandates'},
        ),
        migrations.AlterModelOptions(
            name='becsnzmandate',
            options={'verbose_name': 'BECS NZ Mandate', 'verbose_name_plural': 'BECS NZ Mandates'},
        ),
        migrations.AlterModelOptions(
            name='betalingsservicemandate',
            options={'verbose_name': 'Betalingsservice Mandate', 'verbose_name_plural': 'Betalingsservice Mandates'},
        ),
        migrations.AlterModelOptions(
            name='gcbacsmandate',
            options={'verbose_name': 'GoCardless BACS Mandate', 'verbose_name_plural': 'GoCardless BACS Mandates'},
        ),
        migrations.AlterModelOptions(
            name='gcsepamandate',
            options={'verbose_name': 'GoCardless SEPA Mandate', 'verbose_name_plural': 'GoCardless SEPA Mandates'},
        ),
        migrations.AlterModelOptions(
            name='padmandate',
            options={'verbose_name': 'PAD Mandate', 'verbose_name_plural': 'PAD Mandates'},
        ),
        migrations.AlterModelOptions(
            name='sepamandate',
            options={'verbose_name': 'Stripe SEPA Mandate', 'verbose_name_plural': 'Stripe SEPA Mandates'},
        ),
        migrations.AddField(
            model_name='ledgeritem',
            name='evidence_ach_mandate',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.ACHMandate'),
        ),
        migrations.AddField(
            model_name='ledgeritem',
            name='evidence_autogiro_mandate',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.AutogiroMandate'),
        ),
        migrations.AddField(
            model_name='ledgeritem',
            name='evidence_bacs_mandate',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.BACSMandate'),
        ),
        migrations.AddField(
            model_name='ledgeritem',
            name='evidence_bank_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.KnownBankAccount'),
        ),
        migrations.AddField(
            model_name='ledgeritem',
            name='evidence_becs_mandate',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.BECSMandate'),
        ),
        migrations.AddField(
            model_name='ledgeritem',
            name='evidence_becs_nz_mandate',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.BECSNZMandate'),
        ),
        migrations.AddField(
            model_name='ledgeritem',
            name='evidence_betalingsservice_mandate',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.BetalingsserviceMandate'),
        ),
        migrations.AddField(
            model_name='ledgeritem',
            name='evidence_gc_bacs_mandate',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.GCBACSMandate'),
        ),
        migrations.AddField(
            model_name='ledgeritem',
            name='evidence_gc_sepa_mandate',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.GCSEPAMandate'),
        ),
        migrations.AddField(
            model_name='ledgeritem',
            name='evidence_pad_mandate',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.PADMandate'),
        ),
        migrations.AddField(
            model_name='ledgeritem',
            name='evidence_sepa_mandate',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='billing.SEPAMandate'),
        ),
    ]
